"""Transparent, review-required reasoning value objects without clinical rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vinami.shared.domain import DomainInvariantError, require_text, require_utc

# Shared with vinami.reasoning.domain.engine/risk_scores; kept here (a leaf module with no
# intra-context dependents) so those two modules can both use it without an import cycle.
NOTICE = "Demonstration rule — not for clinical use."


@dataclass(frozen=True, slots=True)
class RiskScore:
    """Named score with declared algorithm version; no threshold is embedded here."""

    name: str
    value: float
    algorithm_version: str
    calculated_at: datetime

    def __post_init__(self) -> None:
        """Require transparent score provenance."""
        object.__setattr__(self, "name", require_text(self.name, "risk score name"))
        object.__setattr__(
            self, "algorithm_version", require_text(self.algorithm_version, "algorithm_version")
        )
        object.__setattr__(self, "calculated_at", require_utc(self.calculated_at, "calculated_at"))


@dataclass(frozen=True, slots=True)
class ClinicalPathwaySuggestion:
    """A visible demonstration suggestion that always requires physician review."""

    ruleset_version: str
    summary: str
    created_at: datetime
    requires_physician_confirmation: bool = True
    demonstration_notice: str = "Demonstration rule - not for clinical use."

    def __post_init__(self) -> None:
        """Enforce safety wording and prohibit autonomous consequences."""
        object.__setattr__(
            self, "ruleset_version", require_text(self.ruleset_version, "ruleset_version")
        )
        object.__setattr__(self, "summary", require_text(self.summary, "suggestion summary"))
        object.__setattr__(self, "created_at", require_utc(self.created_at, "created_at"))
        if not self.requires_physician_confirmation:
            raise DomainInvariantError(
                "clinical pathway suggestions require physician confirmation"
            )
        if self.demonstration_notice != "Demonstration rule - not for clinical use.":
            raise DomainInvariantError(
                "clinical pathway must carry the approved demonstration notice"
            )
