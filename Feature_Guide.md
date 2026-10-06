# Creating or Changing a Feature

The harness is designed so that the requested software outcome is defined
outside the implementation agent.

A feature is represented by three independent pieces:

```text
Requirement        → contracts/feature.yaml
API contract       → contracts/openapi.json
Acceptance tests   → tests/test_feature.py
```
Currently The implementation agent is not allowed to modify any of these.

## Option 1 — Modify the Existing Inventory Demo
The simplest way to experiment with the repository is to change the existing
feature.

### Step 1 — Update the requirement
Edit:

```text
contracts/feature.yaml
```
Define:
- task ID
- goal
- allowed write paths
- protected paths
- endpoint behaviour
- acceptance criteria
- risk level
Example:

```text
task_id: inventory-cancellation

title: Add inventory reservation cancellation

goal: >
  Allow an existing reservation to be cancelled.

allowed_write_paths:
  - app/main.py
  - app/models.py

protected_paths:
  - contracts/
  - tests/
  - verification/
  - workspace/
  - agent/
  - .github/
  - AGENTS.md
  - .cursor/

acceptance_criteria:
  - A valid reservation can be cancelled.
  - Successful cancellation returns HTTP 200.
  - An unknown reservation returns HTTP 404.
  - Existing health behaviour remains unchanged.

risk:
  level: medium
  requires_human_review: true
  reasons:
    - Modifies inventory state.
```
### Step 2 — Update the API Contract

Edit:
```text
contracts/openapi.json
```
Describe the expected endpoint independently from the application code.
For example:

```text
DELETE /inventory/reservations/{reservation_id}
```
The contract should describe:
- HTTP method
- path
- required parameters
- request body where applicable
- response codes
- expected response structure
Do not generate this contract from the implementation being tested.
It represents the expected behaviour.

### Step 3 — Update the Acceptance Tests

Edit:
```text
tests/test_feature.py
```

Write tests that express the outcome rather than prescribing the
implementation.
For example:

```text
def test_cancel_existing_reservation() -> None:
    ...
```
and:
```text
def test_unknown_reservation_returns_404() -> None:
    ...
```

The feature tests should normally fail before the new feature is implemented.
That gives the harness an explicit definition of "done".
Do not modify the application yet.

### Step 4 — Confirm the Baseline Is Healthy

Run:
```text
python -m pytest \
tests/test_baseline.py \
tests/test_workspace_manager.py \
-v
```
These tests should pass.
Then:

```text
python -m pytest tests/test_feature.py -v
```

The new feature tests should normally fail because the feature does not exist
yet.
This gives the intended starting state:

```text
Existing application     PASS
Requested feature        FAIL
```

### Step 5 — Inspect What the Agent Will Receive

Run:
```text
python -m agent.run contracts/feature.yaml --dry-run
```
Check:
- task description
- allowed write paths
- protected paths
- verification commands
- retry limit
- risk classification
Dry-run does not call an LLM or modify the repository.

### Step 6 — Execute the Feature

Configure the LLM environment:
```text
cp .env.example .env
```
Add the provider credentials and model.
Then:
```text
python -m agent.run contracts/feature.yaml
```
The harness will:
```text
Create isolated worktree
        ↓
Build scoped context
        ↓
Ask model for implementation
        ↓
Validate proposed file writes
        ↓
Run deterministic verification
        ↓
Retry with failure evidence if necessary
        ↓
Apply risk gate
        ↓
Return candidate or human-review decision
```

Generated code remains on a separate Git branch.
The harness does not merge or deploy it automatically.

## Important Rules

### Do Not put the Implementation in the Contract

Bad design would look like something
```text
acceptance_criteria:
  - Create a Python dictionary called reservations
  - Add a function named create_reservation()
```

Better follow
```text
acceptance_criteria:
  - A valid reservation returns HTTP 201.
  - Repeated requests using the same idempotency key return the same reservation.
```
Define the outcome, not the implementation.

### Keep Write Access Narrow

Prefer:
```text
allowed_write_paths:
  - app/main.py
  - app/models.py
```

rather than:
```text
allowed_write_paths:
  - .
```
The agent should receive the minimum permissions necessary to complete the
task.

### Keep Verification Independent

The implementation agent must not be allowed to modify:

```text
contracts/
tests/
verification/
workspace/
AGENTS.md
.cursor/
```

as per the current contract, Otherwise it could make its own work appear successful by weakening the
definition of success.

### Use Risk-Based Human Review

Passing tests does not automatically mean a change should be deployed.
Examples:
```text
Documentation change       Low
Simple isolated UI change  Low

Business-rule change       Medium
Inventory state change     Medium

Authentication             High
Authorization              High
Database migration         High
Security controls          High
```

## Adding Multiple Independent Features

The current repository is intentionally optimized around one feature definition:

```text
contracts/feature.yaml
contracts/openapi.json
tests/test_feature.py
```
For experimenting with another feature, the simplest supported approach is to replace those three definitions and rerun the harness. 

If you wanted to support multiple independent features at the same time, the next extension would be to group each feature into its own directory, for example:

```text
contracts/
├── inventory-reservation/
│   ├── feature.yaml
│   └── openapi.json
│
├── reservation-cancellation/
│   ├── feature.yaml
│   └── openapi.json
```

Each feature would then reference its own acceptance-test set, for example:
```text
tests/features/
├── test_inventory_reservation.py
└── test_reservation_cancellation.py
```

The CLI could then accept a feature directory:
```text
python -m agent.run contracts/inventory-reservation/
```
The harness would load the corresponding feature contract, API contract and acceptance tests while keeping the same isolation, verification, retry and risk-gate behaviour.
That extension is intentionally outside the scope of this small reference implementation, which focuses on demonstrating one complete bounded development loop clearly rather than adding feature-management complexity.
