from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from langgraph.graph import END, START, StateGraph

from agent.context_builder import build_context
from agent.implementer import run_implementation
from agent.model import ModelClient
from agent.risk_gate import evaluate_risk_gate
from agent.state import AgentState
from verification.runner import VerificationResult, run_verification

VerificationRunner = Callable[[Path], VerificationResult]

RouteAfterVerify = Literal["risk_gate", "build_context", "escalate"]


def _format_verification_failures(result: VerificationResult) -> list[str]:
    failures: list[str] = []
    for check in result.checks:
        if check.passed:
            continue
        detail = (check.stdout or check.stderr or "").strip()
        if detail:
            failures.append(f"{check.name} failed (exit {check.exit_code}): {detail}")
        else:
            failures.append(f"{check.name} failed (exit {check.exit_code})")
    return failures


def route_after_verify(state: AgentState) -> RouteAfterVerify:
    """
    Explicit bounded routing after deterministic verification.

    - pass -> risk_gate
    - fail with attempts remaining -> build_context
    - fail with attempts exhausted -> escalate
    """
    if state["verification_passed"]:
        return "risk_gate"
    if state["attempt"] < state["max_attempts"]:
        return "build_context"
    return "escalate"


def build_agent_graph(
    *,
    primary_repo_root: Path,
    model_client: ModelClient,
    verification_runner: VerificationRunner | None = None,
) -> object:
    """
    Compile the bounded LangGraph orchestration loop.

    Workspace creation, verification rules, implementation writes and risk
    decisions remain owned by their existing modules. This graph only
    coordinates state transitions. It never merges or deploys.
    """
    primary_root = primary_repo_root.resolve()
    run_checks = verification_runner or run_verification

    def build_context_node(state: AgentState) -> dict[str, object]:
        next_attempt = state["attempt"] + 1
        bundle = build_context(
            Path(state["workspace_path"]),
            previous_failures=tuple(state.get("previous_failures") or ()),
        )
        return {
            "attempt": next_attempt,
            "task_id": bundle.task_id,
            "goal": bundle.goal,
            "allowed_write_paths": list(bundle.allowed_write_paths),
            "protected_paths": list(bundle.protected_paths),
            "acceptance_criteria": list(bundle.acceptance_criteria),
            "status": "context_ready",
        }

    def implement_node(state: AgentState) -> dict[str, object]:
        workspace_path = Path(state["workspace_path"])
        bundle = build_context(
            workspace_path,
            previous_failures=tuple(state.get("previous_failures") or ()),
        )
        result = run_implementation(
            workspace_path,
            bundle,
            primary_repo_root=primary_root,
            previous_failures=tuple(state.get("previous_failures") or ()),
            model_client=model_client,
        )
        return {
            "changed_files": list(result.changed_files),
            "status": "implemented",
        }

    def verify_node(state: AgentState) -> dict[str, object]:
        workspace_path = Path(state["workspace_path"])
        result = run_checks(workspace_path)
        if result.passed:
            return {
                "verification_passed": True,
                "previous_failures": [],
                "status": "verification_passed",
            }
        return {
            "verification_passed": False,
            "previous_failures": _format_verification_failures(result),
            "status": "verification_failed",
        }

    def risk_gate_node(state: AgentState) -> dict[str, object]:
        decision = evaluate_risk_gate(
            Path(state["workspace_path"]),
            verification_passed=bool(state["verification_passed"]),
        )
        # Persist the gate outcome only. Never merge or deploy.
        return {
            "risk_level": decision.risk_level,
            "requires_human_review": decision.requires_human_review,
            "status": decision.decision,
        }

    def escalate_node(state: AgentState) -> dict[str, object]:
        return {
            "status": "human_escalation",
            "verification_passed": False,
        }

    graph = StateGraph(AgentState)
    graph.add_node("build_context", build_context_node)
    graph.add_node("implement", implement_node)
    graph.add_node("verify", verify_node)
    graph.add_node("risk_gate", risk_gate_node)
    graph.add_node("escalate", escalate_node)

    graph.add_edge(START, "build_context")
    graph.add_edge("build_context", "implement")
    graph.add_edge("implement", "verify")
    graph.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "risk_gate": "risk_gate",
            "build_context": "build_context",
            "escalate": "escalate",
        },
    )
    graph.add_edge("risk_gate", END)
    graph.add_edge("escalate", END)

    return graph.compile()


def initial_agent_state(
    *,
    workspace_path: Path,
    max_attempts: int,
    base_commit: str = "HEAD",
    task_id: str = "",
    goal: str = "",
) -> AgentState:
    """Construct a minimal starting AgentState for graph invocation."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    return AgentState(
        task_id=task_id,
        goal=goal,
        workspace_path=workspace_path.resolve(),
        base_commit=base_commit,
        allowed_write_paths=[],
        protected_paths=[],
        acceptance_criteria=[],
        attempt=0,
        max_attempts=max_attempts,
        previous_failures=[],
        changed_files=[],
        verification_passed=False,
        risk_level="",
        requires_human_review=False,
        status="started",
    )
