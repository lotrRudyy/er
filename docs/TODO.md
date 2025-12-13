- [ ] Decide Room2 Chess architecture: single-ESP shared-SPI (W5500 + 4×RC522) vs dual-ESP UART bridge.
  - Gather exact wiring/power/cable lengths + RC522 library used.
  - Evaluate SPI coexistence (CS pullups, decoupling, reset, SPI clocks, transactions).
  - If needed: define UART bridge protocol + failure modes.











Paste-this Codex prompt (repo audit)

Use this as the first Codex message in VS Code:

You are a senior embedded systems architect + systems engineer + technical project lead.
MODE: DESIGN.
SOURCE OF TRUTH: only the checked-out repository files. Do not assume anything not shown in the repo. If info is missing, make the minimal safe assumption, label it clearly, and continue.

Goal: perform a production-grade audit of this escape-room system repo. Focus on reliability, safety, maintainability, observability, deployment, and recoverability.

Repo layout hint: look for er1, er2, er3, and shared (firmware, pi-runtime, docs, scripts).

Process:

Build an inventory: components, languages, targets (MCU vs Pi), entrypoints, deploy scripts, configs, log formats, and shared libraries.

Identify risks and failures: watchdog/reset strategy, power-loss behavior, networking failure modes, time sync, storage wear, SD corruption, stuck relays/actuators, safety interlocks, idempotent reboots, config drift.

Evaluate observability: structured logs, log levels, correlation IDs, remote collection, “last known state”, metrics/health endpoints, crash dumps.

Evaluate deployment/ops: versioning, rollback, migrations, service supervision, startup ordering, secrets handling.

Evaluate coding quality: error handling, timeouts, retries, resource leaks, concurrency hazards, undefined behavior, input validation, test coverage.

Output format (must):

Executive Summary (5–10 bullets)

System Map (diagram in text + key paths/files)

Top 20 Findings with: severity (S0–S3), likelihood, impact, evidence (file paths + snippets), and recommended fix

Prioritized Remediation Plan (1-day / 1-week / 1-month)

Quick Wins (patchable in <30 min each)

Questions / Unknowns (only if truly blocked)

Rules:

Prefer small, reversible changes. No big rewrites.

When proposing code changes, present them as small commits with clear diffs and rationale.

If you suggest adding logging, keep logs structured (JSON), stable keys, and rate-limited.

Start by scanning the repo and building the inventory.

2) Recommended Codex settings (models / temperature / tools)
Model

Pick the most capable reasoning/code model available in your Codex/VS Code picker for the audit + architecture decisions.

If there’s a separate “fast” model, use it only for mechanical refactors after you already know exactly what to change.

Temperature

0.1–0.2 for audit work (keeps it deterministic, less “creative guessing”).

0.0–0.1 when generating patches/diffs you intend to ship.

Tools / permissions

Allow: read repo, ripgrep/search, open files, run unit tests/build (if available).

Disallow (during audit): “auto-apply changes everywhere” / “bulk rewrite”.

Require: “propose diffs first”, then you apply.

Output discipline

Tell it to always cite: file path + line range for every finding.

3) Safe audit workflow (how we run this without breaking prod)
Phase A — Read-only audit (no edits)

Create a branch: audit/<date>-baseline

Inventory + system map

Findings list with severity:

S0 = safety / hardware damage / fire risk / uncontrolled actuator

S1 = can hard-stop the room / frequent ops intervention

S2 = intermittent bugs / degraded experience / hard to debug

S3 = maintainability / cleanup

Phase B — “Quick wins” patch set (tiny commits)

Only fixes that are:

localized

reversible

don’t change gameplay logic

Each fix:

one commit

includes a short test/verification step

adds/extends structured logs where it helps (rate-limited)

Phase C — Hardening plan (bigger work, still controlled)

Introduce (if missing):

a healthcheck concept (per node/service)

a last-known-state snapshot

supervised services on Pi (restart policy + startup ordering)

explicit timeouts/retries/backoff

a consistent log schema + levels across firmware/Pi

Phase D — Release discipline

Tag releases, keep rollback path, and run a “power cut / reboot” test checklist.
