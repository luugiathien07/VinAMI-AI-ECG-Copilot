"""Convert EchoNext-Mini (PhysioNet, restricted access) into signals.f16.npy
+ index.csv for the *unmodified* src/train_signal_1d.py.

See Research/VinStroke_ECG_LVEF_Research_Proposal.md section 8.1 for why this
dataset is now the priority-1 source: it ships a ready-made structured label
(`lvef_lte_45_flag`) from the source study, so unlike the MIMIC-IV-Note route
in build_lvef_label_extraction.py, there is NO NLP/regex extraction step
here -- this script only reshapes/resamples/normalises and writes the label.

CONFIRMED against the real download (2026-09-08, dataset version 1.0.0 --
this replaces the earlier unverified placeholder assumptions):
    EchoNext_metadata_100k.csv: 100,000 rows, one per ECG, columns include
        ecg_key (record id), patient_key (patient id), lvef_lte_45_flag,
        and split (values: train/val/test/no_split -- train=72475,
        val=4626, test=5442, no_split=17457).
    EchoNext_<split>_waveforms.npy: one file PER split (train/val/test/
        no_split), shape (N, 1, 2500, 12), float64, 10 s @ 250 Hz, 12 leads.
        Row i of a split's waveform file corresponds to the i-th row of
        metadata filtered to that split, IN THE ORIGINAL CSV ORDER (verified
        by matching array lengths to the metadata split value_counts above)
        -- there is no shared global waveform file across splits.

Per the dataset's own README: `no_split` ECGs are excluded from model
training (train may have multiple ECGs per patient; val/test keep only the
latest ECG per patient) -- --splits defaults to train/val/test only.

The metadata ALSO carries a continuous `lvef_value` (%) column, present for
~91% of rows (the rest are qualitative-only echo reports) -- 74,957/82,543
in the train/val/test splits this script keeps. That means the TRUE HFrEF
<= 40% question the rest of this proposal is built around (section 7.2-7.3,
matching cache_lvef_signals.py's `lvef_binary_40`) can be answered directly
from EchoNext-Mini, by thresholding `lvef_value` ourselves -- correcting an
earlier assumption in section 8.1 that <= 40% required the harder MIMIC-IV-
Note NLP branch. The output index.csv therefore carries BOTH:
    lvef_binary_45: from the ready-made `lvef_lte_45_flag` -- always defined
        (no NaN), because EchoNext's own labelling methodology back-fills
        negatives for ECGs preceding a documented negative echo even without
        a same-visit numeric EF (see the dataset's README). This is NOT the
        same population as thresholding lvef_value directly.
    lvef_binary_40: our own `lvef_value <= 40` threshold -- NaN wherever
        lvef_value is missing (~9% of rows); DROP those before training on
        this label rather than treating NaN as a class.
Do not merge/compare the two labels as if they were the same population or
the same clinical question -- they use different row sets and different
thresholds.

The output index.csv carries the dataset's own `split` column, so downstream
training should reuse the *official* patient-disjoint split (train may
repeat patients, val/test do not) instead of re-randomising one:
    python3 src/train_signal_1d.py --cache data/echonext_signal_cache \\
        --labels lvef_binary_45 --split-from data/echonext_signal_cache/index.csv ...

Resampling 250 Hz -> 500 Hz (2500 -> 5000 samples): the Conv1d/
AdaptiveAvgPool1d backbones in src/models_1d.py are shape-agnostic, so this
is not required for the model to run, but a fixed-width kernel covers a
different real-world time window at a different sampling rate (e.g. a
15-tap kernel spans 60 ms at 250 Hz vs 30 ms at 500 Hz). Resampling keeps
kernel receptive fields physiologically comparable to the PTB-XL/ACS-
pretrained checkpoints this is meant to transfer-learn from.

Usage:
    python3 scripts/cache_echonext_signals.py --data-dir data/echonext_raw
    python3 scripts/cache_echonext_signals.py --selftest   # resample/normalise math only, no real data needed
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.signal


def resample_to(signals: np.ndarray, target_length: int) -> np.ndarray:
    """(T, 12) -> (target_length, 12) via FFT-based polyphase resampling."""
    if signals.shape[0] == target_length:
        return signals
    return scipy.signal.resample(signals, target_length, axis=0)


def normalise(signals: np.ndarray) -> np.ndarray:
    mean = signals.mean(axis=0, keepdims=True)
    std = signals.std(axis=0, keepdims=True)
    return (signals - mean) / np.maximum(std, 1e-6)


def cmd_convert(args: argparse.Namespace) -> None:
    metadata = pd.read_csv(args.data_dir / args.metadata_name)
    print(f"Doc metadata: {len(metadata)} dong tong, split: {metadata['split'].value_counts().to_dict()}")

    per_split_kept: dict[str, pd.DataFrame] = {}
    total = 0
    for split in args.splits:
        subset = metadata[metadata["split"] == split].reset_index(drop=True)
        waveform_path = args.data_dir / f"EchoNext_{split}_waveforms.npy"
        waveform_len = len(np.load(waveform_path, mmap_mode="r"))
        if len(subset) != waveform_len:
            raise SystemExit(
                f"split={split}: metadata co {len(subset)} dong nhung {waveform_path.name} co "
                f"{waveform_len} ban ghi - gia dinh ve thu tu/so luong hang co the sai, kiem tra lai."
            )
        keep = subset[args.label_col].notna().to_numpy()
        n_dropped = int((~keep).sum())
        if n_dropped:
            print(f"  split={split}: bo {n_dropped}/{len(subset)} ban ghi thieu {args.label_col}")
        subset = subset.assign(_waveform_position=np.arange(len(subset)))[keep].reset_index(drop=True)
        per_split_kept[split] = subset
        total += len(subset)

    if args.limit:
        total = min(total, args.limit)

    args.outdir.mkdir(parents=True, exist_ok=True)
    signal_path = args.outdir / "signals.f16.npy"
    memmap = np.lib.format.open_memmap(
        signal_path, mode="w+", dtype=np.float16, shape=(total, 12, args.target_length)
    )

    rows = []
    cache_index = 0
    for split, subset in per_split_kept.items():
        if cache_index >= total:
            break
        waveforms = np.load(args.data_dir / f"EchoNext_{split}_waveforms.npy", mmap_mode="r")
        for _, row in subset.iterrows():
            if cache_index >= total:
                break
            raw = np.asarray(waveforms[row["_waveform_position"], 0], dtype=np.float32)  # (2500, 12)
            prepared = normalise(resample_to(raw, args.target_length))
            memmap[cache_index] = prepared.T.astype(np.float16)
            lvef_value = row.get(args.value_col)
            has_value = pd.notna(lvef_value)
            rows.append(
                {
                    "Patient_id": row[args.id_col],
                    "record_id": row[args.record_id_col],
                    "cache_index": cache_index,
                    "lvef_binary_45": int(row[args.label_col]),
                    "lvef_value": float(lvef_value) if has_value else float("nan"),
                    "lvef_binary_40": float(lvef_value <= args.threshold_40) if has_value else float("nan"),
                    "split": split,
                }
            )
            cache_index += 1
            if cache_index % 5000 == 0:
                print(f"  {cache_index}/{total}")
    memmap.flush()
    del memmap

    index = pd.DataFrame(rows)
    index.to_csv(args.outdir / "index.csv", index=False)
    n_has_40 = int(index["lvef_binary_40"].notna().sum())
    print(f"Da ghi {len(index)} ban ghi vao {args.outdir}")
    print(f"split phan bo: {index['split'].value_counts().to_dict()}")
    print(f"lvef_binary_45 phan bo (luon co, tu {args.label_col}): {index['lvef_binary_45'].value_counts().to_dict()}")
    print(
        f"lvef_binary_40 phan bo ({n_has_40}/{len(index)} ban ghi co {args.value_col}, "
        f"NaN cho phan con lai - loc truoc khi train): "
        f"{index['lvef_binary_40'].value_counts(dropna=True).to_dict()}"
    )
    print(f"So benh nhan (Patient_id) duy nhat: {index['Patient_id'].nunique()}")


def _selftest() -> None:
    rng = np.random.default_rng(0)
    raw = rng.normal(0, 1, (2500, 12)).astype(np.float32)

    same_length = resample_to(raw, 2500)
    assert same_length.shape == (2500, 12)
    assert np.allclose(same_length, raw)

    upsampled = resample_to(raw, 5000)
    assert upsampled.shape == (5000, 12), f"expected (5000, 12), got {upsampled.shape}"

    normalised = normalise(upsampled)
    assert normalised.shape == upsampled.shape
    means = normalised.mean(axis=0)
    stds = normalised.std(axis=0)
    assert np.allclose(means, 0.0, atol=1e-5), f"per-lead mean should be ~0, got {means}"
    assert np.allclose(stds, 1.0, atol=1e-2), f"per-lead std should be ~1, got {stds}"

    print("resample_to: identity + 250Hz->500Hz upsampling shape OK")
    print("normalise: per-lead zero-mean/unit-variance OK")
    print("all selftest assertions passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--data-dir", type=Path, help="Directory holding the extracted EchoNext-Mini files")
    parser.add_argument("--metadata-name", default="EchoNext_metadata_100k.csv")
    parser.add_argument(
        "--splits", nargs="+", default=["train", "val", "test"],
        help="Which EchoNext_<split>_waveforms.npy files to include. Default excludes 'no_split' "
        "per the dataset's own README (those ECGs are not meant for model training).",
    )
    parser.add_argument("--id-col", default="patient_key")
    parser.add_argument("--record-id-col", default="ecg_key")
    parser.add_argument("--label-col", default="lvef_lte_45_flag", help="Ready-made LVEF<=45%% flag -- always populated, no NaN.")
    parser.add_argument(
        "--value-col", default="lvef_value",
        help="Continuous EF (%%) column, used to derive the TRUE lvef_binary_40 label "
        "(threshold-col missing/NaN for ~9%% of rows -- those get lvef_binary_40=NaN, "
        "filter them out before training on that label).",
    )
    parser.add_argument("--threshold-40", type=float, default=40.0, help="lvef_binary_40 = 1 if lvef_value <= this.")
    parser.add_argument("--outdir", type=Path, default=Path("data/echonext_signal_cache"))
    parser.add_argument("--target-length", type=int, default=5000, help="500 Hz x 10s, matches PTB-XL/ACS caches")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap total records across all splits combined (for a quick smoke run), "
        "filled from --splits in order -- not an even sample across splits.",
    )
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return
    if not args.data_dir:
        raise SystemExit("--data-dir is required (or use --selftest)")
    cmd_convert(args)


if __name__ == "__main__":
    main()
