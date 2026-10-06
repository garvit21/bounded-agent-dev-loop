# Bounded Agent Development Loop

A small reference implementation exploring how a coding agent can take a
human-defined software requirement, work inside an isolated Git worktree,
verify its own output against deterministic checks, retry within bounded
limits, and escalate to a human when judgement is required.

This is intentionally a small experiment rather than a complete autonomous
software factory.

## What It Demonstrates

The project separates agent autonomy from the controls required to trust it:

- Human-authored feature contracts and acceptance criteria
- Scoped repository context
- Isolated Git worktree execution
- Restricted file-write permissions
- Deterministic verification using tests, Ruff and mypy
- Bounded self-correction
- Risk-based human review
- Audit evidence for agent runs

The implementation agent cannot modify its own contracts, tests or
verification logic.

## Development Flow

```text
Feature Contract
      ↓
Scoped Context
      ↓
Isolated Git Worktree
      ↓
Implementation Agent
      ↓
Deterministic Verification
      ↓
   Pass?
   /   \
 No     Yes
 ↓       ↓
Retry   Risk Gate
(max 3)  ↓
 ↓      Candidate / Human Review
Human
Escalation

text```


## Demo Requirement

This repository includes one deliberately small feature task so the complete
development loop can be exercised end to end.

The baseline FastAPI application currently exposes:


GET /health

The requested feature is :

POST /inventory/reservations

""The inventory reservation endpoint is intentionally not implemented on the
main branch.
Instead, the expected behaviour is defined independently through:""

contracts/feature.yaml
contracts/openapi.json
tests/test_feature.py
