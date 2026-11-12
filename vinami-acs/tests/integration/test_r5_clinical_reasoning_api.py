"""PostgreSQL-backed R5 evidence and physician-decision acceptance tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from apps.api.app import create_app
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from tests.integration.test_r3_ecg_api import _case, _headers, _settings

from vinami.audit.adapters.persistence import AuditLogRecord, OutboxEventRecord
from vinami.reasoning.adapters.persistence import RiskScoreRecord
from vinami.reasoning.adapters.r5_persistence import (
    ClinicalEvidenceSnapshotRecord,
    ClinicalReasoningDecisionRecord,
    PhysicianClinicalAssessmentRecord,
)
from vinami.timeline.adapters.persistence import TimelineEventRecord
from vinami.workflow.adapters.decision_persistence import PhysicianDecisionRecord


def test_r5_snapshot_assessment_decision_idempotency_and_stale_rejection(
    postgres_session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    app = create_app(_settings(tmp_path / "r5-evidence"))
    with TestClient(app) as client:
        clinical_case = _case(client)
        case_id = str(clinical_case["case_id"])
        case_version = int(clinical_case["aggregate_version"])
        key = f"snapshot-{uuid4()}"
        first = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/snapshots",
            headers=_headers(key, "physician"),
            json={"expected_case_version": case_version},
        )
        assert first.status_code == 201, first.text
        duplicate = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/snapshots",
            headers=_headers(key, "physician"),
            json={"expected_case_version": case_version},
        )
        assert duplicate.json()["snapshot_id"] == first.json()["snapshot_id"]
        assert {"triage", "vitals", "ecg", "troponin"}.issubset(
            set(first.json()["missing_evidence"])
        )
        assert any(
            source["source_type"] == "clinical_case" and source["source_version"] == case_version
            for source in first.json()["sources"]
        )

        assessment_body = {
            "snapshot_id": first.json()["snapshot_id"],
            "expected_snapshot_version": 1,
            "summary": "Physician-authored synthesis of currently available evidence.",
            "supporting_evidence": ["clinical_case:v1"],
            "contradictory_evidence": [],
            "uncertainty": "ECG and serial troponin evidence are not yet available.",
            "additional_information_required": "Obtain missing evidence.",
        }
        assessment = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/assessments",
            headers=_headers(f"assessment-{uuid4()}", "physician"),
            json=assessment_body,
        )
        assert assessment.status_code == 201, assessment.text

        decision_body = {
            "snapshot_id": first.json()["snapshot_id"],
            "assessment_id": assessment.json()["assessment_id"],
            "expected_snapshot_version": 1,
            "expected_assessment_version": 1,
            "decision_type": "continue_observation",
            "rationale": "Continue documented observation while evidence is obtained.",
            "uncertainty": "Missing ECG and troponin evidence.",
            "evidence_references": ["clinical_case:v1"],
        }
        decision = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions",
            headers=_headers(f"decision-{uuid4()}", "physician"),
            json=decision_body,
        )
        assert decision.status_code == 201, decision.text
        assert decision.json()["workflow_authorization_id"] is None

        second = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/snapshots",
            headers=_headers(f"snapshot-{uuid4()}", "physician"),
            json={"expected_case_version": case_version},
        )
        assert second.status_code == 201
        stale = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/assessments",
            headers=_headers(f"stale-{uuid4()}", "physician"),
            json=assessment_body,
        )
        assert stale.status_code == 409

        dispatcher = app.state.container.event_dispatcher()
        for _ in range(10):
            summary = dispatcher.dispatch_pending(limit=100)
            if summary.published + summary.retried + summary.dead_lettered + summary.skipped == 0:
                break
        with postgres_session_factory() as session:
            case_uuid = UUID(case_id)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ClinicalEvidenceSnapshotRecord)
                    .where(ClinicalEvidenceSnapshotRecord.clinical_case_id == case_uuid)
                )
                == 2
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(PhysicianClinicalAssessmentRecord)
                    .where(PhysicianClinicalAssessmentRecord.clinical_case_id == case_uuid)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ClinicalReasoningDecisionRecord)
                    .where(ClinicalReasoningDecisionRecord.clinical_case_id == case_uuid)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEventRecord)
                    .where(OutboxEventRecord.event_type.like("%Clinical%"))
                )
                >= 3
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(TimelineEventRecord)
                    .where(
                        TimelineEventRecord.case_id == case_uuid,
                        TimelineEventRecord.event_type == "PhysicianClinicalDecisionRecorded.v1",
                    )
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditLogRecord)
                    .where(
                        AuditLogRecord.case_id == case_uuid,
                        AuditLogRecord.action == "event:PhysicianClinicalDecisionRecorded.v1",
                    )
                )
                == 1
            )


def test_r5_cath_lab_authorization_is_physician_only_and_revocable(
    postgres_session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    app = create_app(_settings(tmp_path / "r5-cath"))
    with TestClient(app) as client:
        clinical_case = _case(client)
        case_id = str(clinical_case["case_id"])
        version = int(clinical_case["aggregate_version"])
        snapshot = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/snapshots",
            headers=_headers(f"snapshot-{uuid4()}", "physician"),
            json={"expected_case_version": version},
        ).json()
        assessment = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/assessments",
            headers=_headers(f"assessment-{uuid4()}", "physician"),
            json={
                "snapshot_id": snapshot["snapshot_id"],
                "expected_snapshot_version": 1,
                "summary": "Physician assessment for specialist review request.",
                "supporting_evidence": ["clinical_case:v1"],
                "contradictory_evidence": [],
                "uncertainty": "Specialist review remains necessary.",
            },
        ).json()
        body = {
            "snapshot_id": snapshot["snapshot_id"],
            "assessment_id": assessment["assessment_id"],
            "expected_snapshot_version": 1,
            "expected_assessment_version": 1,
            "decision_type": "request_cath_lab_review",
            "rationale": "Physician request for cath-lab review.",
            "uncertainty": "Review request only; no automated action.",
            "evidence_references": ["clinical_case:v1"],
        }
        rejected = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions",
            headers=_headers(f"nurse-{uuid4()}", "nurse"),
            json=body,
        )
        assert rejected.status_code == 403
        accepted = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions",
            headers=_headers(f"physician-{uuid4()}", "physician"),
            json=body,
        )
        assert accepted.status_code == 201, accepted.text
        authorization_id = UUID(accepted.json()["workflow_authorization_id"])
        revoked = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions/{accepted.json()['decision_id']}/revocations",
            headers=_headers(f"revoke-{uuid4()}", "physician"),
            json={
                **body,
                "expected_decision_version": 1,
                "revocation_reason": "Physician withdrew the review request.",
                "operation": "revoke",
            },
        )
        assert revoked.status_code == 201, revoked.text
        with postgres_session_factory() as session:
            original = session.get(PhysicianDecisionRecord, authorization_id)
            assert original is not None
            revocation = session.scalar(
                select(PhysicianDecisionRecord).where(
                    PhysicianDecisionRecord.corrects_decision_id == authorization_id
                )
            )
            assert revocation is not None
            assert revocation.decision_status.value == "revoked"


def test_r5_heart_and_grace_scores_computed_persisted_and_idempotent(
    postgres_session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    app = create_app(_settings(tmp_path / "r5-risk-scores"))
    with TestClient(app) as client:
        clinical_case = _case(client)
        case_id = str(clinical_case["case_id"])

        heart_body = {
            "history": "highly_suspicious",
            "ecg": "significant_st_deviation",
            "age_years": 70,
            "risk_factor_count": 3,
            "has_atherosclerotic_disease_history": True,
            "troponin_multiple_of_upper_limit_normal": 5.0,
        }
        heart_key = f"heart-{uuid4()}"
        first = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/risk-scores/heart",
            headers=_headers(heart_key, "physician"),
            json=heart_body,
        )
        assert first.status_code == 201, first.text
        assert first.json()["name"] == "HEART"
        assert first.json()["value"] == 10.0
        assert first.json()["category"] == "high"

        duplicate = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/risk-scores/heart",
            headers=_headers(heart_key, "physician"),
            json=heart_body,
        )
        assert duplicate.json()["risk_score_id"] == first.json()["risk_score_id"]

        rejected = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/risk-scores/heart",
            headers=_headers(f"heart-nurse-{uuid4()}", "nurse"),
            json=heart_body,
        )
        assert rejected.status_code == 403

        grace_body = {
            "age_years": 75.0,
            "heart_rate_bpm": 120.0,
            "systolic_bp_mmhg": 90.0,
            "creatinine_mg_dl": 2.2,
            "killip_class": "iii",
            "cardiac_arrest_at_admission": False,
            "st_segment_deviation": True,
            "elevated_cardiac_enzymes": True,
        }
        grace = client.post(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/risk-scores/grace",
            headers=_headers(f"grace-{uuid4()}", "physician"),
            json=grace_body,
        )
        assert grace.status_code == 201, grace.text
        assert grace.json()["name"] == "GRACE"
        assert grace.json()["category"] == "high"

        listed = client.get(
            f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/risk-scores",
            headers=_headers(identity="physician"),
        )
        assert listed.status_code == 200, listed.text
        names = {row["name"] for row in listed.json()}
        assert names == {"HEART", "GRACE"}

        with postgres_session_factory() as session:
            case_uuid = UUID(case_id)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RiskScoreRecord)
                    .where(RiskScoreRecord.case_id == case_uuid)
                )
                == 2
            )
