# sm-tool Iter 4 (multisprint) — Customer Ask

**Opened:** 2026-05-11, immediately after iter3-autonomy closed
**Customer:** Nick Trolian
**Execution mode:** DOGFOOD — first real-SDK end-to-end test of the
iter3-autonomy work (req-1 codebase context-passing + req-2 file
materialization)
**Iteration_id:** `sm-tool-iter4-multisprint`
**Target version:** v0.5.0 holds; this iteration ships as a checkpoint
toward v0.5.0 once iter5 also closes (req-3 + req-4 work).

## The customer's pain (Mom Test discipline)

*"sm-tool's lifecycle allows only one sprint per iteration. If
iteration → sprint → story collapses to iteration → story under that
constraint, the sprint layer is dead ceremony."*

Discovered when attempting Sprint 2 within iter3-autonomy:
sprint-cut errored "sprint cut locked — these in-sprint stories
have left planned state." Filed as `iter3/Findings.md` Finding 3.

## Scope (1 requirement — laser tight on the constraint relaxation)

Relax the sprint-cut lock so multiple sprint-cuts are allowed per
iteration. Lock fires only when stories from the previous cut are
still non-terminal. Once all previously-cut stories reach
terminal state, new sprint-cut allowed. `derive_state` uses the
LATEST `sprint_cut` entry as the active sprint.

## Non-goals

- The 6 polish items deferred from Iter 3 v1 (those stay in
  Iter 3 Findings, await later iteration planning)
- req-3 + req-4 deferred from iter3-autonomy (those are iter5 scope)
- Renaming or restructuring sprint concepts (the fix is a
  constraint relaxation, not a redesign)
- Validating against the original Iter 1 sprint-cut tests beyond
  what naturally cascades (preserve their intent where possible;
  update count-pins or assertions where the constraint relaxation
  changes the contract)

## Definition of success

- `sprint-cut` no longer locks when previous in-sprint stories are
  all terminal
- Multiple `sprint_cut` entries in a single iteration's log accepted
  by replay
- `derive_state` reports the latest sprint as the active sprint
- All existing tests pass (cascade resolution where needed)
- Iter 4 itself was BUILT via dogfood mode — sm-tool's execute
  pipeline produced the code changes through real Anthropic SDK
  calls, landing as .candidate sidecars that the operator reviewed
  and applied. **Empirical data on whether dogfood mode works at
  all is the secondary deliverable of this iteration.**

## Dogfood execution notes

This iteration is the first real test of the iter3-autonomy work.
Each story will run through the live `python -m sm execute` pipeline
against the real Anthropic SDK. Expected outcome per story:
- TestWriter agent produces a test file (greenfield) — materialized
  at `tests/test_<short_id>.py`
- Coder agent produces sm.py changes (collision with existing
  sm.py) — materialized at `sm.py.candidate` + `sm.py.candidate.diff`
- Reviewer agent produces accept/reject verdict
- Operator (me, in this session) reviews the candidate diff,
  decides to merge or not, runs pytest to confirm the test file
  passes against the merged sm.py
- If pytest passes: submit + record-review + accept the story
- If pytest fails or the candidate diff is unusable: force-close
  the story with reason, orchestrator picks up the work manually,
  file the failure as a Finding for next iteration

Worst-case scenario: dogfood produces unusable outputs across the
board, we pivot to orchestrator-driven for the actual work, and
the iteration ships the multi-sprint fix anyway (via orchestrator)
while filing rich Findings about what dogfood needs to be viable.
Even that outcome is high-value data.

Cost cap: ~$2 in SDK spend across the iteration.
