from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

Decision = Literal["candidate_ready", "human_review_required"]

AUTO_MERGE_BLOCKED_LEVELS = frozenset({"medium", "high"})


class RiskGateError(ValueError):
    """Raised when the feature contract risk declaration is invalid."""


@dataclass(frozen=True)
class RiskDecision:
    risk_level: str
    requires_human_review: bool
    reasons: tuple[str, ...]
    decision: Decision


def _load_risk_declaration(workspace_path: Path) -> dict[str, object]:
    contract_path = workspace_path / "contracts" / "feature.yaml"
    if not contract_path.is_file():
        raise RiskGateError(f"Feature contract not found: {contract_path}")

    loaded = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RiskGateError("contracts/feature.yaml must contain a mapping")

    risk = loaded.get("risk")
    if not isinstance(risk, dict):
        raise RiskGateError("contracts/feature.yaml must declare a risk mapping")

    return risk


def _parse_risk_declaration(risk: dict[str, object]) -> tuple[str, bool, tuple[str, ...]]:
    level = risk.get("level")
    if not isinstance(level, str) or not level.strip():
        raise RiskGateError("risk.level must be a non-empty string")

    requires_human_review = risk.get("requires_human_review")
    if not isinstance(requires_human_review, bool):
        raise RiskGateError("risk.requires_human_review must be a boolean")

    reasons_raw = risk.get("reasons", [])
    if not isinstance(reasons_raw, list) or not all(
        isinstance(item, str) for item in reasons_raw
    ):
        raise RiskGateError("risk.reasons must be a list of strings")

    return level.strip().lower(), requires_human_review, tuple(reasons_raw)


def decide_risk(
    *,
    risk_level: str,
    requires_human_review: bool,
    reasons: tuple[str, ...],
    verification_passed: bool,
) -> RiskDecision:
    """
    Apply deterministic risk rules without calling an LLM.

    - Failed verification never yields candidate_ready.
    - requires_human_review=True always yields human_review_required.
    - medium/high risk is never auto-merged.
    - low risk may be candidate_ready only after verification passes.
    """
    normalised_level = risk_level.strip().lower()
    decision_reasons = list(reasons)

    if not verification_passed:
        decision_reasons.append("Verification has not passed.")
        decision: Decision = "human_review_required"
    elif requires_human_review:
        decision = "human_review_required"
    elif normalised_level in AUTO_MERGE_BLOCKED_LEVELS:
        decision_reasons.append(
            f"Risk level '{normalised_level}' must not be auto-merged."
        )
        decision = "human_review_required"
    elif normalised_level == "low":
        decision = "candidate_ready"
    else:
        decision_reasons.append(
            f"Unknown risk level '{normalised_level}' requires human review."
        )
        decision = "human_review_required"

    return RiskDecision(
        risk_level=normalised_level,
        requires_human_review=requires_human_review,
        reasons=tuple(decision_reasons),
        decision=decision,
    )


def evaluate_risk_gate(
    workspace_path: Path,
    *,
    verification_passed: bool,
) -> RiskDecision:
    """
    Read the feature contract risk declaration and return a RiskDecision.
    """
    risk = _load_risk_declaration(workspace_path.resolve())
    risk_level, requires_human_review, reasons = _parse_risk_declaration(risk)
    return decide_risk(
        risk_level=risk_level,
        requires_human_review=requires_human_review,
        reasons=reasons,
        verification_passed=verification_passed,
    )
