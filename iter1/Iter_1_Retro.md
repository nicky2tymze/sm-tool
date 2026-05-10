# sm-tool — Iteration 1 Retro

**Iter 1 SHIPPED 2026-05-10.** All 23 stories across two sprints
delivered, recorded live across two recording sessions on a single
Mother's Day.

---

## Headline numbers

| Metric | Sprint 1 | Sprint 2 | Iter 1 Total |
|---|---|---|---|
| Stories shipped | 12/12 | 11/11 | **23/23** |
| Sizes | 2 S + 6 M + 4 L | 3 S + 5 M + 3 L | 5 S + 11 M + 7 L |
| Tests | 942 | 738 | **1680** |
| Test files | 13 | 10 | **23** |
| L stories first-pass clean (impl) | 4/4 | 3/3 | **7/7** |
| Push-back fires | 1 (Story 1) | 0 direct | **1** |
| Cascade fixes | 1 (Story 10) | 1 (Story 15) | **2** |
| Verification-only stories (no Coder) | 0 | 3 (S20, S21, S22) | **3** |

## What worked across the iteration

**The TestWriter → Coder → Reviewer pipeline scales.** Reproduced
PO Tool's pattern cleanly: thorough TestWriter briefs + Coder maps
1:1 to spec + Reviewer signs. 7-of-7 L linchpins first-pass
implementation-clean held across 23 stories.

**Forward-compat design pays compound interest.** Story 18's
`closed_by`/`reason` kwargs made Story 19's force-close ~30 minutes
of structural delegation rather than rework. Story 4's full
`_VALID_TRANSITIONS` graph made Story 19's force-close writer a
clean private bypass of Story 13's narrow operator graph. The
dual-graph design kept writer authority partitioned across stories
without one having to know about the other's existence.

**Verification-only stories surface as a category.** Stories 20,
21, 22 introduced no production code — the prior stories already
implemented the contract; the test files locked it in as standing
assertions. New pattern for the brief library: when a story is
purely a contract pin against existing behavior, ship as
verification-only with no Coder spawn.

**Schema-extension cascades resolve cleanly.** Two cascades in this
iteration (Story 10 cross-ref check, Story 15 review gate) both
exposed latent fixture-data issues in earlier stories' tests.
Both resolved via behavior-preserving fixture extension (extending
seed data to satisfy the new contract) — zero assertion drift.
Pattern matches PO Tool's Sprint 2 schema extensions exactly.

**The brief library is real and effective.** Push-back categories
retire after first observation: PO Tool's Windows-tempdir push-back
was gone in sm-tool because the TestWriter brief had Windows
guardrails. sm-tool's only push-back (Story 1, Python 3.13+ API
in tests) is now captured for the next build's brief: "Python 3.10-
compatible APIs only — no `Path.read_text(newline=...)`, no `match`."

## What hit friction

**Story 1 push-back — Python 3.13+ API leak.** TestWriter used
`Path.read_text(newline="")` (3.13+); project runs 3.11.
Orchestrator-fixed via `read_bytes().decode("utf-8")`. **Captured
for next-build brief.**

**Story 9 — 3 edit cycles on test-fixture coordination.**
`isolated_log` redirects LOG_PATH; `resolve_role_spec` anchors at
LOG_PATH.parent. First attempt missed the coupling; second broke
sidecar tests via overly-broad autouse; third correctly scoped the
autouse to test_decompose.py only. Implementation logic itself
landed first-pass clean.

**Story 10 cascade — 9 Story 9 tests had latent fixture issues.**
`_canonical_agent_output(n=3+)` referenced req-3+ but `_seed_iteration`
default was 2 reqs. Story 10's cross-ref check correctly caught
the inconsistency. Resolution: extend `_seed_iteration` defaults
to 5 reqs.

**Story 15 cascade — 22 prior tests inserted record_review before
accept.** Story 15's gate exposed prior tests' assumption that
`accept` could happen without prior approval. Resolution: insert
`record_review` calls + extend `_advance` helper to auto-record.
Zero assertion drift; pure fixture-data extension.

**Orchestrator routing error on Story 18.** First Coder spawn went
to `Tools/PO/po.py` (FluxPlatform) instead of sm-tool's `sm.py` —
the agent guessed wrong from "test_close_iteration.py" name
collision with PO Tool's file. Re-spawned with explicit absolute
paths; clean first-try implementation. **Brief-library item:** when
test filename collides across repos, lead the Coder brief with
explicit absolute paths to the target repo.

## Retro items captured (non-blocking, for Iter 2)

1. **`DecomposeAgentError` defined but unused** (Story 9)
2. **`_TERMINAL_STATES` constant defined but unused** (Story 4)
3. **`SM_LOG_PATH` env var read by production** (Story 5) — contract-driven
   for hermetic test isolation; consider gating behind `--log-path`
   flag for Iter 2
4. **`_HELP_TEXT` lags far behind** — only `ingest` documented;
   missing `decompose`, `sprint-cut`, `start/submit/accept/reject`,
   `record-review`, `status`, `close`, `force-close`, `execute`
   subcommands and exit codes 7-11
5. **`test_no_inline_entry_construction_in_sm_module` weaker than
   name** (Story 3) — currently checks `def build_entry` exists;
   doesn't grep for inline construction. Tighten now that more
   writers exist (close handoff producer adds another writer site).
6. **Docstring "deep independence both directions"** (Story 3) —
   overpromises shallow-copy behavior. Either rewrite or implement
   deep-copy.
7. **Two log replays per ingest** (Story 6) — once via derive_state,
   once via dup-id loop. Acceptable at Iter 1 scale; consolidate
   if Iter 2 perf matters.
8. **Defensive `or []` after `.get()`** in `aggregate_requirements`
   (Story 17) — redundant since defaults already handle missing
   keys; harmless.
9. **Paranoid `try/except Exception: pass`** around `record_review`
   in execute reject path (Story 23) — branch is unreachable since
   `test_result_str.strip()` is truthy when entered.
10. **derive_state could carry `iteration_goal`** to remove the
    log re-scan in `close_iteration` (Story 18). State-shape
    enrichment opportunity.
11. **`_LIFECYCLE_TARGETS` defined inside `_cli_main`** (Story 14) —
    rebuilds on every CLI invocation. Hoist to module-level constant.

## What goes in the brief library

For next iteration / next build's TestWriter briefs:

- "Python 3.10-compatible APIs only — no `Path.read_text(newline=...)`,
  no `Path.write_text(newline=...)`, no `match` statements, no 3.13+
  syntax." (Story 1 push-back)
- "When introducing a new validation rule, audit the prior stories'
  fixtures — your new check may surface latent fixture-data bugs."
  (Stories 10, 15 cascades)
- "If your tests use `isolated_log` (monkeypatched LOG_PATH), be
  aware that role-spec resolution and any other LOG_PATH-anchored
  paths follow. Use a tightly-scoped conftest if you need staged
  fixture data." (Story 9 friction)
- "When test filename collides with a sibling-tool's filename in
  another repo, lead the Coder brief with explicit absolute paths
  to the target repo." (Story 18 orchestrator routing error)
- "Verification-only stories: when a story is purely a contract
  pin against existing behavior, ship as verification-only with
  no Coder spawn." (Stories 20, 21, 22 pattern)

## Iter 2 outlook (non-binding, suggestions only)

- **Real agent integration**: the four NotImplementedError defaults
  (decompose, execute × 3 spawn callables) all carry a single
  message: "real agent integration ships in Iter 2." Wire actual
  Claude Code subprocess invocation as the default.
- **Polish pass**: address the 11 retro items as a single Iter 2
  cleanup story.
- **HELP_TEXT refresh**: bring CLI help in line with the actual
  surface (12 subcommands, 12 exit codes).
- **Real customer interview**: PO Tool v0.2's conditional acceptance
  gate is still open — drive a real customer through PO Tool to
  close it. (Was added to backlog at Mom-time.)
