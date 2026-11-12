"""Hand-engineered DSP feature baseline for the LVEF<=40%/<=45% screening
pipeline (Research/VinStroke_ECG_LVEF_Research_Proposal.md), same rationale
as train_dsp_features_acs.py: before committing to fusing DSP features into
the deep 1D models, measure how much signal timing/morphology carries on its
own for the binary LVEF-screening head. Works against the cache layout
(signals.f16.npy + index.csv) written by both
scripts/cache_lvef_signals.py (label lvef_binary_40, MIMIC/Vinmec) and
scripts/cache_echonext_signals.py (label lvef_binary_45, EchoNext-Mini) --
pass --label to pick which.

Reuses R-peak detection, wavelet denoising, and the base HR/HRV/QRS/ST/T
feature set from train_dsp_features_acs.py (same signal chain, same
per-lead z-normalised input) rather than re-deriving them, then layers on a
few LVEF-motivated additions: QRS duration, QTc (Bazett), a T-wave-inversion
count, HRV RMSSD, and RR irregularity.

IMPORTANT correctness caveat: the input cache is normalised PER LEAD
(cache_lvef_signals.py::normalise -- mean/std along the time axis of EACH
lead independently), which forces every lead to unit variance. That destroys
the *cross-lead* amplitude relationships that real voltage-based criteria
depend on -- frontal QRS axis, precordial R-wave progression, Sokolow-Lyon
voltage. Those are deliberately NOT implemented here: computing them on this
cache would silently produce numbers that look like clinical features but
carry none of their clinical meaning. Only within-lead timing (R-peak
spacing, QRS/QT duration) and within-lead sign (T-wave inverted or not)
survive per-lead normalisation intact, so the new features here are
restricted to those two categories.

Usage:
    python3 src/train_dsp_features_lvef.py --cache data/lvef_signal_cache --label lvef_binary_40
    python3 src/train_dsp_features_lvef.py --cache data/echonext_signal_cache --label lvef_binary_45
    python3 src/train_dsp_features_lvef.py --selftest
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, roc_auc_score

from train_dsp_features_acs import (
    LEAD_NAMES,
    R_PEAK_LEAD,
    detect_r_peaks,
    wavelet_denoise,
)
from train_dsp_features_acs import record_features as base_record_features


def assign_patient_splits(frame: pd.DataFrame, seed: int, val_frac: float, test_frac: float) -> pd.Series:
    """Copied from src/train_signal_1d.py::assign_patient_splits -- importing
    that module here would pull in its top-level torch/nn imports for a
    12-line function this sklearn-only script doesn't otherwise need. Keep in
    sync if the split logic changes there."""
    patients = frame["Patient_id"].unique()
    shuffled = np.random.default_rng(seed).permutation(patients)
    n_val = int(len(shuffled) * val_frac)
    n_test = int(len(shuffled) * test_frac)
    assignment = {}
    for patient in shuffled[:n_val]:
        assignment[patient] = "val"
    for patient in shuffled[n_val : n_val + n_test]:
        assignment[patient] = "test"
    for patient in shuffled[n_val + n_test :]:
        assignment[patient] = "train"
    return frame["Patient_id"].map(assignment)


def hrv_rmssd_ms(rr: np.ndarray) -> float:
    """Root mean square of successive RR differences -- short-term HRV,
    complementary to the SDNN base_record_features already computes."""
    if len(rr) < 2:
        return float("nan")
    diffs = np.diff(rr) * 1000.0
    return float(np.sqrt(np.mean(diffs**2)))


def rr_irregularity_frac(rr: np.ndarray) -> float:
    """Fraction of RR intervals deviating >20% from the record's median RR --
    a crude ectopy/arrhythmia proxy, not a beat classifier."""
    if len(rr) < 2:
        return float("nan")
    median_rr = np.median(rr)
    if median_rr <= 0:
        return float("nan")
    deviation = np.abs(rr - median_rr) / median_rr
    return float(np.mean(deviation > 0.2))


def qtc_bazett_ms(trace: np.ndarray, r_peaks: np.ndarray, fs: float) -> float:
    """Median Bazett-corrected QT (ms) across beats. T-wave end is a
    threshold crossing at 15% of that beat's own T-peak amplitude relative to
    a pre-QRS baseline -- a first-pass heuristic like the rest of this
    module's R-peak/QRS/ST logic, not a clinical-grade delineator. Uses the
    RR interval PRECEDING each beat (standard Bazett convention), so the
    first beat is skipped. Implausible per-beat QT estimates (outside
    150-600 ms) are dropped rather than corrupting the median."""
    qtc_values = []
    for i in range(1, len(r_peaks) - 1):
        r = r_peaks[i]
        rr_sec = (r_peaks[i] - r_peaks[i - 1]) / fs
        if rr_sec <= 0:
            continue
        baseline_idx = r - int(0.08 * fs)
        qrs_onset = r - int(0.02 * fs)
        search_start = r + int(0.08 * fs)
        search_end = min(r + int(0.44 * fs), len(trace) - 1)
        if baseline_idx < 0 or search_end <= search_start:
            continue
        baseline = trace[baseline_idx]
        segment = trace[search_start:search_end] - baseline
        if len(segment) < 5:
            continue
        t_peak_rel = int(np.argmax(np.abs(segment)))
        t_peak_amp = segment[t_peak_rel]
        if abs(t_peak_amp) < 1e-6:
            continue
        threshold = 0.15 * abs(t_peak_amp)
        tail = segment[t_peak_rel:]
        below = np.where(np.abs(tail) <= threshold)[0]
        t_end_rel = t_peak_rel + (int(below[0]) if len(below) else len(tail) - 1)
        t_end_idx = search_start + t_end_rel
        qt_sec = (t_end_idx - qrs_onset) / fs
        if not (0.15 <= qt_sec <= 0.6):
            continue
        qtc_values.append((qt_sec / np.sqrt(rr_sec)) * 1000.0)
    return float(np.median(qtc_values)) if qtc_values else float("nan")


def t_wave_trough(trace: np.ndarray, r_peaks: np.ndarray, fs: float) -> float:
    """Median per-beat MIN (not max) of the T-wave window relative to
    baseline. base_record_features' own t_{lead} takes the max in this
    window, which understates inversion depth when the whole T-wave sits
    below baseline -- this is complementary, not a replacement, using the
    same offsets as base_record_features so the two stay comparable."""
    t_lo_offset, t_hi_offset = int(0.20 * fs), int(0.30 * fs)
    pr_offset = int(0.08 * fs)
    troughs = []
    for r in r_peaks[1:-1]:
        baseline_idx = r - pr_offset
        t_lo, t_hi = r + t_lo_offset, r + t_hi_offset
        if baseline_idx < 0 or t_hi >= len(trace):
            continue
        baseline = trace[baseline_idx]
        troughs.append(float(np.min(trace[t_lo:t_hi]) - baseline))
    return float(np.median(troughs)) if troughs else float("nan")


def lvef_record_features(signals: np.ndarray, fs: float) -> dict[str, float]:
    """signals: (12, L) in LEAD_NAMES order, already bandpass-filtered and
    per-lead z-normalised (see module docstring for why cross-lead amplitude
    features are deliberately excluded). Layers LVEF-motivated additions on
    top of train_dsp_features_acs.record_features's base HR/HRV/QRS/ST/T
    set. Empty dict if too few beats to trust (mirrors the base function)."""
    base = base_record_features(signals, fs)
    if not base:
        return {}

    r_lead_idx = LEAD_NAMES.index(R_PEAK_LEAD)
    r_peaks = detect_r_peaks(signals[r_lead_idx], fs)
    rr = np.diff(r_peaks) / fs

    qrs_widths = [base[f"qrs_ms_{lead}"] for lead in LEAD_NAMES if not np.isnan(base[f"qrs_ms_{lead}"])]
    extra: dict[str, float] = {
        "qrs_duration_max_ms": float(np.max(qrs_widths)) if qrs_widths else float("nan"),
        "hrv_rmssd_ms": hrv_rmssd_ms(rr),
        "rr_irregularity_frac": rr_irregularity_frac(rr),
        "qtc_bazett_ms": qtc_bazett_ms(signals[r_lead_idx], r_peaks, fs),
    }

    t_inverted = 0
    for lead_idx, lead in enumerate(LEAD_NAMES):
        trough = t_wave_trough(signals[lead_idx], r_peaks, fs)
        extra[f"t_trough_{lead}"] = trough
        if not np.isnan(trough) and trough < -0.5:
            t_inverted += 1
    extra["t_inverted_lead_count"] = float(t_inverted)

    return {**base, **extra}


def score(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, float]:
    metrics: dict[str, float] = {"n": len(y_true), "n_positive": int(y_true.sum())}
    if len(np.unique(y_true)) < 2:
        metrics["auroc"] = metrics["auprc"] = float("nan")
    else:
        metrics["auroc"] = float(roc_auc_score(y_true, y_prob))
        metrics["auprc"] = float(average_precision_score(y_true, y_prob))
    return metrics


def cmd_train(args: argparse.Namespace) -> None:
    index = pd.read_csv(args.cache / "index.csv")
    if args.limit:
        index = index.head(args.limit).reset_index(drop=True)

    if args.split_from:
        reference = pd.read_csv(args.split_from)[["record_id", "split"]]
        reference["record_id"] = reference["record_id"].astype(str)
        index["record_id"] = index["record_id"].astype(str)
        # Drop any pre-existing 'split' column before merging: cache_echonext_signals.py's
        # index.csv already carries the dataset's own official split, and --split-from
        # is often pointed at that SAME file to reuse it -- without the drop, pandas
        # would suffix both to split_x/split_y and the "split" column used below would
        # silently not exist.
        index = index.drop(columns=["split"], errors="ignore").merge(reference, on="record_id", how="inner")
        print(f"Dung split tu {args.split_from}: {len(index)} ban ghi khop")
    else:
        index["split"] = assign_patient_splits(index, args.seed, args.val_frac, args.test_frac)
        print(f"Chia split theo benh nhan (seed={args.seed}): {index['split'].value_counts().to_dict()}")

    before = len(index)
    index = index[index[args.label].notna()].reset_index(drop=True)
    if len(index) != before:
        print(f"Bo {before - len(index)}/{before} ban ghi thieu nhan {args.label}")

    if args.features_cache and args.features_cache.exists():
        feat_frame = pd.read_parquet(args.features_cache)
        feature_columns = [c for c in feat_frame.columns if c != "cache_index"]
        print(f"Doc dac trung da cache: {args.features_cache} ({len(feat_frame)} ban ghi)")
    else:
        signals_memmap = np.load(args.cache / "signals.f16.npy", mmap_mode="r")
        subset_index = np.sort(index["cache_index"].to_numpy())
        print(f"Trich dac trung DSP cho {len(subset_index)} ban ghi (R-peak / QRS / QTc / T-wave)...")
        rows = []
        start = time.time()
        for position, cache_idx in enumerate(subset_index):
            record = np.asarray(signals_memmap[cache_idx], dtype=np.float64)
            if args.denoise:
                record = wavelet_denoise(record.T).T
            feats = lvef_record_features(record, args.fs)
            feats["cache_index"] = int(cache_idx)
            rows.append(feats)
            if (position + 1) % 2000 == 0:
                print(f"  {position + 1}/{len(subset_index)} ({time.time() - start:.0f}s)")
        feat_frame = pd.DataFrame(rows)
        feature_columns = [c for c in feat_frame.columns if c != "cache_index"]
        if args.features_cache:
            args.features_cache.parent.mkdir(parents=True, exist_ok=True)
            feat_frame.to_parquet(args.features_cache)
            print(f"Da luu dac trung: {args.features_cache}")

    frame = index.merge(feat_frame, on="cache_index", how="inner")
    no_beats = frame[feature_columns].isna().all(axis=1).sum()
    print(f"Ban ghi khong tach duoc nhip tim (SQI qua kem): {no_beats}/{len(frame)}")

    train_frame = frame[frame["split"] == "train"].reset_index(drop=True)
    val_frame = frame[frame["split"] == "val"].reset_index(drop=True)
    test_frame = frame[frame["split"] == "test"].reset_index(drop=True)
    print(f"train={len(train_frame)} val={len(val_frame)} test={len(test_frame)}")

    x_train = train_frame[feature_columns].to_numpy(dtype=np.float64)
    x_val = val_frame[feature_columns].to_numpy(dtype=np.float64)
    x_test = test_frame[feature_columns].to_numpy(dtype=np.float64)
    y_train = train_frame[args.label].to_numpy()
    y_val = val_frame[args.label].to_numpy()
    y_test = test_frame[args.label].to_numpy()

    if y_train.sum() == 0 or y_train.sum() == len(y_train):
        raise SystemExit(f"{args.label}: chi co mot lop trong train, khong the huan luyen.")

    clf = HistGradientBoostingClassifier(
        max_depth=4, max_iter=300, learning_rate=0.05, random_state=args.seed, early_stopping=True,
    )
    clf.fit(x_train, y_train)
    prob_val = clf.predict_proba(x_val)[:, 1].astype(np.float32)
    prob_test = clf.predict_proba(x_test)[:, 1].astype(np.float32)

    perm = permutation_importance(
        clf, x_val, y_val, scoring="roc_auc", n_repeats=5, random_state=args.seed, n_jobs=-1,
    )
    ranked = sorted(zip(feature_columns, perm.importances_mean), key=lambda kv: -kv[1])[:10]
    print("Top dac trung -> " + ", ".join(f"{name}={value:.3f}" for name, value in ranked))

    val_metrics = score(y_val, prob_val)
    test_metrics = score(y_test, prob_test)
    print(f"Val:  {val_metrics}")
    print(f"Test: {test_metrics}")

    if args.probabilities:
        args.probabilities.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            args.probabilities,
            labels=np.array([args.label]),
            y_val=y_val.reshape(-1, 1), prob_val=prob_val.reshape(-1, 1),
            y_test=y_test.reshape(-1, 1), prob_test=prob_test.reshape(-1, 1),
            id_val=val_frame["record_id"].to_numpy(),
            id_test=test_frame["record_id"].to_numpy(),
        )
        print(
            f"Da ghi xac suat: {args.probabilities} "
            f"(dung voi: python3 scripts/score_lvef_screening.py --probabilities {args.probabilities} --label {args.label})"
        )

    if args.metrics_json:
        import json

        args.metrics_json.parent.mkdir(parents=True, exist_ok=True)
        with args.metrics_json.open("w") as handle:
            json.dump(
                {
                    "val": val_metrics,
                    "test": test_metrics,
                    "n_no_beats": int(no_beats),
                    "top_features": [[name, float(value)] for name, value in ranked],
                },
                handle,
                indent=2,
            )
        print(f"Da ghi metrics: {args.metrics_json}")


def _selftest() -> None:
    fs = 500.0
    duration_s = 10.0
    n = int(fs * duration_s)
    rng = np.random.default_rng(0)

    # Synthetic single-lead trace: ~75 bpm beats, each a QRS spike + an upright T-wave bump.
    signal = rng.normal(0, 0.02, n)
    r_positions = []
    beat_t = 0.2
    while beat_t < duration_s - 0.3:
        r_idx = int(beat_t * fs)
        r_positions.append(r_idx)
        signal[max(0, r_idx - 5) : r_idx + 5] += 3.0 * np.hanning(10)
        t_idx = r_idx + int(0.25 * fs)
        if t_idx + 20 < n:
            signal[t_idx - 10 : t_idx + 10] += 0.8 * np.hanning(20)
        beat_t += 0.8 + rng.normal(0, 0.01)
    r_peaks_truth = np.array(r_positions)
    rr = np.diff(r_peaks_truth) / fs

    detected = detect_r_peaks(signal, fs)
    assert len(detected) >= len(r_peaks_truth) - 2, f"expected ~{len(r_peaks_truth)} R-peaks, detected {len(detected)}"

    rmssd = hrv_rmssd_ms(rr)
    assert rmssd >= 0.0 and not np.isnan(rmssd)

    irregularity = rr_irregularity_frac(rr)
    assert 0.0 <= irregularity <= 1.0

    qtc = qtc_bazett_ms(signal, detected, fs)
    assert np.isnan(qtc) or 200.0 <= qtc <= 600.0, f"qtc out of plausible range: {qtc}"

    trough = t_wave_trough(signal, detected, fs)
    assert not np.isnan(trough)
    assert trough > -1.0, f"expected an upright synthetic T-wave (trough not deeply negative), got {trough}"

    signals_12lead = np.tile(signal, (12, 1))
    features = lvef_record_features(signals_12lead, fs)
    assert features, "expected a non-empty feature dict for a clean synthetic signal"
    for key in ("qrs_duration_max_ms", "hrv_rmssd_ms", "rr_irregularity_frac", "qtc_bazett_ms", "t_inverted_lead_count"):
        assert key in features, f"missing expected feature: {key}"
    assert features["t_inverted_lead_count"] == 0.0, "upright synthetic T-waves should not count as inverted"

    print(f"detected {len(detected)}/{len(r_peaks_truth)} synthetic R-peaks")
    print(f"hrv_rmssd_ms={rmssd:.1f}, rr_irregularity_frac={irregularity:.2f}, qtc_bazett_ms={qtc:.1f}")
    print(f"t_wave_trough={trough:.3f} (upright synthetic T, should not be strongly negative)")
    print("all selftest assertions passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--cache", type=Path, default=Path("data/lvef_signal_cache"))
    parser.add_argument(
        "--label", default="lvef_binary_40",
        help="lvef_binary_40 for cache_lvef_signals.py's cache, lvef_binary_45 for cache_echonext_signals.py's",
    )
    parser.add_argument("--fs", type=float, default=500.0)
    parser.add_argument(
        "--denoise", action="store_true",
        help="Apply VisuShrink wavelet-threshold denoising before feature extraction, same as train_dsp_features_acs.py.",
    )
    parser.add_argument(
        "--split-from", type=Path, default=None,
        help="Optional record_id,split CSV. Default: patient-grouped assign_patient_splits with the same "
        "seed/fracs as src/train_signal_1d.py's default, so DSP and deep-model runs are comparable.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-frac", type=float, default=0.15)
    parser.add_argument("--test-frac", type=float, default=0.15)
    parser.add_argument("--features-cache", type=Path, default=None, help="Save/load extracted features as parquet to skip re-extraction.")
    parser.add_argument("--probabilities", type=Path, default=Path("probs_lvef_dsp_features.npz"))
    parser.add_argument("--metrics-json", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return
    cmd_train(args)


if __name__ == "__main__":
    main()
