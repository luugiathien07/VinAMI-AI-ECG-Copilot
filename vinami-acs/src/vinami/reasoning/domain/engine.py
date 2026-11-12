"""Transparent demonstration reasoning engine; it never performs a clinical action."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from vinami.reasoning.domain.model import NOTICE
from vinami.reasoning.domain.risk_scores import (
    GraceRiskCategory,
    HeartRiskCategory,
    classify_grace_risk,
    classify_heart_risk,
)


class Pathway(StrEnum):
    STEMI_REVIEW_PATHWAY = "STEMI_REVIEW_PATHWAY"
    VERY_HIGH_RISK_REVIEW = "VERY_HIGH_RISK_REVIEW"
    HIGH_RISK_REVIEW = "HIGH_RISK_REVIEW"
    OBSERVATION = "OBSERVATION"
    RULE_OUT_CANDIDATE_REVIEW = "RULE_OUT_CANDIDATE_REVIEW"
    ALTERNATIVE_DIAGNOSIS_REVIEW = "ALTERNATIVE_DIAGNOSIS_REVIEW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class RuleSpecification:
    """Versioned, review-only rule artifact with no hidden guideline threshold."""

    version: str
    notice: str = NOTICE

    def __post_init__(self) -> None:
        if self.notice != NOTICE:
            raise ValueError("rule specification requires demonstration notice")


@dataclass(frozen=True, slots=True)
class ReasoningInput:
    """Normalized evidence supplied by bounded contexts, not raw model tensors.

    `risk_scores` holds point totals keyed by `RiskScore.name` (e.g.
    `{"HEART": 5.0, "GRACE": 145.0}`), produced by
    `vinami.reasoning.domain.risk_scores.calculate_heart_score`/`calculate_grace_score`.
    """

    symptoms: Mapping[str, bool]
    vitals: Mapping[str, float]
    ecg_quality_acceptable: bool | None
    ai_findings: Mapping[str, float]
    physician_ecg_findings: Mapping[str, bool]
    troponin_ratios: tuple[float, ...]
    risk_scores: Mapping[str, float]
    unresolved_alerts: int
    arrival_minutes: int | None


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    """Auditable explanation of a non-autonomous demonstration recommendation."""

    pathway: Pathway
    rule_version: str
    evidence_used: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    conflicts: tuple[str, ...]
    explanation: str
    required_physician_action: str
    safety_warnings: tuple[str, ...]
    notice: str = NOTICE


class ClinicalReasoningEngine:
    """Evaluate an ordered, inspectable review-only demonstration rule set."""

    def evaluate(self, specification: RuleSpecification, data: ReasoningInput) -> DecisionTrace:
        missing = self._missing(data)
        conflicts = self._conflicts(data)
        if missing:
            return self._trace(Pathway.INSUFFICIENT_DATA, specification, (), missing, conflicts)
        if conflicts:
            return self._trace(
                Pathway.VERY_HIGH_RISK_REVIEW,
                specification,
                ("conflicting evidence",),
                (),
                conflicts,
            )
        if data.physician_ecg_findings.get("stemi_review", False):
            return self._trace(
                Pathway.STEMI_REVIEW_PATHWAY, specification, ("physician ECG review flag",), (), ()
            )
        if data.unresolved_alerts:
            return self._trace(
                Pathway.VERY_HIGH_RISK_REVIEW, specification, ("unresolved safety alert",), (), ()
            )
        if self._high_risk_score(data):
            return self._trace(
                Pathway.HIGH_RISK_REVIEW,
                specification,
                ("high HEART or GRACE risk score",),
                (),
                (),
            )
        if len(data.troponin_ratios) > 1 and data.troponin_ratios[-1] > data.troponin_ratios[-2]:
            return self._trace(
                Pathway.HIGH_RISK_REVIEW, specification, ("rising normalized troponin",), (), ()
            )
        if data.symptoms.get("alternative_diagnosis", False):
            return self._trace(
                Pathway.ALTERNATIVE_DIAGNOSIS_REVIEW,
                specification,
                ("alternative diagnosis fact",),
                (),
                (),
            )
        if data.troponin_ratios and data.troponin_ratios[-1] <= 1:
            return self._trace(
                Pathway.RULE_OUT_CANDIDATE_REVIEW,
                specification,
                ("available normalized troponin",),
                (),
                (),
            )
        return self._trace(
            Pathway.OBSERVATION, specification, ("complete non-escalating demo evidence",), (), ()
        )

    @staticmethod
    def _missing(data: ReasoningInput) -> tuple[str, ...]:
        fields = {
            "ECG quality": data.ecg_quality_acceptable,
            "troponin": bool(data.troponin_ratios),
            "encounter timing": data.arrival_minutes,
        }
        return tuple(name for name, value in fields.items() if value is None or value is False)

    @staticmethod
    def _high_risk_score(data: ReasoningInput) -> bool:
        heart = data.risk_scores.get("HEART")
        grace = data.risk_scores.get("GRACE")
        heart_high = heart is not None and classify_heart_risk(int(heart)) is HeartRiskCategory.HIGH
        grace_high = grace is not None and classify_grace_risk(int(grace)) is GraceRiskCategory.HIGH
        return heart_high or grace_high

    @staticmethod
    def _conflicts(data: ReasoningInput) -> tuple[str, ...]:
        return (
            ("AI and physician ECG findings conflict",)
            if data.ai_findings.get("stemi", 0) > 0
            and data.physician_ecg_findings.get("no_stemi", False)
            else ()
        )

    @staticmethod
    def _trace(
        pathway: Pathway,
        spec: RuleSpecification,
        evidence: tuple[str, ...],
        missing: tuple[str, ...],
        conflicts: tuple[str, ...],
    ) -> DecisionTrace:
        return DecisionTrace(
            pathway,
            spec.version,
            evidence,
            missing,
            conflicts,
            f"Demonstration recommendation: {pathway.value}.",
            "Physician review and documented decision required.",
            ("No automatic discharge, AMI rule-out, or cath-lab activation.",),
        )
