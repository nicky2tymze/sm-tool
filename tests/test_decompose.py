"""Story 9 — pin the contract of `sm.decompose`.

What this file pins:

  - Function signature and shape:
      `decompose(spawn_agent: Callable[[str, list[dict]], str] = None) -> dict`
    PUBLIC, callable, in `sm.__all__`, importable as
    `from sm import decompose`. Returns the appended `story_backlog` log
    entry on success.

  - Default `spawn_agent` (no kwarg) raises `NotImplementedError` with a
    message mentioning Iter 2 — real agent integration ships later.
    Operators / tests inject a callable to drive the function in Iter 1.

  - Required behavior:
      * Reads the active iteration via `derive_state()`. No active
        iteration → `ValueError("no active iteration; ingest a handoff
        first")`. No log write.
      * Resolves the SM Agent role-spec via `resolve_role_spec("sm_agent")`
        and computes the role-spec hash via `_role_spec_hash("sm_agent")`.
      * Calls `spawn_agent(role_spec_path: str, requirements: list[dict])`
        — synchronous, blocks until the agent returns. The result is
        expected to be a JSON string with shape:
            {"stories": [
              {"sequence": 1, "title": "...", "size": "S"|"M"|"L",
               "requirement_ids": ["req-1", ...],
               "acceptance_criteria": "..."},
              ...
            ]}
      * Parses the agent output. On parse failure raises
        `DecomposeOutputParseError` (a `ValueError` subclass). On shape
        failure raises `DecomposeOutputShapeError` (a `ValueError`
        subclass). On agent exception, the exception propagates (or wraps
        in `DecomposeAgentError`). Every failure path: NO log write.
      * On success: assigns each story a fresh uuid4-hex `story_id`
        (operator's job, not the agent's), then writes a single
        `story_backlog` log entry via `build_entry` + `_append_entry`.
        Returns the entry dict.

  - Story_backlog entry content:
        {
          "stories": [
            {"story_id": "<uuid4-hex>",
             "sequence": int, "title": str, "size": "S"|"M"|"L",
             "requirement_ids": [str, ...],
             "acceptance_criteria": str},
            ...
          ],
          "role_spec_path": "<absolute path string>",
          "role_spec_hash": "<sha256 hex>"
        }

  - Failure invariant: log.jsonl is byte-for-byte unchanged on any
    validation / parse / agent failure.

Tests must FAIL on first run — `decompose`, `DecomposeAgentError`,
`DecomposeOutputParseError`, and `DecomposeOutputShapeError` do not exist
yet. The Coder downstream implements the function and the typed errors to
satisfy these tests.
"""

from __future__ import annotations

import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import time

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


def _seed_iteration(iteration_id: str = "iter-1",
                    requirements=None) -> dict:
    """Append an `iteration_open` entry directly via build_entry + _append_entry
    so a subsequent decompose() has an active iteration to work against.

    Returns the requirements list that landed on the entry.
    """
    import sm
    if requirements is None:
        # Seed req-1 through req-5 so _canonical_agent_output(n) for n in 1..5
        # works without surfacing unknown requirement_ids (Story 10's cross-ref
        # check would correctly flag references to unseeded ids).
        priorities = ["MUST", "SHOULD", "NICE", "MUST", "SHOULD"]
        requirements = [
            {
                "requirement_id": f"req-{i}",
                "title": f"Title {i}",
                "description": f"Description {i}.",
                "priority": priorities[i - 1],
                "acceptance_criteria": f"AC{i}",
            }
            for i in range(1, 6)
        ]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": "Test iteration",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return list(requirements)


def _seed_log_with_one_entry(isolated_log_path: pathlib.Path) -> bytes:
    """Write a single benign (non-iteration_open) entry so the log is non-empty.

    Returns the bytes of the seeded log so tests can verify byte-for-byte
    equality after a failure.
    """
    import sm
    e = sm.build_entry("decompose_test_seed", {"marker": "before"})
    sm._append_entry(e)
    return isolated_log_path.read_bytes()


def _canonical_agent_output(n: int = 2) -> dict:
    """Build a canonical, valid agent-output dict (n stories)."""
    sizes = ["S", "M", "L"]
    stories = []
    for i in range(1, n + 1):
        stories.append({
            "sequence": i,
            "title": f"Story {i}",
            "size": sizes[(i - 1) % 3],
            "requirement_ids": [f"req-{i}"],
            "acceptance_criteria": f"Story {i} must pass its tests.",
        })
    return {"stories": stories}


def _stub_spawn(output_dict_or_str):
    """Build a spawn_agent stub that returns the given output as a JSON string.

    If a dict is passed, it's `json.dumps`-ed. If a str is passed, it's
    returned as-is (so tests can inject malformed JSON).
    """
    if isinstance(output_dict_or_str, str):
        payload = output_dict_or_str
    else:
        payload = json.dumps(output_dict_or_str)

    def _spawn(role_spec_path, requirements):
        return payload

    return _spawn


def _capturing_spawn(output_dict_or_str):
    """Build a spawn_agent stub that records its (role_spec_path, requirements)
    args and returns the given output. Returns (spawn_fn, captured_dict)."""
    captured = {"calls": [], "role_spec_path": None, "requirements": None}

    if isinstance(output_dict_or_str, str):
        payload = output_dict_or_str
    else:
        payload = json.dumps(output_dict_or_str)

    def _spawn(role_spec_path, requirements):
        captured["calls"].append({
            "role_spec_path": role_spec_path,
            "requirements": requirements,
        })
        captured["role_spec_path"] = role_spec_path
        captured["requirements"] = requirements
        return payload

    return _spawn, captured


# ===========================================================================
# Smoke (5+) — function exists, callable, public, in __all__, accepts kwarg
# ===========================================================================


def test_function_exists_on_module():
    import sm
    assert hasattr(sm, "decompose"), "sm.decompose must exist"


def test_function_is_callable():
    import sm
    assert callable(sm.decompose)


def test_function_name_is_public():
    """No leading underscore — public API."""
    import sm
    assert not sm.decompose.__name__.startswith("_")
    assert sm.decompose.__name__ == "decompose"


def test_function_importable_directly():
    """`from sm import decompose` succeeds — public-import form."""
    from sm import decompose  # noqa: F401
    assert callable(decompose)


def test_function_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "decompose" in sm.__all__, (
        f"decompose must be in __all__; got {sm.__all__!r}"
    )


def test_signature_accepts_spawn_agent_kwarg():
    """decompose accepts a `spawn_agent` keyword argument."""
    import sm
    sig = inspect.signature(sm.decompose)
    assert "spawn_agent" in sig.parameters, (
        f"decompose must accept a 'spawn_agent' kwarg; "
        f"got params {list(sig.parameters)!r}"
    )


def test_spawn_agent_is_optional_kwarg():
    """spawn_agent has a default — call decompose() with no args is legal
    syntax (it raises NotImplementedError, but that's behavior, not signature)."""
    import sm
    sig = inspect.signature(sm.decompose)
    p = sig.parameters["spawn_agent"]
    assert p.default is not inspect.Parameter.empty, (
        "spawn_agent must have a default value (so decompose() with no args "
        "is a legal call)"
    )


# ===========================================================================
# Default spawn_agent refuses to silently run without proper setup (3+)
#
# Iter 2 Story 6 cascade: the Iter 1 default raised NotImplementedError
# unconditionally. With Story 6, the default IS implemented but it
# refuses to run without an `ANTHROPIC_API_KEY` — the original intent
# ("default refuses to silently run") is preserved; the mechanism
# changed from NotImplementedError to MissingAPIKeyError.
# ===========================================================================


def test_default_spawn_agent_raises_not_implemented(isolated_log, monkeypatch):
    """No spawn_agent passed + no API key → MissingAPIKeyError.

    Iter 1 historic name; under Iter 2 Story 6 the default raises
    MissingAPIKeyError instead of NotImplementedError. Same intent:
    the default refuses to run without the operator's explicit setup.
    """
    import sm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed_iteration()
    with pytest.raises(sm.MissingAPIKeyError):
        sm.decompose()


def test_default_spawn_agent_error_mentions_iter_2(isolated_log, monkeypatch):
    """The default-refusal error names the missing env var so the
    operator knows how to fix it.

    Iter 1 historic name; under Iter 2 Story 6 the error is
    MissingAPIKeyError and names `ANTHROPIC_API_KEY` (Story 2's
    actionable-error pin), re-asserted in the cascade context.
    """
    import sm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed_iteration()
    with pytest.raises(sm.MissingAPIKeyError) as exc_info:
        sm.decompose()
    assert "ANTHROPIC_API_KEY" in str(exc_info.value), (
        f"MissingAPIKeyError must name ANTHROPIC_API_KEY; got: "
        f"{exc_info.value!s}"
    )


def test_default_spawn_agent_writes_no_entry(isolated_log, monkeypatch):
    """When the default refuses to run, no log entry is written.

    Iter 1 pinned NotImplementedError; under Iter 2 Story 6 the trigger
    is MissingAPIKeyError. The log-unchanged invariant survives
    verbatim.
    """
    import sm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed_iteration()
    seeded = isolated_log.read_bytes()
    with pytest.raises(sm.MissingAPIKeyError):
        sm.decompose()
    assert isolated_log.read_bytes() == seeded


def test_explicit_none_spawn_agent_raises_not_implemented(
        isolated_log, monkeypatch):
    """Passing spawn_agent=None is the same as omitting it — uses default.

    Iter 1 historic name; under Iter 2 Story 6 the default raises
    MissingAPIKeyError when ANTHROPIC_API_KEY is unset.
    """
    import sm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed_iteration()
    with pytest.raises(sm.MissingAPIKeyError):
        sm.decompose(spawn_agent=None)


# ===========================================================================
# No active iteration (4+)
# ===========================================================================


def test_no_active_iteration_raises_value_error(isolated_log):
    """Empty log (no iteration_open entry) → ValueError."""
    import sm
    spawn = _stub_spawn(_canonical_agent_output())
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)


def test_no_active_iteration_error_message(isolated_log):
    """Error message names the missing-iteration condition."""
    import sm
    spawn = _stub_spawn(_canonical_agent_output())
    with pytest.raises(ValueError) as exc_info:
        sm.decompose(spawn_agent=spawn)
    msg = str(exc_info.value).lower()
    assert "iteration" in msg, (
        f"error must mention 'iteration'; got: {exc_info.value!s}"
    )


def test_no_active_iteration_does_not_write_log(isolated_log):
    """No iteration → no log write."""
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    spawn = _stub_spawn(_canonical_agent_output())
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == seeded


def test_no_active_iteration_after_close_raises(isolated_log):
    """An iteration that's been closed leaves no active iteration."""
    import sm
    _seed_iteration(iteration_id="iter-1")
    close = sm.build_entry("iteration_close", {
        "closed_by": "operator", "reason": None,
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 0,
    })
    sm._append_entry(close)
    spawn = _stub_spawn(_canonical_agent_output())
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)


def test_no_active_iteration_does_not_call_spawn(isolated_log):
    """Decomposing with no active iteration must not call spawn_agent."""
    import sm

    calls = {"n": 0}

    def spawn(role_spec_path, requirements):
        calls["n"] += 1
        return json.dumps(_canonical_agent_output())

    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)
    assert calls["n"] == 0, (
        f"spawn_agent must not be called when no iteration is active; "
        f"got {calls['n']} call(s)"
    )


def test_no_active_iteration_derive_state_unchanged(isolated_log):
    """derive_state before/after the failed call is equal."""
    import sm
    _seed_log_with_one_entry(isolated_log)
    before = sm.derive_state()
    spawn = _stub_spawn(_canonical_agent_output())
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)
    after = sm.derive_state()
    assert before == after


# ===========================================================================
# Happy path with injected stub (10+)
# ===========================================================================


def test_happy_path_writes_one_entry(isolated_log):
    """A valid decompose run appends exactly one new log entry."""
    import sm
    _seed_iteration()
    before = list(sm.read_entries())
    spawn = _stub_spawn(_canonical_agent_output(n=2))
    sm.decompose(spawn_agent=spawn)
    after = list(sm.read_entries())
    assert len(after) == len(before) + 1


def test_happy_path_entry_type_is_story_backlog(isolated_log):
    """The single emitted entry has type `story_backlog`."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    sm.decompose(spawn_agent=spawn)
    entries = list(sm.read_entries())
    assert entries[-1]["type"] == "story_backlog", (
        f"latest entry type must be 'story_backlog'; "
        f"got {entries[-1]['type']!r}"
    )


def test_happy_path_returns_appended_entry(isolated_log):
    """decompose returns the dict that was appended to the log."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    entries = list(sm.read_entries())
    assert result == entries[-1]


def test_happy_path_return_value_is_dict(isolated_log):
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    assert isinstance(result, dict)


def test_happy_path_entry_has_canonical_fields(isolated_log):
    """The emitted entry has id, type, timestamp from build_entry."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    assert "id" in result
    assert "type" in result
    assert "timestamp" in result


def test_happy_path_entry_carries_stories_key(isolated_log):
    """The entry's content contains a `stories` list."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=3))
    result = sm.decompose(spawn_agent=spawn)
    assert "stories" in result
    assert isinstance(result["stories"], list)
    assert len(result["stories"]) == 3


def test_happy_path_each_story_has_assigned_story_id(isolated_log):
    """Each story in the appended entry has a non-empty `story_id`."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=4))
    result = sm.decompose(spawn_agent=spawn)
    for s in result["stories"]:
        assert "story_id" in s, f"story missing story_id: {s!r}"
        assert isinstance(s["story_id"], str), (
            f"story_id must be a string; got {s['story_id']!r}"
        )
        assert len(s["story_id"]) > 0, (
            f"story_id must be non-empty; got {s['story_id']!r}"
        )


def test_happy_path_entry_has_role_spec_path(isolated_log):
    """The entry carries the resolved role-spec path as a string."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    assert "role_spec_path" in result
    assert isinstance(result["role_spec_path"], str)
    assert len(result["role_spec_path"]) > 0


def test_happy_path_entry_has_role_spec_hash(isolated_log):
    """The entry carries the resolved role-spec hash."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    assert "role_spec_hash" in result
    assert isinstance(result["role_spec_hash"], str)
    assert re.fullmatch(r"[0-9a-f]+", result["role_spec_hash"]), (
        f"role_spec_hash must be a hex digest; got {result['role_spec_hash']!r}"
    )


def test_happy_path_sequence_preserved(isolated_log):
    """The agent's `sequence` numbers are preserved into the entry."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=3))
    result = sm.decompose(spawn_agent=spawn)
    seqs = [s["sequence"] for s in result["stories"]]
    assert seqs == [1, 2, 3]


def test_happy_path_titles_preserved(isolated_log):
    """The agent's titles are preserved into the entry."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 1, "title": "Build the foo",
         "size": "S", "requirement_ids": ["req-1"],
         "acceptance_criteria": "AC"},
        {"sequence": 2, "title": "Wire the bar",
         "size": "M", "requirement_ids": ["req-1"],
         "acceptance_criteria": "AC"},
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    titles = [s["title"] for s in result["stories"]]
    assert titles == ["Build the foo", "Wire the bar"]


def test_happy_path_sizes_preserved(isolated_log):
    """The agent's sizes are preserved into the entry."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 1, "title": "x", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
        {"sequence": 2, "title": "y", "size": "M",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
        {"sequence": 3, "title": "z", "size": "L",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    sizes = [s["size"] for s in result["stories"]]
    assert sizes == ["S", "M", "L"]


def test_happy_path_requirement_ids_preserved(isolated_log):
    """The agent's requirement_ids are preserved into the entry."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 1, "title": "x", "size": "S",
         "requirement_ids": ["req-1", "req-2"],
         "acceptance_criteria": "AC1"},
        {"sequence": 2, "title": "y", "size": "M",
         "requirement_ids": ["req-2"],
         "acceptance_criteria": "AC2"},
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert result["stories"][0]["requirement_ids"] == ["req-1", "req-2"]
    assert result["stories"][1]["requirement_ids"] == ["req-2"]


def test_happy_path_acceptance_criteria_preserved(isolated_log):
    """The agent's acceptance_criteria are preserved into the entry."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 1, "title": "x", "size": "S",
         "requirement_ids": ["req-1"],
         "acceptance_criteria": "must validate inputs"},
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    assert result["stories"][0]["acceptance_criteria"] == "must validate inputs"


# ===========================================================================
# Story_id minting (5+)
# ===========================================================================


def test_story_ids_are_unique_within_backlog(isolated_log):
    """Each minted story_id is unique within a single decompose call."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=5))
    result = sm.decompose(spawn_agent=spawn)
    ids = [s["story_id"] for s in result["stories"]]
    assert len(set(ids)) == len(ids), (
        f"story_ids must be unique; got duplicates in {ids!r}"
    )


def test_story_ids_are_uuid4_hex_format(isolated_log):
    """Each story_id is a 32-char lowercase hex string (uuid4 hex)."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=3))
    result = sm.decompose(spawn_agent=spawn)
    for s in result["stories"]:
        sid = s["story_id"]
        assert re.fullmatch(r"[0-9a-f]{32}", sid), (
            f"story_id must be 32-char lowercase hex (uuid4); got {sid!r}"
        )


def test_story_ids_are_strings(isolated_log):
    """story_id must be a string, not bytes / int / uuid object."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=2))
    result = sm.decompose(spawn_agent=spawn)
    for s in result["stories"]:
        assert isinstance(s["story_id"], str)


def test_story_ids_differ_across_two_runs(isolated_log):
    """Running decompose twice (after re-opening) yields fresh story_ids."""
    import sm

    # First iteration → first decompose
    _seed_iteration(iteration_id="iter-1")
    spawn1 = _stub_spawn(_canonical_agent_output(n=2))
    result1 = sm.decompose(spawn_agent=spawn1)
    ids_1 = {s["story_id"] for s in result1["stories"]}

    # Close it.
    close = sm.build_entry("iteration_close", {
        "closed_by": "operator", "reason": None,
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 0,
    })
    sm._append_entry(close)

    # Second iteration → second decompose
    _seed_iteration(iteration_id="iter-2")
    spawn2 = _stub_spawn(_canonical_agent_output(n=2))
    result2 = sm.decompose(spawn_agent=spawn2)
    ids_2 = {s["story_id"] for s in result2["stories"]}

    # Two independent uuid4-mintings: with overwhelming probability, no overlap.
    assert ids_1.isdisjoint(ids_2), (
        f"fresh decompose should mint fresh story_ids; "
        f"saw overlap: {ids_1 & ids_2!r}"
    )


def test_story_id_not_provided_by_agent(isolated_log):
    """Even if the agent's output happens to include a story_id field,
    the tool overrides it with its own minted uuid4 (operator's job, not
    the agent's)."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 1, "title": "x", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC",
         "story_id": "agent-supplied-id"},
    ]}
    spawn = _stub_spawn(output)
    result = sm.decompose(spawn_agent=spawn)
    sid = result["stories"][0]["story_id"]
    assert sid != "agent-supplied-id", (
        f"tool must mint story_id, not pass-through agent's; got {sid!r}"
    )
    assert re.fullmatch(r"[0-9a-f]{32}", sid)


def test_story_id_minted_for_every_story(isolated_log):
    """Every story in the output has a story_id, even with N=1 or N=10."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=1))
    result = sm.decompose(spawn_agent=spawn)
    assert len(result["stories"]) == 1
    assert "story_id" in result["stories"][0]


# ===========================================================================
# Agent output parse failures (8+) — DecomposeOutputParseError, no log write
# ===========================================================================


def test_parse_error_class_exists():
    """DecomposeOutputParseError must exist on sm."""
    import sm
    assert hasattr(sm, "DecomposeOutputParseError"), (
        "sm.DecomposeOutputParseError must exist"
    )


def test_parse_error_subclasses_value_error():
    """DecomposeOutputParseError subclasses ValueError so existing
    `except ValueError` callers keep working."""
    import sm
    assert issubclass(sm.DecomposeOutputParseError, ValueError), (
        "DecomposeOutputParseError must subclass ValueError"
    )


def test_non_json_string_raises_parse_error(isolated_log):
    """Agent returns non-JSON garbage → DecomposeOutputParseError."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn("not json at all")
    with pytest.raises(sm.DecomposeOutputParseError):
        sm.decompose(spawn_agent=spawn)


def test_malformed_json_raises_parse_error(isolated_log):
    """Agent returns truncated/malformed JSON → DecomposeOutputParseError."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn('{"stories": [')  # truncated
    with pytest.raises(sm.DecomposeOutputParseError):
        sm.decompose(spawn_agent=spawn)


def test_empty_string_raises_parse_error(isolated_log):
    """Agent returns the empty string → DecomposeOutputParseError."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn("")
    with pytest.raises(sm.DecomposeOutputParseError):
        sm.decompose(spawn_agent=spawn)


def test_whitespace_only_raises_parse_error(isolated_log):
    """Agent returns whitespace-only → DecomposeOutputParseError."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn("   \n\t  ")
    with pytest.raises(sm.DecomposeOutputParseError):
        sm.decompose(spawn_agent=spawn)


def test_trailing_garbage_raises_parse_error(isolated_log):
    """Agent returns valid JSON followed by garbage → parse error."""
    import sm
    _seed_iteration()
    payload = json.dumps(_canonical_agent_output()) + "GARBAGE"
    spawn = _stub_spawn(payload)
    with pytest.raises(sm.DecomposeOutputParseError):
        sm.decompose(spawn_agent=spawn)


def test_parse_error_writes_no_log_entry(isolated_log):
    """On parse failure, the log is byte-for-byte unchanged."""
    import sm
    _seed_iteration()
    seeded = isolated_log.read_bytes()
    spawn = _stub_spawn("{ malformed")
    with pytest.raises(sm.DecomposeOutputParseError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == seeded


def test_parse_error_caught_as_value_error(isolated_log):
    """A bare `except ValueError` clause catches DecomposeOutputParseError."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn("{ not json")
    caught = False
    try:
        sm.decompose(spawn_agent=spawn)
    except ValueError:
        caught = True
    assert caught, (
        "DecomposeOutputParseError must be catchable as ValueError"
    )


# ===========================================================================
# Agent output shape failures (12+) — DecomposeOutputShapeError, no log write
# ===========================================================================


def test_shape_error_class_exists():
    """DecomposeOutputShapeError must exist on sm."""
    import sm
    assert hasattr(sm, "DecomposeOutputShapeError"), (
        "sm.DecomposeOutputShapeError must exist"
    )


def test_shape_error_subclasses_value_error():
    """DecomposeOutputShapeError subclasses ValueError."""
    import sm
    assert issubclass(sm.DecomposeOutputShapeError, ValueError), (
        "DecomposeOutputShapeError must subclass ValueError"
    )


def test_top_level_non_object_raises_shape_error(isolated_log):
    """Agent returns a JSON list at top level → shape error."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn("[1, 2, 3]")
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_top_level_string_raises_shape_error(isolated_log):
    """Agent returns a JSON string at top level → shape error."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(json.dumps("just a string"))
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_missing_stories_key_raises_shape_error(isolated_log):
    """Agent returns an object without `stories` → shape error."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn({"not_stories": []})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_stories_not_list_raises_shape_error(isolated_log):
    """`stories` must be a list, not a dict / string / int."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn({"stories": {"story-1": "..."}})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_stories_string_raises_shape_error(isolated_log):
    """`stories` as a string is not a list — even though strings are iterable."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn({"stories": "story-1"})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_stories_null_raises_shape_error(isolated_log):
    """`stories` of null is invalid."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn({"stories": None})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_empty_stories_list_raises_shape_error(isolated_log):
    """An empty stories list is not a meaningful decomposition."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn({"stories": []})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_story_not_dict_raises_shape_error(isolated_log):
    """A non-dict story raises shape error."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn({"stories": ["just a string"]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_story_missing_sequence_raises_shape_error(isolated_log):
    """A story missing `sequence` raises shape error."""
    import sm
    _seed_iteration()
    bad = {"title": "x", "size": "S",
           "requirement_ids": ["req-1"], "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_story_missing_title_raises_shape_error(isolated_log):
    """A story missing `title` raises shape error."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "size": "S",
           "requirement_ids": ["req-1"], "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_story_missing_size_raises_shape_error(isolated_log):
    """A story missing `size` raises shape error."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x",
           "requirement_ids": ["req-1"], "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_story_missing_requirement_ids_raises_shape_error(isolated_log):
    """A story missing `requirement_ids` raises shape error."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x", "size": "S",
           "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_story_missing_acceptance_criteria_raises_shape_error(isolated_log):
    """A story missing `acceptance_criteria` raises shape error."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x", "size": "S",
           "requirement_ids": ["req-1"]}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_story_bad_size_raises_shape_error(isolated_log):
    """A story whose size is not in {S, M, L} raises shape error."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x", "size": "XL",
           "requirement_ids": ["req-1"], "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_story_lowercase_size_raises_shape_error(isolated_log):
    """Sizes are case-sensitive — 's' is not 'S'."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x", "size": "s",
           "requirement_ids": ["req-1"], "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_story_size_null_raises_shape_error(isolated_log):
    """size=None raises shape error."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x", "size": None,
           "requirement_ids": ["req-1"], "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_sequences_skipping_raises_shape_error(isolated_log):
    """Sequences that skip (1, 3) raise shape error — must be strictly +1."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 1, "title": "x", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
        {"sequence": 3, "title": "y", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_sequences_duplicate_raises_shape_error(isolated_log):
    """Duplicate sequences raise shape error."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 1, "title": "x", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
        {"sequence": 1, "title": "y", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_sequences_not_starting_at_1_raises_shape_error(isolated_log):
    """First sequence must be 1, not 0 or 2."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 0, "title": "x", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
        {"sequence": 1, "title": "y", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_sequences_starting_at_2_raises_shape_error(isolated_log):
    """Sequences starting at 2 (skipping 1) raise shape error."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 2, "title": "x", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
        {"sequence": 3, "title": "y", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_sequences_descending_raises_shape_error(isolated_log):
    """Sequences must be strictly increasing — descending is invalid."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 2, "title": "x", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
        {"sequence": 1, "title": "y", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC"},
    ]}
    spawn = _stub_spawn(output)
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_requirement_ids_not_list_raises_shape_error(isolated_log):
    """requirement_ids must be a list, not a string."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x", "size": "S",
           "requirement_ids": "req-1", "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_requirement_ids_empty_list_raises_shape_error(isolated_log):
    """requirement_ids must be a non-empty list — every story rolls up to
    at least one requirement."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x", "size": "S",
           "requirement_ids": [], "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_requirement_ids_with_non_string_raises_shape_error(isolated_log):
    """requirement_ids must be a list of strings — ints rejected."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x", "size": "S",
           "requirement_ids": ["req-1", 42], "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_requirement_ids_null_raises_shape_error(isolated_log):
    """requirement_ids of None raises shape error."""
    import sm
    _seed_iteration()
    bad = {"sequence": 1, "title": "x", "size": "S",
           "requirement_ids": None, "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)


def test_shape_error_caught_as_value_error(isolated_log):
    """A bare `except ValueError` clause catches DecomposeOutputShapeError."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn({"stories": []})
    caught = False
    try:
        sm.decompose(spawn_agent=spawn)
    except ValueError:
        caught = True
    assert caught


# ===========================================================================
# Failure invariants — log byte-for-byte unchanged on any failure (5+)
# ===========================================================================


def test_log_unchanged_after_no_active_iteration(isolated_log):
    """No iteration → no log write."""
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    spawn = _stub_spawn(_canonical_agent_output())
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == seeded


def test_log_unchanged_after_parse_error(isolated_log):
    """Parse error → log unchanged."""
    import sm
    _seed_iteration()
    bytes_before = isolated_log.read_bytes()
    spawn = _stub_spawn("{ malformed")
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_shape_error_missing_stories(isolated_log):
    """Shape error → log unchanged."""
    import sm
    _seed_iteration()
    bytes_before = isolated_log.read_bytes()
    spawn = _stub_spawn({"not_stories": []})
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_shape_error_empty_stories(isolated_log):
    """Empty stories list → no log write."""
    import sm
    _seed_iteration()
    bytes_before = isolated_log.read_bytes()
    spawn = _stub_spawn({"stories": []})
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_shape_error_bad_size(isolated_log):
    """Bad size → log unchanged."""
    import sm
    _seed_iteration()
    bytes_before = isolated_log.read_bytes()
    bad = {"sequence": 1, "title": "x", "size": "huge",
           "requirement_ids": ["req-1"], "acceptance_criteria": "AC"}
    spawn = _stub_spawn({"stories": [bad]})
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_agent_exception(isolated_log):
    """If spawn_agent itself raises, the log is unchanged."""
    import sm
    _seed_iteration()
    bytes_before = isolated_log.read_bytes()
    # Sentinel — make sure the boom path actually fires (not, e.g., a
    # bare AttributeError from `sm.decompose` being missing).
    assert callable(getattr(sm, "decompose", None)), (
        "sm.decompose must exist for this test to be meaningful"
    )

    def boom(role_spec_path, requirements):
        raise RuntimeError("agent process exited 1")

    with pytest.raises(RuntimeError):
        sm.decompose(spawn_agent=boom)
    assert isolated_log.read_bytes() == bytes_before


def test_log_unchanged_after_default_spawn_agent_raises(
        isolated_log, monkeypatch):
    """Default spawn_agent refuses to run → log unchanged.

    Iter 1 pinned NotImplementedError; under Iter 2 Story 6 the trigger
    is MissingAPIKeyError (no ANTHROPIC_API_KEY). The log-unchanged
    invariant survives the mechanism swap.
    """
    import sm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed_iteration()
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.MissingAPIKeyError):
        sm.decompose()
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# Round-trip via derive_state (4+)
# ===========================================================================


def test_round_trip_story_backlog_populated(isolated_log):
    """After decompose, derive_state.story_backlog has the same N stories."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=3))
    sm.decompose(spawn_agent=spawn)
    state = sm.derive_state()
    assert len(state["story_backlog"]) == 3


def test_round_trip_story_backlog_preserves_sequence_order(isolated_log):
    """derive_state's story_backlog is ordered by sequence ascending."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=3))
    sm.decompose(spawn_agent=spawn)
    state = sm.derive_state()
    seqs = [s["sequence"] for s in state["story_backlog"]]
    assert seqs == sorted(seqs)
    assert seqs == [1, 2, 3]


def test_round_trip_story_states_all_planned(isolated_log):
    """Each freshly decomposed story is in the 'planned' state."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=3))
    result = sm.decompose(spawn_agent=spawn)
    state = sm.derive_state()
    for s in result["stories"]:
        assert state["story_states"][s["story_id"]] == "planned"


def test_round_trip_story_backlog_carries_story_ids(isolated_log):
    """derive_state story_backlog carries each story's minted story_id."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=2))
    result = sm.decompose(spawn_agent=spawn)
    state = sm.derive_state()
    backlog_ids = {s["story_id"] for s in state["story_backlog"]}
    appended_ids = {s["story_id"] for s in result["stories"]}
    assert backlog_ids == appended_ids


def test_round_trip_story_backlog_carries_titles_sizes_acs(isolated_log):
    """derive_state story_backlog carries titles, sizes, and ACs verbatim."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 1, "title": "Build A", "size": "S",
         "requirement_ids": ["req-1"], "acceptance_criteria": "AC-A"},
        {"sequence": 2, "title": "Build B", "size": "M",
         "requirement_ids": ["req-2"], "acceptance_criteria": "AC-B"},
    ]}
    spawn = _stub_spawn(output)
    sm.decompose(spawn_agent=spawn)
    state = sm.derive_state()
    backlog = state["story_backlog"]
    assert backlog[0]["title"] == "Build A"
    assert backlog[0]["size"] == "S"
    assert backlog[0]["acceptance_criteria"] == "AC-A"
    assert backlog[1]["title"] == "Build B"
    assert backlog[1]["size"] == "M"
    assert backlog[1]["acceptance_criteria"] == "AC-B"


# ===========================================================================
# Role-spec wiring (4+)
# ===========================================================================


def test_role_spec_path_in_entry_matches_resolver(isolated_log):
    """The entry's role_spec_path equals str(resolve_role_spec('sm_agent'))."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    expected = str(sm.resolve_role_spec("sm_agent"))
    assert result["role_spec_path"] == expected, (
        f"role_spec_path mismatch:\n  expected {expected!r}\n  "
        f"got      {result['role_spec_path']!r}"
    )


def test_role_spec_hash_in_entry_matches_helper(isolated_log):
    """The entry's role_spec_hash equals _role_spec_hash('sm_agent')."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    expected = sm._role_spec_hash("sm_agent")
    assert result["role_spec_hash"] == expected, (
        f"role_spec_hash mismatch:\n  expected {expected!r}\n  "
        f"got      {result['role_spec_hash']!r}"
    )


def test_spawn_agent_receives_role_spec_path(isolated_log):
    """spawn_agent is called with the resolved role-spec path (sm_agent.md)."""
    import sm
    _seed_iteration()
    spawn, captured = _capturing_spawn(_canonical_agent_output())
    sm.decompose(spawn_agent=spawn)
    assert captured["role_spec_path"] is not None, (
        "spawn_agent must be called with a role_spec_path"
    )
    expected = str(sm.resolve_role_spec("sm_agent"))
    assert captured["role_spec_path"] == expected, (
        f"spawn_agent role_spec_path mismatch:\n  "
        f"expected {expected!r}\n  got      {captured['role_spec_path']!r}"
    )


def test_spawn_agent_receives_active_iteration_requirements(isolated_log):
    """spawn_agent is called with the active iteration's requirements list."""
    import sm
    reqs = [
        {"requirement_id": "req-A", "title": "A", "description": "DA",
         "priority": "MUST", "acceptance_criteria": "ACA"},
        {"requirement_id": "req-B", "title": "B", "description": "DB",
         "priority": "SHOULD", "acceptance_criteria": "ACB"},
    ]
    _seed_iteration(requirements=reqs)
    # Custom agent output referencing only the seeded requirement_ids
    # (req-A / req-B), so Story 10's cross-ref check stays clean while
    # the assertion verifies spawn_agent received the seeded requirements list.
    custom_output = {
        "stories": [
            {"sequence": 1, "title": "Story 1", "size": "S",
             "requirement_ids": ["req-A"],
             "acceptance_criteria": "Story 1 must pass its tests."},
            {"sequence": 2, "title": "Story 2", "size": "M",
             "requirement_ids": ["req-B"],
             "acceptance_criteria": "Story 2 must pass its tests."},
        ]
    }
    spawn, captured = _capturing_spawn(custom_output)
    sm.decompose(spawn_agent=spawn)
    assert captured["requirements"] == reqs, (
        f"spawn_agent must receive the active iteration's requirements;\n"
        f"  expected {reqs!r}\n  got      {captured['requirements']!r}"
    )


def test_spawn_agent_called_exactly_once(isolated_log):
    """spawn_agent is called exactly once per decompose call."""
    import sm
    _seed_iteration()
    spawn, captured = _capturing_spawn(_canonical_agent_output())
    sm.decompose(spawn_agent=spawn)
    assert len(captured["calls"]) == 1, (
        f"spawn_agent must be called exactly once; "
        f"got {len(captured['calls'])} calls"
    )


# ===========================================================================
# Synchronous behavior (2+)
# ===========================================================================


def test_decompose_blocks_until_spawn_returns(isolated_log):
    """decompose returns only after spawn_agent returns."""
    import sm
    _seed_iteration()

    state = {"spawn_returned": False}

    def slow_spawn(role_spec_path, requirements):
        time.sleep(0.05)
        state["spawn_returned"] = True
        return json.dumps(_canonical_agent_output())

    sm.decompose(spawn_agent=slow_spawn)
    # By the time decompose returned, spawn must have returned too.
    assert state["spawn_returned"], (
        "decompose must block until spawn_agent returns"
    )


def test_decompose_runtime_at_least_spawn_runtime(isolated_log):
    """If spawn sleeps for X seconds, decompose takes at least X seconds."""
    import sm
    _seed_iteration()

    SLEEP_S = 0.10

    def slow_spawn(role_spec_path, requirements):
        time.sleep(SLEEP_S)
        return json.dumps(_canonical_agent_output())

    t0 = time.time()
    sm.decompose(spawn_agent=slow_spawn)
    elapsed = time.time() - t0
    # Allow a generous fudge to absorb scheduler jitter; the point is that
    # decompose did not return BEFORE spawn.
    assert elapsed >= SLEEP_S * 0.8, (
        f"decompose returned too fast — must block on spawn_agent. "
        f"elapsed={elapsed:.3f}s sleep={SLEEP_S:.3f}s"
    )


# ===========================================================================
# Built via build_entry (3+)
# ===========================================================================


def test_uses_build_entry(isolated_log, monkeypatch):
    """decompose must go through sm.build_entry for the story_backlog entry."""
    import sm
    _seed_iteration()

    calls = {"n": 0, "types": []}
    real = sm.build_entry

    def fake(type_, content):
        calls["n"] += 1
        calls["types"].append(type_)
        return real(type_, content)

    monkeypatch.setattr(sm, "build_entry", fake)
    spawn = _stub_spawn(_canonical_agent_output())
    sm.decompose(spawn_agent=spawn)
    # decompose must call build_entry at least once with type=story_backlog.
    assert "story_backlog" in calls["types"], (
        f"decompose must call build_entry(type='story_backlog'); "
        f"got types {calls['types']!r}"
    )


def test_uses_append_entry(isolated_log, monkeypatch):
    """decompose must go through sm._append_entry for the story_backlog entry."""
    import sm
    _seed_iteration()

    calls = {"n": 0, "entries": []}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        calls["entries"].append(entry)
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    spawn = _stub_spawn(_canonical_agent_output())
    sm.decompose(spawn_agent=spawn)
    # exactly one append, with type=story_backlog
    assert calls["n"] == 1, (
        f"decompose must call _append_entry exactly once; got {calls['n']}"
    )
    assert calls["entries"][0]["type"] == "story_backlog"


def test_append_entry_not_called_on_validation_failure(isolated_log,
                                                       monkeypatch):
    """Output-shape failure → no append. Pin the wire-up."""
    import sm
    _seed_iteration()

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    spawn = _stub_spawn({"stories": []})  # empty → shape error
    with pytest.raises(sm.DecomposeOutputShapeError):
        sm.decompose(spawn_agent=spawn)
    assert calls["n"] == 0, (
        f"_append_entry must not be called on validation failure; "
        f"got {calls['n']} call(s)"
    )


def test_append_entry_not_called_on_parse_failure(isolated_log, monkeypatch):
    """Parse failure → no append."""
    import sm
    _seed_iteration()

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    spawn = _stub_spawn("{ malformed")
    with pytest.raises(sm.DecomposeOutputParseError):
        sm.decompose(spawn_agent=spawn)
    assert calls["n"] == 0


def test_append_entry_not_called_when_no_active_iteration(isolated_log,
                                                          monkeypatch):
    """No active iteration → no append."""
    import sm

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    spawn = _stub_spawn(_canonical_agent_output())
    with pytest.raises(ValueError):
        sm.decompose(spawn_agent=spawn)
    assert calls["n"] == 0


# ===========================================================================
# Entry shape — id/type/timestamp from build_entry
# ===========================================================================


def test_entry_id_is_uuid_hex(isolated_log):
    """The story_backlog entry has a uuid4-hex id."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    assert re.fullmatch(r"[0-9a-f]{32}", result["id"])


def test_entry_timestamp_is_iso8601(isolated_log):
    """The story_backlog entry has an ISO-8601 timestamp."""
    import sm
    import datetime as _dt
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    # fromisoformat tolerates the local-tz offset format used by build_entry.
    parsed = _dt.datetime.fromisoformat(result["timestamp"])
    assert parsed is not None


def test_entry_id_differs_from_iteration_open_id(isolated_log):
    """Each entry gets a fresh id — story_backlog id != iteration_open id."""
    import sm
    _seed_iteration()
    iter_open = list(sm.read_entries())[0]
    spawn = _stub_spawn(_canonical_agent_output())
    result = sm.decompose(spawn_agent=spawn)
    assert result["id"] != iter_open["id"]


# ===========================================================================
# Round-trip — written entry round-trips through read_entries
# ===========================================================================


def test_written_entry_round_trips_through_read_entries(isolated_log):
    """The entry returned by decompose() equals the entry read back."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output(n=2))
    returned = sm.decompose(spawn_agent=spawn)
    entries = list(sm.read_entries())
    assert returned == entries[-1]


def test_written_entry_is_json_serializable(isolated_log):
    """The written entry survives a json.dumps round-trip."""
    import sm
    _seed_iteration()
    spawn = _stub_spawn(_canonical_agent_output())
    e = sm.decompose(spawn_agent=spawn)
    s = json.dumps(e)
    assert json.loads(s) == e


# ===========================================================================
# Typed errors hygiene — exported, distinct, ValueError-subclass
# ===========================================================================


def test_decompose_output_parse_error_in_dunder_all():
    """Public typed error — exported via __all__."""
    import sm
    assert "DecomposeOutputParseError" in sm.__all__, (
        f"DecomposeOutputParseError must be in __all__; got {sm.__all__!r}"
    )


def test_decompose_output_shape_error_in_dunder_all():
    """Public typed error — exported via __all__."""
    import sm
    assert "DecomposeOutputShapeError" in sm.__all__, (
        f"DecomposeOutputShapeError must be in __all__; got {sm.__all__!r}"
    )


def test_parse_and_shape_errors_are_distinct_classes():
    """The two error classes are distinct so callers can branch."""
    import sm
    assert sm.DecomposeOutputParseError is not sm.DecomposeOutputShapeError


def test_parse_error_not_subclass_of_shape_error():
    """Distinct hierarchy — parse and shape are siblings under ValueError."""
    import sm
    assert not issubclass(sm.DecomposeOutputParseError,
                          sm.DecomposeOutputShapeError)
    assert not issubclass(sm.DecomposeOutputShapeError,
                          sm.DecomposeOutputParseError)


# ===========================================================================
# Subprocess CLI surface — `python -m sm decompose`
# ===========================================================================


def test_cli_decompose_command_known(tmp_path):
    """`python -m sm decompose` is a known command (does not exit with the
    'unknown command' status). Without an injected spawn_agent, the default
    raises NotImplementedError, so the CLI must exit non-zero — that's fine.
    What we pin: the command is recognized, not 'unknown'."""
    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")

    result = subprocess.run(
        [sys.executable, "-m", "sm", "decompose"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The CLI must NOT print 'unknown command' — that is the unrecognized
    # subcommand response. decompose IS a known subcommand.
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'decompose' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_decompose_no_active_iteration_exits_nonzero(tmp_path):
    """`python -m sm decompose` with an empty log exits non-zero (no active
    iteration). Pinned: exit code != 0 AND the failure is the expected
    'no active iteration' path (not the 'unknown command' path)."""
    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")

    result = subprocess.run(
        [sys.executable, "-m", "sm", "decompose"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"decompose with no active iteration must exit non-zero;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # Reject the 'unknown command' path — that would mean the CLI didn't
    # recognize 'decompose' at all, which is the wrong failure mode.
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'decompose' and fail with the 'no active "
        f"iteration' error, not 'unknown command';\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
