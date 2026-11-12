"""SQLAlchemy persistence and evidence assembly for the R5 vertical slice."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Index, Integer, String, Text, exists, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from vinami.clinical_case.adapters.persistence import ClinicalCaseRecord
from vinami.ecg.adapters.r3_persistence import (
    ECGAIAnalysisFindingRecord,
    ECGAIAnalysisRecord,
    ECGQualityRecord,
    ECGStudyDetailRecord,
    PhysicianECGInterpretationRecord,
)
from vinami.reasoning.adapters.persistence import RiskScoreRecord
from vinami.reasoning.domain.clinical_evidence import (
    ClinicalDecisionType,
    ClinicalEvidenceSnapshot,
    DecisionRecordStatus,
    EvidenceFreshness,
    EvidenceReference,
    PhysicianClinicalAssessment,
    PhysicianDecision,
)
from vinami.reasoning.domain.model import RiskScore
from vinami.shared.domain import utc_now
from vinami.shared.persistence import Base, JSONDocument, UTCDateTime, UUIDType
from vinami.triage.adapters.persistence import (
    TriageAssessmentRecord,
    VitalMeasurementRecord,
    VitalObservationSetRecord,
)
from vinami.troponin.adapters.r4_persistence import TroponinAssayRecord, TroponinEvidenceRecord


class ClinicalEvidenceSnapshotRecord(Base):
    """Immutable case-level evidence synthesis."""

    __tablename__ = "clinical_evidence_snapshots"
    __table_args__ = (Index("ix_evidence_snapshot_case_created", "clinical_case_id", "created_at"),)
    snapshot_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    clinical_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("clinical_cases.id", ondelete="CASCADE"), nullable=False
    )
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("clinical_users.id", ondelete="RESTRICT"), nullable=False
    )
    sources: Mapped[list[dict[str, object]]] = mapped_column(JSONDocument, nullable=False)
    summaries: Mapped[dict[str, object]] = mapped_column(JSONDocument, nullable=False)
    freshness: Mapped[dict[str, str]] = mapped_column(JSONDocument, nullable=False)
    missing_evidence: Mapped[list[str]] = mapped_column(JSONDocument, nullable=False)
    technical_conflicts: Mapped[list[str]] = mapped_column(JSONDocument, nullable=False)


class PhysicianClinicalAssessmentRecord(Base):
    """Append-only physician clinical assessment revision."""

    __tablename__ = "physician_clinical_assessments"
    __table_args__ = (
        Index("ix_physician_assessment_case_revision", "clinical_case_id", "revision_number"),
    )
    assessment_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    clinical_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("clinical_cases.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("clinical_evidence_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_evidence: Mapped[list[str]] = mapped_column(JSONDocument, nullable=False)
    contradictory_evidence: Mapped[list[str]] = mapped_column(JSONDocument, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    additional_information_required: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("clinical_users.id", ondelete="RESTRICT"), nullable=False
    )
    actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    recorded_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
    corrects_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("physician_clinical_assessments.assessment_id", ondelete="RESTRICT"),
        nullable=True,
    )
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ClinicalReasoningDecisionRecord(Base):
    """Append-only R5 physician decision and revocation history."""

    __tablename__ = "clinical_reasoning_decisions"
    __table_args__ = (
        Index("ix_reasoning_decision_case_revision", "clinical_case_id", "revision_number"),
    )
    decision_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    clinical_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("clinical_cases.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("clinical_evidence_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("physician_clinical_assessments.assessment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_references: Mapped[list[str]] = mapped_column(JSONDocument, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("clinical_users.id", ondelete="RESTRICT"), nullable=False
    )
    actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    recorded_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
    corrects_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("clinical_reasoning_decisions.decision_id", ondelete="RESTRICT"), nullable=True
    )
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_authorization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("physician_decisions.decision_id", ondelete="RESTRICT"), nullable=True
    )


class SQLAlchemyClinicalReasoningRepository:
    """Persist immutable records and assemble a bounded, non-diagnostic evidence view."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def assemble_snapshot(self, case_id: UUID, actor_id: UUID) -> ClinicalEvidenceSnapshot:
        case = self._session.get(ClinicalCaseRecord, case_id)
        if case is None:
            raise LookupError("clinical case was not found")
        now = utc_now()
        sources = [EvidenceReference("clinical_case", case.id, case.version, case.updated_at)]
        missing: list[str] = []
        conflicts: list[str] = []
        freshness: dict[str, EvidenceFreshness] = {"clinical_case": EvidenceFreshness.CURRENT}
        summaries: dict[str, object] = {
            "case": {
                "status": case.status.value,
                "priority": case.priority.value,
                "opened_at": case.opened_at.isoformat(),
            }
        }

        triage = self._session.scalar(
            select(TriageAssessmentRecord)
            .where(TriageAssessmentRecord.clinical_case_id == case_id)
            .order_by(TriageAssessmentRecord.revision_number.desc())
            .limit(1)
        )
        if triage is None:
            missing.append("triage")
            freshness["triage"] = EvidenceFreshness.MISSING
        else:
            sources.append(
                EvidenceReference("triage", triage.id, triage.record_version, triage.recorded_at)
            )
            freshness["triage"] = self._freshness(triage.recorded_at, now)
            summaries["triage"] = {
                "chief_complaint": triage.chief_complaint,
                "symptom_onset_state": triage.symptom_onset_state,
                "symptom_description": triage.symptom_description,
                "revision": triage.revision_number,
            }

        vital = self._session.scalar(
            select(VitalObservationSetRecord)
            .where(VitalObservationSetRecord.clinical_case_id == case_id)
            .order_by(
                VitalObservationSetRecord.observed_at.desc(),
                VitalObservationSetRecord.revision_number.desc(),
            )
            .limit(1)
        )
        if vital is None:
            missing.append("vitals")
            freshness["vitals"] = EvidenceFreshness.MISSING
        else:
            measurements = self._session.scalars(
                select(VitalMeasurementRecord).where(
                    VitalMeasurementRecord.vital_set_id == vital.id
                )
            ).all()
            sources.append(
                EvidenceReference("vitals", vital.id, vital.record_version, vital.observed_at)
            )
            freshness["vitals"] = self._freshness(vital.observed_at, now)
            summaries["vitals"] = [
                {
                    "kind": item.kind,
                    "value": item.value,
                    "unit": item.unit,
                    "quality_status": item.quality_status,
                }
                for item in measurements
            ]

        studies = self._session.scalars(
            select(ECGStudyDetailRecord)
            .where(ECGStudyDetailRecord.clinical_case_id == case_id)
            .order_by(ECGStudyDetailRecord.acquisition_at.desc())
        ).all()
        if not studies:
            missing.append("ecg")
            freshness["ecg"] = EvidenceFreshness.MISSING
        else:
            ecg_summaries: list[dict[str, object]] = []
            for study in studies:
                quality = self._session.scalar(
                    select(ECGQualityRecord).where(ECGQualityRecord.study_id == study.study_id)
                )
                analysis = self._session.scalar(
                    select(ECGAIAnalysisRecord)
                    .where(ECGAIAnalysisRecord.ecg_study_id == study.study_id)
                    .order_by(ECGAIAnalysisRecord.requested_at.desc())
                    .limit(1)
                )
                interpretation = self._session.scalar(
                    select(PhysicianECGInterpretationRecord)
                    .where(PhysicianECGInterpretationRecord.ecg_study_id == study.study_id)
                    .order_by(PhysicianECGInterpretationRecord.revision_number.desc())
                    .limit(1)
                )
                sources.append(
                    EvidenceReference(
                        "ecg_study", study.study_id, study.aggregate_version, study.updated_at
                    )
                )
                findings: list[dict[str, object]] = []
                if analysis is not None:
                    sources.append(
                        EvidenceReference(
                            "ecg_ai_analysis",
                            analysis.id,
                            analysis.record_version,
                            analysis.completed_at or analysis.requested_at,
                        )
                    )
                    findings = [
                        {
                            "code": f.finding_code,
                            "label": f.display_label,
                            "threshold_met": f.threshold_met,
                            "status": f.finding_status,
                        }
                        for f in self._session.scalars(
                            select(ECGAIAnalysisFindingRecord)
                            .where(ECGAIAnalysisFindingRecord.analysis_id == analysis.id)
                            .order_by(ECGAIAnalysisFindingRecord.rank)
                        ).all()
                    ]
                if interpretation is not None:
                    sources.append(
                        EvidenceReference(
                            "physician_ecg_interpretation",
                            interpretation.id,
                            interpretation.record_version,
                            interpretation.recorded_at,
                        )
                    )
                    if interpretation.agreement_status == "disagree":
                        conflicts.append(
                            f"ECG study {study.study_id}: physician interpretation differs "
                            "from AI-supported findings"
                        )
                ecg_summaries.append(
                    {
                        "study_id": str(study.study_id),
                        "acquired_at": study.acquisition_at.isoformat(),
                        "quality_status": quality.status if quality else study.quality_status,
                        "ai_status": analysis.status if analysis else None,
                        "ai_supported_findings": findings,
                        "physician_interpretation": interpretation.structured_interpretation
                        if interpretation
                        else None,
                        "agreement_status": interpretation.agreement_status
                        if interpretation
                        else None,
                    }
                )
            freshness["ecg"] = self._freshness(studies[0].acquisition_at, now)
            summaries["ecg"] = ecg_summaries
            if not any(
                self._session.scalar(
                    select(
                        exists().where(PhysicianECGInterpretationRecord.ecg_study_id == s.study_id)
                    )
                )
                for s in studies
            ):
                missing.append("physician_ecg_interpretation")

        all_results = self._session.scalars(
            select(TroponinEvidenceRecord)
            .where(TroponinEvidenceRecord.clinical_case_id == case_id)
            .order_by(TroponinEvidenceRecord.specimen_collected_at)
        ).all()
        superseded = {
            r.supersedes_result_id for r in all_results if r.supersedes_result_id is not None
        }
        results = [
            r
            for r in all_results
            if r.result_id not in superseded and r.result_status != "entered_in_error"
        ]
        if not results:
            missing.append("troponin")
            freshness["troponin"] = EvidenceFreshness.MISSING
        else:
            values: list[dict[str, object]] = []
            for result in results:
                assay = self._session.get(TroponinAssayRecord, result.assay_id)
                sources.append(
                    EvidenceReference(
                        "troponin_result",
                        result.result_id,
                        result.record_version,
                        result.resulted_at,
                    )
                )
                values.append(
                    {
                        "result_id": str(result.result_id),
                        "assay": assay.display_name if assay else None,
                        "analyte": result.analyte,
                        "value": str(result.normalized_value or result.original_value),
                        "unit": result.normalized_unit or result.original_unit,
                        "collected_at": result.specimen_collected_at.isoformat(),
                        "status": result.result_status,
                        "quality_flags": result.quality_flags,
                    }
                )
            compatible = (
                len({(r.assay_id, r.normalized_unit or r.original_unit) for r in results}) == 1
            )
            if not compatible and len(results) > 1:
                conflicts.append("Serial troponin results use incompatible assay or unit context")
            deltas = []
            if compatible:
                for left, right in zip(results, results[1:], strict=False):
                    left_value = left.normalized_value or left.original_value
                    right_value = right.normalized_value or right.original_value
                    deltas.append(
                        {
                            "from_result_id": str(left.result_id),
                            "to_result_id": str(right.result_id),
                            "absolute_delta": str(right_value - left_value),
                            "unit": right.normalized_unit or right.original_unit,
                        }
                    )
            summaries["troponin"] = {
                "effective_results": values,
                "valid_numerical_deltas": deltas,
                "compatible_series": compatible,
            }
            freshness["troponin"] = self._freshness(results[-1].resulted_at, now)

        return ClinicalEvidenceSnapshot(
            uuid4(),
            case_id,
            case.version,
            1,
            now,
            actor_id,
            tuple(sources),
            summaries,
            freshness,
            tuple(missing),
            tuple(conflicts),
        )

    @staticmethod
    def _freshness(relevant_at: Any, now: Any) -> EvidenceFreshness:
        return (
            EvidenceFreshness.STALE
            if now - relevant_at > timedelta(hours=24)
            else EvidenceFreshness.CURRENT
        )

    def add_snapshot(self, value: ClinicalEvidenceSnapshot) -> None:
        self._session.add(
            ClinicalEvidenceSnapshotRecord(
                snapshot_id=value.snapshot_id,
                clinical_case_id=value.clinical_case_id,
                case_version=value.case_version,
                record_version=value.record_version,
                created_at=value.created_at,
                created_by_actor_id=value.created_by_actor_id,
                sources=[
                    {
                        "source_type": s.source_type,
                        "source_id": str(s.source_id),
                        "source_version": s.source_version,
                        "relevant_at": s.relevant_at.isoformat(),
                    }
                    for s in value.sources
                ],
                summaries=dict(value.summaries),
                freshness={k: v.value for k, v in value.freshness.items()},
                missing_evidence=list(value.missing_evidence),
                technical_conflicts=list(value.technical_conflicts),
            )
        )

    def get_snapshot(self, snapshot_id: UUID) -> ClinicalEvidenceSnapshot | None:
        record = self._session.get(ClinicalEvidenceSnapshotRecord, snapshot_id)
        return _snapshot(record) if record else None

    def latest_snapshot(self, case_id: UUID) -> ClinicalEvidenceSnapshot | None:
        record = self._session.scalar(
            select(ClinicalEvidenceSnapshotRecord)
            .where(ClinicalEvidenceSnapshotRecord.clinical_case_id == case_id)
            .order_by(ClinicalEvidenceSnapshotRecord.created_at.desc())
            .limit(1)
        )
        return _snapshot(record) if record else None

    def add_assessment(self, value: PhysicianClinicalAssessment) -> None:
        self._session.add(
            PhysicianClinicalAssessmentRecord(
                assessment_id=value.assessment_id,
                clinical_case_id=value.clinical_case_id,
                snapshot_id=value.snapshot_id,
                revision_number=value.revision_number,
                record_version=value.record_version,
                summary=value.summary,
                supporting_evidence=list(value.supporting_evidence),
                contradictory_evidence=list(value.contradictory_evidence),
                uncertainty=value.uncertainty,
                additional_information_required=value.additional_information_required,
                actor_id=value.actor_id,
                actor_role=value.actor_role,
                recorded_at=value.recorded_at,
                corrects_assessment_id=value.corrects_assessment_id,
                correction_reason=value.correction_reason,
            )
        )

    def get_assessment(self, assessment_id: UUID) -> PhysicianClinicalAssessment | None:
        record = self._session.get(PhysicianClinicalAssessmentRecord, assessment_id)
        return _assessment(record) if record else None

    def assessments(self, case_id: UUID) -> list[PhysicianClinicalAssessment]:
        return [
            _assessment(r)
            for r in self._session.scalars(
                select(PhysicianClinicalAssessmentRecord)
                .where(PhysicianClinicalAssessmentRecord.clinical_case_id == case_id)
                .order_by(PhysicianClinicalAssessmentRecord.revision_number.desc())
            ).all()
        ]

    def add_decision(self, value: PhysicianDecision) -> None:
        self._session.add(
            ClinicalReasoningDecisionRecord(
                decision_id=value.decision_id,
                clinical_case_id=value.clinical_case_id,
                snapshot_id=value.snapshot_id,
                assessment_id=value.assessment_id,
                decision_type=value.decision_type.value,
                status=value.status.value,
                revision_number=value.revision_number,
                record_version=value.record_version,
                rationale=value.rationale,
                uncertainty=value.uncertainty,
                evidence_references=list(value.evidence_references),
                actor_id=value.actor_id,
                actor_role=value.actor_role,
                recorded_at=value.recorded_at,
                corrects_decision_id=value.corrects_decision_id,
                correction_reason=value.correction_reason,
                workflow_authorization_id=value.workflow_authorization_id,
            )
        )

    def get_decision(self, decision_id: UUID) -> PhysicianDecision | None:
        record = self._session.get(ClinicalReasoningDecisionRecord, decision_id)
        return _decision(record) if record else None

    def decisions(self, case_id: UUID) -> list[PhysicianDecision]:
        return [
            _decision(r)
            for r in self._session.scalars(
                select(ClinicalReasoningDecisionRecord)
                .where(ClinicalReasoningDecisionRecord.clinical_case_id == case_id)
                .order_by(ClinicalReasoningDecisionRecord.revision_number.desc())
            ).all()
        ]

    def is_decision_effective(self, decision_id: UUID) -> bool:
        record = self._session.get(ClinicalReasoningDecisionRecord, decision_id)
        if record is None or record.status == DecisionRecordStatus.REVOCATION.value:
            return False
        return not bool(
            self._session.scalar(
                select(
                    exists().where(
                        ClinicalReasoningDecisionRecord.corrects_decision_id == decision_id,
                        ClinicalReasoningDecisionRecord.status
                        == DecisionRecordStatus.REVOCATION.value,
                    )
                )
            )
        )

    def save_risk_score(self, case_id: UUID, score: RiskScore) -> UUID:
        score_id = uuid4()
        self._session.add(
            RiskScoreRecord(
                id=score_id,
                case_id=case_id,
                name=score.name,
                value=score.value,
                algorithm_version=score.algorithm_version,
                calculated_at=score.calculated_at,
            )
        )
        return score_id

    def risk_scores_for_case(self, case_id: UUID) -> tuple[RiskScore, ...]:
        records = self._session.scalars(
            select(RiskScoreRecord)
            .where(RiskScoreRecord.case_id == case_id)
            .order_by(RiskScoreRecord.calculated_at.desc())
        ).all()
        return tuple(
            RiskScore(r.name, r.value, r.algorithm_version, r.calculated_at) for r in records
        )


def _snapshot(r: ClinicalEvidenceSnapshotRecord) -> ClinicalEvidenceSnapshot:
    from datetime import datetime

    return ClinicalEvidenceSnapshot(
        r.snapshot_id,
        r.clinical_case_id,
        r.case_version,
        r.record_version,
        r.created_at,
        r.created_by_actor_id,
        tuple(
            EvidenceReference(
                str(s["source_type"]),
                UUID(str(s["source_id"])),
                int(str(s["source_version"])),
                datetime.fromisoformat(str(s["relevant_at"])),
            )
            for s in r.sources
        ),
        dict(r.summaries),
        {k: EvidenceFreshness(v) for k, v in r.freshness.items()},
        tuple(r.missing_evidence),
        tuple(r.technical_conflicts),
    )


def _assessment(r: PhysicianClinicalAssessmentRecord) -> PhysicianClinicalAssessment:
    return PhysicianClinicalAssessment(
        r.assessment_id,
        r.clinical_case_id,
        r.snapshot_id,
        r.revision_number,
        r.record_version,
        r.summary,
        tuple(r.supporting_evidence),
        tuple(r.contradictory_evidence),
        r.uncertainty,
        r.additional_information_required,
        r.actor_id,
        r.actor_role,
        r.recorded_at,
        r.corrects_assessment_id,
        r.correction_reason,
    )


def _decision(r: ClinicalReasoningDecisionRecord) -> PhysicianDecision:
    return PhysicianDecision(
        r.decision_id,
        r.clinical_case_id,
        r.snapshot_id,
        r.assessment_id,
        ClinicalDecisionType(r.decision_type),
        DecisionRecordStatus(r.status),
        r.revision_number,
        r.record_version,
        r.rationale,
        r.uncertainty,
        tuple(r.evidence_references),
        r.actor_id,
        r.actor_role,
        r.recorded_at,
        r.corrects_decision_id,
        r.correction_reason,
        r.workflow_authorization_id,
    )
