# sm-tool — Sprint 1 Plan

Source: `iter1/Stories_v1.md` (23 stories total, sequenced)
Cut position: **12** — Stories 1-12 in Sprint 1; Stories 13-23 deferred to Sprint 2.

---

## Sprint 1 — In Scope (Stories 1-12)

Foundation + ingestion + decomposition + sprint-cut commands. The
goal is a working core: a tool that can ingest a PO handoff, run SM
decomposition, and execute a sprint cut. Lifecycle / status / close
follow in Sprint 2 once the core is solid.

| # | Title | Size | Rolls up to req# |
|---|---|---|---|
| 1 | Append-only JSONL log writer | M | 2, 17 |
| 2 | JSONL log reader and replay scanner | M | 2, 17 |
| 3 | Content-oriented entry builder | **L** | 3 |
| 4 | State derivation by log replay | **L** | 2, 8 |
| 5 | Iteration ingestion command — happy path | **L** | 1, 3 |
| 6 | Ingestion validation — malformed and duplicate handoffs | M | 1 |
| 7 | Single-active-iteration enforcement | S | 1, 4 |
| 8 | Role-spec file resolution and recording | M | 12 |
| 9 | SM Agent invocation (sync, role-spec driven) | **L** | 5, 12 |
| 10 | Story backlog ingestion from SM Agent output | M | 5 |
| 11 | Sprint cut command (with re-cut rules) | M | 6 |
| 12 | Sprint cut validation and supersede logic | S | 6 |

**Sprint 1 totals:** 12 stories — 2 S, 7 M, 3 L.

## Sprint 2 — Deferred (Stories 13-23)

| # | Title | Size |
|---|---|---|
| 13 | Story lifecycle state machine | **L** |
| 14 | Per-story state transition commands | M |
| 15 | Test-pass gate enforcement | M |
| 16 | Status query command | M |
| 17 | Close handoff JSON shape and writer | M |
| 18 | Iteration close command (full path) | **L** |
| 19 | Force-close command with reason | M |
| 20 | Close-and-flow cleanup | S |
| 21 | Single-user / no-auth posture | S |
| 22 | JSONL-only persistence audit | S |
| 23 | Per-story execution pipeline (NICE) | **L** |

**Sprint 2 totals:** 11 stories — 4 S, 4 M, 3 L.

---

## Sequencing rationale

1. **Stories 1-4 are pure foundation** — log writer, reader,
   entry builder, replay. Every Sprint 1 feature depends on at
   least one of these. Foundation-first matches the suite's
   established build pattern.
2. **Stories 5-7 are ingestion** — the front door of every
   cycle. Until ingestion works, decomposition has no input.
3. **Stories 8-10 are decomposition** — role-spec resolution +
   SM Agent invocation + backlog ingestion. Stories 8 and 9
   are paired tightly: 8 establishes how role specs are
   referenced and recorded; 9 invokes an agent using one.
4. **Stories 11-12 are sprint cut** — the boundary of Sprint 1.
   Cutting commands are simple in the absence of lifecycle
   transitions, so they fit cleanly here.

## Why cut at 12

Sprint 1 ends with a tool that can ingest, decompose, and cut. That's
a working pipeline-front-half — usable for testing the integration
with the rest of the dev suite. Adding lifecycle/close to Sprint 1
would make the sprint unwieldy (3 Ls is plenty of linchpin work).
Sprint 2 picks up the back half (lifecycle, status, close, force,
execution pipeline) on a stable foundation.

## Per-story pipeline

Each Sprint 1 story routes through the same three-agent flow used
to build `po-tool`:

  Test Writer → Coder → Reviewer

- **Test Writer** drafts 100+ acceptance tests against the story's
  technical criteria.
- **Coder** implements to pass the tests, no scope creep.
- **Reviewer** verifies coverage, contract, and quality. Reviewer
  push-back is a normal quality signal — fix and re-review.

Each story closes when Reviewer signs off. Close-and-flow per story.
