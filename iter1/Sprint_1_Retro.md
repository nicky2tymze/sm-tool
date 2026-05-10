# sm-tool — Sprint 1 Retro

Sprint 1 shipped 12 stories across 942 tests in one focused recorded
session. This retro captures what worked, what slipped, and what
goes into the next sprint's brief library.

---

## Headline numbers

| Metric | Value |
|---|---|
| Stories shipped | 12 of 12 |
| Sizes | 2 S + 6 M + 4 L |
| Tests | 942 across 13 files, 100% green |
| L stories first-pass clean (impl) | 4 of 4 |
| Push-back fires | 1 (Story 1) |
| Cascade fix | 1 (Story 10) |
| Edit-cycle iterations | Story 9 had 3 (test-fixture coordination, not implementation drift) |
| Stories with 0 push-back | 11 of 12 |

## What worked

**Thorough TestWriter briefs + spec-aligned Coder + independent
Reviewer ran cleanly.** Same pattern PO Tool established. Once the
TestWriter pins the contract tightly, the Coder's job becomes a
1:1 mapping — and 4-of-4 L stories landing first-pass clean is the
proof.

**Story-9 derive_state extension was correctly classified.** The
single-line `or "story_backlog"` branch was required by tests and
preserved Story 4's contract — additive forward-compat, not scope
creep. The Reviewer caught it as the right kind of question.

**Story-10 cross-ref check correctly surfaced latent fixture bugs**
in Story 9 tests. Behavior-preserving cascade fix matched PO Tool's
schema-extension pattern (Sprint 2 stories 17/19/21).

**Single-active-iteration / dup-id precedence inversion** (Story 7)
was the right call: more actionable error wins. Both regimes coexist
correctly across the duplicate-id and currently-open cases.

## What hit friction

**Story 1 push-back — Python 3.13+ API leak in tests.** TestWriter
used `Path.read_text(newline="")`, available 3.13+; project runs
3.11. Orchestrator-fixed via `read_bytes().decode("utf-8")`. Captured
for next build's TestWriter brief: include "Python 3.10-compatible
APIs only — no `Path.read_text(newline=...)` / no `match` statements."

**Story 9 — 3 edit cycles on fixture coordination.** `isolated_log`
fixture redirects `LOG_PATH` to tmp_path, but `resolve_role_spec`
anchors at `LOG_PATH.parent / "roles"`. First attempt missed this
coupling; second attempt fixed decompose but broke 2 sidecar tests
in other files (overly-broad autouse); third attempt scoped the
autouse fixture to test_decompose.py only. Implementation itself
landed first-pass clean — friction was test-infrastructure, not
contract drift.

## Retro items captured (non-blocking, for Sprint 2 / Iter 1 close)

1. **`DecomposeAgentError` defined but unused** (Story 9) — delete
   in cleanup OR wire it up if Iter 2 needs structured agent-side
   wrapping.
2. **`_TERMINAL_STATES` constant defined but unused** (Story 4) —
   transition table encodes terminality via empty frozensets; constant
   is redundant. One-line cleanup.
3. **`SM_LOG_PATH` env var read by production** (Story 5) — only set
   by test fixtures, but production also reads it. If Iter 2 adds
   shared-shell scenarios, gate behind `--log-path` flag or rename to
   `SM_TEST_LOG_PATH`.
4. **`_HELP_TEXT` doesn't list `decompose` or `sprint-cut`** (introduced
   Stories 9, 11) **or exit codes 7 and 8** (Stories 10, 11). One
   block edit at Iter 1 close.
5. **`test_no_inline_entry_construction_in_sm_module` weaker than its
   name suggests** (Story 3) — currently checks `def build_entry`
   exists; doesn't grep for inline construction. Tighten when more
   writers exist.
6. **Docstring "deep independence both directions"** (Story 3) —
   overpromises shallow-copy behavior. Either rewrite docstring to
   pin shallow-only or add a deep-copy implementation + tests.
7. **Two log replays per ingest** (Story 6) — once via derive_state,
   once via dup-id loop. Acceptable at Iter 1 scale; consolidate if
   perf ever matters.

## What goes in the brief library

For next sprint / next build's TestWriter briefs:

- "Python 3.10-compatible APIs only — no `Path.read_text(newline=...)`,
  no `match` statements, no 3.13+ syntax."
- "If your tests use `isolated_log` (monkeypatched LOG_PATH), be
  aware that role-spec resolution and any other LOG_PATH-anchored
  paths follow. Use a tightly-scoped conftest if you need staged
  fixture data."
- "When introducing a new validation rule (Story 6 dup-id, Story 10
  cross-ref), audit the prior stories' fixtures — your new check
  may surface latent fixture-data bugs."

## Sprint 2 outlook

Stories 13-23 remain (per `Stories_v1.md`):
- 13: Per-story lifecycle state machine (L)
- 14: Manual lifecycle commands (M)
- 15: Reviewer approval entry + test-pass gate (M)
- 16: Status query command (M)
- 17: Per-requirement aggregation (M)
- 18: Iteration close handoff producer (L)
- 19: Force-close command with reason (M)
- 20: Close-and-flow cleanup (S)
- 21: Single-user / no-auth posture (S)
- 22: JSONL-only persistence audit (S)
- 23: Per-story execution pipeline TestWriter→Coder→Reviewer (L, NICE)

3 Ls + 5 Ms + 3 Ss. Story 23 is the NICE-priority that, if it lands,
closes the loop completely (the orchestrator role this build's recording
showed could itself be tooled).
