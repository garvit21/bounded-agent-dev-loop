from __future__ import annotations

from pathlib import Path

from agent.risk_gate import RiskDecision, decide_risk, evaluate_risk_gate


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write_feature_contract(
    workspace: Path,
    *,
    level: str,
    requires_human_review: bool,
    reasons: list[str],
) -> None:
    contracts = workspace / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    reason_lines = "\n".join(f"    - {reason}" for reason in reasons)
    content = (
        "task_id: sample-task\n"
        "goal: sample goal\n"
        "risk:\n"
        f"  level: {level}\n"
        f"  requires_human_review: {'true' if requires_human_review else 'false'}\n"
        "  reasons:\n"
        f"{reason_lines}\n"
    )
    (contracts / "feature.yaml").write_text(content, encoding="utf-8")


def test_inventory_feature_requires_human_review_after_successful_verification() -> None:
    decision = evaluate_risk_gate(_repo_root(), verification_passed=True)

    assert isinstance(decision, RiskDecision)
    assert decision.risk_level == "medium"
    assert decision.requires_human_review is True
    assert decision.decision == "human_review_required"
    assert "Modifies inventory state." in decision.reasons
    assert "Introduces idempotency behaviour." in decision.reasons


def test_failed_verification_never_produces_candidate_ready(tmp_path: Path) -> None:
    _write_feature_contract(
        tmp_path,
        level="low",
        requires_human_review=False,
        reasons=["Cosmetic change."],
    )

    decision = evaluate_risk_gate(tmp_path, verification_passed=False)

    assert decision.decision == "human_review_required"
    assert decision.decision != "candidate_ready"


def test_requires_human_review_true_forces_human_review(tmp_path: Path) -> None:
    _write_feature_contract(
        tmp_path,
        level="low",
        requires_human_review=True,
        reasons=["Explicit review flag."],
    )

    decision = evaluate_risk_gate(tmp_path, verification_passed=True)

    assert decision.decision == "human_review_required"


def test_medium_or_high_risk_never_auto_merged() -> None:
    medium = decide_risk(
        risk_level="medium",
        requires_human_review=False,
        reasons=("State change.",),
        verification_passed=True,
    )
    high = decide_risk(
        risk_level="high",
        requires_human_review=False,
        reasons=("Security-sensitive.",),
        verification_passed=True,
    )

    assert medium.decision == "human_review_required"
    assert high.decision == "human_review_required"


def test_low_risk_may_be_candidate_ready_after_verification_passes(
    tmp_path: Path,
) -> None:
    _write_feature_contract(
        tmp_path,
        level="low",
        requires_human_review=False,
        reasons=["Docs typo."],
    )

    decision = evaluate_risk_gate(tmp_path, verification_passed=True)

    assert decision.risk_level == "low"
    assert decision.requires_human_review is False
    assert decision.decision == "candidate_ready"
