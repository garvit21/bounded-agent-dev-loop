from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent.context_builder import ContextBundle
from agent.model import ModelClient, ModelProposal, propose_changes

DEFAULT_SYSTEM_INSTRUCTIONS = (
    "You are a bounded implementation agent. Modify only files listed in "
    "allowed_write_paths. Do not modify contracts, tests, verification, "
    "workspace management, CI, or repository rules. Return JSON only."
)

PROTECTED_EXACT = frozenset(
    {
        "AGENTS.md",
    }
)

PROTECTED_PREFIXES = (
    "contracts/",
    "tests/",
    "verification/",
    "workspace/",
    ".github/",
    ".cursor/",
)


class ImplementerError(ValueError):
    """Raised when a proposed change violates write boundaries."""


@dataclass(frozen=True)
class ImplementationResult:
    summary: str
    changed_files: tuple[str, ...]


def _normalise_proposed_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    if not cleaned:
        raise ImplementerError("Proposed path must be a non-empty string")

    candidate = Path(cleaned)
    if candidate.is_absolute() or cleaned.startswith(("/", "~")):
        raise ImplementerError(f"Absolute paths are not permitted: {path!r}")

    if ".." in candidate.parts:
        raise ImplementerError(f"Path traversal is not permitted: {path!r}")

    if cleaned in PROTECTED_EXACT:
        raise ImplementerError(f"Protected path is not writable: {cleaned}")

    for prefix in PROTECTED_PREFIXES:
        if cleaned == prefix.rstrip("/") or cleaned.startswith(prefix):
            raise ImplementerError(f"Protected path is not writable: {cleaned}")

    return cleaned


def _assert_path_allowed(path: str, allowed_write_paths: Sequence[str]) -> None:
    allowed = {item.replace("\\", "/") for item in allowed_write_paths}
    if path not in allowed:
        raise ImplementerError(
            f"Path is not in allowed_write_paths: {path}. "
            f"Allowed: {sorted(allowed)}"
        )


def _assert_target_inside_workspace(workspace_path: Path, relative_path: str) -> Path:
    root = workspace_path.resolve()
    target = (root / relative_path).resolve()
    if not target.is_relative_to(root):
        raise ImplementerError(
            f"Resolved write target escapes workspace: {relative_path}"
        )
    return target


def _assert_not_primary_worktree(
    workspace_path: Path,
    primary_repo_root: Path,
) -> None:
    workspace = workspace_path.resolve()
    primary = primary_repo_root.resolve()
    if workspace == primary:
        raise ImplementerError(
            "Refusing to write into the primary development worktree; "
            "supply an isolated workspace path"
        )


def apply_proposal(
    workspace_path: Path,
    proposal: ModelProposal,
    *,
    allowed_write_paths: Sequence[str],
    primary_repo_root: Path,
) -> list[str]:
    """
    Validate a model proposal and write permitted files into workspace_path.

    Generated code is written as text only; it is never executed.
    """
    _assert_not_primary_worktree(workspace_path, primary_repo_root)

    changed: list[str] = []
    pending_writes: list[tuple[str, Path, str]] = []

    for file_change in proposal.files:
        relative_path = _normalise_proposed_path(file_change.path)
        _assert_path_allowed(relative_path, allowed_write_paths)
        target = _assert_target_inside_workspace(workspace_path, relative_path)
        pending_writes.append((relative_path, target, file_change.content))

    for relative_path, target, content in pending_writes:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        changed.append(relative_path)

    return changed


def run_implementation(
    workspace_path: Path,
    context: ContextBundle,
    *,
    primary_repo_root: Path,
    system_instructions: str = DEFAULT_SYSTEM_INSTRUCTIONS,
    previous_failures: Sequence[str] | None = None,
    model_client: ModelClient | None = None,
) -> ImplementationResult:
    """
    Ask the model for scoped changes, validate them, and apply them only
    inside the isolated workspace.
    """
    failures = (
        tuple(previous_failures)
        if previous_failures is not None
        else context.previous_failures
    )

    proposal = propose_changes(
        system_instructions=system_instructions,
        context=context,
        previous_failures=failures,
        client=model_client,
    )

    changed_files = apply_proposal(
        workspace_path,
        proposal,
        allowed_write_paths=context.allowed_write_paths,
        primary_repo_root=primary_repo_root,
    )

    return ImplementationResult(
        summary=proposal.summary,
        changed_files=tuple(changed_files),
    )
