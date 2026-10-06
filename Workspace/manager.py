from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class WorkspaceError(RuntimeError):
    """Raised when an isolated Git workspace cannot be created or managed."""


@dataclass(frozen=True)
class Workspace:
    task_id: str
    branch_name: str
    path: Path
    base_ref: str


def _run_git(
    args: list[str],
    *,
    cwd: Path,
    timeout: int = 30,
) -> str:
    """
    Run a Git command deterministically.

    shell=True is intentionally avoided.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(
            f"Git command timed out: git {' '.join(args)}"
        ) from exc

    if result.returncode != 0:
        raise WorkspaceError(
            f"Git command failed: git {' '.join(args)}\n"
            f"{result.stderr.strip()}"
        )

    return result.stdout.strip()


def get_repository_root(start_path: Path | None = None) -> Path:
    """Return the root directory of the current Git repository."""
    start_path = (start_path or Path.cwd()).resolve()

    output = _run_git(
        ["rev-parse", "--show-toplevel"],
        cwd=start_path,
    )

    return Path(output).resolve()


def _normalise_task_id(task_id: str) -> str:
    """
    Convert a task identifier into a safe Git branch/path component.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", task_id.strip())
    cleaned = cleaned.strip("-_").lower()

    if not cleaned:
        raise WorkspaceError("task_id must contain at least one valid character")

    return cleaned[:50]


def create_workspace(
    task_id: str,
    *,
    base_ref: str = "HEAD",
    repo_root: Path | None = None,
) -> Workspace:
    """
    Create an isolated Git worktree for an agent task.

    Worktrees are created as a sibling of the main repository rather
    than inside it.
    """
    repo_root = (repo_root or get_repository_root()).resolve()

    safe_task_id = _normalise_task_id(task_id)
    unique_suffix = uuid4().hex[:8]

    branch_name = f"agent/{safe_task_id}-{unique_suffix}"

    worktree_root = (
        repo_root.parent / f"{repo_root.name}-worktrees"
    ).resolve()

    worktree_root.mkdir(parents=True, exist_ok=True)

    workspace_path = (
        worktree_root / f"{safe_task_id}-{unique_suffix}"
    ).resolve()

    # Resolve the base reference now so the task is anchored
    # to a known commit rather than a moving branch.
    base_commit = _run_git(
        ["rev-parse", base_ref],
        cwd=repo_root,
    )

    _run_git(
        [
            "worktree",
            "add",
            "-b",
            branch_name,
            str(workspace_path),
            base_commit,
        ],
        cwd=repo_root,
    )

    return Workspace(
        task_id=safe_task_id,
        branch_name=branch_name,
        path=workspace_path,
        base_ref=base_commit,
    )


def remove_workspace(
    workspace: Workspace,
    *,
    repo_root: Path | None = None,
    force: bool = False,
) -> None:
    """
    Remove an isolated worktree.

    By default, removal fails if Git considers the worktree unsafe
    to remove. force=True must be explicitly requested.
    """
    repo_root = (repo_root or get_repository_root()).resolve()

    args = ["worktree", "remove"]

    if force:
        args.append("--force")

    args.append(str(workspace.path))

    _run_git(args, cwd=repo_root)


def workspace_has_changes(workspace: Workspace) -> bool:
    """Return True if the agent workspace contains uncommitted changes."""
    output = _run_git(
        ["status", "--porcelain"],
        cwd=workspace.path,
    )

    return bool(output)