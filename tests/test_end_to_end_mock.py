from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Sequence

import pytest

from agent.context_builder import ContextBundle
from agent.model import ModelProposal, ProposedFileChange
from agent.risk_gate import evaluate_risk_gate
from agent.run import main
from verification.runner import run_verification
from workspace.manager import (
    create_workspace,
    get_repository_root,
    remove_workspace,
)


MOCK_MODELS_PY = '''\
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class ReservationRequest(BaseModel):
    item_id: str
    quantity: int = Field(ge=1)


class ReservationResponse(BaseModel):
    reservation_id: str
    item_id: str
    quantity: int = Field(ge=1)
    status: str
'''


MOCK_MAIN_PY = '''\
from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException

from app.models import HealthResponse, ReservationRequest, ReservationResponse

app = FastAPI(
    title="Bounded Agent Development Loop",
    version="0.1.0",
)

KNOWN_INVENTORY: dict[str, int] = {
    "item-001": 10,
    "item-002": 5,
}

_idempotency_store: dict[str, ReservationResponse] = {}


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post(
    "/inventory/reservations",
    response_model=ReservationResponse,
    status_code=201,
    tags=["inventory"],
)
def create_inventory_reservation(
    body: ReservationRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1),
    ],
) -> ReservationResponse:
    existing = _idempotency_store.get(idempotency_key)
    if existing is not None:
        return existing

    if body.item_id not in KNOWN_INVENTORY:
        raise HTTPException(
            status_code=404,
            detail="Inventory item not found",
        )

    reservation = ReservationResponse(
        reservation_id=str(uuid4()),
        item_id=body.item_id,
        quantity=body.quantity,
        status="reserved",
    )
    _idempotency_store[idempotency_key] = reservation
    return reservation
'''


class FakeModelClient:
    """Deterministic model stub — no network, no API key usage."""

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


def _fingerprint(paths: list[Path]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in paths:
        digests[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _watched_primary_files(repo_root: Path) -> list[Path]:
    return [
        repo_root / "app" / "main.py",
        repo_root / "app" / "models.py",
        repo_root / "contracts" / "feature.yaml",
        repo_root / "contracts" / "openapi.json",
        repo_root / "tests" / "test_feature.py",
        repo_root / "AGENTS.md",
    ]


def _inventory_proposal() -> ModelProposal:
    return ModelProposal(
        summary="Add inventory reservation endpoint with idempotency",
        files=(
            ProposedFileChange(path="app/models.py", content=MOCK_MODELS_PY),
            ProposedFileChange(path="app/main.py", content=MOCK_MAIN_PY),
        ),
    )


def test_end_to_end_mocked_inventory_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Exercise the real bounded loop with a mocked model response that satisfies
    the human-authored inventory acceptance tests.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used-by-mock")
    monkeypatch.setenv("LLM_MODEL", "mock-model")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MAX_AGENT_ATTEMPTS", "3")

    repo_root = get_repository_root()
    watched = _watched_primary_files(repo_root)
    before = _fingerprint(watched)

    audit_root = tmp_path / "agent-runs"
    run_id = "e2e-mock-inventory"
    workspace = create_workspace(
        "inventory-reservation-e2e",
        repo_root=repo_root,
    )

    try:
        # 2. Feature verification initially fails in the isolated worktree.
        initial = run_verification(workspace.path)
        assert initial.passed is False
        by_name = {check.name: check for check in initial.checks}
        assert by_name["pytest_feature"].passed is False

        fake_model = FakeModelClient(_inventory_proposal())
        buffer = io.StringIO()

        # 3–8. Real CLI path: bounded implementer, real verifier, real risk gate.
        exit_code = main(
            ["contracts/feature.yaml"],
            out=buffer,
            model_client=fake_model,
            workspace_factory=lambda task_id, **kwargs: workspace,
            runs_root=audit_root,
            run_id=run_id,
        )

        output = buffer.getvalue()
        assert exit_code == 0
        assert fake_model.calls >= 1
        assert "human_review_required" in output

        # 5–6. Real deterministic verifier: all configured checks pass.
        final_verification = run_verification(workspace.path)
        assert final_verification.passed is True
        final_by_name = {check.name: check for check in final_verification.checks}
        for required in ("pytest_baseline", "pytest_feature", "ruff", "mypy"):
            assert required in final_by_name
            assert final_by_name[required].passed is True, (
                f"{required} failed: "
                f"{final_by_name[required].stdout}\n{final_by_name[required].stderr}"
            )

        # 7–8. Real risk gate → human review (medium risk inventory change).
        decision = evaluate_risk_gate(
            workspace.path,
            verification_passed=True,
        )
        assert decision.decision == "human_review_required"
        assert decision.decision.upper() == "HUMAN_REVIEW_REQUIRED"
        assert decision.risk_level == "medium"
        assert decision.requires_human_review is True

        # 9. Primary developer working tree unchanged.
        assert _fingerprint(watched) == before
        assert "create_inventory_reservation" not in (
            repo_root / "app" / "main.py"
        ).read_text(encoding="utf-8")

        # Changes exist only in the isolated worktree.
        assert "create_inventory_reservation" in (
            workspace.path / "app" / "main.py"
        ).read_text(encoding="utf-8")

        # 10. Audit evidence in the temporary audit directory.
        run_dir = audit_root / run_id
        run_json = run_dir / "run.json"
        verification_json = run_dir / "verification-attempt-1.json"
        assert run_json.is_file()
        assert verification_json.is_file()

        run_payload = json.loads(run_json.read_text(encoding="utf-8"))
        assert run_payload["run_id"] == run_id
        assert run_payload["verification_passed"] is True
        assert run_payload["final_decision"] == "human_review_required"
        assert run_payload["risk_level"] == "medium"
        assert "test-key-not-used-by-mock" not in run_json.read_text(encoding="utf-8")

        verification_payload = json.loads(
            verification_json.read_text(encoding="utf-8")
        )
        assert verification_payload["passed"] is True
        assert {item["name"] for item in verification_payload["checks"]} >= {
            "pytest_baseline",
            "pytest_feature",
            "ruff",
            "mypy",
        }
    finally:
        # 11. Clean up the isolated Git worktree.
        remove_workspace(workspace, repo_root=repo_root, force=True)
        assert not workspace.path.exists()
