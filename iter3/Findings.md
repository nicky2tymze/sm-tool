# sm-tool Iter 3 v2 — Findings (for Iter 4 sprint planning)

Findings surfaced during Iter 3 v2 that do NOT block this iteration's close.
Each enters Iter 4 sprint planning where it competes on merit.

## Finding 1: Six deferred polish items from Iter 3 v1 pivot

**Source:** Iter 3 v1 → v2 pivot decision (2026-05-11, see `Pivot_Notes.md`).

When Iter 3 pivoted from "6 polish items" to "close autonomy gaps,"
the original 6 candidates were deferred to Iter 4:

1. TestWriter canonical-schema cheat-sheet
2. Shim-detector pre-commit
3. Status output formatting
4. Cost telemetry
5. Rate-limit / retry policy
6. Provider-swap groundwork

The 23-story decomposition for these items is in the iter3_log.jsonl
record of the closed `sm-tool-iter3` iteration (the pre-pivot
iteration). It can be re-ingested OR re-decomposed in Iter 4 planning
depending on whether the scope still makes sense after autonomy lands.

## Finding 2: SM Agent over-decomposes TDD-shaped acceptance criteria

**Source:** Iter 3 v2 Sprint 1 Story 4 (verification-only close).

The SM Agent's decomposition of req-1 produced 4 stories:
1. Add helpers (Story 1) — included tests
2. Integrate into spawn defaults (Story 2) — included tests
3. Token budget guard (Story 3) — included tests
4. **Write mocked-SDK tests for context passing (Story 4) — REDUNDANT**

Story 4's acceptance criteria was entirely pinned by Stories 2 + 3
(every line had a corresponding test in those prior stories' files).
The SM Agent treats "implementation" and "test-writing" as separate
stories by default, but when prior stories are TDD-shaped
(TestWriter agent runs FIRST per role spec), the test-writing story
is redundant.

**Proposed Iter 4 work:** tune `roles/sm_agent.md` to recognize
TDD-shaped acceptance criteria and either skip the redundant story
OR merge it into the prior story's verification.

**Size:** S. Markdown-only edit to the role spec, exercised by the
existing role-spec drift catcher from MiniSprint 2.1.

**Risk of not doing it:** Iter 4 and later will keep producing
verification-only stories that consume orchestrator cycles for zero
new test coverage. Low cost per occurrence but compounds.
