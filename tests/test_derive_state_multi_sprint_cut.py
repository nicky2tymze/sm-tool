"""iter4-multisprint-v2 Sprint 1 Story 2 — pin multi-sprint_cut replay contract.

Story:  Update derive_state to track latest sprint_cut entry  (S, req-1)
ID:     046048e3576b4cf08797cc572ee6f63c

Acceptance criteria:
  - derive_state replay iterates through all sprint_cut entries and
    retains only the LATEST (last write wins).
  - state['sprint_cut'] returns the cut_position int from the most
    recent sprint_cut entry.
  - Multiple sprint_cut entries in one iteration replay without error.
  - Unknown entry types remain no-op.

VERIFICATION-ONLY: Story 1's Coder report stated:
  "derive_state already supports multi-sprint_cut replay correctly
   (latest wins). Story 2 will pin/formalize this behavior."

The underlying primitive (_derive_state_full) writes
    state["sprint_cut"] = entry.get("cut_position")
on every sprint_cut entry walked, so by construction the last write
wins. These tests pin that contract explicitly so any future refactor
that breaks latest-wins (e.g., switching to first-wins or summing) is
caught.

Cross-iteration scope (Category C tests) — IMPORTANT CONTRACT NOTE:
  At the REPLAY layer, iteration_close does NOT reset state["sprint_cut"]
  to None — replay walks the entire log, and any sprint_cut entry from a
  PRIOR closed iteration still "wins" if no newer sprint_cut entry exists.
  This is the current contract (confirmed by reading the iteration_close
  branch in _derive_state_full at sm.py:1691-1700: it resets
  active_iteration, iteration_goal, close_status, and the
  _decomposed_since_open flag, but NOT sprint_cut).

  The COMMAND-layer scoping (sprint_cut() lock check resetting on
  iteration_open / iteration_close) lives in the sprint_cut() command,
  not in derive_state, and is pinned by test_sprint_cut_lock.py's
  cross-iteration test (Story 1 cascade).

  Category C tests therefore pin the actual replay-layer behavior:
  state['sprint_cut'] persists across iteration_close (it reflects the
  latest sprint_cut entry anywhere in the log), AND a new sprint_cut
  entry in Iteration B overrides Iteration A's cut.

Suite baseline at write time: 2784/2784 (after Story 1 closed).
"""

from __future__ import annotations

import pathlib
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers (mirror test_derive_state.py conventions)
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file."""
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _emit(type_: str, content: dict) -> dict:
    """Build + append an entry through the canonical path."""
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
    return _emit("story_decomposed", {"stories": list(stories)})


def _cut(n: int, in_sprint_ids=None, deferred_ids=None) -> dict:
    """Emit a sprint_cut entry. By default, in_sprint_story_ids and
    deferred_story_ids are empty — tests that don't care about those
    fields can use _cut(n); tests that DO care pass them explicitly.
    Using the raw _emit path (not sm.sprint_cut) so we can craft
    multi-cut sequences without invoking the lock check."""
    content = {"cut_position": n}
    if in_sprint_ids is not None:
        content["in_sprint_story_ids"] = list(in_sprint_ids)
    if deferred_ids is not None:
        content["deferred_story_ids"] = list(deferred_ids)
    return _emit("sprint_cut", content)


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


def _resolve_terminal(story_id: str, terminal: str = "accepted") -> None:
    """Drive a story all the way to a terminal state through the legal
    planned -> in_progress -> in_review -> {accepted|rejected} chain (or
    planned -> force_closed direct)."""
    if terminal == "force_closed":
        _state_change(story_id, "planned", "force_closed")
        return
    _state_change(story_id, "planned", "in_progress")
    _state_change(story_id, "in_progress", "in_review")
    _state_change(story_id, "in_review", terminal)


# ===========================================================================
# A. Multi-cut replay smoke (4 tests)
#
# Pin: derive_state replay tolerates 2+, 3+, and N sprint_cut entries
# without error, and the single-cut case is preserved unchanged.
# ===========================================================================


def test_two_sprint_cuts_replay_without_error(isolated_log):
    """Two sprint_cut entries (terminal-resolved between) — derive_state
    walks the log without raising."""
    import sm
    stories = [_story("s1", 1), _story("s2", 2), _story("s3", 3)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(1, in_sprint_ids=["s1"])
    _resolve_terminal("s1", "accepted")
    _cut(2, in_sprint_ids=["s1", "s2"])
    # Replay must succeed — no exception.
    state = sm.derive_state()
    assert state is not None


def test_three_sprint_cuts_replay_without_error(isolated_log):
    """Three sprint_cut entries (each prior cohort terminal-resolved) —
    derive_state walks the log without raising."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 5)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(1, in_sprint_ids=["s1"])
    _resolve_terminal("s1", "accepted")
    _cut(2, in_sprint_ids=["s1", "s2"])
    _resolve_terminal("s2", "accepted")
    _cut(3, in_sprint_ids=["s1", "s2", "s3"])
    state = sm.derive_state()
    assert state is not None


def test_single_sprint_cut_unchanged(isolated_log):
    """Existing single-cut case still works (regression guard)."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1), _story("s2", 2), _story("s3", 3)])
    _cut(2, in_sprint_ids=["s1", "s2"])
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_many_sprint_cuts_replay_without_error(isolated_log):
    """Five sprint_cut entries — replay walks the whole log, last wins."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 7)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    for i in range(1, 6):
        sid = f"s{i}"
        _cut(i, in_sprint_ids=[sid])
        _resolve_terminal(sid, "accepted")
    # Final cut covers s1..s6.
    _cut(6, in_sprint_ids=[f"s{j}" for j in range(1, 7)])
    state = sm.derive_state()
    assert state["sprint_cut"] == 6


# ===========================================================================
# B. Latest-wins semantics (5 tests)
#
# Pin: state["sprint_cut"] is the cut_position from the LAST sprint_cut
# entry walked — not the max, not the min, not the first. Latest wins
# even when it's smaller than a prior cut. Idempotent on duplicates.
# ===========================================================================


def test_two_cuts_latest_wins_three_then_five(isolated_log):
    """cut_position 3 then cut_position 5 → state['sprint_cut'] == 5."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 7)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(3, in_sprint_ids=["s1", "s2", "s3"])
    _resolve_terminal("s1", "accepted")
    _resolve_terminal("s2", "accepted")
    _resolve_terminal("s3", "accepted")
    _cut(5, in_sprint_ids=["s1", "s2", "s3", "s4", "s5"])
    state = sm.derive_state()
    assert state["sprint_cut"] == 5


def test_two_cuts_latest_wins_five_then_two_decrease(isolated_log):
    """cut_position 5 then cut_position 2 → state['sprint_cut'] == 2.
    Latest wins even when the latest is SMALLER than a prior cut. Pins
    that derive_state does not compute max/min — just last-write-wins."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 7)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(5, in_sprint_ids=["s1", "s2", "s3", "s4", "s5"])
    for sid in ("s1", "s2", "s3", "s4", "s5"):
        _resolve_terminal(sid, "accepted")
    _cut(2, in_sprint_ids=["s1", "s2"])
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_same_cut_position_twice_is_idempotent(isolated_log):
    """Two sprint_cut entries with the same cut_position → state's
    cut_position matches that position (and replay doesn't raise)."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1), _story("s2", 2), _story("s3", 3)])
    _cut(2, in_sprint_ids=["s1", "s2"])
    _resolve_terminal("s1", "accepted")
    _resolve_terminal("s2", "accepted")
    _cut(2, in_sprint_ids=["s1", "s2"])
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_sprint_cut_is_int_not_list(isolated_log):
    """state['sprint_cut'] remains a plain int after multiple cuts —
    pins that no multi-sprint helper was introduced that would change
    the public type to a list or dict."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 5)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(1, in_sprint_ids=["s1"])
    _resolve_terminal("s1", "accepted")
    _cut(3, in_sprint_ids=["s1", "s2", "s3"])
    state = sm.derive_state()
    assert isinstance(state["sprint_cut"], int)
    assert state["sprint_cut"] == 3


def test_intermediate_cut_not_lost_from_log(isolated_log):
    """All sprint_cut entries remain in the log (read_entries returns
    them all); only the LATEST is active per state['sprint_cut']."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 5)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(1, in_sprint_ids=["s1"])
    _resolve_terminal("s1", "accepted")
    _cut(2, in_sprint_ids=["s1", "s2"])
    _resolve_terminal("s2", "accepted")
    _cut(3, in_sprint_ids=["s1", "s2", "s3"])

    cut_entries = [
        e for e in sm.read_entries() if e.get("type") == "sprint_cut"
    ]
    assert len(cut_entries) == 3
    assert [e["cut_position"] for e in cut_entries] == [1, 2, 3]
    # But only the latest is reflected in derived state.
    state = sm.derive_state()
    assert state["sprint_cut"] == 3


# ===========================================================================
# C. Cross-iteration replay behavior (2 tests)
#
# Pin the CURRENT replay-layer contract: iteration_close does NOT reset
# state["sprint_cut"]; replay walks the whole log and the latest
# sprint_cut entry wins regardless of which iteration it came from. A
# new sprint_cut entry in Iteration B overrides Iteration A's cut.
#
# (The COMMAND-layer iteration-scoping of the re-cut lock is a separate
# concern, pinned by test_sprint_cut_lock.py — not duplicated here.)
# ===========================================================================


def test_replay_state_sprint_cut_persists_across_iteration_close(
    isolated_log,
):
    """After iteration_close, state['sprint_cut'] retains the last
    sprint_cut entry's value (replay walks the full log).

    This pins the current replay-layer contract. If the contract ever
    changes (e.g., to reset on close for symmetry with active_iteration),
    this test will fail and force a deliberate update to the contract
    docstring + this test. That's the desired behavior."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 4)]
    _open_iteration("iter-A", ["1"])
    _decompose(stories)
    _cut(2, in_sprint_ids=["s1", "s2"])
    _resolve_terminal("s1", "accepted")
    _resolve_terminal("s2", "accepted")
    _close(accepted_count=2)

    state = sm.derive_state()
    # active_iteration is reset on close, but sprint_cut persists.
    assert state["active_iteration"] is None
    assert state["sprint_cut"] == 2


def test_replay_state_sprint_cut_from_iteration_b_overrides_a(
    isolated_log,
):
    """A sprint_cut entry in iteration B overrides a sprint_cut entry
    from a prior closed iteration A — latest-wins is global across the
    log, not scoped per-iteration at the replay layer."""
    import sm
    # Iteration A — cut at position 2, close.
    stories_a = [_story(f"a{i}", i) for i in range(1, 4)]
    _open_iteration("iter-A", ["1"])
    _decompose(stories_a)
    _cut(2, in_sprint_ids=["a1", "a2"])
    _resolve_terminal("a1", "accepted")
    _resolve_terminal("a2", "accepted")
    _resolve_terminal("a3", "accepted")
    _close(accepted_count=3)

    # Iteration B — fresh open, decompose, cut at position 1.
    stories_b = [
        {**_story(f"b{i}", i), "story_id": f"b{i}"}
        for i in range(1, 3)
    ]
    _open_iteration("iter-B", ["2"])
    _decompose(stories_b)
    _cut(1, in_sprint_ids=["b1"])

    state = sm.derive_state()
    assert state["sprint_cut"] == 1
    assert state["active_iteration"]["iteration_id"] == "iter-B"


# ===========================================================================
# D. latest_in_sprint_story_ids from latest cut (3 tests)
#
# These pin the internal _derive_state_full primitive's third return
# slot: latest_in_sprint_story_ids reflects the LATEST sprint_cut entry,
# not an earlier one. Public state shape unchanged (no in_sprint_ids on
# state['sprint_cut']). close_iteration consumes the private helper, so
# this contract matters even though it isn't surfaced publicly.
# ===========================================================================


def test_latest_in_sprint_story_ids_from_latest_cut(isolated_log):
    """_derive_state_full returns the LATEST sprint_cut entry's
    in_sprint_story_ids in its third tuple slot."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 5)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(1, in_sprint_ids=["s1"])
    _resolve_terminal("s1", "accepted")
    _cut(3, in_sprint_ids=["s1", "s2", "s3"])

    _state, _seen, latest_in_sprint, _cohorts = sm._derive_state_full()
    assert latest_in_sprint == ["s1", "s2", "s3"]


def test_latest_in_sprint_story_ids_smaller_cut_still_wins(isolated_log):
    """When the latest cut is SMALLER, latest_in_sprint_story_ids
    reflects that smaller set — not the prior larger one."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 5)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(3, in_sprint_ids=["s1", "s2", "s3"])
    _resolve_terminal("s1", "accepted")
    _resolve_terminal("s2", "accepted")
    _resolve_terminal("s3", "accepted")
    _cut(1, in_sprint_ids=["s1"])

    _state, _seen, latest_in_sprint, _cohorts = sm._derive_state_full()
    assert latest_in_sprint == ["s1"]


def test_latest_in_sprint_story_ids_empty_when_no_cuts(isolated_log):
    """No sprint_cut entries → latest_in_sprint_story_ids is []."""
    import sm
    _open_iteration("iter-1", ["1"])
    _decompose([_story("s1", 1)])

    _state, _seen, latest_in_sprint, _cohorts = sm._derive_state_full()
    assert latest_in_sprint == []


# ===========================================================================
# E. Unknown entry tolerance (2 tests)
#
# Pin: derive_state's existing tolerance for unknown entry types is
# preserved alongside multi-sprint_cut replay. An unknown-type entry
# between/around sprint_cut entries must not affect state['sprint_cut']
# or raise an exception.
# ===========================================================================


def test_unknown_entry_between_sprint_cuts_does_not_affect_state(
    isolated_log,
):
    """An unknown-type entry interleaved with sprint_cut entries replays
    cleanly; state['sprint_cut'] still reflects the latest sprint_cut."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 4)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(1, in_sprint_ids=["s1"])
    _resolve_terminal("s1", "accepted")
    # Unknown-type entry — derive_state must no-op past it.
    _emit("some_future_entry_type", {"payload": {"k": "v"}})
    _cut(2, in_sprint_ids=["s1", "s2"])

    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_unknown_entry_after_sprint_cuts_does_not_raise(isolated_log):
    """Unknown entry after multiple sprint_cut entries — replay still
    succeeds and state['sprint_cut'] reflects the latest sprint_cut."""
    import sm
    stories = [_story(f"s{i}", i) for i in range(1, 4)]
    _open_iteration("iter-1", ["1"])
    _decompose(stories)
    _cut(1, in_sprint_ids=["s1"])
    _resolve_terminal("s1", "accepted")
    _cut(2, in_sprint_ids=["s1", "s2"])
    _emit("trailing_unknown_type", {"junk": [1, 2, 3]})

    state = sm.derive_state()
    assert state["sprint_cut"] == 2
