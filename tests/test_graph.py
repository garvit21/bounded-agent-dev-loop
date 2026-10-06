from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

from agent.context_builder import ContextBundle
from agent.graph import build_agent_graph, initial_agent_state, route_after_verify
from agent.model import ModelProposal, ProposedFileChange
from agent.state import AgentState
from verification.runner import CheckResult, VerificationResult


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _seed_workspace(tmp_path: Path) -> Path:
    """Copy contract/app scaffolding into an isolated workspace for the graph."""
    primary = _repo_root()
    workspace = tmp_path / "graph-workspace"
    workspace.mkdir()

    for relative in (
        "contracts/feature.yaml",
        "contracts/openapi.json",
        "AGENTS.md",
        "app/main.py",
        "app/models.py",
    ):
        source = primary / relative
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    return workspace


class RecordingModelClient:
    def __init__(self, proposals: Sequence[ModelProposal]) -> None:
        self._proposals = list(proposals)
        self.calls = 0
        self.seen_previous_failures: list[tuple[str, ...]] = []

    def propose(
        self,
        *,
        system_instructions: str,
        context: ContextBundle,
        previous_failures: Sequence[str],
    ) -> ModelProposal:
        self.calls += 1
        self.seen_previous_failures.append(tuple(previous_failures))
        if not self._proposals:
            raise AssertionError("Model client has no remaining proposals")
        return self._proposals.pop(0)


def _proposal(content: str = "from fastapi import FastAPI\n\napp = FastAPI()\n") -> ModelProposal:
    return ModelProposal(
        summary="noop change",
        files=(ProposedFileChange(path="app/main.py", content=content),),
    )


def _verification_result(*, passed: bool, detail: str = "boom") -> VerificationResult:
    if passed:
        checks = (
            CheckResult(
                name="pytest_baseline",
                passed=True,
                command=("pytest",),
                exit_code=0,
                stdout="ok",
                stderr="",
            ),
        )
    else:
        checks = (
            CheckResult(
                name="pytest_feature",
                passed=False,
                command=("pytest",),
                exit_code=1,
                stdout=detail,
                stderr="",
            ),
        )
    return VerificationResult(passed=passed, checks=checks)


def test_route_after_verify_branches() -> None:
    base = AgentState(
        task_id="t",
        goal="g",
        workspace_path=Path("."),
        base_commit="abc",
        allowed_write_paths=[],
        protected_paths=[],
        acceptance_criteria=[],
        attempt=1,
        max_attempts=3,
        previous_failures=[],
        changed_files=[],
        verification_passed=True,
        risk_level="",
        requires_human_review=False,
        status="verification_passed",
    )
    assert route_after_verify(base) == "risk_gate"

    failed = dict(base)
    failed["verification_passed"] = False
    assert route_after_verify(failed) == "build_context"  # type: ignore[arg-type]

    exhausted = dict(failed)
    exhausted["attempt"] = 3
    assert route_after_verify(exhausted) == "escalate"  # type: ignore[arg-type]


def test_graph_passes_to_risk_gate_without_merge(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    client = RecordingModelClient([_proposal("PASS_CONTENT\n")])

    def always_pass(_path: Path) -> VerificationResult:
        return _verification_result(passed=True)

    graph = build_agent_graph(
        primary_repo_root=_repo_root(),
        model_client=client,
        verification_runner=always_pass,
    )

    final_state = graph.invoke(
        initial_agent_state(workspace_path=workspace, max_attempts=3)
    )

    assert client.calls == 1
    assert final_state["verification_passed"] is True
    assert final_state["attempt"] == 1
    assert final_state["status"] == "human_review_required"
    assert final_state["risk_level"] == "medium"
    assert final_state["requires_human_review"] is True
    assert (workspace / "app" / "main.py").read_text(encoding="utf-8") == "PASS_CONTENT\n"
    # Primary tree untouched.
    assert "PASS_CONTENT" not in (_repo_root() / "app" / "main.py").read_text(
        encoding="utf-8"
    )


def test_graph_retries_with_previous_failures_then_escalates(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    client = RecordingModelClient(
        [
            _proposal("attempt-1\n"),
            _proposal("attempt-2\n"),
        ]
    )

    def always_fail(_path: Path) -> VerificationResult:
        return _verification_result(passed=False, detail="feature still red")

    graph = build_agent_graph(
        primary_repo_root=_repo_root(),
        model_client=client,
        verification_runner=always_fail,
    )

    final_state = graph.invoke(
        initial_agent_state(workspace_path=workspace, max_attempts=2)
    )

    assert client.calls == 2
    assert final_state["attempt"] == 2
    assert final_state["verification_passed"] is False
    assert final_state["status"] == "human_escalation"
    assert final_state["previous_failures"]
    assert any("pytest_feature failed" in item for item in final_state["previous_failures"])

    # First implement call has no prior failures; second receives failure evidence.
    assert client.seen_previous_failures[0] == ()
    assert client.seen_previous_failures[1]
    assert any("feature still red" in item for item in client.seen_previous_failures[1])


def test_max_attempts_comes_from_state(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    client = RecordingModelClient([_proposal(), _proposal(), _proposal()])

    def always_fail(_path: Path) -> VerificationResult:
        return _verification_result(passed=False)

    graph = build_agent_graph(
        primary_repo_root=_repo_root(),
        model_client=client,
        verification_runner=always_fail,
    )

    final_state = graph.invoke(
        initial_agent_state(workspace_path=workspace, max_attempts=3)
    )

    assert client.calls == 3
    assert final_state["max_attempts"] == 3
    assert final_state["attempt"] == 3
    assert final_state["status"] == "human_escalation"
