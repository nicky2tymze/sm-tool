"""Story 11 — pin the contract of `sm.sprint_cut`.

What this file pins:

  - Function signature and shape:
      `sprint_cut(n: int) -> dict`
    PUBLIC, callable, in `sm.__all__`, importable as
    `from sm import sprint_cut`. Returns the appended `sprint_cut` log
    entry on success.

  - Required behavior:
      * Reads the active iteration's story backlog via `derive_state()`.
      * Validates 1 <= N <= len(backlog). On success, writes a single
        `sprint_cut` log entry whose content includes:
            - `cut_position`: int N (the field Story 4's replay reads)
            - `in_sprint_story_ids`: [story_ids 1..N] (in sequence order)
            - `deferred_story_ids`: [story_ids N+1..end] (in sequence order)
      * Replay: Story 4's `derive_state` already treats the LATEST
        `sprint_cut` entry as authoritative — earlier cuts are superseded.
        Story 11 just writes a new entry; re-cut is allowed regardless of
        story states (the lock-when-not-planned rule is Story 12's job).

  - Typed exception class: `SprintCutError(ValueError)`.
      * Raised for: no active iteration; no story_backlog yet; N out of
        range (zero, negative, > len(backlog)).
      * `TypeError` (NOT `SprintCutError`) for non-int N (str, float, etc.).
      * Subclasses ValueError so existing `except ValueError` callers keep
        working, while callers can branch on the class.

  - Failure invariant: log.jsonl is byte-for-byte unchanged on any
    validation/argument failure.

  - CLI surface: `python -m sm sprint-cut <N>` exits 0 on success, exits
    non-zero (and writes nothing) on any validation failure.

  - Story 12 deferral: tests do NOT pin the lock-when-stories-leave-planned
    rule. Story 11 always allows re-cut regardless of story states.

Tests must FAIL on first run — `sprint_cut` and `SprintCutError` do not
exist yet. The Coder downstream implements the function and the typed
error to satisfy these tests.
"""

from __future__ import annotations

import inspect
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
    """Redirect `sm.LOG_PATH` to a per-test tmp file.

    Mirrors the suite convention.
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _open_iteration(iteration_id: str = "iter-1",
                    requirements=None) -> dict:
    """Append an `iteration_open` entry directly via build_entry +
    _append_entry so a subsequent sprint_cut() has an active iteration to
    work against.
    """
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
    """Append a `story_backlog` entry with N canonical stories. Returns the
    list of minted story_ids (in sequence order).

    Bypasses `decompose()` so Story 11's tests don't depend on Story 9's
    role-spec wiring (we don't need a roles/ dir staged in tmp_path).
    """
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
    """Convenience: open iteration + decompose backlog. Returns story_ids."""
    _open_iteration(iteration_id=iteration_id)
    return _seed_backlog(n=n_stories)


# ===========================================================================
# Smoke (5+) — function exists, callable, public, in __all__, accepts int
# ===========================================================================


def test_function_exists_on_module():
    import sm
    assert hasattr(sm, "sprint_cut"), "sm.sprint_cut must exist"


def test_function_is_callable():
    import sm
    assert callable(sm.sprint_cut)


def test_function_name_is_public():
    """No leading underscore — public API."""
    import sm
    assert not sm.sprint_cut.__name__.startswith("_")
    assert sm.sprint_cut.__name__ == "sprint_cut"


def test_function_importable_directly():
    """`from sm import sprint_cut` succeeds — public-import form."""
    from sm import sprint_cut  # noqa: F401
    assert callable(sprint_cut)


def test_function_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "sprint_cut" in sm.__all__, (
        f"sprint_cut must be in __all__; got {sm.__all__!r}"
    )


def test_signature_accepts_n_positional():
    """sprint_cut accepts a positional integer argument."""
    import sm
    sig = inspect.signature(sm.sprint_cut)
    params = list(sig.parameters)
    assert len(params) >= 1, (
        f"sprint_cut must accept at least one parameter; "
        f"got params {params!r}"
    )


# ===========================================================================
# Typed error class — SprintCutError (5+)
# ===========================================================================


def test_sprint_cut_error_class_exists():
    """SprintCutError must exist on sm."""
    import sm
    assert hasattr(sm, "SprintCutError"), "sm.SprintCutError must exist"


def test_sprint_cut_error_subclasses_value_error():
    """SprintCutError subclasses ValueError so existing
    `except ValueError` callers keep working."""
    import sm
    assert issubclass(sm.SprintCutError, ValueError), (
        "SprintCutError must subclass ValueError"
    )


def test_sprint_cut_error_in_dunder_all():
    """Public typed error — exported via __all__."""
    import sm
    assert "SprintCutError" in sm.__all__, (
        f"SprintCutError must be in __all__; got {sm.__all__!r}"
    )


def test_sprint_cut_error_caught_as_value_error(isolated_log):
    """A bare `except ValueError` clause catches SprintCutError."""
    import sm
    # No active iteration → SprintCutError, must be ValueError-catchable.
    caught = False
    try:
        sm.sprint_cut(1)
    except ValueError:
        caught = True
    assert caught, "SprintCutError must be catchable as ValueError"


def test_sprint_cut_error_is_distinct_class():
    """SprintCutError is not the same as ValueError itself."""
    import sm
    assert sm.SprintCutError is not ValueError


# ===========================================================================
# Happy path (8+) — valid N writes a sprint_cut entry
# ===========================================================================


def test_happy_path_writes_one_entry(isolated_log):
    """A valid sprint_cut call appends exactly one new log entry."""
    import sm
    _seed_full(n_stories=5)
    before = list(sm.read_entries())
    sm.sprint_cut(3)
    after = list(sm.read_entries())
    assert len(after) == len(before) + 1


def test_happy_path_entry_type_is_sprint_cut(isolated_log):
    """The single emitted entry has type `sprint_cut`."""
    import sm
    _seed_full(n_stories=5)
    sm.sprint_cut(3)
    entries = list(sm.read_entries())
    assert entries[-1]["type"] == "sprint_cut", (
        f"latest entry type must be 'sprint_cut'; "
        f"got {entries[-1]['type']!r}"
    )


def test_happy_path_returns_appended_entry(isolated_log):
    """sprint_cut returns the dict that was appended to the log."""
    import sm
    _seed_full(n_stories=5)
    result = sm.sprint_cut(3)
    entries = list(sm.read_entries())
    assert result == entries[-1]


def test_happy_path_return_value_is_dict(isolated_log):
    import sm
    _seed_full(n_stories=4)
    result = sm.sprint_cut(2)
    assert isinstance(result, dict)


def test_happy_path_entry_has_canonical_fields(isolated_log):
    """The emitted entry has id, type, timestamp from build_entry."""
    import sm
    _seed_full(n_stories=4)
    result = sm.sprint_cut(2)
    assert "id" in result
    assert "type" in result
    assert "timestamp" in result


def test_happy_path_cut_position_recorded(isolated_log):
    """The entry carries `cut_position` as the int N."""
    import sm
    _seed_full(n_stories=5)
    result = sm.sprint_cut(3)
    assert result["cut_position"] == 3


def test_happy_path_in_sprint_story_ids_is_first_n(isolated_log):
    """`in_sprint_story_ids` lists story_ids 1..N in sequence order."""
    import sm
    sids = _seed_full(n_stories=5)
    result = sm.sprint_cut(3)
    assert result["in_sprint_story_ids"] == sids[:3]


def test_happy_path_deferred_story_ids_is_remainder(isolated_log):
    """`deferred_story_ids` lists story_ids N+1..end in sequence order."""
    import sm
    sids = _seed_full(n_stories=5)
    result = sm.sprint_cut(3)
    assert result["deferred_story_ids"] == sids[3:]


def test_happy_path_in_sprint_and_deferred_are_lists(isolated_log):
    """Both id fields are lists, not tuples / sets / strings."""
    import sm
    _seed_full(n_stories=4)
    result = sm.sprint_cut(2)
    assert isinstance(result["in_sprint_story_ids"], list)
    assert isinstance(result["deferred_story_ids"], list)


def test_happy_path_in_sprint_plus_deferred_equals_backlog(isolated_log):
    """The two id lists partition the backlog exactly."""
    import sm
    sids = _seed_full(n_stories=6)
    result = sm.sprint_cut(4)
    assert (result["in_sprint_story_ids"]
            + result["deferred_story_ids"]) == sids


def test_happy_path_derive_state_sprint_cut_updated(isolated_log):
    """After sprint_cut, `derive_state().sprint_cut == N`."""
    import sm
    _seed_full(n_stories=5)
    sm.sprint_cut(3)
    state = sm.derive_state()
    assert state["sprint_cut"] == 3


def test_happy_path_derive_state_unchanged_otherwise(isolated_log):
    """sprint_cut does not mutate active_iteration or story_backlog."""
    import sm
    _seed_full(n_stories=5)
    before = sm.derive_state()
    sm.sprint_cut(2)
    after = sm.derive_state()
    assert before["active_iteration"] == after["active_iteration"]
    assert before["story_backlog"] == after["story_backlog"]
    assert before["story_states"] == after["story_states"]


# ===========================================================================
# N validation (10+) — out-of-range raises SprintCutError, no log write
# ===========================================================================


def test_n_zero_raises_sprint_cut_error(isolated_log):
    """N=0 → SprintCutError (must be >= 1)."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(0)


def test_n_negative_one_raises_sprint_cut_error(isolated_log):
    """N=-1 → SprintCutError."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(-1)


def test_n_large_negative_raises_sprint_cut_error(isolated_log):
    """N=-99 → SprintCutError."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(-99)


def test_n_just_over_backlog_raises_sprint_cut_error(isolated_log):
    """N = len(backlog) + 1 → SprintCutError."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(6)


def test_n_far_over_backlog_raises_sprint_cut_error(isolated_log):
    """N = 99999 (way over) → SprintCutError."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(99999)


def test_n_zero_error_message_mentions_position(isolated_log):
    """N=0's error message names position/value (helpful for operator)."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(sm.SprintCutError) as exc_info:
        sm.sprint_cut(0)
    msg = str(exc_info.value).lower()
    assert "position" in msg or "0" in msg or ">= 1" in msg, (
        f"error message should name position; got: {exc_info.value!s}"
    )


def test_n_too_large_error_message_mentions_length(isolated_log):
    """N>len's error message names backlog length (helpful for operator)."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(sm.SprintCutError) as exc_info:
        sm.sprint_cut(99)
    msg = str(exc_info.value).lower()
    assert ("length" in msg or "backlog" in msg
            or "exceed" in msg or "5" in msg or "99" in msg), (
        f"error message should name backlog length; got: {exc_info.value!s}"
    )


def test_n_float_raises_type_error(isolated_log):
    """N=1.5 → TypeError (non-int)."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(TypeError):
        sm.sprint_cut(1.5)


def test_n_integer_valued_float_raises_type_error(isolated_log):
    """N=2.0 → TypeError (a float is not an int, even if integer-valued)."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(TypeError):
        sm.sprint_cut(2.0)


def test_n_str_raises_type_error(isolated_log):
    """N='3' → TypeError (string is not int)."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(TypeError):
        sm.sprint_cut("3")


def test_n_none_raises_type_error(isolated_log):
    """N=None → TypeError."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(TypeError):
        sm.sprint_cut(None)


def test_n_list_raises_type_error(isolated_log):
    """N=[1] → TypeError."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(TypeError):
        sm.sprint_cut([1])


def test_n_bool_true_raises_type_error(isolated_log):
    """N=True → TypeError. bool is technically a subclass of int but the
    Story 4 pattern (decompose's int validation) rejects bool — same here."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(TypeError):
        sm.sprint_cut(True)


def test_n_bool_false_raises_type_error(isolated_log):
    """N=False → TypeError (same reasoning as True)."""
    import sm
    _seed_full(n_stories=5)
    with pytest.raises(TypeError):
        sm.sprint_cut(False)


# ===========================================================================
# No active iteration (3+) — SprintCutError + log unchanged
# ===========================================================================


def test_no_active_iteration_empty_log_raises(isolated_log):
    """Empty log → SprintCutError."""
    import sm
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1)


def test_no_active_iteration_after_close_raises(isolated_log):
    """An iteration that's been closed leaves no active iteration."""
    import sm
    _seed_full(n_stories=3, iteration_id="iter-1")
    close = sm.build_entry("iteration_close", {
        "closed_by": "operator", "reason": None,
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 0,
    })
    sm._append_entry(close)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1)


def test_no_active_iteration_log_unchanged(isolated_log):
    """No active iteration + sprint_cut attempt → log byte-for-byte same."""
    import sm
    # Seed a benign entry so the log is non-empty.
    seed = sm.build_entry("sprint_cut_test_seed", {"marker": "before"})
    sm._append_entry(seed)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1)
    assert isolated_log.read_bytes() == bytes_before


def test_no_active_iteration_derive_state_unchanged(isolated_log):
    """derive_state before/after the failed call is equal."""
    import sm
    seed = sm.build_entry("sprint_cut_test_seed", {"marker": "before"})
    sm._append_entry(seed)
    before = sm.derive_state()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1)
    after = sm.derive_state()
    assert before == after


def test_no_active_iteration_error_message_mentions_iteration(isolated_log):
    """The error message names the missing-iteration condition."""
    import sm
    with pytest.raises(sm.SprintCutError) as exc_info:
        sm.sprint_cut(1)
    msg = str(exc_info.value).lower()
    assert "iteration" in msg, (
        f"error must mention 'iteration'; got: {exc_info.value!s}"
    )


# ===========================================================================
# No backlog yet (3+) — SprintCutError + log unchanged
# ===========================================================================


def test_no_backlog_yet_raises_sprint_cut_error(isolated_log):
    """Iteration is open but decompose hasn't run → SprintCutError."""
    import sm
    _open_iteration()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1)


def test_no_backlog_yet_log_unchanged(isolated_log):
    """No backlog + sprint_cut → log byte-for-byte unchanged."""
    import sm
    _open_iteration()
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1)
    assert isolated_log.read_bytes() == bytes_before


def test_no_backlog_yet_error_mentions_backlog_or_decompose(isolated_log):
    """The error message names the missing-backlog condition."""
    import sm
    _open_iteration()
    with pytest.raises(sm.SprintCutError) as exc_info:
        sm.sprint_cut(1)
    msg = str(exc_info.value).lower()
    assert ("backlog" in msg or "decompose" in msg
            or "stories" in msg), (
        f"error must mention backlog/decompose/stories; "
        f"got: {exc_info.value!s}"
    )


def test_no_backlog_yet_derive_state_unchanged(isolated_log):
    """derive_state before/after the failed call is equal."""
    import sm
    _open_iteration()
    before = sm.derive_state()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1)
    after = sm.derive_state()
    assert before == after


# ===========================================================================
# Re-cut behavior (5+) — latest cut wins on replay
# ===========================================================================


def test_recut_3_then_5_replay_shows_5(isolated_log):
    """Cut at 3, then cut at 5 → derive_state shows 5 (latest wins)."""
    import sm
    _seed_full(n_stories=6)
    sm.sprint_cut(3)
    sm.sprint_cut(5)
    state = sm.derive_state()
    assert state["sprint_cut"] == 5


def test_recut_5_then_2_replay_shows_2(isolated_log):
    """Cut at 5, then cut at 2 → derive_state shows 2 (latest wins,
    even when the new cut is smaller)."""
    import sm
    _seed_full(n_stories=6)
    sm.sprint_cut(5)
    sm.sprint_cut(2)
    state = sm.derive_state()
    assert state["sprint_cut"] == 2


def test_recut_writes_two_entries(isolated_log):
    """Each successful cut writes a new entry — re-cut does not erase."""
    import sm
    _seed_full(n_stories=6)
    before = list(sm.read_entries())
    sm.sprint_cut(3)
    sm.sprint_cut(5)
    after = list(sm.read_entries())
    assert len(after) == len(before) + 2


def test_recut_many_times_latest_wins(isolated_log):
    """Many recuts in a row — derive_state shows the very last."""
    import sm
    _seed_full(n_stories=8)
    sm.sprint_cut(1)
    sm.sprint_cut(2)
    sm.sprint_cut(3)
    sm.sprint_cut(4)
    sm.sprint_cut(7)
    state = sm.derive_state()
    assert state["sprint_cut"] == 7


def test_recut_to_same_value_is_legal(isolated_log):
    """Cutting at the same N twice is legal at Story 11 (the lock for
    state-changes-out-of-planned is Story 12)."""
    import sm
    _seed_full(n_stories=5)
    sm.sprint_cut(3)
    # Second cut to the same value — must not raise (Story 11 contract).
    sm.sprint_cut(3)
    state = sm.derive_state()
    assert state["sprint_cut"] == 3


def test_recut_in_sprint_ids_track_latest(isolated_log):
    """The latest sprint_cut entry's in_sprint_story_ids reflects the
    latest N — the older entries are inert on replay (Story 4 ignores them)."""
    import sm
    sids = _seed_full(n_stories=6)
    sm.sprint_cut(2)
    second = sm.sprint_cut(4)
    # Latest entry's content reflects N=4.
    assert second["in_sprint_story_ids"] == sids[:4]
    assert second["deferred_story_ids"] == sids[4:]


def test_recut_invalid_n_does_not_overwrite_prior(isolated_log):
    """A failed re-cut (invalid N) must not overwrite the prior cut."""
    import sm
    _seed_full(n_stories=5)
    sm.sprint_cut(3)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(99)
    state = sm.derive_state()
    assert state["sprint_cut"] == 3


# ===========================================================================
# Failure invariants (5+) — log byte-for-byte unchanged on any failure
# ===========================================================================


def test_log_unchanged_after_n_zero(isolated_log):
    """N=0 → log unchanged."""
    import sm
    _seed_full(n_stories=5)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(0)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_n_negative(isolated_log):
    """N<0 → log unchanged."""
    import sm
    _seed_full(n_stories=5)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(-3)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_n_too_large(isolated_log):
    """N>len(backlog) → log unchanged."""
    import sm
    _seed_full(n_stories=5)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1000)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_type_error_float(isolated_log):
    """Non-int N → log unchanged."""
    import sm
    _seed_full(n_stories=5)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.sprint_cut(1.5)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_type_error_str(isolated_log):
    """Non-int N (str) → log unchanged."""
    import sm
    _seed_full(n_stories=5)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.sprint_cut("3")
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_no_active_iteration(isolated_log):
    """No iteration → log unchanged (already pinned, mirrored here for
    failure-invariant completeness)."""
    import sm
    seed = sm.build_entry("sprint_cut_test_seed", {"marker": "before"})
    sm._append_entry(seed)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_no_backlog(isolated_log):
    """No backlog → log unchanged."""
    import sm
    _open_iteration()
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(1)
    assert isolated_log.read_bytes() == bytes_before


def test_append_entry_not_called_on_failure(isolated_log, monkeypatch):
    """On any validation failure, _append_entry must NOT be called.
    Pin the wire-up — failure invariants flow through the no-call path."""
    import sm
    _seed_full(n_stories=5)

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(99)
    assert calls["n"] == 0, (
        f"_append_entry must not be called on validation failure; "
        f"got {calls['n']} call(s)"
    )


# ===========================================================================
# build_entry / _append_entry wiring (3+)
# ===========================================================================


def test_uses_build_entry(isolated_log, monkeypatch):
    """sprint_cut must go through sm.build_entry for the sprint_cut entry."""
    import sm
    _seed_full(n_stories=5)

    calls = {"n": 0, "types": []}
    real = sm.build_entry

    def fake(type_, content):
        calls["n"] += 1
        calls["types"].append(type_)
        return real(type_, content)

    monkeypatch.setattr(sm, "build_entry", fake)
    sm.sprint_cut(3)
    assert "sprint_cut" in calls["types"], (
        f"sprint_cut must call build_entry(type='sprint_cut'); "
        f"got types {calls['types']!r}"
    )


def test_uses_append_entry(isolated_log, monkeypatch):
    """sprint_cut must go through sm._append_entry for the sprint_cut entry."""
    import sm
    _seed_full(n_stories=5)

    calls = {"n": 0, "entries": []}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        calls["entries"].append(entry)
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    sm.sprint_cut(3)
    assert calls["n"] == 1, (
        f"sprint_cut must call _append_entry exactly once; got {calls['n']}"
    )
    assert calls["entries"][0]["type"] == "sprint_cut"


def test_entry_round_trips_through_read_entries(isolated_log):
    """The entry returned by sprint_cut() equals the entry read back."""
    import sm
    _seed_full(n_stories=5)
    returned = sm.sprint_cut(3)
    entries = list(sm.read_entries())
    assert returned == entries[-1]


def test_entry_is_json_serializable(isolated_log):
    """The written entry survives a json.dumps round-trip."""
    import sm
    _seed_full(n_stories=5)
    e = sm.sprint_cut(3)
    s = json.dumps(e)
    assert json.loads(s) == e


# ===========================================================================
# Edge cases (5+)
# ===========================================================================


def test_n_equals_1_smallest_legal_sprint(isolated_log):
    """N=1 — minimum legal cut. Sprint has 1 story; rest are deferred."""
    import sm
    sids = _seed_full(n_stories=5)
    result = sm.sprint_cut(1)
    assert result["cut_position"] == 1
    assert result["in_sprint_story_ids"] == [sids[0]]
    assert result["deferred_story_ids"] == sids[1:]


def test_n_equals_len_whole_backlog_in_sprint(isolated_log):
    """N=len(backlog) — entire backlog is in sprint, deferred is empty."""
    import sm
    sids = _seed_full(n_stories=5)
    result = sm.sprint_cut(5)
    assert result["cut_position"] == 5
    assert result["in_sprint_story_ids"] == sids
    assert result["deferred_story_ids"] == []


def test_one_story_backlog_cut_at_1(isolated_log):
    """Backlog of 1 story, cut at 1 → in_sprint=[sid], deferred=[]."""
    import sm
    sids = _seed_full(n_stories=1)
    result = sm.sprint_cut(1)
    assert result["cut_position"] == 1
    assert result["in_sprint_story_ids"] == sids
    assert result["deferred_story_ids"] == []


def test_one_story_backlog_cut_at_2_raises(isolated_log):
    """Backlog of 1 story, cut at 2 → SprintCutError."""
    import sm
    _seed_full(n_stories=1)
    with pytest.raises(sm.SprintCutError):
        sm.sprint_cut(2)


def test_five_stories_cut_at_5(isolated_log):
    """Decompose 5 stories + cut at 5 → all in sprint, none deferred."""
    import sm
    sids = _seed_full(n_stories=5)
    result = sm.sprint_cut(5)
    assert len(result["in_sprint_story_ids"]) == 5
    assert result["deferred_story_ids"] == []
    assert result["in_sprint_story_ids"] == sids


def test_large_backlog_cut_in_middle(isolated_log):
    """Decompose 10 stories + cut at 5 → 5 in sprint, 5 deferred."""
    import sm
    sids = _seed_full(n_stories=10)
    result = sm.sprint_cut(5)
    assert len(result["in_sprint_story_ids"]) == 5
    assert len(result["deferred_story_ids"]) == 5
    assert result["in_sprint_story_ids"] == sids[:5]
    assert result["deferred_story_ids"] == sids[5:]


def test_in_sprint_order_matches_sequence(isolated_log):
    """in_sprint_story_ids is in sequence (1..N) order, not insertion order."""
    import sm
    sids = _seed_full(n_stories=4)
    result = sm.sprint_cut(3)
    # Sequence order matches our seeded order — confirmed by id positions.
    assert result["in_sprint_story_ids"][0] == sids[0]
    assert result["in_sprint_story_ids"][1] == sids[1]
    assert result["in_sprint_story_ids"][2] == sids[2]


# ===========================================================================
# CLI surface (4+) — `python -m sm sprint-cut <N>`
# ===========================================================================


def test_cli_sprint_cut_command_known(tmp_path):
    """`python -m sm sprint-cut 1` is a known command — does NOT exit with
    the 'unknown command' status. Without an active iteration, the CLI
    must exit non-zero, but with a recognized-command failure path."""
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
        f"CLI must recognize 'sprint-cut' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_sprint_cut_no_active_iteration_exits_nonzero(tmp_path):
    """`python -m sm sprint-cut 1` with an empty log exits non-zero."""
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
    assert result.returncode != 0, (
        f"sprint-cut with no active iteration must exit non-zero;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'sprint-cut' and fail with a real error, "
        f"not 'unknown command';\nstdout={result.stdout!r}\n"
        f"stderr={result.stderr!r}"
    )


def test_cli_sprint_cut_invalid_n_exits_nonzero(tmp_path):
    """`python -m sm sprint-cut -1` (with active iter + backlog) exits
    non-zero with the out-of-range path — NOT 'unknown command'."""
    import sm

    log_path = tmp_path / "cli_log.jsonl"

    # Seed the log via direct LOG_PATH redirection so the CLI sees an
    # active iteration + backlog; the failure path we want to pin is
    # 'N out of range', not 'no active iteration' and not 'unknown command'.
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration(iteration_id="cli-iter-cut-invalid")
        _seed_backlog(n=5)
    finally:
        sm.LOG_PATH = orig_log

    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(log_path)

    result = subprocess.run(
        [sys.executable, "-m", "sm", "sprint-cut", "-1"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"sprint-cut with invalid N must exit non-zero;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'sprint-cut' and fail with the out-of-range "
        f"path, not 'unknown command';\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_sprint_cut_valid_n_exits_zero(tmp_path):
    """`python -m sm sprint-cut <valid N>` exits 0 when an active iteration
    + backlog exist on the log being pointed at via SM_LOG_PATH.

    We seed the log directly via the same module the CLI uses, then point
    the CLI at it via SM_LOG_PATH.
    """
    import sm

    log_path = tmp_path / "cli_log.jsonl"

    # Seed the log file directly (no subprocess) by temporarily pointing
    # sm.LOG_PATH there, writing the seed entries, and restoring.
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration(iteration_id="cli-iter-cut")
        _seed_backlog(n=5)
    finally:
        sm.LOG_PATH = orig_log

    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(log_path)

    result = subprocess.run(
        [sys.executable, "-m", "sm", "sprint-cut", "3"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"sprint-cut with valid N must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_sprint_cut_invalid_n_writes_nothing(tmp_path):
    """An invalid CLI sprint-cut leaves the log byte-for-byte unchanged."""
    import sm

    log_path = tmp_path / "cli_log.jsonl"

    # Seed the log via direct LOG_PATH redirection.
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration(iteration_id="cli-iter-cut")
        _seed_backlog(n=5)
    finally:
        sm.LOG_PATH = orig_log

    bytes_before = log_path.read_bytes()

    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(log_path)

    result = subprocess.run(
        [sys.executable, "-m", "sm", "sprint-cut", "999"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"sprint-cut 999 (out of range) must exit non-zero; "
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'sprint-cut' and fail with the out-of-range "
        f"path, not 'unknown command';\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    bytes_after = log_path.read_bytes()
    assert bytes_after == bytes_before, (
        "log must be byte-for-byte unchanged on a failed CLI sprint-cut"
    )


# ===========================================================================
# Entry shape — id/type/timestamp from build_entry
# ===========================================================================


def test_entry_id_is_uuid_hex(isolated_log):
    """The sprint_cut entry has a uuid4-hex id."""
    import sm
    import re
    _seed_full(n_stories=5)
    result = sm.sprint_cut(3)
    assert re.fullmatch(r"[0-9a-f]{32}", result["id"])


def test_entry_timestamp_is_iso8601(isolated_log):
    """The sprint_cut entry has an ISO-8601 timestamp."""
    import sm
    import datetime as _dt
    _seed_full(n_stories=5)
    result = sm.sprint_cut(3)
    parsed = _dt.datetime.fromisoformat(result["timestamp"])
    assert parsed is not None


def test_entry_id_differs_across_two_cuts(isolated_log):
    """Each successful cut gets a fresh id — entry ids differ."""
    import sm
    _seed_full(n_stories=6)
    a = sm.sprint_cut(2)
    b = sm.sprint_cut(4)
    assert a["id"] != b["id"]
