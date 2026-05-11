"""Iter 2 Story 4 — JSON ask-and-parse helper with typed parse errors.

This file pins the contract of `sm.parse_agent_json(raw, role)`, the
four per-role typed parse errors
(`DecomposeAgentError`, `TestWriterAgentError`, `CoderAgentError`,
`ReviewerAgentError`), and the single-source-of-truth invariant that
`json.loads` of agent-response text happens in exactly one place
(this helper) across the four spawn defaults.

Pinned clauses (verbatim from `iter2/Stories_v1.md`, Story 4):

  1. Exposes `parse_agent_json(raw: str, role: str) -> dict | list`
     that runs `json.loads` on the agent response text and returns
     the parsed object.
  2. On `json.JSONDecodeError`, raises a typed parse error specific
     to the calling role: `DecomposeAgentError` for decompose (also
     closes retro item 1 per ASSUMPTION 8) and an appropriate typed
     error (e.g., `ReviewerAgentError` or shared parse-error class)
     for reviewer.
  3. Typed parse errors carry the raw response snippet (truncated)
     and the role for operator debugging.
  4. The CLI dispatcher maps every typed agent error to exit code
     `12` (`EXIT_AGENT_ERROR`).
  5. A grep across the four spawn defaults finds exactly one call
     site invoking `json.loads` directly — this helper — and zero
     ad-hoc parse-and-raise blocks at spawn sites.

CONTRACT INTERPRETATION (locked by TestWriter):

  - Public surface: `parse_agent_json` listed in `sm.__all__`.
  - Per-role typed parse errors (each subclass `ValueError` so
    existing `except ValueError` handlers keep working):
      role="decompose"    -> `DecomposeAgentError`
      role="test_writer"  -> `TestWriterAgentError`
      role="coder"        -> `CoderAgentError`
      role="reviewer"     -> `ReviewerAgentError`
    All four classes public and in `sm.__all__`. Story 4 wires
    `DecomposeAgentError` (Iter 1 Story 9 declared but unused) and
    adds the three new classes; Coder rebases `DecomposeAgentError`
    from `RuntimeError` to `ValueError` so the pattern is uniform.
  - Error message format: contains the role name, the original
    `json.JSONDecodeError` message, AND a truncated snippet of the
    raw response (first 200 chars). Operator can diagnose without a
    traceback.
  - Truncation length: 200 characters. Raw longer than 200 chars is
    truncated; raw at or under 200 chars is preserved verbatim.
  - Invalid `role` argument (not one of the four canonical names)
    raises `ValueError`, message lists the valid role set. Empty
    string is invalid.
  - Non-string `raw` argument raises `TypeError` (json.loads itself
    would raise TypeError on non-str/bytes; we surface that
    contract explicitly — `parse_agent_json` is a string helper).
  - Empty-string `raw` is treated as malformed JSON (json.loads
    raises JSONDecodeError on `""`) → the role's typed parse error.
  - Whitespace-only `raw` is treated as malformed JSON → the role's
    typed parse error.
  - Single-source-of-truth grep: `sm.py` has at most TWO `json.loads`
    call sites — the helper (this one) and the Iter 1 Story 5
    `ingest` function (parses the handoff JSON file, NOT agent
    output). No third site, and no ad-hoc parse-and-raise at any
    spawn-default call site.

Every test below FAILS on first run — `parse_agent_json`,
`TestWriterAgentError`, `CoderAgentError`, and `ReviewerAgentError`
do not exist yet, and `DecomposeAgentError` is not in `__all__`.
The Coder implements them to drive this suite green.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pathlib
import re
import sys

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# The four canonical agent roles. The helper accepts any of these
# strings for the `role` argument and maps each to its typed parse
# error class. Iteration order matters for parametrized tests; keep
# stable.
_ROLES = ("decompose", "test_writer", "coder", "reviewer")

# Map from role -> typed-error attribute name on the sm module. Used
# by every per-role parametrized test. The Coder must register each
# class on the module with these exact names.
_ROLE_TO_ERROR_NAME = {
    "decompose": "DecomposeAgentError",
    "test_writer": "TestWriterAgentError",
    "coder": "CoderAgentError",
    "reviewer": "ReviewerAgentError",
}

# Truncation length for the raw snippet embedded in error messages.
# Pinned at 200 per the contract interpretation; the Coder must use
# this exact value so the assertions in Category F are stable.
_SNIPPET_LIMIT = 200


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
    import sm  # noqa: PLC0415 — fixture imports lazily
    return sm


def _read_sm_source() -> str:
    """Return sm.py as text. Used by the static grep tests."""
    return SM_PATH.read_text(encoding="utf-8")


def _get_error_class(sm_module, role: str):
    """Look up the typed parse-error class for a given role on the
    sm module. Returns the class if present, else None — caller
    asserts presence with a helpful failure message."""
    name = _ROLE_TO_ERROR_NAME[role]
    return getattr(sm_module, name, None)


# ===========================================================================
# Category A — Smoke (6 tests)
#
# `parse_agent_json` exists on the module, is callable, public (no
# leading underscore), listed in `sm.__all__`, and its signature
# accepts (raw, role) as positional parameters.
# ===========================================================================


def test_parse_agent_json_exists_on_module(sm_module):
    """`sm.parse_agent_json` is defined at module scope."""
    assert hasattr(sm_module, "parse_agent_json"), (
        "expected `parse_agent_json` to be defined on the sm module; "
        f"missing from dir(sm)={sorted(n for n in dir(sm_module) if not n.startswith('_'))!r}"
    )


def test_parse_agent_json_is_callable(sm_module):
    """`sm.parse_agent_json` is callable (function or callable
    object)."""
    obj = getattr(sm_module, "parse_agent_json", None)
    assert callable(obj), (
        f"expected `sm.parse_agent_json` to be callable; got "
        f"{type(obj).__name__}"
    )


def test_parse_agent_json_is_public_name(sm_module):
    """The helper is named `parse_agent_json` (no leading
    underscore). Public per the contract — shared parse path for
    decompose and reviewer."""
    assert hasattr(sm_module, "parse_agent_json"), (
        "expected the public name `parse_agent_json`, not a private "
        "`_parse_agent_json`"
    )


def test_parse_agent_json_in_all(sm_module):
    """`parse_agent_json` is listed in `sm.__all__` so wildcard
    imports pick it up and the public surface is documented in one
    place."""
    assert "parse_agent_json" in sm_module.__all__, (
        f"`parse_agent_json` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_parse_agent_json_signature_accepts_raw_and_role(sm_module):
    """`parse_agent_json` accepts `raw` and `role` positional
    parameters. The signature must support
    `parse_agent_json("...json...", "decompose")` as the documented
    call form."""
    sig = inspect.signature(sm_module.parse_agent_json)
    params = list(sig.parameters.values())
    assert len(params) >= 2, (
        f"parse_agent_json must accept at least two parameters "
        f"(raw, role); got signature {sig!s}"
    )
    raw_p, role_p = params[0], params[1]
    for p in (raw_p, role_p):
        assert p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ), (
            f"parameter {p.name!r} of parse_agent_json must be "
            f"positional; got {p.kind!s}"
        )


def test_parse_agent_json_signature_param_names(sm_module):
    """First two parameter names are `raw` and `role` so call-site
    keyword usage works (e.g. `parse_agent_json(raw=..., role=...)`).
    The acceptance criterion documents the names verbatim."""
    sig = inspect.signature(sm_module.parse_agent_json)
    names = [p.name for p in sig.parameters.values()]
    assert names[:2] == ["raw", "role"], (
        f"first two parameters must be (raw, role); got {names!r}"
    )


# ===========================================================================
# Category B — Typed errors exist (12 tests)
#
# Each of the four typed parse-error classes:
#   - is defined on the sm module
#   - is listed in `sm.__all__`
#   - subclasses `ValueError` so existing `except ValueError` paths
#     still catch the error
# ===========================================================================


@pytest.mark.parametrize("role", _ROLES)
def test_typed_error_class_exists(sm_module, role):
    """Each role's typed parse-error class is defined on the sm
    module (DecomposeAgentError, TestWriterAgentError, CoderAgentError,
    ReviewerAgentError)."""
    name = _ROLE_TO_ERROR_NAME[role]
    assert hasattr(sm_module, name), (
        f"expected `{name}` to be defined on the sm module for "
        f"role={role!r}; missing from public surface"
    )


@pytest.mark.parametrize("role", _ROLES)
def test_typed_error_class_in_all(sm_module, role):
    """Each typed parse-error class is listed in `sm.__all__` — the
    public surface is documented in one place. Notably,
    `DecomposeAgentError` was declared in Iter 1 Story 9 but never
    added to `__all__`; Story 4 closes that gap."""
    name = _ROLE_TO_ERROR_NAME[role]
    assert name in sm_module.__all__, (
        f"`{name}` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


@pytest.mark.parametrize("role", _ROLES)
def test_typed_error_class_subclasses_valueerror(sm_module, role):
    """Each typed parse-error class inherits from `ValueError` so
    existing `except ValueError` handlers in the codebase still
    catch it. `DecomposeAgentError` (declared in Iter 1 Story 9 as
    RuntimeError) is rebased to ValueError so the pattern is uniform
    across all four roles."""
    cls = _get_error_class(sm_module, role)
    assert cls is not None, (
        f"typed parse-error class for role={role!r} not defined; "
        "test_typed_error_class_exists should have caught this"
    )
    assert issubclass(cls, ValueError), (
        f"{cls.__name__} must subclass ValueError (was RuntimeError "
        f"in Iter 1 for DecomposeAgentError; Story 4 rebases). "
        f"Current mro={cls.__mro__!r}"
    )


# ===========================================================================
# Category C — Happy path: dict (4 tests, one per role)
#
# A valid JSON object string parses to a dict for every role.
# `parse_agent_json` returns the dict; the role argument does NOT
# affect happy-path parsing (it only routes the error class on
# failure).
# ===========================================================================


@pytest.mark.parametrize("role", _ROLES)
def test_happy_path_returns_dict_for_each_role(sm_module, role):
    """A well-formed JSON object string returns the parsed dict for
    each role. The role argument is irrelevant on the happy path —
    only the error class on failure depends on role."""
    payload = {"stories": [{"sequence": 1, "title": "x"}], "ok": True}
    raw = json.dumps(payload)
    got = sm_module.parse_agent_json(raw, role)
    assert isinstance(got, dict), (
        f"expected dict return for valid JSON object; got "
        f"{type(got).__name__} for role={role!r}"
    )
    assert got == payload, (
        f"parsed dict must equal the original payload; got "
        f"{got!r} for role={role!r}"
    )


def test_happy_path_returns_dict_with_nested_structure(sm_module):
    """Nested JSON objects round-trip correctly. The helper is a
    thin wrapper around `json.loads`; it inherits json's full nested
    handling."""
    payload = {
        "outer": {"inner": {"deep": [1, 2, {"k": "v"}]}},
        "meta": {"n": 7},
    }
    raw = json.dumps(payload)
    got = sm_module.parse_agent_json(raw, "decompose")
    assert got == payload, (
        f"nested dict round-trip failed; got {got!r}"
    )


# ===========================================================================
# Category D — Happy path: list (4 tests, one per role)
#
# A valid JSON array string parses to a list for every role. The
# return-type annotation is `dict | list`; both are honored.
# ===========================================================================


@pytest.mark.parametrize("role", _ROLES)
def test_happy_path_returns_list_for_each_role(sm_module, role):
    """A well-formed JSON array string returns the parsed list for
    each role. The signature is `-> dict | list`; both are valid
    top-level returns."""
    payload = [1, 2, {"x": "y"}, [3, 4]]
    raw = json.dumps(payload)
    got = sm_module.parse_agent_json(raw, role)
    assert isinstance(got, list), (
        f"expected list return for valid JSON array; got "
        f"{type(got).__name__} for role={role!r}"
    )
    assert got == payload, (
        f"parsed list must equal the original payload; got "
        f"{got!r} for role={role!r}"
    )


def test_happy_path_returns_empty_list_ok(sm_module):
    """An empty JSON array (`[]`) is valid JSON and parses to `[]`.
    No special-case rejection — that is the caller's domain check."""
    got = sm_module.parse_agent_json("[]", "decompose")
    assert got == [], f"empty list round-trip failed; got {got!r}"


def test_happy_path_returns_empty_dict_ok(sm_module):
    """An empty JSON object (`{}`) is valid JSON and parses to `{}`.
    Same reasoning as empty list — domain rejection is upstream."""
    got = sm_module.parse_agent_json("{}", "reviewer")
    assert got == {}, f"empty dict round-trip failed; got {got!r}"


# ===========================================================================
# Category E — Malformed JSON → typed error per role (8 tests)
#
# For each of the four canonical roles, malformed JSON raises the
# correct typed parse-error class. The class identity is per-role:
#   decompose    -> DecomposeAgentError
#   test_writer  -> TestWriterAgentError
#   coder        -> CoderAgentError
#   reviewer     -> ReviewerAgentError
# Two malformed inputs per role exercise both unterminated-string
# and bare-token failure modes.
# ===========================================================================


@pytest.mark.parametrize("role", _ROLES)
def test_malformed_json_raises_role_typed_error(sm_module, role):
    """Malformed JSON ('{not valid json}') raises the role's typed
    parse-error class. A bare token / unquoted key is a canonical
    `json.JSONDecodeError` trigger."""
    cls = _get_error_class(sm_module, role)
    assert cls is not None, (
        f"typed parse-error class for role={role!r} not defined"
    )
    with pytest.raises(cls):
        sm_module.parse_agent_json("{not valid json}", role)


@pytest.mark.parametrize("role", _ROLES)
def test_truncated_json_raises_role_typed_error(sm_module, role):
    """A truncated JSON object (`{"a":`) raises the role's typed
    parse-error class — confirms the helper raises the correct class
    for an entirely different malformed-JSON shape than the bare
    token case."""
    cls = _get_error_class(sm_module, role)
    assert cls is not None, (
        f"typed parse-error class for role={role!r} not defined"
    )
    with pytest.raises(cls):
        sm_module.parse_agent_json('{"a":', role)


# ===========================================================================
# Category F — Error message content (6 tests)
#
# Typed parse-error messages must carry:
#   - the role name (so the operator knows which spawn failed),
#   - a raw snippet (so the operator can eyeball the agent output),
#   - and the snippet is truncated to at most _SNIPPET_LIMIT chars.
# A raw input longer than _SNIPPET_LIMIT triggers truncation; a
# shorter raw input is preserved as-is.
# ===========================================================================


@pytest.mark.parametrize("role", _ROLES)
def test_error_message_contains_role_name(sm_module, role):
    """The typed parse-error message contains the role name verbatim
    so an operator scanning a log line can attribute the failure to
    the correct spawn."""
    cls = _get_error_class(sm_module, role)
    assert cls is not None, (
        f"typed parse-error class for role={role!r} not defined"
    )
    with pytest.raises(cls) as exc_info:
        sm_module.parse_agent_json("not-json", role)
    msg = str(exc_info.value)
    assert role in msg, (
        f"expected role={role!r} in error message; got msg={msg!r}"
    )


def test_error_message_contains_raw_snippet_short(sm_module):
    """A short malformed raw input is included in the error message
    (no truncation). The operator sees the actual agent output that
    failed to parse."""
    raw = "definitely not json {"
    cls = _get_error_class(sm_module, "decompose")
    with pytest.raises(cls) as exc_info:
        sm_module.parse_agent_json(raw, "decompose")
    msg = str(exc_info.value)
    assert raw in msg or raw[:_SNIPPET_LIMIT] in msg, (
        f"expected raw snippet {raw!r} in error message; got "
        f"msg={msg!r}"
    )


def test_error_message_truncates_long_raw(sm_module):
    """A raw input longer than 200 chars is truncated in the error
    message — the operator sees a snippet, not the full payload.
    Pin: the message length is bounded; the full raw is NOT echoed
    back when it exceeds the snippet limit."""
    long_raw = "x" * 1000 + " definitely not valid json"
    cls = _get_error_class(sm_module, "reviewer")
    with pytest.raises(cls) as exc_info:
        sm_module.parse_agent_json(long_raw, "reviewer")
    msg = str(exc_info.value)
    # Heuristic: the message must NOT contain the entire 1000-char
    # run of 'x'. The exact truncation marker (e.g. '…' or '...') is
    # the Coder's call; we pin the bound on length.
    assert "x" * 1000 not in msg, (
        f"expected raw snippet truncated; got full 1000-char run in "
        f"message of length {len(msg)}"
    )


def test_error_message_truncation_preserves_prefix(sm_module):
    """When the raw is truncated, the FIRST 200 chars are what gets
    embedded — the operator sees the start of the agent output,
    which is the most informative slice for diagnosing a malformed
    response (e.g. an unescaped header that broke parsing)."""
    prefix = "PREFIX_VISIBLE_" + ("z" * (_SNIPPET_LIMIT - 20))
    long_raw = prefix + " then garbage that should be cut off " * 50
    cls = _get_error_class(sm_module, "test_writer")
    with pytest.raises(cls) as exc_info:
        sm_module.parse_agent_json(long_raw, "test_writer")
    msg = str(exc_info.value)
    assert "PREFIX_VISIBLE_" in msg, (
        f"expected the prefix of the raw input to survive "
        f"truncation; got msg={msg!r}"
    )


def test_error_message_truncation_does_not_include_suffix(sm_module):
    """The trailing portion of a long raw input is NOT in the error
    message — confirms the truncation actually drops the tail. We
    embed a sentinel beyond the 200-char prefix and assert it does
    not surface in the message."""
    prefix = "head" + ("y" * (_SNIPPET_LIMIT * 2))
    sentinel = "TAIL_SENTINEL_SHOULD_BE_DROPPED"
    long_raw = prefix + sentinel
    cls = _get_error_class(sm_module, "coder")
    with pytest.raises(cls) as exc_info:
        sm_module.parse_agent_json(long_raw, "coder")
    msg = str(exc_info.value)
    assert sentinel not in msg, (
        f"sentinel {sentinel!r} should have been truncated out of "
        f"the error message; got msg={msg!r}"
    )


def test_error_message_under_limit_preserved_fully(sm_module):
    """A raw input at or under the snippet limit is embedded
    verbatim — no truncation marker, no partial slice. The 200-char
    boundary is exact."""
    raw = "a" * (_SNIPPET_LIMIT - 5) + "}}{{"  # ends in malformed JSON, 199 chars
    assert len(raw) <= _SNIPPET_LIMIT, "test setup: raw must be <= limit"
    cls = _get_error_class(sm_module, "decompose")
    with pytest.raises(cls) as exc_info:
        sm_module.parse_agent_json(raw, "decompose")
    msg = str(exc_info.value)
    # The full raw should appear in the message (since it does not
    # exceed the limit). At minimum a substantial prefix must.
    assert raw[:_SNIPPET_LIMIT - 10] in msg, (
        f"expected short raw (len={len(raw)}) preserved in message; "
        f"got msg={msg!r}"
    )


# ===========================================================================
# Category G — Empty raw input (4 tests, one per role)
#
# An empty string is not valid JSON (`json.loads("")` raises
# `JSONDecodeError`). The helper surfaces this as the role's typed
# parse error.
# ===========================================================================


@pytest.mark.parametrize("role", _ROLES)
def test_empty_raw_raises_role_typed_error(sm_module, role):
    """An empty raw string raises the role's typed parse-error
    class. `json.loads("")` is malformed JSON; the helper treats it
    no differently than any other parse failure."""
    cls = _get_error_class(sm_module, role)
    assert cls is not None, (
        f"typed parse-error class for role={role!r} not defined"
    )
    with pytest.raises(cls):
        sm_module.parse_agent_json("", role)


# ===========================================================================
# Category H — Whitespace-only raw input (2 tests)
#
# A whitespace-only string is malformed JSON (`json.loads("   ")`
# raises `JSONDecodeError`). The helper raises the role's typed
# parse error.
# ===========================================================================


def test_whitespace_only_raw_raises_typed_error_decompose(sm_module):
    """A raw of spaces only raises `DecomposeAgentError` for the
    decompose role. JSON does not tolerate whitespace-only input as
    a valid document."""
    cls = _get_error_class(sm_module, "decompose")
    with pytest.raises(cls):
        sm_module.parse_agent_json("   \t\n  ", "decompose")


def test_whitespace_only_raw_raises_typed_error_reviewer(sm_module):
    """Same for the reviewer role — the routing-to-typed-error path
    is symmetric across the four roles. We pin two roles (rather
    than parametrize over all four) to keep the category small and
    catch a per-role wiring drift if it occurs."""
    cls = _get_error_class(sm_module, "reviewer")
    with pytest.raises(cls):
        sm_module.parse_agent_json("\n  \t  \r\n", "reviewer")


# ===========================================================================
# Category I — Non-string raw input (4 tests)
#
# `parse_agent_json` is documented as a string helper (signature
# `raw: str`). Non-string inputs raise `TypeError` — either because
# the helper guards explicitly, or because `json.loads` itself
# raises TypeError on non-str/bytes. Either is acceptable; the test
# pins `TypeError` as the surfaced class.
# ===========================================================================


@pytest.mark.parametrize(
    "bad_raw",
    [
        None,
        123,
        [1, 2, 3],
        {"a": "b"},
    ],
    ids=["None", "int", "list", "dict"],
)
def test_non_string_raw_raises_typeerror(sm_module, bad_raw):
    """Non-string `raw` argument raises `TypeError`. Covers None,
    int, list, and dict — the four common wrong types an injected
    callable might return by mistake. The role argument is valid;
    only the raw type is wrong."""
    with pytest.raises(TypeError):
        sm_module.parse_agent_json(bad_raw, "decompose")


# ===========================================================================
# Category J — Invalid role argument (4 tests)
#
# An unknown role string raises `ValueError`. The error message
# lists the valid roles so the caller can self-correct. Empty
# string and non-string role values also raise (ValueError or
# TypeError respectively).
# ===========================================================================


def test_unknown_role_raises_valueerror_listing_valid_roles(sm_module):
    """An unknown role argument (e.g. `"sm_agent"` — close to a
    canonical name but NOT one of the four spawn roles) raises
    `ValueError`. The message names the four valid roles so the
    caller can fix the typo."""
    with pytest.raises(ValueError) as exc_info:
        sm_module.parse_agent_json('{"ok": 1}', "sm_agent")
    msg = str(exc_info.value)
    # The message must enumerate (at least mention) the valid roles.
    # We pin presence of at least three of the four canonical names
    # in the message so the operator gets actionable diagnostics.
    matched = sum(1 for r in _ROLES if r in msg)
    assert matched >= 3, (
        f"expected unknown-role error message to enumerate valid "
        f"roles; only {matched}/{len(_ROLES)} role names found in "
        f"msg={msg!r}"
    )


def test_empty_role_raises_valueerror(sm_module):
    """An empty-string role argument raises `ValueError`. Empty is
    not one of the four canonical roles and must not silently
    fall through to a default."""
    with pytest.raises(ValueError):
        sm_module.parse_agent_json('{"ok": 1}', "")


def test_garbage_role_raises_valueerror(sm_module):
    """A garbage role string (e.g. `"reviewerX"`) raises
    `ValueError`. Pin the no-prefix-match invariant: the role
    argument is matched exactly, not as a prefix."""
    with pytest.raises(ValueError):
        sm_module.parse_agent_json('{"ok": 1}', "reviewerX")


@pytest.mark.parametrize(
    "bad_role",
    [None, 0, ["decompose"]],
    ids=["None", "int", "list"],
)
def test_non_string_role_raises(sm_module, bad_role):
    """A non-string role argument raises (either `TypeError` or
    `ValueError`). The exact class is the Coder's call — both
    surface the contract violation cleanly. The pin: `parse_agent_json`
    does NOT proceed to `json.loads` and return a value when the role
    type is wrong."""
    with pytest.raises((TypeError, ValueError)):
        sm_module.parse_agent_json('{"ok": 1}', bad_role)


# ===========================================================================
# Category K — Single-source-of-truth grep (3 tests)
#
# Across `sm.py`, `json.loads` appears at exactly the documented
# call sites:
#   - `read_entries` (Iter 1 Story 4 — parses log file lines, NOT
#     agent output; pre-dates this story)
#   - `ingest` (Iter 1 Story 5 — parses the handoff JSON file, NOT
#     agent output)
#   - `parse_agent_json` (this story — parses agent response text)
# No ad-hoc parse-and-raise blocks at spawn-default call sites.
# ===========================================================================


def test_json_loads_call_site_count_bounded(sm_module):
    """`sm.py` contains at most THREE `json.loads` call sites: the
    log-line parse (Iter 1 Story 4 `read_entries`), the handoff
    parse (Iter 1 Story 5 `ingest`), and the agent-response parse
    (Story 4 `parse_agent_json`). A fourth `json.loads` would
    indicate a spawn-default skipped the helper — flag it."""
    text = _read_sm_source()
    pattern = re.compile(r"\bjson\.loads\s*\(")
    hits = pattern.findall(text)
    assert len(hits) <= 3, (
        f"expected at most 3 `json.loads` call sites in sm.py "
        f"(log read_entries, ingest handoff, parse_agent_json); "
        f"found {len(hits)} hits — a spawn-default likely skipped "
        f"the parse helper"
    )


def test_parse_agent_json_is_a_json_loads_site(sm_module):
    """Confirms `parse_agent_json` itself contains a `json.loads`
    call — it is the documented agent-response parse site. If this
    test fails, either the helper was renamed or `json.loads` was
    moved out of it (in which case the bounded-count test above
    needs to be re-examined)."""
    src = inspect.getsource(sm_module.parse_agent_json)
    assert "json.loads" in src, (
        "expected `parse_agent_json` to invoke `json.loads`; the "
        "helper IS the agent-response parse site. Got source "
        f"starting with: {src[:200]!r}"
    )


def test_no_ad_hoc_parse_and_raise_blocks_outside_helper():
    """`sm.py` has no second `json.loads` followed by a
    `JSONDecodeError`-catching block outside `parse_agent_json` for
    agent-response parsing. The Iter 1 `read_entries` and `ingest`
    sites are NOT agent-response parsers — they parse log lines and
    a handoff file. This regex captures the spawn-default pattern:
    `json.loads(...)` followed quickly by `except ... JSONDecodeError`
    in the same function body. If three or more such blocks appear,
    a spawn default is doing its own parse-and-raise instead of
    routing through the helper."""
    text = _read_sm_source()
    # Crude: find all `except ... JSONDecodeError` clauses and bound
    # them. read_entries and ingest each contribute one. Anything
    # over 3 (helper + read_entries + ingest) is a spawn-default
    # ad-hoc block.
    pattern = re.compile(r"except\s+(?:[\w.,\s|()]+)?json\.JSONDecodeError")
    hits = pattern.findall(text)
    assert len(hits) <= 3, (
        f"expected at most 3 `except json.JSONDecodeError` blocks "
        f"in sm.py (read_entries log parse, ingest handoff parse, "
        f"parse_agent_json agent parse); found {len(hits)} — a "
        f"spawn-default is doing ad-hoc parse-and-raise"
    )


# ===========================================================================
# Category L — Round-trip via json.dumps then parse_agent_json (3 tests)
#
# For correctness, dumping a Python object then parsing it back
# through `parse_agent_json` returns an equal object. This is a
# transitive property of `json.dumps` + `json.loads`, but pinning it
# guards against any unexpected wrapping/unwrapping the helper might
# accidentally introduce.
# ===========================================================================


def test_round_trip_dict_preserves_payload(sm_module):
    """`parse_agent_json(json.dumps(d), role) == d` for a dict
    payload. The helper is transparent on the happy path."""
    payload = {
        "stories": [
            {"sequence": 1, "title": "story one", "size": "S"},
            {"sequence": 2, "title": "story two", "size": "M"},
        ],
        "iteration_id": "iter-0001",
    }
    got = sm_module.parse_agent_json(json.dumps(payload), "decompose")
    assert got == payload, (
        f"round-trip dict mismatch; got {got!r}, expected {payload!r}"
    )


def test_round_trip_list_preserves_payload(sm_module):
    """`parse_agent_json(json.dumps(lst), role) == lst` for a list
    payload. Same transparency invariant, list flavor."""
    payload = [
        {"id": "a", "n": 1},
        {"id": "b", "n": 2},
        [1, 2, 3],
        "string",
        42,
        None,
        True,
        False,
    ]
    got = sm_module.parse_agent_json(json.dumps(payload), "reviewer")
    assert got == payload, (
        f"round-trip list mismatch; got {got!r}, expected {payload!r}"
    )


def test_round_trip_unicode_preserves_payload(sm_module):
    """Non-ASCII payloads round-trip cleanly. The helper does not
    impose an encoding constraint beyond what `json.loads` does."""
    payload = {
        "greeting": "héllo wörld",
        "emoji_ok": "no decoration, just unicode: αβγ Δ",
        "nested": {"k": "Mycen"},
    }
    got = sm_module.parse_agent_json(json.dumps(payload), "coder")
    assert got == payload, (
        f"round-trip unicode mismatch; got {got!r}, expected "
        f"{payload!r}"
    )
