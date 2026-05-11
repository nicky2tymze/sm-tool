"""Story 10 — pin SM Agent output cross-reference + acceptance-criteria
validation that goes BEYOND what Story 9 already pins.

Story 9 pinned the SHAPE of the agent's JSON output (sequences, sizes,
required fields, requirement_ids list-of-non-empty-strings). Story 10 adds:

  1. Cross-reference check: every requirement_id in every story must match
     a requirement_id in the active iteration's handoff. Unknown ids raise
     `DecomposeUnknownRequirementError` (a ValueError subclass), DISTINCT
     from `DecomposeOutputShapeError`. The error message names the
     offending story (by sequence and/or title) AND the unknown id.

  2. Acceptance-criteria non-empty AFTER strip(): empty string,
     whitespace-only, tab-only, newline-only → `DecomposeOutputShapeError`
     (Story 9's existing class — a tightening of Story 9, not a new
     domain).

  3. Acceptance-criteria captured verbatim into the appended log entry
     (no trim, no normalization, no markdown processing).

  4. Failure invariant: any Story 10 validation failure leaves the log
     byte-for-byte unchanged (no `story_backlog` entry written).

  5. CLI exit-code separation: unknown-requirement-id failure exits
     non-zero. Recommended distinct from shape-error code, but tests
     accept either-or.

Tests must FAIL on first run — `DecomposeUnknownRequirementError` does
not exist yet, and the cross-reference + whitespace-strip tightening
behavior is not implemented yet. The Coder downstream adds the class
and the validation to satisfy these tests.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SOURCE_ROLES_DIR = PACKAGE_DIR / "roles"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file, and stage the package's
    `roles/` dir under tmp_path so `resolve_role_spec` finds the canonical
    sm_agent.md spec at the new anchor.

    Story 10 mirrors Story 9's `isolated_log` fixture, locally — the
    project conftest's autouse staging is scoped to `test_decompose.py`,
    so this file stages its own.
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)

    # Mirror the package's roles/ dir into tmp_path/roles/ so
    # resolve_role_spec("sm_agent") finds the spec at the redirected
    # anchor.
    dest = tmp_path / "roles"
    if not dest.exists() and SOURCE_ROLES_DIR.is_dir():
        shutil.copytree(SOURCE_ROLES_DIR, dest)

    return log_file


def _seed_iteration(iteration_id: str = "iter-1",
                    requirement_ids=("req-1", "req-2")) -> list:
    """Append an iteration_open entry directly via build_entry +
    _append_entry so a subsequent decompose() has an active iteration to
    work against.

    Returns the requirements list that landed on the entry.
    """
    import sm
    requirements = [
        {
            "requirement_id": rid,
            "title": f"Title {rid}",
            "description": f"Description for {rid}.",
            "priority": "MUST",
            "acceptance_criteria": f"AC for {rid}",
        }
        for rid in requirement_ids
    ]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": "Test iteration",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return list(requirements)


def _seed_log_with_one_entry(isolated_log_path: pathlib.Path) -> bytes:
    """Write a single benign (non-iteration_open) entry so the log is
    non-empty. Returns the bytes of the seeded log so tests can verify
    byte-for-byte equality after a failure."""
    import sm
    e = sm.build_entry("decompose_validation_seed", {"marker": "before"})
    sm._append_entry(e)
    return isolated_log_path.read_bytes()


def _stub_spawn(output_dict_or_str):
    """Build a spawn_agent stub that returns the given output as a JSON
    string (passed through verbatim if already a string)."""
    if isinstance(output_dict_or_str, str):
        payload = output_dict_or_str
    else:
        payload = json.dumps(output_dict_or_str)

    def _spawn(role_spec_path, requirements):
        return payload

    return _spawn


def _story(sequence: int, requirement_ids, *, title=None,
           size: str = "S", acceptance_criteria: str = "AC"):
    """Build one canonical story dict. Convenience for keeping test
    bodies readable."""
    return {
        "sequence": sequence,
        "title": title if title is not None else f"Story {sequence}",
        "size": size,
        "requirement_ids": list(requirement_ids),
        "acceptance_criteria": acceptance_criteria,
    }


# ===========================================================================
# Smoke (3) — file imports cleanly, error class exists, in __all__,
# subclasses ValueError.
# ===========================================================================


def test_module_imports_cleanly():
    """sm imports without raising — sanity check for the whole suite."""
    import sm  # noqa: F401
    assert hasattr(sm, "decompose")


def test_unknown_requirement_error_class_exists():
    """sm.DecomposeUnknownRequirementError must exist on the module."""
    import sm
    assert hasattr(sm, "DecomposeUnknownRequirementError"), (
        "sm.DecomposeUnknownRequirementError must exist (Story 10 "
        "introduces this distinct error class for cross-reference "
        "failures)"
    )


def test_unknown_requirement_error_in_dunder_all():
    """The new typed error is exported via __all__ — public API."""
    import sm
    assert "DecomposeUnknownRequirementError" in sm.__all__, (
        f"DecomposeUnknownRequirementError must be in sm.__all__; "
        f"got {sm.__all__!r}"
    )


def test_unknown_requirement_error_subclasses_value_error():
    """Subclasses ValueError so existing `except ValueError` callers keep
    working — same convention as the other typed decompose errors."""
    import sm
    assert issubclass(sm.DecomposeUnknownRequirementError, ValueError), (
        "DecomposeUnknownRequirementError must subclass ValueError so "
        "bare `except ValueError:` callers stay correct"
    )


# ===========================================================================
# Unknown requirement_id rejection (10+) — DecomposeUnknownRequirementError
# raised on cross-reference failure; log unchanged on failure.
# ===========================================================================


def test_single_unknown_id_raises_unknown_requirement_error(isolated_log):
    """One story references a requirement_id not in the active iteration's
    handoff → DecomposeUnknownRequirementError."""
    import sm
    _seed_iteration(requirement_ids=("req-1", "req-2"))
    output = {"stories": [
        _story(1, ["req-NOPE"]),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)


def test_multiple_unknown_ids_in_one_story_raise(isolated_log):
    """A story with several unknown ids → unknown-requirement error."""
    import sm
    _seed_iteration(requirement_ids=("req-1", "req-2"))
    output = {"stories": [
        _story(1, ["req-X", "req-Y", "req-Z"]),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)


def test_all_ids_unknown_raises(isolated_log):
    """Every requirement_id across every story is unknown → error."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-A"]),
        _story(2, ["req-B"]),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)


def test_partially_unknown_story_raises(isolated_log):
    """A single story mixes a known id with an unknown id → error.
    Even one bad id in a list of otherwise-good ids fails the story."""
    import sm
    _seed_iteration(requirement_ids=("req-1", "req-2"))
    output = {"stories": [
        _story(1, ["req-1", "req-NOPE"]),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)


def test_unknown_id_in_later_story_raises(isolated_log):
    """First story is clean; second story has an unknown id. Validation
    runs across all stories — error must still fire."""
    import sm
    _seed_iteration(requirement_ids=("req-1", "req-2"))
    output = {"stories": [
        _story(1, ["req-1"]),
        _story(2, ["req-NOPE"]),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)


def test_unknown_id_error_names_unknown_id(isolated_log):
    """The error message names the offending unknown requirement id so
    the operator can spot it immediately."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-MISSING-XYZ"]),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError) as exc_info:
        sm.decompose(spawn_agent=spawn)
    assert "req-MISSING-XYZ" in str(exc_info.value), (
        f"error must name the unknown id 'req-MISSING-XYZ'; "
        f"got: {exc_info.value!s}"
    )


def test_unknown_id_error_names_offending_story_title(isolated_log):
    """The error message names the offending story so the operator knows
    where to look. Names by title (or sequence) — we accept either."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-NOPE"], title="The Offender Story"),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError) as exc_info:
        sm.decompose(spawn_agent=spawn)
    msg = str(exc_info.value)
    assert ("The Offender Story" in msg) or ("sequence 1" in msg) or (
            re.search(r"\bstory\W*1\b", msg, re.IGNORECASE) is not None
        ), (
        f"error must name the offending story by title or by sequence; "
        f"got: {msg!r}"
    )


def test_unknown_id_error_names_sequence(isolated_log):
    """Sequence-only fallback: if a story has a generic title, the error
    must still pin it via its sequence."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-1"], title="Story 1"),
        _story(2, ["req-NOPE"], title="Story 2"),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError) as exc_info:
        sm.decompose(spawn_agent=spawn)
    msg = str(exc_info.value)
    # Either the title "Story 2" appears, or the sequence number "2"
    # appears in a story-naming context.
    assert ("Story 2" in msg) or (
        re.search(r"\bsequence\b\W*2\b", msg, re.IGNORECASE) is not None
        or re.search(r"\bstory\b\W*2\b", msg, re.IGNORECASE) is not None
    ), (
        f"error must identify the offending story (sequence 2 / 'Story 2'); "
        f"got: {msg!r}"
    )


def test_unknown_id_failure_writes_no_log_entry(isolated_log):
    """Cross-reference failure → log byte-for-byte unchanged."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    bytes_before = isolated_log.read_bytes()
    output = {"stories": [_story(1, ["req-NOPE"])]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == bytes_before, (
        "cross-reference failure must not write any log entry"
    )


def test_unknown_id_failure_does_not_change_derive_state(isolated_log):
    """derive_state before/after a cross-reference failure is equal."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    before = sm.derive_state()
    output = {"stories": [_story(1, ["req-OOPS"])]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)
    after = sm.derive_state()
    assert before == after, (
        "derive_state must be unchanged after a cross-reference failure"
    )


def test_unknown_id_failure_no_story_backlog_entry_appended(isolated_log):
    """No `story_backlog` entry exists in the log after a cross-reference
    failure (regardless of any seed entries)."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [_story(1, ["req-NOPE"])]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)
    types = [e["type"] for e in sm.read_entries()]
    assert "story_backlog" not in types, (
        f"no story_backlog entry should land after cross-reference "
        f"failure; got types {types!r}"
    )


def test_unknown_id_caught_as_value_error(isolated_log):
    """Bare `except ValueError` clause catches the new error — keeps
    legacy callers correct."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [_story(1, ["req-NOPE"])]}
    spawn = _stub_spawn(output)
    caught = False
    try:
        sm.decompose(spawn_agent=spawn)
    except ValueError:
        caught = True
    assert caught, (
        "DecomposeUnknownRequirementError must be catchable as ValueError"
    )


# ===========================================================================
# Cross-reference happy path (5+) — exact, subset, full coverage all
# succeed. Pins that valid cross-references DON'T trigger the new error.
# ===========================================================================


def test_exact_match_succeeds(isolated_log):
    """Single story rolls up to exactly the iteration's single requirement —
    happy path, no error."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [_story(1, ["req-1"])]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert result["type"] == "story_backlog"


def test_subset_match_succeeds(isolated_log):
    """Story rolls up to one of two iteration requirements — subsets are
    legal, only unknown ids are illegal."""
    import sm
    _seed_iteration(requirement_ids=("req-1", "req-2", "req-3"))
    output = {"stories": [_story(1, ["req-2"])]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert result["type"] == "story_backlog"


def test_full_coverage_succeeds(isolated_log):
    """One story rolls up to ALL iteration requirements — legal."""
    import sm
    _seed_iteration(requirement_ids=("req-1", "req-2", "req-3"))
    output = {"stories": [
        _story(1, ["req-1", "req-2", "req-3"]),
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert len(result["stories"]) == 1
    assert set(result["stories"][0]["requirement_ids"]) == {
        "req-1", "req-2", "req-3"
    }


def test_multiple_stories_each_subset_succeeds(isolated_log):
    """N stories each rolling up to a different subset — happy path."""
    import sm
    _seed_iteration(requirement_ids=("req-1", "req-2", "req-3"))
    output = {"stories": [
        _story(1, ["req-1"]),
        _story(2, ["req-2", "req-3"]),
        _story(3, ["req-1", "req-3"]),
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert len(result["stories"]) == 3


def test_repeated_id_within_story_succeeds(isolated_log):
    """A story listing the same valid requirement_id twice should not
    trigger unknown-requirement error — the id is known, repetition is
    a separate (Story 9) shape concern."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [_story(1, ["req-1", "req-1"])]}
    spawn = _stub_spawn(output)
    # If the implementation does not also reject duplicates, this should
    # succeed. If it does reject duplicates as a SHAPE error, we accept
    # that — but it must NOT be the unknown-requirement error class.
    try:
        sm.decompose(spawn_agent=spawn)
    except sm.DecomposeUnknownRequirementError:  # noqa: BLE001
        pytest.fail(
            "duplicate-but-known requirement_id must not raise "
            "DecomposeUnknownRequirementError; the id IS known"
        )
    except sm.DecomposeOutputShapeError:
        # Acceptable — implementation may decide list-uniqueness is a
        # shape concern. Not the bug Story 10 is fixing.
        pass


# ===========================================================================
# Acceptance-criteria non-empty tightening (5) — empty / whitespace-only
# acceptance_criteria → DecomposeOutputShapeError (Story 9's class —
# Story 10 tightens the validation, not the error type).
# ===========================================================================


def test_empty_string_acceptance_criteria_raises_shape_error(isolated_log):
    """acceptance_criteria = '' → shape error (Story 10 tightening)."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=""),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_whitespace_only_acceptance_criteria_raises_shape_error(isolated_log):
    """acceptance_criteria = '   ' (spaces only) → shape error."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria="     "),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_tab_only_acceptance_criteria_raises_shape_error(isolated_log):
    """acceptance_criteria = '\\t\\t' (tabs only) → shape error."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria="\t\t\t"),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_newline_only_acceptance_criteria_raises_shape_error(isolated_log):
    """acceptance_criteria = '\\n\\n' (newlines only) → shape error."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria="\n\n"),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_mixed_whitespace_acceptance_criteria_raises_shape_error(isolated_log):
    """acceptance_criteria = ' \\t \\n ' (mixed whitespace) → shape error."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=" \t \n \r "),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


# ===========================================================================
# Acceptance-criteria captured verbatim (5+) — no normalization, no trim,
# no markdown processing. Round-trip via the appended log entry.
# ===========================================================================


def test_acceptance_criteria_unicode_preserved(isolated_log):
    """Unicode in AC survives the round-trip byte-for-byte."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    ac = "Validate input — characters: café, naïve, π ≈ 3.14, 日本語"
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=ac),
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert result["stories"][0]["acceptance_criteria"] == ac, (
        "acceptance_criteria must be preserved verbatim including unicode"
    )


def test_acceptance_criteria_newlines_preserved(isolated_log):
    """Embedded newlines inside AC are preserved (multi-line AC blocks)."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    ac = "Line one of AC.\nLine two of AC.\nLine three of AC."
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=ac),
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert result["stories"][0]["acceptance_criteria"] == ac
    assert "\n" in result["stories"][0]["acceptance_criteria"], (
        "newlines inside AC must be preserved"
    )


def test_acceptance_criteria_special_chars_preserved(isolated_log):
    """Special / punctuation chars in AC are preserved verbatim."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    ac = 'AC with "quotes", \'apostrophes\', backslash \\ and {braces} & <tags>'
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=ac),
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert result["stories"][0]["acceptance_criteria"] == ac, (
        f"AC must be preserved byte-for-byte; got "
        f"{result['stories'][0]['acceptance_criteria']!r}"
    )


def test_acceptance_criteria_leading_whitespace_preserved(isolated_log):
    """A non-empty AC with leading whitespace is preserved (no auto-trim).

    Leading/trailing spaces inside an otherwise-substantive AC are NOT
    stripped — the validator's `strip()`-and-check is for emptiness only,
    not for storage normalization.
    """
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    ac = "   leading spaces are preserved on the way to disk"
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=ac),
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert result["stories"][0]["acceptance_criteria"] == ac, (
        "leading whitespace in non-empty AC must be preserved (no auto-trim)"
    )


def test_acceptance_criteria_markdown_not_processed(isolated_log):
    """Markdown chars in AC are stored as-is, not parsed/rendered."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    ac = "# Heading\n- bullet **bold** _italic_ `code` [link](http://x)"
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=ac),
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert result["stories"][0]["acceptance_criteria"] == ac, (
        "markdown chars in AC must be stored verbatim, not processed"
    )


def test_acceptance_criteria_round_trips_through_read_entries(isolated_log):
    """The stored AC survives a full read_entries() round-trip."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    ac = "AC with embedded\ttabs and a — em-dash."
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=ac),
    ]}
    spawn = _stub_spawn(output)
    sm.decompose(spawn_agent=spawn)
    read_back = list(sm.read_entries())[-1]
    assert read_back["stories"][0]["acceptance_criteria"] == ac, (
        "AC must round-trip through read_entries() byte-for-byte"
    )


def test_acceptance_criteria_round_trips_through_derive_state(isolated_log):
    """The stored AC survives a full derive_state() round-trip."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    ac = 'Verbatim AC: keep "this" exactly\nas-is.'
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=ac),
    ]}
    spawn = _stub_spawn(output)
    sm.decompose(spawn_agent=spawn)
    state = sm.derive_state()
    assert state["story_backlog"][0]["acceptance_criteria"] == ac, (
        "AC must round-trip through derive_state() story_backlog "
        "byte-for-byte"
    )


# ===========================================================================
# Error message content (4) — the validation error names the offending
# story (sequence or title) AND the missing/unknown field.
# ===========================================================================


def test_unknown_id_error_message_matches_regex(isolated_log):
    """The error message contains both the story marker (sequence or
    title) AND the unknown id — pinned via a permissive regex."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-NOPE"], title="MyStory"),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError) as exc_info:
        sm.decompose(spawn_agent=spawn)
    msg = str(exc_info.value)
    # The unknown id must appear.
    assert re.search(r"req-NOPE", msg), (
        f"error message must name the unknown id 'req-NOPE'; got: {msg!r}"
    )
    # AND a story marker must appear (title or sequence).
    assert (re.search(r"MyStory", msg) is not None
            or re.search(r"\b1\b", msg) is not None), (
        f"error message must name the story by title or sequence; "
        f"got: {msg!r}"
    )


def test_whitespace_ac_error_names_offending_story(isolated_log):
    """When AC is whitespace-only, the shape error names the offending
    story so the operator can locate it."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-1"], title="EmptyACStory",
               acceptance_criteria="   "),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError) as exc_info:
        sm.decompose(spawn_agent=spawn)
    msg = str(exc_info.value)
    # Either the title or the sequence marker must appear.
    assert ("EmptyACStory" in msg) or (
        re.search(r"\b(?:sequence|story|index)\b\W*1\b", msg,
                  re.IGNORECASE) is not None
    ) or (
        re.search(r"\bstory\W*1\b", msg, re.IGNORECASE) is not None
    ), (
        f"shape error for whitespace-only AC must name the offending "
        f"story; got: {msg!r}"
    )


def test_whitespace_ac_error_names_field(isolated_log):
    """The shape error message names the offending field so the operator
    sees what to fix."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=""),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError) as exc_info:
        sm.decompose(spawn_agent=spawn)
    msg = str(exc_info.value).lower()
    assert "acceptance" in msg or "acceptance_criteria" in msg, (
        f"shape error must name the offending field 'acceptance_criteria'; "
        f"got: {msg!r}"
    )


def test_unknown_id_error_message_is_nonempty_string(isolated_log):
    """The error has a meaningful message (not empty / not just the
    class name)."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    output = {"stories": [_story(1, ["req-NOPE"])]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError) as exc_info:
        sm.decompose(spawn_agent=spawn)
    msg = str(exc_info.value)
    assert isinstance(msg, str)
    assert len(msg.strip()) > 0, (
        "error message must be a non-empty string"
    )


# ===========================================================================
# Failure invariants (5+) — log unchanged on every Story 10 failure.
# ===========================================================================


def test_log_unchanged_after_unknown_id_failure(isolated_log):
    """Cross-reference failure → log byte-for-byte unchanged."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    bytes_before = isolated_log.read_bytes()
    output = {"stories": [_story(1, ["req-NOPE"])]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_whitespace_ac_failure(isolated_log):
    """Whitespace-only AC failure → log byte-for-byte unchanged."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    bytes_before = isolated_log.read_bytes()
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria="   "),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_empty_ac_failure(isolated_log):
    """Empty-string AC failure → log byte-for-byte unchanged."""
    import sm
    _seed_iteration(requirement_ids=("req-1",))
    bytes_before = isolated_log.read_bytes()
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=""),
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == bytes_before


def test_unknown_id_failure_does_not_change_active_iteration(isolated_log):
    """Cross-reference failure must not corrupt the active iteration."""
    import sm
    _seed_iteration(iteration_id="iter-1", requirement_ids=("req-1",))
    before = sm.derive_state()["active_iteration"]
    output = {"stories": [_story(1, ["req-NOPE"])]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeUnknownRequirementError):
        sm.decompose(spawn_agent=spawn)
    after = sm.derive_state()["active_iteration"]
    assert before == after, (
        "active_iteration must be unchanged after cross-reference failure"
    )


def test_round_trip_after_success_story_backlog_in_derive_state(isolated_log):
    """After a successful Story 10-valid decompose, derive_state's
    story_backlog carries the stories — verbatim AC included."""
    import sm
    _seed_iteration(requirement_ids=("req-1", "req-2"))
    ac1 = "AC for story 1 — verbatim"
    ac2 = "AC for story 2 — verbatim"
    output = {"stories": [
        _story(1, ["req-1"], acceptance_criteria=ac1),
        _story(2, ["req-2"], acceptance_criteria=ac2),
    ]}
    spawn = _stub_spawn(output)
    sm.decompose(spawn_agent=spawn)
    state = sm.derive_state()
    assert len(state["story_backlog"]) == 2
    backlog = sorted(state["story_backlog"], key=lambda s: s["sequence"])
    assert backlog[0]["acceptance_criteria"] == ac1
    assert backlog[1]["acceptance_criteria"] == ac2


def test_round_trip_after_success_requirement_ids_match_inputs(isolated_log):
    """After a successful decompose, derive_state's story_backlog carries
    the requirement_ids verbatim from the agent output."""
    import sm
    _seed_iteration(requirement_ids=("req-1", "req-2", "req-3"))
    output = {"stories": [
        _story(1, ["req-1", "req-3"]),
        _story(2, ["req-2"]),
    ]}
    spawn = _stub_spawn(output)
    sm.decompose(spawn_agent=spawn)
    state = sm.derive_state()
    backlog = sorted(state["story_backlog"], key=lambda s: s["sequence"])
    assert backlog[0]["requirement_ids"] == ["req-1", "req-3"]
    assert backlog[1]["requirement_ids"] == ["req-2"]


# ===========================================================================
# Distinct error class (3) — UnknownRequirementError ≠ OutputShapeError
# but both subclass ValueError. CLI surfaces them with distinct exit codes
# (recommended) — tests accept either-or for exit codes.
# ===========================================================================


def test_unknown_requirement_error_not_subclass_of_shape_error():
    """The new class is a SIBLING of OutputShapeError under ValueError,
    not a subclass — callers can branch on the exact class."""
    import sm
    assert not issubclass(sm.DecomposeUnknownRequirementError,
                          sm.DecomposeOutputShapeError), (
        "DecomposeUnknownRequirementError must NOT subclass "
        "DecomposeOutputShapeError — they are distinct error domains"
    )


def test_shape_error_not_subclass_of_unknown_requirement_error():
    """And vice versa — distinct hierarchy, no leak in either direction."""
    import sm
    assert not issubclass(sm.DecomposeOutputShapeError,
                          sm.DecomposeUnknownRequirementError), (
        "DecomposeOutputShapeError must NOT subclass "
        "DecomposeUnknownRequirementError"
    )


def test_unknown_requirement_error_subclasses_value_error_not_other():
    """It subclasses ValueError but NOT the other decompose error
    classes — clean inheritance."""
    import sm
    assert issubclass(sm.DecomposeUnknownRequirementError, ValueError)
    assert not issubclass(sm.DecomposeUnknownRequirementError,
                          sm.DecomposeOutputParseError)


def test_unknown_and_shape_errors_are_distinct_classes():
    """The two classes are distinct objects — `is not` check."""
    import sm
    assert sm.DecomposeUnknownRequirementError is not (
        sm.DecomposeOutputShapeError
    )
    assert sm.DecomposeUnknownRequirementError is not (
        sm.DecomposeOutputParseError
    )


# ===========================================================================
# CLI surface (3) — `python -m sm decompose` exit codes.
# Without an injectable spawn_agent on the CLI, we can only exercise the
# happy and pre-spawn-failure paths via subprocess. Story 10's CLI exit
# codes are pinned via the 'no active iteration' contrast: any nonzero
# code is acceptable, but it must not be 'unknown command'.
# ===========================================================================


def test_cli_decompose_subcommand_known(tmp_path):
    """`python -m sm decompose` is recognized as a known subcommand
    (does not print 'unknown command')."""
    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")

    result = subprocess.run(
        [sys.executable, "-m", "sm", "decompose"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'decompose' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_decompose_no_active_iteration_exits_nonzero(tmp_path):
    """`python -m sm decompose` with empty log → nonzero exit (no active
    iteration). Pin: not the success code, not the 'unknown command'
    failure mode."""
    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")

    result = subprocess.run(
        [sys.executable, "-m", "sm", "decompose"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"decompose with no active iteration must exit nonzero; "
        f"got {result.returncode}"
    )


def test_cli_decompose_help_lists_decompose(tmp_path):
    """The CLI help text mentions decompose, OR `decompose --help` prints
    something other than 'unknown command'. Accommodates whichever help
    surface the implementation chose."""
    result = subprocess.run(
        [sys.executable, "-m", "sm", "--help"],
        cwd=str(PACKAGE_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (result.stdout + result.stderr).lower()
    # Either decompose appears in --help output, OR at least the help
    # ran without erroring on 'unknown command'.
    if "decompose" not in combined:
        # Fall back: confirm `--help` itself was recognized (didn't fail
        # with 'unknown command'); decompose-specific help may live
        # elsewhere.
        assert "unknown command" not in combined, (
            f"--help must run cleanly; got "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
