"""iter4-multisprint-v2 Story 1 — relax sprint-cut lock to terminal-only.

Story 1 (Sprint 1, size M, req-1) pivots the sprint-cut lock from
"any non-planned state blocks re-cut" (Iter 1 / Story 12 semantics) to
"only non-terminal in-sprint stories block re-cut". The result: multiple
sprint_cut entries can coexist within a single iteration, as long as the
prior in-sprint cohort has been fully resolved (accepted, rejected, or
force_closed) before the next cut.

What this file pins:

  - Lock acceptance rule (relaxed):
      * `sprint_cut(N)` accepts a re-cut when EVERY story_id in the
        LATEST prior sprint_cut entry's `in_sprint_story_ids` has a
        current state in {accepted, rejected, force_closed}.
      * The mix of terminal states is irrelevant — any combination of
        the three terminal states permits the re-cut.

  - Lock rejection rule (narrowed):
      * `sprint_cut(N)` raises `SprintCutLockedError` when ANY story_id
        in the LATEST prior sprint_cut entry's `in_sprint_story_ids`
        has a current state that is NOT terminal.
      * Non-terminal = anything not in {accepted, rejected,
        force_closed}, which includes {planned, in_progress, in_review}.
      * The error message names each offender and (where applicable)
        its current state, so the operator can resolve the lock.

  - Design decision — `planned` blocks re-cut:
      The story's AC enumerates the BLOCKING set as
      {in_progress, in_review} and the ACCEPTING set as
      {accepted, rejected, force_closed}, leaving `planned` ambiguous.
      The TestWriter pins the conservative interpretation:
      `planned` is non-terminal and BLOCKS the re-cut. This avoids
      orphaning a story that was cut but never started — re-cutting
      with a still-planned prior story would silently move the in-
      sprint cohort while a planned story sits in limbo. The operator
      must explicitly resolve every in-sprint story to a terminal
      state before re-cutting.

  - First-cut behavior unchanged:
      * With no prior `sprint_cut` entry, the lock check is a no-op.
      * The first cut in an iteration proceeds unconditionally,
        regardless of any state_change entries on the log.

  - Failure invariants (lock failure):
      * `SprintCutLockedError` raised — same class as Iter 1, narrowed
        triggering condition.
      * Log is byte-for-byte unchanged after the failed call.
      * No new `sprint_cut` entry appended.
      * `_append_entry` not called for any sprint_cut entry.
      * `derive_state()` is unchanged byte-for-byte.

  - Lock applies only to in-sprint stories (preserved from Story 12):
      * Deferred stories (NOT in the LATEST sprint_cut's
        `in_sprint_story_ids`) do NOT count toward the lock check.

  - Error class identity preserved:
      * `SprintCutLockedError` is still raised (NOT a new exception
        class). Existing `except SprintCutLockedError` callers — and
        transitively `except SprintCutError` / `except ValueError`
        callers — continue to catch lock failures unchanged.

What this file does NOT pin (deferred to later stories):

  - Story 2: derive_state's `sprint_cut` field semantics under
    multiple cuts (LATEST wins / cut_position semantics).
  - Story 3: cut_position N as absolute backlog index vs running offset.
  - Story 4: close_iteration's validation interaction with multiple
    sprint_cuts in one iteration.

Tests must FAIL on first run under the existing Iter 1 implementation —
the Iter 1 lock fires on ANY non-planned state (including the terminal
states {accepted, rejected, force_closed} that the new semantics treat
as the ACCEPTING set). The Coder downstream relaxes the lock check to
the terminal-only condition to satisfy these tests.

Cascade tests in `test_sprint_cut_lock.py` that will FAIL under the new
semantics (flagged for the Coder to update — they pin the Iter 1
behavior verbatim, and the iter4-multisprint-v2 pivot intentionally
changes that behavior):

  1. `test_recut_blocked_after_chain_to_accepted`
     (terminal `accepted` is now in the ACCEPTING set, not blocking)
  2. `test_recut_blocked_after_chain_to_rejected`
     (terminal `rejected` is now in the ACCEPTING set)
  3. `test_recut_blocked_after_force_close_from_planned`
     (terminal `force_closed` is now in the ACCEPTING set)
  4. `test_recut_blocked_after_force_close_from_in_progress`
     (same — destination is `force_closed`)
  5. `test_recut_blocked_after_force_close_from_in_review`
     (same — destination is `force_closed`)
  6. `test_recut_blocked_when_multiple_in_sprint_moved`
     (uses a force_closed offender — under new semantics that offender
      no longer blocks; the in_progress offenders still do)

These are tests-only updates and Coder owns the rewrite.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import uuid as _uuid

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers (mirrors test_sprint_cut_lock.py conventions)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file. Suite convention."""
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
    """Bypass the per-story command — directly write a
    `story_state_change` entry. `derive_state` already reads these
    (Story 4). Mirrors the helper in `test_sprint_cut_lock.py`."""
    import sm

    entry = sm.build_entry("story_state_change", {
        "story_id": story_id,
        "from_state": from_state,
        "to_state": to_state,
        "notes": "test fixture",
    })
    sm._append_entry(entry)
    return entry


def _drive_to_accepted(story_id: str) -> None:
    """Walk a story planned -> in_progress -> in_review -> accepted via
    crafted state_change entries (the per-story state-machine writer
    has its own gates; this bypasses them and writes the change events
    directly, which is the same pattern test_sprint_cut_lock.py uses)."""
    _craft_state_change(story_id, "planned", "in_progress")
    _craft_state_change(story_id, "in_progress", "in_review")
    _craft_state_change(story_id, "in_review", "accepted")


def _drive_to_rejected(story_id: str) -> None:
    """Walk a story planned -> in_progress -> in_review -> rejected."""
    _craft_state_change(story_id, "planned", "in_progress")
    _craft_state_change(story_id, "in_progress", "in_review")
    _craft_state_change(story_id, "in_review", "rejected")


def _drive_to_force_closed(story_id: str) -> None:
    """Force-close direct from planned (legal per _VALID_TRANSITIONS)."""
    _craft_state_change(story_id, "planned", "force_closed")


# ===========================================================================
# A. First-cut behavior unchanged (smoke, 5 tests)
# ===========================================================================


def test_first_cut_succeeds_with_no_prior(isolated_log):
    """No prior sprint_cut entry → first cut proceeds without the lock
    check (preserved from Iter 1)."""
    import sm
    _seed_full(n_stories=5)
    result = sm.sprint_cut(3)
    assert result["cut_position"] == 3


def test_first_cut_succeeds_even_with_state_changes_on_log(isolated_log):
    """State_change entries can exist on the log before the first cut
    (e.g. for stories that aren't yet in any sprint set). The first
    cut still proceeds unconditionally — the lock fires only on RE-cut."""
    import sm
    sids = _seed_full(n_stories=5)
    _craft_state_change(sids[0], "planned", "in_progress")
    result = sm.sprint_cut(3)
    assert result["cut_position"] == 3
    assert result["in_sprint_story_ids"] == sids[:3]


def test_first_cut_does_not_raise_locked_error(isolated_log):
    """First cut MUST NOT raise SprintCutLockedError under any
    circumstance — lock applies only when a prior sprint_cut entry
    already exists."""
    import sm
    sids = _seed_full(n_stories=5)
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[0], "in_progress", "in_review")
    # No SprintCutLockedError on the first cut.
    sm.sprint_cut(3)


def test_first_cut_writes_one_entry(isolated_log):
    """First cut writes exactly one new sprint_cut entry."""
    import sm
    _seed_full(n_stories=5)
    before = list(sm.read_entries())
    sm.sprint_cut(3)
    after = list(sm.read_entries())
    assert len(after) == len(before) + 1


def test_first_cut_records_cut_position_and_membership(isolated_log):
    """The first cut writes the expected cut_position, in_sprint, and
    deferred lists — Story 11's contract holds unchanged in this story."""
    import sm
    sids = _seed_full(n_stories=6)
    result = sm.sprint_cut(4)
    assert result["cut_position"] == 4
    assert result["in_sprint_story_ids"] == sids[:4]
    assert result["deferred_story_ids"] == sids[4:]


# ===========================================================================
# B. Re-cut accepted when prior in-sprint stories all terminal (10 tests)
# ===========================================================================


def test_recut_allowed_when_all_in_sprint_accepted(isolated_log):
    """All prior in-sprint stories accepted → re-cut accepted."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)  # in-sprint = sids[0..2]
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    # Re-cut must succeed — all in-sprint terminal.
    result = sm.sprint_cut(4)
    assert result["cut_position"] == 4


def test_recut_allowed_when_all_in_sprint_rejected(isolated_log):
    """All prior in-sprint stories rejected → re-cut accepted."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_rejected(sid)
    result = sm.sprint_cut(2)
    assert result["cut_position"] == 2


def test_recut_allowed_when_all_in_sprint_force_closed(isolated_log):
    """All prior in-sprint stories force_closed → re-cut accepted."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_force_closed(sid)
    result = sm.sprint_cut(5)
    assert result["cut_position"] == 5


def test_recut_allowed_when_terminal_mix_accepted_and_rejected(
    isolated_log,
):
    """Mix of accepted + rejected in the prior in-sprint cohort →
    re-cut accepted."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(4)
    _drive_to_accepted(sids[0])
    _drive_to_rejected(sids[1])
    _drive_to_accepted(sids[2])
    _drive_to_rejected(sids[3])
    result = sm.sprint_cut(5)
    assert result["cut_position"] == 5


def test_recut_allowed_when_terminal_mix_all_three_states(isolated_log):
    """Mix of accepted + rejected + force_closed in the prior in-sprint
    cohort → re-cut accepted. The three terminal states are
    interchangeable for the lock check."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _drive_to_rejected(sids[1])
    _drive_to_force_closed(sids[2])
    result = sm.sprint_cut(5)
    assert result["cut_position"] == 5


def test_recut_writes_new_sprint_cut_entry(isolated_log):
    """A successful re-cut after terminal resolution writes a new
    sprint_cut entry (so two coexist on the log within one iteration)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    cut_count_before = sum(
        1 for e in sm.read_entries() if e.get("type") == "sprint_cut"
    )
    sm.sprint_cut(4)
    cut_count_after = sum(
        1 for e in sm.read_entries() if e.get("type") == "sprint_cut"
    )
    assert cut_count_after == cut_count_before + 1
    assert cut_count_after == 2  # original + new


def test_recut_in_sprint_membership_reflects_new_cut(isolated_log):
    """After a permitted re-cut, the new entry's in_sprint_story_ids
    reflects the new N (the multisprint enabler — Story 11 cut_position
    semantics preserved for this story)."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_force_closed(sid)
    second = sm.sprint_cut(5)
    # Story 1 preserves current cut_position semantics — Story 3 will
    # update them. For this story we pin only that the new entry's
    # cut_position and membership are as Iter 1 produces.
    assert second["cut_position"] == 5
    assert second["in_sprint_story_ids"] == sids[:5]


def test_recut_does_not_raise_locked_error_when_all_terminal(
    isolated_log,
):
    """Explicit assertion: re-cut after terminal resolution must NOT
    raise SprintCutLockedError. Belt and suspenders for the relaxation."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _drive_to_rejected(sids[1])
    _drive_to_force_closed(sids[2])
    # Must not raise.
    sm.sprint_cut(4)


def test_recut_returns_dict_with_canonical_fields(isolated_log):
    """The permitted re-cut returns a dict with build_entry's canonical
    fields (id, type, timestamp)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    result = sm.sprint_cut(4)
    assert isinstance(result, dict)
    assert "id" in result
    assert result["type"] == "sprint_cut"
    assert "timestamp" in result


def test_recut_allowed_when_all_terminal_via_force_close_from_in_review(
    isolated_log,
):
    """force_closed from in_review (legal per _VALID_TRANSITIONS) →
    still terminal → re-cut accepted."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _craft_state_change(sid, "planned", "in_progress")
        _craft_state_change(sid, "in_progress", "in_review")
        _craft_state_change(sid, "in_review", "force_closed")
    result = sm.sprint_cut(4)
    assert result["cut_position"] == 4


def test_recut_chain_with_terminal_resolution_each_round(isolated_log):
    """Multi-round multisprint: cut, resolve, recut, resolve, recut.
    Each round resolves to terminal then re-cuts; all succeed."""
    import sm
    sids = _seed_full(n_stories=6)
    # Round 1.
    sm.sprint_cut(2)
    _drive_to_accepted(sids[0])
    _drive_to_accepted(sids[1])
    # Round 2 — bigger cut.
    sm.sprint_cut(4)
    # sids[0..3] now in-sprint; sids[0..1] already terminal. Resolve
    # sids[2..3] to terminal.
    _drive_to_rejected(sids[2])
    _drive_to_force_closed(sids[3])
    # Round 3 — even bigger cut.
    result = sm.sprint_cut(6)
    assert result["cut_position"] == 6


# ===========================================================================
# C. Re-cut rejected when any in-sprint story non-terminal (12 tests)
# ===========================================================================


def test_recut_blocked_when_one_in_progress(isolated_log):
    """One in_progress story in the prior cohort → raises
    SprintCutLockedError."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)


def test_recut_blocked_when_one_in_review(isolated_log):
    """One in_review story in the prior cohort → raises
    SprintCutLockedError."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[1], "planned", "in_progress")
    _craft_state_change(sids[1], "in_progress", "in_review")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)


def test_recut_blocked_when_one_planned(isolated_log):
    """DESIGN DECISION: planned is non-terminal — blocks re-cut.

    The AC enumerates the accepting set as {accepted, rejected,
    force_closed}, so a story still in `planned` does NOT satisfy
    'all prior terminal'. Blocking is the safe default — re-cutting
    while a story sits planned would orphan it.

    Setup: two prior in-sprint stories — one terminal, one still
    planned. The planned story alone must trigger the lock.
    """
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    # Resolve sids[0] and sids[1] to terminal but leave sids[2] planned.
    _drive_to_accepted(sids[0])
    _drive_to_rejected(sids[1])
    # sids[2] is still in `planned` (no state_change entry).
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)


def test_recut_blocked_when_all_prior_still_planned(isolated_log):
    """Pure planned cohort: no in-sprint story has any state_change
    entry, all are still planned. Under the new terminal-only semantics,
    planned blocks → re-cut rejected.

    (Under Iter 1 semantics, this case ALLOWED the re-cut because no
    story had left planned. This test pins the design flip: the new
    semantics demand affirmative terminal resolution, not 'still
    untouched'.)
    """
    import sm
    _seed_full(n_stories=5)
    sm.sprint_cut(3)
    # No state changes at all — all in-sprint stories still planned.
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)


def test_recut_blocked_when_mix_of_in_progress_and_terminal(isolated_log):
    """Mixed cohort: some terminal, some in_progress → lock fires
    because at least one offender exists."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _craft_state_change(sids[1], "planned", "in_progress")  # offender
    _drive_to_force_closed(sids[2])
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(4)


def test_recut_blocked_when_mix_of_in_review_and_terminal(isolated_log):
    """Mixed cohort with an in_review offender among terminals."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _craft_state_change(sids[1], "planned", "in_progress")
    _craft_state_change(sids[1], "in_progress", "in_review")  # offender
    _drive_to_rejected(sids[2])
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)


def test_error_message_names_single_offender(isolated_log):
    """Lock error names the offending story_id."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    offender = sids[1]
    _craft_state_change(offender, "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError) as exc_info:
        sm.sprint_cut(2)
    msg = str(exc_info.value)
    assert offender in msg, (
        f"error must name offender {offender!r}; got: {exc_info.value!s}"
    )


def test_error_message_names_all_offenders(isolated_log):
    """Multiple non-terminal stories → error names ALL of them, not
    just the first."""
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


def test_error_message_names_offender_states(isolated_log):
    """The AC requires the message to name the offenders 'and their
    states'. Pin that the offending state appears somewhere in the
    message (so the operator knows what action resolves it)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError) as exc_info:
        sm.sprint_cut(2)
    msg = str(exc_info.value)
    assert "in_progress" in msg, (
        "error must name the offender's state ('in_progress'); "
        f"got: {exc_info.value!s}"
    )


def test_error_class_is_sprint_cut_locked_error(isolated_log):
    """The error class is `SprintCutLockedError` — same class as
    Iter 1's lock failure (existing class name unchanged)."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError) as exc_info:
        sm.sprint_cut(2)
    # Exact class — not a sibling or a subclass.
    assert exc_info.type is sm.SprintCutLockedError


def test_error_class_still_subclass_of_sprint_cut_error(isolated_log):
    """SprintCutLockedError is still a subclass of SprintCutError so
    existing `except SprintCutError` callers keep catching it."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    caught_as_parent = False
    try:
        sm.sprint_cut(2)
    except sm.SprintCutError:
        caught_as_parent = True
    assert caught_as_parent, (
        "SprintCutLockedError must still be catchable as SprintCutError"
    )


def test_log_unchanged_on_lock(isolated_log):
    """Lock failure → log byte-for-byte unchanged. Preserves Iter 1's
    failure invariant."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(2)
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# D. Edge cases (7 tests)
# ===========================================================================


def test_no_prior_cut_no_lock_check(isolated_log):
    """No prior sprint_cut entry → lock check skipped entirely. Pin
    this even when there are state_changes on the log (mimicking a
    pre-cut transition scenario — should not affect the first cut)."""
    import sm
    sids = _seed_full(n_stories=5)
    # State change with no prior cut — first cut still proceeds.
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[0], "in_progress", "in_review")
    result = sm.sprint_cut(3)
    assert result["cut_position"] == 3


def test_deferred_story_in_progress_does_not_lock(isolated_log):
    """Lock applies only to in-sprint stories. A deferred story being
    in_progress (legal in multisprint flows if it was previously
    in-sprint and got force-closed... but more practically: a story
    NOT in the latest sprint_cut's in_sprint_story_ids does not count
    toward the lock).

    Setup: cut at 3 of 6; resolve sids[0..2] to accepted; transition
    a deferred story (sids[4]) to in_progress. Re-cut must succeed
    because only the latest in_sprint cohort is checked.
    """
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    _craft_state_change(sids[4], "planned", "in_progress")
    # Re-cut must succeed — sids[4] is deferred per the latest cut.
    result = sm.sprint_cut(4)
    assert result["cut_position"] == 4


def test_deferred_story_in_review_does_not_lock(isolated_log):
    """Same as above, but the deferred story is in in_review. Still
    doesn't count toward the lock — only the in-sprint cohort matters."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_rejected(sid)
    _craft_state_change(sids[5], "planned", "in_progress")
    _craft_state_change(sids[5], "in_progress", "in_review")
    result = sm.sprint_cut(5)
    assert result["cut_position"] == 5


def test_only_latest_prior_cut_in_sprint_set_counts(isolated_log):
    """When multiple prior sprint_cut entries exist (multisprint!), the
    lock check uses the LATEST one's in_sprint_story_ids — not any
    earlier cut's membership.

    Setup: cut(2) → resolve sids[0..1] to terminal → cut(4) [new
    in-sprint = sids[0..3]] → transition sids[3] to in_progress →
    re-cut blocked (sids[3] is in the LATEST cut's cohort)."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(2)
    _drive_to_accepted(sids[0])
    _drive_to_accepted(sids[1])
    sm.sprint_cut(4)  # latest cut: in-sprint = sids[0..3]
    _craft_state_change(sids[2], "planned", "in_progress")
    with pytest.raises(sm.SprintCutLockedError):
        sm.sprint_cut(5)


def test_only_latest_cohort_checked_earlier_cohort_ignored(isolated_log):
    """Mirror of above: when the LATEST cohort is all-terminal, an
    earlier cohort's state is irrelevant.

    cut(2) → resolve sids[0..1] → cut(4) [latest cohort = sids[0..3]]
    → resolve sids[2..3] to terminal → re-cut allowed. sids[0..1] are
    already terminal from before; sids[2..3] are now terminal too —
    the LATEST cohort is fully resolved."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(2)
    _drive_to_accepted(sids[0])
    _drive_to_accepted(sids[1])
    sm.sprint_cut(4)
    _drive_to_rejected(sids[2])
    _drive_to_force_closed(sids[3])
    result = sm.sprint_cut(6)
    assert result["cut_position"] == 6


def test_no_active_iteration_takes_priority_over_lock(isolated_log):
    """If the iteration is closed between cuts, sprint_cut raises
    SprintCutError (no active iteration), NOT SprintCutLockedError.
    The active-iteration precondition fires before the lock check."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")

    # Close the iteration (force-close style — sids[0] is in_progress).
    # For this test we synthesize a close entry directly.
    close = sm.build_entry("iteration_close", {
        "closed_by": "operator", "reason": "wrap",
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 1,
    })
    sm._append_entry(close)

    # Now sprint_cut(N) must raise SprintCutError (no active iteration)
    # — the lock is moot. SprintCutLockedError WOULD be a subclass match,
    # but the implementation must check active_iteration first.
    with pytest.raises(sm.SprintCutError) as exc_info:
        sm.sprint_cut(2)
    # If it's the locked variant, the active-iteration check came too
    # late — the no-iteration message must be present.
    msg = str(exc_info.value).lower()
    assert ("iteration" in msg or "active" in msg), (
        f"error must mention active-iteration precondition; "
        f"got: {exc_info.value!s}"
    )


def test_type_error_takes_priority_over_lock(isolated_log):
    """Type validation runs before the lock check. A non-int N raises
    TypeError even when the lock would otherwise fire."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(TypeError):
        sm.sprint_cut("2")  # non-int — TypeError, not lock error.


def test_range_error_takes_priority_over_lock(isolated_log):
    """Range validation (N out of [1, len(backlog)]) runs before the
    lock check. An out-of-range N raises SprintCutError (range), not
    SprintCutLockedError, even when the lock would otherwise fire."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    with pytest.raises(sm.SprintCutError) as exc_info:
        sm.sprint_cut(99)  # > backlog length.
    # Must be a range error (mentions position or backlog), not the
    # lock message. SprintCutLockedError is a subclass so we have to
    # check the message itself.
    msg = str(exc_info.value).lower()
    assert ("exceed" in msg or "position" in msg or "backlog" in msg), (
        f"out-of-range N must produce a range error, not the lock "
        f"error; got: {exc_info.value!s}"
    )


# ===========================================================================
# E. Behavioral preservation (5 tests) — Iter 1 invariants still hold
# ===========================================================================


def test_lock_failure_does_not_call_append_entry(isolated_log,
                                                  monkeypatch):
    """Pin the no-write invariant directly: _append_entry must NOT be
    called on a lock failure. Preserved from Story 12."""
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


def test_lock_failure_no_new_sprint_cut_entry(isolated_log):
    """Lock failure → no new sprint_cut entry on the log."""
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


def test_sprint_cut_locked_error_class_unchanged_in_all(isolated_log):
    """SprintCutLockedError is still exported via __all__ — Story 1
    must not remove it."""
    import sm
    assert "SprintCutLockedError" in sm.__all__, (
        f"SprintCutLockedError must remain in __all__; "
        f"got {sm.__all__!r}"
    )


def test_sprint_cut_locked_error_still_value_error(isolated_log):
    """Transitively, SprintCutLockedError still subclasses ValueError
    so bare `except ValueError` callers keep catching it."""
    import sm
    sids = _seed_full(n_stories=5)
    sm.sprint_cut(3)
    _craft_state_change(sids[0], "planned", "in_progress")
    caught_as_value_error = False
    try:
        sm.sprint_cut(2)
    except ValueError:
        caught_as_value_error = True
    assert caught_as_value_error


# ===========================================================================
# F. End-to-end multisprint sanity (2 tests) — the whole point of the
# iter4 pivot
# ===========================================================================


def test_two_sprints_in_one_iteration_round_trip(isolated_log):
    """The headline scenario: open iteration, cut, resolve, cut again,
    resolve again. Both sprint_cut entries land on the log. derive_state
    surfaces a sprint_cut (Story 2 will pin the exact semantics)."""
    import sm
    sids = _seed_full(n_stories=6)
    # First sprint.
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _drive_to_rejected(sids[1])
    _drive_to_force_closed(sids[2])
    # Second sprint.
    sm.sprint_cut(5)
    cuts = [e for e in sm.read_entries() if e.get("type") == "sprint_cut"]
    assert len(cuts) == 2, (
        f"expected 2 sprint_cut entries after a permitted re-cut; "
        f"got {len(cuts)}"
    )


def test_three_sprints_in_one_iteration_round_trip(isolated_log):
    """Triple sprint within one iteration. Pin that the lock relaxation
    scales beyond one re-cut."""
    import sm
    sids = _seed_full(n_stories=8)
    # Sprint 1.
    sm.sprint_cut(2)
    _drive_to_accepted(sids[0])
    _drive_to_accepted(sids[1])
    # Sprint 2 — cut grows; resolve only the newly-added stories.
    sm.sprint_cut(4)
    _drive_to_rejected(sids[2])
    _drive_to_force_closed(sids[3])
    # Sprint 3.
    sm.sprint_cut(7)
    cuts = [e for e in sm.read_entries() if e.get("type") == "sprint_cut"]
    assert len(cuts) == 3, (
        f"expected 3 sprint_cut entries after two permitted re-cuts; "
        f"got {len(cuts)}"
    )
