"""Iter 2 Story 7 — Real `spawn_test_writer` default in `execute`.

This file pins the contract of `execute`'s real (non-injected) TestWriter
spawn default — the SECOND linchpin of Iter 2 (after Story 6 wired
`decompose`). Stories 1-5 wired the pieces (anthropic dep, resolve_api_key,
resolve_model, resolve_max_tokens, parse_agent_json, _invoke_anthropic
provider seam); Story 6 wired them through `decompose`; Story 7 wires them
through `execute`'s `spawn_test_writer` — and ONLY `spawn_test_writer`.
`spawn_coder` and `spawn_reviewer` stay `None`-defaults-to-
NotImplementedError until Stories 8 and 9.

Pinned clauses (Story 7 acceptance, paraphrased from Stories_v1.md):

  1. Replaces the `NotImplementedError` default for `spawn_test_writer`
     in `execute` with a real implementation matching the existing
     injectable-callable signature `(role_spec_path: str, story: dict) ->
     str` exactly — no signature drift, no downstream ripple.
  2. The real default reads `roles/test_writer.md` via Story 8 Iter 1's
     `resolve_role_spec` (the caller passes the resolved path; the
     default reads it), packages it plus the active story's dict (as
     JSON) into a single user message, calls the provider seam (Story 5)
     with `resolve_model("test_writer")` and
     `resolve_max_tokens("test_writer")`, then RETURNS THE RAW TEXT
     UNMODIFIED — TestWriter returns code, not JSON, so the response
     does NOT route through `parse_agent_json`.
  3. On SDK-level failure (network, auth, rate-limit, generic Exception
     that is NOT MissingAPIKeyError / ConfigError / TestWriterAgentError),
     the exception wraps as `TestWriterAgentError` with the original
     chained via `__cause__`. `MissingAPIKeyError` and `ConfigError`
     propagate UNCHANGED so the CLI maps `MissingAPIKeyError` to exit 12
     and config errors are diagnosable.
  4. No auto-retry — exactly one SDK call per `execute` invocation.
  5. End-to-end (with mocked SDK): `execute <story_id>` against an active
     iteration with a story_backlog and a cut sprint, with NO injected
     `spawn_test_writer` callable (and operator-injected `spawn_coder`
     and `spawn_reviewer` because those stay None-defaults to
     NotImplementedError until Stories 8 and 9), produces test code as a
     string that flows to the coder stage. Stories 8 and 9 are NOT
     pre-tested here — coder and reviewer must remain None-defaults to
     NotImplementedError under Story 7.

CONTRACT INTERPRETATION (locked by TestWriter):

  - PRIVATE name: the real default is `_default_execute_test_writer_spawn`
    at module scope on `sm`. NOT in `sm.__all__`. The four spawn defaults
    are internal implementation; only their wired-up signatures are
    public surface.
  - Signature is `_default_execute_test_writer_spawn(role_spec_path: str,
    story: dict) -> str` — exact match with the existing injectable-
    callable signature pinned by Iter 1 Story 23.
  - The default reads `role_spec_path` content from disk (the caller in
    `execute` already calls `resolve_role_spec("test_writer")` and passes
    the path; the default reads it). Mirrors Story 6's shape.
  - Message shape: a single user-turn message whose `content` is a
    string that contains BOTH the role-spec text and the story dict (as
    JSON) and an instruction to return test code. Exact framing is the
    Coder's call; tests verify both pieces appear in the message content.
  - Model/max_tokens are read at call time via `resolve_model("test_writer")`
    and `resolve_max_tokens("test_writer")` — so env-var overrides
    (`SM_TEST_WRITER_MODEL`, `SM_TEST_WRITER_MAX_TOKENS`) are honored on
    every call. No caching.
  - API key is read via `resolve_api_key()` — so a missing key raises
    `MissingAPIKeyError` before any SDK work.
  - Provider-seam invocation: the default calls `_invoke_anthropic(
    messages=..., model=..., max_tokens=..., api_key=...)`. Anthropic
    SDK is NOT imported by the default itself — only by the seam.
  - Return value: the default returns the SDK seam's response string
    AS-IS. The caller (`execute`) writes a `testwriter_output` entry
    with the raw `output` field set to the returned string. No
    `parse_agent_json` call — TestWriter returns code.
  - SDK exception wrapping: when `_invoke_anthropic` raises (network /
    auth / rate-limit / generic Exception that is NOT a
    `MissingAPIKeyError` / `ConfigError` and NOT already a
    `TestWriterAgentError`), the default wraps it as a
    `TestWriterAgentError` with the original chained via `__cause__`.
    `MissingAPIKeyError` and `ConfigError` propagate unchanged.
  - No auto-retry: one SDK call per `execute` invocation.
  - Caller-bind contract: `execute` falls back to the real default by
    looking up `_default_execute_test_writer_spawn` on
    `sys.modules[__name__]` so monkeypatches in tests
    (`monkeypatch.setattr(sm, "_default_execute_test_writer_spawn",
    ...)`) take effect. Mirrors Story 6's pattern.
  - Injectable callable preserved: `execute(spawn_test_writer=callable,
    spawn_coder=..., spawn_reviewer=...)` continues to bypass the real
    default entirely. No regression on Iter 1 Story 23's injectable
    contract.

CRITICAL — tests must NOT make real API calls. Every test that triggers
the default path injects a fake `anthropic` module into `sys.modules`
via `monkeypatch.setitem` BEFORE the call. The lazy import inside
`_invoke_anthropic` finds the fake and never touches the real SDK.

Iter 1 cascade note: `test_execute.py` has tests that pin the OLD
`NotImplementedError` default for `spawn_test_writer`. The Coder resolves
those cascades per Iter 2's behavior-preserving update pattern; this
file does NOT modify them (anti-lane). See the cascade list in the
final report for line numbers.
"""

from __future__ import annotations

import inspect
import json
import os
import pathlib
import re
import shutil
import sys
import types
import uuid as _uuid

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"
SOURCE_ROLES_DIR = PACKAGE_DIR / "roles"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fake Anthropic SDK — mirrors Story 6's tests. Installed into sys.modules
# per-test.
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    """Minimal stand-in for an Anthropic content block. Carries `.text`."""

    def __init__(self, text: str = "def test_x(): assert True\n"):
        self.text = text


class _FakeResponse:
    """Stand-in for the object returned by `client.messages.create(...)`.

    Mirrors the real Messages API: `.content` is a list of content
    blocks; each block has a `.text` attribute.
    """

    def __init__(self, text: str = "def test_x(): assert True\n"):
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    """Stand-in for `client.messages` — the `.create` subobject. Records
    every call into `self.calls` as a dict of kwargs."""

    def __init__(self, response=None, raise_exc=None):
        self._response = response or _FakeResponse()
        self._raise_exc = raise_exc
        self.calls: list = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


class _FakeAnthropicClient:
    """Stand-in for `anthropic.Anthropic`. Records the `api_key` it was
    constructed with and exposes a `.messages` subobject that records
    every `.create(...)` call. Constructed by the seam on every call."""

    instances: list = []

    def __init__(self, api_key=None, **kwargs):
        self.api_key = api_key
        self.ctor_kwargs = kwargs
        self.messages = _FakeMessages()
        _FakeAnthropicClient.instances.append(self)


_DEFAULT_TEST_CODE = (
    "import pytest\n"
    "\n"
    "def test_canonical_thing():\n"
    "    assert True\n"
)


def _install_fake_anthropic(monkeypatch,
                            response_text=None,
                            raise_exc=None):
    """Build a fake `anthropic` module and install it into `sys.modules`.

    `response_text` controls the `.text` returned by the fake
    `client.messages.create(...)`. If None, a default valid pytest test
    string is used.

    `raise_exc`, if set, causes `.create(...)` to raise the given
    exception instead of returning a response — used by the
    SDK-exception-propagation tests.

    NOTE: clears `_FakeAnthropicClient.instances` so each test starts
    with a clean record.
    """
    _FakeAnthropicClient.instances = []

    if response_text is None:
        response_text = _DEFAULT_TEST_CODE

    class _BoundClient(_FakeAnthropicClient):
        def __init__(self, api_key=None, **kwargs):
            super().__init__(api_key=api_key, **kwargs)
            self.messages = _FakeMessages(
                response=_FakeResponse(response_text),
                raise_exc=raise_exc,
            )

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _BoundClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return fake_module


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file AND mirror the
    package `roles/` dir under `tmp_path/roles/` so `resolve_role_spec`
    finds the canonical role-spec markdown files.
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)

    dest = tmp_path / "roles"
    if not dest.exists() and SOURCE_ROLES_DIR.is_dir():
        shutil.copytree(SOURCE_ROLES_DIR, dest)
    return log_file


@pytest.fixture
def api_key_env(monkeypatch):
    """Set `ANTHROPIC_API_KEY` to a test value so `resolve_api_key()`
    succeeds.

    Iter 3 v2 Sprint 1 Story 2 cascade: also pins
    `SM_CONTEXT_MODE=minimal` so these pre-Story-2 tests, which assert
    on the exact pre-Story-2 user-message framing, keep their existing
    assertions valid after Story 2 wired the codebase-context block
    into the spawn defaults' full-mode path.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-12345")
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    return "sk-test-key-12345"


@pytest.fixture
def no_api_key_env(monkeypatch):
    """Ensure `ANTHROPIC_API_KEY` is UNSET. Used by tests that pin the
    MissingAPIKeyError path."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return None


@pytest.fixture
def clean_resolver_env(monkeypatch):
    """Unset all per-spawn + global model / max_tokens env vars so a
    fresh test sees the documented defaults (Haiku 4.5, 4096)."""
    for name in (
        "SM_DECOMPOSE_MODEL", "SM_TEST_WRITER_MODEL",
        "SM_CODER_MODEL", "SM_REVIEWER_MODEL", "SM_MODEL",
        "SM_DECOMPOSE_MAX_TOKENS", "SM_TEST_WRITER_MAX_TOKENS",
        "SM_CODER_MAX_TOKENS", "SM_REVIEWER_MAX_TOKENS", "SM_MAX_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)


def _open_iteration(iteration_id: str = "iter-1",
                    requirements=None) -> dict:
    """Append an `iteration_open` entry."""
    import sm
    if requirements is None:
        requirements = [
            {"requirement_id": "req-1", "title": "T1",
             "description": "D1", "priority": "MUST",
             "acceptance_criteria": "AC1"},
        ]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": "Story 7 test iteration",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return entry


def _seed_backlog(n: int = 5) -> list:
    """Append a `story_backlog` entry with N canonical stories. Returns
    the list of minted story_ids in sequence order."""
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
            "acceptance_criteria": f"Story {i} must pass its tests.",
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
    """Open iteration + seed backlog + cut the sprint.
    Returns (story_ids, in_sprint_ids, deferred_ids)."""
    import sm
    _open_iteration(iteration_id=iteration_id)
    sids = _seed_backlog(n=n_stories)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at], sids[cut_at:]


def _make_coder(impl_code: str = "def foo(): return 1\n",
                record=None):
    """Build a spawn_coder stub. Used to fill the still-required coder
    callable so we can reach the test_writer spawn in Story 7's lane."""
    def _spawn(role_spec_path, story, test_code):
        if record is not None:
            record.setdefault("coder_calls", []).append({
                "role_spec_path": role_spec_path,
                "story": story,
                "test_code": test_code,
            })
        return impl_code
    return _spawn


def _make_reviewer(approved: bool = True,
                   test_result: str = "12 of 12 passed",
                   record=None):
    """Build a spawn_reviewer stub."""
    def _spawn(role_spec_path, story, test_code, impl_code):
        if record is not None:
            record.setdefault("reviewer_calls", []).append({
                "role_spec_path": role_spec_path,
                "story": story,
                "test_code": test_code,
                "impl_code": impl_code,
            })
        return {"approved": approved, "test_result": test_result}
    return _spawn


def _read_sm_source() -> str:
    """Return sm.py as text. Used by static grep tests."""
    return SM_PATH.read_text(encoding="utf-8")


def _read_role_spec_text() -> str:
    """Return the canonical test_writer.md role-spec content."""
    return (SOURCE_ROLES_DIR / "test_writer.md").read_text(encoding="utf-8")


def _captured_create_call() -> dict:
    """Return the kwargs of the single `messages.create(...)` call
    recorded by the last fake client. Asserts exactly one call was
    made (no retries, no double-fire)."""
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly one fake-client construction; got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    calls = _FakeAnthropicClient.instances[0].messages.calls
    assert len(calls) == 1, (
        f"expected exactly one create() call; got {len(calls)}"
    )
    return calls[0]


def _flatten_message_content(messages: list) -> str:
    """Flatten a `messages` list into a single string for substring
    matching."""
    out: list = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict):
                    if "text" in block:
                        out.append(str(block["text"]))
                    else:
                        out.append(json.dumps(block))
                else:
                    out.append(str(block))
        else:
            out.append(str(c))
    return "\n".join(out)


# ===========================================================================
# Category A — Smoke (6 tests)
#
# `_default_execute_test_writer_spawn` exists on the module, is PRIVATE,
# is NOT in `sm.__all__`, is callable, has the right signature.
# ===========================================================================


def test_default_test_writer_spawn_exists_on_module():
    """`sm._default_execute_test_writer_spawn` is defined at module scope."""
    import sm
    assert hasattr(sm, "_default_execute_test_writer_spawn"), (
        "expected `_default_execute_test_writer_spawn` to be defined on "
        "the sm module; missing from dir(sm)="
        f"{sorted(n for n in dir(sm) if 'test_writer' in n.lower())!r}"
    )


def test_default_test_writer_spawn_is_callable():
    """`sm._default_execute_test_writer_spawn` is callable."""
    import sm
    obj = getattr(sm, "_default_execute_test_writer_spawn", None)
    assert callable(obj), (
        f"expected `sm._default_execute_test_writer_spawn` to be callable; "
        f"got {type(obj).__name__}"
    )


def test_default_test_writer_spawn_is_private_name():
    """The default is `_default_execute_test_writer_spawn` (leading
    underscore)."""
    import sm
    assert hasattr(sm, "_default_execute_test_writer_spawn"), (
        "expected the private name `_default_execute_test_writer_spawn` "
        "to exist"
    )
    assert not hasattr(sm, "default_execute_test_writer_spawn"), (
        "expected no public `default_execute_test_writer_spawn`; the "
        "default is private."
    )


def test_default_test_writer_spawn_not_in_all():
    """`_default_execute_test_writer_spawn` is NOT in `sm.__all__`."""
    import sm
    assert "_default_execute_test_writer_spawn" not in sm.__all__, (
        f"`_default_execute_test_writer_spawn` must NOT be in sm.__all__; "
        f"got {sm.__all__!r}"
    )


def test_default_test_writer_spawn_signature_two_positional_params():
    """`_default_execute_test_writer_spawn` accepts (role_spec_path, story)
    — two positional parameters matching the injectable signature pinned
    by Iter 1 Story 23."""
    import sm
    sig = inspect.signature(sm._default_execute_test_writer_spawn)
    params = list(sig.parameters.values())
    assert len(params) >= 2, (
        f"expected at least 2 parameters (role_spec_path, story); "
        f"got signature {sig!s}"
    )


def test_default_test_writer_spawn_signature_parameter_names():
    """Parameter names are exactly `role_spec_path` and `story` in that
    order — exact match with the injectable signature."""
    import sm
    sig = inspect.signature(sm._default_execute_test_writer_spawn)
    names = list(sig.parameters)[:2]
    assert names == ["role_spec_path", "story"], (
        f"_default_execute_test_writer_spawn parameter names must be "
        f"['role_spec_path', 'story']; got {names!r}"
    )


# ===========================================================================
# Category B — Happy path with mocked SDK (8 tests)
#
# Default fires (no injected test_writer callable, but coder+reviewer
# still supplied because Stories 8/9 haven't shipped). API key, model,
# max_tokens resolved. Fake SDK returns test-code text. The
# `testwriter_output` entry carries the SDK response verbatim.
# ===========================================================================


def test_happy_path_default_returns_final_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>)` with NO spawn_test_writer kwarg (default fires)
    but with coder + reviewer injected, returns the final state-change
    entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    result = sm.execute(in_sprint[0],
                        spawn_coder=_make_coder(),
                        spawn_reviewer=_make_reviewer())
    assert isinstance(result, dict), (
        f"expected dict return; got {type(result).__name__}"
    )


def test_happy_path_default_writes_testwriter_output_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The real default fires -> `testwriter_output` entry is appended
    with the SDK response as `output`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_TEST_CODE)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"]
    assert len(tw) == 1, (
        f"expected exactly one testwriter_output entry; got {len(tw)}"
    )
    assert tw[0]["output"] == _DEFAULT_TEST_CODE, (
        f"expected testwriter_output.output to be the SDK response "
        f"verbatim; got {tw[0]['output']!r}"
    )


def test_happy_path_default_calls_invoke_anthropic_once(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """One `execute()` -> exactly one fake-client construction and one
    `messages.create` call. No retries."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_happy_path_default_constructs_client_with_resolved_api_key(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The api_key the seam constructs the client with is the value of
    `ANTHROPIC_API_KEY`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-roundtrip-tw-99999")
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert _FakeAnthropicClient.instances[0].api_key == \
        "sk-roundtrip-tw-99999"


def test_happy_path_default_response_text_flows_to_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The SDK response text flows VERBATIM into spawn_coder's
    `test_code` arg — confirming the default returns raw text without
    parsing."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel_code = "# SENTINEL-TW-RAW-9b3c1f4e2d\ndef test_x(): pass\n"
    _install_fake_anthropic(monkeypatch, response_text=sentinel_code)
    record = {}
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(record=record),
               spawn_reviewer=_make_reviewer())
    assert "coder_calls" in record
    assert record["coder_calls"][0]["test_code"] == sentinel_code, (
        f"expected SDK response to flow verbatim to spawn_coder; got "
        f"{record['coder_calls'][0]['test_code']!r}"
    )


def test_happy_path_default_entry_has_role_spec_path(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`testwriter_output` entry carries the resolved role-spec path
    pointing at test_writer.md."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"][0]
    assert "role_spec_path" in tw
    assert isinstance(tw["role_spec_path"], str)
    assert "test_writer" in tw["role_spec_path"], (
        f"role_spec_path must reference test_writer; got "
        f"{tw['role_spec_path']!r}"
    )


def test_happy_path_default_entry_has_role_spec_hash(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`testwriter_output` entry carries the role-spec hash."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"][0]
    assert "role_spec_hash" in tw
    assert isinstance(tw["role_spec_hash"], str)
    assert tw["role_spec_hash"] != "", (
        "role_spec_hash must be non-empty"
    )


def test_happy_path_default_entry_carries_story_id(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`testwriter_output` entry carries the story_id."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    target_id = in_sprint[0]
    sm.execute(target_id,
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"][0]
    assert tw["story_id"] == target_id


# ===========================================================================
# Category C — Role spec read from roles/test_writer.md (5 tests)
#
# The default reads the role-spec markdown file (via the path it
# receives) and includes its content in the user message.
# ===========================================================================


def test_role_spec_content_appears_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The text of `roles/test_writer.md` appears in the user message
    content reaching the SDK."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_role_spec_text()
    first_line = next(
        (ln.strip() for ln in role_text.splitlines() if ln.strip()),
        None,
    )
    assert first_line is not None, (
        "test_writer.md is empty or all-blank — fixture invariant broken"
    )
    assert first_line in msg_text, (
        f"expected role-spec excerpt {first_line!r} in message content; "
        f"message starts: {msg_text[:200]!r}"
    )


def test_role_spec_full_content_appears_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The ENTIRE test_writer.md content (verbatim) appears somewhere in
    the message — pins that the default reads + injects the full file."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_role_spec_text().strip()
    assert role_text in msg_text, (
        f"expected full role-spec content in message; first 200 chars of "
        f"role spec: {role_text[:200]!r}; first 400 chars of message: "
        f"{msg_text[:400]!r}"
    )


def test_role_spec_file_read_from_resolved_path(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """When `resolve_role_spec("test_writer")` points at a custom file
    (via the staged tmp roles dir), THAT file's content reaches the
    message. Verified by writing a sentinel string into the staged
    test_writer.md."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel = "SENTINEL-STORY-7-TW-MARKER-5f7e3a"
    staged = isolated_log.parent / "roles" / "test_writer.md"
    staged.write_text(sentinel + "\nrest of spec...", encoding="utf-8")
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    assert sentinel in msg_text, (
        f"expected sentinel {sentinel!r} (from staged test_writer.md) in "
        f"message content; default may have read a different file. "
        f"Message: {msg_text[:400]!r}"
    )


def test_role_spec_path_passed_into_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The default's `role_spec_path` argument is the absolute path to
    `roles/test_writer.md`. Verified by spying on the default itself."""
    import sm

    captured: dict = {}
    original_default = sm._default_execute_test_writer_spawn

    def _spy(role_spec_path, story):
        captured["role_spec_path"] = role_spec_path
        captured["story"] = dict(story)
        return original_default(role_spec_path, story)

    monkeypatch.setattr(sm, "_default_execute_test_writer_spawn", _spy)
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert "role_spec_path" in captured, (
        "expected spy to record role_spec_path"
    )
    path = captured["role_spec_path"]
    assert isinstance(path, str), (
        f"role_spec_path arg must be a string; got {type(path).__name__}"
    )
    assert path.endswith("test_writer.md"), (
        f"expected path to end with 'test_writer.md'; got {path!r}"
    )


def test_role_spec_read_failure_propagates(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """If the role-spec file is missing/unreadable when the default
    tries to read it, the error propagates. Simulated by deleting the
    staged test_writer.md just before the default body runs."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    real_default = sm._default_execute_test_writer_spawn

    def _delete_then_call(role_spec_path, story):
        try:
            os.remove(role_spec_path)
        except OSError:
            pass
        return real_default(role_spec_path, story)

    monkeypatch.setattr(sm, "_default_execute_test_writer_spawn",
                        _delete_then_call)
    # Either FileNotFoundError or TestWriterAgentError is acceptable —
    # the spec wraps SDK exceptions, but the file read is BEFORE the SDK
    # call, so wrapping behavior is the Coder's call. Pin that SOMETHING
    # raises.
    with pytest.raises(Exception):  # noqa: PT011
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


# ===========================================================================
# Category D — Story dict passed to SDK (5 tests)
#
# The full story dict from the backlog is included in the user message.
# ===========================================================================


def test_story_dict_appears_in_message_by_id(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `story_id` reaches the user message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    target_id = in_sprint[0]
    sm.execute(target_id,
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    assert target_id in msg_text, (
        f"expected story_id {target_id!r} in message content; "
        f"message: {msg_text[:500]!r}"
    )


def test_story_dict_title_appears_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `title` reaches the user message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    # First in-sprint story has sequence=1 and title "Story 1"
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    assert "Story 1" in msg_text, (
        f"expected story title 'Story 1' in message; "
        f"message: {msg_text[:500]!r}"
    )


def test_story_dict_acceptance_criteria_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `acceptance_criteria` reaches the user
    message — the agent needs the AC to pin tests."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    assert "Story 1 must pass its tests." in msg_text, (
        f"expected acceptance_criteria text in message; "
        f"message: {msg_text[:500]!r}"
    )


def test_story_dict_size_appears_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `size` reaches the user message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    # First in-sprint story has size "S" (sizes cycle S/M/L from sequence 1)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    # Size appears in JSON as "size": "S" — pin the size value's presence
    # in the message in a form that survives JSON-or-yaml framing.
    assert '"size"' in msg_text or "size" in msg_text, (
        "expected size field present in message"
    )
    assert '"S"' in msg_text or "'S'" in msg_text, (
        f"expected size value 'S' in message; message: {msg_text[:500]!r}"
    )


def test_story_dict_passed_to_default_arg(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The default receives the active story dict as its `story` arg.
    Verified by spying on the default."""
    import sm
    captured: dict = {}
    real_default = sm._default_execute_test_writer_spawn

    def _spy(role_spec_path, story):
        captured["story"] = dict(story)
        return real_default(role_spec_path, story)

    monkeypatch.setattr(sm, "_default_execute_test_writer_spawn", _spy)
    _, in_sprint, _ = _seed_sprint()
    target_id = in_sprint[0]
    _install_fake_anthropic(monkeypatch)
    sm.execute(target_id,
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert captured["story"]["story_id"] == target_id
    assert captured["story"]["title"] == "Story 1"
    assert captured["story"]["size"] == "S"


# ===========================================================================
# Category E — resolve_model("test_writer") wired (4 tests)
#
# With SM_TEST_WRITER_MODEL override set, the SDK call receives that
# model id. With override unset, the Haiku 4.5 default reaches the SDK.
# ===========================================================================


def test_resolve_model_test_writer_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_TEST_WRITER_MODEL=custom-tw-model` -> that exact string reaches
    `messages.create(model=...)`."""
    import sm
    monkeypatch.setenv("SM_TEST_WRITER_MODEL", "custom-tw-model-v9")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    assert call["model"] == "custom-tw-model-v9", (
        f"expected model='custom-tw-model-v9' to reach SDK; got "
        f"{call.get('model')!r}"
    )


def test_resolve_model_global_override_reaches_sdk_for_test_writer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With per-spawn unset and `SM_MODEL=global-id` set, the global
    fallback reaches the SDK."""
    import sm
    monkeypatch.delenv("SM_TEST_WRITER_MODEL", raising=False)
    monkeypatch.setenv("SM_MODEL", "global-fallback-model-tw")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    assert call["model"] == "global-fallback-model-tw"


def test_resolve_model_default_reaches_sdk_for_test_writer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With no env-var overrides, the documented Haiku 4.5 default reaches
    the SDK. Pinned via `resolve_model('test_writer')` so the test stays
    decoupled from the constant's literal value."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    expected = sm.resolve_model("test_writer")
    assert call["model"] == expected, (
        f"expected default model {expected!r} to reach SDK; got "
        f"{call.get('model')!r}"
    )


def test_resolve_model_per_spawn_beats_global_for_test_writer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With BOTH `SM_TEST_WRITER_MODEL` and `SM_MODEL` set, the per-spawn
    var wins."""
    import sm
    monkeypatch.setenv("SM_TEST_WRITER_MODEL", "per-spawn-wins-tw")
    monkeypatch.setenv("SM_MODEL", "global-loses-tw")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    assert call["model"] == "per-spawn-wins-tw"


# ===========================================================================
# Category F — resolve_max_tokens("test_writer") wired (4 tests)
#
# With SM_TEST_WRITER_MAX_TOKENS override set, the SDK call receives that
# int. With unset, the 4096 default reaches the SDK.
# ===========================================================================


def test_resolve_max_tokens_test_writer_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_TEST_WRITER_MAX_TOKENS=2048` -> 2048 (int) reaches the SDK."""
    import sm
    monkeypatch.setenv("SM_TEST_WRITER_MAX_TOKENS", "2048")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    assert call["max_tokens"] == 2048
    assert isinstance(call["max_tokens"], int), (
        f"max_tokens must be int (not str); got "
        f"{type(call['max_tokens']).__name__}"
    )


def test_resolve_max_tokens_global_override_reaches_sdk_for_test_writer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With per-spawn unset and `SM_MAX_TOKENS=8192` set, the global
    fallback reaches the SDK as an int."""
    import sm
    monkeypatch.delenv("SM_TEST_WRITER_MAX_TOKENS", raising=False)
    monkeypatch.setenv("SM_MAX_TOKENS", "8192")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    assert call["max_tokens"] == 8192


def test_resolve_max_tokens_default_reaches_sdk_for_test_writer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With no env-var overrides, `resolve_max_tokens('test_writer')`'s
    default (4096 per Story 3) reaches the SDK."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    expected = sm.resolve_max_tokens("test_writer")
    assert call["max_tokens"] == expected
    assert call["max_tokens"] == 4096


def test_resolve_max_tokens_per_spawn_beats_global_for_test_writer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With BOTH `SM_TEST_WRITER_MAX_TOKENS` and `SM_MAX_TOKENS` set, the
    per-spawn var wins."""
    import sm
    monkeypatch.setenv("SM_TEST_WRITER_MAX_TOKENS", "1024")
    monkeypatch.setenv("SM_MAX_TOKENS", "8192")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    assert call["max_tokens"] == 1024


# ===========================================================================
# Category G — API key missing (4 tests)
#
# Unset `ANTHROPIC_API_KEY` -> `MissingAPIKeyError` propagates UNCHANGED;
# SDK is NOT called.
# ===========================================================================


def test_missing_api_key_raises_missing_api_key_error(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """`execute()` falling through to the real default with no
    `ANTHROPIC_API_KEY` raises `MissingAPIKeyError` — propagated
    unchanged, NOT wrapped as TestWriterAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_missing_api_key_not_wrapped_as_test_writer_agent_error(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """MissingAPIKeyError must NOT be wrapped as TestWriterAgentError —
    the CLI's exit-12 mapping depends on the typed class."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
        pytest.fail("expected MissingAPIKeyError")
    except sm.TestWriterAgentError as e:
        pytest.fail(
            f"MissingAPIKeyError must propagate UNCHANGED, not be wrapped "
            f"as TestWriterAgentError; got: {e!s}"
        )
    except sm.MissingAPIKeyError:
        pass


def test_missing_api_key_does_not_call_sdk(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """When `ANTHROPIC_API_KEY` is unset, the SDK is not invoked."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    assert _FakeAnthropicClient.instances == [], (
        "MissingAPIKeyError must fire BEFORE the SDK is constructed"
    )


def test_missing_api_key_error_message_mentions_env_var(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """The `MissingAPIKeyError` message names `ANTHROPIC_API_KEY` so the
    operator knows which var to set."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError) as exc_info:
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    assert "ANTHROPIC_API_KEY" in str(exc_info.value), (
        f"MissingAPIKeyError message must name ANTHROPIC_API_KEY; got: "
        f"{exc_info.value!s}"
    )


# ===========================================================================
# Category H — ConfigError propagates (3 tests)
#
# Invalid SM_TEST_WRITER_MAX_TOKENS / SM_MAX_TOKENS int -> ConfigError.
# Must NOT be wrapped as TestWriterAgentError (operator needs the typed
# error for diagnosis).
# ===========================================================================


def test_invalid_test_writer_max_tokens_raises_config_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_TEST_WRITER_MAX_TOKENS=notanint` -> ConfigError propagates."""
    import sm
    monkeypatch.setenv("SM_TEST_WRITER_MAX_TOKENS", "notanint")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.ConfigError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_config_error_not_wrapped_as_test_writer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """ConfigError must propagate UNCHANGED, not be wrapped as
    TestWriterAgentError."""
    import sm
    monkeypatch.setenv("SM_TEST_WRITER_MAX_TOKENS", "garbage")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
        pytest.fail("expected ConfigError")
    except sm.TestWriterAgentError as e:
        pytest.fail(
            f"ConfigError must propagate UNCHANGED, not be wrapped as "
            f"TestWriterAgentError; got: {e!s}"
        )
    except sm.ConfigError:
        pass


def test_invalid_global_max_tokens_raises_config_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_MAX_TOKENS=notanint` (per-spawn unset) -> ConfigError."""
    import sm
    monkeypatch.delenv("SM_TEST_WRITER_MAX_TOKENS", raising=False)
    monkeypatch.setenv("SM_MAX_TOKENS", "notanint")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.ConfigError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


# ===========================================================================
# Category I — SDK exceptions wrapped as TestWriterAgentError (6 tests)
# ===========================================================================


def test_sdk_network_error_wraps_as_test_writer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated network failure (`ConnectionError`) -> wrapped as
    `TestWriterAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    boom = ConnectionError("ECONNREFUSED 443")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.TestWriterAgentError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_sdk_auth_error_wraps_as_test_writer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated auth failure -> wrapped as `TestWriterAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    class _FakeAuthError(Exception):
        pass

    boom = _FakeAuthError("401 invalid api key")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.TestWriterAgentError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_sdk_rate_limit_error_wraps_as_test_writer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated rate limit -> wrapped as `TestWriterAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    class _FakeRateLimitError(Exception):
        pass

    boom = _FakeRateLimitError("429 rate-limited")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.TestWriterAgentError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_sdk_exception_original_chained_via_cause(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The original SDK exception is chained via `__cause__` on the
    `TestWriterAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    boom = ConnectionError("network down")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.TestWriterAgentError) as exc_info:
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    err = exc_info.value
    chained = []
    cur = err
    while cur is not None:
        chained.append(cur)
        cur = cur.__cause__
    types_in_chain = {type(c) for c in chained}
    assert ConnectionError in types_in_chain or err.__cause__ is boom, (
        f"expected the original ConnectionError reachable via __cause__ "
        f"chain on TestWriterAgentError; chain types "
        f"{[t.__name__ for t in types_in_chain]!r}; direct cause: "
        f"{err.__cause__!r}"
    )


def test_sdk_exception_no_silent_swallow(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK raises -> execute() MUST raise (not return None / empty
    dict)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=RuntimeError("boom-tw"))
    with pytest.raises(Exception):  # noqa: PT011
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_sdk_exception_no_auto_retry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """On SDK failure, the seam is called exactly ONCE — no retries."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=RuntimeError("boom-no-retry"))
    with pytest.raises(Exception):  # noqa: PT011
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly ONE fake-client construction (no retries); "
        f"got {len(_FakeAnthropicClient.instances)}"
    )
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1, (
        f"expected exactly ONE messages.create() call (no retries); "
        f"got {len(_FakeAnthropicClient.instances[0].messages.calls)}"
    )


# ===========================================================================
# Category J — Raw text returned (no parse_agent_json) (4 tests)
#
# TestWriter returns code, not JSON. The default MUST NOT route the
# response through parse_agent_json. Any string — even malformed JSON,
# even an empty string — flows through unmodified.
# ===========================================================================


def test_default_returns_raw_text_unmodified(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The SDK response text reaches the testwriter_output entry's
    `output` field BYTE-FOR-BYTE."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    raw = (
        "# coding: utf-8\n"
        "import pytest\n"
        "\n"
        "def test_raw():\n"
        "    assert True\n"
    )
    _install_fake_anthropic(monkeypatch, response_text=raw)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"][0]
    assert tw["output"] == raw, (
        f"expected output to be SDK response BYTE-FOR-BYTE; got "
        f"{tw['output']!r}"
    )


def test_default_does_not_call_parse_agent_json(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Pin that `parse_agent_json` is NOT called during execute() — the
    default returns raw text. Verified by spying on parse_agent_json."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    called = {"count": 0}
    real_parse = sm.parse_agent_json

    def _spy(raw, role):
        called["count"] += 1
        return real_parse(raw, role)

    monkeypatch.setattr(sm, "parse_agent_json", _spy)
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert called["count"] == 0, (
        f"parse_agent_json must NOT be called by the TestWriter default; "
        f"got {called['count']} calls"
    )


def test_malformed_json_response_still_flows_through(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Even if the SDK returns malformed JSON-shaped text, the default
    returns it as-is (no parse, no TestWriterAgentError on shape)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    garbage = "this is { not } valid JSON ((("
    _install_fake_anthropic(monkeypatch, response_text=garbage)
    # Should NOT raise — TestWriter response is raw text.
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"][0]
    assert tw["output"] == garbage


def test_empty_response_flows_through(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Empty-string SDK response: default returns it unmodified. (Whether
    downstream coder/reviewer treat empty test_code as failure is THEIR
    contract, not test_writer's.)"""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch, response_text="")
    # Should NOT raise from the test_writer side.
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"][0]
    assert tw["output"] == ""


# ===========================================================================
# Category K — Removed NotImplementedError for test_writer only (4 tests)
#
# `execute(<id>, spawn_coder=..., spawn_reviewer=...)` (test_writer
# omitted) no longer raises NotImplementedError. Coder + reviewer ALONE
# (without test_writer) was the OLD breakage path; under Story 7 it
# works. But test_writer alone (without coder/reviewer) STILL raises
# NotImplementedError because Stories 8/9 haven't shipped.
# ===========================================================================


def test_test_writer_default_no_longer_raises_not_implemented(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>, spawn_coder=..., spawn_reviewer=...)` with
    spawn_test_writer omitted does NOT raise NotImplementedError —
    Story 7 inverted that default."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    except NotImplementedError as e:
        pytest.fail(
            f"Story 7: execute() with spawn_test_writer omitted must NOT "
            f"raise NotImplementedError; got: {e!s}"
        )


def test_test_writer_explicit_none_no_longer_raises_not_implemented(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>, spawn_test_writer=None, spawn_coder=..., spawn_reviewer=...)`
    routes test_writer=None to the real default, NOT NotImplementedError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=None,
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    except NotImplementedError as e:
        pytest.fail(
            f"Story 7: explicit spawn_test_writer=None must fall through "
            f"to real default, not raise NotImplementedError; got: {e!s}"
        )


def test_coder_still_raises_not_implemented_under_story_7(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """spawn_coder=None no longer raises NotImplementedError under
    Story 8 — Story 8 wired the real coder default.

    Iter 2 Story 8 inverted spawn_coder's default: None now routes
    to the real `_default_execute_coder_spawn`. Under Story 7 this
    test pinned the OLD NotImplementedError path; Story 8 resolves
    the cascade per the established behavior-preserving update
    pattern. With api_key_env set and fake SDK installed, the call
    now succeeds end-to-end (test_writer default + coder default
    both fire).
    """
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_coder=None,
                   spawn_reviewer=_make_reviewer())
    except NotImplementedError as e:
        pytest.fail(
            f"Story 8: explicit spawn_coder=None must fall through to "
            f"real default, not raise NotImplementedError; got: {e!s}"
        )


def test_reviewer_still_raises_not_implemented_under_story_7(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """spawn_reviewer=None no longer raises NotImplementedError under
    Story 9 — it routes to the real `_default_execute_reviewer_spawn`.

    This test was originally a Story-7-era forward-looking guard for
    "Story 9 will wire reviewer." Story 9 has shipped, so the guard
    inverts: spawn_reviewer=None must fall through to the real default.
    The reviewer-stage fake-SDK response is the default test-code
    string (not a valid reviewer JSON verdict), so the real default's
    `parse_agent_json` rejects it with a `ReviewerAgentError` — but the
    important thing here is that NotImplementedError is NOT raised.
    Behavior-preserving update — see Story 9's cascade list.
    """
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=None)
    except NotImplementedError as e:
        pytest.fail(
            f"Story 9: spawn_reviewer=None must fall through to the real "
            f"default, not raise NotImplementedError; got: {e!s}"
        )
    except sm.ReviewerAgentError:
        # Expected: the default test-code response is not valid JSON, so
        # parse_agent_json (Story 4) raises ReviewerAgentError. The test
        # only pins that NotImplementedError is NOT raised — Story 9 has
        # shipped, the linchpin is closed.
        pass


# ===========================================================================
# Category L — Injectable callable still works (5 tests)
# ===========================================================================


def test_injectable_callable_bypasses_real_default(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """Injecting `spawn_test_writer=callable` bypasses the real default
    — even with NO `ANTHROPIC_API_KEY`, the call succeeds."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    def _spawn(role_spec_path, story):
        return "def test_injected(): assert True\n"

    # No SDK fake installed — the injected callable must NOT trigger any
    # SDK path.
    result = sm.execute(in_sprint[0],
                        spawn_test_writer=_spawn,
                        spawn_coder=_make_coder(),
                        spawn_reviewer=_make_reviewer())
    assert isinstance(result, dict)


def test_injectable_callable_does_not_construct_sdk_client(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Even with SDK fake installed + valid API key, an injected
    test_writer callable means the fake SDK is never touched."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []

    def _spawn(role_spec_path, story):
        return "def test_x(): assert True\n"

    sm.execute(in_sprint[0],
               spawn_test_writer=_spawn,
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert _FakeAnthropicClient.instances == [], (
        "expected NO fake-SDK construction when spawn_test_writer is "
        "injected"
    )


def test_injectable_callable_receives_role_spec_path_and_story(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The injected callable receives `(role_spec_path: str, story:
    dict)` — same signature as the real default."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target_id = in_sprint[0]

    captured: dict = {}

    def _spawn(role_spec_path, story):
        captured["role_spec_path"] = role_spec_path
        captured["story"] = dict(story)
        return "def test_x(): assert True\n"

    sm.execute(target_id,
               spawn_test_writer=_spawn,
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert isinstance(captured["role_spec_path"], str)
    assert captured["role_spec_path"].endswith("test_writer.md")
    assert captured["story"]["story_id"] == target_id


def test_injectable_callable_return_value_used_verbatim(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The injected callable's return value reaches the
    testwriter_output entry verbatim."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    payload = "# CUSTOM-INJECTED-CODE-marker\ndef test_y(): pass\n"

    def _spawn(role_spec_path, story):
        return payload

    sm.execute(in_sprint[0],
               spawn_test_writer=_spawn,
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"][0]
    assert tw["output"] == payload


def test_injectable_callable_exception_propagates(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """An injected callable that raises has its exception propagated
    verbatim — Iter 1 Story 23's contract preserved."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    class _SpawnFailure(RuntimeError):
        pass

    def _spawn(role_spec_path, story):
        raise _SpawnFailure("custom test_writer callable failure")

    with pytest.raises(_SpawnFailure):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_spawn,
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


# ===========================================================================
# Category M — Caller-bind via sys.modules (2 tests)
#
# `execute` must bind the default by looking up
# `_default_execute_test_writer_spawn` on `sys.modules[__name__]` so
# monkeypatches via `monkeypatch.setattr(sm, "_default_..", ...)` take
# effect. Mirrors Story 6's pattern.
# ===========================================================================


def test_monkeypatch_on_default_takes_effect(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`monkeypatch.setattr(sm, "_default_execute_test_writer_spawn",
    fake)` replaces what `execute` calls when test_writer is omitted."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    called = {"count": 0}
    payload = "# MONKEYPATCHED-DEFAULT\ndef test_mp(): pass\n"

    def _patched(role_spec_path, story):
        called["count"] += 1
        return payload

    monkeypatch.setattr(sm, "_default_execute_test_writer_spawn", _patched)
    # No SDK fake installed — if execute were calling the real default
    # despite the patch, it would try to import anthropic and fail.
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert called["count"] == 1, (
        f"expected the monkeypatched default to be called exactly once; "
        f"got {called['count']}"
    )
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"][0]
    assert tw["output"] == payload, (
        "monkeypatched default's return value must reach the "
        "testwriter_output entry"
    )


def test_monkeypatch_default_does_not_touch_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """A monkeypatched default that doesn't call the SDK leaves the fake
    SDK untouched — proves the bind goes through sys.modules at call
    time, not via a closure captured at module-import time."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []

    def _patched(role_spec_path, story):
        return "def test_x(): pass\n"

    monkeypatch.setattr(sm, "_default_execute_test_writer_spawn", _patched)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert _FakeAnthropicClient.instances == [], (
        "monkeypatched default must REPLACE the real default — the "
        "fake SDK should NOT be touched"
    )


# ===========================================================================
# Category N — Message shape (4 tests)
# ===========================================================================


def test_messages_arg_is_a_list(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`messages` reaching the SDK is a list."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    assert isinstance(call["messages"], list), (
        f"messages must be a list; got {type(call['messages']).__name__}"
    )


def test_messages_arg_is_non_empty(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`messages` reaching the SDK is non-empty (at least one turn)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    assert len(call["messages"]) >= 1, (
        f"messages must have at least one turn; got {call['messages']!r}"
    )


def test_messages_have_user_role(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """At least one message has `role='user'`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    roles = [m.get("role") for m in call["messages"]]
    assert "user" in roles, (
        f"expected at least one user-role message; got roles {roles!r}"
    )


def test_messages_carry_both_spec_and_story(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The user message bundles BOTH the role-spec content AND the
    story dict — per Story 7 spec: 'role-spec text + story dict (JSON) +
    instruction to return test code'."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_role_spec_text().strip()
    assert role_text in msg_text, (
        "role spec content missing from messages"
    )
    assert in_sprint[0] in msg_text, (
        "story_id missing from messages — story dict not bundled"
    )


# ===========================================================================
# Category O — Static grep: default routes through provider seam (3 tests)
# ===========================================================================


def test_default_routes_through_invoke_anthropic_seam(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Runtime: invoking the default calls the provider seam."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_default_resolves_api_key_at_call_time(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Two calls with two different `ANTHROPIC_API_KEY` values use the
    LATEST value for each — pins read at call time, not at module-import."""
    import sm
    # First call
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-key-A-tw")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    first_api_key = _FakeAnthropicClient.instances[0].api_key

    # Second call — need a fresh story so the state-gate passes.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-key-B-tw")
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[1],
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    second_api_key = _FakeAnthropicClient.instances[0].api_key

    assert first_api_key == "sk-key-A-tw"
    assert second_api_key == "sk-key-B-tw"


def test_default_does_not_import_anthropic_directly():
    """Static check: `import anthropic` / `from anthropic` inside the
    `_default_execute_test_writer_spawn` function body is zero. The
    seam is the only legitimate import site."""
    src = _read_sm_source()
    lines = src.splitlines()
    def_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^def\s+_default_execute_test_writer_spawn\s*\(",
                    line):
            def_idx = i
            break
    if def_idx is None:
        pytest.fail(
            "`_default_execute_test_writer_spawn` def not found at "
            "module-top-level in sm.py — Story 7's name contract is broken"
        )
    body_lines = []
    for j in range(def_idx + 1, len(lines)):
        ln = lines[j]
        if ln.strip() == "":
            body_lines.append(ln)
            continue
        if not ln.startswith((" ", "\t")):
            break
        body_lines.append(ln)
    body_text = "\n".join(body_lines)
    for body_ln in body_text.splitlines():
        code = body_ln.split("#", 1)[0]
        if re.search(r"\b(import\s+anthropic|from\s+anthropic)\b", code):
            pytest.fail(
                f"`_default_execute_test_writer_spawn` body has a direct "
                f"`anthropic` import: {body_ln!r}. The default must route "
                f"through `_invoke_anthropic`, not import the SDK itself."
            )


# ===========================================================================
# Category P — Failure invariants: SDK exception, log shape (3 tests)
# ===========================================================================


def test_sdk_exception_does_not_write_testwriter_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK exception fires BEFORE the testwriter_output entry is written.
    No testwriter_output entry in the log after a failure."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=ConnectionError("downed"))
    with pytest.raises(sm.TestWriterAgentError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"]
    assert len(tw) == 0, (
        f"expected NO testwriter_output entry on SDK failure; got "
        f"{len(tw)} entries"
    )


def test_missing_api_key_does_not_write_testwriter_entry(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """MissingAPIKeyError -> no testwriter_output entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"]
    assert len(tw) == 0


def test_sdk_exception_does_not_call_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK exception means the test_writer step fails; spawn_coder is
    never called."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=ConnectionError("bad"))
    record = {}
    with pytest.raises(sm.TestWriterAgentError):
        sm.execute(in_sprint[0],
                   spawn_coder=_make_coder(record=record),
                   spawn_reviewer=_make_reviewer(record=record))
    assert "coder_calls" not in record, (
        "spawn_coder must NOT be called when test_writer fails"
    )
    assert "reviewer_calls" not in record, (
        "spawn_reviewer must NOT be called when test_writer fails"
    )
