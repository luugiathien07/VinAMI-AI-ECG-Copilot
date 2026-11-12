"""R5 application commands and queries for evidence synthesis and physician authorship."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from vinami.reasoning.domain.clinical_evidence import (
    ClinicalDecisionType,
    DecisionRecordStatus,
    PhysicianClinicalAssessment,
    PhysicianDecision,
)
from vinami.reasoning.domain.model import RiskScore
from vinami.reasoning.domain.risk_scores import (
    GraceScoreInput,
    HeartScoreInput,
    calculate_grace_score,
    calculate_heart_score,
    classify_grace_risk,
    classify_heart_risk,
)
from vinami.shared.actor import ActorContext
from vinami.shared.application_errors import ResourceConflictError, ResourceNotFoundError
from vinami.shared.domain import require_text, utc_now
from vinami.shared.events.contracts import EventContext, EventEnvelope
from vinami.shared.idempotency import IdempotencyResult, command_fingerprint
from vinami.workflow.domain.decision import (
    PhysicianDecision as WorkflowPhysicianDecision,
)
from vinami.workflow.domain.decision import (
    PhysicianDecisionStatus as WorkflowDecisionStatus,
)
from vinami.workflow.domain.decision import (
    PhysicianDecisionType as WorkflowDecisionType,
)


class UnitOfWork(Protocol):
    reasoning: Any
    cases: Any
    workflows: Any
    physician_decisions: Any
    actors: Any
    idempotency: Any

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, *args: object) -> None: ...
    def stage_event(self, event: EventEnvelope) -> None: ...
    def commit(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


@dataclass(frozen=True, slots=True)
class CreateEvidenceSnapshot:
    case_id: UUID
    expected_case_version: int
    actor: ActorContext
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RecordClinicalAssessment:
    case_id: UUID
    snapshot_id: UUID
    expected_snapshot_version: int
    summary: str
    supporting_evidence: tuple[str, ...]
    contradictory_evidence: tuple[str, ...]
    uncertainty: str
    additional_information_required: str | None
    actor: ActorContext
    idempotency_key: str
    corrects_assessment_id: UUID | None = None
    expected_assessment_version: int | None = None
    correction_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RecordClinicalDecision:
    case_id: UUID
    snapshot_id: UUID
    assessment_id: UUID
    expected_snapshot_version: int
    expected_assessment_version: int
    decision_type: ClinicalDecisionType
    rationale: str
    uncertainty: str
    evidence_references: tuple[str, ...]
    actor: ActorContext
    idempotency_key: str
    corrects_decision_id: UUID | None = None
    expected_decision_version: int | None = None
    correction_reason: str | None = None
    revoke: bool = False


@dataclass(frozen=True, slots=True)
class RecordHeartScore:
    case_id: UUID
    inputs: HeartScoreInput
    actor: ActorContext
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RecordGraceScore:
    case_id: UUID
    inputs: GraceScoreInput
    actor: ActorContext
    idempotency_key: str


class ClinicalReasoningApplicationService:
    """Coordinate R5 immutable writes through one PostgreSQL transaction."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._uow = unit_of_work_factory

    def create_snapshot(self, command: CreateEvidenceSnapshot) -> dict[str, object]:
        fingerprint = command_fingerprint(
            {"case_id": command.case_id, "expected_case_version": command.expected_case_version}
        )
        with self._uow() as uow:
            duplicate = self._existing(
                uow, command.actor, "create_evidence_snapshot", command.idempotency_key, fingerprint
            )
            if duplicate:
                return duplicate.response_payload
            case = uow.cases.get(command.case_id)
            if case is None:
                raise ResourceNotFoundError("clinical case was not found")
            if case.version != command.expected_case_version:
                raise ResourceConflictError("stale clinical case version")
            snapshot = uow.reasoning.assemble_snapshot(command.case_id, command.actor.actor_id)
            uow.reasoning.add_snapshot(snapshot)
            response = _snapshot_dict(snapshot)
            self._store(
                uow,
                command.actor,
                "create_evidence_snapshot",
                command.idempotency_key,
                fingerprint,
                snapshot.snapshot_id,
                response,
            )
            self._event(
                uow,
                "ClinicalEvidenceSnapshotCreated.v1",
                "ClinicalEvidenceSnapshot",
                snapshot.snapshot_id,
                snapshot.record_version,
                command.case_id,
                command.actor,
                {
                    "snapshot_id": str(snapshot.snapshot_id),
                    "case_version": snapshot.case_version,
                    "missing_evidence": list(snapshot.missing_evidence),
                    "technical_conflicts": list(snapshot.technical_conflicts),
                },
            )
            uow.commit()
            return response

    def record_assessment(self, command: RecordClinicalAssessment) -> dict[str, object]:
        command.actor.require_physician()
        fingerprint = command_fingerprint(asdict(command) | {"actor": command.actor.actor_id})
        with self._uow() as uow:
            duplicate = self._existing(
                uow,
                command.actor,
                "record_clinical_assessment",
                command.idempotency_key,
                fingerprint,
            )
            if duplicate:
                return duplicate.response_payload
            snapshot = self._current_snapshot(
                uow, command.case_id, command.snapshot_id, command.expected_snapshot_version
            )
            history = uow.reasoning.assessments(command.case_id)
            corrected = None
            if command.corrects_assessment_id:
                corrected = uow.reasoning.get_assessment(command.corrects_assessment_id)
                if corrected is None or corrected.clinical_case_id != command.case_id:
                    raise ResourceNotFoundError("assessment to correct was not found for this case")
                if command.expected_assessment_version != corrected.record_version:
                    raise ResourceConflictError("stale assessment version")
            assessment = PhysicianClinicalAssessment(
                uuid4(),
                command.case_id,
                snapshot.snapshot_id,
                len(history) + 1,
                1,
                command.summary,
                command.supporting_evidence,
                command.contradictory_evidence,
                command.uncertainty,
                command.additional_information_required,
                command.actor.actor_id,
                command.actor.actor_role,
                utc_now(),
                command.corrects_assessment_id,
                command.correction_reason,
            )
            uow.reasoning.add_assessment(assessment)
            response = _assessment_dict(assessment)
            self._store(
                uow,
                command.actor,
                "record_clinical_assessment",
                command.idempotency_key,
                fingerprint,
                assessment.assessment_id,
                response,
            )
            event = (
                "PhysicianClinicalAssessmentCorrected.v1"
                if corrected
                else "PhysicianClinicalAssessmentRecorded.v1"
            )
            self._event(
                uow,
                event,
                "PhysicianClinicalAssessment",
                assessment.assessment_id,
                1,
                command.case_id,
                command.actor,
                {
                    "assessment_id": str(assessment.assessment_id),
                    "snapshot_id": str(snapshot.snapshot_id),
                    "revision_number": assessment.revision_number,
                    "corrects_assessment_id": str(corrected.assessment_id) if corrected else None,
                },
            )
            uow.commit()
            return response

    def record_decision(self, command: RecordClinicalDecision) -> dict[str, object]:
        command.actor.require_physician()
        fingerprint = command_fingerprint(asdict(command) | {"actor": command.actor.actor_id})
        with self._uow() as uow:
            duplicate = self._existing(
                uow, command.actor, "record_clinical_decision", command.idempotency_key, fingerprint
            )
            if duplicate:
                return duplicate.response_payload
            snapshot = self._current_snapshot(
                uow, command.case_id, command.snapshot_id, command.expected_snapshot_version
            )
            assessment = uow.reasoning.get_assessment(command.assessment_id)
            if (
                assessment is None
                or assessment.clinical_case_id != command.case_id
                or assessment.snapshot_id != snapshot.snapshot_id
            ):
                raise ResourceConflictError(
                    "decision assessment must belong to the same case and snapshot"
                )
            if assessment.record_version != command.expected_assessment_version:
                raise ResourceConflictError("stale assessment version")
            prior = None
            status = DecisionRecordStatus.ACTIVE
            if command.corrects_decision_id:
                prior = uow.reasoning.get_decision(command.corrects_decision_id)
                if prior is None or prior.clinical_case_id != command.case_id:
                    raise ResourceNotFoundError("decision to revise was not found for this case")
                if command.expected_decision_version != prior.record_version:
                    raise ResourceConflictError("stale decision version")
                status = (
                    DecisionRecordStatus.REVOCATION
                    if command.revoke
                    else DecisionRecordStatus.CORRECTION
                )
            elif command.revoke:
                raise ResourceConflictError("revocation must reference an existing decision")
            workflow_authorization_id = self._workflow_authorization(
                uow, command, snapshot.case_version, prior, status
            )
            history = uow.reasoning.decisions(command.case_id)
            decision = PhysicianDecision(
                uuid4(),
                command.case_id,
                snapshot.snapshot_id,
                assessment.assessment_id,
                command.decision_type,
                status,
                len(history) + 1,
                1,
                command.rationale,
                command.uncertainty,
                command.evidence_references,
                command.actor.actor_id,
                command.actor.actor_role,
                utc_now(),
                command.corrects_decision_id,
                command.correction_reason,
                workflow_authorization_id,
            )
            uow.reasoning.add_decision(decision)
            response = _decision_dict(decision)
            self._store(
                uow,
                command.actor,
                "record_clinical_decision",
                command.idempotency_key,
                fingerprint,
                decision.decision_id,
                response,
            )
            event = {
                DecisionRecordStatus.ACTIVE: "PhysicianClinicalDecisionRecorded.v1",
                DecisionRecordStatus.CORRECTION: "PhysicianClinicalDecisionCorrected.v1",
                DecisionRecordStatus.REVOCATION: "PhysicianClinicalDecisionRevoked.v1",
            }[status]
            self._event(
                uow,
                event,
                "PhysicianClinicalDecision",
                decision.decision_id,
                1,
                command.case_id,
                command.actor,
                {
                    "decision_id": str(decision.decision_id),
                    "snapshot_id": str(snapshot.snapshot_id),
                    "assessment_id": str(assessment.assessment_id),
                    "decision_type": decision.decision_type.value,
                    "status": status.value,
                    "workflow_authorization_id": str(workflow_authorization_id)
                    if workflow_authorization_id
                    else None,
                },
            )
            uow.commit()
            return response

    def record_heart_score(self, command: RecordHeartScore) -> dict[str, object]:
        command.actor.require_physician()
        fingerprint = command_fingerprint(asdict(command) | {"actor": command.actor.actor_id})
        with self._uow() as uow:
            duplicate = self._existing(
                uow, command.actor, "record_heart_score", command.idempotency_key, fingerprint
            )
            if duplicate:
                return duplicate.response_payload
            case = uow.cases.get(command.case_id)
            if case is None:
                raise ResourceNotFoundError("clinical case was not found")
            result = calculate_heart_score(command.inputs, utc_now())
            score_id = uow.reasoning.save_risk_score(command.case_id, result.risk_score)
            response = _risk_score_dict(score_id, result.risk_score, result.risk_category.value)
            self._store(
                uow,
                command.actor,
                "record_heart_score",
                command.idempotency_key,
                fingerprint,
                score_id,
                response,
            )
            self._event(
                uow,
                "RiskScoreRecorded.v1",
                "RiskScore",
                score_id,
                1,
                command.case_id,
                command.actor,
                {
                    "name": "HEART",
                    "value": result.risk_score.value,
                    "category": response["category"],
                },
            )
            uow.commit()
            return response

    def record_grace_score(self, command: RecordGraceScore) -> dict[str, object]:
        command.actor.require_physician()
        fingerprint = command_fingerprint(asdict(command) | {"actor": command.actor.actor_id})
        with self._uow() as uow:
            duplicate = self._existing(
                uow, command.actor, "record_grace_score", command.idempotency_key, fingerprint
            )
            if duplicate:
                return duplicate.response_payload
            case = uow.cases.get(command.case_id)
            if case is None:
                raise ResourceNotFoundError("clinical case was not found")
            result = calculate_grace_score(command.inputs, utc_now())
            score_id = uow.reasoning.save_risk_score(command.case_id, result.risk_score)
            response = _risk_score_dict(score_id, result.risk_score, result.risk_category.value)
            self._store(
                uow,
                command.actor,
                "record_grace_score",
                command.idempotency_key,
                fingerprint,
                score_id,
                response,
            )
            self._event(
                uow,
                "RiskScoreRecorded.v1",
                "RiskScore",
                score_id,
                1,
                command.case_id,
                command.actor,
                {
                    "name": "GRACE",
                    "value": result.risk_score.value,
                    "category": response["category"],
                },
            )
            uow.commit()
            return response

    @staticmethod
    def _current_snapshot(
        uow: UnitOfWork, case_id: UUID, snapshot_id: UUID, expected_version: int
    ) -> Any:
        latest = uow.reasoning.latest_snapshot(case_id)
        if latest is None or latest.snapshot_id != snapshot_id:
            raise ResourceConflictError("evidence snapshot is stale; create a current snapshot")
        if latest.record_version != expected_version:
            raise ResourceConflictError("stale evidence snapshot version")
        case = uow.cases.get(case_id)
        if case is None:
            raise ResourceNotFoundError("clinical case was not found")
        if case.version != latest.case_version:
            raise ResourceConflictError("case evidence changed after snapshot creation")
        return latest

    @staticmethod
    def _workflow_authorization(
        uow: UnitOfWork,
        command: RecordClinicalDecision,
        case_version: int,
        prior: PhysicianDecision | None,
        status: DecisionRecordStatus,
    ) -> UUID | None:
        is_cath = command.decision_type is ClinicalDecisionType.REQUEST_CATH_LAB_REVIEW
        prior_auth = prior.workflow_authorization_id if prior else None
        if not is_cath and prior_auth is None:
            return None
        workflow = uow.workflows.get_by_case_id(command.case_id)
        if workflow is None:
            raise ResourceConflictError(
                "cath-lab review request requires an existing case workflow"
            )
        if uow.actors.role_for(command.actor.actor_id) != command.actor.actor_role:
            raise ResourceConflictError(
                "physician identity is not authorized by persisted actor binding"
            )
        authorization_id = uuid4()
        workflow_status = (
            WorkflowDecisionStatus.REVOKED
            if status is DecisionRecordStatus.REVOCATION
            else (
                WorkflowDecisionStatus.CORRECTION if prior_auth else WorkflowDecisionStatus.ACTIVE
            )
        )
        authorization = WorkflowPhysicianDecision(
            authorization_id,
            command.case_id,
            command.actor.actor_id,
            command.actor.actor_role,
            WorkflowDecisionType.CATH_LAB_REVIEW_REQUESTED,
            workflow_status,
            "physician_clinical_decision",
            command.rationale,
            str(command.snapshot_id),
            workflow.version,
            case_version,
            utc_now(),
            utc_now(),
            command.actor.correlation_id,
            command.actor.causation_id,
            prior_auth,
        )
        uow.physician_decisions.add(authorization)
        return authorization_id

    @staticmethod
    def _existing(
        uow: UnitOfWork, actor: ActorContext, command_type: str, key: str, fingerprint: str
    ) -> IdempotencyResult | None:
        existing = cast(
            IdempotencyResult | None,
            uow.idempotency.lock_and_get(
                actor.actor_id, command_type, require_text(key, "idempotency_key")
            ),
        )
        if existing and existing.request_hash != fingerprint:
            raise ResourceConflictError("idempotency key was already used with a different request")
        return existing

    @staticmethod
    def _store(
        uow: UnitOfWork,
        actor: ActorContext,
        command_type: str,
        key: str,
        fingerprint: str,
        resource_id: UUID,
        response: dict[str, object],
    ) -> None:
        uow.idempotency.add(
            IdempotencyResult(
                actor.actor_id,
                command_type,
                key,
                uuid4(),
                fingerprint,
                "ClinicalReasoning",
                resource_id,
                response,
                utc_now(),
            )
        )

    @staticmethod
    def _event(
        uow: UnitOfWork,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        version: int,
        case_id: UUID,
        actor: ActorContext,
        payload: dict[str, object],
    ) -> None:
        uow.stage_event(
            EventEnvelope.from_context(
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=version,
                payload={"case_id": str(case_id), **payload},
                context=EventContext(
                    actor.correlation_id, actor.causation_id, actor.actor_id, actor.audit_metadata()
                ),
            )
        )


class ClinicalReasoningQueryService:
    """Return one server-computed physician workspace without clinical conclusions."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._uow = unit_of_work_factory

    def workspace(self, case_id: UUID) -> dict[str, object]:
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            if case is None:
                raise ResourceNotFoundError("clinical case was not found")
            snapshot = uow.reasoning.latest_snapshot(case_id)
            return {
                "case_id": str(case_id),
                "case_version": case.version,
                "snapshot": _snapshot_dict(snapshot) if snapshot else None,
                "assessments": [_assessment_dict(a) for a in uow.reasoning.assessments(case_id)],
                "decisions": [
                    _decision_dict(d)
                    | {"effective": uow.reasoning.is_decision_effective(d.decision_id)}
                    for d in uow.reasoning.decisions(case_id)
                ],
                "safety_notice": (
                    "Research-use prototype. Evidence synthesis does not diagnose ACS or AMI "
                    "and does not authorize treatment."
                ),
            }

    def risk_scores(self, case_id: UUID) -> list[dict[str, object]]:
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            if case is None:
                raise ResourceNotFoundError("clinical case was not found")
            classify = {"HEART": classify_heart_risk, "GRACE": classify_grace_risk}
            return [
                _risk_score_dict(None, score, classify[score.name](int(score.value)).value)
                for score in uow.reasoning.risk_scores_for_case(case_id)
                if score.name in classify
            ]


def _risk_score_dict(
    score_id: UUID | None, score: RiskScore, category: str
) -> dict[str, object]:
    return {
        "risk_score_id": str(score_id) if score_id else None,
        "name": score.name,
        "value": score.value,
        "category": category,
        "algorithm_version": score.algorithm_version,
        "calculated_at": score.calculated_at.isoformat(),
    }


def _snapshot_dict(s: Any) -> dict[str, object]:
    return {
        "snapshot_id": str(s.snapshot_id),
        "clinical_case_id": str(s.clinical_case_id),
        "case_version": s.case_version,
        "record_version": s.record_version,
        "created_at": s.created_at.isoformat(),
        "sources": [
            {
                "source_type": r.source_type,
                "source_id": str(r.source_id),
                "source_version": r.source_version,
                "relevant_at": r.relevant_at.isoformat(),
            }
            for r in s.sources
        ],
        "summaries": dict(s.summaries),
        "freshness": {k: v.value for k, v in s.freshness.items()},
        "missing_evidence": list(s.missing_evidence),
        "technical_conflicts": list(s.technical_conflicts),
    }


def _assessment_dict(a: PhysicianClinicalAssessment) -> dict[str, object]:
    return {
        "assessment_id": str(a.assessment_id),
        "clinical_case_id": str(a.clinical_case_id),
        "snapshot_id": str(a.snapshot_id),
        "revision_number": a.revision_number,
        "record_version": a.record_version,
        "summary": a.summary,
        "supporting_evidence": list(a.supporting_evidence),
        "contradictory_evidence": list(a.contradictory_evidence),
        "uncertainty": a.uncertainty,
        "additional_information_required": a.additional_information_required,
        "actor_id": str(a.actor_id),
        "recorded_at": a.recorded_at.isoformat(),
        "corrects_assessment_id": str(a.corrects_assessment_id)
        if a.corrects_assessment_id
        else None,
        "correction_reason": a.correction_reason,
    }


def _decision_dict(d: PhysicianDecision) -> dict[str, object]:
    return {
        "decision_id": str(d.decision_id),
        "clinical_case_id": str(d.clinical_case_id),
        "snapshot_id": str(d.snapshot_id),
        "assessment_id": str(d.assessment_id),
        "decision_type": d.decision_type.value,
        "status": d.status.value,
        "revision_number": d.revision_number,
        "record_version": d.record_version,
        "rationale": d.rationale,
        "uncertainty": d.uncertainty,
        "evidence_references": list(d.evidence_references),
        "actor_id": str(d.actor_id),
        "recorded_at": d.recorded_at.isoformat(),
        "corrects_decision_id": str(d.corrects_decision_id) if d.corrects_decision_id else None,
        "correction_reason": d.correction_reason,
        "workflow_authorization_id": str(d.workflow_authorization_id)
        if d.workflow_authorization_id
        else None,
    }
