"""Story 12 — sprint-cut re-run lock once any in-sprint story leaves planned.

Story 12 is the FINAL story of Sprint 1 (size: S). It adds the lock check
that rejects re-cut once any in-sprint story has transitioned out of
``planned``.

What this file pins:

  - New typed exception `SprintCutLockedError(SprintCutError)`.
      * Subclass of `SprintCutError`, transitively of `ValueError`.
      * Distinct class from `SprintCutError`.
      * Public — exported via `__all__`, importable as
        `from sm import SprintCutLockedError`.

  - Lock-check rule:
      * On a re-cut (a prior `sprint_cut` entry already exists in the log),
        if `derive_state()` shows ANY in-sprint story (per the LATEST
        sprint_cut entry's `in_sprint_story_ids`) has a state other than
        ``planned`` (i.e., `in_progress`, `in_review`, `accepted`,
        `rejected`, or `force_closed`), the new sprint_cut call refuses
        with `SprintCutLockedError` and writes nothing to the log.
      * The lock check uses the same replay-derived state — no separate
        flag is persisted.
      * Error message names the offending story_id(s) and instructs the
        operator (e.g., to close / force-close the iteration before
        re-cutting).

  - First-cut unconditional:
      * The lock applies only to RE-cut. With no prior `sprint_cut` entry,
        `sprint_cut(N)` succeeds regardless of any story_state_change
        entries present in the log.

  - Lock applies only to in-sprint stories:
      * Deferred stories (those NOT in the latest sprint_cut entry's
        `in_sprint_story_ids`) do NOT lock the cut. Transitioning a
        deferred story out of `planned` and re-cutting → still allowed.
      * Only stories in the LATEST sprint_cut's `in_sprint_story_ids`
        count for the lock.

  - All-still-planned re-cut:
      * If every in-sprint story is still `planned`, the re-cut succeeds.
      * A successful re-cut writes a new `sprint_cut` entry that
        supersedes the prior on replay (Story 4 already gives us this).

  - Failure invariants:
      * Lock failure → log byte-for-byte unchanged.
      * Lock failure → derive_state byte-for-byte unchanged.
      * `_append_entry` not called on lock failure.

  - CLI surface:
      * `python -m sm sprint-cut <N>` is still a recognized command.
      * Lock failure → exits non-zero, NOT 'unknown command'.

Story 13 deferral: Story 13 implements the per-story lifecycle state-
machine command. For Story 12, tests CRAFT `story_state_change` entries
directly (via `build_entry` + `_append_entry`) to simulate transitions —
this works because Story 4's `derive_state()` already reads
`story_state_change` entries.

Tests must FAIL on first run — `SprintCutLockedError` does not exist
yet, and `sprint_cut` does not yet enforce the lock. The Coder
downstream implements the lock check and the typed error to satisfy
these tests.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file. Mirrors suite
    convention."""
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _open_iteration(iteration_id: str = "iter-1",
                    requirements=None) -> dict:
    """Append an `iteration_open` entry directly so a subsequent
    sprint_cut() has an active iteration to work against."""
    import sm
    if requirements is None:
        requirements = [
            {"requirement_id": "req-1", "title": "T1",
             "description": "D1", "priority": "MUST",
             "acceptance_criteria": "AC1"},
        ]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": "Test iteration",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return entry


def _seed_backlog(n: int = 5) -> list:
    """Append a `story_backlog` entry with N canonical stories. Returns
    the list of minted story_ids (in sequence order)."""
    import sm
    import uuid as _uuid

    story_ids = [_uuid.uuid4().hex for _ in range(n)]
    sizes = ["S", "M", "L"]
    stories = []
    for i in range(1, n + 1):
        stories.append({
            "story_id": story_ids[i - 1],
            "sequence": i,
            "title": f"Story {i}",
            "size": sizes[(i - 1) % 3],
            "requirement_ids": ["req-1"],
            "acceptance_criteria": f"Story {i} must pass.",
        })
    entry = sm.build_entry("story_backlog", {
        "stories": stories,
        "role_spec_path": "<test-stub>",
        "role_spec_hash": "<test-stub>",
    })
    sm._append_entry(entry)
    return story_ids


def _seed_full(n_stories: int = 5,
               iteration_id: str = "iter-1") -> list:
    """Convenience: open iteration + seed backlog. Returns story_ids."""
    _open_iteration(iteration_id=iteration_id)
    return _seed_backlog(n=n_stories)


def _craft_state_change(story_id: str, from_state: str, to_state: str):
    """Bypass Story 13's command — directly write a `story_state_change`
    entry. derive_state already reads these (Story 4)."""
    import sm
    entry = sm.build_entry("story_state_change", {
        "story_id": story_id,
        "from_state": from_state,
        "to_state": to_state,
        "notes": "test fixture",
    })
    sm._append_entry(entry)
    return entry


def _resolve_to_force_closed(story_ids):
    """iter4-multisprint-v2 cascade helper — force-close each story
    straight from planned (legal per _VALID_TRANSITIONS). Used to put
    an in-sprint cohort into the terminal accepting set before a re-cut
    under the relaxed lock."""
    for sid in story_ids:
        _craft_state_change(sid, "planned", "force_closed")


# ===========================================================================
# Smoke (3) — SprintCutLockedError exists, in __all__, subclass relationships
# ===========================================================================


def test_sprint_cut_locked_error_class_exists():
    """`SprintCutLockedError` must exist on `sm`."""
    import sm
    assert hasattr(sm, "SprintCutLockedError"), (
        "sm.SprintCutLockedError must exist"
    )


def test_sprint_cut_locked_error_in_dunder_all():
    """Public typed error — exported via __all__."""
    import sm
    assert "SprintCutLockedError" in sm.__all__, (
        f"SprintCutLockedError must be in __all__; got {sm.__all__!r}"
    )


def test_sprint_cut_locked_error_subclasses_sprint_cut_error():
    """SprintCutLockedError must be a subclass of SprintCutError so
    existing `except SprintCutError` callers keep catching it."""
    import sm
    assert issubclass(sm.SprintCutLockedError, sm.SprintCutError), (
        "SprintCutLockedError must subclass SprintCutError"
    )


def test_sprint_cut_locked_error_subclasses_value_error():
    """Transitively, SprintCutLockedError is also a ValueError so a bare
    `except ValueError` clause keeps catching it."""
    import sm
    assert issubclass(sm.SprintCutLockedError, ValueError), (
        "SprintCutLockedError must transitively subclass ValueError"
    )


def test_sprint_cut_locked_error_is_distinct_class():
    """SprintCutLockedError is not the same class as SprintCutError —
    callers can branch on the exact class."""
    import sm
    assert sm.SprintCutLockedError is not sm.SprintCutError


def test_sprint_cut_locked_error_importable_directly():
    """`from sm import SprintCutLockedError` succeeds — public form."""
    from sm import SprintCutLockedError  # noqa: F401
    assert isinstance(SprintCutLockedError, type)


# ===========================================================================
# First-cut unconditional (3+) — lock applies ONLY to RE-cut
# ===========================================================================


def test_first_cut_succeeds_with_no_prior_state_changes(isolated_log):
    """Plain first cut, no state changes anywhere. Lock check is a no-op
    on first cut — sanity baseline."""
    import sm
    _seed_full(n_stories=5)
    # No prior sprint_cut, no state changes — first cut must succeed.
    result = sm.sprint_cut(3)
    assert result["cut_position"] == 3


def test_first_cut_succeeds_even_if_state_changes_exist(isolated_log):
    """Even with crafted story_state_change entries on the log, the FIRST
    sprint_cut (no prior cut) must succeed. The lock applies only to
    re-cut.

    NOTE: under the normal lifecycle, a story_state_change entry can't
    target a story id that isn't yet in the backlog — but it CAN exist
    before any sprint_cut. derive_state() will treat the in-sprint set as
    empty until the first sprint_cut entry lands. So crafting a
    state-change before the first cut is legal at the replay layer.

    Story 3 cascade: under Story 3 semantics, sprint_cut(N) counts over
    CURRENTLY-PLANNED stories. Moving sids[0] to in_progress removes it
    from the currently-planned set, so the cut selects from the tail.
    """
    import sm
    sids = _seed_full(n_stories=5)
    # Move a story out of planned BEFORE any sprint_cut entry.
    _craft_state_change(sids[0], "planned", "in_progress")
    # First cut: no prior sprint_cut entry → lock cannot apply.
    result = sm.sprint_cut(3)
    assert result["cut_position"] == 3
    # Under Story 3: currently-planned = sids[1..4]; cut(3) = sids[1:4].
    assert result["in_sprint_story_ids"] == sids[1:4]


def test_first_cut_writes_entry_log_grows(isolated_log):
    """First cut always writes an entry — confirm log grew by 1."""
    import sm
    _seed_full(n_stories=4)
    before = list(sm.read_entries())
    sm.sprint_cut(2)
    after = list(sm.read_entries())
    assert len(after) == len(before) + 1


def test_first_cut_no_lock_error_raised(isolated_log):
    """First cut must NOT raise SprintCutLockedError, even if a story has
    transitioned. The lock targets re-cut only."""
    import sm
    sids = _seed_full(n_stories=5)
    _craft_state_change(sids[0], "planned", "force_closed")
    # Must not raise — first cut is unconditional.
    sm.sprint_cut(3)


# ===========================================================================
# Re-cut blocked when in-sprint story transitions (10+)
# ===========================================================================


def test_recut_blocked_after_planned_to_in_progress(isolated_log):
    """In-sprint story transitions planned→in_progress → re-cut blocked."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)  # in-sprint = sids[0..2]
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)


def test_recut_blocked_after_in_progress_to_in_review(isolated_log):
    """Chain: planned→in_progress→in_review → re-cut blocked."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[1], "planned", "in_progress")
    _craft_state_change(sids[1], "in_progress", "in_review")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)


def test_recut_blocked_after_chain_to_accepted(isolated_log):
    """Chain to accepted (terminal) on the single moved story →
    iter4-multisprint-v2 Story 1 relaxes the lock: terminal-only blocks,
    so this single-mover case must now SUCCEED on re-cut once the rest
    of the in-sprint cohort is also resolved to terminal.

    Behavior-preserving update: same setup shape (drive sids[2] to
    accepted) plus terminal-resolve the rest of the in-sprint cohort
    (sids[0..1]) so the LATEST cut's full cohort is terminal. The new
    semantics permit the re-cut."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[2], "planned", "in_progress")
    _craft_state_change(sids[2], "in_progress", "in_review")
    _craft_state_change(sids[2], "in_review", "accepted")
    # Resolve the rest of the in-sprint cohort to terminal so the full
    # cohort is in the accepting set.
    _craft_state_change(sids[0], "planned", "force_closed")
    _craft_state_change(sids[1], "planned", "force_closed")
    # Re-cut now succeeds under the relaxed terminal-only lock.
    result = sm.sprint_cut(2)
    assert result["cut_position"] == 2


def test_recut_blocked_after_chain_to_rejected(isolated_log):
    """Chain to rejected (terminal) on the single moved story →
    iter4-multisprint-v2 Story 1: terminal blocks no longer apply, so
    once the full in-sprint cohort is resolved to terminal the re-cut
    succeeds.

    Story 3 cascade: backlog expanded from 5 to 6 so 3 planned stories
    remain after the first cohort goes terminal — enough to satisfy
    cut(3) under Story 3's currently-planned-count precondition."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[0], "in_progress", "in_review")
    _craft_state_change(sids[0], "in_review", "rejected")
    # Resolve the rest of the in-sprint cohort to terminal.
    _craft_state_change(sids[1], "planned", "force_closed")
    _craft_state_change(sids[2], "planned", "force_closed")
    result = sm.sprint_cut(3)
    assert result["cut_position"] == 3


def test_recut_blocked_after_force_close_from_planned(isolated_log):
    """Force-close direct from planned (terminal) on the single moved
    story → iter4-multisprint-v2 Story 1: terminal no longer blocks. Once
    the rest of the in-sprint cohort is also terminal the re-cut
    succeeds."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[1], "planned", "force_closed")
    # Resolve the rest of the in-sprint cohort to terminal.
    _craft_state_change(sids[0], "planned", "force_closed")
    _craft_state_change(sids[2], "planned", "force_closed")
    result = sm.sprint_cut(2)
    assert result["cut_position"] == 2


def test_recut_blocked_after_force_close_from_in_progress(isolated_log):
    """Force-close from in_progress (terminal) → iter4-multisprint-v2
    Story 1: terminal no longer blocks. Once the full cohort is terminal
    the re-cut succeeds."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[2], "planned", "in_progress")
    _craft_state_change(sids[2], "in_progress", "force_closed")
    # Resolve the rest of the in-sprint cohort to terminal.
    _craft_state_change(sids[0], "planned", "force_closed")
    _craft_state_change(sids[1], "planned", "force_closed")
    result = sm.sprint_cut(1)
    assert result["cut_position"] == 1


def test_recut_blocked_after_force_close_from_in_review(isolated_log):
    """Force-close from in_review (terminal) → iter4-multisprint-v2
    Story 1: terminal no longer blocks. Once the full cohort is terminal
    the re-cut succeeds."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[0], "in_progress", "in_review")
    _craft_state_change(sids[0], "in_review", "force_closed")
    # Resolve the rest of the in-sprint cohort to terminal.
    _craft_state_change(sids[1], "planned", "force_closed")
    _craft_state_change(sids[2], "planned", "force_closed")
    result = sm.sprint_cut(2)
    assert result["cut_position"] == 2


def test_recut_blocked_when_any_in_sprint_story_moved(isolated_log):
    """Lock fires if ANY (not all) in-sprint stories have moved."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(4)  # in-sprint = sids[0..3]
    # Move just one of the four.
    _craft_state_change(sids[2], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(3)


def test_recut_blocked_when_first_in_sprint_story_moved(isolated_log):
    """Lock fires when story at sequence 1 (first) has transitioned."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)


def test_recut_blocked_when_last_in_sprint_story_moved(isolated_log):
    """Lock fires when story at the cut boundary (sequence N) has
    transitioned."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)  # boundary at sids[2]
    _craft_state_change(sids[2], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)


def test_recut_blocked_when_multiple_in_sprint_moved(isolated_log):
    """Multiple in-sprint stories moved → still SprintCutLockedError
    (one error covers all offenders, no cascade of exceptions).

    iter4-multisprint-v2 Story 1 cascade: under the relaxed terminal-only
    lock, the force_closed offender from the original setup no longer
    blocks. Swap it for an in_review offender so the test still pins the
    multi-offender lock path (the two in_progress offenders alone would
    pin a multi-offender case, but using two different non-terminal
    states exercises the message-naming surface as well)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[1], "planned", "in_progress")
    _craft_state_change(sids[1], "in_progress", "in_review")
    _craft_state_change(sids[2], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)


def test_blocked_recut_log_unchanged(isolated_log):
    """Lock failure → log byte-for-byte unchanged after the call."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)
    assert isolated_log.read_bytes() == bytes_before


def test_blocked_recut_no_new_sprint_cut_entry(isolated_log):
    """Lock failure → no new sprint_cut entry was appended."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")

    cut_count_before = sum(
        1 for e in sm.read_entries() if e.get("type") == "sprint_cut"
    )
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)
    cut_count_after = sum(
        1 for e in sm.read_entries() if e.get("type") == "sprint_cut"
    )
    assert cut_count_after == cut_count_before


# ===========================================================================
# Re-cut allowed when all in-sprint stories still planned (3+)
# ===========================================================================


def test_recut_allowed_when_no_state_changes(isolated_log):
    """Cut, then re-cut after the in-sprint cohort is terminal-resolved
    → allowed.

    iter4-multisprint-v2 Story 1 cascade: under the relaxed terminal-
    only lock, 'still planned' no longer satisfies the accepting set.
    Resolve sids[0..2] to terminal explicitly before the re-cut.

    Story 3 cascade: backlog expanded to 7 so 4 planned stories remain
    after the first cohort goes terminal."""
    import sm
    sids = _seed_full(n_stories=7)
    sm.sprint_cut(3)
    _resolve_to_force_closed(sids[:3])
    sm.sprint_cut(4)
    state = sm.derive_state()
    assert state["sprint_cut"] == 4


def test_recut_allowed_supersedes_prior_on_replay(isolated_log):
    """A successful re-cut writes a new sprint_cut entry that supersedes
    the prior one on replay — Story 4 already gives us this.

    iter4-multisprint-v2 Story 1 cascade: terminal-resolve sids[0..1]
    between the two cuts.

    Story 3 cascade: under Story 3, the second cut's in_sprint contains
    only the new cohort = sids[2..5] (first 4 currently-planned)."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(2)
    _resolve_to_force_closed(sids[:2])
    second = sm.sprint_cut(4)
    state = sm.derive_state()
    assert state["sprint_cut"] == 4
    assert second["in_sprint_story_ids"] == sids[2:6]


def test_recut_allowed_to_smaller_n_when_all_planned(isolated_log):
    """Re-cut to a SMALLER N succeeds when the in-sprint cohort is
    terminal-resolved.

    iter4-multisprint-v2 Story 1 cascade: under the relaxed lock,
    'still planned' blocks. Resolve sids[0..4] to terminal before the
    smaller re-cut. Test name preserved for cross-referencing the lock
    behavior under smaller-N re-cut.

    Story 3 cascade: backlog expanded to 7 so 2 planned stories remain
    after sids[0..4] go terminal."""
    import sm
    sids = _seed_full(n_stories=7)
    sm.sprint_cut(5)
    _resolve_to_force_closed(sids[:5])
    sm.sprint_cut(2)
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_recut_allowed_chain_when_all_planned(isolated_log):
    """Multiple re-cuts in a row — all succeed when the in-sprint cohort
    is terminal-resolved between cuts.

    iter4-multisprint-v2 Story 1 cascade: between every pair of cuts,
    force-close the newly-added in-sprint stories so the relaxed lock
    permits the next cut. Test name preserved.

    Story 3 cascade: under Story 3, each sprint_cut(N) selects N from
    the currently-planned tail. Backlog kept at 8; chain rewritten as
    (1, 2, 2, 3) with each round resolving its full cohort. cut_position
    of the final cut equals 3 under Story 3 (count for THIS cut, not
    cumulative)."""
    import sm
    sids = _seed_full(n_stories=8)
    sm.sprint_cut(1)  # cohort = sids[0]
    _resolve_to_force_closed(sids[0:1])
    sm.sprint_cut(2)  # cohort = sids[1..2]
    _resolve_to_force_closed(sids[1:3])
    sm.sprint_cut(2)  # cohort = sids[3..4]
    _resolve_to_force_closed(sids[3:5])
    sm.sprint_cut(3)  # cohort = sids[5..7]; cut_position = 3 under Story 3.
    state = sm.derive_state()
    assert state["sprint_cut"] == 3


def test_recut_allowed_writes_new_entry(isolated_log):
    """Allowed re-cut writes exactly one new sprint_cut entry on the
    log.

    iter4-multisprint-v2 Story 1 cascade: terminal-resolve sids[0..2]
    so the relaxed lock permits the re-cut.

    Story 3 cascade: backlog expanded to 7 so 4 planned remain after
    the first cohort goes terminal."""
    import sm
    sids = _seed_full(n_stories=7)
    sm.sprint_cut(3)
    _resolve_to_force_closed(sids[:3])
    cut_count_before = sum(
        1 for e in sm.read_entries() if e.get("type") == "sprint_cut"
    )
    sm.sprint_cut(4)
    cut_count_after = sum(
        1 for e in sm.read_entries() if e.get("type") == "sprint_cut"
    )
    assert cut_count_after == cut_count_before + 1


# ===========================================================================
# Lock applies only to in-sprint (4+)
# ===========================================================================


def test_deferred_story_transition_does_not_lock(isolated_log):
    """Cut at 3 of 6 → sids[3..5] are deferred. Transitioning a
    deferred story does not affect the in-sprint cohort.

    iter4-multisprint-v2 Story 1 cascade: still pin 'deferred state
    changes don't lock' but additionally terminal-resolve the in-sprint
    cohort (sids[0..2]) so the relaxed lock permits the re-cut. The
    test's intent — that deferred transitions don't count — is preserved
    via the contrast: sids[4] is in_progress (would block if in-sprint)
    yet the re-cut succeeds.

    Story 3 cascade: backlog expanded from 5 to 6 because under Story 3
    the moved deferred story (sids[4]) is no longer currently-planned,
    so the tail needs at least 2 planned entries (sids[3] and sids[5])
    for cut(2)."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)  # in-sprint = sids[0..2], deferred = sids[3..5]
    _craft_state_change(sids[4], "planned", "in_progress")
    # Resolve in-sprint cohort to terminal (relaxed lock).
    _resolve_to_force_closed(sids[:3])
    # Re-cut must succeed — sids[4] is deferred (non-terminal but not
    # in the in-sprint cohort). currently-planned tail = sids[3], sids[5].
    sm.sprint_cut(2)
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_deferred_story_force_closed_does_not_lock(isolated_log):
    """Force-closing a DEFERRED story → re-cut still allowed.

    iter4-multisprint-v2 Story 1 cascade: terminal-resolve the in-sprint
    cohort too. The test's intent — that deferred-story state doesn't
    matter — is preserved.

    Story 3 cascade: backlog expanded from 6 to 8 because under Story 3
    `sids[5]` (force_closed) no longer counts as currently-planned. The
    tail needs 4 entries for cut(4)."""
    import sm
    sids = _seed_full(n_stories=8)
    sm.sprint_cut(3)
    _craft_state_change(sids[5], "planned", "force_closed")
    _resolve_to_force_closed(sids[:3])
    # currently_planned tail = sids[3], sids[4], sids[6], sids[7] (4).
    sm.sprint_cut(4)
    state = sm.derive_state()
    assert state["sprint_cut"] == 4


def test_deferred_story_chain_to_accepted_does_not_lock(isolated_log):
    """Deferred story → in_progress → in_review → accepted → re-cut
    still allowed.

    iter4-multisprint-v2 Story 1 cascade: terminal-resolve the in-sprint
    cohort. The test's intent (deferred chain doesn't affect lock) is
    preserved.

    Story 3 cascade: backlog expanded from 6 to 9 because under Story 3
    sids[4] (accepted) no longer counts as currently-planned. The tail
    needs 5 entries for cut(5)."""
    import sm
    sids = _seed_full(n_stories=9)
    sm.sprint_cut(3)
    _craft_state_change(sids[4], "planned", "in_progress")
    _craft_state_change(sids[4], "in_progress", "in_review")
    _craft_state_change(sids[4], "in_review", "accepted")
    _resolve_to_force_closed(sids[:3])
    # currently_planned tail = sids[3], sids[5..8] (5).
    sm.sprint_cut(5)
    state = sm.derive_state()
    assert state["sprint_cut"] == 5


def test_only_latest_cut_in_sprint_set_counts(isolated_log):
    """Cut at 3, re-cut at 5 (changing in-sprint to the next 5 planned).
    Then transition a story in the NEW cohort. Re-cut must be blocked
    because that story is now in the IN-SPRINT set per the LATEST cut.

    iter4-multisprint-v2 Story 1 cascade: terminal-resolve sids[0..2]
    between the two cuts so the second cut is permitted; then sids[4]'s
    transition to in_progress provides the lock offender for the third
    cut attempt.

    Story 3 cascade: backlog expanded from 6 to 8 so the second cut(5)
    fits the currently-planned tail. Under Story 3 cut(5) cohort =
    sids[3..7]; sids[4] is in that cohort, so transitioning it to
    in_progress is a valid lock offender. Third cut N reduced to 2 so
    range precondition doesn't preempt lock check."""
    import sm
    sids = _seed_full(n_stories=8)
    sm.sprint_cut(3)  # in-sprint = sids[0..2]
    _resolve_to_force_closed(sids[:3])
    sm.sprint_cut(5)  # Story 3 cohort = sids[3..7]
    _craft_state_change(sids[4], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)


def test_chain_recut_then_transition_then_recut(isolated_log):
    """Cut → re-cut [allowed after terminal resolve] → transition a
    story in the new in-sprint set → re-cut blocked.

    iter4-multisprint-v2 Story 1 cascade: terminal-resolve sids[0..2]
    between the first two allowed cuts. The second cut picks up sids[3]
    which is still planned; transition sids[3] to in_progress to provide
    a non-terminal offender that blocks the third cut. The test's
    intent — that a chain of cuts followed by an offender transition
    blocks the next cut — is preserved.

    Story 3 cascade: backlog expanded to 6 so 3 planned remain after the
    first cohort goes terminal. Under Story 3 the second cut(3) cohort =
    sids[3..5]; transitioning sids[3] to in_progress provides the offender.
    Third cut N reduced to 1 so range doesn't preempt the lock check
    (currently_planned tail = 2 after sids[3] goes in_progress)."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)
    _resolve_to_force_closed(sids[:3])
    sm.sprint_cut(3)  # Story 3 cohort = sids[3..5]; all still planned
    _craft_state_change(sids[3], "planned", "in_progress")
    # sids[3] is in the latest cohort and is non-terminal — blocks.
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(1)


def test_only_in_sprint_stories_not_full_backlog(isolated_log):
    """Cut at 1 — only sids[0] is in-sprint. Transitioning deferred
    stories does NOT lock — only sids[0] would.

    iter4-multisprint-v2 Story 1 cascade: terminal-resolve sids[0] (the
    only in-sprint story) so the relaxed lock permits the re-cut. The
    test's intent — that deferred-story moves don't count — is preserved
    via the contrast: some deferred stories are in_progress (would block
    if in in-sprint) yet the re-cut succeeds.

    Story 3 cascade: backlog expanded from 5 to 8 — under Story 3,
    in_progress stories don't count toward currently-planned, so we
    need 3 planned stories remaining for cut(3). Move only sids[1..4]
    to in_progress (sids[5..7] stay planned)."""
    import sm
    sids = _seed_full(n_stories=8)
    sm.sprint_cut(1)
    # Move some deferred stories (sids[1..4]) — but leave sids[5..7]
    # planned so currently-planned tail has enough for cut(3).
    for i in range(1, 5):
        _craft_state_change(sids[i], "planned", "in_progress")
    # Resolve the in-sprint cohort (just sids[0]) to terminal.
    _resolve_to_force_closed(sids[:1])
    # All four deferred moves don't count — re-cut still allowed.
    # currently_planned tail = sids[5..7] (3 stories).
    sm.sprint_cut(3)
    state = sm.derive_state()
    assert state["sprint_cut"] == 3


# ===========================================================================
# Error message content (3+) — names offending story_id and instructs
# ===========================================================================


def test_lock_error_message_names_offending_story_id(isolated_log):
    """The error message names the story_id that has left planned."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    offender = sids[1]
    _craft_state_change(offender, "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError) as exc_info:
        sm.sprint_cut(2)
    msg = str(exc_info.value)
    assert offender in msg, (
        f"error must name offending story_id {offender!r}; "
        f"got: {exc_info.value!s}"
    )


def test_lock_error_message_lists_multiple_offenders(isolated_log):
    """Multiple offenders → error names ALL of them (or at minimum more
    than one).

    iter4-multisprint-v2 Story 1 cascade: the original setup used a
    force_closed offender, which under the relaxed lock is terminal
    (not an offender). Swap to two non-terminal offenders so the
    multi-offender naming surface is still exercised."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[1], "planned", "in_progress")
    _craft_state_change(sids[1], "in_progress", "in_review")
    with pytest.raises(sm.SprintCutLockedError) as exc_info:
        sm.sprint_cut(2)
    msg = str(exc_info.value)
    assert sids[0] in msg, (
        f"error must name first offender {sids[0]!r}; got: {msg}"
    )
    assert sids[1] in msg, (
        f"error must name second offender {sids[1]!r}; got: {msg}"
    )


def test_lock_error_message_is_actionable(isolated_log):
    """The error message instructs the operator about the path forward —
    closing or force-closing or some actionable hint."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError) as exc_info:
        sm.sprint_cut(2)
    msg = str(exc_info.value).lower()
    # Some actionable language must be present — close, force-close,
    # iteration, planned, lock — operator-facing hint.
    actionable_terms = (
        "close", "force", "iteration", "planned", "lock", "cannot",
        "leave", "left", "transitioned", "re-cut", "recut",
    )
    assert any(t in msg for t in actionable_terms), (
        f"error must include actionable language; got: {exc_info.value!s}"
    )


def test_lock_error_message_mentions_state(isolated_log):
    """Error names the lock condition — 'planned' / state / lock / etc."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError) as exc_info:
        sm.sprint_cut(2)
    msg = str(exc_info.value).lower()
    assert ("planned" in msg or "state" in msg or "lock" in msg), (
        f"error must mention planned/state/lock; got: {exc_info.value!s}"
    )


# ===========================================================================
# Failure invariants (3+) — log unchanged, derive_state unchanged, etc.
# ===========================================================================


def test_lock_failure_log_unchanged(isolated_log):
    """On lock failure, the log is byte-for-byte the same as before
    the failed call."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[1], "planned", "in_progress")
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)
    assert isolated_log.read_bytes() == bytes_before


def test_lock_failure_derive_state_unchanged(isolated_log):
    """derive_state() before/after a locked re-cut must be equal."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    before = sm.derive_state()
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)
    after = sm.derive_state()
    assert before == after


def test_lock_failure_append_entry_not_called(isolated_log, monkeypatch):
    """On lock failure, `_append_entry` must NOT be called for a new
    sprint_cut entry — pin the no-write wire-up directly."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)
    assert calls["n"] == 0, (
        f"_append_entry must not be called on lock failure; "
        f"got {calls['n']} call(s)"
    )


def test_lock_failure_existing_sprint_cut_still_authoritative(
    isolated_log,
):
    """The prior cut remains authoritative on derive_state after a
    failed re-cut (since nothing was written).

    Story 3 cascade: re-cut N reduced from 5 to 4 so the range
    precondition (operating over currently-planned count = 4 after
    sids[0] goes in_progress) doesn't preempt the lock check. The
    lock then fires on sids[0] (in_progress in the prior cohort)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)
    state = sm.derive_state()
    # Prior cut still wins.
    assert state["sprint_cut"] == 3


def test_lock_failure_story_states_still_intact(isolated_log):
    """Failed re-cut does not corrupt the story_states map."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[1], "planned", "in_progress")
    states_before = dict(sm.derive_state()["story_states"])
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)
    states_after = dict(sm.derive_state()["story_states"])
    assert states_before == states_after
    # And specifically: sids[1] is still in_progress.
    assert states_after[sids[1]] == "in_progress"


# ===========================================================================
# CLI surface (2+) — subcommand still works; lock failure exits non-zero
# ===========================================================================


def test_cli_sprint_cut_subcommand_still_known(tmp_path):
    """`python -m sm sprint-cut <N>` is still a recognized subcommand
    after Story 12 — Story 12 must not break the CLI surface."""
    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")

    result = subprocess.run(
        [sys.executable, "-m", "sm", "sprint-cut", "1"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must still recognize 'sprint-cut' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_lock_failure_exits_nonzero(tmp_path):
    """Lock failure on a re-cut → CLI exits non-zero, NOT 'unknown
    command'."""
    import sm

    log_path = tmp_path / "cli_log.jsonl"

    # Seed: open iteration, backlog, do a successful first cut, craft a
    # state change that LOCKS the in-sprint set.
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration(iteration_id="cli-iter-lock")
        sids = _seed_backlog(n=5)
        sm.sprint_cut(3)
        _craft_state_change(sids[0], "planned", "in_progress")
    finally:
        sm.LOG_PATH = orig_log

    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(log_path)

    result = subprocess.run(
        [sys.executable, "-m", "sm", "sprint-cut", "2"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"sprint-cut re-cut under lock must exit non-zero;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'sprint-cut' and fail with the lock path, "
        f"not 'unknown command';\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_lock_failure_writes_nothing(tmp_path):
    """A locked re-cut on the CLI leaves the log byte-for-byte
    unchanged."""
    import sm

    log_path = tmp_path / "cli_log.jsonl"

    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration(iteration_id="cli-iter-lock-2")
        sids = _seed_backlog(n=5)
        sm.sprint_cut(3)
        _craft_state_change(sids[0], "planned", "in_progress")
    finally:
        sm.LOG_PATH = orig_log

    bytes_before = log_path.read_bytes()

    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(log_path)

    result = subprocess.run(
        [sys.executable, "-m", "sm", "sprint-cut", "4"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"locked CLI re-cut must exit non-zero; got "
        f"returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    bytes_after = log_path.read_bytes()
    assert bytes_after == bytes_before, (
        "log must be byte-for-byte unchanged on a failed CLI lock-recut"
    )


def test_cli_allowed_recut_still_exits_zero(tmp_path):
    """A re-cut under the lock when the in-sprint cohort is terminal is
    allowed — CLI exits 0.

    iter4-multisprint-v2 Story 1 cascade: terminal-resolve the
    in-sprint cohort (sids[0..2]) before the CLI re-cut so the relaxed
    lock permits it.

    Story 3 cascade: backlog expanded from 5 to 7 so 4 planned remain
    after the first cohort goes terminal (CLI re-cut is `sprint-cut 4`)."""
    import sm

    log_path = tmp_path / "cli_log.jsonl"

    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration(iteration_id="cli-iter-recut-ok")
        sids = _seed_backlog(n=7)
        sm.sprint_cut(3)
        # Terminal-resolve the in-sprint cohort so the relaxed lock
        # permits the re-cut.
        _resolve_to_force_closed(sids[:3])
    finally:
        sm.LOG_PATH = orig_log

    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(log_path)

    result = subprocess.run(
        [sys.executable, "-m", "sm", "sprint-cut", "4"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"unlocked re-cut on CLI must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ===========================================================================
# Lock-uses-replay-derived-state pin (no separate flag persisted)
# ===========================================================================


def test_lock_uses_derive_state_not_separate_flag(isolated_log,
                                                  monkeypatch):
    """The lock check must derive its state via derive_state() — no
    separate persisted flag. Pin this by counting derive_state calls
    during a re-cut attempt: at least one call must happen.

    This is a structural pin: if the implementation ever reads a
    persisted lock flag instead of replaying, this test breaks.
    """
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")

    calls = {"n": 0}
    real = sm.derive_state

    def fake():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(sm, "derive_state", fake)

    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)

    assert calls["n"] >= 1, (
        "sprint_cut must call derive_state() to perform the lock check; "
        f"got {calls['n']} call(s)"
    )


def test_no_persisted_lock_flag_in_log(isolated_log):
    """After a locked re-cut attempt, no NEW entry types ('sprint_cut_
    locked', 'lock_flag', etc.) should appear on the log. Pins 'no
    separate flag persisted' — the lock is replay-derived only."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    types_before = [e.get("type") for e in sm.read_entries()]
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)
    types_after = [e.get("type") for e in sm.read_entries()]
    assert types_before == types_after, (
        "no entries (lock flag or otherwise) should be persisted on a "
        "locked re-cut"
    )


# ===========================================================================
# Cross-iteration sanity (1) — after iteration_close, lock is moot
# ===========================================================================


def test_lock_does_not_apply_after_iteration_close(isolated_log):
    """If the iteration is closed, sprint_cut(N) raises SprintCutError
    (no active iteration) NOT SprintCutLockedError — the lock is only a
    concern within an active iteration."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")

    # Close the iteration.
    close = sm.build_entry("iteration_close", {
        "closed_by": "operator", "reason": "wrap",
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 1,
    })
    sm._append_entry(close)

    # Now sprint_cut must raise SprintCutError (no active iteration). It
    # may or may not be SprintCutLockedError — but it MUST be at least
    # SprintCutError, and it must not write a new sprint_cut entry.
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(2)


# ===========================================================================
# Round-trip sanity for an allowed re-cut entry (1)
# ===========================================================================


def test_allowed_recut_entry_round_trips_through_read_entries(
    isolated_log,
):
    """An allowed re-cut writes a normal sprint_cut entry that round-trips
    through read_entries() unchanged — Story 11's contract still holds.

    iter4-multisprint-v2 Story 1 cascade: terminal-resolve sids[0..2]
    so the relaxed lock permits the re-cut.

    Story 3 cascade: backlog expanded from 5 to 7 so 4 planned remain
    after the first cohort goes terminal."""
    import sm
    sids = _seed_full(n_stories=7)
    sm.sprint_cut(3)
    _resolve_to_force_closed(sids[:3])
    second = sm.sprint_cut(4)
    entries = list(sm.read_entries())
    assert second == entries[-1]
    # And it survives a json round-trip.
    assert json.loads(json.dumps(second)) == second
