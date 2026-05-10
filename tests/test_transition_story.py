"""Story 13 — pin the contract of `sm.transition_story`.

Story 13 is the FIRST story of Sprint 2 (size: L). It implements the
per-story lifecycle state-machine writer — the single function that
emits `story_state_change` entries to the log.

What this file pins:

  - Function signature and shape:
      `transition_story(story_id: str, to_state: str) -> dict`
    PUBLIC, callable, in `sm.__all__`, importable as
    `from sm import transition_story`. Returns the appended
    `story_state_change` log entry on success.

  - Allowed transition graph (Story 13 ONLY — Story 19 adds force_closed):
      * planned -> in_progress
      * in_progress -> in_review
      * in_review -> accepted
      * in_review -> rejected
      * accepted, rejected are TERMINAL — no transitions out
    Story 13 does NOT call force_closed itself; Story 4's `derive_state`
    already accepts force_closed transitions structurally.

  - Required `story_state_change` entry shape:
        {
          "type": "story_state_change",
          "story_id": "<uuid hex>",
          "from_state": "planned",
          "to_state": "in_progress",
          "notes": "<free text>"
        }
    `notes` is required but allows empty string — Sprint 2 Story 14 will
    write more meaningful notes.

  - Typed exception class: `StoryTransitionError(ValueError)`.
      * Raised for: no active iteration, no active sprint, story not in
        sprint, terminal-already, invalid state name, illegal transition.
      * `TypeError` (NOT `StoryTransitionError`) for non-string story_id
        or to_state, etc.
      * Subclasses ValueError so existing `except ValueError` callers keep
        working.

  - Failure invariant: log.jsonl is byte-for-byte unchanged on any
    validation/argument failure.

  - CLI: full subcommand wiring is Story 14's lane. Story 13 only adds
    the `EXIT_TRANSITION = 9` constant.

Tests must FAIL on first run — `transition_story` and
`StoryTransitionError` do not exist yet. The Coder downstream implements
the function and the typed error to satisfy these tests.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re
import sys
import uuid as _uuid

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
    convention from test_sprint_cut.py / test_sprint_cut_lock.py."""
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _open_iteration(iteration_id: str = "iter-1",
                    requirements=None) -> dict:
    """Append an `iteration_open` entry directly via build_entry +
    _append_entry."""
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


def _seed_sprint(n_stories: int = 5,
                 cut_at: int = 3,
                 iteration_id: str = "iter-1") -> tuple:
    """Convenience: open iteration + seed backlog + cut the sprint.
    Returns (story_ids, in_sprint_ids, deferred_ids)."""
    import sm
    _open_iteration(iteration_id=iteration_id)
    sids = _seed_backlog(n=n_stories)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at], sids[cut_at:]


# ===========================================================================
# Smoke (6) — function exists, callable, public, in __all__, signature
# ===========================================================================


def test_function_exists_on_module():
    """sm.transition_story must exist."""
    import sm
    assert hasattr(sm, "transition_story"), (
        "sm.transition_story must exist"
    )


def test_function_is_callable():
    """sm.transition_story must be callable."""
    import sm
    assert callable(sm.transition_story)


def test_function_name_is_public():
    """No leading underscore — public API."""
    import sm
    assert not sm.transition_story.__name__.startswith("_")
    assert sm.transition_story.__name__ == "transition_story"


def test_function_importable_directly():
    """`from sm import transition_story` succeeds — public-import form."""
    from sm import transition_story  # noqa: F401
    assert callable(transition_story)


def test_function_in_dunder_all():
    """Public function exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "transition_story" in sm.__all__, (
        f"transition_story must be in __all__; got {sm.__all__!r}"
    )


def test_signature_accepts_two_positional_args():
    """transition_story takes (story_id, to_state) positionally."""
    import sm
    sig = inspect.signature(sm.transition_story)
    params = list(sig.parameters)
    assert len(params) >= 2, (
        f"transition_story must accept at least two parameters; "
        f"got params {params!r}"
    )


def test_signature_first_param_named_story_id():
    """First positional arg should be named `story_id` (or close)."""
    import sm
    sig = inspect.signature(sm.transition_story)
    params = list(sig.parameters)
    # Be lenient — story_id is the obvious name but story-tagged is fine.
    assert "story" in params[0].lower(), (
        f"first param should mention 'story'; got {params[0]!r}"
    )


def test_signature_second_param_names_target_state():
    """Second positional arg should be the target state."""
    import sm
    sig = inspect.signature(sm.transition_story)
    params = list(sig.parameters)
    assert "state" in params[1].lower() or params[1].lower() in (
        "to", "to_state", "target",
    ), (
        f"second param should mention 'state'/'to'; got {params[1]!r}"
    )


# ===========================================================================
# Typed exception — StoryTransitionError (6)
# ===========================================================================


def test_story_transition_error_class_exists():
    """sm.StoryTransitionError must exist."""
    import sm
    assert hasattr(sm, "StoryTransitionError"), (
        "sm.StoryTransitionError must exist"
    )


def test_story_transition_error_subclasses_value_error():
    """StoryTransitionError subclasses ValueError so `except ValueError`
    callers keep working."""
    import sm
    assert issubclass(sm.StoryTransitionError, ValueError), (
        "StoryTransitionError must subclass ValueError"
    )


def test_story_transition_error_in_dunder_all():
    """Public typed error — exported via __all__."""
    import sm
    assert "StoryTransitionError" in sm.__all__, (
        f"StoryTransitionError must be in __all__; got {sm.__all__!r}"
    )


def test_story_transition_error_caught_as_value_error(isolated_log):
    """A bare `except ValueError` catches StoryTransitionError."""
    import sm
    caught = False
    try:
        # No active iteration → StoryTransitionError, must be
        # ValueError-catchable.
        sm.transition_story(_uuid.uuid4().hex, "in_progress")
    except ValueError:
        caught = True
    assert caught, "StoryTransitionError must be catchable as ValueError"


def test_story_transition_error_is_distinct_class():
    """StoryTransitionError is not the same as ValueError itself."""
    import sm
    assert sm.StoryTransitionError is not ValueError


def test_story_transition_error_importable_directly():
    """`from sm import StoryTransitionError` succeeds — public form."""
    from sm import StoryTransitionError  # noqa: F401
    assert isinstance(StoryTransitionError, type)


# ===========================================================================
# Happy-path forward transitions (16) — each writes one story_state_change
# ===========================================================================


def test_planned_to_in_progress_writes_entry(isolated_log):
    """planned -> in_progress: writes one new entry."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    before = list(sm.read_entries())
    sm.transition_story(in_sprint[0], "in_progress")
    after = list(sm.read_entries())
    assert len(after) == len(before) + 1


def test_planned_to_in_progress_entry_type(isolated_log):
    """The single emitted entry has type `story_state_change`."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    sm.transition_story(in_sprint[0], "in_progress")
    entries = list(sm.read_entries())
    assert entries[-1]["type"] == "story_state_change", (
        f"latest entry type must be 'story_state_change'; "
        f"got {entries[-1]['type']!r}"
    )


def test_planned_to_in_progress_returns_entry(isolated_log):
    """transition_story returns the dict it appended."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    result = sm.transition_story(in_sprint[0], "in_progress")
    entries = list(sm.read_entries())
    assert result == entries[-1]


def test_planned_to_in_progress_entry_fields(isolated_log):
    """Entry has from_state=planned, to_state=in_progress, story_id, notes."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    result = sm.transition_story(target, "in_progress")
    assert result["story_id"] == target
    assert result["from_state"] == "planned"
    assert result["to_state"] == "in_progress"
    assert "notes" in result


def test_planned_to_in_progress_derive_state_updates(isolated_log):
    """After planned -> in_progress, derive_state shows the new state."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    state = sm.derive_state()
    assert state["story_states"][target] == "in_progress"


def test_in_progress_to_in_review_writes_entry(isolated_log):
    """in_progress -> in_review: writes one new entry."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    sm.transition_story(in_sprint[0], "in_progress")
    before = list(sm.read_entries())
    sm.transition_story(in_sprint[0], "in_review")
    after = list(sm.read_entries())
    assert len(after) == len(before) + 1


def test_in_progress_to_in_review_entry_fields(isolated_log):
    """from_state=in_progress, to_state=in_review."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    sm.transition_story(in_sprint[0], "in_progress")
    result = sm.transition_story(in_sprint[0], "in_review")
    assert result["from_state"] == "in_progress"
    assert result["to_state"] == "in_review"
    assert result["story_id"] == in_sprint[0]


def test_in_progress_to_in_review_derive_state_updates(isolated_log):
    """After chain to in_review, derive_state shows in_review."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    state = sm.derive_state()
    assert state["story_states"][target] == "in_review"


def test_in_review_to_accepted_writes_entry(isolated_log):
    """in_review -> accepted: writes one new entry."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    before = list(sm.read_entries())
    sm.transition_story(target, "accepted")
    after = list(sm.read_entries())
    assert len(after) == len(before) + 1


def test_in_review_to_accepted_entry_fields(isolated_log):
    """from_state=in_review, to_state=accepted."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    result = sm.transition_story(target, "accepted")
    assert result["from_state"] == "in_review"
    assert result["to_state"] == "accepted"
    assert result["story_id"] == target


def test_in_review_to_accepted_derive_state_updates(isolated_log):
    """derive_state reflects accepted after the full chain."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    sm.transition_story(target, "accepted")
    state = sm.derive_state()
    assert state["story_states"][target] == "accepted"


def test_in_review_to_rejected_writes_entry(isolated_log):
    """in_review -> rejected: writes one new entry."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    before = list(sm.read_entries())
    sm.transition_story(target, "rejected")
    after = list(sm.read_entries())
    assert len(after) == len(before) + 1


def test_in_review_to_rejected_entry_fields(isolated_log):
    """from_state=in_review, to_state=rejected."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    result = sm.transition_story(target, "rejected")
    assert result["from_state"] == "in_review"
    assert result["to_state"] == "rejected"
    assert result["story_id"] == target


def test_in_review_to_rejected_derive_state_updates(isolated_log):
    """derive_state reflects rejected after the chain."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.transition_story(target, "rejected")
    state = sm.derive_state()
    assert state["story_states"][target] == "rejected"


def test_returns_dict(isolated_log):
    """Return value is a dict."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    result = sm.transition_story(in_sprint[0], "in_progress")
    assert isinstance(result, dict)


def test_entry_has_canonical_id_type_timestamp(isolated_log):
    """The emitted entry has id, type, timestamp from build_entry."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    result = sm.transition_story(in_sprint[0], "in_progress")
    assert "id" in result
    assert "type" in result
    assert "timestamp" in result


def test_entry_id_is_uuid_hex(isolated_log):
    """Entry id is a uuid4-hex string."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    result = sm.transition_story(in_sprint[0], "in_progress")
    assert re.fullmatch(r"[0-9a-f]{32}", result["id"])


# ===========================================================================
# Notes field (5)
# ===========================================================================


def test_notes_field_present_on_success_entry(isolated_log):
    """Every successful entry carries a `notes` field (free text, may be
    empty per Sprint 2 Story 13 contract)."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    result = sm.transition_story(in_sprint[0], "in_progress")
    assert "notes" in result, (
        f"entry must contain 'notes'; got keys {sorted(result.keys())!r}"
    )


def test_notes_field_is_string_type(isolated_log):
    """The `notes` field on the written entry is a string."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    result = sm.transition_story(in_sprint[0], "in_progress")
    assert isinstance(result["notes"], str), (
        f"notes must be str; got {type(result['notes']).__name__}"
    )


def test_notes_default_is_empty_or_short(isolated_log):
    """When the operator doesn't pass notes, the default is empty/short
    free-text — Story 14 will write meaningful notes."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    result = sm.transition_story(in_sprint[0], "in_progress")
    # Loose check — empty string is acceptable; any short auto-string is too.
    assert isinstance(result["notes"], str)
    # Must not be missing/None.
    assert result["notes"] is not None


def test_chain_notes_each_entry(isolated_log):
    """Every transition in a chain produces an entry with a notes field."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    for to_state in ("in_progress", "in_review", "accepted"):
        if to_state == "accepted":
            sm.record_review(target, True, "ok")
        result = sm.transition_story(target, to_state)
        assert "notes" in result
        assert isinstance(result["notes"], str)


def test_entry_round_trips_through_read_entries(isolated_log):
    """The entry returned by transition_story equals what's read back."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    returned = sm.transition_story(in_sprint[0], "in_progress")
    entries = list(sm.read_entries())
    assert returned == entries[-1]


def test_entry_is_json_serializable(isolated_log):
    """The entry survives a json.dumps round-trip."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    e = sm.transition_story(in_sprint[0], "in_progress")
    s = json.dumps(e)
    assert json.loads(s) == e


# ===========================================================================
# Type validation on inputs (10)
# ===========================================================================


def test_story_id_none_raises_type_error(isolated_log):
    """story_id=None → TypeError."""
    import sm
    _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story(None, "in_progress")


def test_story_id_int_raises_type_error(isolated_log):
    """story_id=42 (int) → TypeError."""
    import sm
    _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story(42, "in_progress")


def test_story_id_list_raises_type_error(isolated_log):
    """story_id=[..] → TypeError."""
    import sm
    sids, _, _ = _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story([sids[0]], "in_progress")


def test_story_id_dict_raises_type_error(isolated_log):
    """story_id={'id': '...'} → TypeError."""
    import sm
    sids, _, _ = _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story({"id": sids[0]}, "in_progress")


def test_story_id_bytes_raises_type_error(isolated_log):
    """story_id=b'..' (bytes) → TypeError. Strict str-only."""
    import sm
    sids, _, _ = _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story(sids[0].encode("ascii"), "in_progress")


def test_to_state_none_raises_type_error(isolated_log):
    """to_state=None → TypeError."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story(in_sprint[0], None)


def test_to_state_int_raises_type_error(isolated_log):
    """to_state=1 (int) → TypeError."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story(in_sprint[0], 1)


def test_to_state_list_raises_type_error(isolated_log):
    """to_state=['in_progress'] → TypeError."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story(in_sprint[0], ["in_progress"])


def test_to_state_bool_raises_type_error(isolated_log):
    """to_state=True (bool, not str) → TypeError."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story(in_sprint[0], True)


def test_both_args_none_raises_type_error(isolated_log):
    """Both args None → TypeError."""
    import sm
    _seed_sprint(5, 3)
    with pytest.raises(TypeError):
        sm.transition_story(None, None)


# ===========================================================================
# No active iteration (5)
# ===========================================================================


def test_no_active_iteration_empty_log_raises(isolated_log):
    """Empty log → StoryTransitionError."""
    import sm
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(_uuid.uuid4().hex, "in_progress")


def test_no_active_iteration_after_close_raises(isolated_log):
    """Iteration that has been closed → StoryTransitionError."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    close = sm.build_entry("iteration_close", {
        "closed_by": "operator", "reason": None,
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 0,
    })
    sm._append_entry(close)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "in_progress")


def test_no_active_iteration_log_unchanged(isolated_log):
    """No active iteration → log byte-for-byte unchanged."""
    import sm
    seed = sm.build_entry("transition_test_seed", {"marker": "before"})
    sm._append_entry(seed)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(_uuid.uuid4().hex, "in_progress")
    assert isolated_log.read_bytes() == bytes_before


def test_no_active_iteration_derive_state_unchanged(isolated_log):
    """derive_state before/after a no-iteration failure is equal."""
    import sm
    seed = sm.build_entry("transition_test_seed", {"marker": "before"})
    sm._append_entry(seed)
    before = sm.derive_state()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(_uuid.uuid4().hex, "in_progress")
    after = sm.derive_state()
    assert before == after


def test_no_active_iteration_error_mentions_iteration(isolated_log):
    """Error message names the missing-iteration condition."""
    import sm
    with pytest.raises(sm.StoryTransitionError) as exc_info:
        sm.transition_story(_uuid.uuid4().hex, "in_progress")
    msg = str(exc_info.value).lower()
    assert "iteration" in msg, (
        f"error must mention 'iteration'; got: {exc_info.value!s}"
    )


# ===========================================================================
# No active sprint (4)
# ===========================================================================


def test_no_sprint_yet_raises(isolated_log):
    """Iteration open + decompose done but no sprint_cut →
    StoryTransitionError."""
    import sm
    _open_iteration()
    sids = _seed_backlog(n=3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(sids[0], "in_progress")


def test_no_sprint_yet_log_unchanged(isolated_log):
    """No sprint_cut + transition attempt → log byte-for-byte unchanged."""
    import sm
    _open_iteration()
    sids = _seed_backlog(n=3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(sids[0], "in_progress")
    assert isolated_log.read_bytes() == bytes_before


def test_no_sprint_yet_error_mentions_sprint(isolated_log):
    """Error message names the missing-sprint condition."""
    import sm
    _open_iteration()
    sids = _seed_backlog(n=3)
    with pytest.raises(sm.StoryTransitionError) as exc_info:
        sm.transition_story(sids[0], "in_progress")
    msg = str(exc_info.value).lower()
    assert "sprint" in msg, (
        f"error must mention 'sprint'; got: {exc_info.value!s}"
    )


def test_no_sprint_yet_derive_state_unchanged(isolated_log):
    """derive_state before/after a no-sprint failure is equal."""
    import sm
    _open_iteration()
    sids = _seed_backlog(n=3)
    before = sm.derive_state()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(sids[0], "in_progress")
    after = sm.derive_state()
    assert before == after


# ===========================================================================
# Story not in sprint (6)
# ===========================================================================


def test_unknown_story_id_raises(isolated_log):
    """story_id that doesn't exist in any backlog → StoryTransitionError."""
    import sm
    _seed_sprint(5, 3)
    bogus_id = _uuid.uuid4().hex
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(bogus_id, "in_progress")


def test_unknown_story_id_error_names_id(isolated_log):
    """The error message names the offending story_id."""
    import sm
    _seed_sprint(5, 3)
    bogus_id = _uuid.uuid4().hex
    with pytest.raises(sm.StoryTransitionError) as exc_info:
        sm.transition_story(bogus_id, "in_progress")
    assert bogus_id in str(exc_info.value), (
        f"error must name story_id {bogus_id!r}; got: {exc_info.value!s}"
    )


def test_deferred_story_id_raises(isolated_log):
    """story_id is in backlog but DEFERRED (not in_sprint) →
    StoryTransitionError."""
    import sm
    sids, in_sprint, deferred = _seed_sprint(5, 3)
    assert deferred, "test setup requires at least one deferred story"
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(deferred[0], "in_progress")


def test_deferred_story_id_error_names_id(isolated_log):
    """The error message names the deferred story_id."""
    import sm
    sids, in_sprint, deferred = _seed_sprint(5, 3)
    target = deferred[0]
    with pytest.raises(sm.StoryTransitionError) as exc_info:
        sm.transition_story(target, "in_progress")
    assert target in str(exc_info.value), (
        f"error must name deferred story_id {target!r}; "
        f"got: {exc_info.value!s}"
    )


def test_unknown_story_id_log_unchanged(isolated_log):
    """Unknown story_id → log byte-for-byte unchanged."""
    import sm
    _seed_sprint(5, 3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(_uuid.uuid4().hex, "in_progress")
    assert isolated_log.read_bytes() == bytes_before


def test_deferred_story_id_log_unchanged(isolated_log):
    """Deferred story_id → log byte-for-byte unchanged."""
    import sm
    sids, in_sprint, deferred = _seed_sprint(5, 3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(deferred[0], "in_progress")
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# Terminal states reject further transitions (10)
# ===========================================================================


def test_accepted_to_in_progress_raises(isolated_log):
    """accepted -> in_progress: terminal, reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    sm.transition_story(target, "accepted")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "in_progress")


def test_accepted_to_in_review_raises(isolated_log):
    """accepted -> in_review: terminal, reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    sm.transition_story(target, "accepted")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "in_review")


def test_accepted_to_rejected_raises(isolated_log):
    """accepted -> rejected: terminal, reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    sm.transition_story(target, "accepted")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "rejected")


def test_accepted_to_planned_raises(isolated_log):
    """accepted -> planned: terminal, reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    sm.transition_story(target, "accepted")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "planned")


def test_rejected_to_in_progress_raises(isolated_log):
    """rejected -> in_progress: terminal, reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.transition_story(target, "rejected")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "in_progress")


def test_rejected_to_in_review_raises(isolated_log):
    """rejected -> in_review: terminal, reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.transition_story(target, "rejected")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "in_review")


def test_rejected_to_accepted_raises(isolated_log):
    """rejected -> accepted: terminal, reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.transition_story(target, "rejected")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")


def test_rejected_to_planned_raises(isolated_log):
    """rejected -> planned: terminal, reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.transition_story(target, "rejected")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "planned")


def test_terminal_accepted_error_names_current_state(isolated_log):
    """Error from accepted -> X names current state ('accepted')."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    sm.transition_story(target, "accepted")
    with pytest.raises(sm.StoryTransitionError) as exc_info:
        sm.transition_story(target, "in_progress")
    assert "accepted" in str(exc_info.value), (
        f"error must name current state 'accepted'; got: {exc_info.value!s}"
    )


def test_terminal_rejected_error_names_current_state(isolated_log):
    """Error from rejected -> X names current state ('rejected')."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.transition_story(target, "rejected")
    with pytest.raises(sm.StoryTransitionError) as exc_info:
        sm.transition_story(target, "in_progress")
    assert "rejected" in str(exc_info.value), (
        f"error must name current state 'rejected'; got: {exc_info.value!s}"
    )


def test_accepted_terminal_log_unchanged(isolated_log):
    """Failed transition from accepted → log byte-for-byte unchanged."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    sm.transition_story(target, "accepted")
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "in_progress")
    assert isolated_log.read_bytes() == bytes_before


def test_rejected_terminal_log_unchanged(isolated_log):
    """Failed transition from rejected → log byte-for-byte unchanged."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.transition_story(target, "rejected")
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# Skip rejection (12) — illegal transitions in the graph
# ===========================================================================


def test_skip_planned_to_in_review_raises(isolated_log):
    """planned -> in_review (skip in_progress) → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "in_review")


def test_skip_planned_to_accepted_raises(isolated_log):
    """planned -> accepted → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "accepted")


def test_skip_planned_to_rejected_raises(isolated_log):
    """planned -> rejected → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "rejected")


def test_skip_in_progress_to_accepted_raises(isolated_log):
    """in_progress -> accepted (skip in_review) → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")


def test_skip_in_progress_to_rejected_raises(isolated_log):
    """in_progress -> rejected (skip in_review) → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "rejected")


def test_back_in_review_to_in_progress_raises(isolated_log):
    """in_review -> in_progress (backwards) → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "in_progress")


def test_back_in_progress_to_planned_raises(isolated_log):
    """in_progress -> planned (backwards) → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "planned")


def test_back_in_review_to_planned_raises(isolated_log):
    """in_review -> planned (backwards) → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "planned")


def test_self_loop_planned_to_planned_raises(isolated_log):
    """planned -> planned (self-loop) → reject (not in graph)."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "planned")


def test_self_loop_in_progress_to_in_progress_raises(isolated_log):
    """in_progress -> in_progress (self-loop) → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "in_progress")


def test_self_loop_in_review_to_in_review_raises(isolated_log):
    """in_review -> in_review (self-loop) → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "in_review")


def test_skip_error_message_names_from_and_to(isolated_log):
    """The error message names both the from-state and the to-state."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    with pytest.raises(sm.StoryTransitionError) as exc_info:
        sm.transition_story(target, "accepted")
    msg = str(exc_info.value)
    # planned is the from-state; accepted is the to-state.
    assert "planned" in msg, (
        f"error must name from-state 'planned'; got: {msg}"
    )
    assert "accepted" in msg, (
        f"error must name to-state 'accepted'; got: {msg}"
    )


def test_skip_planned_to_in_review_log_unchanged(isolated_log):
    """planned->in_review attempt → log byte-for-byte unchanged."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "in_review")
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# Invalid to_state value (8)
# ===========================================================================


def test_unknown_state_name_raises(isolated_log):
    """Made-up state name → StoryTransitionError."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "frobnicated")


def test_empty_string_state_raises(isolated_log):
    """to_state='' → StoryTransitionError (or ValueError subclass)."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "")


def test_whitespace_only_state_raises(isolated_log):
    """to_state='   ' → StoryTransitionError."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "   ")


def test_state_with_leading_space_raises(isolated_log):
    """to_state=' in_progress' → StoryTransitionError (case- and space-sensitive)."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], " in_progress")


def test_state_with_trailing_space_raises(isolated_log):
    """to_state='in_progress ' → StoryTransitionError."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "in_progress ")


def test_state_uppercase_raises(isolated_log):
    """to_state='PLANNED' → StoryTransitionError (case-sensitive)."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    # Story is in 'planned'; PLANNED != planned.
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "IN_PROGRESS")


def test_state_camelcase_raises(isolated_log):
    """to_state='InProgress' → StoryTransitionError (case-sensitive)."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "InProgress")


def test_state_with_dash_raises(isolated_log):
    """to_state='in-progress' (dash, not underscore) → reject."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "in-progress")


# ===========================================================================
# Failure invariants — log byte-for-byte unchanged on every failure mode (10)
# ===========================================================================


def test_log_unchanged_after_unknown_story(isolated_log):
    """Unknown story_id → log unchanged."""
    import sm
    _seed_sprint(5, 3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(_uuid.uuid4().hex, "in_progress")
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_deferred_story(isolated_log):
    """Deferred story → log unchanged."""
    import sm
    sids, in_sprint, deferred = _seed_sprint(5, 3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(deferred[0], "in_progress")
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_skip_transition(isolated_log):
    """Skip transition (planned->accepted) → log unchanged."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "accepted")
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_terminal_transition(isolated_log):
    """Transition out of accepted → log unchanged."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    sm.transition_story(target, "accepted")
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "rejected")
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_no_iteration(isolated_log):
    """No active iteration → log unchanged."""
    import sm
    seed = sm.build_entry("transition_test_seed", {"marker": "X"})
    sm._append_entry(seed)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(_uuid.uuid4().hex, "in_progress")
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_no_sprint(isolated_log):
    """No active sprint → log unchanged."""
    import sm
    _open_iteration()
    sids = _seed_backlog(n=3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(sids[0], "in_progress")
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_unknown_state_name(isolated_log):
    """Unknown state name → log unchanged."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "frobnicated")
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_type_error_story_id(isolated_log):
    """Non-string story_id → log unchanged."""
    import sm
    _seed_sprint(5, 3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.transition_story(42, "in_progress")
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_type_error_to_state(isolated_log):
    """Non-string to_state → log unchanged."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.transition_story(in_sprint[0], 1)
    assert isolated_log.read_bytes() == bytes_before


def test_append_entry_not_called_on_failure(isolated_log, monkeypatch):
    """On any validation failure, _append_entry must NOT be called."""
    import sm
    _seed_sprint(5, 3)

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    # Try an unknown story_id — should fail without writing.
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(_uuid.uuid4().hex, "in_progress")
    assert calls["n"] == 0, (
        f"_append_entry must not be called on failure; "
        f"got {calls['n']} call(s)"
    )


def test_append_entry_not_called_on_skip_transition(
    isolated_log, monkeypatch,
):
    """On a skip transition failure, _append_entry must NOT be called."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "accepted")
    assert calls["n"] == 0


# ===========================================================================
# Round-trip via derive_state (6)
# ===========================================================================


def test_derive_state_after_one_transition(isolated_log):
    """After planned->in_progress, derive_state shows the new state."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    state = sm.derive_state()
    assert state["story_states"][target] == "in_progress"


def test_derive_state_other_stories_remain_planned(isolated_log):
    """After a transition on one story, OTHER in-sprint stories still
    show 'planned'."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    state = sm.derive_state()
    for sid in in_sprint[1:]:
        assert state["story_states"][sid] == "planned", (
            f"other in-sprint story {sid!r} must still be planned"
        )


def test_derive_state_chain_to_accepted(isolated_log):
    """Full chain to accepted reflects in derive_state."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    sm.transition_story(target, "accepted")
    state = sm.derive_state()
    assert state["story_states"][target] == "accepted"


def test_derive_state_chain_to_rejected(isolated_log):
    """Full chain to rejected reflects in derive_state."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.transition_story(target, "rejected")
    state = sm.derive_state()
    assert state["story_states"][target] == "rejected"


def test_replay_reconstructs_final_states(isolated_log):
    """Two calls to derive_state produce the same final story_states."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    sm.transition_story(in_sprint[0], "in_progress")
    sm.transition_story(in_sprint[1], "in_progress")
    sm.transition_story(in_sprint[1], "in_review")
    a = sm.derive_state()
    b = sm.derive_state()
    assert a["story_states"] == b["story_states"]


def test_replay_does_not_modify_log(isolated_log):
    """derive_state is pure read — the log is unchanged after replay."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    sm.transition_story(in_sprint[0], "in_progress")
    bytes_before = isolated_log.read_bytes()
    sm.derive_state()
    sm.derive_state()
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# Re-entrancy (5) — multiple stories, chained transitions
# ===========================================================================


def test_two_stories_transition_independently(isolated_log):
    """Two different stories can be transitioned independently — both
    succeed, both reflected in derive_state."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    a, b = in_sprint[0], in_sprint[1]
    sm.transition_story(a, "in_progress")
    sm.transition_story(b, "in_progress")
    state = sm.derive_state()
    assert state["story_states"][a] == "in_progress"
    assert state["story_states"][b] == "in_progress"


def test_chain_planned_to_accepted_one_story(isolated_log):
    """Full happy chain on a single story — every step succeeds."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    a = sm.transition_story(target, "in_progress")
    b = sm.transition_story(target, "in_review")
    sm.record_review(target, True, "ok")
    c = sm.transition_story(target, "accepted")
    assert a["to_state"] == "in_progress"
    assert b["to_state"] == "in_review"
    assert c["to_state"] == "accepted"


def test_chain_planned_to_rejected_one_story(isolated_log):
    """Full chain to rejected on a single story — every step succeeds."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    a = sm.transition_story(target, "in_progress")
    b = sm.transition_story(target, "in_review")
    c = sm.transition_story(target, "rejected")
    assert a["to_state"] == "in_progress"
    assert b["to_state"] == "in_review"
    assert c["to_state"] == "rejected"


def test_two_stories_one_accepted_one_rejected(isolated_log):
    """One story accepted, another rejected — both reflected in
    derive_state."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    a, b = in_sprint[0], in_sprint[1]
    for to_state in ("in_progress", "in_review", "accepted"):
        if to_state == "accepted":
            sm.record_review(a, True, "ok")
        sm.transition_story(a, to_state)
    for to_state in ("in_progress", "in_review", "rejected"):
        sm.transition_story(b, to_state)
    state = sm.derive_state()
    assert state["story_states"][a] == "accepted"
    assert state["story_states"][b] == "rejected"


def test_each_transition_ids_distinct(isolated_log):
    """Each emitted entry has its own unique uuid id."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    target = in_sprint[0]
    a = sm.transition_story(target, "in_progress")
    b = sm.transition_story(target, "in_review")
    assert a["id"] != b["id"]


# ===========================================================================
# build_entry / _append_entry wiring (5)
# ===========================================================================


def test_uses_build_entry(isolated_log, monkeypatch):
    """transition_story must go through sm.build_entry for the entry."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)

    calls = {"n": 0, "types": []}
    real = sm.build_entry

    def fake(type_, content):
        calls["n"] += 1
        calls["types"].append(type_)
        return real(type_, content)

    monkeypatch.setattr(sm, "build_entry", fake)
    sm.transition_story(in_sprint[0], "in_progress")
    assert "story_state_change" in calls["types"], (
        f"transition_story must call build_entry(type='story_state_change'); "
        f"got types {calls['types']!r}"
    )


def test_uses_append_entry_exactly_once_per_success(
    isolated_log, monkeypatch,
):
    """transition_story calls _append_entry exactly once per success."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)

    calls = {"n": 0, "entries": []}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        calls["entries"].append(entry)
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    sm.transition_story(in_sprint[0], "in_progress")
    assert calls["n"] == 1, (
        f"transition_story must call _append_entry exactly once; "
        f"got {calls['n']}"
    )
    assert calls["entries"][0]["type"] == "story_state_change"


def test_neither_called_on_unknown_story(isolated_log, monkeypatch):
    """On unknown-story failure, _append_entry is NOT called."""
    import sm
    _seed_sprint(5, 3)

    appends = {"n": 0}
    real_append = sm._append_entry

    def fake_append(entry):
        appends["n"] += 1
        return real_append(entry)

    monkeypatch.setattr(sm, "_append_entry", fake_append)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(_uuid.uuid4().hex, "in_progress")
    assert appends["n"] == 0


def test_uses_derive_state_to_lookup_current_state(
    isolated_log, monkeypatch,
):
    """transition_story uses derive_state() to read the current state."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)

    calls = {"n": 0}
    real = sm.derive_state

    def fake():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(sm, "derive_state", fake)
    sm.transition_story(in_sprint[0], "in_progress")
    assert calls["n"] >= 1, (
        f"transition_story must call derive_state() at least once; "
        f"got {calls['n']}"
    )


def test_only_path_emitting_story_state_change(isolated_log):
    """Pin: after a successful transition, the only `story_state_change`
    entry on the log is the one emitted by transition_story (i.e., the
    function is the SOLE writer)."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    sm.transition_story(in_sprint[0], "in_progress")
    sm.transition_story(in_sprint[0], "in_review")
    state_change_entries = [
        e for e in sm.read_entries()
        if e.get("type") == "story_state_change"
    ]
    assert len(state_change_entries) == 2
    # Every state-change entry has the transition_story-shaped fields.
    for e in state_change_entries:
        assert "story_id" in e
        assert "from_state" in e
        assert "to_state" in e
        assert "notes" in e


# ===========================================================================
# EXIT_TRANSITION constant (3)
# ===========================================================================


def test_exit_transition_constant_exists():
    """sm.EXIT_TRANSITION must exist as a module-level constant."""
    import sm
    assert hasattr(sm, "EXIT_TRANSITION"), (
        "sm.EXIT_TRANSITION must exist (Story 13 reserves an exit code)"
    )


def test_exit_transition_value_is_9():
    """Story 13 reserves exit code 9 for transition failures."""
    import sm
    assert sm.EXIT_TRANSITION == 9, (
        f"EXIT_TRANSITION must be 9; got {sm.EXIT_TRANSITION!r}"
    )


def test_exit_transition_distinct_from_other_codes():
    """EXIT_TRANSITION must be distinct from every other exit code."""
    import sm
    others = (
        sm.EXIT_OK, sm.EXIT_OTHER, sm.EXIT_PATH, sm.EXIT_JSON,
        sm.EXIT_SHAPE, sm.EXIT_DUP_ID, sm.EXIT_SINGLE_ACTIVE,
        sm.EXIT_UNKNOWN_REQ, sm.EXIT_SPRINT_CUT,
    )
    assert sm.EXIT_TRANSITION not in others, (
        f"EXIT_TRANSITION must be distinct; got {sm.EXIT_TRANSITION!r} "
        f"in {others!r}"
    )


# ===========================================================================
# Sanity — entry timestamp shape (1)
# ===========================================================================


def test_entry_timestamp_is_iso8601(isolated_log):
    """Entry has an ISO-8601 timestamp."""
    import sm
    import datetime as _dt
    sids, in_sprint, _ = _seed_sprint(5, 3)
    result = sm.transition_story(in_sprint[0], "in_progress")
    parsed = _dt.datetime.fromisoformat(result["timestamp"])
    assert parsed is not None


# ===========================================================================
# Cross-iteration: after iteration_close, transitions are rejected (2)
# ===========================================================================


def test_transition_after_close_raises(isolated_log):
    """After iteration_close, no transitions are accepted."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    close = sm.build_entry("iteration_close", {
        "closed_by": "operator", "reason": "wrap",
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 0,
    })
    sm._append_entry(close)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "in_progress")


def test_transition_after_close_log_unchanged(isolated_log):
    """After close, a transition attempt leaves the log unchanged."""
    import sm
    sids, in_sprint, _ = _seed_sprint(5, 3)
    close = sm.build_entry("iteration_close", {
        "closed_by": "operator", "reason": "wrap",
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 0,
    })
    sm._append_entry(close)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(in_sprint[0], "in_progress")
    assert isolated_log.read_bytes() == bytes_before
