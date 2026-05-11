# sm-tool — Iter 2 Sprint 1 Plan

Source: `iter2/Stories_v1.md` (16 stories total, sequenced)
Cut position: **16** — single sprint, all 16 stories. Story 16 is
operator-gated (live API smoke) and runs after Stories 1-15 ship.

---

## Sprint 1 — In Scope (Stories 1-16)

All of Iter 2 in one sprint. Iter 2's scope is narrower than Iter 1
(15 reqs vs 17, 16 stories vs 23) and the foundation + real-agent
clusters are tightly coupled — splitting would just add ceremony.

| # | Title | Size | Rolls up to req# |
|---|---|---|---|
| 1 | Anthropic SDK runtime dependency declared | S | 5 |
| 2 | API key resolution with actionable error | S | 6, 9 |
| 3 | Model and max_tokens resolution with precedence | M | 7 |
| 4 | JSON ask-and-parse helper with typed parse errors | M | 1, 4, 9 |
| 5 | Provider seam — single Anthropic SDK invocation point | M | 8 |
| 6 | Real `spawn_agent` default in `decompose` | **L** | 1, 9 |
| 7 | Real `spawn_test_writer` default in `execute` | M | 2, 9 |
| 8 | Real `spawn_coder` default in `execute` | M | 3, 9 |
| 9 | Real `spawn_reviewer` default in `execute` | **L** | 4, 9 |
| 10 | Retro polish — dead-code cleanup | S | 10, 9 |
| 11 | Retro polish — log-replay consolidation + state enrichment | M | 11 |
| 12 | Retro polish — `SM_LOG_PATH` rename to `SM_TEST_LOG_PATH` | S | 12 |
| 13 | Retro polish — `_HELP_TEXT` refresh for all 12 subcommands and exit codes 0-12 | S | 12, 9 |
| 14 | Retro polish — tightened structural test + honest `build_entry` docstring | S | 13 |
| 15 | Test suite stays 100% green with mocked SDK | M | 14 |
| 16 | Cardiff end-to-end smoke run against real Anthropic API | M | 15 |

**Sprint 1 totals:** 16 stories — 7 S, 7 M, 2 L.

---

## Sequencing — the work order

**Foundation cluster (Stories 1-5), serial:**
SDK dep → API-key resolver → model/max_tokens resolver → JSON parser
→ provider seam. Stories 2 and 3 can run in parallel after Story 1;
Stories 4 and 5 depend on what came before. Foundation must close
before any real-agent story can land.

**Real-agent cluster (Stories 6-9), parallel:**
All four spawn defaults can run in parallel work units once Story 5
ships. Stories 6 and 9 are the L linchpins (structured-output paths
with parse + shape validation); Stories 7 and 8 are M (string-return
only). Reviewing the four together catches drift since they're
mechanically symmetric.

**Retro polish cluster (Stories 10, 11, 12, 14), parallelizable with foundation:**
These four have no dependencies on real-agent work and can land
anywhere in the sprint. Story 11 (log-replay consolidation) is M;
the other three are S mechanical fixes. Story 13 sits after Story 9
because the help-text refresh needs `EXIT_AGENT_ERROR = 12` wired.

**Validation cluster (Stories 15, 16), gate:**
Story 15 (mocked-SDK suite 100% green) gates Story 16 (live
operator-driven smoke). Story 16 is the Iter 2 release gate — no
sign-off without it. Both depend on all four real-agent defaults
(6-9). Story 16 is operator-driven, not automated.

---

## Why single sprint

1. **Scope narrower than Iter 1.** 15 reqs vs 17, 16 stories vs 23. Less ceremony earned by the scope.
2. **Tight cluster coupling.** Foundation (1-5) and real-agent (6-9) are too tightly bound to split — Story 6 depends on Stories 4 and 5; Stories 7, 8, 9 all depend on Story 5. A foundation-only sprint would ship nothing usable.
3. **Retro polish is mechanical.** Stories 10-14 are atomic fixes — no decomposition value in splitting them across sprints.
4. **Validation is the gate.** Story 16 is operator-driven, not automated. Splitting it into Sprint 2 just to mirror Iter 1's two-sprint structure would be ceremony for ceremony's sake.

If we hit unexpected scope explosion mid-sprint, we re-cut. Iter 1's sprint-cut lock rule (Story 12) was deliberately designed for this — if any story has left `planned`, the cut locks. So we can pre-cut at 16, and if scope blows up after some stories move, we close Sprint 1 where it lies and cut Sprint 2 for the remainder.

---

## Per-story pipeline

Each story routes through the same agent flow used in Iter 1 Sprint 1:

  Test Writer → Coder → Reviewer

Same brief format as Iter 1. Same `role_spec/` files. Same Python 3.10+
constraints. Same shipping standard: 100% test pass per story before
closing.

**Story 16 is the exception.** It is operator-driven, not agent-driven.
The smoke run requires:
- `ANTHROPIC_API_KEY` exported in the Architect's environment
- A real active iteration (PO Tool handoff, or hand-crafted iteration_open
  entry for testing)
- Operator executes `python -m sm decompose` and `python -m sm execute
  <story_id>`
- Observed behavior captured in `iter2/Smoke_Run.md`

Story 16 closes when the operator confirms both commands ran successfully
end-to-end and produced expected outputs.

---

## Ship target

**Iter 2 ships when all 16 stories close.** Tag: `v0.4.0`. Iter 2 retro
follows Iter 1's pattern (Iter_2_Retro.md). Both released as a single
ship event — no intermediate tag for partial completion.

The Cardiff narrative line "Iter 2 is wiring real agent integration"
becomes literally true the moment `v0.4.0` lands.
