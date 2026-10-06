from __future__ import annotations

from pathlib import Path
from typing import TypedDict


class AgentState(TypedDict):
    """
    Structured orchestration state for a bounded implementation loop.

    Intended for later LangGraph use; no graph wiring in this step.
    """

    task_id: str
    goal: str
    workspace_path: Path
    base_commit: str
    allowed_write_paths: list[str]
    protected_paths: list[str]
    acceptance_criteria: list[str]
    attempt: int
    max_attempts: int
    previous_failures: list[str]
    changed_files: list[str]
    verification_passed: bool
    risk_level: str
    requires_human_review: bool
    status: str
