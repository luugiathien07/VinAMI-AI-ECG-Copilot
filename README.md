# VinAMI ACS Operating System

VinAMI la mot **research-use MVP** cho luong xu ly benh nhan nghi ngo **Acute Coronary Syndrome (ACS)**.

Muc tieu cua he thong:

- giup bac si theo doi ca ACS tren mot workspace ro rang;
- tong hop triage, sinh hieu, ECG, troponin, task, alert, timeline va audit;
- dung AI nhu mot module ho tro bang chung, khong thay the quyet dinh cua bac si.

> Research-use prototype only. VinAMI chua phai thiet bi y te da duoc kiem dinh, khong tu chan doan, khong tu rule-out AMI va khong tu kich hoat cath-lab.

## Trang thai hien tai

Da co ban MVP gom:

- FastAPI backend, PostgreSQL, Redis, worker va transactional outbox;
- Next.js clinical dashboard;
- Command Center cho ca ACS dang active;
- Clinical Case Workspace;
- triage va vitals;
- ECG workspace;
- troponin timeline;
- clinical reasoning demo rule;
- physician assessment va physician decision;
- task, reminder va alert;
- timeline, audit, outcome va learning data co ban;
- real-time/update foundation va integration simulator;
- ECG AI showcase voi PTB-XL/ConvNeXt artifact path;
- Dynamic ECG Comparison de so sanh ECG lan 1 va lan 2 o muc technical assist.

## Cau truc repo

```text
.
+-- HUONG_DAN_CHAY_LAI.md       # huong dan cho nguoi clone repo chay demo
+-- HUONG_DAN_MVP_ECG.md        # huong dan chay MVP model ECG da nhan + UI tieng Viet (app_vi.py)
+-- vinami-acs/                 # ung dung VinAMI ACS chinh
|   +-- apps/api/               # FastAPI
|   +-- apps/web/               # Next.js UI
|   +-- apps/worker/            # outbox/timer worker
|   +-- src/vinami/             # domain, application, ports, adapters
|   +-- migrations/             # Alembic migrations
|   +-- packages/contracts/     # OpenAPI generated contracts
|   +-- docs/                   # architecture, recovery, validation, UI docs
+-- Research/                   # ECG research reports
+-- src/                        # ECG research prototype utilities
+-- VinStroke_ECG_artifacts/    # local model/data artifacts, ignored by git
```

## Chay lai demo

Neu ban chi muon chay lai he thong de xem UI va demo cases, doc file:

```text
HUONG_DAN_CHAY_LAI.md
```

## Chay local

```bash
cd vinami-acs
cp .env.example .env
docker compose up --build
```

Sau khi chay:

- Web UI: `http://localhost:3000`
- API health: `http://localhost:8000/health`
- API ready: `http://localhost:8000/ready`
- OpenAPI: `http://localhost:8000/openapi.json`

## Huong phat trien

VinAMI se di theo huong **multimodal clinical evidence support**:

```text
Patient with suspected ACS
  -> Triage, symptoms and vitals
  -> Evidence completeness engine
  -> ECG quality/OOD, ECG five-label AI, serial ECG comparison
  -> Serial troponin trend intelligence
  -> Cardiac imaging AI when available
  -> Multimodal evidence model
  -> Clinical copilot with citations
  -> Physician assessment and decision
  -> Workflow, cath-lab review, outcome and follow-up
```

Nguyen tac an toan:

- AI chi ho tro phan tich bang chung;
- missing data khong duoc xem la negative evidence;
- ECG AI khong phai chan doan AMI;
- cath-lab review phai dua tren physician decision hop le;
- moi hanh dong quan trong can audit trail.

## Tai lieu quan trong

- `vinami-acs/docs/PROGRAM_CHARTER.md`
- `vinami-acs/docs/architecture/`
- `vinami-acs/docs/recovery/`
- `vinami-acs/docs/frontend/`
- `vinami-acs/docs/demo-cases.md`
- `vinami-acs/docs/ecg-r23a-real-model-integration.md`
- `Research/VinStroke_ECG_Research_Proposal.md`

## GitHub

Repository: https://github.com/104221795/VinAMI-AI-ECG-Copilot
