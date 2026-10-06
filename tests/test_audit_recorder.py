from __future__ import annotations

import json
from pathlib import Path

import pytest

from audit.recorder import AuditError, AuditRecorder, new_run_id
from verification.runner import CheckResult, VerificationResult


def _sample_result(*, passed: bool) -> VerificationResult:
    return VerificationResult(
        passed=passed,
        checks=(
            CheckResult(
                name="pytest_baseline",
                passed=True,
                command=("pytest",),
                exit_code=0,
                stdout="ok",
                stderr="",
            ),
            CheckResult(
                name="pytest_feature",
                passed=passed,
                command=("pytest",),
                exit_code=0 if passed else 1,
                stdout="details should not be persisted",
                stderr="secret-looking stderr",
            ),
        ),
    )


def test_new_run_id_is_unique() -> None:
    assert new_run_id() != new_run_id()


def test_recorder_writes_run_and_verification_artifacts(tmp_path: Path) -> None:
    recorder = AuditRecorder(runs_root=tmp_path, run_id="run-test-001")
    run_dir = recorder.start(
        task_id="inventory-reservation",
        base_commit="abc123",
        generated_branch="agent/inventory-reservation-test",
        model="test-model",
        provider="anthropic",
        max_attempts=3,
    )

    assert run_dir == tmp_path / "run-test-001"
    assert run_dir.is_dir()

    failed = _sample_result(passed=False)
    verification_path = recorder.record_verification(1, failed)
    recorder.record_attempt(
        attempt=1,
        changed_files=["app/main.py"],
        verification_passed=False,
    )

    passed = _sample_result(passed=True)
    recorder.record_verification(2, passed)
    recorder.record_attempt(
        attempt=2,
        changed_files=["app/main.py", "app/models.py"],
        verification_passed=True,
    )

    run_json_path = recorder.complete(
        verification_passed=True,
        risk_level="medium",
        final_decision="human_review_required",
    )

    verification_payload = json.loads(verification_path.read_text(encoding="utf-8"))
    assert verification_payload["attempt"] == 1
    assert verification_payload["passed"] is False
    assert verification_payload["checks"] == [
        {"exit_code": 0, "name": "pytest_baseline", "passed": True},
        {"exit_code": 1, "name": "pytest_feature", "passed": False},
    ]
    serialized = verification_path.read_text(encoding="utf-8")
    assert "details should not be persisted" not in serialized
    assert "secret-looking stderr" not in serialized
    assert "ANTHROPIC_API_KEY" not in serialized
    assert ".env" not in serialized

    run_payload = json.loads(run_json_path.read_text(encoding="utf-8"))
    assert run_payload["run_id"] == "run-test-001"
    assert run_payload["task_id"] == "inventory-reservation"
    assert run_payload["base_commit"] == "abc123"
    assert run_payload["generated_branch"] == "agent/inventory-reservation-test"
    assert run_payload["model"] == "test-model"
    assert run_payload["provider"] == "anthropic"
    assert run_payload["max_attempts"] == 3
    assert run_payload["started_at"]
    assert run_payload["completed_at"]
    assert run_payload["verification_passed"] is True
    assert run_payload["risk_level"] == "medium"
    assert run_payload["final_decision"] == "human_review_required"
    assert run_payload["attempts"] == [
        {
            "attempt": 1,
            "changed_files": ["app/main.py"],
            "verification_passed": False,
        },
        {
            "attempt": 2,
            "changed_files": ["app/main.py", "app/models.py"],
            "verification_passed": True,
        },
    ]
    assert (tmp_path / "run-test-001" / "verification-attempt-2.json").is_file()


def test_recorder_does_not_store_secrets_in_run_json(tmp_path: Path) -> None:
    recorder = AuditRecorder(runs_root=tmp_path, run_id="run-secure")
    recorder.start(
        task_id="t",
        base_commit="c",
        generated_branch="b",
        model="m",
        provider="anthropic",
        max_attempts=1,
    )
    path = recorder.complete(
        verification_passed=False,
        risk_level="low",
        final_decision="human_escalation",
    )
    text = path.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in text
    assert "OPENAI_API_KEY" not in text
    assert "api_key" not in text.lower()


def test_audit_write_failure_is_explicit(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked-file"
    blocked.write_text("not a directory", encoding="utf-8")
    recorder = AuditRecorder(runs_root=blocked, run_id="cannot-create")

    with pytest.raises(AuditError, match="Unable to create audit directory"):
        recorder.start(
            task_id="t",
            base_commit="c",
            generated_branch="b",
            model="m",
            provider="anthropic",
            max_attempts=1,
        )
