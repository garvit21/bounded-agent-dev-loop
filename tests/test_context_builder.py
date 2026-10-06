from __future__ import annotations

from pathlib import Path

from agent.context_builder import ContextBundle, build_context


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _watched_paths(root: Path) -> list[Path]:
    return [
        root / "contracts" / "feature.yaml",
        root / "contracts" / "openapi.json",
        root / "AGENTS.md",
        root / "app" / "main.py",
        root / "app" / "models.py",
    ]


def test_build_context_includes_only_intended_app_files() -> None:
    root = _repo_root()
    bundle = build_context(root)

    assert set(bundle.source_files.keys()) == {"app/main.py", "app/models.py"}
    assert "from fastapi import FastAPI" in bundle.source_files["app/main.py"]
    assert "class HealthResponse" in bundle.source_files["app/models.py"]


def test_build_context_loads_acceptance_criteria() -> None:
    root = _repo_root()
    bundle = build_context(root)

    assert bundle.acceptance_criteria
    assert any("HTTP 201" in criterion for criterion in bundle.acceptance_criteria)
    assert any("Idempotency-Key" in criterion for criterion in bundle.acceptance_criteria)


def test_build_context_excludes_protected_implementation_areas() -> None:
    root = _repo_root()
    bundle = build_context(root)

    forbidden_snippets = (
        "def run_verification",
        "def test_create_inventory_reservation",
        "def create_workspace",
    )
    combined = "\n".join(
        [
            bundle.contract_text,
            bundle.openapi_text,
            bundle.engineering_rules,
            *bundle.source_files.values(),
        ]
    )

    for snippet in forbidden_snippets:
        assert snippet not in combined

    assert "tests/" in bundle.protected_paths
    assert "verification/" in bundle.protected_paths
    assert "workspace/" in bundle.protected_paths


def test_build_context_accepts_previous_failure_evidence() -> None:
    root = _repo_root()
    failures = (
        "pytest_feature failed: 5 failed, 1 passed",
        "ruff failed: Found 1 error in app/main.py",
    )

    bundle = build_context(root, previous_failures=failures)

    assert bundle.previous_failures == failures


def test_build_context_does_not_modify_files() -> None:
    root = _repo_root()
    paths = _watched_paths(root)
    before = {path: path.stat().st_mtime_ns for path in paths}

    build_context(root)

    after = {path: path.stat().st_mtime_ns for path in paths}
    assert before == after


def test_build_context_returns_structured_bundle() -> None:
    root = _repo_root()
    bundle = build_context(root)

    assert isinstance(bundle, ContextBundle)
    assert bundle.task_id == "inventory-reservation"
    assert bundle.goal
    assert bundle.contract_text
    assert bundle.openapi_text
    assert '"openapi": "3.1.0"' in bundle.openapi_text
    assert "Bounded Agent Development Loop" in bundle.engineering_rules
