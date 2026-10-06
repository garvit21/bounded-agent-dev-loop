# Bounded Agent Development Loop

A small reference implementation exploring how a coding agent can take a human-defined software requirement, work inside an isolated Git worktree, verify its own output against deterministic checks, retry within bounded limits, and escalate to a human when judgement is required.

This is intentionally a small experiment rather than a complete autonomous software agent factory.

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

```


## Demo Requirement

This repository includes one deliberately small feature task so the complete
development loop can be exercised end to end.

The baseline FastAPI application currently exposes:

```text
GET /health
```

The demonstration asks the coding agent to implement:

```text

POST /inventory/reservations
```

The inventory reservation endpoint is intentionally not implemented on the main branch.
Instead, the expected behaviour is defined independently through:

```text
contracts/feature.yaml
contracts/openapi.json
tests/test_feature.py

```
The purpose of the agent run is to create the implementation inside an
isolated Git worktree and make the acceptance tests pass without modifying
the tests or contracts.

Quick Start
Requirements
- Python 3.12+
- Git
Create the environment:
```text
python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```
Inspect the Workflow Without an API Key
No LLM credentials are required for dry-run mode:
```text
python -m agent.run contracts/feature.yaml --dry-run
```
his displays:
- requested outcome
- permitted write paths
- protected repository areas
- verification checks
- retry limit
- risk classification
No model is called and no source files are changed.

Run With an LLM
Copy the example environment file:
```text
cp .env.example .env
```
Configure the provider values:
```text
ANTHROPIC_API_KEY=
LLM_MODEL=
```
Then run:
```text
python -m agent.run contracts/feature.yaml
```
A real run:
1. creates an isolated Git worktree;
2. performs initial verification;
3. builds scoped model context;
4. asks the model to implement the requirement;
5. permits changes only to approved application files;
6. runs deterministic verification;
7. feeds failures back for another bounded attempt;
8. stops after the configured retry limit;
9. applies the risk gate;
10. leaves the generated implementation on a separate Git branch.
The harness never automatically merges or deploys generated code.
Verification
The current verification layer includes:

```text
pytest baseline tests
pytest feature acceptance tests
Ruff
mypy
```

```text
```

```text
```