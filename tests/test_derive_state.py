"""Story 4 — pin the contract of `sm.derive_state`.

What this file pins:
  - Function signature and shape: `derive_state() -> dict`, PUBLIC, callable,
    in `sm.__all__`, importable as `from sm import derive_state`. Takes no
    arguments. Returns a plain dict (content-oriented bias — no dataclass,
    no NamedTuple).

  - Return shape (top-level keys, all required, every call):
      * `active_iteration` — dict {iteration_id, requirements: [...]} or None
      * `story_backlog`    — list[dict] of story records (sequence-ordered)
      * `sprint_cut`       — int (1..len(backlog)) or None
      * `story_states`     — dict mapping story_id → lifecycle state string
      * `close_status`     — dict {closed_by, reason, accepted_count,
                                   rejected_count, force_closed_count}
                             or None

  - Empty log: every field at empty/None default, no exception.

  - Replay consumes `read_entries()` once and reduces to current state.

  - Pure replay: log bytes unchanged; two calls yield equal results.

  - State machine errors: an entry that violates the lifecycle (e.g.,
    transition from a terminal state) raises `ValueError` naming the
    offending entry id.

  - Single-active-iteration enforcement at replay: a second `iteration_open`
    with no intervening `iteration_close` raises `ValueError` naming the
    offending entry id.

  - Re-cut: latest `sprint_cut` entry wins (replay layer accepts ALL
    sprint_cut entries; the cut-after-state-change LOCK is the COMMAND
    layer's responsibility, not replay's — Story 11's lane).

  - Mutation independence: mutating a returned state dict does not affect
    a subsequent `derive_state()` call.

  - LOG_PATH-based: uses `sm.LOG_PATH` (verified via monkeypatch).

Log entry types replay must understand (built via `build_entry`):
  - `iteration_open`       content: {iteration_id, requirements: [...]}
  - `iteration_close`      content: {closed_by, reason, accepted_count,
                                     rejected_count, force_closed_count}
  - `story_decomposed`     content: {stories: [list of story dicts]}
  - `sprint_cut`           content: {cut_position: int}
  - `story_state_change`   content: {story_id, from_state, to_state, notes}

Lifecycle states:
  planned → in_progress → in_review → accepted | rejected | force_closed
  force_closed is also reachable directly from any non-terminal state.
  Terminal states: accepted, rejected, force_closed. No transitions OUT of
  terminal states are legal.

Tests must FAIL on first run — `derive_state` does not exist yet. The Coder
downstream implements to satisfy these tests.
"""

from __future__ import annotations

import json
import pathlib
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
    """Redirect `sm.LOG_PATH` to a per-test tmp file.

    Mirrors the suite convention (test_append_entry.py, test_read_entries.py,
    test_build_entry.py).
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _emit(type_: str, content: dict) -> dict:
    """Build + append an entry through the canonical path. Returns the entry."""
    import sm

    e = sm.build_entry(type_, content)
    sm._append_entry(e)
    return e


def _open_iteration(iteration_id: str = "iter-1",
                    requirements=None) -> dict:
    if requirements is None:
        requirements = ["1", "2", "3"]
    return _emit("iteration_open", {
        "iteration_id": iteration_id,
        "requirements": list(requirements),
    })


def _decompose(stories) -> dict:
    """Each story is a dict with all the canonical fields."""
    return _emit("story_decomposed", {"stories": list(stories)})


def _cut(n: int) -> dict:
    return _emit("sprint_cut", {"cut_position": n})


def _state_change(story_id: str, from_state: str, to_state: str,
                  notes: str = "") -> dict:
    return _emit("story_state_change", {
        "story_id": story_id,
        "from_state": from_state,
        "to_state": to_state,
        "notes": notes,
    })


def _close(closed_by: str = "operator", reason=None,
           accepted_count: int = 0, rejected_count: int = 0,
           force_closed_count: int = 0) -> dict:
    return _emit("iteration_close", {
        "closed_by": closed_by,
        "reason": reason,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "force_closed_count": force_closed_count,
    })


def _story(story_id: str, sequence: int, title: str = "",
           size: str = "S", requirement_ids=None,
           acceptance_criteria=None) -> dict:
    if requirement_ids is None:
        requirement_ids = []
    if acceptance_criteria is None:
        acceptance_criteria = []
    return {
        "story_id": story_id,
        "sequence": sequence,
        "title": title or f"Story {sequence}",
        "size": size,
        "requirement_ids": list(requirement_ids),
        "acceptance_criteria": list(acceptance_criteria),
    }


def _three_stories():
    """A minimal canonical 3-story backlog."""
    return [
        _story("s1", 1, requirement_ids=["1"]),
        _story("s2", 2, requirement_ids=["2"]),
        _story("s3", 3, requirement_ids=["3"]),
    ]


# ===========================================================================
# Smoke (5+)
# ===========================================================================

def test_function_exists_on_module():
    import sm
    assert hasattr(sm, "derive_state"), "sm.derive_state must exist"


def test_function_is_callable():
    import sm
    assert callable(sm.derive_state)


def test_function_name_is_public():
    """No leading underscore — public API."""
    import sm
    assert not sm.derive_state.__name__.startswith("_")
    assert sm.derive_state.__name__ == "derive_state"


def test_function_importable_directly():
    """`from sm import derive_state` succeeds — public-import form."""
    from sm import derive_state  # noqa: F401
    assert callable(derive_state)


def test_function_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "derive_state" in sm.__all__, (
        f"derive_state must be in __all__; got {sm.__all__!r}"
    )


def test_function_returns_dict(isolated_log):
    """Empty-log call returns a dict (the content-oriented shape)."""
    import sm
    result = sm.derive_state()
    assert isinstance(result, dict), (
        f"derive_state must return a dict; got {type(result).__name__}"
    )


def test_function_takes_no_required_args(isolated_log):
    import sm
    # No positional, no keyword required.
    sm.derive_state()


# ===========================================================================
# Empty log (4+)
# ===========================================================================

def test_empty_log_active_iteration_is_none(isolated_log):
    import sm
    state = sm.derive_state()
    assert state["active_iteration"] is None


def test_empty_log_story_backlog_is_empty_list(isolated_log):
    import sm
    state = sm.derive_state()
    assert state["story_backlog"] == []


def test_empty_log_sprint_cut_is_none(isolated_log):
    import sm
    state = sm.derive_state()
    assert state["sprint_cut"] is None


def test_empty_log_story_states_is_empty_dict(isolated_log):
    import sm
    state = sm.derive_state()
    assert state["story_states"] == {}


def test_empty_log_close_status_is_none(isolated_log):
    import sm
    state = sm.derive_state()
    assert state["close_status"] is None


def test_empty_log_does_not_raise(isolated_log):
    import sm
    try:
        sm.derive_state()
    except Exception as e:
        pytest.fail(f"derive_state on empty log raised: {e!r}")


def test_empty_log_two_calls_equal(isolated_log):
    import sm
    a = sm.derive_state()
    b = sm.derive_state()
    assert a == b


def test_empty_log_has_all_required_keys(isolated_log):
    """Every field present, even on empty log."""
    import sm
    state = sm.derive_state()
    expected = {"active_iteration", "story_backlog", "sprint_cut",
                "story_states", "close_status"}
    assert set(state.keys()) >= expected, (
        f"State must contain {expected!r}; got {set(state.keys())!r}"
    )


def test_missing_log_file_treated_as_empty(isolated_log):
    """If log.jsonl doesn't exist, derive_state is empty (no exception)."""
    import sm
    assert not isolated_log.exists()
    state = sm.derive_state()
    assert state["active_iteration"] is None
    assert state["story_backlog"] == []
    assert state["sprint_cut"] is None
    assert state["story_states"] == {}
    assert state["close_status"] is None


# ===========================================================================
# Iteration open (5+)
# ===========================================================================

def test_iteration_open_sets_active_iteration(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    state = sm.derive_state()
    assert state["active_iteration"] is not None


def test_iteration_open_captures_iteration_id(isolated_log):
    import sm
    _open_iteration("iter-alpha", ["1"])
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-alpha"


def test_iteration_open_captures_requirements_verbatim(isolated_log):
    import sm
    reqs = ["1", "2", "5", "8"]
    _open_iteration("iter-1", reqs)
    state = sm.derive_state()
    assert state["active_iteration"]["requirements"] == reqs


def test_iteration_open_requirements_order_preserved(isolated_log):
    """Requirements list is verbatim — order is preserved exactly."""
    import sm
    reqs = ["8", "2", "5", "1"]
    _open_iteration("iter-1", reqs)
    state = sm.derive_state()
    assert state["active_iteration"]["requirements"] == reqs


def test_iteration_open_empty_requirements_allowed(isolated_log):
    import sm
    _open_iteration("iter-1", [])
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-1"
    assert state["active_iteration"]["requirements"] == []


def test_iteration_open_close_status_remains_none(isolated_log):
    """Just opening doesn't populate close_status."""
    import sm
    _open_iteration("iter-1", ["1"])
    state = sm.derive_state()
    assert state["close_status"] is None


def test_iteration_open_alone_leaves_backlog_empty(isolated_log):
    """Opening an iteration without decomposing stories yields empty backlog."""
    import sm
    _open_iteration("iter-1", ["1"])
    state = sm.derive_state()
    assert state["story_backlog"] == []
    assert state["story_states"] == {}


# ===========================================================================
# Iteration close (5+)
# ===========================================================================

def test_iteration_close_resets_active_iteration_to_none(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _close()
    state = sm.derive_state()
    assert state["active_iteration"] is None


def test_iteration_close_populates_close_status(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _close(closed_by="nick", reason=None,
           accepted_count=0, rejected_count=0, force_closed_count=0)
    state = sm.derive_state()
    assert state["close_status"] is not None
    assert state["close_status"]["closed_by"] == "nick"


def test_iteration_close_status_has_required_fields(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _close(closed_by="op", reason="x",
           accepted_count=2, rejected_count=1, force_closed_count=3)
    state = sm.derive_state()
    cs = state["close_status"]
    assert cs["closed_by"] == "op"
    assert cs["reason"] == "x"
    assert cs["accepted_count"] == 2
    assert cs["rejected_count"] == 1
    assert cs["force_closed_count"] == 3


def test_iteration_close_preserves_story_states(isolated_log):
    """Closing does NOT clear per-story state — they survive close."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    _close(accepted_count=1)
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "accepted"


def test_iteration_close_preserves_backlog(isolated_log):
    """Closing does NOT clear backlog — it remains visible after close."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose(_three_stories())
    _close()
    state = sm.derive_state()
    assert len(state["story_backlog"]) == 3


def test_iteration_close_with_none_reason(isolated_log):
    """Normal close — reason is None."""
    import sm
    _open_iteration("iter-1", ["1"])
    _close(reason=None)
    state = sm.derive_state()
    assert state["close_status"]["reason"] is None


def test_iteration_close_with_force_close_reason(isolated_log):
    """Force-close — reason is the operator-supplied string."""
    import sm
    _open_iteration("iter-1", ["1"])
    _close(reason="abort-mission", force_closed_count=2)
    state = sm.derive_state()
    assert state["close_status"]["reason"] == "abort-mission"
    assert state["close_status"]["force_closed_count"] == 2


# ===========================================================================
# Story decomposition (8+)
# ===========================================================================

def test_decomposition_populates_backlog(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    state = sm.derive_state()
    assert len(state["story_backlog"]) == 3


def test_decomposition_backlog_in_sequence_order(isolated_log):
    """Backlog list ordered by `sequence` — emit out of order, still ordered."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose([
        _story("s2", 2),
        _story("s1", 1),
        _story("s3", 3),
    ])
    state = sm.derive_state()
    seqs = [s["sequence"] for s in state["story_backlog"]]
    assert seqs == [1, 2, 3], (
        f"Backlog must be ordered by sequence; got {seqs!r}"
    )


def test_decomposition_preserves_story_id(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    state = sm.derive_state()
    ids = [s["story_id"] for s in state["story_backlog"]]
    assert ids == ["s1", "s2", "s3"]


def test_decomposition_preserves_title(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1, title="Implement foo")])
    state = sm.derive_state()
    assert state["story_backlog"][0]["title"] == "Implement foo"


def test_decomposition_preserves_size(isolated_log):
    """Each story's `size` is preserved verbatim."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose([
        _story("s1", 1, size="S"),
        _story("s2", 2, size="M"),
        _story("s3", 3, size="L"),
    ])
    state = sm.derive_state()
    sizes = [s["size"] for s in state["story_backlog"]]
    assert sizes == ["S", "M", "L"]


def test_decomposition_preserves_requirement_ids_list(isolated_log):
    """requirement_ids list is preserved exactly per story."""
    import sm
    _open_iteration("iter-1", ["1", "2", "5"])
    _decompose([
        _story("s1", 1, requirement_ids=["1", "2"]),
        _story("s2", 2, requirement_ids=["5"]),
    ])
    state = sm.derive_state()
    backlog = state["story_backlog"]
    assert backlog[0]["requirement_ids"] == ["1", "2"]
    assert backlog[1]["requirement_ids"] == ["5"]


def test_decomposition_preserves_requirement_ids_order(isolated_log):
    """The list order of requirement_ids is preserved (verbatim)."""
    import sm
    _open_iteration("iter-1", ["1", "2", "5", "8"])
    _decompose([
        _story("s1", 1, requirement_ids=["8", "1", "5", "2"]),
    ])
    state = sm.derive_state()
    assert state["story_backlog"][0]["requirement_ids"] == ["8", "1", "5", "2"]


def test_decomposition_preserves_acceptance_criteria(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    ac = ["AC1: foo", "AC2: bar", "AC3: baz"]
    _decompose([_story("s1", 1, acceptance_criteria=ac)])
    state = sm.derive_state()
    assert state["story_backlog"][0]["acceptance_criteria"] == ac


def test_decomposition_default_state_is_planned(isolated_log):
    """Every decomposed story starts in `planned`."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    state = sm.derive_state()
    assert state["story_states"] == {
        "s1": "planned",
        "s2": "planned",
        "s3": "planned",
    }


def test_decomposition_preserves_all_fields_simultaneously(isolated_log):
    """One story emitted with all canonical fields — every field round-trips."""
    import sm
    _open_iteration("iter-1", ["7"])
    s = _story("s-x", 5, title="The X Story", size="L",
               requirement_ids=["7", "9"],
               acceptance_criteria=["AC: it works", "AC: it shines"])
    _decompose([s])
    state = sm.derive_state()
    out = state["story_backlog"][0]
    assert out["story_id"] == "s-x"
    assert out["sequence"] == 5
    assert out["title"] == "The X Story"
    assert out["size"] == "L"
    assert out["requirement_ids"] == ["7", "9"]
    assert out["acceptance_criteria"] == ["AC: it works", "AC: it shines"]


def test_decomposition_empty_stories_yields_empty_backlog(isolated_log):
    """If `stories` is empty, backlog stays empty (legal degenerate case)."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([])
    state = sm.derive_state()
    assert state["story_backlog"] == []
    assert state["story_states"] == {}


# ===========================================================================
# Sprint cut (8+)
# ===========================================================================

def test_single_cut_sets_sprint_cut(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(2)
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_single_cut_one_keeps_one_story(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(1)
    state = sm.derive_state()
    assert state["sprint_cut"] == 1


def test_cut_full_backlog(isolated_log):
    """Cut == len(backlog) — entire backlog included."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(3)
    state = sm.derive_state()
    assert state["sprint_cut"] == 3


def test_recut_latest_wins_two_cuts(isolated_log):
    """Two sprint_cut entries: the latest wins."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3", "4", "5", "6", "7", "8"])
    _decompose([_story(f"s{i}", i) for i in range(1, 9)])
    _cut(5)
    _cut(8)
    state = sm.derive_state()
    assert state["sprint_cut"] == 8


def test_recut_latest_wins_three_cuts(isolated_log):
    """Three sprint_cut entries — only the last counts."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3", "4", "5", "6", "7", "8"])
    _decompose([_story(f"s{i}", i) for i in range(1, 9)])
    _cut(2)
    _cut(5)
    _cut(7)
    state = sm.derive_state()
    assert state["sprint_cut"] == 7


def test_recut_latest_wins_can_decrease(isolated_log):
    """Latest wins even when the latest is SMALLER than a prior cut."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3", "4", "5"])
    _decompose([_story(f"s{i}", i) for i in range(1, 6)])
    _cut(5)
    _cut(2)
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_recut_many_iterations(isolated_log):
    """Five cut events: the fifth wins."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3", "4", "5", "6", "7", "8"])
    _decompose([_story(f"s{i}", i) for i in range(1, 9)])
    _cut(1)
    _cut(2)
    _cut(3)
    _cut(4)
    _cut(8)
    state = sm.derive_state()
    assert state["sprint_cut"] == 8


def test_no_cut_yields_none(isolated_log):
    """Backlog exists but no cut yet — sprint_cut is None."""
    import sm
    _open_iteration("iter-1", ["1", "2"])
    _decompose([_story("s1", 1), _story("s2", 2)])
    state = sm.derive_state()
    assert state["sprint_cut"] is None


# IMPORTANT: Replay does NOT enforce the cut-after-state-change LOCK rule.
# That belongs to the COMMAND layer (Story 11). At replay time, ALL
# sprint_cut entries are accepted in order — latest wins, regardless of
# whether stories have moved out of `planned`.
# These tests pin the replay-layer permissive semantics:

def test_replay_accepts_cut_after_story_moved(isolated_log):
    """Replay does NOT enforce cut-lock — Story 11's lane.

    Even though the cut happens after a story left planned, replay must
    still accept the entry and apply it (latest-wins). The COMMAND that
    emits sprint_cut is responsible for enforcing the lock at write time.
    """
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(3)
    _state_change("s1", "planned", "in_progress")
    # This second cut happens AFTER s1 moved — the COMMAND layer (Story 11)
    # should reject this at write time, but if it ever lands in the log,
    # replay must tolerate it.
    _cut(1)
    state = sm.derive_state()
    assert state["sprint_cut"] == 1


def test_replay_accepts_multiple_cuts_after_state_changes(isolated_log):
    """Replay accepts the full sequence even if the cut-lock would have
    forbidden writes — the latest cut wins regardless."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _state_change("s1", "planned", "in_progress")
    _cut(2)
    _cut(3)
    state = sm.derive_state()
    assert state["sprint_cut"] == 3


# ===========================================================================
# Story state transitions (12+)
# ===========================================================================

def test_planned_to_in_progress(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "in_progress"


def test_in_progress_to_in_review(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "in_review"


def test_in_review_to_accepted(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "accepted"


def test_in_review_to_rejected(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "rejected")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "rejected"


def test_full_happy_path_all_three_states(isolated_log):
    """planned → in_progress → in_review → accepted — straight line."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    for sid in ("s1", "s2", "s3"):
        _state_change(sid, "planned", "in_progress")
        _state_change(sid, "in_progress", "in_review")
        _state_change(sid, "in_review", "accepted")
    state = sm.derive_state()
    assert state["story_states"] == {
        "s1": "accepted",
        "s2": "accepted",
        "s3": "accepted",
    }


def test_planned_to_force_closed(isolated_log):
    """force_closed is reachable directly from planned."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "force_closed")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "force_closed"


def test_in_progress_to_force_closed(isolated_log):
    """force_closed is reachable from in_progress."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "force_closed")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "force_closed"


def test_in_review_to_force_closed(isolated_log):
    """force_closed is reachable from in_review."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "force_closed")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "force_closed"


def test_illegal_planned_to_accepted_raises(isolated_log):
    """Cannot skip in_progress and in_review — illegal transition."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    bad = _state_change("s1", "planned", "accepted")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    msg = str(exc_info.value)
    assert bad["id"] in msg, (
        f"Error must name offending entry id {bad['id']!r}; got: {msg!r}"
    )


def test_illegal_planned_to_rejected_raises(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    bad = _state_change("s1", "planned", "rejected")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_illegal_planned_to_in_review_raises(isolated_log):
    """Cannot skip in_progress."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    bad = _state_change("s1", "planned", "in_review")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_illegal_in_progress_to_accepted_raises(isolated_log):
    """Cannot skip in_review."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    bad = _state_change("s1", "in_progress", "accepted")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_illegal_in_progress_to_rejected_raises(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    bad = _state_change("s1", "in_progress", "rejected")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_illegal_transition_from_accepted_raises(isolated_log):
    """`accepted` is terminal — no transitions out are legal."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    bad = _state_change("s1", "accepted", "rejected")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_illegal_transition_from_rejected_raises(isolated_log):
    """`rejected` is terminal — no transitions out are legal."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "rejected")
    bad = _state_change("s1", "rejected", "accepted")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_illegal_transition_from_force_closed_raises(isolated_log):
    """`force_closed` is terminal — no transitions out are legal."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "force_closed")
    bad = _state_change("s1", "force_closed", "in_progress")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_illegal_accepted_to_force_closed_raises(isolated_log):
    """Even force_closed cannot pull a story back from accepted."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    bad = _state_change("s1", "accepted", "force_closed")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_illegal_in_review_to_in_progress_raises(isolated_log):
    """No backwards transitions allowed (other than via force_closed)."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    bad = _state_change("s1", "in_review", "in_progress")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


# ===========================================================================
# Force-close (5+)
# ===========================================================================

def test_force_close_from_planned_allowed(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "force_closed")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "force_closed"


def test_force_close_from_in_progress_allowed(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "force_closed")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "force_closed"


def test_force_close_from_in_review_allowed(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "force_closed")
    state = sm.derive_state()
    assert state["story_states"]["s1"] == "force_closed"


def test_force_close_count_in_close_status(isolated_log):
    """force_closed_count in close_status reflects what the close entry says."""
    import sm
    _open_iteration("iter-1", ["1", "2"])
    _decompose([_story("s1", 1), _story("s2", 2)])
    _state_change("s1", "planned", "force_closed")
    _state_change("s2", "planned", "force_closed")
    _close(reason="abort", force_closed_count=2)
    state = sm.derive_state()
    assert state["close_status"]["force_closed_count"] == 2


def test_force_close_terminal_state_blocks_further_changes(isolated_log):
    """Once force_closed, no further state-change entries are legal."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "force_closed")
    bad = _state_change("s1", "force_closed", "in_review")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


# ===========================================================================
# State machine error messages (5+)
# ===========================================================================

def test_error_names_offending_entry_id_planned_to_accepted(isolated_log):
    """Illegal transition error message contains the offending entry's id."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    bad = _state_change("s1", "planned", "accepted")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_error_names_offending_entry_id_after_terminal(isolated_log):
    """Transition out of accepted — error names that entry."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    bad = _state_change("s1", "accepted", "rejected")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_error_names_only_offending_entry_id(isolated_log):
    """Earlier valid entries' ids are NOT in the error message."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    good1 = _state_change("s1", "planned", "in_progress")
    good2 = _state_change("s1", "in_progress", "in_review")
    bad = _state_change("s1", "in_review", "in_progress")  # backwards — illegal
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    msg = str(exc_info.value)
    assert bad["id"] in msg
    # Good entry ids should not be in the error (only the offender).
    assert good1["id"] not in msg
    assert good2["id"] not in msg


def test_error_message_is_structured(isolated_log):
    """Error message is non-empty and points at a specific cause."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    bad = _state_change("s1", "planned", "accepted")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    msg = str(exc_info.value)
    assert msg, "Error message must not be empty"
    assert bad["id"] in msg


def test_error_for_unknown_story_id_names_entry_id(isolated_log):
    """A state_change for a story not in the backlog is illegal — names entry id."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    bad = _state_change("does-not-exist", "planned", "in_progress")
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_error_two_calls_both_raise(isolated_log):
    """Replay error is reproducible — second call also raises."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    bad = _state_change("s1", "planned", "accepted")
    with pytest.raises(ValueError) as exc1:
        sm.derive_state()
    with pytest.raises(ValueError) as exc2:
        sm.derive_state()
    assert bad["id"] in str(exc1.value)
    assert bad["id"] in str(exc2.value)


# ===========================================================================
# Single-active-iteration enforcement at replay
# ===========================================================================

def test_two_iteration_opens_no_close_raises(isolated_log):
    """Two iteration_open entries with no intervening close — raises."""
    import sm
    _open_iteration("iter-1", ["1"])
    bad = _open_iteration("iter-2", ["2"])
    with pytest.raises(ValueError) as exc_info:
        sm.derive_state()
    assert bad["id"] in str(exc_info.value)


def test_open_close_open_is_legal(isolated_log):
    """A normal close-and-flow sequence is fine — even though close_status
    only reflects the most recent close, replay does not error."""
    import sm
    _open_iteration("iter-1", ["1"])
    _close()
    _open_iteration("iter-2", ["2"])
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-2"


# ===========================================================================
# Pure replay (5+)
# ===========================================================================

def test_pure_replay_log_bytes_unchanged(isolated_log):
    """derive_state() does not modify log.jsonl."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(2)
    _state_change("s1", "planned", "in_progress")
    before = isolated_log.read_bytes()
    sm.derive_state()
    after = isolated_log.read_bytes()
    assert before == after, "derive_state must not modify log.jsonl"


def test_pure_replay_two_calls_equal(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(2)
    _state_change("s1", "planned", "in_progress")
    a = sm.derive_state()
    b = sm.derive_state()
    assert a == b


def test_pure_replay_three_calls_equal(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2"])
    _decompose([_story("s1", 1), _story("s2", 2)])
    _cut(2)
    _state_change("s1", "planned", "in_progress")
    a = sm.derive_state()
    b = sm.derive_state()
    c = sm.derive_state()
    assert a == b == c


def test_pure_replay_no_sidecar_files(tmp_path, monkeypatch):
    """No `.state`, no journal, no DB — derive_state creates nothing."""
    import sm
    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    sm.derive_state()
    contents = sorted(p.name for p in tmp_path.iterdir())
    assert contents == ["log.jsonl"]


def test_pure_replay_byte_for_byte_unchanged_complex(isolated_log):
    """Complex scenario, 20+ entries, log byte-for-byte unchanged after replay."""
    import sm
    _open_iteration("iter-1", [str(i) for i in range(1, 9)])
    _decompose([_story(f"s{i}", i, requirement_ids=[str(i)]) for i in range(1, 9)])
    _cut(5)
    _cut(8)
    for i in range(1, 5):
        _state_change(f"s{i}", "planned", "in_progress")
        _state_change(f"s{i}", "in_progress", "in_review")
        _state_change(f"s{i}", "in_review", "accepted")
    before = isolated_log.read_bytes()
    sm.derive_state()
    sm.derive_state()
    sm.derive_state()
    after = isolated_log.read_bytes()
    assert before == after


def test_pure_replay_does_not_raise_on_re_call(isolated_log):
    """Calling derive_state() twice in a row is safe."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    sm.derive_state()
    # Second call — must not raise from caching/iterator-exhaustion bugs.
    sm.derive_state()


# ===========================================================================
# Re-cut after stories moving (3+ — replay-layer permissive)
# ===========================================================================

def test_replay_recut_after_state_change_takes_latest(isolated_log):
    """At the REPLAY layer, sprint_cut is always latest-wins regardless of
    whether stories have moved out of `planned`. The cut-LOCK rule is the
    COMMAND layer's lane (Story 11)."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(2)
    _state_change("s1", "planned", "in_progress")
    _cut(3)
    state = sm.derive_state()
    assert state["sprint_cut"] == 3


def test_replay_recut_after_acceptance_takes_latest(isolated_log):
    """Even after a story has been accepted, replay takes the latest cut."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(3)
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    _cut(1)
    state = sm.derive_state()
    assert state["sprint_cut"] == 1


def test_replay_recut_interleaved_with_state_changes(isolated_log):
    """Many cuts interleaved with state changes — final cut is the answer."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(2)
    _state_change("s1", "planned", "in_progress")
    _cut(3)
    _state_change("s2", "planned", "in_progress")
    _cut(1)
    _state_change("s3", "planned", "in_progress")
    _cut(2)
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


# ===========================================================================
# Mutation independence (3+)
# ===========================================================================

def test_mutating_state_does_not_affect_next_call(isolated_log):
    """State is freshly built per call — mutating returned dict is safe."""
    import sm
    _open_iteration("iter-1", ["1", "2"])
    _decompose([_story("s1", 1), _story("s2", 2)])
    a = sm.derive_state()
    a["story_backlog"].clear()
    a["story_states"]["s1"] = "MUTATED"
    a["sprint_cut"] = 999
    b = sm.derive_state()
    assert len(b["story_backlog"]) == 2
    assert b["story_states"]["s1"] == "planned"
    assert b["sprint_cut"] is None


def test_mutating_active_iteration_does_not_affect_next_call(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    a = sm.derive_state()
    a["active_iteration"]["requirements"].append("MUTATED")
    a["active_iteration"]["iteration_id"] = "MUTATED"
    b = sm.derive_state()
    assert b["active_iteration"]["iteration_id"] == "iter-1"
    assert b["active_iteration"]["requirements"] == ["1", "2", "3"]


def test_mutating_close_status_does_not_affect_next_call(isolated_log):
    import sm
    _open_iteration("iter-1", ["1"])
    _close(closed_by="op", accepted_count=0)
    a = sm.derive_state()
    a["close_status"]["closed_by"] = "MUTATED"
    a["close_status"]["accepted_count"] = 999
    b = sm.derive_state()
    assert b["close_status"]["closed_by"] == "op"
    assert b["close_status"]["accepted_count"] == 0


def test_two_results_are_distinct_objects(isolated_log):
    """Two calls return two distinct dict objects."""
    import sm
    _open_iteration("iter-1", ["1"])
    a = sm.derive_state()
    b = sm.derive_state()
    assert a is not b


def test_mutating_one_state_does_not_affect_another(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2"])
    _decompose([_story("s1", 1), _story("s2", 2)])
    a = sm.derive_state()
    b = sm.derive_state()
    a["story_states"]["s1"] = "MUTATED"
    assert b["story_states"]["s1"] == "planned"


# ===========================================================================
# Mixed entry order — real-world ordering (5+)
# ===========================================================================

def test_full_lifecycle_open_decompose_cut_changes_close(isolated_log):
    """Real-world: open, decompose, cut, state changes, close — clean state."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(2)
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    _state_change("s2", "planned", "in_progress")
    _state_change("s2", "in_progress", "in_review")
    _state_change("s2", "in_review", "rejected")
    _close(closed_by="op", reason=None,
           accepted_count=1, rejected_count=1, force_closed_count=0)
    state = sm.derive_state()
    assert state["active_iteration"] is None
    assert len(state["story_backlog"]) == 3
    assert state["sprint_cut"] == 2
    assert state["story_states"] == {
        "s1": "accepted",
        "s2": "rejected",
        "s3": "planned",
    }
    assert state["close_status"]["accepted_count"] == 1
    assert state["close_status"]["rejected_count"] == 1


def test_decompose_before_open_decompose_then_open(isolated_log):
    """If a decompose lands before open (degenerate but legal at replay layer):
    backlog still populates."""
    import sm
    # NOTE: We pin the permissive replay semantic — the operator-layer (commands)
    # would prevent this, but replay should not crash on out-of-order events
    # if the entries themselves are well-formed and don't violate the state
    # machine. We only assert the function does not error and backlog populates.
    _decompose([_story("s1", 1)])
    _open_iteration("iter-1", ["1"])
    state = sm.derive_state()
    # The decomposition still registered.
    assert any(s["story_id"] == "s1" for s in state["story_backlog"])


def test_open_decompose_cut_no_changes(isolated_log):
    """Mid-iteration snapshot: opened, decomposed, cut — but no state changes."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(2)
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-1"
    assert state["sprint_cut"] == 2
    assert all(v == "planned" for v in state["story_states"].values())
    assert state["close_status"] is None


def test_two_iterations_open_close_open(isolated_log):
    """Open iter-1, close, open iter-2 — current state reflects iter-2."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    _close(accepted_count=1)
    _open_iteration("iter-2", ["5", "6"])
    _decompose([_story("s10", 1), _story("s11", 2)])
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-2"
    assert state["active_iteration"]["requirements"] == ["5", "6"]
    # close_status should be None — a new iteration is now active.
    assert state["close_status"] is None


def test_real_world_with_force_close(isolated_log):
    """Mid-flight abort — force-close the iteration with mixed story states."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(3)
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    _state_change("s2", "planned", "in_progress")
    _state_change("s2", "in_progress", "force_closed")
    _state_change("s3", "planned", "force_closed")
    _close(closed_by="op", reason="customer-abort",
           accepted_count=1, rejected_count=0, force_closed_count=2)
    state = sm.derive_state()
    assert state["active_iteration"] is None
    assert state["story_states"]["s1"] == "accepted"
    assert state["story_states"]["s2"] == "force_closed"
    assert state["story_states"]["s3"] == "force_closed"
    assert state["close_status"]["reason"] == "customer-abort"
    assert state["close_status"]["force_closed_count"] == 2


# ===========================================================================
# Pre-existing log (3+)
# ===========================================================================

def test_pre_existing_log_replays_clean(isolated_log):
    """Log written across "multiple sessions" still derives clean state.

    Simulate by appending a session of entries, then more entries — no
    explicit session boundary in the log, just continued appends.
    """
    import sm
    # "Session 1" — open + decompose
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())

    # "Session 2" — cut + state changes
    _cut(2)
    _state_change("s1", "planned", "in_progress")

    # "Session 3" — finish + close
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    _close(accepted_count=1)

    state = sm.derive_state()
    assert state["active_iteration"] is None
    assert state["sprint_cut"] == 2
    assert state["story_states"]["s1"] == "accepted"
    assert state["close_status"]["accepted_count"] == 1


def test_pre_existing_log_seeded_by_external_writer(isolated_log):
    """Log seeded externally (e.g. canonical bytes from a prior run) replays."""
    import sm
    # Seed: open + decompose only.
    _open_iteration("iter-1", ["1", "2"])
    _decompose([_story("s1", 1), _story("s2", 2)])
    seed_bytes = isolated_log.read_bytes()
    # Simulate restart: same log file, fresh process — derive_state still works.
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-1"
    assert len(state["story_backlog"]) == 2
    # Log bytes unchanged after replay.
    assert isolated_log.read_bytes() == seed_bytes


def test_pre_existing_log_many_entries(isolated_log):
    """A log with many entries replays into one consistent state."""
    import sm
    _open_iteration("iter-1", [str(i) for i in range(1, 11)])
    _decompose([_story(f"s{i}", i, requirement_ids=[str(i)])
                for i in range(1, 11)])
    _cut(5)
    for i in range(1, 6):
        _state_change(f"s{i}", "planned", "in_progress")
        _state_change(f"s{i}", "in_progress", "in_review")
        _state_change(f"s{i}", "in_review", "accepted")
    state = sm.derive_state()
    assert state["sprint_cut"] == 5
    assert len(state["story_backlog"]) == 10
    for i in range(1, 6):
        assert state["story_states"][f"s{i}"] == "accepted"
    for i in range(6, 11):
        assert state["story_states"][f"s{i}"] == "planned"


# ===========================================================================
# LOG_PATH-based (4+)
# ===========================================================================

def test_reads_from_patched_log_path(tmp_path, monkeypatch):
    """Monkeypatching sm.LOG_PATH redirects all reads — proves no hardcoded path."""
    import sm
    custom = tmp_path / "custom_log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", custom)
    # Emit on the patched path.
    _open_iteration("iter-x", ["7"])
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-x"
    assert state["active_iteration"]["requirements"] == ["7"]


def test_does_not_read_from_real_log_path(tmp_path, monkeypatch):
    """If the patched path is empty, derive_state does NOT fall back to real log."""
    import sm
    custom = tmp_path / "patched.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", custom)
    assert not custom.exists()
    state = sm.derive_state()
    # All defaults — proves we read from the patched path, not the real one.
    assert state["active_iteration"] is None
    assert state["story_backlog"] == []
    assert state["story_states"] == {}


def test_log_path_in_nested_directory(tmp_path, monkeypatch):
    import sm
    nested = tmp_path / "nested.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", nested)
    _open_iteration("iter-nested", ["3"])
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-nested"


def test_log_path_change_between_calls(tmp_path, monkeypatch):
    """Changing LOG_PATH between calls redirects derive_state."""
    import sm

    log_a = tmp_path / "a.jsonl"
    log_b = tmp_path / "b.jsonl"

    # Seed log A.
    monkeypatch.setattr(sm, "LOG_PATH", log_a)
    _open_iteration("iter-A", ["1"])
    state_a = sm.derive_state()
    assert state_a["active_iteration"]["iteration_id"] == "iter-A"

    # Switch to log B (empty).
    monkeypatch.setattr(sm, "LOG_PATH", log_b)
    state_b = sm.derive_state()
    assert state_b["active_iteration"] is None


def test_log_path_used_by_derive_state(tmp_path, monkeypatch):
    """derive_state consults sm.LOG_PATH at call time (not import time)."""
    import sm
    log_file = tmp_path / "patched.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    # Append directly using build/append on the patched path.
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-1"
    assert state["story_backlog"][0]["story_id"] == "s1"


# ===========================================================================
# close_status accuracy (4+)
# ===========================================================================

def test_close_status_accepted_count_reflects_close_entry(isolated_log):
    """accepted_count in close_status comes from the close entry itself."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    for sid in ("s1", "s2", "s3"):
        _state_change(sid, "planned", "in_progress")
        _state_change(sid, "in_progress", "in_review")
        _state_change(sid, "in_review", "accepted")
    _close(accepted_count=3, rejected_count=0, force_closed_count=0)
    state = sm.derive_state()
    assert state["close_status"]["accepted_count"] == 3


def test_close_status_rejected_count_reflects_close_entry(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2"])
    _decompose([_story("s1", 1), _story("s2", 2)])
    for sid in ("s1", "s2"):
        _state_change(sid, "planned", "in_progress")
        _state_change(sid, "in_progress", "in_review")
        _state_change(sid, "in_review", "rejected")
    _close(accepted_count=0, rejected_count=2, force_closed_count=0)
    state = sm.derive_state()
    assert state["close_status"]["rejected_count"] == 2


def test_close_status_force_closed_count_reflects_close_entry(isolated_log):
    import sm
    _open_iteration("iter-1", ["1", "2"])
    _decompose([_story("s1", 1), _story("s2", 2)])
    _state_change("s1", "planned", "force_closed")
    _state_change("s2", "planned", "force_closed")
    _close(accepted_count=0, rejected_count=0, force_closed_count=2,
           reason="abort")
    state = sm.derive_state()
    assert state["close_status"]["force_closed_count"] == 2


def test_close_status_mixed_terminal_counts(isolated_log):
    """All three counts populated — mixed terminal outcome."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3", "4"])
    _decompose([_story(f"s{i}", i) for i in range(1, 5)])
    # s1 accepted
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    # s2 rejected
    _state_change("s2", "planned", "in_progress")
    _state_change("s2", "in_progress", "in_review")
    _state_change("s2", "in_review", "rejected")
    # s3 force_closed
    _state_change("s3", "planned", "force_closed")
    # s4 still planned (force_closed at iteration close)
    _state_change("s4", "planned", "force_closed")
    _close(closed_by="op", reason="mixed",
           accepted_count=1, rejected_count=1, force_closed_count=2)
    state = sm.derive_state()
    cs = state["close_status"]
    assert cs["accepted_count"] == 1
    assert cs["rejected_count"] == 1
    assert cs["force_closed_count"] == 2
    assert cs["closed_by"] == "op"
    assert cs["reason"] == "mixed"


def test_close_status_zero_counts_after_immediate_close(isolated_log):
    """Open then close with no work done — all counts zero."""
    import sm
    _open_iteration("iter-1", ["1"])
    _close(accepted_count=0, rejected_count=0, force_closed_count=0)
    state = sm.derive_state()
    cs = state["close_status"]
    assert cs["accepted_count"] == 0
    assert cs["rejected_count"] == 0
    assert cs["force_closed_count"] == 0


# ===========================================================================
# Active iteration cleared on close — close_status remains until new open
# ===========================================================================

def test_close_status_cleared_on_new_open(isolated_log):
    """Opening a new iteration clears the prior close_status."""
    import sm
    _open_iteration("iter-1", ["1"])
    _close(accepted_count=0)
    # After close: close_status is populated.
    state_a = sm.derive_state()
    assert state_a["close_status"] is not None
    # After new open: close_status is None (current iteration is open).
    _open_iteration("iter-2", ["2"])
    state_b = sm.derive_state()
    assert state_b["close_status"] is None
    assert state_b["active_iteration"]["iteration_id"] == "iter-2"


# ===========================================================================
# story_backlog independence — backlog persists across close
# ===========================================================================

def test_backlog_persists_through_close(isolated_log):
    """The closed iteration's backlog is still visible until next open."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _close()
    state = sm.derive_state()
    assert len(state["story_backlog"]) == 3


def test_backlog_replaced_on_new_decompose(isolated_log):
    """A new decompose entry in a fresh iteration replaces the backlog."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _close()
    _open_iteration("iter-2", ["4", "5"])
    _decompose([
        _story("new1", 1, requirement_ids=["4"]),
        _story("new2", 2, requirement_ids=["5"]),
    ])
    state = sm.derive_state()
    backlog_ids = {s["story_id"] for s in state["story_backlog"]}
    assert backlog_ids == {"new1", "new2"}, (
        f"Backlog must be replaced on new decompose; got {backlog_ids!r}"
    )


# ===========================================================================
# Replay reads log only (does not call _append_entry, no I/O side effects)
# ===========================================================================

def test_replay_does_not_call_append_entry(isolated_log, monkeypatch):
    """derive_state must not call _append_entry (would be a write side effect)."""
    import sm

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    # Seed using the real path (calls allowed during seeding).
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    seed_n = calls["n"]
    # Now derive_state — must add 0 calls.
    sm.derive_state()
    assert calls["n"] == seed_n, (
        f"derive_state must not invoke _append_entry; calls increased "
        f"from {seed_n} to {calls['n']}"
    )


def test_replay_log_size_unchanged(isolated_log):
    """File size unchanged across multiple derive_state calls."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _state_change("s1", "planned", "in_progress")
    size_before = isolated_log.stat().st_size
    for _ in range(5):
        sm.derive_state()
    size_after = isolated_log.stat().st_size
    assert size_before == size_after


# ===========================================================================
# Defensive: ignore unknown entry types (forward-compat)
# ===========================================================================
# Pin the permissive replay-layer behavior: an unknown `type` (e.g. emitted
# by a future story) should not crash replay. Replay only enforces the
# state machine for types it knows. Unknown types are no-ops.

def test_unknown_entry_type_is_noop(isolated_log):
    """Forward-compatibility: unknown entry types do not break replay."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    # Emit a future/unknown type.
    _emit("future_event_type", {"foo": "bar"})
    state = sm.derive_state()
    # State derived from the known entries — unaffected by the future entry.
    assert state["active_iteration"]["iteration_id"] == "iter-1"
    assert state["story_backlog"][0]["story_id"] == "s1"
    assert state["story_states"]["s1"] == "planned"


def test_unknown_entry_does_not_create_phantom_states(isolated_log):
    """An unknown type must not add phantom story_id keys to story_states."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])
    _emit("strange_type", {"story_id": "s99", "from_state": "planned",
                           "to_state": "accepted"})
    state = sm.derive_state()
    # 's99' must NOT appear in story_states — it's not in the backlog and the
    # entry type is unknown, so replay ignores it.
    assert "s99" not in state["story_states"]


# ===========================================================================
# Round-trip via real append path — every entry is well-formed
# ===========================================================================

def test_replay_consumes_entries_from_canonical_path(isolated_log):
    """Every entry built via build_entry + appended via _append_entry replays."""
    import sm
    _open_iteration("iter-1", ["1", "2"])
    _decompose([_story("s1", 1), _story("s2", 2)])
    _cut(2)
    _state_change("s1", "planned", "in_progress")
    # Read entries directly — there should be exactly 4.
    entries = list(sm.read_entries())
    assert len(entries) == 4
    # Every entry has id/type/timestamp.
    for e in entries:
        assert "id" in e
        assert "type" in e
        assert "timestamp" in e
    # And derive_state produces a consistent view.
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-1"
    assert state["sprint_cut"] == 2
    assert state["story_states"]["s1"] == "in_progress"


# ===========================================================================
# Sanity: state is JSON-serializable (content-oriented hygiene)
# ===========================================================================

def test_state_is_json_serializable_empty(isolated_log):
    """Empty state must JSON-encode without exceptions."""
    import sm
    state = sm.derive_state()
    json.dumps(state)


def test_state_is_json_serializable_full(isolated_log):
    """Full populated state must JSON-encode without exceptions."""
    import sm
    _open_iteration("iter-1", ["1", "2", "3"])
    _decompose(_three_stories())
    _cut(2)
    _state_change("s1", "planned", "in_progress")
    _state_change("s1", "in_progress", "in_review")
    _state_change("s1", "in_review", "accepted")
    _close(accepted_count=1, force_closed_count=0)
    state = sm.derive_state()
    s = json.dumps(state)
    parsed = json.loads(s)
    assert parsed == state
