# R1.5 Endpoint Permission Matrix

| Method | Path | Required permission |
|---|---|---|
| POST | `/api/v1/patients` | `patient:create` |
| GET | `/api/v1/patients/{patient_id}` | `patient:read` |
| POST | `/api/v1/encounters` | `encounter:create` |
| GET | `/api/v1/encounters/{encounter_id}` | `encounter:read` |
| POST | `/api/v1/clinical-cases` | `clinical_case:create` |
| GET | `/api/v1/clinical-cases` | `clinical_case:read` |
| GET | `/api/v1/clinical-cases/{case_id}` | `clinical_case:read` |
| GET | `/api/v1/clinical-cases/{case_id}/timeline` | `timeline:read` |
| GET | `/api/v1/clinical-cases/{case_id}/audit` | `audit:read` |
| GET | `/api/v1/clinical-cases/{case_id}/clinical-reasoning` | `clinical_reasoning:read` |
| GET | `/api/v1/clinical-cases/{case_id}/clinical-reasoning/risk-scores` | `clinical_reasoning:read` |
| POST | `/api/v1/clinical-cases/{case_id}/clinical-reasoning/risk-scores/heart` | `clinical_reasoning:write` |
| POST | `/api/v1/clinical-cases/{case_id}/clinical-reasoning/risk-scores/grace` | `clinical_reasoning:write` |
| POST | `/api/v1/clinical-cases/{case_id}/clinical-reasoning/snapshots` | `clinical_reasoning:write` |
| POST | `/api/v1/clinical-cases/{case_id}/clinical-reasoning/assessments` | `clinical_reasoning:write` |
| POST | `/api/v1/clinical-cases/{case_id}/clinical-reasoning/assessments/{assessment_id}/corrections` | `clinical_reasoning:write` |
| POST | `/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions` | `clinical_decision:write` |
| POST | `/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions/{decision_id}/corrections` | `clinical_decision:write` |
| POST | `/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions/{decision_id}/revocations` | `clinical_decision:write` |

The `risk-scores/heart` and `risk-scores/grace` writes additionally require an authenticated
physician actor (`ActorContext.require_physician()`), same as `assessments`/`decisions`.

Authentication failures return RFC 7807 status 401 and `WWW-Authenticate: Bearer`.
Authorization failures return RFC 7807 status 403. Both include a correlation ID and are
recorded as distinct audit outcomes without credentials.

## Development Fixtures

| Fixture | Lifecycle mutation | Lifecycle read | Audit read | System/identity inspect |
|---|---:|---:|---:|---:|
| `physician` | Yes | Yes | Yes | No |
| `nurse` | Yes | Yes | No | No |
| `technician` | No | Yes | No | No |
| `care_coordinator` | Yes | Yes | No | No |
| `administrator` | Yes | Yes | Yes | Yes |
| `auditor` | No | Yes | Yes | Yes |
| `system_service` | Yes | Yes | Yes | Yes |
| `read_only_reviewer` | No | Yes | No | No |

Fixtures are synthetic and deterministic. They are not hospital users and must not be
enabled in production.
