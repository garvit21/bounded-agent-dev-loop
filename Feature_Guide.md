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

Option 1 — Modify the Existing Inventory Demo
The simplest way to experiment with the repository is to change the existing
feature.

###Step 1 — Update the requirement
Edit: