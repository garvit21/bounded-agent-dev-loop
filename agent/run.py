from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TextIO, cast

import yaml

from agent.graph import build_agent_graph, initial_agent_state
from agent.model import AnthropicModelClient, ModelClient, ModelConfigError, load_model_credentials
from agent.risk_gate import evaluate_risk_gate
from agent.state import AgentState
from audit.recorder import AuditError, AuditRecorder, new_run_id
from verification.runner import VerificationResult, _configured_checks, run_verification
from workspace.manager import Workspace, create_workspace, get_repository_root

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_PROVIDER = "anthropic"


class _CompiledAgentGraph(Protocol):
    """Minimal surface used by the CLI for a compiled LangGraph runnable."""

    def stream(
        self,
        input: AgentState,
        stream_mode: str = "values",
    ) -> Iterator[object]: ...


def _mapping_str_keys(value: object) -> dict[str, object] | None:
    """Narrow a YAML/JSON-like mapping to dict[str, object], or None."""
    if not isinstance(value, dict):
        return None
    narrowed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        narrowed[key] = item
    return narrowed


def _string_list(value: object, *, field_name: str) -> list[str]:
    """Validate a contract field as a list of strings (empty if absent/falsey)."""
    if value is None or value is False or value == []:
        return []
    if not isinstance(value, list):
        raise SystemExit(f"Feature contract field {field_name!r} must be a list")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise SystemExit(
                f"Feature contract field {field_name!r} must contain only strings"
            )
        items.append(item)
    return items


def _display_str(value: object, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return int(default)


def _as_str_list_field(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file without overriding existing env vars."""
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _max_attempts_from_env() -> int:
    raw = os.environ.get("MAX_AGENT_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(
            f"MAX_AGENT_ATTEMPTS must be an integer, got {raw!r}"
        ) from exc
    if value < 1:
        raise SystemExit("MAX_AGENT_ATTEMPTS must be at least 1")
    return value


def _model_identity() -> tuple[str, str]:
    """Return (provider, model) identifiers without exposing secrets."""
    provider = os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER).strip() or DEFAULT_PROVIDER
    model = os.environ.get("LLM_MODEL", "").strip() or "(unspecified)"
    return provider, model


def _load_feature_contract(contract_path: Path) -> dict[str, object]:
    if not contract_path.is_file():
        raise SystemExit(f"Feature contract not found: {contract_path}")
    loaded = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    narrowed = _mapping_str_keys(loaded)
    if narrowed is None:
        raise SystemExit(f"Feature contract must be a mapping: {contract_path}")
    return narrowed


def _print_dry_run(
    contract: Mapping[str, object],
    *,
    max_attempts: int,
    out: TextIO,
) -> None:
    task_id = _display_str(contract.get("task_id", ""))
    title = _display_str(contract.get("title", ""))
    goal = _display_str(contract.get("goal", "")).strip()
    allowed = _string_list(
        contract.get("allowed_write_paths"),
        field_name="allowed_write_paths",
    )
    protected = _string_list(
        contract.get("protected_paths"),
        field_name="protected_paths",
    )
    risk = _mapping_str_keys(contract.get("risk")) or {}

    out.write("=== Dry run (no API key, no LLM, no file modifications) ===\n")
    out.write(f"Task ID: {task_id}\n")
    out.write(f"Title: {title}\n")
    out.write(f"Goal: {goal}\n")
    out.write("\nAllowed write paths:\n")
    out.writelines(f"  - {path}\n" for path in allowed)
    out.write("\nProtected paths:\n")
    out.writelines(f"  - {path}\n" for path in protected)

    out.write("\nVerification checks:\n")
    out.writelines(f"  - {name}: {' '.join(command)}\n" for name, command in _configured_checks())

    out.write("\nRisk declaration:\n")
    out.write(f"  level: {_display_str(risk.get('level'), default='(missing)')}\n")
    out.write(
        "  requires_human_review: "
        f"{_display_str(risk.get('requires_human_review'), default='(missing)')}\n"
    )
    reasons = risk.get("reasons") or []
    if isinstance(reasons, list):
        out.writelines(
            f"  - {reason}\n" for reason in reasons if isinstance(reason, str)
        )

    out.write(f"\nRetry limit (max attempts): {max_attempts}\n")
    out.write("Auto-merge: disabled\n")
    out.write("Auto-deploy: disabled\n")
    out.write("Audit output: not created for --dry-run\n")


def _final_decision_from_state(final_state: dict[str, object]) -> str:
    status = str(final_state.get("status") or "")
    if status in {"candidate_ready", "human_review_required", "human_escalation"}:
        return status
    return status or "unknown"


@dataclass
class _AttemptScratch:
    attempt: int = 0
    changed_files: list[str] = field(default_factory=list)


def _run_graph_with_reporting(
    *,
    workspace: Workspace,
    primary_repo_root: Path,
    max_attempts: int,
    model_client: ModelClient,
    out: TextIO,
    recorder: AuditRecorder | None = None,
) -> dict[str, object]:
    scratch = _AttemptScratch()
    audit_errors: list[str] = []

    def verification_runner(workspace_path: Path) -> VerificationResult:
        # Always run real verification first. Audit must not alter this result.
        result = run_verification(workspace_path)
        if recorder is not None and scratch.attempt > 0:
            try:
                recorder.record_verification(scratch.attempt, result)
                recorder.record_attempt(
                    attempt=scratch.attempt,
                    changed_files=list(scratch.changed_files),
                    verification_passed=result.passed,
                )
            except AuditError as exc:
                audit_errors.append(str(exc))
                out.write(f"Audit recording failed: {exc}\n")
        return result

    graph = cast(
        _CompiledAgentGraph,
        build_agent_graph(
            primary_repo_root=primary_repo_root,
            model_client=model_client,
            verification_runner=verification_runner,
        ),
    )
    state = initial_agent_state(
        workspace_path=workspace.path,
        max_attempts=max_attempts,
        base_commit=workspace.base_ref,
        task_id=workspace.task_id,
    )

    final_state: dict[str, object] = dict(state)

    out.write("\n=== Bounded agent run ===\n")
    if recorder is not None:
        out.write(f"Run ID: {recorder.run_id}\n")
        out.write(f"Audit directory: {recorder.run_dir}\n")
    out.write(f"Workspace: {workspace.path}\n")
    out.write(f"Branch: {workspace.branch_name}\n")
    out.write(f"Base commit: {workspace.base_ref}\n")
    out.write(f"Max attempts: {max_attempts}\n")
    out.write("Auto-merge: disabled\n")
    out.write("Auto-deploy: disabled\n\n")

    for raw_update in graph.stream(state, stream_mode="updates"):
        update = _mapping_str_keys(raw_update)
        if update is None:
            continue
        if "build_context" in update:
            payload = _mapping_str_keys(update["build_context"]) or {}
            scratch.attempt = _as_int(payload.get("attempt", scratch.attempt), default=scratch.attempt)
            scratch.changed_files = []
            out.write(f"[attempt {scratch.attempt}] context assembled\n")
        if "implement" in update:
            payload = _mapping_str_keys(update["implement"]) or {}
            changed = _as_str_list_field(payload.get("changed_files"))
            scratch.changed_files = changed
            out.write(
                f"[attempt {scratch.attempt}] implemented files: "
                f"{', '.join(changed) if changed else '(none)'}\n"
            )
        if "verify" in update:
            payload = _mapping_str_keys(update["verify"]) or {}
            passed = bool(payload.get("verification_passed"))
            status = "PASSED" if passed else "FAILED"
            out.write(f"[attempt {scratch.attempt}] verification: {status}\n")
            if not passed:
                failures = _as_str_list_field(payload.get("previous_failures"))
                out.writelines(f"  - {failure}\n" for failure in failures)
        if "risk_gate" in update:
            payload = _mapping_str_keys(update["risk_gate"]) or {}
            out.write(
                "[risk gate] "
                f"level={payload.get('risk_level')} "
                f"requires_human_review={payload.get('requires_human_review')} "
                f"decision={payload.get('status')}\n"
            )
        if "escalate" in update:
            out.write(
                f"[attempt {scratch.attempt}] attempts exhausted -> human escalation\n"
            )

        for node_update in update.values():
            nested = _mapping_str_keys(node_update)
            if nested is not None:
                final_state.update(nested)

    final_decision = _final_decision_from_state(final_state)
    if recorder is not None:
        try:
            recorder.complete(
                verification_passed=bool(final_state.get("verification_passed")),
                risk_level=str(final_state.get("risk_level") or ""),
                final_decision=final_decision,
            )
        except AuditError as exc:
            audit_errors.append(str(exc))
            out.write(f"Audit recording failed: {exc}\n")

    out.write("\n=== Final result ===\n")
    if recorder is not None:
        out.write(f"Run ID: {recorder.run_id}\n")
        out.write(f"Audit directory: {recorder.run_dir}\n")
    out.write(f"Generated branch: {workspace.branch_name}\n")
    out.write(f"Workspace path: {workspace.path}\n")
    out.write(f"Attempts used: {final_state.get('attempt')}/{max_attempts}\n")
    out.write(f"Verification passed: {final_state.get('verification_passed')}\n")
    out.write(f"Status: {final_state.get('status')}\n")
    out.write(f"Risk level: {final_state.get('risk_level') or '(n/a)'}\n")
    out.write(
        "Requires human review: "
        f"{final_state.get('requires_human_review')}\n"
    )
    out.write("Final risk decision: ")
    status = str(final_state.get("status") or "")
    if status in {"candidate_ready", "human_review_required"}:
        out.write(f"{status}\n")
    elif status == "human_escalation":
        out.write("human_escalation (verification did not converge)\n")
    else:
        decision = evaluate_risk_gate(
            workspace.path,
            verification_passed=bool(final_state.get("verification_passed")),
        )
        out.write(f"{decision.decision}\n")
    out.write("Auto-merge: not performed\n")
    out.write("Auto-deploy: not performed\n")
    if audit_errors:
        out.write(
            "Audit warnings: "
            f"{len(audit_errors)} audit write failure(s) reported above "
            "(verification outcome unchanged)\n"
        )
    return final_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent.run",
        description=(
            "Run the bounded agent development loop against a feature contract."
        ),
    )
    parser.add_argument(
        "contract",
        type=Path,
        help="Path to contracts/feature.yaml (or another feature contract)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without API keys, LLM calls, or file modifications",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO | None = None,
    model_client: ModelClient | None = None,
    workspace_factory=create_workspace,
    runs_root: Path | None = None,
    run_id: str | None = None,
) -> int:
    """
    CLI entry point.

    model_client, workspace_factory, runs_root and run_id are injectable for tests.
    """
    output = out or sys.stdout
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = get_repository_root()
    _load_dotenv(repo_root / ".env")

    contract_path = Path(args.contract)
    if not contract_path.is_absolute():
        contract_path = (Path.cwd() / contract_path).resolve()

    contract = _load_feature_contract(contract_path)
    max_attempts = _max_attempts_from_env()

    if args.dry_run:
        _print_dry_run(contract, max_attempts=max_attempts, out=output)
        return 0

    try:
        load_model_credentials()
    except ModelConfigError as exc:
        output.write(f"Configuration error: {exc}\n")
        return 2

    task_id = contract.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        output.write("Feature contract must declare a non-empty task_id\n")
        return 2

    workspace = workspace_factory(
        task_id.strip(),
        repo_root=repo_root,
    )

    provider, model = _model_identity()
    audit_root = (runs_root or (repo_root / ".agent-runs")).resolve()
    candidate_recorder = AuditRecorder(
        runs_root=audit_root,
        run_id=run_id or new_run_id(),
    )
    recorder: AuditRecorder | None
    try:
        candidate_recorder.start(
            task_id=task_id.strip(),
            base_commit=workspace.base_ref,
            generated_branch=workspace.branch_name,
            model=model,
            provider=provider,
            max_attempts=max_attempts,
        )
        recorder = candidate_recorder
    except AuditError as exc:
        output.write(f"Audit recording failed: {exc}\n")
        output.write(
            "Continuing run without persistent audit "
            "(verification outcome will remain authoritative)\n"
        )
        recorder = None

    client = model_client or AnthropicModelClient()
    _run_graph_with_reporting(
        workspace=workspace,
        primary_repo_root=repo_root,
        max_attempts=max_attempts,
        model_client=client,
        out=output,
        recorder=recorder,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
