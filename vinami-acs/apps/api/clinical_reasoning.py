"""FastAPI delivery adapter for the R5 physician evidence workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.http import AuthorizedIdentity, CommandHeaders, authorize_for, command_headers_for
from vinami.identity.domain.security import Permission
from vinami.reasoning.application.service import (
    CreateEvidenceSnapshot,
    RecordClinicalAssessment,
    RecordClinicalDecision,
    RecordGraceScore,
    RecordHeartScore,
)
from vinami.reasoning.domain.clinical_evidence import ClinicalDecisionType
from vinami.reasoning.domain.risk_scores import (
    GraceScoreInput,
    HeartEcgCategory,
    HeartHistoryCategory,
    HeartScoreInput,
    KillipClass,
)
from vinami.shared.container import AppContainer

router = APIRouter(prefix="/api/v1", tags=["clinical-reasoning"])


class SnapshotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_case_version: int = Field(ge=1)


class AssessmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: UUID
    expected_snapshot_version: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=6000)
    supporting_evidence: list[str] = Field(default_factory=list, max_length=100)
    contradictory_evidence: list[str] = Field(default_factory=list, max_length=100)
    uncertainty: str = Field(min_length=1, max_length=3000)
    additional_information_required: str | None = Field(default=None, max_length=3000)


class AssessmentCorrectionBody(AssessmentBody):
    expected_assessment_version: int = Field(ge=1)
    correction_reason: str = Field(min_length=1, max_length=2000)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: UUID
    assessment_id: UUID
    expected_snapshot_version: int = Field(ge=1)
    expected_assessment_version: int = Field(ge=1)
    decision_type: ClinicalDecisionType
    rationale: str = Field(min_length=1, max_length=6000)
    uncertainty: str = Field(min_length=1, max_length=3000)
    evidence_references: list[str] = Field(min_length=1, max_length=100)


class DecisionCorrectionBody(DecisionBody):
    expected_decision_version: int = Field(ge=1)
    correction_reason: str = Field(min_length=1, max_length=2000)


class DecisionRevocationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: UUID
    assessment_id: UUID
    expected_snapshot_version: int = Field(ge=1)
    expected_assessment_version: int = Field(ge=1)
    expected_decision_version: int = Field(ge=1)
    decision_type: ClinicalDecisionType
    rationale: str = Field(min_length=1, max_length=6000)
    uncertainty: str = Field(min_length=1, max_length=3000)
    evidence_references: list[str] = Field(min_length=1, max_length=100)
    revocation_reason: str = Field(min_length=1, max_length=2000)
    operation: Literal["revoke"] = "revoke"


class ClinicalReasoningWorkspaceResponse(BaseModel):
    """Generated-client contract for the server-computed R5 workspace."""

    model_config = ConfigDict(extra="forbid")
    case_id: UUID
    case_version: int
    snapshot: dict[str, Any] | None
    assessments: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    safety_notice: str


class HeartScoreBody(BaseModel):
    """Physician-supplied HEART score inputs; no field is auto-derived from stored data."""

    model_config = ConfigDict(extra="forbid")
    history: HeartHistoryCategory
    ecg: HeartEcgCategory
    age_years: int = Field(ge=0)
    risk_factor_count: int = Field(ge=0)
    has_atherosclerotic_disease_history: bool
    troponin_multiple_of_upper_limit_normal: float = Field(ge=0)


class GraceScoreBody(BaseModel):
    """Physician-supplied GRACE score inputs; no field is auto-derived from stored data."""

    model_config = ConfigDict(extra="forbid")
    age_years: float = Field(ge=0)
    heart_rate_bpm: float = Field(ge=0)
    systolic_bp_mmhg: float = Field(ge=0)
    creatinine_mg_dl: float = Field(ge=0)
    killip_class: KillipClass
    cardiac_arrest_at_admission: bool
    st_segment_deviation: bool
    elevated_cardiac_enzymes: bool


class RiskScoreResponse(BaseModel):
    """Generated-client contract for one computed, persisted risk score."""

    model_config = ConfigDict(extra="forbid")
    risk_score_id: str | None
    name: str
    value: float
    category: str
    algorithm_version: str
    calculated_at: datetime


def _container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


@router.get(
    "/clinical-cases/{case_id}/clinical-reasoning",
    response_model=ClinicalReasoningWorkspaceResponse,
)
def workspace(
    case_id: UUID,
    request: Request,
    _: Annotated[AuthorizedIdentity, Depends(authorize_for(Permission.CLINICAL_REASONING_READ))],
) -> dict[str, object]:
    return _container(request).clinical_reasoning_queries().workspace(case_id)


@router.get(
    "/clinical-cases/{case_id}/clinical-reasoning/risk-scores",
    response_model=list[RiskScoreResponse],
)
def risk_scores(
    case_id: UUID,
    request: Request,
    _: Annotated[AuthorizedIdentity, Depends(authorize_for(Permission.CLINICAL_REASONING_READ))],
) -> list[dict[str, object]]:
    return _container(request).clinical_reasoning_queries().risk_scores(case_id)


@router.post(
    "/clinical-cases/{case_id}/clinical-reasoning/risk-scores/heart",
    status_code=201,
    response_model=RiskScoreResponse,
)
def record_heart_score(
    case_id: UUID,
    body: HeartScoreBody,
    request: Request,
    headers: Annotated[
        CommandHeaders, Depends(command_headers_for(Permission.CLINICAL_REASONING_WRITE))
    ],
) -> dict[str, object]:
    return (
        _container(request)
        .clinical_reasoning_application()
        .record_heart_score(
            RecordHeartScore(
                case_id,
                HeartScoreInput(
                    body.history,
                    body.ecg,
                    body.age_years,
                    body.risk_factor_count,
                    body.has_atherosclerotic_disease_history,
                    body.troponin_multiple_of_upper_limit_normal,
                ),
                headers.actor,
                headers.idempotency_key,
            )
        )
    )


@router.post(
    "/clinical-cases/{case_id}/clinical-reasoning/risk-scores/grace",
    status_code=201,
    response_model=RiskScoreResponse,
)
def record_grace_score(
    case_id: UUID,
    body: GraceScoreBody,
    request: Request,
    headers: Annotated[
        CommandHeaders, Depends(command_headers_for(Permission.CLINICAL_REASONING_WRITE))
    ],
) -> dict[str, object]:
    return (
        _container(request)
        .clinical_reasoning_application()
        .record_grace_score(
            RecordGraceScore(
                case_id,
                GraceScoreInput(
                    body.age_years,
                    body.heart_rate_bpm,
                    body.systolic_bp_mmhg,
                    body.creatinine_mg_dl,
                    body.killip_class,
                    body.cardiac_arrest_at_admission,
                    body.st_segment_deviation,
                    body.elevated_cardiac_enzymes,
                ),
                headers.actor,
                headers.idempotency_key,
            )
        )
    )


@router.post("/clinical-cases/{case_id}/clinical-reasoning/snapshots", status_code=201)
def create_snapshot(
    case_id: UUID,
    body: SnapshotBody,
    request: Request,
    headers: Annotated[
        CommandHeaders, Depends(command_headers_for(Permission.CLINICAL_REASONING_WRITE))
    ],
) -> dict[str, object]:
    return (
        _container(request)
        .clinical_reasoning_application()
        .create_snapshot(
            CreateEvidenceSnapshot(
                case_id, body.expected_case_version, headers.actor, headers.idempotency_key
            )
        )
    )


@router.post("/clinical-cases/{case_id}/clinical-reasoning/assessments", status_code=201)
def record_assessment(
    case_id: UUID,
    body: AssessmentBody,
    request: Request,
    headers: Annotated[
        CommandHeaders, Depends(command_headers_for(Permission.CLINICAL_REASONING_WRITE))
    ],
) -> dict[str, object]:
    return (
        _container(request)
        .clinical_reasoning_application()
        .record_assessment(_assessment_command(case_id, body, headers))
    )


@router.post(
    "/clinical-cases/{case_id}/clinical-reasoning/assessments/{assessment_id}/corrections",
    status_code=201,
)
def correct_assessment(
    case_id: UUID,
    assessment_id: UUID,
    body: AssessmentCorrectionBody,
    request: Request,
    headers: Annotated[
        CommandHeaders, Depends(command_headers_for(Permission.CLINICAL_REASONING_WRITE))
    ],
) -> dict[str, object]:
    return (
        _container(request)
        .clinical_reasoning_application()
        .record_assessment(_assessment_command(case_id, body, headers, assessment_id))
    )


@router.post("/clinical-cases/{case_id}/clinical-reasoning/decisions", status_code=201)
def record_decision(
    case_id: UUID,
    body: DecisionBody,
    request: Request,
    headers: Annotated[
        CommandHeaders, Depends(command_headers_for(Permission.CLINICAL_DECISION_WRITE))
    ],
) -> dict[str, object]:
    return (
        _container(request)
        .clinical_reasoning_application()
        .record_decision(_decision_command(case_id, body, headers))
    )


@router.post(
    "/clinical-cases/{case_id}/clinical-reasoning/decisions/{decision_id}/corrections",
    status_code=201,
)
def correct_decision(
    case_id: UUID,
    decision_id: UUID,
    body: DecisionCorrectionBody,
    request: Request,
    headers: Annotated[
        CommandHeaders, Depends(command_headers_for(Permission.CLINICAL_DECISION_WRITE))
    ],
) -> dict[str, object]:
    return (
        _container(request)
        .clinical_reasoning_application()
        .record_decision(_decision_command(case_id, body, headers, decision_id))
    )


@router.post(
    "/clinical-cases/{case_id}/clinical-reasoning/decisions/{decision_id}/revocations",
    status_code=201,
)
def revoke_decision(
    case_id: UUID,
    decision_id: UUID,
    body: DecisionRevocationBody,
    request: Request,
    headers: Annotated[
        CommandHeaders, Depends(command_headers_for(Permission.CLINICAL_DECISION_WRITE))
    ],
) -> dict[str, object]:
    return (
        _container(request)
        .clinical_reasoning_application()
        .record_decision(_decision_command(case_id, body, headers, decision_id, True))
    )


def _assessment_command(
    case_id: UUID, body: AssessmentBody, headers: CommandHeaders, corrects: UUID | None = None
) -> RecordClinicalAssessment:
    return RecordClinicalAssessment(
        case_id,
        body.snapshot_id,
        body.expected_snapshot_version,
        body.summary,
        tuple(body.supporting_evidence),
        tuple(body.contradictory_evidence),
        body.uncertainty,
        body.additional_information_required,
        headers.actor,
        headers.idempotency_key,
        corrects,
        getattr(body, "expected_assessment_version", None),
        getattr(body, "correction_reason", None),
    )


def _decision_command(
    case_id: UUID,
    body: DecisionBody | DecisionRevocationBody,
    headers: CommandHeaders,
    corrects: UUID | None = None,
    revoke: bool = False,
) -> RecordClinicalDecision:
    return RecordClinicalDecision(
        case_id,
        body.snapshot_id,
        body.assessment_id,
        body.expected_snapshot_version,
        body.expected_assessment_version,
        body.decision_type,
        body.rationale,
        body.uncertainty,
        tuple(body.evidence_references),
        headers.actor,
        headers.idempotency_key,
        corrects,
        getattr(body, "expected_decision_version", None),
        getattr(body, "correction_reason", None) or getattr(body, "revocation_reason", None),
        revoke,
    )
