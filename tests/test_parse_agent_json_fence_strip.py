"""Iter 2 Sprint 2 Story 17 — `parse_agent_json` strips markdown code fences.

Triggered by the Cardiff live smoke run (Story 16) which observed the live
Anthropic SDK return valid JSON wrapped in ` ```json ... ``` ` markdown
fences. The Story 4 `parse_agent_json` helper passes the raw string straight
to `json.loads`, which chokes on the leading backticks and surfaces a
`DecomposeOutputParseError` (decompose path) or a `ReviewerAgentError`
(reviewer path), failing the smoke run mid-decompose.

CONTRACT PINNED BY THIS FILE (verbatim, Sprint_2_Plan.md Story 17):

  1. `parse_agent_json(raw, role)` strips a leading ` ```json ` /
     ` ```JSON ` / ` ``` ` (case-variant, language tag optional)
     and a trailing ` ``` ` from `raw` BEFORE invoking `json.loads`.
  2. Strips leading/trailing whitespace and newlines OUTSIDE the
     fences.
  3. Preserves the Story 4 typed-error contract: a genuine JSON
     syntax error after the fence strip still raises the role's
     typed parse-error class (`DecomposeAgentError` /
     `TestWriterAgentError` / `CoderAgentError` /
     `ReviewerAgentError`).
  4. No regression on fenceless input: `{"k": 1}` parses identically.
  5. Both `decompose` (Story 6) and `reviewer` (Story 9) paths benefit
     automatically; no caller-side changes.

WHAT THIS FILE DOES NOT PIN:

  - The implementation strategy (regex vs `startswith`/`endswith`).
  - Whether fences with NO surrounding newlines (e.g.
    ` ```json {"k": 1} ``` `) are accepted. The smoke run real-world
    output has newlines; aggressive fence-strip is the Coder's call.
    See Category A note below.

Every test below FAILS on first run — current Story 4 implementation
calls `json.loads(raw)` directly with no fence-aware preprocessing.
The Coder adds the strip step to drive this suite green.

ANTI-LANE NOTES FOR THE CODER:
  - Story 4's existing test file (`tests/test_parse_agent_json.py`)
    stays untouched and stays green. Category C below is the
    regression guard.
  - Public surface of `parse_agent_json` does NOT change: signature
    is still `(raw, role)`, return type still `dict | list`,
    role-routing of typed errors stays identical.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# The four canonical agent roles. Each maps to its typed parse-error
# class via the Story 4 contract; Story 17 preserves that mapping.
_ROLES = ("decompose", "test_writer", "coder", "reviewer")

_ROLE_TO_ERROR_NAME = {
    "decompose": "DecomposeAgentError",
    "test_writer": "TestWriterAgentError",
    "coder": "CoderAgentError",
    "reviewer": "ReviewerAgentError",
}


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sm_module():
    """Return a freshly imported `sm` module. Guarantees the test
    observes the current source state rather than a stale cached
    import."""
    if "sm" in sys.modules:
        return importlib.reload(sys.modules["sm"])
    import sm  # noqa: PLC0415
    return sm


def _get_error_class(sm_module, role: str):
    """Look up the typed parse-error class for a given role on the
    sm module. Returns the class; raises AssertionError if missing
    (Story 4 should have wired this already)."""
    name = _ROLE_TO_ERROR_NAME[role]
    cls = getattr(sm_module, name, None)
    assert cls is not None, (
        f"sm.{name} should exist (Story 4 wires it); cannot test "
        f"Story 17 fence-strip without it"
    )
    return cls


# ===========================================================================
# Category A — Fence strip: LEADING variations (6 tests)
#
# Real-world Cardiff smoke output looked like:
#
#     ```json
#     {
#       "stories": [ ... ]
#     }
#     ```
#
# These tests pin that the helper tolerates the four leading-fence
# shapes (lowercase, uppercase, mixed-case, no-language-tag) and
# surrounding whitespace before the fence.
# ===========================================================================


def test_fence_strip_lowercase_json_tag(sm_module):
    """Leading ` ```json\\n ` + trailing ` \\n``` ` strip to a clean
    `{"k": 1}` and parse. This is the EXACT shape the live SDK
    returned during the Cardiff smoke run."""
    raw = '```json\n{"k": 1}\n```'
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == {"k": 1}, (
        f"expected fenced ` ```json\\n{{}}\\n``` ` to parse to "
        f"{{'k': 1}}; got {got!r}"
    )


def test_fence_strip_uppercase_json_tag(sm_module):
    """Uppercase language tag ` ```JSON ` is stripped — markdown
    parsers are case-insensitive on fence language tags, so the model
    occasionally capitalizes."""
    raw = '```JSON\n{"k": 1}\n```'
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == {"k": 1}, (
        f"expected uppercase ` ```JSON ` tag to be stripped; got "
        f"{got!r}"
    )


def test_fence_strip_mixed_case_json_tag(sm_module):
    """Mixed-case ` ```Json ` is stripped — defensive against any
    title-case variant the model might emit."""
    raw = '```Json\n{"k": 1}\n```'
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == {"k": 1}, (
        f"expected mixed-case ` ```Json ` tag to be stripped; got "
        f"{got!r}"
    )


def test_fence_strip_no_language_tag(sm_module):
    """Bare ` ``` ` (no language tag) is also a valid markdown fence
    and must be stripped. Some models omit the language tag entirely."""
    raw = '```\n{"k": 1}\n```'
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == {"k": 1}, (
        f"expected bare ` ``` ` (no language tag) to be stripped; "
        f"got {got!r}"
    )


def test_fence_strip_leading_whitespace_before_fence(sm_module):
    """Leading whitespace/newlines BEFORE the opening fence are
    tolerated. The model occasionally emits a blank line of preamble
    before the fenced block."""
    raw = '  \n\n```json\n{"k": 1}\n```'
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == {"k": 1}, (
        f"expected leading whitespace + fence to strip cleanly; got "
        f"{got!r}"
    )


def test_fence_strip_nested_json_object_preserved(sm_module):
    """The fence strip is purely outer-shell — nested JSON content
    (objects, arrays, escaped strings) round-trips identically. Pin:
    the inner JSON is NOT touched."""
    payload = {
        "stories": [
            {"sequence": 1, "title": "outer", "nested": {"k": [1, 2, 3]}},
            {"sequence": 2, "title": "second"},
        ],
        "meta": {"count": 2},
    }
    raw = "```json\n" + json.dumps(payload) + "\n```"
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == payload, (
        f"nested payload should round-trip through fence strip; got "
        f"{got!r}, expected {payload!r}"
    )


# ===========================================================================
# Category B — Fence strip: TRAILING variations (4 tests)
# ===========================================================================


def test_fence_strip_trailing_whitespace_after_close(sm_module):
    """Trailing whitespace after the closing ` ``` ` is tolerated."""
    raw = '```json\n{"k": 1}\n```   \t  '
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == {"k": 1}, (
        f"expected trailing whitespace after close-fence to be "
        f"stripped; got {got!r}"
    )


def test_fence_strip_trailing_multiple_newlines(sm_module):
    """Multiple trailing newlines after the closing fence are
    tolerated. The model sometimes pads its response with blank
    lines."""
    raw = '```json\n{"k": 1}\n```\n\n\n'
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == {"k": 1}, (
        f"expected multiple trailing newlines to be stripped; got "
        f"{got!r}"
    )


def test_fence_strip_trailing_fence_no_terminating_newline(sm_module):
    """The closing ` ``` ` is the last thing in `raw` (no newline
    after). Pin: the closing fence is still stripped — the regex /
    string scan must not require a trailing newline."""
    raw = '```json\n{"k": 1}\n```'
    assert raw.endswith("```"), "test setup: raw must end with the fence"
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == {"k": 1}, (
        f"expected closing fence stripped even with no terminating "
        f"newline; got {got!r}"
    )


def test_fence_strip_inner_payload_has_trailing_newline(sm_module):
    """The inner JSON itself ends with a newline INSIDE the fences —
    the strip preserves that the inner content is parseable. The
    common shape (per the Cardiff smoke output) is:
       ```json
       {...}
       ```
    where there IS a newline between `}` and ` ``` `; this test pins
    that case."""
    raw = '```json\n{"a": 1, "b": [1, 2, 3]}\n```'
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == {"a": 1, "b": [1, 2, 3]}, (
        f"expected inner payload (with intra-fence newlines) to parse "
        f"cleanly; got {got!r}"
    )


# ===========================================================================
# Category C — NO FENCE: regression guard (5 tests)
#
# The Story 4 contract must NOT regress. Plain JSON (no fences) parses
# identically. Empty string still raises the role's typed error.
# This category is the firewall against the Coder over-stripping in a
# way that breaks the fenceless path.
# ===========================================================================


def test_no_fence_plain_dict_still_parses(sm_module):
    """Plain `{"k": 1}` (no fences) still parses to `{"k": 1}` —
    Story 4 behavior is preserved."""
    got = sm_module.parse_agent_json('{"k": 1}', "decompose")
    assert got == {"k": 1}, (
        f"plain (fenceless) JSON object regressed; got {got!r}"
    )


def test_no_fence_plain_list_still_parses(sm_module):
    """Plain `[1, 2, 3]` (no fences) still parses — list happy path
    is preserved across all roles after fence-strip is added."""
    got = sm_module.parse_agent_json("[1, 2, 3]", "reviewer")
    assert got == [1, 2, 3], (
        f"plain (fenceless) JSON array regressed; got {got!r}"
    )


def test_no_fence_with_outer_whitespace_still_parses(sm_module):
    """Plain JSON with leading/trailing whitespace (no fences) still
    parses. The outer-whitespace strip is independent of the fence
    strip; both are pre-`json.loads`."""
    got = sm_module.parse_agent_json('  \n {"k": 1}  \n  ', "decompose")
    assert got == {"k": 1}, (
        f"plain JSON with outer whitespace regressed; got {got!r}"
    )


def test_no_fence_empty_string_still_raises_typed_error(sm_module):
    """An empty string STILL raises the role's typed parse error
    after the fence strip is added. Empty stripped to empty is still
    malformed JSON — Story 4 contract clause."""
    cls = _get_error_class(sm_module, "decompose")
    with pytest.raises(cls):
        sm_module.parse_agent_json("", "decompose")


def test_no_fence_nested_payload_unchanged(sm_module):
    """A nested fenceless payload parses unchanged. Confirms the
    fence-strip logic does not accidentally mutate inner content."""
    payload = {
        "stories": [
            {"sequence": 1, "title": "a", "nested": [{"x": [1, 2]}]},
        ],
    }
    raw = json.dumps(payload)
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == payload, (
        f"nested fenceless payload regressed; got {got!r}, expected "
        f"{payload!r}"
    )


# ===========================================================================
# Category D — Genuine parse errors still raise typed errors (6 tests)
#
# The Story 4 contract for typed parse errors is preserved AFTER the
# fence strip. If the input is malformed JSON (with or without
# surrounding fences), the role's typed error fires.
# ===========================================================================


@pytest.mark.parametrize("role", _ROLES)
def test_bare_garbage_raises_typed_error_per_role(sm_module, role):
    """`not json at all` is malformed regardless of fence strip —
    each role's typed error fires. Story 4 contract clause preserved."""
    cls = _get_error_class(sm_module, role)
    with pytest.raises(cls):
        sm_module.parse_agent_json("not json at all", role)


def test_fenced_malformed_inner_raises_typed_error_decompose(sm_module):
    """Input is fence-wrapped but the inner content is NOT valid
    JSON. After the fence strip, `json.loads` fails — the decompose
    role's `DecomposeAgentError` fires. Pin: fence strip alone is not
    a license to swallow inner-content parse failures."""
    cls = _get_error_class(sm_module, "decompose")
    raw = "```json\nnot json at all\n```"
    with pytest.raises(cls):
        sm_module.parse_agent_json(raw, "decompose")


def test_fenced_truncated_json_raises_typed_error_reviewer(sm_module):
    """Fenced + truncated JSON (` ```json\\n{"a":\\n``` `) → reviewer
    role's typed error fires after the fence strip. Confirms the
    typed-error mapping survives the new preprocessing step."""
    cls = _get_error_class(sm_module, "reviewer")
    raw = '```json\n{"a":\n```'
    with pytest.raises(cls):
        sm_module.parse_agent_json(raw, "reviewer")


def test_fenced_malformed_error_message_contains_role(sm_module):
    """The typed error from a fenced-but-malformed input still
    embeds the role name in its message. The Story 4 message contract
    (role name + JSONDecodeError message + raw snippet) survives the
    fence-strip preprocessor."""
    cls = _get_error_class(sm_module, "coder")
    raw = "```json\n{invalid: missing quotes}\n```"
    with pytest.raises(cls) as exc_info:
        sm_module.parse_agent_json(raw, "coder")
    msg = str(exc_info.value)
    assert "coder" in msg, (
        f"expected role 'coder' to appear in fenced-malformed error "
        f"message; got msg={msg!r}"
    )


@pytest.mark.parametrize("role", _ROLES)
def test_fenced_malformed_uses_correct_error_class_per_role(
    sm_module, role
):
    """Each of the four roles surfaces its own typed parse-error
    class when the inner content (post-fence-strip) is malformed.
    This is the parametrized version of the per-role check —
    confirms the role→error mapping is preserved end-to-end."""
    cls = _get_error_class(sm_module, role)
    raw = "```json\n{not valid}\n```"
    with pytest.raises(cls):
        sm_module.parse_agent_json(raw, role)


def test_fence_with_only_whitespace_inside_raises_typed_error(sm_module):
    """A fenced block with ONLY whitespace inside is still malformed
    JSON after the fence strip — the role's typed error fires.
    Defensive: ensures the strip doesn't return some sentinel that
    accidentally parses to `None`."""
    cls = _get_error_class(sm_module, "decompose")
    raw = "```json\n   \n```"
    with pytest.raises(cls):
        sm_module.parse_agent_json(raw, "decompose")


# ===========================================================================
# Category E — End-to-end through `decompose` (3 tests)
#
# Story 6 (`decompose`) routes its agent output through
# `parse_agent_json(..., role="decompose")`. Story 17 adds the fence
# strip; these tests pin that benefit transparently — fenced agent
# output now drives a successful decompose, and fenced-but-malformed
# output still raises `DecomposeOutputParseError`.
#
# We inject a `spawn_agent` stub that returns a fence-wrapped string.
# The decompose function calls `parse_agent_json` internally; no
# caller-side change is required.
# ===========================================================================


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file. Mirrors the
    suite convention (see test_decompose.py for the canonical
    fixture)."""
    import sm
    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _seed_active_iteration(iteration_id: str = "iter-fence-1") -> list:
    """Append an `iteration_open` entry so a subsequent `decompose()`
    has an active iteration to work against. Returns the seeded
    requirements."""
    import sm
    requirements = [
        {
            "requirement_id": f"req-{i}",
            "title": f"Title {i}",
            "description": f"Description {i}.",
            "priority": "MUST",
            "acceptance_criteria": f"AC{i}",
        }
        for i in range(1, 4)
    ]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": "Fence-strip end-to-end test",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return requirements


def test_decompose_succeeds_with_fenced_agent_output(
    sm_module, isolated_log, monkeypatch
):
    """End-to-end: `decompose` is invoked with a `spawn_agent` stub
    that returns the EXACT Cardiff smoke output shape — JSON wrapped
    in ` ```json\\n ... \\n``` ` fences. After Story 17 closes,
    `decompose` succeeds and writes a `story_backlog` entry."""
    _seed_active_iteration()

    payload = {
        "stories": [
            {
                "sequence": 1,
                "title": "Fenced story one",
                "size": "S",
                "requirement_ids": ["req-1"],
                "acceptance_criteria": "story one passes its tests",
            },
            {
                "sequence": 2,
                "title": "Fenced story two",
                "size": "M",
                "requirement_ids": ["req-2"],
                "acceptance_criteria": "story two passes its tests",
            },
        ]
    }
    fenced_output = "```json\n" + json.dumps(payload) + "\n```"

    def _stub_spawn(role_spec_path, requirements):
        return fenced_output

    entry = sm_module.decompose(spawn_agent=_stub_spawn)
    assert entry["entry_type"] == "story_backlog", (
        f"expected entry_type='story_backlog'; got "
        f"{entry.get('entry_type')!r}"
    )
    assert len(entry["payload"]["stories"]) == 2, (
        f"expected 2 stories landed on the entry; got "
        f"{len(entry['payload']['stories'])}"
    )


def test_decompose_raises_on_fenced_malformed_agent_output(
    sm_module, isolated_log
):
    """End-to-end: fenced-but-malformed agent output → `decompose`
    raises `DecomposeOutputParseError` (the Story 4 / Story 6 typed
    error). The fence strip does NOT mask genuine parse failures."""
    _seed_active_iteration()

    fenced_garbage = "```json\nnot valid json at all\n```"

    def _stub_spawn(role_spec_path, requirements):
        return fenced_garbage

    # `DecomposeOutputParseError` is a subclass of `DecomposeAgentError`
    # (which subclasses `ValueError`). Either typed class catches it;
    # pin the narrower one to confirm the Story 6 wrapping still fires.
    err_cls = getattr(sm_module, "DecomposeOutputParseError", None)
    assert err_cls is not None, (
        "sm.DecomposeOutputParseError should exist (Story 4 / Story 6 "
        "wires it); cannot test fenced-malformed end-to-end without it"
    )
    with pytest.raises(err_cls):
        sm_module.decompose(spawn_agent=_stub_spawn)


def test_decompose_succeeds_with_fenced_output_no_language_tag(
    sm_module, isolated_log
):
    """End-to-end: bare ` ``` ` fences (no language tag) around valid
    decompose output also drive a successful decompose. Confirms the
    Story 17 fence-strip robustness extends to fenceless-tag variants
    through the real caller."""
    _seed_active_iteration()

    payload = {
        "stories": [
            {
                "sequence": 1,
                "title": "Bare-fence story",
                "size": "L",
                "requirement_ids": ["req-3"],
                "acceptance_criteria": "passes its tests",
            },
        ]
    }
    fenced_output = "```\n" + json.dumps(payload) + "\n```"

    def _stub_spawn(role_spec_path, requirements):
        return fenced_output

    entry = sm_module.decompose(spawn_agent=_stub_spawn)
    assert entry["entry_type"] == "story_backlog", (
        f"expected entry_type='story_backlog' from bare-fence output; "
        f"got {entry.get('entry_type')!r}"
    )
    assert len(entry["payload"]["stories"]) == 1, (
        f"expected 1 story landed; got "
        f"{len(entry['payload']['stories'])}"
    )
