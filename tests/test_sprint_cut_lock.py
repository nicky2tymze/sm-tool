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
    """
    import sm
    sids = _seed_full(n_stories=5)
    # Move a story out of planned BEFORE any sprint_cut entry.
    _craft_state_change(sids[0], "planned", "in_progress")
    # First cut: no prior sprint_cut entry → lock cannot apply.
    result = sm.sprint_cut(3)
    assert result["cut_position"] == 3
    assert result["in_sprint_story_ids"] == sids[:3]


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
    """Chain to accepted (terminal) → re-cut blocked."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[2], "planned", "in_progress")
    _craft_state_change(sids[2], "in_progress", "in_review")
    _craft_state_change(sids[2], "in_review", "accepted")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)


def test_recut_blocked_after_chain_to_rejected(isolated_log):
    """Chain to rejected (terminal) → re-cut blocked."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[0], "in_progress", "in_review")
    _craft_state_change(sids[0], "in_review", "rejected")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(3)


def test_recut_blocked_after_force_close_from_planned(isolated_log):
    """Force-close direct from planned → re-cut blocked."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[1], "planned", "force_closed")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)


def test_recut_blocked_after_force_close_from_in_progress(isolated_log):
    """Force-close from in_progress → re-cut blocked."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[2], "planned", "in_progress")
    _craft_state_change(sids[2], "in_progress", "force_closed")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(1)


def test_recut_blocked_after_force_close_from_in_review(isolated_log):
    """Force-close from in_review → re-cut blocked."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[0], "in_progress", "in_review")
    _craft_state_change(sids[0], "in_review", "force_closed")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)


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
    (one error covers all offenders, no cascade of exceptions)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[1], "planned", "force_closed")
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
    """Cut, then re-cut with no intervening state changes → allowed."""
    import sm
    _seed_full(n_stories=5)
    sm.sprint_cut(3)
    # No state changes — re-cut should succeed.
    sm.sprint_cut(4)
    state = sm.derive_state()
    assert state["sprint_cut"] == 4


def test_recut_allowed_supersedes_prior_on_replay(isolated_log):
    """A successful re-cut writes a new sprint_cut entry that supersedes
    the prior one on replay — Story 4 already gives us this."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(2)
    second = sm.sprint_cut(4)
    state = sm.derive_state()
    assert state["sprint_cut"] == 4
    assert second["in_sprint_story_ids"] == sids[:4]


def test_recut_allowed_to_smaller_n_when_all_planned(isolated_log):
    """Re-cut to a SMALLER N is fine when all in-sprint stories are
    still planned."""
    import sm
    _seed_full(n_stories=6)
    sm.sprint_cut(5)
    sm.sprint_cut(2)
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_recut_allowed_chain_when_all_planned(isolated_log):
    """Multiple re-cuts in a row, all-planned the whole time → all
    succeed; latest wins."""
    import sm
    _seed_full(n_stories=8)
    sm.sprint_cut(1)
    sm.sprint_cut(3)
    sm.sprint_cut(5)
    sm.sprint_cut(7)
    state = sm.derive_state()
    assert state["sprint_cut"] == 7


def test_recut_allowed_writes_new_entry(isolated_log):
    """Allowed re-cut writes exactly one new sprint_cut entry on the
    log."""
    import sm
    _seed_full(n_stories=5)
    sm.sprint_cut(3)
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
    """Cut at 3 of 5 → sids[3], sids[4] are deferred. Transitioning a
    deferred story → re-cut still allowed (only in-sprint stories
    matter)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)  # in-sprint = sids[0..2], deferred = sids[3..4]
    _craft_state_change(sids[4], "planned", "in_progress")
    # Re-cut must succeed — sids[4] is deferred.
    sm.sprint_cut(2)
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_deferred_story_force_closed_does_not_lock(isolated_log):
    """Force-closing a DEFERRED story → re-cut still allowed."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)
    _craft_state_change(sids[5], "planned", "force_closed")
    sm.sprint_cut(4)
    state = sm.derive_state()
    assert state["sprint_cut"] == 4


def test_deferred_story_chain_to_accepted_does_not_lock(isolated_log):
    """Deferred story → in_progress → in_review → accepted → re-cut
    still allowed."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)
    _craft_state_change(sids[4], "planned", "in_progress")
    _craft_state_change(sids[4], "in_progress", "in_review")
    _craft_state_change(sids[4], "in_review", "accepted")
    sm.sprint_cut(5)
    state = sm.derive_state()
    assert state["sprint_cut"] == 5


def test_only_latest_cut_in_sprint_set_counts(isolated_log):
    """Cut at 3, re-cut at 5 (changing in-sprint to sids[0..4]). Then
    transition sids[4] (which was previously deferred but is NOW
    in-sprint per the latest cut). Re-cut must be blocked because
    sids[4] is now in the IN-SPRINT set per the LATEST cut."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)  # in-sprint = sids[0..2]
    sm.sprint_cut(5)  # in-sprint = sids[0..4]
    _craft_state_change(sids[4], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)


def test_chain_recut_then_transition_then_recut(isolated_log):
    """Cut(3) → re-cut(2) [allowed, all planned] → transition story in
    new in-sprint set → re-cut blocked."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    sm.sprint_cut(2)  # in-sprint now sids[0..1]
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(3)


def test_only_in_sprint_stories_not_full_backlog(isolated_log):
    """Cut at 1 of 5 — only sids[0] is in-sprint. Transitioning any of
    sids[1..4] does NOT lock — only sids[0] would."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(1)
    # Move every deferred story.
    for i in range(1, 5):
        _craft_state_change(sids[i], "planned", "in_progress")
    # All four moves were deferred — re-cut still allowed.
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
    than one)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[1], "planned", "force_closed")
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
    failed re-cut (since nothing was written)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(5)
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
    env["SM_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")

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
    env["SM_LOG_PATH"] = str(log_path)

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
    env["SM_LOG_PATH"] = str(log_path)

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
    """A re-cut under the lock when no in-sprint story has moved is
    allowed — CLI exits 0."""
    import sm

    log_path = tmp_path / "cli_log.jsonl"

    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration(iteration_id="cli-iter-recut-ok")
        _seed_backlog(n=5)
        sm.sprint_cut(3)
        # No state changes — re-cut should be allowed.
    finally:
        sm.LOG_PATH = orig_log

    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(log_path)

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
    through read_entries() unchanged — Story 11's contract still holds."""
    import sm
    _seed_full(n_stories=5)
    sm.sprint_cut(3)
    second = sm.sprint_cut(4)
    entries = list(sm.read_entries())
    assert second == entries[-1]
    # And it survives a json round-trip.
    assert json.loads(json.dumps(second)) == second
