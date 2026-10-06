from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from agent.context_builder import ContextBundle
from agent.implementer import ImplementerError, apply_proposal, run_implementation
from agent.model import ModelProposal, ProposedFileChange


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _make_context(
    *,
    allowed_write_paths: tuple[str, ...] = ("app/main.py", "app/models.py"),
) -> ContextBundle:
    return ContextBundle(
        task_id="inventory-reservation",
        goal="Add inventory reservation endpoint",
        acceptance_criteria=("A valid reservation returns HTTP 201.",),
        allowed_write_paths=allowed_write_paths,
        protected_paths=(
            "contracts/",
            "tests/",
            "verification/",
            "workspace/",
            ".github/",
            "AGENTS.md",
            ".cursor/",
        ),
        contract_text="task_id: inventory-reservation\n",
        openapi_text='{"openapi":"3.1.0"}',
        engineering_rules="Keep writes inside app/ only.",
        source_files={
            "app/main.py": "from fastapi import FastAPI\n\napp = FastAPI()\n",
            "app/models.py": "from pydantic import BaseModel\n",
        },
        previous_failures=(),
    )


class FakeModelClient:
    def __init__(self, proposal: ModelProposal) -> None:
        self.proposal = proposal
        self.calls = 0

    def propose(
        self,
        *,
        system_instructions: str,
        context: ContextBundle,
        previous_failures: Sequence[str],
    ) -> ModelProposal:
        self.calls += 1
        return self.proposal


def _isolated_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "agent-workspace"
    workspace.mkdir()
    app_dir = workspace / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("ORIGINAL_MAIN\n", encoding="utf-8")
    (app_dir / "models.py").write_text("ORIGINAL_MODELS\n", encoding="utf-8")
    return workspace


def test_valid_app_main_update_is_accepted(tmp_path: Path) -> None:
    workspace = _isolated_workspace(tmp_path)
    primary = _repo_root()
    primary_main = primary / "app" / "main.py"
    before = primary_main.read_text(encoding="utf-8")

    proposal = ModelProposal(
        summary="Update health handler comment",
        files=(
            ProposedFileChange(
                path="app/main.py",
                content="UPDATED_MAIN_CONTENT\n",
            ),
        ),
    )
    client = FakeModelClient(proposal)

    result = run_implementation(
        workspace,
        _make_context(),
        primary_repo_root=primary,
        model_client=client,
    )

    assert client.calls == 1
    assert result.changed_files == ("app/main.py",)
    assert (workspace / "app" / "main.py").read_text(encoding="utf-8") == (
        "UPDATED_MAIN_CONTENT\n"
    )
    assert primary_main.read_text(encoding="utf-8") == before


def test_protected_path_update_is_rejected(tmp_path: Path) -> None:
    workspace = _isolated_workspace(tmp_path)
    (workspace / "contracts").mkdir()
    (workspace / "contracts" / "feature.yaml").write_text("keep\n", encoding="utf-8")

    proposal = ModelProposal(
        summary="Tamper with contract",
        files=(
            ProposedFileChange(
                path="contracts/feature.yaml",
                content="hacked\n",
            ),
        ),
    )

    with pytest.raises(ImplementerError, match="Protected path"):
        apply_proposal(
            workspace,
            proposal,
            allowed_write_paths=("app/main.py",),
            primary_repo_root=_repo_root(),
        )

    assert (workspace / "contracts" / "feature.yaml").read_text(encoding="utf-8") == (
        "keep\n"
    )


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    workspace = _isolated_workspace(tmp_path)
    proposal = ModelProposal(
        summary="Escape workspace",
        files=(
            ProposedFileChange(
                path="../secret.txt",
                content="nope\n",
            ),
        ),
    )

    with pytest.raises(ImplementerError, match="Path traversal"):
        apply_proposal(
            workspace,
            proposal,
            allowed_write_paths=("app/main.py", "../secret.txt"),
            primary_repo_root=_repo_root(),
        )


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    workspace = _isolated_workspace(tmp_path)
    absolute = str((tmp_path / "elsewhere.py").resolve())
    proposal = ModelProposal(
        summary="Absolute write",
        files=(
            ProposedFileChange(
                path=absolute,
                content="nope\n",
            ),
        ),
    )

    with pytest.raises(ImplementerError, match="Absolute paths"):
        apply_proposal(
            workspace,
            proposal,
            allowed_write_paths=(absolute, "app/main.py"),
            primary_repo_root=_repo_root(),
        )


def test_primary_repository_files_remain_untouched(tmp_path: Path) -> None:
    workspace = _isolated_workspace(tmp_path)
    primary = _repo_root()

    watched = [
        primary / "app" / "main.py",
        primary / "app" / "models.py",
        primary / "contracts" / "feature.yaml",
        primary / "AGENTS.md",
    ]
    before = {path: path.read_bytes() for path in watched}

    # Also ensure refusing primary-as-workspace.
    with pytest.raises(ImplementerError, match="primary development worktree"):
        apply_proposal(
            primary,
            ModelProposal(
                summary="bad",
                files=(
                    ProposedFileChange(path="app/main.py", content="should-not-write\n"),
                ),
            ),
            allowed_write_paths=("app/main.py",),
            primary_repo_root=primary,
        )

    proposal = ModelProposal(
        summary="Safe isolated update",
        files=(
            ProposedFileChange(path="app/main.py", content="workspace-only\n"),
            ProposedFileChange(path="app/models.py", content="workspace-models\n"),
        ),
    )
    run_implementation(
        workspace,
        _make_context(),
        primary_repo_root=primary,
        model_client=FakeModelClient(proposal),
    )

    after = {path: path.read_bytes() for path in watched}
    assert before == after
    assert (workspace / "app" / "main.py").read_text(encoding="utf-8") == (
        "workspace-only\n"
    )
