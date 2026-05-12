"""iter4-multisprint-v2 Sprint 1 Story 3 — pin cut_position semantics under
multi-sprint iteration.

Story:  Update cut_position semantics for multi-sprint iteration  (S, req-1)

Acceptance criteria (verbatim):
  - `sprint_cut(n)` interprets `n` as position within currently-planned
    stories only (stories already in terminal state from prior cuts do
    not count toward `n`).
  - `in_sprint_story_ids` contains only story_ids from the new cut
    (does not include stories from prior cuts).
  - `deferred_story_ids` contains all non-cut stories (planned +
    already-cut-and-terminal).

What this file pins:

  - **`n` is a count over CURRENTLY-PLANNED stories.**
      A story is currently-planned if it is in the backlog AND its
      lifecycle state is exactly `"planned"` (never moved into any
      sprint, or moved in and back out — though the current state
      machine has no reverse path, so in practice: never cut).
      Stories in prior cuts that are now terminal (`accepted`,
      `rejected`, `force_closed`) are NOT currently-planned and do
      NOT count toward `n`.

  - **`in_sprint_story_ids` is the new cut's cohort only.**
      After a re-cut, the entry's `in_sprint_story_ids` list contains
      ONLY the story_ids selected by this cut (the first N of the
      currently-planned stories, in sequence order). It does NOT
      contain story_ids from any prior sprint_cut entry's
      `in_sprint_story_ids`.

  - **`deferred_story_ids` is everything else in the backlog.**
      All non-cut stories: planned stories beyond the new cut PLUS
      story_ids from prior cuts that are now terminal. From the
      perspective of the ACTIVE sprint, those prior-cut stories are
      "deferred" (i.e., not in the active in-sprint cohort).
      Sequence order is preserved (backlog sequence ascending).

  - **Range precondition uses currently-planned count.**
      `n > currently_planned_count` raises `SprintCutError` (range
      error, no log write). `n == currently_planned_count` is the
      edge: all remaining planned stories enter the new sprint.
      `n < 1` (zero or negative) is still rejected by the existing
      precondition.

  - **`cut_position` field value semantics (TestWriter design choice):**
      The numeric `cut_position` field on the new sprint_cut entry
      equals N, the COUNT of stories in THIS cut (NOT the cumulative
      position in the original backlog).
      Rationale: matches the AC's "n as position within currently-
      planned stories"; the simplest interpretation; matches the field
      name's natural reading ("the position at which we cut the
      currently-planned list"); downstream consumers that need the
      cumulative original-backlog position can compute it by summing
      cut_positions of prior sprint_cut entries in the same iteration.

  - **First-cut behavior unchanged from Iter 1.**
      With no prior sprint_cut entry, every backlog story is still in
      `planned` state, so currently-planned count equals backlog
      length. `sprint_cut(N)` then writes
      `in_sprint_story_ids = backlog[:N]` and
      `deferred_story_ids = backlog[N:]` exactly as Iter 1 did.

  - **Stories from prior cuts that are terminal stay in their terminal
    state.** Their `story_state_change` history is preserved on the log
    and `state["story_states"]` reflects their terminal state. They
    are NOT re-introduced or rewound by a subsequent cut.

  - **Behavioral preservation: status/transition_story/etc. continue
    to read `in_sprint_story_ids` of the LATEST cut.** Existing
    consumers that look at the latest sprint_cut entry's
    `in_sprint_story_ids` keep working — the list now contains only
    the active cohort, which is exactly what those consumers want.

What this file does NOT pin (deferred to later stories):

  - Story 4: close_iteration's behavior when multiple sprint_cut
    entries exist in one iteration.
  - Story 5: _HELP_TEXT updates documenting multi-sprint semantics.

Tests must FAIL on first run under the post-Story-1/2 implementation —
the Iter 1 `sprint_cut` body still slices the FULL backlog from index 0,
so:
  - After a first cut of 3 (sids[0..2] now terminal) and a second
    `sprint_cut(4)`, the current code returns
    `in_sprint_story_ids = backlog[:4] = sids[0..3]` — WRONG; should
    be `sids[3..6]` (the first 4 currently-planned).
  - Same code returns `deferred_story_ids = backlog[4:] = sids[4..]` —
    WRONG; should be `sids[0..2]` (terminal from prior cut) PLUS the
    planned tail (sids[7..]).

The Coder downstream rewrites `sprint_cut`'s cohort-building math to
operate over currently-planned stories rather than the raw backlog.

Cascade tests flagged for Coder review (they pin Iter 1 single-cut
semantics that still hold for the FIRST cut in an iteration — they
should remain passing, but the Coder must verify):
  - `test_sprint_cut.py` tests for cut_position/in_sprint/deferred all
    seed a fresh backlog with no prior cut, so currently-planned ==
    full backlog and the math reduces to the Iter 1 form. No cascade
    expected.
  - `test_sprint_cut_multisprint_lock.py::
    test_recut_in_sprint_membership_reflects_new_cut` asserts
    `second["in_sprint_story_ids"] == sids[:5]` after first cut of 3
    (sids[0..2] now force_closed) and re-cut of 5. Under Story 3
    semantics, the second cut's `in_sprint_story_ids` is the first 5
    CURRENTLY-PLANNED (sids[3..7]) — NOT `sids[:5]`. This test
    EXPLICITLY DEFERS Story 3 ("Story 1 preserves current cut_position
    semantics — Story 3 will update them"), so it WILL FAIL and the
    Coder must update its assertion.
  - `test_sprint_cut_multisprint_lock.py::
    test_recut_chain_with_terminal_resolution_each_round` chains
    cuts (2, 4, 6). Under Iter 1 semantics each N was the absolute
    backlog index, so cut(4) covered sids[0..3]. Under Story 3
    semantics, cut(2) [sids 0..1] -> cut(4) [first 4 of remaining
    sids 2..5] -> cut(6) [first 6 of remaining sids 6..] — but the
    backlog only has 6 stories, so cut(6) would fail (only 0
    currently-planned remain after rounds 1+2 covered all 6).
    The Coder must rewrite this test's N values to fit the new
    semantics.

Suite baseline at write time: 2800/2800 (after Stories 1+2 closed).
"""

from __future__ import annotations

import json
import pathlib
import sys
import uuid as _uuid

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers (mirror test_sprint_cut_multisprint_lock.py conventions)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file."""
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _open_iteration(iteration_id: str = "iter-1",
                    requirements=None) -> dict:
    """Append an `iteration_open` entry directly."""
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
    """Bypass the per-story command and write a `story_state_change`
    entry directly. Mirrors the pattern used by
    `test_sprint_cut_multisprint_lock.py`."""
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
    """planned -> in_progress -> in_review -> accepted."""
    _craft_state_change(story_id, "planned", "in_progress")
    _craft_state_change(story_id, "in_progress", "in_review")
    _craft_state_change(story_id, "in_review", "accepted")


def _drive_to_rejected(story_id: str) -> None:
    """planned -> in_progress -> in_review -> rejected."""
    _craft_state_change(story_id, "planned", "in_progress")
    _craft_state_change(story_id, "in_progress", "in_review")
    _craft_state_change(story_id, "in_review", "rejected")


def _drive_to_force_closed(story_id: str) -> None:
    """Force-close direct from planned (legal per _VALID_TRANSITIONS)."""
    _craft_state_change(story_id, "planned", "force_closed")


def _resolve_cohort_to_terminal(story_ids) -> None:
    """Force-close every story_id in the iterable so the cohort is
    fully terminal and the next sprint_cut is permitted by the relaxed
    Story-1 lock."""
    for sid in story_ids:
        _drive_to_force_closed(sid)


# ===========================================================================
# A. Smoke for re-cut math (6 tests)
# ===========================================================================


def test_first_cut_unchanged_in_sprint_membership(isolated_log):
    """First cut in an iteration — currently-planned == full backlog.
    Math reduces to Iter 1: in_sprint = backlog[:N], deferred =
    backlog[N:]. This is the pre-existing behavior and Story 3 MUST
    preserve it for the first cut."""
    import sm
    sids = _seed_full(n_stories=10)
    result = sm.sprint_cut(3)
    assert result["in_sprint_story_ids"] == sids[:3], (
        f"first cut in_sprint_story_ids should be backlog[:3]; "
        f"got {result['in_sprint_story_ids']!r}"
    )
    assert result["deferred_story_ids"] == sids[3:], (
        f"first cut deferred_story_ids should be backlog[3:]; "
        f"got {result['deferred_story_ids']!r}"
    )


def test_recut_picks_next_currently_planned_stories(isolated_log):
    """Headline example from the story prompt.

    Backlog of 10. First cut of 3 -> sids[0..2] go in-sprint, all
    accepted (terminal). Second cut of 4 -> in_sprint should be the
    first 4 of the currently-planned (sids[3..6]), NOT backlog[:4]
    (which would be sids[0..3], reintroducing a terminal story).
    """
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    second = sm.sprint_cut(4)
    assert second["in_sprint_story_ids"] == sids[3:7], (
        f"second cut should pick the first 4 currently-planned "
        f"stories (sids[3:7]={sids[3:7]!r}); "
        f"got {second['in_sprint_story_ids']!r}"
    )


def test_recut_consumes_all_remaining_planned(isolated_log):
    """First cut of 3 (sids[0..2] terminal), then sprint_cut(7) — all
    7 remaining currently-planned go in-sprint (sids[3..9])."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    second = sm.sprint_cut(7)
    assert second["in_sprint_story_ids"] == sids[3:10], (
        f"second cut of 7 should cover all remaining currently-planned "
        f"sids[3:10]={sids[3:10]!r}; got {second['in_sprint_story_ids']!r}"
    )
    assert second["deferred_story_ids"] == sids[:3], (
        f"deferred after second cut should be the prior-cut terminal "
        f"stories sids[:3]={sids[:3]!r}; got {second['deferred_story_ids']!r}"
    )


def test_three_consecutive_single_cuts(isolated_log):
    """Three cuts of 1 each, terminal-resolved between each. Each new
    cut takes the next available currently-planned story."""
    import sm
    sids = _seed_full(n_stories=5)

    # Cut 1: in-sprint = [sids[0]]
    r1 = sm.sprint_cut(1)
    assert r1["in_sprint_story_ids"] == [sids[0]]
    _drive_to_accepted(sids[0])

    # Cut 2: in-sprint = [sids[1]]
    r2 = sm.sprint_cut(1)
    assert r2["in_sprint_story_ids"] == [sids[1]], (
        f"second cut should pick the next currently-planned story "
        f"sids[1]={sids[1]!r}; got {r2['in_sprint_story_ids']!r}"
    )
    _drive_to_accepted(sids[1])

    # Cut 3: in-sprint = [sids[2]]
    r3 = sm.sprint_cut(1)
    assert r3["in_sprint_story_ids"] == [sids[2]], (
        f"third cut should pick the next currently-planned story "
        f"sids[2]={sids[2]!r}; got {r3['in_sprint_story_ids']!r}"
    )


def test_recut_after_mixed_terminal_picks_correct_planned(isolated_log):
    """Mix of accepted/rejected/force_closed in the first cohort — all
    three are terminal and so all three are excluded from the second
    cut's pool."""
    import sm
    sids = _seed_full(n_stories=8)
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _drive_to_rejected(sids[1])
    _drive_to_force_closed(sids[2])
    second = sm.sprint_cut(3)
    assert second["in_sprint_story_ids"] == sids[3:6], (
        f"second cut should pick sids[3:6]={sids[3:6]!r} (next 3 planned); "
        f"got {second['in_sprint_story_ids']!r}"
    )


def test_recut_skipping_first_cut_via_first_cut_one(isolated_log):
    """Tiny first cut (1), terminal, then a bigger second cut. The N
    of the second cut indexes into the remaining planned stories,
    not the full backlog."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(1)
    _drive_to_accepted(sids[0])
    second = sm.sprint_cut(4)
    assert second["in_sprint_story_ids"] == sids[1:5], (
        f"second cut of 4 after 1-cut should pick sids[1:5]; "
        f"got {second['in_sprint_story_ids']!r}"
    )


# ===========================================================================
# B. in_sprint_story_ids contains only the new cut (4 tests)
# ===========================================================================


def test_in_sprint_excludes_prior_cut_story_ids(isolated_log):
    """The new sprint_cut entry's in_sprint_story_ids list MUST NOT
    contain any story_id from a prior cut (which are all terminal at
    this point)."""
    import sm
    sids = _seed_full(n_stories=8)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    second = sm.sprint_cut(4)
    for prior in sids[:3]:
        assert prior not in second["in_sprint_story_ids"], (
            f"prior-cut story_id {prior!r} must NOT appear in the new "
            f"cut's in_sprint_story_ids; got "
            f"{second['in_sprint_story_ids']!r}"
        )


def test_in_sprint_excludes_planned_beyond_cut(isolated_log):
    """Currently-planned stories beyond the new cut's N do NOT appear
    in in_sprint_story_ids."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    second = sm.sprint_cut(2)
    # Currently-planned stories beyond the cut: sids[5..9] (since cut
    # takes sids[3..4]).
    for tail in sids[5:]:
        assert tail not in second["in_sprint_story_ids"], (
            f"planned-beyond-cut story_id {tail!r} must NOT appear in "
            f"in_sprint_story_ids; got {second['in_sprint_story_ids']!r}"
        )


def test_in_sprint_length_equals_n(isolated_log):
    """`len(in_sprint_story_ids) == N` — N stories selected, no more,
    no less."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(2)
    for sid in sids[:2]:
        _drive_to_rejected(sid)
    second = sm.sprint_cut(5)
    assert len(second["in_sprint_story_ids"]) == 5, (
        f"in_sprint_story_ids length should equal N=5; "
        f"got {len(second['in_sprint_story_ids'])}"
    )


def test_in_sprint_preserves_sequence_order(isolated_log):
    """in_sprint_story_ids preserves backlog sequence order (the same
    invariant Iter 1 held)."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_force_closed(sid)
    second = sm.sprint_cut(4)
    # Expected: sids[3..6] in that order — sequence order matches the
    # original backlog sequence (which was minted in order at seed time).
    assert second["in_sprint_story_ids"] == sids[3:7], (
        f"in_sprint_story_ids must preserve backlog sequence order; "
        f"expected {sids[3:7]!r}, got {second['in_sprint_story_ids']!r}"
    )


# ===========================================================================
# C. deferred_story_ids semantics (5 tests)
# ===========================================================================


def test_deferred_includes_prior_cut_terminal_stories(isolated_log):
    """deferred_story_ids on the NEW cut entry includes the story_ids
    from prior cuts that are now terminal."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    second = sm.sprint_cut(4)
    for prior in sids[:3]:
        assert prior in second["deferred_story_ids"], (
            f"prior-cut terminal story_id {prior!r} must appear in "
            f"deferred_story_ids; got {second['deferred_story_ids']!r}"
        )


def test_deferred_includes_planned_beyond_cut(isolated_log):
    """Currently-planned stories beyond the new cut also appear in
    deferred_story_ids."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    second = sm.sprint_cut(4)
    # Cut takes sids[3..6]; planned tail is sids[7..9].
    for tail in sids[7:]:
        assert tail in second["deferred_story_ids"], (
            f"planned-beyond-cut story_id {tail!r} must appear in "
            f"deferred_story_ids; got {second['deferred_story_ids']!r}"
        )


def test_deferred_excludes_active_sprint_stories(isolated_log):
    """deferred_story_ids does NOT contain any story_id that is in the
    active (new) sprint's in_sprint_story_ids."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    second = sm.sprint_cut(4)
    overlap = set(second["in_sprint_story_ids"]) & set(
        second["deferred_story_ids"]
    )
    assert overlap == set(), (
        f"in_sprint_story_ids and deferred_story_ids must be disjoint; "
        f"overlap={overlap!r}"
    )


def test_deferred_partition_covers_full_backlog(isolated_log):
    """Union of in_sprint_story_ids and deferred_story_ids equals the
    full backlog (every story is accounted for)."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    second = sm.sprint_cut(4)
    union = set(second["in_sprint_story_ids"]) | set(
        second["deferred_story_ids"]
    )
    assert union == set(sids), (
        f"in_sprint U deferred must cover the full backlog; missing="
        f"{set(sids) - union!r}, extra={union - set(sids)!r}"
    )


def test_deferred_when_cut_takes_all_planned(isolated_log):
    """If N equals the currently-planned count, deferred_story_ids
    contains ONLY the prior-cut terminal stories — no planned stories
    remain outside the active sprint."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_rejected(sid)
    # Currently-planned = sids[3..5] (3 stories). Cut N=3 — take them all.
    second = sm.sprint_cut(3)
    assert second["in_sprint_story_ids"] == sids[3:6], (
        f"all 3 remaining planned go in-sprint; "
        f"got {second['in_sprint_story_ids']!r}"
    )
    # Deferred = exactly the prior-cut terminal stories.
    assert set(second["deferred_story_ids"]) == set(sids[:3]), (
        f"deferred should equal the prior-cut terminal stories "
        f"{set(sids[:3])!r}; got {set(second['deferred_story_ids'])!r}"
    )


# ===========================================================================
# D. Range / precondition tests (5 tests)
# ===========================================================================


def test_recut_n_exceeds_currently_planned_count(isolated_log):
    """N > currently-planned count raises SprintCutError (range).
    Currently-planned count is 7 (10 backlog - 3 terminal); N=8 must
    fail."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    with pytest.raises(sm.SprintCutError) as exc_info:
        sm.sprint_cut(8)
    # Ensure it's a range error, not the lock error (lock is satisfied:
    # prior cohort all terminal).
    assert not isinstance(exc_info.value, sm.SprintCutLockedError), (
        f"out-of-range N must produce a SprintCutError (range), NOT "
        f"the locked variant; got {exc_info.value!r}"
    )


def test_recut_n_zero_still_rejected(isolated_log):
    """N=0 still rejected with the existing precondition."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    with pytest.raises(sm.SprintCutError) as exc_info:
        sm.sprint_cut(0)
    assert not isinstance(exc_info.value, sm.SprintCutLockedError)


def test_recut_n_negative_still_rejected(isolated_log):
    """Negative N still rejected with the existing precondition."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(-1)


def test_recut_n_equals_currently_planned_count_succeeds(isolated_log):
    """Edge: N == currently-planned count. All remaining planned
    stories enter the new sprint."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(4)
    for sid in sids[:4]:
        _drive_to_force_closed(sid)
    # Currently-planned count = 6 (sids[4..9]). N=6 must succeed.
    result = sm.sprint_cut(6)
    assert result["in_sprint_story_ids"] == sids[4:10], (
        f"N=6 should consume all remaining planned sids[4:10]; "
        f"got {result['in_sprint_story_ids']!r}"
    )


def test_range_error_log_unchanged(isolated_log):
    """Range failure on re-cut does not write to the log."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(99)  # way out of range
    assert isolated_log.read_bytes() == before, (
        "range failure must leave log byte-for-byte unchanged"
    )


# ===========================================================================
# E. cut_position field semantics (3 tests)
# ===========================================================================


def test_cut_position_equals_n_count_for_recut(isolated_log):
    """DESIGN DECISION: cut_position is N (the count for THIS cut),
    NOT a cumulative offset into the original backlog.

    First cut of 3 -> cut_position=3. Re-cut of 4 -> cut_position=4
    (not 7, which would be the cumulative position of the last cut
    story in the original backlog).
    """
    import sm
    sids = _seed_full(n_stories=10)
    first = sm.sprint_cut(3)
    assert first["cut_position"] == 3
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    second = sm.sprint_cut(4)
    assert second["cut_position"] == 4, (
        f"cut_position must be N (count for THIS cut), not cumulative; "
        f"expected 4, got {second['cut_position']!r}"
    )


def test_each_cut_records_its_own_cut_position(isolated_log):
    """In a multi-cut iteration, each sprint_cut entry persists its
    own cut_position. Reading the log back yields every cut's N."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(2)
    for sid in sids[:2]:
        _drive_to_accepted(sid)
    sm.sprint_cut(3)
    for sid in sids[2:5]:
        _drive_to_rejected(sid)
    sm.sprint_cut(4)
    cuts = [e for e in sm.read_entries() if e.get("type") == "sprint_cut"]
    assert len(cuts) == 3, f"expected 3 sprint_cut entries; got {len(cuts)}"
    positions = [c.get("cut_position") for c in cuts]
    assert positions == [2, 3, 4], (
        f"cut_position per entry should be [2, 3, 4]; got {positions!r}"
    )


def test_cut_position_persists_on_log_round_trip(isolated_log):
    """The cut_position field round-trips through the JSONL log
    correctly — replay's `state["sprint_cut"]` reflects the LATEST
    entry's cut_position (Story 2 contract preserved)."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    sm.sprint_cut(4)
    state = sm.derive_state()
    # Story 2 pinned: state["sprint_cut"] = LATEST entry's cut_position.
    # Under Story 3 semantics, latest cut_position = 4 (count of the
    # new cut), so state["sprint_cut"] == 4.
    assert state["sprint_cut"] == 4, (
        f"state['sprint_cut'] should reflect the latest cut's "
        f"cut_position (=4 under Story 3 semantics); "
        f"got {state['sprint_cut']!r}"
    )


# ===========================================================================
# F. Behavioral preservation (4 tests)
# ===========================================================================


def test_prior_cut_terminal_stories_keep_their_state(isolated_log):
    """A re-cut does NOT rewind the state of prior-cut terminal stories.
    Their story_state_change history stays on the log and
    state["story_states"] still shows them as terminal."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _drive_to_rejected(sids[1])
    _drive_to_force_closed(sids[2])
    sm.sprint_cut(4)  # second cut
    state = sm.derive_state()
    assert state["story_states"][sids[0]] == "accepted"
    assert state["story_states"][sids[1]] == "rejected"
    assert state["story_states"][sids[2]] == "force_closed"


def test_transition_story_reads_latest_cut_cohort(isolated_log):
    """`transition_story` consumes the LATEST sprint_cut's
    in_sprint_story_ids to validate membership. After a re-cut, the
    new cohort is transitionable; prior-cut stories are NOT (they're
    terminal anyway, but the membership check fires first if they
    weren't terminal — which under Story 3 they always are)."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    sm.sprint_cut(4)  # new cohort = sids[3..6] under Story 3
    # A story in the new cohort must be transitionable.
    result = sm.transition_story(sids[3], "in_progress",
                                 notes="story 3 test")
    assert result["story_id"] == sids[3]
    assert result["to_state"] == "in_progress"


def test_status_in_sprint_set_reflects_latest_cut(isolated_log):
    """The `status` command's consumers (the per-story view) use the
    LATEST sprint_cut entry's in_sprint_story_ids. After a re-cut,
    the in-sprint set surfaced by status equals the new cohort."""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    sm.sprint_cut(4)  # new cohort under Story 3 = sids[3..6]
    # Re-derive the in-sprint set the way status does (scan log for
    # latest sprint_cut entry).
    in_sprint_ids = []
    for entry in sm.read_entries():
        if entry.get("type") == "sprint_cut":
            in_sprint_ids = entry.get("in_sprint_story_ids", []) or []
    assert in_sprint_ids == sids[3:7], (
        f"latest in_sprint set must be the new cohort sids[3:7]; "
        f"got {in_sprint_ids!r}"
    )


def test_log_jsonl_round_trip_records_correct_cohort(isolated_log):
    """Re-cut writes a JSONL entry that, when re-read from disk,
    carries the correct in_sprint_story_ids and deferred_story_ids
    fields. (Storage-layer round trip.)"""
    import sm
    sids = _seed_full(n_stories=10)
    sm.sprint_cut(3)
    for sid in sids[:3]:
        _drive_to_accepted(sid)
    sm.sprint_cut(4)

    # Re-read the file directly to verify the on-disk entry shape.
    lines = isolated_log.read_text().splitlines()
    sprint_cut_entries = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("type") == "sprint_cut"
    ]
    assert len(sprint_cut_entries) == 2, (
        f"expected 2 sprint_cut entries on disk; got "
        f"{len(sprint_cut_entries)}"
    )
    latest = sprint_cut_entries[-1]
    assert latest["in_sprint_story_ids"] == sids[3:7], (
        f"latest on-disk in_sprint_story_ids should be sids[3:7]; "
        f"got {latest['in_sprint_story_ids']!r}"
    )
    # Disjoint check against the first cut's cohort.
    first_cohort = set(sprint_cut_entries[0]["in_sprint_story_ids"])
    latest_cohort = set(latest["in_sprint_story_ids"])
    assert first_cohort.isdisjoint(latest_cohort), (
        f"first and second cuts' in_sprint cohorts must be disjoint "
        f"on disk; first={first_cohort!r}, latest={latest_cohort!r}"
    )
