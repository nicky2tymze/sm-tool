# sm-tool Iter 4 (multisprint) — Requirements v1

One requirement. Laser-focused.

## Requirement 1: Relax sprint-cut lock to allow multi-sprint per iteration (MUST)

ROLLS UP TO: Customer Ask scope; iter3 Findings Finding 3.

AS AN operator running multi-batch work within a single iteration, I
WANT sprint-cut to allow multiple cuts within an iteration SO THAT
the sprint layer carries real planning-batch semantics rather than
being a dead ceremony layer between iteration and story.

ACCEPTANCE:

**Lock relaxation:**
  - `sprint-cut N` currently rejects with "sprint cut locked" if any
    story in the active iteration has left `planned` state.
  - After this change: `sprint-cut N` rejects only if there are
    currently-active non-terminal in-sprint stories (states
    `in_progress`, `in_review`).
  - When all previously-cut in-sprint stories have reached terminal
    states (`accepted`, `rejected`, `force_closed`), `sprint-cut`
    accepts a new cut.
  - Each `sprint-cut N` writes a new `sprint_cut` log entry; entries
    are append-only (no rewrites).

**`derive_state` semantics:**
  - Multiple `sprint_cut` entries in an iteration are accepted by
    replay.
  - `derive_state` reports the LATEST `sprint_cut` entry as the
    active sprint.
  - Stories cut into earlier sprints remain in their terminal state
    (their `story_state_change` history is preserved); they are NOT
    re-cut by a later `sprint_cut`.
  - The active sprint's story IDs are the prefix of the planned
    backlog corresponding to the latest `sprint_cut`'s `cut_position`
    (the existing semantic, applied to the LATEST cut not the only
    cut).

**Backlog accounting:**
  - The N in `sprint-cut N` refers to the COUNT of CURRENTLY-PLANNED
    stories to cut into the new sprint (i.e., stories not yet
    promoted to any sprint). Already-cut + terminal stories don't
    count against N.
  - `status` output distinguishes stories from each cut (or at
    minimum, the latest cut is clearly visible).

**Behavioral preservation:**
  - All Iter 1 sprint-cut tests pass after the change (intent
    preserved; cascade-update count-pins where the lock semantics
    changed).
  - `close_iteration` validation continues to require every
    in-sprint story (across ALL cuts in this iteration) to be in
    terminal state.
  - `force_close` continues to work unchanged.

**Documentation:**
  - `_HELP_TEXT` updated for `sprint-cut` to reflect multi-cut
    semantics.
  - The change is described in iter4/Retro.md when the iteration
    ships.

## OPEN_QUESTIONS_FOR_PO

None — the design was discussed and locked at iter3-autonomy close:
Option 2 (relax the lock, allow multiple cuts) over Option 1
(remove sprints entirely).
