from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from agent.context_builder import ContextBundle


class ModelConfigError(RuntimeError):
    """Raised when required model configuration is missing."""


class ModelInvocationError(RuntimeError):
    """Raised when the model call fails or returns unusable output."""


@dataclass(frozen=True)
class ProposedFileChange:
    path: str
    content: str


@dataclass(frozen=True)
class ModelProposal:
    summary: str
    files: tuple[ProposedFileChange, ...]


def load_model_credentials() -> tuple[str, str]:
    """
    Read ANTHROPIC_API_KEY and LLM_MODEL from the environment.

    Credentials are never hardcoded. Missing values fail clearly at
    real invocation time.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()

    missing: list[str] = []
    if not api_key:
        missing.append("ANTHROPIC_API_KEY")
    if not model:
        missing.append("LLM_MODEL")

    if missing:
        raise ModelConfigError(
            "Missing required model configuration: "
            + ", ".join(missing)
            + ". Set them in the environment (see .env.example)."
        )

    return api_key, model


def _build_user_prompt(
    context: ContextBundle,
    previous_failures: Sequence[str],
) -> str:
    failures = previous_failures or context.previous_failures
    source_blocks = "\n\n".join(
        f"### {path}\n```\n{content}\n```" for path, content in context.source_files.items()
    )
    failure_block = (
        "\n".join(f"- {item}" for item in failures) if failures else "- (none)"
    )

    return (
        f"Task ID: {context.task_id}\n"
        f"Goal: {context.goal}\n\n"
        "Allowed write paths (ONLY these may be changed):\n"
        + "\n".join(f"- {path}" for path in context.allowed_write_paths)
        + "\n\nProtected paths (must not be modified):\n"
        + "\n".join(f"- {path}" for path in context.protected_paths)
        + "\n\nAcceptance criteria:\n"
        + "\n".join(f"- {item}" for item in context.acceptance_criteria)
        + "\n\nFeature contract:\n"
        f"{context.contract_text}\n\n"
        "OpenAPI contract:\n"
        f"{context.openapi_text}\n\n"
        "Engineering rules:\n"
        f"{context.engineering_rules}\n\n"
        "Current source files:\n"
        f"{source_blocks}\n\n"
        "Previous verification failures:\n"
        f"{failure_block}\n\n"
        "Respond with a single JSON object only, matching:\n"
        '{\n  "summary": "...",\n  "files": [\n'
        '    {"path": "app/main.py", "content": "..."}\n  ]\n}\n'
    )


_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\{.*\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


def parse_model_response(raw_text: str) -> ModelProposal:
    """Parse and validate structured JSON describing proposed file changes."""
    text = raw_text.strip()
    if not text:
        raise ModelInvocationError("Model returned an empty response")

    candidate = text
    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        candidate = fence_match.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ModelInvocationError("Model response did not contain a JSON object")
        candidate = text[start : end + 1]

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ModelInvocationError(
            f"Model response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ModelInvocationError("Model JSON root must be an object")

    summary = payload.get("summary")
    files = payload.get("files")
    if not isinstance(summary, str):
        raise ModelInvocationError('Model JSON must include string field "summary"')
    if not isinstance(files, list) or not files:
        raise ModelInvocationError('Model JSON must include non-empty list "files"')

    parsed_files: list[ProposedFileChange] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ModelInvocationError(f"files[{index}] must be an object")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not path.strip():
            raise ModelInvocationError(f"files[{index}].path must be a non-empty string")
        if not isinstance(content, str):
            raise ModelInvocationError(f"files[{index}].content must be a string")
        parsed_files.append(ProposedFileChange(path=path.strip(), content=content))

    return ModelProposal(summary=summary, files=tuple(parsed_files))


class ModelClient(Protocol):
    def propose(
        self,
        *,
        system_instructions: str,
        context: ContextBundle,
        previous_failures: Sequence[str],
    ) -> ModelProposal: ...


class AnthropicModelClient:
    """Thin Anthropic Messages API adapter. Credentials come from the environment."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 120.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    def propose(
        self,
        *,
        system_instructions: str,
        context: ContextBundle,
        previous_failures: Sequence[str] = (),
    ) -> ModelProposal:
        api_key = self._api_key
        model = self._model
        if not api_key or not model:
            env_key, env_model = load_model_credentials()
            api_key = api_key or env_key
            model = model or env_model

        user_prompt = _build_user_prompt(context, previous_failures)
        request_body = {
            "model": model,
            "max_tokens": 8192,
            "system": system_instructions,
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
        }

        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(timeout=self._timeout_seconds)
        try:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=request_body,
            )
        except httpx.TimeoutException as exc:
            raise ModelInvocationError(
                f"Model request timed out after {self._timeout_seconds} seconds"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelInvocationError(f"Model request failed: {exc}") from exc
        finally:
            if owns_client:
                client.close()

        if response.status_code >= 400:
            raise ModelInvocationError(
                f"Model API returned HTTP {response.status_code}: {response.text}"
            )

        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ModelInvocationError("Model API returned non-JSON body") from exc

        content_blocks = body.get("content")
        if not isinstance(content_blocks, list) or not content_blocks:
            raise ModelInvocationError("Model API response missing content blocks")

        text_parts: list[str] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)

        if not text_parts:
            raise ModelInvocationError("Model API response contained no text content")

        return parse_model_response("\n".join(text_parts))


def propose_changes(
    *,
    system_instructions: str,
    context: ContextBundle,
    previous_failures: Sequence[str] = (),
    client: ModelClient | None = None,
) -> ModelProposal:
    """
    Request structured file-change proposals from the configured model.
    """
    active_client = client or AnthropicModelClient()
    return active_client.propose(
        system_instructions=system_instructions,
        context=context,
        previous_failures=previous_failures,
    )
