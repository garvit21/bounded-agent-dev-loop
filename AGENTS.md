# Bounded Agent Development Loop

## Purpose

This repository is a small reference implementation showing how a coding
agent can safely progress a software change with progressively less human
intervention.

It is NOT intended to be a fully autonomous software factory.

The repository demonstrates five principles:

1. Scoped context
2. Isolated agent execution
3. Independent deterministic verification
4. Bounded self-correction
5. Risk-based human escalation

## Architecture

A task follows this lifecycle:

Requirement
→ Context Builder
→ Workspace Manager
→ Isolated Git Worktree
→ Implementation Agent
→ Deterministic Verification
→ Retry or Risk Gate
→ Candidate for Human Review

## Source of Truth

`contracts/feature.yaml` defines the requested outcome.

`contracts/openapi.json` defines the expected API contract.

Contracts and acceptance criteria are authoritative.
Implementation code must conform to them.

## Agent Boundaries

The implementation agent may modify files only inside:

- app/

The implementation agent MUST NOT modify:

- contracts/
- tests/
- verification/
- workspace/
- .github/
- AGENTS.md
- .cursor/

An agent must never modify its own acceptance criteria, verification logic, execution boundaries, or tests in order to make itself pass.

## Worktree Safety

Agent-generated code must never be written directly into the developer's active branch.

Every implementation attempt must execute inside a short-lived Git worktree created from a known commit.

## Verification

Agent output is not considered correct because the model says it is correct.

Verification should use deterministic evidence such as:

- pytest
- API contract validation
- schema validation
- Ruff
- mypy

## Retry Behaviour

Failed verification may be returned to the implementation agent as
structured feedback.

Retries must be bounded.

The default maximum number of correction attempts is 3.

If the agent cannot converge, execution must stop and escalate to a human.

## Human Review

Changes involving higher-risk areas should require human review even if
automated checks pass.

Examples include:

- authentication
- authorization
- database migrations
- security controls
- secrets
- destructive operations
- material business rules

## Engineering Principles

Prefer deterministic code when deterministic code can solve the problem.

Use agents when interpretation, investigation, planning or generation
provides meaningful value.

Do not give agents unrestricted repository, shell, network or production
access.

Keep the implementation small, readable and defensible in a technical
interview.