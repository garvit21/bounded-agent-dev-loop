from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml


class ContextBuildError(ValueError):
    """Raised when scoped context cannot be assembled safely."""


@dataclass(frozen=True)
class ContextBundle:
    task_id: str
    goal: str
    acceptance_criteria: tuple[str, ...]
    allowed_write_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    contract_text: str
    openapi_text: str
    engineering_rules: str
    source_files: dict[str, str]
    previous_failures: tuple[str, ...]


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise ContextBuildError(f"Expected file not found: {path}")
    return path.read_text(encoding="utf-8")


def _normalise_repo_relative_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/"):
        raise ContextBuildError(f"Invalid repository-relative path: {path!r}")
    parts = Path(cleaned).parts
    if ".." in parts:
        raise ContextBuildError(f"Path traversal is not allowed: {path!r}")
    return cleaned


def _load_feature_contract(workspace_path: Path) -> dict[str, object]:
    contract_path = workspace_path / "contracts" / "feature.yaml"
    raw = _read_text(contract_path)
    loaded = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise ContextBuildError("contracts/feature.yaml must contain a mapping")
    return loaded


def _string_list(value: object, *, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContextBuildError(f"{field_name} must be a list of strings")
    return list(value)


def _read_allowed_source_files(
    workspace_path: Path,
    allowed_write_paths: list[str],
) -> dict[str, str]:
    source_files: dict[str, str] = {}
    for relative_path in allowed_write_paths:
        normalised = _normalise_repo_relative_path(relative_path)
        file_path = workspace_path / normalised
        source_files[normalised] = _read_text(file_path)
    return source_files


def build_context(
    workspace_path: Path,
    *,
    previous_failures: tuple[str, ...] | None = None,
) -> ContextBundle:
    """
    Assemble read-only, scoped context for an implementation agent.

    Loads contract artifacts, engineering rules, and only the application
    files explicitly listed in allowed_write_paths. Never modifies files and
    never recursively dumps the repository.
    """
    root = workspace_path.resolve()
    contract = _load_feature_contract(root)

    task_id = contract.get("task_id")
    goal = contract.get("goal")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ContextBuildError("task_id must be a non-empty string")
    if not isinstance(goal, str) or not goal.strip():
        raise ContextBuildError("goal must be a non-empty string")

    allowed_write_paths = _string_list(
        contract.get("allowed_write_paths"),
        field_name="allowed_write_paths",
    )
    protected_paths = _string_list(
        contract.get("protected_paths"),
        field_name="protected_paths",
    )
    acceptance_criteria = _string_list(
        contract.get("acceptance_criteria"),
        field_name="acceptance_criteria",
    )

    contract_text = _read_text(root / "contracts" / "feature.yaml")
    openapi_text = _read_text(root / "contracts" / "openapi.json")
    engineering_rules = _read_text(root / "AGENTS.md")

    # Validate openapi is readable JSON without embedding parsed structure elsewhere.
    json.loads(openapi_text)

    source_files = _read_allowed_source_files(root, allowed_write_paths)

    return ContextBundle(
        task_id=task_id.strip(),
        goal=goal.strip(),
        acceptance_criteria=tuple(acceptance_criteria),
        allowed_write_paths=tuple(
            _normalise_repo_relative_path(path) for path in allowed_write_paths
        ),
        protected_paths=tuple(protected_paths),
        contract_text=contract_text,
        openapi_text=openapi_text,
        engineering_rules=engineering_rules,
        source_files=source_files,
        previous_failures=previous_failures or (),
    )
