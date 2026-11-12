"""Seed deterministic end-to-end demonstration cases through public FastAPI routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from apps.api.app import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vinami.ecg.adapters.ingestion import build_demo_waveform
from vinami.identity.adapters.development import DEVELOPMENT_IDENTITIES
from vinami.shared.config import Settings
from vinami.shared.container import AppContainer
from vinami.shared.logging import configure_logging, log_event

BASE_TIME = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def main() -> None:
    """Create clinical demo cases without bypassing API validation or audit hooks."""
    settings = Settings(
        identity_mode="development",
        ecg_allow_mock_provider=True,
        ecg_artifact_root=Path("runtime/demo-ecg-artifacts"),
    )
    logger = configure_logging(settings)
    with TestClient(create_app(settings)) as client:
        full = _seed_case(
            client,
            slug="happy-complete",
            patient_reference="VINAMI-DEMO-HAPPY-001",
            priority="urgent",
            complaint="Demo complete case: chest discomfort with full evidence trail.",
            record_outcome=True,
            complete_task=False,
        )
        missing_outcome = _seed_case(
            client,
            slug="missing-outcome",
            patient_reference="VINAMI-DEMO-MISSING-OUTCOME-001",
            priority="routine",
            complaint="Demo safety case: completed task but no physician outcome.",
            record_outcome=False,
            complete_task=True,
        )
        container = cast(AppContainer, cast(FastAPI, client.app).state.container)
        summary = container.event_dispatcher().dispatch_pending(limit=500)
        log_event(
            logger,
            "demo_happy_cases_seeded",
            clinical_cases=[full["case_id"], missing_outcome["case_id"]],
            events_published=summary.published,
            dead_lettered=summary.dead_lettered,
        )
        if summary.dead_lettered:
            raise RuntimeError("Demo happy-case seed produced a dead-letter event")


def _seed_case(
    client: TestClient,
    *,
    slug: str,
    patient_reference: str,
    priority: str,
    complaint: str,
    record_outcome: bool,
    complete_task: bool,
) -> dict[str, Any]:
    patient = _post(
        client,
        "/api/v1/patients",
        key=f"demo-{slug}-patient",
        identity="nurse",
        json={
            "patient_reference": patient_reference,
            "safe_demographic_summary": {
                "age_band": "45-64",
                "sex_at_birth": "female",
            },
        },
    )
    encounter = _post(
        client,
        "/api/v1/encounters",
        key=f"demo-{slug}-encounter",
        identity="nurse",
        json={
            "patient_id": patient["patient_id"],
            "location": "Emergency Department",
            "arrival_at": BASE_TIME.isoformat(),
            "external_reference": f"VINAMI-DEMO-ENC-{slug.upper()}",
        },
    )
    clinical_case = _post(
        client,
        "/api/v1/clinical-cases",
        key=f"demo-{slug}-case",
        identity="physician",
        json={
            "encounter_id": encounter["encounter_id"],
            "opening_reason": "Deterministic VinAMI demonstration case.",
            "priority": priority,
        },
    )
    case_id = clinical_case["case_id"]
    case_version = int(clinical_case["aggregate_version"])
    _record_triage(client, slug, case_id, case_version, complaint)
    _record_vitals(client, slug, case_id, case_version)
    study_id = _record_ecg(client, slug, case_id, case_version)
    _record_troponin(client, slug, case_id, case_version)
    decision = _record_reasoning(client, slug, case_id, case_version)
    task = _record_workflow_task(client, slug, case_id, decision)
    if complete_task:
        _complete_task(client, slug, case_id, task)
    if record_outcome:
        _record_outcome(client, slug, case_id)
    return {"case_id": case_id, "study_id": study_id}


def _record_triage(
    client: TestClient, slug: str, case_id: str, case_version: int, complaint: str
) -> None:
    _post(
        client,
        f"/api/v1/clinical-cases/{case_id}/triage-assessments",
        key=f"demo-{slug}-triage",
        identity="nurse",
        json={
            "expected_case_version": case_version,
            "occurred_at": (BASE_TIME + timedelta(minutes=2)).isoformat(),
            "source": "deterministic-demo-triage",
            "chief_complaint": complaint,
            "symptom_onset_state": "present",
            "symptom_onset_at": (BASE_TIME - timedelta(minutes=35)).isoformat(),
            "symptom_onset_precision": "approximate",
            "symptom_description": "Synthetic pressure-like central discomfort for workflow demo.",
            "associated_symptoms": [
                {"name": "dyspnoea", "state": "absent"},
                {"name": "nausea", "state": "absent"},
            ],
            "cardiovascular_risk_factors": [
                {"name": "hypertension history", "state": "present"},
                {"name": "diabetes history", "state": "absent"},
            ],
        },
    )


def _record_vitals(client: TestClient, slug: str, case_id: str, case_version: int) -> None:
    observed_at = BASE_TIME + timedelta(minutes=4)
    measurements = [
        ("heart_rate", 84, "beats/min"),
        ("systolic_blood_pressure", 132, "mmHg"),
        ("diastolic_blood_pressure", 78, "mmHg"),
        ("oxygen_saturation", 98, "%"),
        ("body_temperature", 36.8, "degC"),
    ]
    _post(
        client,
        f"/api/v1/clinical-cases/{case_id}/vital-observation-sets",
        key=f"demo-{slug}-vitals",
        identity="nurse",
        json={
            "expected_case_version": case_version,
            "observed_at": observed_at.isoformat(),
            "source": "deterministic-demo-bedside-entry",
            "supplemental_oxygen_state": "absent",
            "measurements": [
                {
                    "kind": kind,
                    "value": value,
                    "unit": unit,
                    "source": "manual-entry",
                    "observed_at": observed_at.isoformat(),
                    "quality_status": "accepted",
                }
                for kind, value, unit in measurements
            ],
        },
    )


def _record_ecg(client: TestClient, slug: str, case_id: str, case_version: int) -> str:
    reference = f"VINAMI-DEMO-ECG-{slug.upper()}"
    response = client.post(
        f"/api/v1/clinical-cases/{case_id}/ecg-studies",
        headers=_headers(f"demo-{slug}-ecg-upload", "nurse"),
        data={
            "source_type": "canonical_waveform",
            "study_reference": reference,
            "acquisition_at": (BASE_TIME + timedelta(minutes=6)).isoformat(),
            "content_type": "application/vnd.vinami.ecg+json",
            "expected_case_version": str(case_version),
        },
        files={
            "artifact": (
                f"{reference}.json",
                build_demo_waveform(),
                "application/vnd.vinami.ecg+json",
            )
        },
    )
    _raise_for_status(response, "record ECG")
    study_id = response.json()["resulting_study_id"]
    analysis = _post(
        client,
        f"/api/v1/ecg-studies/{study_id}/analyses",
        key=f"demo-{slug}-ecg-analysis",
        identity="physician",
        json={"provider_name": "mock"},
    )
    _post(
        client,
        f"/api/v1/ecg-studies/{study_id}/physician-interpretations",
        key=f"demo-{slug}-ecg-interpretation",
        identity="physician",
        json={
            "analysis_id": analysis["analysis_id"],
            "interpretation_status": "reviewed",
            "structured_interpretation": (
                "Synthetic physician ECG interpretation for demonstration. "
                "Clinical correlation remains required."
            ),
            "agreement_status": "requires_clinical_correlation",
            "quality_disposition": "acceptable_for_review",
            "repeat_ecg_requested": False,
            "occurred_at": (BASE_TIME + timedelta(minutes=8)).isoformat(),
            "expected_version": 0,
        },
    )
    return str(study_id)


def _record_troponin(client: TestClient, slug: str, case_id: str, case_version: int) -> None:
    for index, value in enumerate(("12.40", "14.10"), start=1):
        minute = 12 + index * 15
        _post(
            client,
            f"/api/v1/clinical-cases/{case_id}/troponin-results",
            key=f"demo-{slug}-troponin-{index}",
            identity="physician",
            json={
                "assay_code": "DEMO-HS-CTNI-01",
                "analyte": "cardiac_troponin_i",
                "value": value,
                "unit": "ng/L",
                "specimen_type": "plasma",
                "specimen_collected_at": (BASE_TIME + timedelta(minutes=minute)).isoformat(),
                "resulted_at": (BASE_TIME + timedelta(minutes=minute + 1)).isoformat(),
                "result_status": "final",
                "quality_flags": [],
                "source_system": "deterministic-demo-lis",
                "source_result_id": f"VINAMI-DEMO-{slug}-TROP-{index}",
                "expected_case_version": case_version,
            },
        )


def _record_reasoning(
    client: TestClient, slug: str, case_id: str, case_version: int
) -> dict[str, Any]:
    snapshot = _post(
        client,
        f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/snapshots",
        key=f"demo-{slug}-snapshot",
        identity="physician",
        json={"expected_case_version": case_version},
    )
    assessment = _post(
        client,
        f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/assessments",
        key=f"demo-{slug}-assessment",
        identity="physician",
        json={
            "snapshot_id": snapshot["snapshot_id"],
            "expected_snapshot_version": snapshot["record_version"],
            "summary": "Synthetic physician synthesis of complete demo evidence.",
            "supporting_evidence": ["triage:v1", "ecg:v1", "troponin:v1"],
            "contradictory_evidence": [],
            "uncertainty": "Research-use demo data; not a clinical validation scenario.",
            "additional_information_required": None,
        },
    )
    return _post(
        client,
        f"/api/v1/clinical-cases/{case_id}/clinical-reasoning/decisions",
        key=f"demo-{slug}-decision",
        identity="physician",
        json={
            "snapshot_id": snapshot["snapshot_id"],
            "assessment_id": assessment["assessment_id"],
            "expected_snapshot_version": snapshot["record_version"],
            "expected_assessment_version": assessment["record_version"],
            "decision_type": "request_repeat_troponin",
            "rationale": "Synthetic physician request to demonstrate R6 task generation.",
            "uncertainty": "Decision is operational and demonstration-only.",
            "evidence_references": ["triage:v1", "ecg:v1", "troponin:v1"],
        },
    )


def _record_workflow_task(
    client: TestClient, slug: str, case_id: str, decision: dict[str, Any]
) -> dict[str, Any]:
    workspace = _get(
        client,
        f"/api/v1/clinical-cases/{case_id}/workflow-operations",
        identity="physician",
    )
    return _post(
        client,
        f"/api/v1/clinical-cases/{case_id}/workflow-operations/tasks/from-decisions",
        key=f"demo-{slug}-task",
        identity="physician",
        json={
            "decision_id": decision["decision_id"],
            "expected_decision_version": decision["record_version"],
            "expected_workflow_version": workspace["workflow"]["version"],
            "priority": "routine",
            "due_at": (BASE_TIME + timedelta(hours=2)).isoformat(),
        },
    )


def _complete_task(client: TestClient, slug: str, case_id: str, task: dict[str, Any]) -> None:
    physician_id = str(DEVELOPMENT_IDENTITIES["physician"].actor_id)
    current = task
    for action, extra in (
        ("assign", {"owner_user_id": physician_id}),
        ("acknowledge", {}),
        ("start", {}),
        ("complete", {"reason": "Synthetic demo task completed; no outcome is inferred."}),
    ):
        current = _post(
            client,
            f"/api/v1/clinical-cases/{case_id}/workflow-operations/tasks/{current['task_id']}/{action}",
            key=f"demo-{slug}-task-{action}",
            identity="physician",
            json={"expected_version": current["version"], **extra},
        )


def _record_outcome(client: TestClient, slug: str, case_id: str) -> None:
    _post(
        client,
        f"/api/v1/clinical-cases/{case_id}/outcomes",
        key=f"demo-{slug}-outcome",
        identity="physician",
        json={
            "outcome_type": "disposition",
            "outcome_timestamp": (BASE_TIME + timedelta(hours=4)).isoformat(),
            "final_conclusion": "Synthetic physician-documented demo outcome.",
            "disposition": "observation_completed",
            "destination": "demo-observation-unit",
            "data_completeness": "documented",
            "source": "physician_documented_demo",
            "uncertainty": "Synthetic demo outcome; not clinical evidence.",
        },
    )


def _post(
    client: TestClient,
    path: str,
    *,
    key: str,
    identity: str,
    json: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(path, headers=_headers(key, identity), json=json)
    _raise_for_status(response, path)
    return dict(response.json())


def _get(client: TestClient, path: str, *, identity: str) -> dict[str, Any]:
    response = client.get(path, headers=_headers(identity=identity))
    _raise_for_status(response, path)
    return dict(response.json())


def _headers(key: str | None = None, identity: str = "physician") -> dict[str, str]:
    headers = {
        "X-Development-Identity": identity,
        "X-Correlation-ID": str(uuid4()),
        "X-Request-Source": "seed-demo-happy-cases",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _raise_for_status(response: Any, action: str) -> None:
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"{action} failed: {response.status_code} {response.text}")


if __name__ == "__main__":
    main()
