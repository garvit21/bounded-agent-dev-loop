from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Sequence

from agent.context_builder import ContextBundle
from agent.model import ModelProposal, ProposedFileChange
from agent.run import main
from verification.runner import CheckResult, VerificationResult
from workspace.manager import Workspace


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


class FakeModelClient:
    def __init__(self, proposal: ModelProposal | None = None) -> None:
        self.calls = 0
        self.proposal = proposal or ModelProposal(
            summary="test",
            files=(
                ProposedFileChange(
                    path="app/main.py",
                    content="from fastapi import FastAPI\n\napp = FastAPI()\n",
                ),
            ),
        )

    def propose(
        self,
        *,
        system_instructions: str,
        context: ContextBundle,
        previous_failures: Sequence[str],
    ) -> ModelProposal:
        self.calls += 1
        return self.proposal


def _seed_workspace(tmp_path: Path, name: str) -> Path:
    primary = _repo_root()
    workspace_path = tmp_path / name
    for relative in (
        "contracts/feature.yaml",
        "contracts/openapi.json",
        "AGENTS.md",
        "app/main.py",
        "app/models.py",
    ):
        target = workspace_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(primary / relative, target)
    return workspace_path


def test_dry_run_requires_no_api_key_and_prints_plan(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("MAX_AGENT_ATTEMPTS", "3")

    buffer = io.StringIO()
    code = main(
        ["contracts/feature.yaml", "--dry-run"],
        out=buffer,
    )

    output = buffer.getvalue()
    assert code == 0
    assert "Dry run" in output
    assert "inventory-reservation" in output
    assert "app/main.py" in output
    assert "app/models.py" in output
    assert "pytest_baseline" in output
    assert "pytest_feature" in output
    assert "ruff" in output
    assert "mypy" in output
    assert "medium" in output
    assert "Retry limit (max attempts): 3" in output
    assert "Auto-merge: disabled" in output
    assert "Auto-deploy: disabled" in output
    assert "Audit output: not created for --dry-run" in output


def test_dry_run_does_not_modify_files_or_create_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    root = _repo_root()
    watched = [
        root / "app" / "main.py",
        root / "contracts" / "feature.yaml",
        root / "AGENTS.md",
    ]
    before = {path: path.read_bytes() for path in watched}
    audit_root = tmp_path / "agent-runs"

    code = main(
        ["contracts/feature.yaml", "--dry-run"],
        out=io.StringIO(),
        runs_root=audit_root,
    )

    assert code == 0
    after = {path: path.read_bytes() for path in watched}
    assert before == after
    assert not audit_root.exists()


def test_real_run_requires_credentials(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    buffer = io.StringIO()
    code = main(["contracts/feature.yaml"], out=buffer)

    assert code == 2
    assert "Configuration error" in buffer.getvalue()
    assert "ANTHROPIC_API_KEY" in buffer.getvalue()


def test_real_run_uses_isolated_workspace_and_mocked_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MAX_AGENT_ATTEMPTS", "2")

    primary = _repo_root()
    workspace_path = _seed_workspace(tmp_path, "cli-workspace")
    audit_root = tmp_path / "agent-runs"

    fake_workspace = Workspace(
        task_id="inventory-reservation",
        branch_name="agent/inventory-reservation-test",
        path=workspace_path,
        base_ref="deadbeef",
    )

    def fake_create_workspace(task_id: str, **kwargs) -> Workspace:
        assert task_id == "inventory-reservation"
        return fake_workspace

    def fake_verification(_path: Path) -> VerificationResult:
        return VerificationResult(
            passed=True,
            checks=(
                CheckResult(
                    name="pytest_baseline",
                    passed=True,
                    command=("pytest",),
                    exit_code=0,
                    stdout="ok",
                    stderr="",
                ),
            ),
        )

    monkeypatch.setattr("agent.run.create_workspace", fake_create_workspace)
    monkeypatch.setattr("agent.run.run_verification", fake_verification)

    client = FakeModelClient(
        ModelProposal(
            summary="cli test",
            files=(
                ProposedFileChange(path="app/main.py", content="CLI_UPDATED\n"),
            ),
        )
    )

    buffer = io.StringIO()
    code = main(
        ["contracts/feature.yaml"],
        out=buffer,
        model_client=client,
        workspace_factory=fake_create_workspace,
        runs_root=audit_root,
        run_id="cli-pass-run",
    )

    output = buffer.getvalue()
    assert code == 0
    assert client.calls == 1
    assert "verification: PASSED" in output
    assert "Generated branch: agent/inventory-reservation-test" in output
    assert "human_review_required" in output
    assert "Auto-merge: not performed" in output
    assert "Auto-deploy: not performed" in output
    assert "Run ID: cli-pass-run" in output
    assert (workspace_path / "app" / "main.py").read_text(encoding="utf-8") == (
        "CLI_UPDATED\n"
    )
    assert "CLI_UPDATED" not in (primary / "app" / "main.py").read_text(encoding="utf-8")

    run_dir = audit_root / "cli-pass-run"
    run_payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_payload["run_id"] == "cli-pass-run"
    assert run_payload["task_id"] == "inventory-reservation"
    assert run_payload["generated_branch"] == "agent/inventory-reservation-test"
    assert run_payload["model"] == "test-model"
    assert run_payload["provider"] == "anthropic"
    assert run_payload["verification_passed"] is True
    assert run_payload["final_decision"] == "human_review_required"
    assert run_payload["attempts"][0]["changed_files"] == ["app/main.py"]
    assert run_payload["attempts"][0]["verification_passed"] is True
    assert "test-key" not in (run_dir / "run.json").read_text(encoding="utf-8")

    verification_payload = json.loads(
        (run_dir / "verification-attempt-1.json").read_text(encoding="utf-8")
    )
    assert verification_payload["checks"] == [
        {"exit_code": 0, "name": "pytest_baseline", "passed": True},
    ]


def test_real_run_prints_failed_attempts_then_escalates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("MAX_AGENT_ATTEMPTS", "2")

    workspace_path = _seed_workspace(tmp_path, "cli-fail-workspace")
    audit_root = tmp_path / "agent-runs-fail"

    fake_workspace = Workspace(
        task_id="inventory-reservation",
        branch_name="agent/inventory-reservation-fail",
        path=workspace_path,
        base_ref="cafebabe",
    )

    def always_fail(_path: Path) -> VerificationResult:
        return VerificationResult(
            passed=False,
            checks=(
                CheckResult(
                    name="pytest_feature",
                    passed=False,
                    command=("pytest",),
                    exit_code=1,
                    stdout="still red",
                    stderr="",
                ),
            ),
        )

    monkeypatch.setattr("agent.run.run_verification", always_fail)

    client = FakeModelClient()
    buffer = io.StringIO()
    code = main(
        ["contracts/feature.yaml"],
        out=buffer,
        model_client=client,
        workspace_factory=lambda task_id, **kwargs: fake_workspace,
        runs_root=audit_root,
        run_id="cli-fail-run",
    )

    output = buffer.getvalue()
    assert code == 0
    assert client.calls == 2
    assert output.count("verification: FAILED") == 2
    assert "human escalation" in output.lower() or "human_escalation" in output
    assert "Generated branch: agent/inventory-reservation-fail" in output

    run_payload = json.loads(
        (audit_root / "cli-fail-run" / "run.json").read_text(encoding="utf-8")
    )
    assert run_payload["verification_passed"] is False
    assert run_payload["final_decision"] == "human_escalation"
    assert len(run_payload["attempts"]) == 2
    assert (audit_root / "cli-fail-run" / "verification-attempt-1.json").is_file()
    assert (audit_root / "cli-fail-run" / "verification-attempt-2.json").is_file()


def test_audit_failure_does_not_flip_verification_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("MAX_AGENT_ATTEMPTS", "1")

    workspace_path = _seed_workspace(tmp_path, "cli-audit-fail")
    # Point runs_root at a regular file so AuditRecorder.start fails.
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("blocked", encoding="utf-8")

    fake_workspace = Workspace(
        task_id="inventory-reservation",
        branch_name="agent/inventory-reservation-audit-fail",
        path=workspace_path,
        base_ref="bead",
    )

    def always_fail(_path: Path) -> VerificationResult:
        return VerificationResult(
            passed=False,
            checks=(
                CheckResult(
                    name="pytest_feature",
                    passed=False,
                    command=("pytest",),
                    exit_code=1,
                    stdout="red",
                    stderr="",
                ),
            ),
        )

    monkeypatch.setattr("agent.run.run_verification", always_fail)

    buffer = io.StringIO()
    code = main(
        ["contracts/feature.yaml"],
        out=buffer,
        model_client=FakeModelClient(),
        workspace_factory=lambda task_id, **kwargs: fake_workspace,
        runs_root=blocked,
        run_id="unused",
    )

    output = buffer.getvalue()
    assert code == 0
    assert "Audit recording failed" in output
    assert "verification: FAILED" in output
    assert "human_escalation" in output
    assert "PASSED" not in output.split("verification:", 1)[-1].splitlines()[0]
