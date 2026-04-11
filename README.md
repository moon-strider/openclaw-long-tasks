# openclaw-long-tasks

Durable long-task orchestration prototype for OpenClaw.

## What is implemented

Code lives in `src/long_tasks/`:

- `config.py` - runtime settings and paths
- `models.py` - task/step/attempt models and transition guards
- `storage.py` - SQLite schema, persistence, leases, events, attempts
- `runtime.py` - worker, verifier, retry/backoff, waiting/blocking/completion flow, scheduler
- `cli.py` - basic inspection and control CLI for the prototype runtime
- `utils.py` - ids, timestamps, stable JSON helpers

## Repository layout

- `src/long_tasks/` - implementation code
- `tests/` - unit/integration-style prototype tests
- `tests_e2e/` - runtime-level e2e tests for this repo's durable kernel
- `scripts/` - helper scripts for local runtime validation
- `skill/long-running-tasks/` - reusable operating skill for long-running-task workflows

## Important distinction

This repo contains a self-contained durable runtime implemented in Python.
It does not depend on OpenClaw built-in detached task ledger or `openclaw tasks` as part of its core execution model.
These long-running tasks are NOT related to `openclaw tasks` in architecture, execution, storage, inspection, or operator workflow.
If you are operating this repo, do not use `openclaw tasks` as the mental model or control surface unless you are explicitly debugging OpenClaw itself rather than this repo.

OpenClaw is used only as a thin transport layer for:

- launching agent runners
- sending user notifications

The runtime itself owns:

- durable task state
- step progression
- retries and backoff
- worker leasing
- recovery after restart
- user-target notification metadata

Default execution mode for multi-step tasks is sequential.
Unless explicitly specified otherwise, steps should run one after another, not in parallel.
This is the safe default while multi-agent parallel orchestration is still not reliable.

## Current commands

Install and expose this tool as a normal system CLI in regular use. Do not rely on manual `.venv` activation for everyday operation. After repo updates, reinstall/refresh the system CLI so the installed command matches the current checkout.

For repository operations, do not assume plain local git identity is already configured just because the checkout exists. Prefer the GitHub-authenticated `gh` workflow/tooling path when commit, push, or PR work is needed, and make sure you are operating inside this repo rather than the parent workspace.

If dependencies are installed:

- `openclaw-long-tasks tasks list`
- `openclaw-long-tasks tasks show <id>`
- `openclaw-long-tasks tasks create --title ... --goal ... --notify-chat-id ... --steps-json ... [--reply-message-id ...]`
- `openclaw-long-tasks tasks resume <id>`
- `openclaw-long-tasks tasks resume-latest-for-chat --chat-id ... --reply-text ...`
- `openclaw-long-tasks tasks cancel <id>`
- `openclaw-long-tasks tasks tick --agent-id main --responder-agent-id main`
- `openclaw-long-tasks service run --agent-id main --responder-agent-id main`

## Goal

Use this repo to define and test a production-oriented long-task kernel with:

- SQLite durability
- explicit state machine
- lease-based execution
- deterministic verification
- retry and recovery behavior
- user-visible LLM-authored summaries on meaningful passes
- full final Telegram report when it fits in one message, otherwise compressed final report plus offer to send relevant files
- reply-linked Telegram delivery
- outbound Telegram file/image sending for relevant artifacts
- durable retryable notification diagnostics

## Non-goal

This repo is not a thin wrapper around OpenClaw's existing background-task machinery.
Its long-running task behavior remains self-contained and testable on its own, while using OpenClaw only for runner launch and outward message delivery.