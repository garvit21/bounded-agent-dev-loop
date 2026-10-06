from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from verification.runner import VerificationResult


class AuditError(RuntimeError):
    """Raised when audit evidence cannot be written."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def new_run_id() -> str:
    """Return a unique run identifier."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


@dataclass
class AttemptRecord:
    attempt: int
    changed_files: list[str]
    verification_passed: bool


@dataclass
class AuditRecorder:
    """
    Write structured runtime evidence under .agent-runs/<run_id>/.

    Never stores API keys, .env contents, prompts, or full check stdout/stderr.
    Audit failures must be raised to the caller; they must not alter verification.
    """

    runs_root: Path
    run_id: str = field(default_factory=new_run_id)
    _started_at: str | None = field(default=None, init=False, repr=False)
    _meta: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _attempts: list[AttemptRecord] = field(default_factory=list, init=False, repr=False)

    @property
    def run_dir(self) -> Path:
        return self.runs_root / self.run_id

    def start(
        self,
        *,
        task_id: str,
        base_commit: str,
        generated_branch: str,
        model: str,
        provider: str,
        max_attempts: int,
    ) -> Path:
        """Create the run directory and capture immutable run metadata."""
        try:
            self.run_dir.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise AuditError(
                f"Unable to create audit directory {self.run_dir}: {exc}"
            ) from exc

        self._started_at = _utc_now_iso()
        self._meta = {
            "run_id": self.run_id,
            "task_id": task_id,
            "base_commit": base_commit,
            "generated_branch": generated_branch,
            "model": model,
            "provider": provider,
            "max_attempts": max_attempts,
            "started_at": self._started_at,
        }
        return self.run_dir

    def record_verification(
        self,
        attempt: int,
        result: VerificationResult,
    ) -> Path:
        """
        Persist a compact verification artifact for one verification cycle.

        Stores only name/passed/exit_code per check.
        """
        if not self.run_dir.is_dir():
            raise AuditError("Audit run has not been started")

        payload = {
            "attempt": attempt,
            "passed": result.passed,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "exit_code": check.exit_code,
                }
                for check in result.checks
            ],
        }
        path = self.run_dir / f"verification-attempt-{attempt}.json"
        try:
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise AuditError(
                f"Unable to write verification artifact {path}: {exc}"
            ) from exc
        return path

    def record_attempt(
        self,
        *,
        attempt: int,
        changed_files: list[str],
        verification_passed: bool,
    ) -> None:
        """Record per-attempt implementation/verification summary for run.json."""
        self._attempts.append(
            AttemptRecord(
                attempt=attempt,
                changed_files=list(changed_files),
                verification_passed=verification_passed,
            )
        )

    def complete(
        self,
        *,
        verification_passed: bool,
        risk_level: str,
        final_decision: str,
    ) -> Path:
        """Write the final run.json summary."""
        if not self.run_dir.is_dir() or self._started_at is None:
            raise AuditError("Audit run has not been started")

        payload = {
            **self._meta,
            "completed_at": _utc_now_iso(),
            "attempts": [
                {
                    "attempt": item.attempt,
                    "changed_files": item.changed_files,
                    "verification_passed": item.verification_passed,
                }
                for item in self._attempts
            ],
            "verification_passed": verification_passed,
            "risk_level": risk_level,
            "final_decision": final_decision,
        }
        path = self.run_dir / "run.json"
        try:
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise AuditError(f"Unable to write run.json at {path}: {exc}") from exc
        return path
