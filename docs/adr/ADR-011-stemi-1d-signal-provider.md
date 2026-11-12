# ADR-011: Second ECGAIProvider for a 1D-Signal STEMI Screener

- **Status:** Accepted
- **Date:** 2026-08-28

## Decision

Add `TorchTCNStemiProvider` (`src/vinami/ecg/adapters/torch_stemi_provider.py`) as a second,
independent `ECGAIProvider` adapter, selected under the provider key `torch-acs-stemi`. It serves
the ACS-native TCN checkpoint (`models/acs_tcn.pt`, trained via `src/train_signal_1d.py --model
tcn` on ACS-ECG-Dataset-2026) and only surfaces the `STEMI` head; `OMI`/`NSTEMI`/`UA`/`AMI` are
computed by the same forward pass but withheld from the result, since only STEMI has been
evaluated for this integration.

Alongside it, `src/vinami/ecg/domain/risk_patterns.py` adds a deterministic, non-ML rule
(`detect_diffuse_st_depression_with_reciprocal_elevation`) implementing the one ECG-morphology
criterion from Vinmec's `VMEC_CM127` clinical pathway (Phu luc 3) that is machine-checkable from a
waveform alone: diffuse ST depression (>=1 mm in >=6 leads) with reciprocal ST elevation in
V1/aVR, a pattern associated with left-main / triple-vessel disease. The STEMI provider calls this
rule and folds its result into the same `ECGPredictionResult`.

## Rationale

The existing `TorchConvNeXtV2Provider` (ADR-004) is architecturally locked to the PTB-XL 5-class
morphology checkpoint: 2D CWT-image input at 100 Hz / 1000 samples/lead, backed by a `timm`
architecture. The STEMI checkpoint is a 1D raw-signal model (TCN) at 500 Hz / 5000 samples/lead —
a different input contract, not an extension of the existing one. TCN is not available through
`timm`, so per the program charter and ADR-004 (`vinami-acs` may consume the ECG research
prototype only through the `ECGAIProvider` port, never by importing its model code), the TCN class
is reimplemented locally rather than imported from `src/models_1d.py`.

The ST-depression/reciprocal-elevation rule has no independent clinical validation of its own — it
is a fixed-offset amplitude heuristic, not a validated ST-measurement algorithm — so it is treated
as a demonstration rule under ADR-006, not a model output, and is barred from carrying more
clinical weight than that.

## Consequences

- A second checkpoint format and a second self-contained model-architecture copy now exist in
  `vinami-acs`; if `src/models_1d.py::TCN` changes upstream, `torch_stemi_provider.py`'s
  `_build_tcn` must be updated by hand — there is no shared import to keep them in sync
  automatically.
- The STEMI rule-out threshold (0.143, at target sensitivity 0.90 on the validation split via
  `src/clinical_evaluation.py --probabilities acs_tcn_probs.npz`) is a module-level constant with
  documented provenance, not re-derived at runtime. Replacing the checkpoint requires re-running
  that evaluation and updating the constant.
- Neither the STEMI score nor the ST-pattern rule can rule out AMI, activate a cath lab, or act
  without physician confirmation — both flow into the same `predicted_findings`/`warnings`
  contract as the existing PTB-XL provider, under the same ADR-006 boundary.
- OMI/NSTEMI/UA/AMI outputs from the same checkpoint are deliberately not exposed yet; adding them
  is a follow-up scoped by their own evaluation, not implied by this decision.
- 1D Grad-CAM explainability is out of scope for this increment; `explain()` returns an explicit
  "not implemented" result rather than a silent stub.
