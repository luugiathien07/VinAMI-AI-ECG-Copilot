"""Generate 12-lead time-frequency grid images from PTB-XL.

This writes a new image manifest:
    outdir/manifest_images.csv
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pywt
import scipy.signal
import wfdb
from tqdm import tqdm


LEADS = ["I", "II", "III", "AVR", "AVL", "AVF", "V1", "V2", "V3", "V4", "V5", "V6"]
LABELS = ["NORM", "MI", "STTC", "CD", "HYP"]


def normalize_signal(signal: np.ndarray) -> np.ndarray:
    signal = signal.astype(np.float32)
    signal = signal - np.nanmean(signal)
    std = np.nanstd(signal)
    if std > 0:
        signal = signal / std
    return signal


def bandpass(signal: np.ndarray, fs: int) -> np.ndarray:
    high = min(40.0, fs / 2 - 1.0)
    sos = scipy.signal.butter(4, [0.5, high], btype="bandpass", fs=fs, output="sos")
    return scipy.signal.sosfiltfilt(sos, signal)


def wavelet_denoise(signal: np.ndarray, wavelet: str = "db6", level: int = 4) -> np.ndarray:
    """VisuShrink wavelet-threshold denoising (Donoho & Johnstone 1994).

    Soft-thresholds detail coefficients at a universal threshold derived
    from the noise level (estimated via MAD of the finest-scale details);
    approximation coefficients are left untouched. No training data needed.
    """
    max_level = pywt.dwt_max_level(len(signal), pywt.Wavelet(wavelet).dec_len)
    coeffs = pywt.wavedec(signal, wavelet, level=min(level, max_level))
    sigma = np.median(np.abs(coeffs[-1])) / 0.6745
    if sigma > 0:
        threshold = sigma * np.sqrt(2 * np.log(len(signal)))
        coeffs[1:] = [pywt.threshold(detail, threshold, mode="soft") for detail in coeffs[1:]]
    return pywt.waverec(coeffs, wavelet)[: len(signal)]


def cwt_image(signal: np.ndarray, fs: int, bins: int = 96) -> np.ndarray:
    frequencies = np.linspace(1, min(45, fs / 2 - 1), bins)
    wavelet = "morl"
    scales = pywt.central_frequency(wavelet) * fs / frequencies
    coeffs, _ = pywt.cwt(signal, scales, wavelet, sampling_period=1 / fs)
    image = np.log1p(np.abs(coeffs))
    return image


def stft_image(signal: np.ndarray, fs: int) -> np.ndarray:
    freqs, _, spectrum = scipy.signal.spectrogram(
        signal,
        fs=fs,
        window="hann",
        nperseg=min(128, len(signal)),
        noverlap=min(64, max(0, len(signal) // 4)),
        mode="magnitude",
    )
    keep = freqs <= min(45, fs / 2)
    return 20 * np.log10(spectrum[keep] + 1e-8)


def render_grid(signals: np.ndarray, fs: int, representation: str, output_path: Path, denoise: bool = False) -> None:
    fig, axes = plt.subplots(4, 3, figsize=(6, 8), dpi=120)
    for idx, ax in enumerate(axes.flat):
        filtered = bandpass(signals[:, idx], fs)
        if denoise:
            filtered = wavelet_denoise(filtered)
        ecg = normalize_signal(filtered)
        if representation == "cwt":
            image = cwt_image(ecg, fs)
        elif representation == "stft":
            image = stft_image(ecg, fs)
        else:
            raise ValueError(f"Unsupported representation: {representation}")
        ax.imshow(image, aspect="auto", origin="lower", cmap="magma")
        ax.set_title(LEADS[idx], fontsize=7)
        ax.axis("off")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout(pad=0.2)
    plt.savefig(output_path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def read_record(ptbxl_root: Path, row: pd.Series | dict, sampling: str) -> tuple[np.ndarray, int]:
    filename_col = "filename_hr" if sampling == "500" else "filename_lr"
    record_base = ptbxl_root / row[filename_col]
    signals, fields = wfdb.rdsamp(str(record_base))
    return signals, int(fields["fs"])


def render_record(job: tuple) -> tuple[dict | None, str | None]:
    """Render one record, or report why it could not be rendered.

    Returns the manifest row on success. An image that is already on disk is
    kept and its row returned without re-rendering, so an interrupted run is
    resumed by simply invoking the same command again.

    A missing or truncated record is reported instead of raised: PTB-XL is
    often only partially downloaded, and one absent .hea used to abort the
    whole render and leave no manifest behind at all.
    """
    row, ptbxl_root, sampling, representation, image_path, denoise = job
    try:
        if not (image_path.exists() and image_path.stat().st_size > 0):
            signals, fs = read_record(ptbxl_root, row, sampling)
            render_grid(signals, fs, representation, image_path, denoise=denoise)
    except (FileNotFoundError, ValueError, OSError) as error:
        return None, f"{row.get('ecg_id', '?')}: {error}"

    output = dict(row)
    output["image_path"] = str(image_path)
    return output, None


def balanced_limit(manifest: pd.DataFrame, limit: int | None) -> pd.DataFrame:
    if not limit:
        return manifest
    # Keep the experiment quick while preserving all official splits.
    chunks = []
    per_split = max(1, limit // 3)
    for split in ["train", "val", "test"]:
        frame = manifest[manifest["split"] == split]
        chunks.append(frame.sample(min(len(frame), per_split), random_state=7))
    return pd.concat(chunks).sort_values(["split", "ecg_id"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ptbxl-root", type=Path, default=Path("data/ptb-xl"))
    parser.add_argument("--manifest", type=Path, default=Path("data/ptbxl_superclass_manifest.csv"))
    parser.add_argument("--outdir", type=Path, default=Path("data/ptbxl_images_cwt"))
    parser.add_argument("--representation", choices=["cwt", "stft"], default="cwt")
    parser.add_argument("--sampling", choices=["100", "500"], default="100")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=12, help="Parallel render processes.")
    parser.add_argument(
        "--denoise",
        action="store_true",
        help="Apply wavelet-threshold denoising (VisuShrink) after the bandpass filter.",
    )
    args = parser.parse_args()

    manifest = pd.read_csv(args.manifest)
    manifest = balanced_limit(manifest, args.limit)

    jobs = []
    for _, row in manifest.iterrows():
        rel_path = Path(row["split"]) / f"{int(row['ecg_id']):05d}_{args.representation}.png"
        jobs.append(
            (row.to_dict(), args.ptbxl_root, args.sampling, args.representation, args.outdir / rel_path, args.denoise)
        )

    image_rows = []
    failed = 0
    # One 12-lead CWT grid costs ~1-2.5 s of CPU, so the full 21.8k-record
    # database is many hours serially; the renders are independent, so fan
    # them out over processes the way build_snomed_ecg_manifest.py does.
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(render_record, job) for job in jobs]
        for future in tqdm(as_completed(futures), total=len(futures)):
            output, error = future.result()
            if error:
                failed += 1
                if failed <= 20:
                    print(f"  ! {error}")
            else:
                image_rows.append(output)

    if failed:
        print(f"Skipped {failed} records that could not be read (missing or truncated download)")
    if not image_rows:
        raise SystemExit("Khong render duoc ban ghi nao - kiem tra lai du lieu PTB-XL da tai chua")

    image_manifest = pd.DataFrame(image_rows)
    image_manifest = image_manifest.sort_values(["split", "ecg_id"]).reset_index(drop=True)  # as_completed order is nondeterministic
    manifest_path = args.outdir / "manifest_images.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    image_manifest.to_csv(manifest_path, index=False)
    print(f"Saved image manifest: {manifest_path} ({len(image_manifest)} rows)")


if __name__ == "__main__":
    main()

