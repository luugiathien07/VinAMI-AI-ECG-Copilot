# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Two independent codebases, one repo

This repo holds two deliberately separated systems. Know which one a task targets before editing.

| | Location | What it is |
|---|---|---|
| **VinAMI ACS OS** | `vinami-acs/` | Modular-monolith clinical workflow app: FastAPI + PostgreSQL + Redis + worker + Next.js dashboard |
| **VinStroke-ECG prototype** | repo root: `app.py`, `app_vi.py`, `src/`, `scripts/`, `models/`, `data/`, notebooks | Streamlit + PyTorch ECG research prototype |

`docs/PROGRAM_CHARTER.md` makes the boundary binding: the root prototype "must be preserved unless a task explicitly changes that prototype," and `vinami-acs` may consume it **only** through the `ECGAIProvider` port — never by importing model code. Do not refactor one side while working on the other.

Governance docs (charter, ADRs, architecture, MVP, UI, validation) live in **root `docs/`**, not `vinami-acs/docs/` (which holds app-level frontend/recovery/demo docs). The charter and README still describe Streamlit as the ACS UI; the shipped UI is Next.js (`apps/web`), with `apps/dashboard` as the legacy Streamlit surface.

## vinami-acs commands

All from `vinami-acs/`. Python 3.12 (`>=3.12,<3.13`), Node >=20, pnpm 10.34.5.

```bash
cp .env.example .env && docker compose up --build          # full stack
docker compose exec api python -m scripts.seed_demo_happy_cases
docker compose up -d postgres redis migrate api worker && pnpm dev   # faster FE loop
```
Web `:3000` · API `:8000` (`/health`, `/ready`, `/openapi.json`) · Streamlit `:8501` · Prometheus `:9090`.

Backend (`Makefile` wraps Docker; CI runs the same tools bare):
```bash
make test lint format typecheck migrate seed        # or:
docker compose run --rm api pytest tests/integration/test_r1_lifecycle_api.py::test_name -q
pytest tests/unit/test_config.py::test_name         # local: unit/contract only, no DB
ruff check src apps tests scripts && mypy src apps scripts   # mypy strict = true
```
Integration tests **skip** unless `VINAMI_DATABASE_URL` is set (`tests/integration/conftest.py` creates a throwaway `<db>_sprint3_test`, runs real Alembic migrations, drops it). Run them through the `api` service.

Frontend:
```bash
pnpm typecheck lint test                     # vitest; coverage gates lines75/fn70/branch65/stmt75
pnpm vitest run apps/web/shared/errors/presentation.test.ts -t "maps 409"
pnpm test:architecture                       # tests/frontend/architecture.test.ts
pnpm exec playwright test apps/web/e2e/r3-ecg-workspace.spec.ts -g "waveform"
pnpm quality                                 # format:check + lint + typecheck + api:check + test + storybook:build
```
Playwright runs `workers: 1`, `fullyParallel: false`, in **Edge** (`channel: "msedge"`), and needs a live API with seeded Postgres; visual specs need Storybook served at `STORYBOOK_BASE_URL`.

Migrations and OpenAPI contracts have non-obvious follow-up steps:
```bash
docker compose run --rm api alembic revision --autogenerate -m "rNN_description"
pnpm api:snapshot && pnpm api:generate && pnpm api:check   # API must be running
```
- After a migration, bump `EXPECTED_ALEMBIC_HEAD` in `src/vinami/shared/schema.py` and add new tables to `CRITICAL_TABLES` there, or `/ready` fails. New ORM modules must be imported in `src/vinami/shared/orm_registry.py::metadata()` or autogenerate won't see them.
- `packages/contracts/openapi.json` and `packages/contracts/src/generated.ts` are generated — never hand-edit. Any route or schema change requires the snapshot/generate/check cycle.
- `VINAMI_IDENTITY_MODE` has no default; `AppContainer.create` raises if unset. `VINAMI_ECG_PROVIDER` ∈ `mock` | `torch` | `ptbxl-showcase`.

## vinami-acs architecture

Twelve bounded contexts under `src/vinami/`, each with the same five packages — `<context>/{domain,application,ports,adapters,api}`: `patient, clinical_case, triage, ecg, troponin, reasoning, workflow, alerts, timeline, audit, model_registry, identity` (plus `integration`). `src/vinami/shared/` holds config, container, database, UoW, events, idempotency, metrics, ORM registry.

Dependency direction is inward and **machine-enforced** by `tests/architecture/test_dependency_rules.py` (prose in `docs/architecture/dependency-rules.md`): `domain/` may not import `fastapi, sqlalchemy, streamlit, torch, timm, onnxruntime, redis`, `vinami.shared.database`, `vinami.shared.container`, any `.adapters`/`.api` module, or another context's `domain`. Contexts share only IDs, value primitives, event envelopes, and published DTOs.

Request path: `apps/api/main.py` → `apps/api/app.py::create_app` builds one `AppContainer` onto `app.state.container` (no globals), registers ~13 handlers mapping errors to **RFC 7807 problem+json** with a `correlation_id`, installs correlation-ID middleware (non-UUID `X-Correlation-ID` ⇒ 400), and includes 11 thin routers. Routers only validate Pydantic models (`extra="forbid"`), build a command dataclass, and call `<Command>Handler(uow_factory).execute(...)`. Application handlers depend on `Protocol` ports only; `SQLAlchemyUnitOfWork` is injected by the container.

Cross-cutting, in `apps/api/http.py`:
- `authorize_for(Permission.X)` per route; permissions are a `StrEnum` in `src/vinami/identity/domain/security.py`, matrix in `vinami-acs/docs/recovery/r1-5/endpoint-permissions.md`.
- `command_headers_for(...)` **requires** an `Idempotency-Key` and builds `ActorContext` from verified claims only — client-supplied actor/role headers are never authoritative.
- Idempotency is persistent: `lock_and_get(actor_id, "CommandName", key)` replays the stored DTO, or raises `IdempotencyConflictError` when a key is reused with a different `command_fingerprint`.

Outbox and worker: `SQLAlchemyUnitOfWork.commit()` writes `OutboxEventRecord` rows **in the same transaction** as aggregate writes (`StaleDataError` → `OptimisticConcurrencyError` → 409). Never publish events outside the UoW. `InternalEventDispatcher.dispatch_pending()` claims rows `FOR UPDATE SKIP LOCKED`, dedups per handler via `EventConsumptionRecord`, retries with backoff, then `DEAD_LETTER`. `apps/worker/main.py` loops timers + dispatch. Replay is explicit: `scripts/replay_events.py --event-type X --start ISO --end ISO`.

Web → API: Server Components use `apps/web/shared/api/client.ts` (`server-only`) straight to FastAPI; Client Components go through `apps/web/shared/api/browser.ts` → the BFF at `apps/web/app/api/backend/[...path]/route.ts`, which enforces **separate GET and POST regex allow-lists** (unlisted paths ⇒ 404). Adding a client-called endpoint means adding it to that allow-list. `packages/api-client` wraps `openapi-fetch` and maps failures to `VinamiApiError.kind`; only safe GETs retry once, commands never do.

## Clinical safety constraints (these gate code review)

From `docs/PROGRAM_CHARTER.md` and `docs/adr/ADR-006-clinical-safety-boundary.md`: ECG alone can never rule out AMI; AI output can never activate a cath lab; consequential actions require physician confirmation; conflicting ECG/troponin/clinical data raises a review alert rather than a conclusion; every AI prediction records model name, version, timestamp, and input reference; every state transition is auditable. Demo rules must visibly read "Demonstration rule - not for clinical use."

`docs/ui/clinical-language-guide.md` defines approved vs prohibited wording, and `tests/frontend/architecture.test.ts` fails the build on the strings "ami ruled out", "patient diagnosed by ai", "cath lab activated by ai", "clinically validated". That test also allow-lists clinical route pages and forbids `server-only` imports from `"use client"` files; `eslint.config.mjs` bans `any` and all `localStorage` access.

Microservices, Kubernetes, and Kafka are out of scope without a new ADR. The charter's Sprint Completion Contract expects format/lint/typecheck/tests run, architecture docs updated, an ADR for significant decisions, the exact commands printed, limitations stated honestly, and unrelated modules left unchanged.

## ECG prototype commands

Run from the repo root — every default path is relative and imports depend on cwd.

```bash
streamlit run app_vi.py            # Vietnamese MVP UI; the live demo. Needs cwd = repo root
python3 src/train_multilabel_vision.py --manifest data/ptbxl_images_cwt_3000/manifest_images.csv \
  --model convnext_tiny --epochs 20 --output models/vinstroke_ecg_mvp.pt --metrics-json metrics_ptbxl.json
python3 src/train_signal_1d.py --cache data/acs_signal_cache --labels OMI STEMI NSTEMI UA AMI \
  --split-from data/acs_images_cwt/manifest_images.csv --output models/acs_resnet1d.pt
python3 src/predict_multilabel_vision.py --checkpoint models/vinstroke_ecg_mvp.pt --image path/to/ecg.png
PYTHONPATH=src python3 src/clinical_evaluation.py --probabilities probs_acs_1d.npz \
  --target-sensitivity OMI=0.95 --output clinical_acs_1d.json
python3 src/train_multilabel_vision.py --manifest sample_data/manifest_sample.csv --epochs 1   # smoke test
```

Import style is inconsistent and matters: `app_vi.py` uses `src.train_multilabel_vision` (needs cwd = root), while `src/predict_multilabel_vision.py`, `src/clinical_evaluation.py`, and `scripts/build_acs_manifest.py` use flat sibling imports — invoke them by script path or set `PYTHONPATH=src` / `PYTHONPATH=scripts`. Never `python -m src.clinical_evaluation`.

There is no test suite on this side; `sample_data/` (random-noise images, not ECGs) is the pipeline smoke test.

`run_demo.cmd` / `run_demo.ps1` hardcode `.venv\Scripts\python.exe` and are Windows-only. `app.py` resolves checkpoints through `src/model_utils.py::find_artifact_root()` looking for `VinStroke_ECG_artifacts/models/best_model*.pt`; that directory is empty and root-owned here, so `app.py` is effectively non-runnable — treat `app_vi.py` as the demo.

## ECG data pipeline

Two label tracks, never mixed (`scripts/build_benchmark_xlsx.py --track morphology|acs`):

- **Morphology** — 5 PTB-XL superclasses `NORM, MI, STTC, CD, HYP`. PTB-XL labels from `ptbxl_database.csv` + `scp_statements.csv` via `src/prepare_ptbxl_superclass_manifest.py`; all other CinC databases from the `# Dx:` SNOMED-CT header line via `CODE_TO_SUPERCLASS` in `scripts/build_snomed_ecg_manifest.py`.
- **ACS** — `OMI, STEMI, NSTEMI, UA, AMI`, angiography-confirmed, built by `scripts/build_acs_manifest.py` (CWT images) and `scripts/cache_acs_signals.py` (a `(N,12,5000)` float16 memmap for the 1D branch). Splits are grouped by `Patient_id`; `train_signal_1d.py` hard-fails on patient leakage across splits.

Signal→image: per lead, 0.5–40 Hz Butterworth → z-normalise → Morlet CWT (`pywt`, 96 bins 1–45 Hz) → `log1p(|coeffs|)` → magma `imshow`, assembled as a 12-lead 4×3 matplotlib grid. Vision training reads `manifest_images.csv` needing `image_path`, `split` (`train|val|test`), and one 0/1 column per label; model selection is on **val macro AUPRC**.

Traps worth knowing:
- The label list is duplicated across ~8 modules (`src/train_multilabel_vision.py`, `src/train_signal_1d.py`, `src/model_utils.py`, `src/prepare_ptbxl_superclass_manifest.py`, `scripts/build_snomed_ecg_manifest.py`, `scripts/merge_manifests.py`, `scripts/build_benchmark_xlsx.py`, `LABEL_VI` in `app_vi.py`). Column **order** matters — prediction zips probabilities against the checkpoint's stored `labels`.
- Two incompatible checkpoint formats (repo-trained `model`/`labels`/`args` vs Kaggle `model_state_dict`/`label_names`/`config`/`threshold`, different normalisation). Always load via `load_inference_checkpoint()` in `src/train_multilabel_vision.py`, never `torch.load` by hand.
- `best_model_convnextv2.pt` was trained on a *different* CWT rendering than the packaged `data/ptbxl_images_cwt*` PNGs, so its predictions on the sample images are meaningless — `app_vi.py` encodes this in `SAMPLES_MATCH_CHECKPOINT`. Use `models/vinstroke_ecg_mvp.pt` for packaged samples.
- `--norm-policy strict` (default) drops rhythm/axis-only records; `legacy` mislabels them NORM. Hence parallel `*_images_cwt` vs `*_images_cwt_strict` directories — never merge across policies.
- Pass `--signal-dir data/acs_ecg/row_data`; the default in `scripts/build_acs_manifest.py` points at a path that doesn't exist on disk.
- Macro AUROC/AUPRC are averaged only over "scorable" labels (both classes present in the split), reported with `scored_labels`. The resulting NaNs are intentional.
- `CHECKPOINT_METRICS` in `app_vi.py` is a hand-pasted dict; update it, `docs/report/multilabel-vision-training-experiments-2026-08.md`, and `docs/model-card-acs-ecg.md` when retraining.
- `data/`, `models/`, `*.pt`, `reports/`, `manifests/`, and `VinStroke_ECG_artifacts/` are gitignored.

Vietnamese is used deliberately in prototype console output, `SystemExit` messages, `HUONG_DAN_*.md`, and report markdown. `instruction.md` is a product/role prompt for the ACS OS, not a description of this prototype.

## 1D signal model benchmark (`docs/plan.odt`)

`docs/plan.odt` is the benchmark plan for the raw-signal (1D) track — **it has no text, only 2 embedded screenshots** (a model/metric/ensemble table for PTB-XL and for the ACS "Chongqing" dataset). Don't try to grep or text-extract it; unzip it and view `Pictures/*.png` directly. It lists ~16 architectures; only some are implemented here (see status in `docs/report/ket-qua-repo-2026-08.md` mục 7, kept current as models finish).

- `src/models_1d.py::BUILDERS` holds the plain-PyTorch architectures (`xresnet1d101`, `xresnet1d50`, `inceptiontime`, `tcn`, `omniscalecnn`, `s4`, `cnn_bilstm_transformer`), selected via `--model` on `src/train_ptbxl_1d.py` (PTB-XL, expects `patient_id`/`ecg_id`/pre-set `split` in the cache index) and `src/train_signal_1d.py` (ACS, patient-grouped split via `--split-from` or `assign_patient_splits`; `--model` was added on top of its own original hardcoded `ResNet1d`, which is still the `resnet1d` choice).
- `src/train_xecg.py` fine-tunes **xECG** (arXiv 2509.10151, an xLSTM foundation model), vendored MIT-licensed in `third_party/xecg/` with a pretrained `model.safetensors` already present — PTB-XL only so far, `--full-finetune` off by default (linear probing).
- `src/train_rocket_1d.py` runs **MiniRocket** (`sktime`) — CPU-only (numba JIT), so it doesn't compete for GPU memory with the others. Needs `--labels` passed explicitly for ACS; its argparse default is the PTB-XL label set and will `KeyError` silently-ish against ACS columns otherwise.
- **`omniscalecnn` and `s4` OOM on the ACS cache** (5000 samples @ 500 Hz vs. PTB-XL's 1000 @ 100 Hz) on an 8 GB GPU. `omniscalecnn`'s kernel-size table balloons to ~385M params at that length — OOMs in the optimizer state regardless of `--batch-size`. `s4` OOMs inside `torch.fft.rfft` on the long buffer — that one *is* `--batch-size`-fixable (16 worked where 64 didn't). `cnn_bilstm_transformer` was written specifically to dodge this class of bug: its CNN stem strides the sequence down ~16x before the BiLSTM/attention stages ever see it, so cost is paid on ~300 steps regardless of input length.
- `scripts/build_ptbxl_ensemble.py` averages any set of `--probabilities` npz files, aligning by `ecg_id`/`id_val`/`id_test` (an inner join, not row order — different scripts build val/test independently and end up with slightly different counts). Finding worth remembering before adding more models to an ensemble: **equal-weight averaging is not monotonic** — the best PTB-XL ensemble found is 3 models (`xresnet1d101`+`InceptionTime`+`tcn`); adding weaker single models (`xECG`, `omniscalecnn`, `s4`) on top made macro AUROC/AUPRC *worse*, not better.
