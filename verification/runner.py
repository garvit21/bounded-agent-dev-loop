from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CHECK_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    checks: tuple[CheckResult, ...]


def _configured_checks() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return ordered (name, command-tail) pairs for deterministic checks."""
    python = sys.executable
    return (
        (
            "pytest_baseline",
            (python, "-m", "pytest", "tests/test_baseline.py", "-q"),
        ),
        (
            "pytest_feature",
            (python, "-m", "pytest", "tests/test_feature.py", "-q"),
        ),
        (
            "ruff",
            (python, "-m", "ruff", "check", "app"),
        ),
        (
            "mypy",
            (python, "-m", "mypy", "app"),
        ),
    )


def _run_check(
    name: str,
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout: int,
) -> CheckResult:
    """
    Run a single deterministic check.

    Failures and timeouts become CheckResult evidence; they do not raise.
    """
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode()
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode()
        timeout_note = f"Command timed out after {timeout} seconds"
        return CheckResult(
            name=name,
            passed=False,
            command=command,
            exit_code=-1,
            stdout=stdout or "",
            stderr=(stderr + ("\n" if stderr else "") + timeout_note).strip(),
        )

    return CheckResult(
        name=name,
        passed=completed.returncode == 0,
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def run_verification(
    workspace_path: Path,
    *,
    timeout_seconds: int = DEFAULT_CHECK_TIMEOUT_SECONDS,
) -> VerificationResult:
    """
    Run all configured deterministic checks against workspace_path.

    Every check runs even if an earlier check fails. Overall verification
    passes only when every check passes. This function never modifies the
    workspace and never calls an LLM.
    """
    cwd = workspace_path.resolve()
    results: list[CheckResult] = []

    for name, command in _configured_checks():
        results.append(
            _run_check(
                name,
                command,
                cwd=cwd,
                timeout=timeout_seconds,
            )
        )

    checks = tuple(results)
    return VerificationResult(
        passed=all(check.passed for check in checks),
        checks=checks,
    )
