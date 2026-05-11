"""Iter 2 Story 6 — Real `spawn_agent` default in `decompose`.

This file pins the contract of `decompose`'s real (non-injected) spawn
default — the first linchpin of Iter 2. Stories 1-5 wired the pieces
(anthropic dep, `resolve_api_key`, `resolve_model`, `resolve_max_tokens`,
`parse_agent_json`, `_invoke_anthropic` provider seam). Story 6 wires
them together so that `python -m sm decompose` against a real active
iteration with NO injected callable produces a real story list.

Pinned clauses (verbatim from `iter2/Stories_v1.md`, Story 6):

  1. Replaces the `NotImplementedError` default in `decompose`'s
     `spawn_agent` parameter with a real implementation matching the
     existing injectable-callable signature exactly (per ASSUMPTION 1)
     — no signature drift, no downstream ripple.
  2. Default reads `roles/sm_agent.md` via Iter 1 Story 8's
     `resolve_role_spec`, packages it plus the active iteration's
     requirement list into a single user message, calls the provider
     seam (Story 5) with `resolve_model("decompose")` and
     `resolve_max_tokens("decompose")`, then routes the response through
     `parse_agent_json(..., role="decompose")` (Story 4).
  3. On `parse_agent_json` failure, raises `DecomposeAgentError` (caught
     by CLI -> exit 12).
  4. On SDK-level failure (network, auth, rate-limit), the exception is
     wrapped as `DecomposeAgentError` and propagated; no silent swallow,
     no auto-retry.
  5. End-to-end (with mocked SDK): `python -m sm decompose` against a
     real active iteration with no injected callable returns a structured
     story list shaped per ASSUMPTION 3 and persists it through the
     existing pipeline contract unchanged.

CONTRACT INTERPRETATION (locked by TestWriter):

  - PRIVATE name: the real default is `_default_decompose_spawn` at
    module scope on `sm`. NOT in `sm.__all__`. The four spawn defaults
    are internal implementation; only their wired-up signatures are
    public surface.
  - Signature is `_default_decompose_spawn(role_spec_path: str,
    requirements: list[dict]) -> str` — exact match with the existing
    injectable-callable signature pinned by Story 9 (Iter 1).
  - The default reads `role_spec_path` content from disk (the caller in
    `decompose` already calls `resolve_role_spec("sm_agent")` and passes
    the path; the default reads it). Keeps the default thin/SDK-shaped.
  - Message shape: a single user-turn message whose `content` is a
    string that contains BOTH the role-spec text and the requirements
    list (as JSON). Exact ordering / framing is the Coder's call; tests
    verify both pieces appear in the message content.
  - Model/max_tokens are read at call time via `resolve_model("decompose")`
    and `resolve_max_tokens("decompose")` — so env-var overrides
    (`SM_DECOMPOSE_MODEL`, `SM_DECOMPOSE_MAX_TOKENS`) are honored on
    every call. No caching.
  - API key is read via `resolve_api_key()` — so a missing key raises
    `MissingAPIKeyError` before any SDK work. The default is responsible
    for resolving the key (not the caller in `decompose`).
  - Provider-seam invocation: the default calls `_invoke_anthropic(
    messages=..., model=..., max_tokens=..., api_key=...)`. Anthropic
    SDK is NOT imported by the default itself — only by the seam.
  - Return value: the default returns the SDK seam's response string
    AS-IS. The caller (`decompose`) routes it through `parse_agent_json`.
    Story 4's helper already raises `DecomposeAgentError` on parse
    failure, which `decompose` re-raises as `DecomposeOutputParseError`
    (the existing subclass).
  - SDK exception wrapping: when `_invoke_anthropic` raises (network /
    auth / rate-limit / generic Exception that is NOT a `MissingAPIKeyError`
    and NOT already a `DecomposeAgentError`), the default wraps it as a
    `DecomposeAgentError` with the original chained via `__cause__`.
    `MissingAPIKeyError` propagates unchanged (the CLI maps it to exit
    12 already).
  - No auto-retry: one SDK call per `decompose` invocation. No retries
    on failure; no second chance.
  - `NotImplementedError` removal: `decompose()` with no args no longer
    raises `NotImplementedError`. With a valid API key and a mocked
    SDK, it returns the appended `story_backlog` entry. Without an API
    key it raises `MissingAPIKeyError`.
  - Injectable callable is preserved: `decompose(spawn_agent=callable)`
    continues to bypass the default entirely. No regression on Story 9's
    injectable contract.

CRITICAL — tests must NOT make real API calls. Every test that triggers
the default path injects a fake `anthropic` module into `sys.modules`
via `monkeypatch.setitem` BEFORE the call. The lazy import inside
`_invoke_anthropic` finds the fake and never touches the real SDK.

Iter 1 cascade note: `test_decompose.py` has 5+ tests that pin the OLD
`NotImplementedError` default behavior (lines 262-297, 1155-1159 of
that file as of Iter 1 Story 22). Those tests WILL break under Story 6
because the default no longer raises NotImplementedError. The Coder
resolves those cascades per Iter 2's allowlist-extension / behavior-
preserving update pattern; this file does NOT modify them (anti-lane).
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import types

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"
SOURCE_ROLES_DIR = PACKAGE_DIR / "roles"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fake Anthropic SDK — mirrors Story 5's tests so the same injection
# pattern works here. Installed into sys.modules per-test.
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    """Minimal stand-in for an Anthropic content block. Carries `.text`."""

    def __init__(self, text: str = "fake response text"):
        self.text = text


class _FakeResponse:
    """Stand-in for the object returned by `client.messages.create(...)`.

    Mirrors the real Messages API: `.content` is a list of content
    blocks; each block has a `.text` attribute.
    """

    def __init__(self, text: str = "fake response text"):
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    """Stand-in for `client.messages` — the `.create` subobject. Records
    every call into `self.calls` as a dict of kwargs."""

    def __init__(self, response: _FakeResponse | None = None,
                 raise_exc: Exception | None = None):
        self._response = response or _FakeResponse()
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


class _FakeAnthropicClient:
    """Stand-in for `anthropic.Anthropic`. Records the `api_key` it was
    constructed with and exposes a `.messages` subobject that records
    every `.create(...)` call. Constructed by the seam on every call."""

    instances: list["_FakeAnthropicClient"] = []

    def __init__(self, api_key=None, **kwargs):
        self.api_key = api_key
        self.ctor_kwargs = kwargs
        self.messages = _FakeMessages()
        _FakeAnthropicClient.instances.append(self)


def _install_fake_anthropic(
    monkeypatch,
    response_text: str | None = None,
    raise_exc: Exception | None = None,
) -> types.ModuleType:
    """Build a fake `anthropic` module and install it into `sys.modules`.

    `response_text` controls the `.text` returned by the fake
    `client.messages.create(...)`. If None, a default valid story-list
    JSON is used so the happy path round-trips through `parse_agent_json`
    without contortions.

    `raise_exc`, if set, causes `.create(...)` to raise the given
    exception instead of returning a response — used by the
    SDK-exception-propagation tests.

    NOTE: clears `_FakeAnthropicClient.instances` so each test starts
    with a clean record.
    """
    _FakeAnthropicClient.instances = []

    if response_text is None:
        response_text = json.dumps(_canonical_agent_output())

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
    finds the canonical role-spec markdown files. The conftest.py
    autouse fixture only fires for `test_decompose.py`; this file needs
    its own roles staging.
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
    succeeds. Tests that exercise the missing-key path use a separate
    fixture that explicitly unsets it.

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


def _seed_iteration(iteration_id: str = "iter-1",
                    requirements=None) -> list:
    """Append an `iteration_open` entry so a subsequent decompose() has
    an active iteration to work against. Mirrors the helper in
    `test_decompose.py` so the seeded shape matches Iter 1's contract.
    """
    import sm
    if requirements is None:
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
        "iteration_goal": "Story 6 test iteration",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return list(requirements)


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


def _read_sm_source() -> str:
    """Return sm.py as text. Used by static grep tests."""
    return SM_PATH.read_text(encoding="utf-8")


def _read_role_spec_text() -> str:
    """Return the canonical sm_agent.md role-spec content. Used by
    message-content verification tests."""
    return (SOURCE_ROLES_DIR / "sm_agent.md").read_text(encoding="utf-8")


def _captured_create_call() -> dict:
    """Return the kwargs of the single `messages.create(...)` call
    recorded by the last fake client. Asserts exactly one call was
    made (no retries, no double-fire). Use INSIDE a test that has
    already triggered the default path."""
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
    matching. Each message's content may be a string OR a list of
    content blocks (Anthropic supports both). This helper handles both
    so tests can do `assert text in _flatten(...)` without caring about
    the exact framing.
    """
    out: list[str] = []
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
# `_default_decompose_spawn` exists on the module, is PRIVATE, is NOT in
# `sm.__all__`, is callable, has the right signature, and `decompose`'s
# `spawn_agent` default is no longer `None`-binds-to-NotImplementedError.
# ===========================================================================


def test_default_decompose_spawn_exists_on_module():
    """`sm._default_decompose_spawn` is defined at module scope."""
    import sm
    assert hasattr(sm, "_default_decompose_spawn"), (
        "expected `_default_decompose_spawn` to be defined on the sm module; "
        f"missing from dir(sm)={sorted(n for n in dir(sm) if 'decomp' in n.lower())!r}"
    )


def test_default_decompose_spawn_is_callable():
    """`sm._default_decompose_spawn` is callable."""
    import sm
    obj = getattr(sm, "_default_decompose_spawn", None)
    assert callable(obj), (
        f"expected `sm._default_decompose_spawn` to be callable; got "
        f"{type(obj).__name__}"
    )


def test_default_decompose_spawn_is_private_name():
    """The default is `_default_decompose_spawn` (leading underscore)."""
    import sm
    assert hasattr(sm, "_default_decompose_spawn"), (
        "expected the private name `_default_decompose_spawn` to exist"
    )
    assert not hasattr(sm, "default_decompose_spawn"), (
        "expected no public `default_decompose_spawn`; the default is "
        "private. Either remove the public alias or rename the private one."
    )


def test_default_decompose_spawn_not_in_all():
    """`_default_decompose_spawn` is NOT in `sm.__all__`."""
    import sm
    assert "_default_decompose_spawn" not in sm.__all__, (
        f"`_default_decompose_spawn` must NOT be in sm.__all__; got "
        f"{sm.__all__!r}"
    )


def test_default_decompose_spawn_signature_two_positional_params():
    """`_default_decompose_spawn` accepts (role_spec_path, requirements)
    — two positional parameters matching the injectable signature pinned
    by Iter 1 Story 9."""
    import sm
    sig = inspect.signature(sm._default_decompose_spawn)
    params = list(sig.parameters.values())
    assert len(params) >= 2, (
        f"expected at least 2 parameters (role_spec_path, requirements); "
        f"got signature {sig!s}"
    )


def test_default_decompose_spawn_signature_parameter_names():
    """Parameter names are exactly `role_spec_path` and `requirements`
    in that order — exact match with the injectable signature."""
    import sm
    sig = inspect.signature(sm._default_decompose_spawn)
    names = list(sig.parameters)[:2]
    assert names == ["role_spec_path", "requirements"], (
        f"_default_decompose_spawn parameter names must be "
        f"['role_spec_path', 'requirements']; got {names!r}"
    )


# ===========================================================================
# Category B — Happy path with mocked SDK (10 tests)
#
# Default fires (no injected callable). API key, model, max_tokens
# resolved. Fake SDK returns a valid JSON story list. decompose appends
# a single `story_backlog` entry and derive_state surfaces it.
# ===========================================================================


def test_happy_path_default_returns_appended_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`decompose()` with NO spawn_agent kwarg fires the real default,
    routes through the mocked SDK, and returns the appended entry."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    result = sm.decompose()
    assert isinstance(result, dict), (
        f"expected dict return; got {type(result).__name__}"
    )
    assert result["type"] == "story_backlog", (
        f"expected type='story_backlog'; got {result['type']!r}"
    )


def test_happy_path_default_writes_one_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The real default appends exactly one log entry (one decompose
    call = one entry)."""
    import sm
    _seed_iteration()
    before = list(sm.read_entries())
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    after = list(sm.read_entries())
    assert len(after) == len(before) + 1, (
        f"expected exactly one new entry; before={len(before)} "
        f"after={len(after)}"
    )


def test_happy_path_default_entry_carries_stories(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The appended entry carries a `stories` list shaped per
    ASSUMPTION 3."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch,
                            response_text=json.dumps(_canonical_agent_output(n=3)))
    result = sm.decompose()
    assert "stories" in result
    assert isinstance(result["stories"], list)
    assert len(result["stories"]) == 3


def test_happy_path_default_stories_have_story_ids(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Each story in the entry has a freshly minted `story_id`."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch,
                            response_text=json.dumps(_canonical_agent_output(n=4)))
    result = sm.decompose()
    for s in result["stories"]:
        assert "story_id" in s, f"story missing story_id: {s!r}"
        assert isinstance(s["story_id"], str)
        assert len(s["story_id"]) > 0


def test_happy_path_default_routes_through_derive_state(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`derive_state().story_backlog` surfaces the persisted stories —
    end-to-end pipeline contract."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch,
                            response_text=json.dumps(_canonical_agent_output(n=2)))
    sm.decompose()
    state = sm.derive_state()
    backlog = state.get("story_backlog") or []
    assert len(backlog) == 2, (
        f"expected 2 stories in derive_state().story_backlog; "
        f"got {len(backlog)}"
    )


def test_happy_path_default_calls_invoke_anthropic_once(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """One `decompose()` call -> exactly one provider-seam invocation
    -> exactly one fake-client construction and one `messages.create`
    call. No retries, no double-fires."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_happy_path_default_constructs_client_with_resolved_api_key(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The api_key the seam constructs the client with is the value of
    `ANTHROPIC_API_KEY`."""
    import sm
    _seed_iteration()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-roundtrip-99999")
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    assert _FakeAnthropicClient.instances[0].api_key == "sk-roundtrip-99999"


def test_happy_path_default_sequences_preserved(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Sequence numbers from the agent's JSON survive into the entry."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch,
                            response_text=json.dumps(_canonical_agent_output(n=3)))
    result = sm.decompose()
    seqs = [s["sequence"] for s in result["stories"]]
    assert seqs == [1, 2, 3]


def test_happy_path_default_role_spec_path_in_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The entry carries the resolved role-spec path. Even though the
    default fires, the caller still records the path on the entry."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    result = sm.decompose()
    assert "role_spec_path" in result
    assert isinstance(result["role_spec_path"], str)
    assert "sm_agent" in result["role_spec_path"], (
        f"role_spec_path must reference sm_agent; got "
        f"{result['role_spec_path']!r}"
    )


def test_happy_path_default_titles_preserved(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The agent's story titles survive into the entry verbatim."""
    import sm
    _seed_iteration()
    output = {"stories": [
        {"sequence": 1, "title": "Real default carries titles",
         "size": "S", "requirement_ids": ["req-1"],
         "acceptance_criteria": "AC1"},
        {"sequence": 2, "title": "Second title preserved too",
         "size": "M", "requirement_ids": ["req-2"],
         "acceptance_criteria": "AC2"},
    ]}
    _install_fake_anthropic(monkeypatch,
                            response_text=json.dumps(output))
    result = sm.decompose()
    titles = [s["title"] for s in result["stories"]]
    assert titles == [
        "Real default carries titles",
        "Second title preserved too",
    ]


# ===========================================================================
# Category C — Role spec read from roles/sm_agent.md (5 tests)
#
# The default reads the role-spec markdown file (via the path it
# receives) and includes its content in the user message. Mock the SDK
# and verify the message content includes substrings from sm_agent.md.
# ===========================================================================


def test_role_spec_content_appears_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The text of `roles/sm_agent.md` appears in the user message
    content reaching the SDK. Pinned by substring match on a known
    excerpt — exact framing is the Coder's call."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_role_spec_text()
    # The role-spec text has a stable header line. Grab the first
    # non-blank line and assert it appears in the message.
    first_line = next(
        (ln.strip() for ln in role_text.splitlines() if ln.strip()),
        None,
    )
    assert first_line is not None, (
        "sm_agent.md is empty or all-blank — test fixture invariant broken"
    )
    assert first_line in msg_text, (
        f"expected role-spec excerpt {first_line!r} in message content; "
        f"message starts: {msg_text[:200]!r}"
    )


def test_role_spec_full_content_appears_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The ENTIRE sm_agent.md content (verbatim) appears somewhere in
    the message. Stronger than the first-line check — pins that the
    default reads + injects the full file, not a truncated/templated
    version."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_role_spec_text().strip()
    # Allow trailing whitespace tolerance — the default may strip the
    # file content. Match on the stripped content.
    assert role_text in msg_text, (
        f"expected full role-spec content in message; first 200 chars of "
        f"role spec: {role_text[:200]!r}; first 400 chars of message: "
        f"{msg_text[:400]!r}"
    )


def test_role_spec_file_read_from_resolved_path(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """When `resolve_role_spec("sm_agent")` points at a custom file
    (via the staged tmp roles dir), THAT file's content reaches the
    message. Verified by writing a sentinel string into the staged
    sm_agent.md and asserting it surfaces."""
    import sm
    _seed_iteration()
    # Overwrite the staged sm_agent.md with a sentinel marker so we
    # know the default read THIS file (not the package-source one).
    sentinel = "SENTINEL-STORY-6-MARKER-9b3c1f4e2d"
    staged = isolated_log.parent / "roles" / "sm_agent.md"
    staged.write_text(sentinel + "\nrest of spec...", encoding="utf-8")
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    assert sentinel in msg_text, (
        f"expected sentinel {sentinel!r} (from staged sm_agent.md) in "
        f"message content; default may have read a different file. "
        f"Message: {msg_text[:400]!r}"
    )


def test_role_spec_path_passed_into_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The default's `role_spec_path` argument is the absolute path to
    `roles/sm_agent.md` (matches the existing injectable contract).
    Verified by spying on the default itself."""
    import sm

    captured: dict = {}
    original_default = sm._default_decompose_spawn

    def _spy(role_spec_path, requirements):
        captured["role_spec_path"] = role_spec_path
        captured["requirements"] = list(requirements)
        return original_default(role_spec_path, requirements)

    monkeypatch.setattr(sm, "_default_decompose_spawn", _spy)
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    assert "role_spec_path" in captured, (
        "expected spy to record role_spec_path"
    )
    path = captured["role_spec_path"]
    assert isinstance(path, str), (
        f"role_spec_path arg must be a string; got {type(path).__name__}"
    )
    assert path.endswith("sm_agent.md"), (
        f"expected path to end with 'sm_agent.md'; got {path!r}"
    )


def test_role_spec_read_failure_propagates(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """If the role-spec file is missing/unreadable when the default
    tries to read it, the error propagates (no silent swallow). Verified
    by deleting the staged sm_agent.md AFTER the resolver runs but
    BEFORE the default body executes."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)

    # Wrap the default to delete the role-spec file BEFORE the real
    # default reads it. This simulates a file-system race.
    real_default = sm._default_decompose_spawn

    def _delete_then_call(role_spec_path, requirements):
        try:
            os.remove(role_spec_path)
        except OSError:
            pass
        return real_default(role_spec_path, requirements)

    monkeypatch.setattr(sm, "_default_decompose_spawn", _delete_then_call)
    # Either FileNotFoundError (raw OS) or DecomposeAgentError (wrapped)
    # is acceptable — the spec wraps SDK exceptions, but the file read
    # is BEFORE the SDK call, so wrapping behavior here is the Coder's
    # call. Pin that SOMETHING raises and the log stays unchanged.
    seeded = isolated_log.read_bytes()
    with pytest.raises(Exception):  # noqa: PT011 — pin "something raises"
        sm.decompose()
    assert isolated_log.read_bytes() == seeded, (
        "log must be byte-for-byte unchanged on role-spec read failure"
    )


# ===========================================================================
# Category D — Requirements passed to SDK (5 tests)
#
# The iteration's requirement list (from the active iteration_open
# entry) is included in the user message. Mock the SDK and verify the
# requirement fields appear in the message content.
# ===========================================================================


def test_requirements_list_appears_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active iteration's requirements list reaches the user
    message. Pinned by substring match on a known requirement id."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    # `req-1` through `req-5` are seeded; all five must appear.
    for rid in ("req-1", "req-2", "req-3", "req-4", "req-5"):
        assert rid in msg_text, (
            f"requirement_id {rid!r} missing from message content; "
            f"message: {msg_text[:500]!r}"
        )


def test_requirements_titles_appear_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Each requirement's `title` reaches the user message."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    for i in range(1, 6):
        assert f"Title {i}" in msg_text, (
            f"expected requirement title 'Title {i}' in message; "
            f"message: {msg_text[:500]!r}"
        )


def test_requirements_descriptions_appear_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Each requirement's `description` reaches the user message."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    for i in range(1, 6):
        assert f"Description {i}." in msg_text, (
            f"expected requirement description 'Description {i}.' in "
            f"message; message: {msg_text[:500]!r}"
        )


def test_requirements_priorities_appear_in_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Each requirement's `priority` reaches the user message — the
    agent needs MUST / SHOULD / NICE to size and sequence."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    # Seeded priorities: MUST, SHOULD, NICE, MUST, SHOULD — assert each
    # priority label appears at least once.
    for prio in ("MUST", "SHOULD", "NICE"):
        assert prio in msg_text, (
            f"expected priority {prio!r} in message content; "
            f"message: {msg_text[:500]!r}"
        )


def test_requirements_passed_to_default_arg(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The default receives the active iteration's requirements list as
    its `requirements` arg. Verified by spying on the default."""
    import sm

    captured: dict = {}
    real_default = sm._default_decompose_spawn

    def _spy(role_spec_path, requirements):
        captured["requirements"] = list(requirements)
        return real_default(role_spec_path, requirements)

    monkeypatch.setattr(sm, "_default_decompose_spawn", _spy)
    seeded = _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    assert captured["requirements"] == seeded, (
        f"expected default to receive the seeded requirements verbatim;\n"
        f"got: {captured.get('requirements')!r}\n"
        f"expected: {seeded!r}"
    )


# ===========================================================================
# Category E — resolve_model("decompose") wired (4 tests)
#
# With SM_DECOMPOSE_MODEL override set, the SDK call receives that
# model id. With override unset, the Haiku 4.5 default reaches the SDK.
# ===========================================================================


def test_resolve_model_decompose_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_DECOMPOSE_MODEL=custom-model-id` -> that exact string reaches
    `messages.create(model=...)`. Pins the Story 3 -> Story 6 wire."""
    import sm
    monkeypatch.setenv("SM_DECOMPOSE_MODEL", "custom-decompose-model-v9")
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    assert call["model"] == "custom-decompose-model-v9", (
        f"expected model='custom-decompose-model-v9' to reach SDK; got "
        f"{call.get('model')!r}"
    )


def test_resolve_model_global_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With per-spawn unset and `SM_MODEL=global-id` set, the global
    fallback reaches the SDK."""
    import sm
    monkeypatch.delenv("SM_DECOMPOSE_MODEL", raising=False)
    monkeypatch.setenv("SM_MODEL", "global-fallback-model")
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    assert call["model"] == "global-fallback-model", (
        f"expected SM_MODEL global to reach SDK; got {call.get('model')!r}"
    )


def test_resolve_model_default_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With no env-var overrides, the Haiku 4.5 default reaches the SDK.
    Pinned to whatever the module-level constant resolves to (not a
    hardcoded string here — that's Story 3's contract)."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    expected = sm.resolve_model("decompose")
    assert call["model"] == expected, (
        f"expected the resolved default model {expected!r} to reach SDK; "
        f"got {call.get('model')!r}"
    )


def test_resolve_model_per_spawn_beats_global(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With BOTH `SM_DECOMPOSE_MODEL` and `SM_MODEL` set, the per-spawn
    var wins (Story 3's precedence pinned end-to-end)."""
    import sm
    monkeypatch.setenv("SM_DECOMPOSE_MODEL", "per-spawn-wins")
    monkeypatch.setenv("SM_MODEL", "global-loses")
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    assert call["model"] == "per-spawn-wins"


# ===========================================================================
# Category F — resolve_max_tokens("decompose") wired (4 tests)
#
# With SM_DECOMPOSE_MAX_TOKENS override set, the SDK call receives that
# int. With unset, the 4096 default reaches the SDK.
# ===========================================================================


def test_resolve_max_tokens_decompose_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_DECOMPOSE_MAX_TOKENS=2048` -> 2048 (int) reaches the SDK."""
    import sm
    monkeypatch.setenv("SM_DECOMPOSE_MAX_TOKENS", "2048")
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    assert call["max_tokens"] == 2048, (
        f"expected max_tokens=2048 (int) to reach SDK; got "
        f"{call.get('max_tokens')!r}"
    )
    assert isinstance(call["max_tokens"], int), (
        f"max_tokens must be int (not str); got "
        f"{type(call['max_tokens']).__name__}"
    )


def test_resolve_max_tokens_global_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With per-spawn unset and `SM_MAX_TOKENS=8192` set, the global
    fallback reaches the SDK as an int."""
    import sm
    monkeypatch.delenv("SM_DECOMPOSE_MAX_TOKENS", raising=False)
    monkeypatch.setenv("SM_MAX_TOKENS", "8192")
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    assert call["max_tokens"] == 8192


def test_resolve_max_tokens_default_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With no env-var overrides, `resolve_max_tokens("decompose")`'s
    default (4096 per Story 3) reaches the SDK."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    expected = sm.resolve_max_tokens("decompose")
    assert call["max_tokens"] == expected
    assert call["max_tokens"] == 4096, (
        f"expected the documented default 4096; got {call['max_tokens']!r}"
    )


def test_resolve_max_tokens_per_spawn_beats_global(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With BOTH `SM_DECOMPOSE_MAX_TOKENS` and `SM_MAX_TOKENS` set, the
    per-spawn var wins."""
    import sm
    monkeypatch.setenv("SM_DECOMPOSE_MAX_TOKENS", "1024")
    monkeypatch.setenv("SM_MAX_TOKENS", "8192")
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    assert call["max_tokens"] == 1024


# ===========================================================================
# Category G — API key missing (4 tests)
#
# Unset `ANTHROPIC_API_KEY` -> `MissingAPIKeyError` propagates from the
# default; SDK is NOT called; log is unchanged. CLI maps to exit 12.
# ===========================================================================


def test_missing_api_key_raises_missing_api_key_error(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """`decompose()` with no `ANTHROPIC_API_KEY` raises
    `MissingAPIKeyError`. The error originates from `resolve_api_key()`
    in the default. The fake SDK is installed but should NOT be touched
    because the API-key check fires first."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError):
        sm.decompose()


def test_missing_api_key_does_not_call_sdk(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """When `ANTHROPIC_API_KEY` is unset, the SDK is not invoked. No
    fake client is constructed; no `messages.create` call is made."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []
    with pytest.raises(sm.MissingAPIKeyError):
        sm.decompose()
    assert _FakeAnthropicClient.instances == [], (
        "MissingAPIKeyError must fire BEFORE the SDK is constructed; "
        "found a constructed client"
    )


def test_missing_api_key_does_not_write_log(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """No log write on missing-key failure — the log is byte-for-byte
    unchanged."""
    import sm
    _seed_iteration()
    seeded = isolated_log.read_bytes()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError):
        sm.decompose()
    assert isolated_log.read_bytes() == seeded, (
        "log must be byte-for-byte unchanged on MissingAPIKeyError"
    )


def test_missing_api_key_error_message_mentions_env_var(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """The `MissingAPIKeyError` message names `ANTHROPIC_API_KEY` so
    the operator knows exactly which var to set (Story 2's
    actionable-error pin, re-asserted in the Story 6 context)."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError) as exc_info:
        sm.decompose()
    assert "ANTHROPIC_API_KEY" in str(exc_info.value), (
        f"MissingAPIKeyError message must name ANTHROPIC_API_KEY; got: "
        f"{exc_info.value!s}"
    )


# ===========================================================================
# Category H — SDK exceptions wrapped as DecomposeAgentError (6 tests)
#
# Network failure, auth failure, rate limit, generic Exception — each
# wraps as `DecomposeAgentError` and propagates; original chained via
# `__cause__`; no silent swallow; no auto-retry. CLI catches and exits
# 12 (EXIT_AGENT_ERROR). MissingAPIKeyError is NOT re-wrapped (it has
# its own error class).
# ===========================================================================


def test_sdk_network_error_wraps_as_decompose_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated network failure (`ConnectionError`) -> wrapped as
    `DecomposeAgentError`."""
    import sm
    _seed_iteration()
    boom = ConnectionError("ECONNREFUSED 443")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.DecomposeAgentError):
        sm.decompose()


def test_sdk_auth_error_wraps_as_decompose_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated auth failure (custom Exception subclass) -> wrapped
    as `DecomposeAgentError`."""
    import sm
    _seed_iteration()

    class _FakeAuthError(Exception):
        pass

    boom = _FakeAuthError("401 invalid api key")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.DecomposeAgentError):
        sm.decompose()


def test_sdk_rate_limit_error_wraps_as_decompose_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated rate limit (custom Exception subclass) -> wrapped as
    `DecomposeAgentError`."""
    import sm
    _seed_iteration()

    class _FakeRateLimitError(Exception):
        pass

    boom = _FakeRateLimitError("429 rate-limited")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.DecomposeAgentError):
        sm.decompose()


def test_sdk_exception_original_chained_via_cause(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The original SDK exception is chained via `__cause__` on the
    `DecomposeAgentError` — the operator can trace the root cause."""
    import sm
    _seed_iteration()
    boom = ConnectionError("network down")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.DecomposeAgentError) as exc_info:
        sm.decompose()
    # The cause may be either the direct SDK exception OR a parse-error
    # error chain (if the empty string from a never-returned response
    # routed through parse_agent_json). Pin that __cause__ is non-None
    # and the original exception is reachable via the cause chain.
    err = exc_info.value
    chained = []
    cur = err
    while cur is not None:
        chained.append(cur)
        cur = cur.__cause__
    # Reachable from chain: either `boom` directly OR `boom` somewhere
    # up the chain. Accept either.
    types_in_chain = {type(c) for c in chained}
    assert ConnectionError in types_in_chain or err.__cause__ is boom, (
        f"expected the original ConnectionError to be reachable via "
        f"__cause__ chain on DecomposeAgentError; got chain types "
        f"{[t.__name__ for t in types_in_chain]!r}, direct cause: "
        f"{err.__cause__!r}"
    )


def test_sdk_exception_no_silent_swallow(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """When the SDK raises, `decompose()` MUST raise — not return None,
    not return an empty dict. Pin that an exception (not silent
    success) is the only allowed outcome."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch, raise_exc=RuntimeError("boom"))
    with pytest.raises(Exception):  # noqa: PT011 — pin "something raises"
        sm.decompose()


def test_sdk_exception_no_auto_retry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """On SDK failure, the seam is called exactly ONCE — no retries.
    Verified by counting fake-client constructions and create calls."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch, raise_exc=RuntimeError("boom"))
    with pytest.raises(Exception):  # noqa: PT011
        sm.decompose()
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly ONE fake-client construction (no retries); "
        f"got {len(_FakeAnthropicClient.instances)}"
    )
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1, (
        f"expected exactly ONE messages.create() call (no retries); "
        f"got {len(_FakeAnthropicClient.instances[0].messages.calls)}"
    )


def test_sdk_exception_does_not_write_log(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK failure -> log byte-for-byte unchanged."""
    import sm
    _seed_iteration()
    seeded = isolated_log.read_bytes()
    _install_fake_anthropic(monkeypatch, raise_exc=ConnectionError("dn"))
    with pytest.raises(Exception):  # noqa: PT011
        sm.decompose()
    assert isolated_log.read_bytes() == seeded


# ===========================================================================
# Category I — parse_agent_json failures still typed (5 tests)
#
# Malformed JSON output from the agent -> `DecomposeAgentError` (via
# `parse_agent_json`'s typed-error path, re-raised as
# `DecomposeOutputParseError` by `decompose`). This category re-asserts
# Story 4's typed-parse-error contract in the real-default context.
# ===========================================================================


def test_malformed_json_raises_decompose_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Agent returns non-JSON garbage -> `DecomposeAgentError` (or
    `DecomposeOutputParseError`, which subclasses it)."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch,
                            response_text="this is not valid JSON {{{")
    with pytest.raises(sm.DecomposeAgentError):
        sm.decompose()


def test_malformed_json_raises_decompose_output_parse_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Agent returns non-JSON -> the specific
    `DecomposeOutputParseError` subclass. Pinned because Iter 1's CLI
    handler branches on this class for the EXIT_JSON path."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch,
                            response_text="not json at all")
    with pytest.raises(sm.DecomposeOutputParseError):
        sm.decompose()


def test_empty_response_raises_decompose_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Agent returns the empty string -> typed parse error."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch, response_text="")
    with pytest.raises(sm.DecomposeAgentError):
        sm.decompose()


def test_malformed_json_does_not_write_log(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Parse failure -> log byte-for-byte unchanged. Failure invariant
    from Iter 1 Story 9, re-asserted under Story 6's real default."""
    import sm
    _seed_iteration()
    seeded = isolated_log.read_bytes()
    _install_fake_anthropic(monkeypatch, response_text="garbage{")
    with pytest.raises(sm.DecomposeAgentError):
        sm.decompose()
    assert isolated_log.read_bytes() == seeded


def test_decompose_output_parse_error_is_decompose_agent_error():
    """Story 4 rebased `DecomposeOutputParseError` to subclass
    `DecomposeAgentError`. Re-asserted under Story 6 because the CLI's
    exit-12 mapping depends on it."""
    import sm
    assert issubclass(sm.DecomposeOutputParseError, sm.DecomposeAgentError), (
        "DecomposeOutputParseError must subclass DecomposeAgentError so "
        "the CLI's exit-12 mapping covers parse failures from the real "
        "default's parse_agent_json call."
    )


# ===========================================================================
# Category J — Removed NotImplementedError (3 tests)
#
# `decompose()` with no args (or `spawn_agent=None`) no longer raises
# `NotImplementedError`. With a valid API key and a mocked SDK, it
# returns the appended entry. Without a key it raises
# `MissingAPIKeyError`. Either way: NOT `NotImplementedError`.
# ===========================================================================


def test_default_no_longer_raises_not_implemented_with_key(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`decompose()` with no args + valid API key + mocked SDK does NOT
    raise `NotImplementedError`. (It returns the entry.)"""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.decompose()
    except NotImplementedError as e:
        pytest.fail(
            f"Story 6: `decompose()` default must no longer raise "
            f"NotImplementedError; got: {e!s}"
        )


def test_default_no_longer_raises_not_implemented_without_key(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """`decompose()` with no args + NO API key raises
    `MissingAPIKeyError`, NOT `NotImplementedError`."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError):
        sm.decompose()
    # Belt-and-suspenders: confirm the raised type is not
    # NotImplementedError. `MissingAPIKeyError` subclasses ValueError;
    # NotImplementedError is a separate stdlib branch.
    try:
        sm.decompose()
    except NotImplementedError as e:
        pytest.fail(
            f"Story 6: missing-key path must raise MissingAPIKeyError, "
            f"not NotImplementedError; got: {e!s}"
        )
    except sm.MissingAPIKeyError:
        pass


def test_explicit_none_spawn_agent_no_longer_raises_not_implemented(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`decompose(spawn_agent=None)` is the same as omitting the kwarg
    — uses the real default, NOT `NotImplementedError`."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.decompose(spawn_agent=None)
    except NotImplementedError as e:
        pytest.fail(
            f"Story 6: `decompose(spawn_agent=None)` must fall through to "
            f"the real default, not raise NotImplementedError; got: {e!s}"
        )


# ===========================================================================
# Category K — Injectable callable still works (6 tests)
#
# Operator can still pass `spawn_agent=callable` for testing — the
# existing injectable contract from Iter 1 Story 9 is preserved. When
# a callable is injected: API key is NOT consulted; SDK is NOT called;
# the injected callable's return value is parsed as before.
# ===========================================================================


def test_injectable_callable_bypasses_real_default(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """Injecting a `spawn_agent` callable bypasses the real default —
    even with NO `ANTHROPIC_API_KEY` set, the call succeeds because the
    injected callable handles the spawn."""
    import sm
    _seed_iteration()

    def _spawn(role_spec_path, requirements):
        return json.dumps(_canonical_agent_output())

    # No SDK fake installed — the injected callable must NOT trigger
    # any SDK path.
    result = sm.decompose(spawn_agent=_spawn)
    assert result["type"] == "story_backlog"


def test_injectable_callable_does_not_construct_sdk_client(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Even with the SDK fake installed AND a valid API key, an injected
    callable means the fake SDK is never touched (no client
    construction, no create call)."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []

    def _spawn(role_spec_path, requirements):
        return json.dumps(_canonical_agent_output())

    sm.decompose(spawn_agent=_spawn)
    assert _FakeAnthropicClient.instances == [], (
        "expected NO fake-SDK construction when spawn_agent is "
        "injected; got constructed clients"
    )


def test_injectable_callable_receives_role_spec_path_and_requirements(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The injected callable receives `(role_spec_path: str,
    requirements: list)` — same signature as the real default. Iter 1
    Story 9's contract is preserved verbatim."""
    import sm
    seeded = _seed_iteration()

    captured: dict = {}

    def _spawn(role_spec_path, requirements):
        captured["role_spec_path"] = role_spec_path
        captured["requirements"] = list(requirements)
        return json.dumps(_canonical_agent_output())

    sm.decompose(spawn_agent=_spawn)
    assert isinstance(captured["role_spec_path"], str)
    assert captured["role_spec_path"].endswith("sm_agent.md")
    assert captured["requirements"] == seeded


def test_injectable_callable_return_value_parsed_as_before(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The injected callable's JSON-string return is parsed via
    `parse_agent_json` exactly like the real default's. Story 4's
    contract preserved."""
    import sm
    _seed_iteration()

    def _spawn(role_spec_path, requirements):
        return json.dumps(_canonical_agent_output(n=3))

    result = sm.decompose(spawn_agent=_spawn)
    assert len(result["stories"]) == 3


def test_injectable_callable_malformed_json_still_raises_parse_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """An injected callable returning malformed JSON raises
    `DecomposeOutputParseError` — same path as the real default."""
    import sm
    _seed_iteration()

    def _spawn(role_spec_path, requirements):
        return "not json"

    with pytest.raises(sm.DecomposeOutputParseError):
        sm.decompose(spawn_agent=_spawn)


def test_injectable_callable_exception_propagates(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """An injected callable that raises has its exception propagated
    verbatim — Iter 1 Story 9's contract (the caller decides whether
    to wrap or not)."""
    import sm
    _seed_iteration()

    class _SpawnFailure(RuntimeError):
        pass

    def _spawn(role_spec_path, requirements):
        raise _SpawnFailure("custom callable failure")

    with pytest.raises(_SpawnFailure):
        sm.decompose(spawn_agent=_spawn)


# ===========================================================================
# Category L — CLI end-to-end (4 tests)
#
# `python -m sm decompose` with valid env + mocked SDK exits 0 and
# persists a story_backlog entry. CLI is the operator surface — pin the
# end-to-end story-list-from-real-agent-default flow.
# ===========================================================================


def _build_subprocess_sitecustomize(tmp_path: pathlib.Path,
                                    response_payload: str) -> pathlib.Path:
    """Stage a `sitecustomize.py` under tmp_path that injects a fake
    `anthropic` module into `sys.modules` BEFORE `sm` imports it. Used
    by subprocess CLI tests so the real SDK is never touched.

    The fake module's `Anthropic` class is the same shape as the
    in-process fakes — constructor takes `api_key`, `.messages.create`
    returns a response with `.content[0].text == response_payload`.

    Returns the path to the tmp dir holding `sitecustomize.py`.
    """
    custom_dir = tmp_path / "sitecustomize_dir"
    custom_dir.mkdir(parents=True, exist_ok=True)
    # The response_payload must round-trip through Python source
    # safely. Use repr() to get a valid string literal.
    payload_literal = repr(response_payload)
    src = (
        "import sys, types\n"
        "class _Block:\n"
        "    def __init__(self, text):\n"
        "        self.text = text\n"
        "class _Resp:\n"
        "    def __init__(self, text):\n"
        "        self.content = [_Block(text)]\n"
        "class _Msgs:\n"
        "    def create(self, **kwargs):\n"
        f"        return _Resp({payload_literal})\n"
        "class _Client:\n"
        "    def __init__(self, api_key=None, **kwargs):\n"
        "        self.api_key = api_key\n"
        "        self.messages = _Msgs()\n"
        "fake = types.ModuleType('anthropic')\n"
        "fake.Anthropic = _Client\n"
        "sys.modules['anthropic'] = fake\n"
    )
    (custom_dir / "sitecustomize.py").write_text(src, encoding="utf-8")
    return custom_dir


def test_cli_decompose_with_mocked_sdk_exits_zero(tmp_path):
    """`python -m sm decompose` with valid env + mocked SDK exits 0."""
    # Stage tmp log
    log_path = tmp_path / "cli_log.jsonl"
    # Seed an iteration into the tmp log by spawning a subprocess that
    # calls `sm.build_entry` + `sm._append_entry`.
    seed_script = tmp_path / "seed.py"
    seed_script.write_text(
        "import os, sys\n"
        f"sys.path.insert(0, {str(PACKAGE_DIR)!r})\n"
        f"os.environ['SM_TEST_LOG_PATH'] = {str(log_path)!r}\n"
        "import sm\n"
        "from pathlib import Path\n"
        f"sm.LOG_PATH = Path({str(log_path)!r})\n"
        "reqs = [\n"
        "    {'requirement_id': f'req-{i}', 'title': f'T{i}',\n"
        "     'description': f'D{i}.', 'priority': 'MUST',\n"
        "     'acceptance_criteria': f'AC{i}'}\n"
        "    for i in range(1, 4)\n"
        "]\n"
        "e = sm.build_entry('iteration_open', {\n"
        "    'iteration_id': 'iter-cli-1',\n"
        "    'iteration_goal': 'cli test',\n"
        "    'requirements': reqs,\n"
        "})\n"
        "sm._append_entry(e)\n",
        encoding="utf-8",
    )
    seed_result = subprocess.run(
        [sys.executable, str(seed_script)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert seed_result.returncode == 0, (
        f"seed step failed: stdout={seed_result.stdout!r} "
        f"stderr={seed_result.stderr!r}"
    )

    payload = json.dumps(_canonical_agent_output(n=2))
    custom_dir = _build_subprocess_sitecustomize(tmp_path, payload)

    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(log_path)
    env["ANTHROPIC_API_KEY"] = "sk-cli-test"
    env["PYTHONPATH"] = (
        str(custom_dir) + os.pathsep + env.get("PYTHONPATH", "")
    )
    # Clear precedence-affecting overrides so the test sees the
    # default model + max_tokens.
    for n in ("SM_DECOMPOSE_MODEL", "SM_MODEL",
              "SM_DECOMPOSE_MAX_TOKENS", "SM_MAX_TOKENS"):
        env.pop(n, None)

    result = subprocess.run(
        [sys.executable, "-m", "sm", "decompose"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"`python -m sm decompose` must exit 0 with valid env + mocked "
        f"SDK; got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_decompose_with_mocked_sdk_persists_story_backlog(tmp_path):
    """After `python -m sm decompose`, the log carries a `story_backlog`
    entry. End-to-end ASSUMPTION-3 shape pinned."""
    log_path = tmp_path / "cli_log.jsonl"
    seed_script = tmp_path / "seed.py"
    seed_script.write_text(
        "import os, sys\n"
        f"sys.path.insert(0, {str(PACKAGE_DIR)!r})\n"
        f"os.environ['SM_TEST_LOG_PATH'] = {str(log_path)!r}\n"
        "import sm\n"
        "from pathlib import Path\n"
        f"sm.LOG_PATH = Path({str(log_path)!r})\n"
        "reqs = [\n"
        "    {'requirement_id': f'req-{i}', 'title': f'T{i}',\n"
        "     'description': f'D{i}.', 'priority': 'MUST',\n"
        "     'acceptance_criteria': f'AC{i}'}\n"
        "    for i in range(1, 4)\n"
        "]\n"
        "e = sm.build_entry('iteration_open', {\n"
        "    'iteration_id': 'iter-cli-2',\n"
        "    'iteration_goal': 'cli persist test',\n"
        "    'requirements': reqs,\n"
        "})\n"
        "sm._append_entry(e)\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(seed_script)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, (
        f"seed failed: stdout={r.stdout!r} stderr={r.stderr!r}"
    )

    payload = json.dumps(_canonical_agent_output(n=2))
    custom_dir = _build_subprocess_sitecustomize(tmp_path, payload)

    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(log_path)
    env["ANTHROPIC_API_KEY"] = "sk-cli-test-2"
    env["PYTHONPATH"] = (
        str(custom_dir) + os.pathsep + env.get("PYTHONPATH", "")
    )

    cli = subprocess.run(
        [sys.executable, "-m", "sm", "decompose"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert cli.returncode == 0, (
        f"CLI decompose failed: stdout={cli.stdout!r} stderr={cli.stderr!r}"
    )
    # Read the log and confirm there is a story_backlog entry.
    log_lines = [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    types_in_log = []
    for ln in log_lines:
        try:
            entry = json.loads(ln)
        except json.JSONDecodeError:
            continue
        types_in_log.append(entry.get("type"))
    assert "story_backlog" in types_in_log, (
        f"expected `story_backlog` entry in log after CLI decompose; "
        f"got types: {types_in_log!r}"
    )


def test_cli_decompose_missing_key_exits_twelve(tmp_path):
    """`python -m sm decompose` with NO `ANTHROPIC_API_KEY` exits 12
    (EXIT_AGENT_ERROR) — the CLI maps `MissingAPIKeyError` per Story 2."""
    log_path = tmp_path / "cli_log.jsonl"
    # Seed an iteration first so the CLI gets past the no-iteration
    # path and reaches the API-key check.
    seed_script = tmp_path / "seed.py"
    seed_script.write_text(
        "import os, sys\n"
        f"sys.path.insert(0, {str(PACKAGE_DIR)!r})\n"
        f"os.environ['SM_TEST_LOG_PATH'] = {str(log_path)!r}\n"
        "import sm\n"
        "from pathlib import Path\n"
        f"sm.LOG_PATH = Path({str(log_path)!r})\n"
        "reqs = [{'requirement_id': 'req-1', 'title': 'T1',\n"
        "         'description': 'D1.', 'priority': 'MUST',\n"
        "         'acceptance_criteria': 'AC1'}]\n"
        "e = sm.build_entry('iteration_open', {\n"
        "    'iteration_id': 'iter-cli-3',\n"
        "    'iteration_goal': 'cli no-key test',\n"
        "    'requirements': reqs,\n"
        "})\n"
        "sm._append_entry(e)\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(seed_script)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, (
        f"seed failed: stdout={r.stdout!r} stderr={r.stderr!r}"
    )

    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(log_path)
    env.pop("ANTHROPIC_API_KEY", None)

    cli = subprocess.run(
        [sys.executable, "-m", "sm", "decompose"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert cli.returncode == 12, (
        f"expected exit 12 (EXIT_AGENT_ERROR) on missing API key; got "
        f"returncode={cli.returncode}\n"
        f"stdout={cli.stdout!r}\nstderr={cli.stderr!r}"
    )


def test_cli_decompose_command_still_recognized_after_story_6(tmp_path):
    """`python -m sm decompose` is still a known subcommand after
    Story 6 — pin the CLI registration didn't drift."""
    log_path = tmp_path / "cli_log.jsonl"
    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(log_path)
    # Provide a key so we don't trip the no-key path; the call will
    # still fail (no iteration seeded), but it must NOT fail with
    # 'unknown command'.
    env["ANTHROPIC_API_KEY"] = "sk-recognition-test"

    result = subprocess.run(
        [sys.executable, "-m", "sm", "decompose"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True, text=True, timeout=30,
    )
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'decompose' after Story 6; "
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ===========================================================================
# Category M — Signature / contract invariants (4 tests)
#
# `decompose`'s public signature is unchanged: `(spawn_agent=None) ->
# dict`. The default value of `spawn_agent` may stay `None` (the body
# binds None -> real default), or may be the real default directly —
# either is acceptable. What's pinned: the kwarg name, the optional-
# ness, and the return-type contract.
# ===========================================================================


def test_decompose_signature_still_has_spawn_agent_kwarg():
    """`decompose` still accepts the `spawn_agent` keyword argument
    after Story 6."""
    import sm
    sig = inspect.signature(sm.decompose)
    assert "spawn_agent" in sig.parameters, (
        f"`decompose` must still accept `spawn_agent` kwarg; got params "
        f"{list(sig.parameters)!r}"
    )


def test_decompose_signature_spawn_agent_still_optional():
    """`spawn_agent` is still an optional keyword (has a default)."""
    import sm
    sig = inspect.signature(sm.decompose)
    p = sig.parameters["spawn_agent"]
    assert p.default is not inspect.Parameter.empty, (
        "`spawn_agent` must have a default — `decompose()` with no args "
        "must remain a legal call form"
    )


def test_decompose_is_still_public():
    """`decompose` is still public — no leading underscore, in __all__."""
    import sm
    assert not sm.decompose.__name__.startswith("_")
    assert "decompose" in sm.__all__


def test_decompose_returns_dict(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`decompose()` still returns a dict (the appended `story_backlog`
    entry) — Story 9's return-type contract is preserved through
    Story 6."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    result = sm.decompose()
    assert isinstance(result, dict), (
        f"expected dict return; got {type(result).__name__}"
    )


# ===========================================================================
# Category N — Static grep: default routes through provider seam (3 tests)
#
# `_default_decompose_spawn` body must NOT import `anthropic` directly,
# must reference `_invoke_anthropic`, and must call `resolve_api_key`,
# `resolve_model`, `resolve_max_tokens` (or arrange for them to be
# called). These are static checks so they fail loudly if the Coder
# forgets to route through Stories 2-5.
# ===========================================================================


def test_default_routes_through_invoke_anthropic_seam(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Runtime: invoking the default calls the provider seam (verified
    by the fake SDK being touched). If the Coder accidentally inlined
    `anthropic.Anthropic(...)` instead of calling `_invoke_anthropic`,
    a fake-SDK injection at `sys.modules['anthropic']` would still
    catch it — but this test pins that the seam is used by checking
    one call to `messages.create` round-tripped."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    # If the default routed through `_invoke_anthropic`, the fake
    # client was constructed and `messages.create` was called once.
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_default_resolves_api_key_at_call_time(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Two calls with two different `ANTHROPIC_API_KEY` values use the
    LATEST value for each — the default reads the env at call time, not
    at module-import time."""
    import sm
    _seed_iteration()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-key-A")
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    first_api_key = _FakeAnthropicClient.instances[0].api_key

    # Second call with a different key. Re-install the fake (clears the
    # instances list) and re-seed the iteration so derive_state has work.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-key-B")
    _seed_iteration(iteration_id="iter-2")
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    second_api_key = _FakeAnthropicClient.instances[0].api_key

    assert first_api_key == "sk-key-A"
    assert second_api_key == "sk-key-B"


def test_default_does_not_import_anthropic_directly():
    """Static check: the substring `import anthropic` / `from anthropic`
    inside the `_default_decompose_spawn` function body is zero. The
    seam is the only legitimate import site."""
    src = _read_sm_source()
    lines = src.splitlines()
    # Find the def line.
    def_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^def\s+_default_decompose_spawn\s*\(", line):
            def_idx = i
            break
    if def_idx is None:
        pytest.fail(
            "`_default_decompose_spawn` def not found at module-top-level "
            "in sm.py — Story 6's name contract is broken"
        )
    # Walk the body until we hit a line at column 0 that ISN'T blank or
    # a continuation — that ends the function body.
    body_lines = []
    for j in range(def_idx + 1, len(lines)):
        ln = lines[j]
        if ln.strip() == "":
            body_lines.append(ln)
            continue
        if not ln.startswith((" ", "\t")):
            # Hit module-scope — function body ended.
            break
        body_lines.append(ln)
    body_text = "\n".join(body_lines)
    for body_ln in body_text.splitlines():
        code = body_ln.split("#", 1)[0]
        if re.search(r"\b(import\s+anthropic|from\s+anthropic)\b", code):
            pytest.fail(
                f"`_default_decompose_spawn` body has a direct `anthropic` "
                f"import: {body_ln!r}. The default must route through "
                f"`_invoke_anthropic`, not import the SDK itself."
            )


# ===========================================================================
# Category O — Message shape (4 tests)
#
# The `messages` list reaching the SDK is a list with at least one
# user-turn message. The role-spec + requirements both end up in the
# message content. Exact framing (single user message vs multi-message,
# system vs user role for the spec) is the Coder's call — these tests
# pin the loose contract.
# ===========================================================================


def test_messages_arg_is_a_list(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`messages` reaching the SDK is a list."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    assert isinstance(call["messages"], list), (
        f"messages must be a list; got {type(call['messages']).__name__}"
    )


def test_messages_arg_is_non_empty(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`messages` reaching the SDK is non-empty (at least one turn)."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    assert len(call["messages"]) >= 1, (
        f"messages must have at least one turn; got {call['messages']!r}"
    )


def test_messages_have_user_role(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """At least one message has `role='user'`. The spec says 'single
    user message' — pin the user role appears."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    roles = [m.get("role") for m in call["messages"]]
    assert "user" in roles, (
        f"expected at least one user-role message; got roles {roles!r}"
    )


def test_messages_carry_both_spec_and_requirements(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The user message bundles BOTH the role-spec content AND the
    requirements list. Per Story 6 spec: 'a single user message' carries
    'role spec text + requirements list'."""
    import sm
    _seed_iteration()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    call = _captured_create_call()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_role_spec_text().strip()
    # Role spec must appear.
    assert role_text in msg_text, (
        "role spec content missing from messages"
    )
    # At least one seeded requirement id must appear.
    assert "req-1" in msg_text, (
        "requirements missing from messages"
    )


# ===========================================================================
# Category P — No active iteration still fails before SDK touched (3 tests)
#
# Pre-existing Iter 1 contract: no active iteration -> ValueError.
# Story 6 must preserve this — the SDK is not touched, the log is
# unchanged, the API key is not even resolved (or if resolved, the SDK
# isn't called).
# ===========================================================================


def test_no_iteration_raises_value_error_with_real_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Empty log + real default -> `ValueError("no active iteration; ...")`."""
    import sm
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(ValueError):
        sm.decompose()


def test_no_iteration_does_not_call_sdk_with_real_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """No active iteration -> SDK is never touched (no fake-client
    construction)."""
    import sm
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []
    with pytest.raises(ValueError):
        sm.decompose()
    assert _FakeAnthropicClient.instances == [], (
        "no-iteration path must not touch the SDK; got constructed clients"
    )


def test_no_iteration_does_not_write_log_with_real_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """No-iteration failure leaves the log byte-for-byte unchanged
    (under the real default)."""
    import sm
    # Seed a non-iteration entry so the log is non-empty.
    seed = sm.build_entry("decompose_real_spawn_test_seed",
                          {"marker": "story6"})
    sm._append_entry(seed)
    seeded_bytes = isolated_log.read_bytes()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(ValueError):
        sm.decompose()
    assert isolated_log.read_bytes() == seeded_bytes
