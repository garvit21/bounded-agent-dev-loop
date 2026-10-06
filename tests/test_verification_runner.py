from __future__ import annotations

from pathlib import Path

from verification.runner import CheckResult, VerificationResult, run_verification

EXPECTED_CHECK_NAMES = (
    "pytest_baseline",
    "pytest_feature",
    "ruff",
    "mypy",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _checks_by_name(result: VerificationResult) -> dict[str, CheckResult]:
    return {check.name: check for check in result.checks}


def test_run_verification_returns_structured_result() -> None:
    result = run_verification(_repo_root())

    assert isinstance(result, VerificationResult)
    assert isinstance(result.passed, bool)
    assert isinstance(result.checks, tuple)
    assert result.checks

    for check in result.checks:
        assert isinstance(check, CheckResult)
        assert isinstance(check.name, str)
        assert isinstance(check.passed, bool)
        assert isinstance(check.command, tuple)
        assert check.command
        assert isinstance(check.exit_code, int)
        assert isinstance(check.stdout, str)
        assert isinstance(check.stderr, str)


def test_run_verification_includes_every_expected_check() -> None:
    result = run_verification(_repo_root())
    names = [check.name for check in result.checks]

    assert names == list(EXPECTED_CHECK_NAMES)


def test_baseline_passes_feature_fails_overall_fails_without_raising() -> None:
    result = run_verification(_repo_root())
    checks = _checks_by_name(result)

    assert checks["pytest_baseline"].passed is True
    assert checks["pytest_feature"].passed is False
    assert result.passed is False

    # Failure is returned as structured evidence, not raised.
    assert checks["pytest_feature"].exit_code != 0
    assert checks["pytest_feature"].stdout or checks["pytest_feature"].stderr
