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

The implementation agent cannot modify its own contracts, tests or verification logic. The model does not receive direct shell or repository write access. It returns structured file-change proposals, which the harness validates before applying permitted changes inside the Git worktree.

The Git worktree provides source-control and workspace isolation; it is not
intended to be an operating-system security sandbox.

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

## Quick Start
### Requirements
- Python 3.12+
- Git
Create the environment:

```text
python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```
### Inspect the Workflow Without an API Key
No LLM credentials are required for dry-run mode:
```text
python -m agent.run contracts/feature.yaml --dry-run
```
This displays:
- requested outcome
- permitted write paths
- protected repository areas
- verification checks
- retry limit
- risk classification
No model is called and no source files are changed.

### Run With an LLM
Copy the example environment file:
```text
cp .env.example .env
```
Configure the Antropic provider values: (In case of openai, we will have to change the Model.py also)
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
2. builds scoped model context from the feature contract and repository rules;
3. asks the model to propose the implementation;
4. validates that proposed writes stay within approved paths;
5. applies permitted changes inside the worktree;
6. runs deterministic verification;
7. feeds verification failures back into the next bounded attempt;
8. stops when verification passes or the retry limit is reached;
9. applies the risk gate;
10. leaves the generated implementation on a separate Git branch.

The harness as of now, would not automatically merges or deploys generated code.

### Verification
The current verification layer includes:

```text
pytest baseline tests
pytest feature acceptance tests
Ruff
mypy
```

The model does not decide whether its own implementation is correct.
Verification is performed independently by the harness.

### Testing the Harness
The repository includes unit and integration tests for:
- workspace isolation
- context construction
- deterministic verification
- implementation write boundaries
- risk evaluation
- LangGraph orchestration
- CLI behaviour
- audit recording
- mocked end-to-end execution
The full mocked end-to-end test requires no API key.

### Repository Structure

```text
agent/          Agent state, context, implementation and orchestration
app/            Application code the agent may modify
audit/          Run evidence recording
contracts/      Human-authored requirement and API contract
tests/          Independent acceptance and harness tests
verification/   Deterministic verification
workspace/      Git worktree isolation
.cursor/        Repository-level agent rules
.github/        CI workflow
```
### Design Principle
The central idea is simple:
Increase what the agent can own only when the surrounding context,
permissions, verification and feedback loops make that additional
autonomy trustworthy.

## Important
For instructions on changing the demo requirement like adding a completly new feature or making changes in existing feature without changing the code, see
```text
Feature_Guide.md
```