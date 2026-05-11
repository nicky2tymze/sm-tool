"""Iter 2 Story 8 — Real `spawn_coder` default in `execute`.

This file pins the contract of `execute`'s real (non-injected) Coder
spawn default — the THIRD linchpin of Iter 2 (after Story 6 wired
`decompose` and Story 7 wired the TestWriter stage). Stories 1-5 wired
the pieces (anthropic dep, resolve_api_key, resolve_model,
resolve_max_tokens, parse_agent_json, _invoke_anthropic provider seam);
Story 6 wired them through `decompose`; Story 7 wired them through
`execute`'s `spawn_test_writer`; Story 8 wires them through `execute`'s
`spawn_coder` — and ONLY `spawn_coder`. `spawn_reviewer` stays
`None`-defaults-to-NotImplementedError until Story 9.

Pinned clauses (Story 8 acceptance, paraphrased from Stories_v1.md):

  1. Replaces the `NotImplementedError` default for `spawn_coder` in
     `execute` with a real implementation matching the existing
     injectable-callable signature
     `(role_spec_path: str, story: dict, test_code: str) -> str` exactly
     — no signature drift, no downstream ripple.
  2. The real default reads `roles/coder.md` via Iter 1 Story 8's
     `resolve_role_spec` (the caller passes the resolved path; the
     default reads it), packages it plus the active story's dict (as
     JSON) plus the test_code string into a single user message, calls
     the provider seam (Story 5) with `resolve_model("coder")` and
     `resolve_max_tokens("coder")`, then RETURNS THE RAW TEXT
     UNMODIFIED — Coder returns implementation code, not JSON, so the
     response does NOT route through `parse_agent_json`. Same shape as
     Story 7's TestWriter default.
  3. On SDK-level failure (network, auth, rate-limit, generic Exception
     that is NOT MissingAPIKeyError / ConfigError / CoderAgentError),
     the exception wraps as `CoderAgentError` with the original chained
     via `__cause__`. `MissingAPIKeyError` and `ConfigError` propagate
     UNCHANGED so the CLI maps `MissingAPIKeyError` to exit 12 and
     config errors are diagnosable.
  4. No auto-retry — exactly one SDK call per coder-stage invocation
     inside `execute`.
  5. End-to-end (with mocked SDK): `execute <story_id>` against an
     active iteration with a story_backlog and a cut sprint, with NO
     injected `spawn_test_writer` and NO injected `spawn_coder` (and
     operator-injected `spawn_reviewer` because reviewer stays
     None-defaults to NotImplementedError until Story 9), drives the
     pipeline test_writer (default) -> coder (default) -> reviewer
     (injected stub); Story 9 is NOT pre-tested here.

CONTRACT INTERPRETATION (locked by TestWriter):

  - PRIVATE name: the real default is `_default_execute_coder_spawn`
    at module scope on `sm`. NOT in `sm.__all__`. The four spawn defaults
    are internal implementation; only their wired-up signatures are
    public surface.
  - Signature is `_default_execute_coder_spawn(role_spec_path: str,
    story: dict, test_code: str) -> str` — exact match with the
    existing injectable-callable signature pinned by Iter 1 Story 23.
  - The default reads `role_spec_path` content from disk (the caller in
    `execute` already calls `resolve_role_spec("coder")` and passes the
    path; the default reads it). Mirrors Story 6 / Story 7 shape.
  - Message shape: a single user-turn message whose `content` is a
    string that contains ALL THREE pieces — the role-spec text, the
    story dict (as JSON), AND the test_code — plus an instruction to
    return implementation code. Exact framing is the Coder's call;
    tests verify all three pieces appear in the message content.
  - Model/max_tokens are read at call time via `resolve_model("coder")`
    and `resolve_max_tokens("coder")` — so env-var overrides
    (`SM_CODER_MODEL`, `SM_CODER_MAX_TOKENS`) are honored on every call.
    No caching.
  - API key is read via `resolve_api_key()` — so a missing key raises
    `MissingAPIKeyError` before any SDK work.
  - Provider-seam invocation: the default calls `_invoke_anthropic(
    messages=..., model=..., max_tokens=..., api_key=...)`. Anthropic
    SDK is NOT imported by the default itself — only by the seam.
  - Return value: the default returns the SDK seam's response string
    AS-IS. The caller (`execute`) writes a `coder_output` entry with
    the raw `output` field set to the returned string. No
    `parse_agent_json` call — Coder returns implementation code.
  - SDK exception wrapping: when `_invoke_anthropic` raises (network /
    auth / rate-limit / generic Exception that is NOT a
    `MissingAPIKeyError` / `ConfigError` and NOT already a
    `CoderAgentError`), the default wraps it as a `CoderAgentError`
    with the original chained via `__cause__`. `MissingAPIKeyError`
    and `ConfigError` propagate unchanged.
  - No auto-retry: one SDK call per coder-stage invocation inside
    `execute`.
  - Caller-bind contract: `execute` falls back to the real default by
    looking up `_default_execute_coder_spawn` on
    `sys.modules[__name__]` so monkeypatches in tests
    (`monkeypatch.setattr(sm, "_default_execute_coder_spawn", ...)`)
    take effect. Mirrors Story 6 / Story 7's pattern.
  - Injectable callable preserved: `execute(spawn_coder=callable, ...)`
    continues to bypass the real default entirely. No regression on
    Iter 1 Story 23's injectable contract.

CRITICAL — tests must NOT make real API calls. Every test that triggers
the default path injects a fake `anthropic` module into `sys.modules`
via `monkeypatch.setitem` BEFORE the call. The lazy import inside
`_invoke_anthropic` finds the fake and never touches the real SDK.

Note: because reaching the coder stage requires the test_writer stage
to succeed first, tests that exercise the coder default ALSO need a
test_writer source. The default test_writer (Story 7) ALSO uses the
fake SDK, so the fake will be called TWICE per execute() invocation:
once for the test_writer stage and once for the coder stage. Helpers
in this file account for both calls explicitly.

Iter 1 cascade note: `test_execute.py` has a test
(`test_default_only_coder_missing_raises`) that pins the OLD
`NotImplementedError` default for `spawn_coder`. The Coder resolves
that cascade per Iter 2's behavior-preserving update pattern; this
file does NOT modify it (anti-lane). See the cascade list in the final
report for line numbers.
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
# Fake Anthropic SDK — mirrors Story 7's tests. Installed into sys.modules
# per-test.
#
# Story 8 wrinkle: reaching the coder stage means the test_writer default
# fires first. Both stages route through the same fake SDK. We support
# both:
#   - a "shared" fake that returns the SAME text for every .create() call
#     (good enough when tests don't differentiate test_writer vs coder
#     responses);
#   - a "scripted" fake that returns a SEQUENCE of texts so test_writer
#     and coder responses can be distinguished.
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    """Minimal stand-in for an Anthropic content block. Carries `.text`."""

    def __init__(self, text: str = "def foo(): return 1\n"):
        self.text = text


class _FakeResponse:
    """Stand-in for the object returned by `client.messages.create(...)`.

    Mirrors the real Messages API: `.content` is a list of content
    blocks; each block has a `.text` attribute.
    """

    def __init__(self, text: str = "def foo(): return 1\n"):
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    """Stand-in for `client.messages` — the `.create` subobject. Records
    every call into `self.calls` as a dict of kwargs.

    If `response_sequence` is given, returns successive responses for
    each call (used when test_writer and coder responses differ). If
    exhausted, falls back to the last response.
    """

    def __init__(self, response=None, raise_exc=None,
                 response_sequence=None):
        self._response = response or _FakeResponse()
        self._raise_exc = raise_exc
        self._sequence = list(response_sequence) if response_sequence else None
        self._sequence_idx = 0
        self.calls: list = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._sequence is not None:
            if self._sequence_idx < len(self._sequence):
                resp = self._sequence[self._sequence_idx]
                self._sequence_idx += 1
                return resp
            # Sequence exhausted -> last response is sticky.
            return self._sequence[-1]
        return self._response


class _FakeAnthropicClient:
    """Stand-in for `anthropic.Anthropic`. Records the `api_key` it was
    constructed with and exposes a `.messages` subobject that records
    every `.create(...)` call.

    NOTE: a single `execute()` invocation that uses BOTH the test_writer
    AND coder defaults will construct the client TWICE (once per stage
    — each default calls `_invoke_anthropic` which constructs a fresh
    client). The fake-client class tracks every construction in
    `instances`.
    """

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

_DEFAULT_IMPL_CODE = (
    "def canonical_thing():\n"
    "    return True\n"
)


def _install_fake_anthropic(monkeypatch,
                            response_text=None,
                            raise_exc=None,
                            response_sequence=None,
                            raise_on_call=None):
    """Build a fake `anthropic` module and install it into `sys.modules`.

    `response_text` controls the `.text` returned by every
    `client.messages.create(...)` call. If None and no sequence,
    `_DEFAULT_IMPL_CODE` is used.

    `response_sequence`, if set, is a list of response TEXT strings
    returned one-per-call in order (sticky on the last after
    exhaustion). Use this when test_writer + coder responses need to
    differ.

    `raise_exc`, if set, causes EVERY `.create(...)` call to raise the
    given exception.

    `raise_on_call`, if set, is the 1-based call index at which to
    raise `raise_exc`. Used to make ONLY the coder-stage call fail
    while the test_writer-stage call succeeds.

    NOTE: clears `_FakeAnthropicClient.instances` so each test starts
    with a clean record.
    """
    _FakeAnthropicClient.instances = []

    if response_sequence is not None:
        # Sequence mode: per-stage responses.
        seq_responses = [_FakeResponse(t) for t in response_sequence]
    else:
        seq_responses = None

    if response_text is None and seq_responses is None:
        response_text = _DEFAULT_IMPL_CODE

    # Track global call index across BOTH client constructions inside one
    # execute() invocation (test_writer then coder).
    call_state = {"global_idx": 0}

    class _BoundClient(_FakeAnthropicClient):
        def __init__(self, api_key=None, **kwargs):
            super().__init__(api_key=api_key, **kwargs)

            # Build per-construction messages object that consults the
            # shared call_state so raise_on_call works across stages.
            outer_seq = seq_responses
            outer_text = response_text
            outer_raise = raise_exc
            outer_raise_on = raise_on_call

            class _StageMessages(_FakeMessages):
                def create(self_inner, **kwargs):
                    call_state["global_idx"] += 1
                    idx = call_state["global_idx"]
                    self_inner.calls.append(kwargs)
                    # Selective raise: only fire on the chosen call.
                    if outer_raise_on is not None and outer_raise is not None:
                        if idx == outer_raise_on:
                            raise outer_raise
                        # Other calls behave normally.
                    elif outer_raise is not None:
                        # Unconditional raise.
                        raise outer_raise
                    if outer_seq is not None:
                        si = call_state["global_idx"] - 1
                        if si < len(outer_seq):
                            return outer_seq[si]
                        return outer_seq[-1]
                    return _FakeResponse(outer_text)

            self.messages = _StageMessages()

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
    succeeds."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-12345")
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
        "iteration_goal": "Story 8 test iteration",
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


def _make_test_writer(test_code: str = _DEFAULT_TEST_CODE,
                      record=None):
    """Build a spawn_test_writer stub. Used to fast-path the test_writer
    stage when a test only wants to exercise the coder default — saves
    one SDK round-trip and lets us isolate per-stage assertions."""
    def _spawn(role_spec_path, story):
        if record is not None:
            record.setdefault("test_writer_calls", []).append({
                "role_spec_path": role_spec_path,
                "story": story,
            })
        return test_code
    return _spawn


def _make_reviewer(approved: bool = True,
                   test_result: str = "12 of 12 passed",
                   record=None):
    """Build a spawn_reviewer stub. Reviewer stays None-default-to-
    NotImplementedError under Story 8, so every coder-stage test
    injects one."""
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


def _read_coder_role_spec_text() -> str:
    """Return the canonical coder.md role-spec content."""
    return (SOURCE_ROLES_DIR / "coder.md").read_text(encoding="utf-8")


def _captured_coder_call() -> dict:
    """Return the kwargs of the coder-stage `messages.create(...)` call.

    Asserts the fake SDK was constructed exactly twice (once per stage)
    AND that each construction made exactly one `.create` call. Returns
    the SECOND `.create` call's kwargs (the coder-stage call).

    This pin enforces the no-retry contract per stage AND the
    per-stage client-construction contract Stories 5/7/8 share.
    """
    assert len(_FakeAnthropicClient.instances) == 2, (
        f"expected exactly TWO fake-client constructions (one per stage: "
        f"test_writer then coder); got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    tw_calls = _FakeAnthropicClient.instances[0].messages.calls
    cd_calls = _FakeAnthropicClient.instances[1].messages.calls
    assert len(tw_calls) == 1, (
        f"expected exactly one test_writer create() call; got {len(tw_calls)}"
    )
    assert len(cd_calls) == 1, (
        f"expected exactly one coder create() call; got {len(cd_calls)}"
    )
    return cd_calls[0]


def _captured_coder_call_with_injected_tw() -> dict:
    """Variant of `_captured_coder_call` for tests that inject a stub
    test_writer (so the fake SDK is only constructed ONCE — for the
    coder stage).
    """
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly ONE fake-client construction (only coder stage "
        f"uses the SDK; test_writer was injected); got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    cd_calls = _FakeAnthropicClient.instances[0].messages.calls
    assert len(cd_calls) == 1, (
        f"expected exactly one coder create() call; got {len(cd_calls)}"
    )
    return cd_calls[0]


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
# `_default_execute_coder_spawn` exists on the module, is PRIVATE, is
# NOT in `sm.__all__`, is callable, has the right signature (3 params).
# ===========================================================================


def test_default_coder_spawn_exists_on_module():
    """`sm._default_execute_coder_spawn` is defined at module scope."""
    import sm
    assert hasattr(sm, "_default_execute_coder_spawn"), (
        "expected `_default_execute_coder_spawn` to be defined on the sm "
        "module; missing from dir(sm)="
        f"{sorted(n for n in dir(sm) if 'coder' in n.lower())!r}"
    )


def test_default_coder_spawn_is_callable():
    """`sm._default_execute_coder_spawn` is callable."""
    import sm
    obj = getattr(sm, "_default_execute_coder_spawn", None)
    assert callable(obj), (
        f"expected `sm._default_execute_coder_spawn` to be callable; "
        f"got {type(obj).__name__}"
    )


def test_default_coder_spawn_is_private_name():
    """The default is `_default_execute_coder_spawn` (leading
    underscore)."""
    import sm
    assert hasattr(sm, "_default_execute_coder_spawn"), (
        "expected the private name `_default_execute_coder_spawn` to "
        "exist"
    )
    assert not hasattr(sm, "default_execute_coder_spawn"), (
        "expected no public `default_execute_coder_spawn`; the default "
        "is private."
    )


def test_default_coder_spawn_not_in_all():
    """`_default_execute_coder_spawn` is NOT in `sm.__all__`."""
    import sm
    assert "_default_execute_coder_spawn" not in sm.__all__, (
        f"`_default_execute_coder_spawn` must NOT be in sm.__all__; "
        f"got {sm.__all__!r}"
    )


def test_default_coder_spawn_signature_three_positional_params():
    """`_default_execute_coder_spawn` accepts (role_spec_path, story,
    test_code) — THREE positional parameters matching the injectable
    signature pinned by Iter 1 Story 23 (coder takes test_code unlike
    test_writer)."""
    import sm
    sig = inspect.signature(sm._default_execute_coder_spawn)
    params = list(sig.parameters.values())
    assert len(params) >= 3, (
        f"expected at least 3 parameters (role_spec_path, story, "
        f"test_code); got signature {sig!s}"
    )


def test_default_coder_spawn_signature_parameter_names():
    """Parameter names are exactly `role_spec_path`, `story`, `test_code`
    in that order — exact match with the injectable signature."""
    import sm
    sig = inspect.signature(sm._default_execute_coder_spawn)
    names = list(sig.parameters)[:3]
    assert names == ["role_spec_path", "story", "test_code"], (
        f"_default_execute_coder_spawn parameter names must be "
        f"['role_spec_path', 'story', 'test_code']; got {names!r}"
    )


# ===========================================================================
# Category B — Happy path with mocked SDK (8 tests)
#
# Default fires for BOTH test_writer (Story 7) AND coder (Story 8). Two
# SDK round-trips per execute(). The `coder_output` entry carries the
# coder-stage SDK response verbatim.
# ===========================================================================


def test_happy_path_default_coder_returns_final_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>)` with NO spawn_test_writer / NO spawn_coder but
    with reviewer injected returns the final state-change entry. Both
    test_writer + coder defaults fire under the same fake SDK."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    result = sm.execute(in_sprint[0],
                        spawn_reviewer=_make_reviewer())
    assert isinstance(result, dict), (
        f"expected dict return; got {type(result).__name__}"
    )


def test_happy_path_default_coder_writes_coder_output_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The real coder default fires -> `coder_output` entry is appended
    with the SDK response as `output`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=[
                                _DEFAULT_TEST_CODE,
                                _DEFAULT_IMPL_CODE,
                            ])
    sm.execute(in_sprint[0],
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"]
    assert len(co) == 1, (
        f"expected exactly one coder_output entry; got {len(co)}"
    )
    assert co[0]["output"] == _DEFAULT_IMPL_CODE, (
        f"expected coder_output.output to be the coder-stage SDK "
        f"response verbatim; got {co[0]['output']!r}"
    )


def test_happy_path_default_coder_calls_sdk_for_coder_stage(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """One `execute()` with both defaults -> exactly TWO fake-client
    constructions (one per stage) and TWO `.create` calls total (one
    each). No retries."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_reviewer=_make_reviewer())
    assert len(_FakeAnthropicClient.instances) == 2, (
        f"expected exactly two fake-client constructions (test_writer + "
        f"coder); got {len(_FakeAnthropicClient.instances)}"
    )
    for i, inst in enumerate(_FakeAnthropicClient.instances):
        assert len(inst.messages.calls) == 1, (
            f"client #{i} should have exactly one create() call; got "
            f"{len(inst.messages.calls)}"
        )


def test_happy_path_default_coder_constructs_client_with_resolved_api_key(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The api_key the coder-stage seam constructs the client with is
    the value of `ANTHROPIC_API_KEY`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-roundtrip-coder-99999")
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_reviewer=_make_reviewer())
    # Both stages should receive the same api_key.
    assert _FakeAnthropicClient.instances[1].api_key == \
        "sk-roundtrip-coder-99999"


def test_happy_path_default_coder_response_flows_to_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The coder-stage SDK response text flows VERBATIM into
    spawn_reviewer's `impl_code` arg — confirming the default returns
    raw text without parsing."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel_impl = "# SENTINEL-CODER-RAW-9b3c1f4e2d\ndef impl_x(): pass\n"
    _install_fake_anthropic(monkeypatch,
                            response_sequence=[
                                _DEFAULT_TEST_CODE,
                                sentinel_impl,
                            ])
    record = {}
    sm.execute(in_sprint[0],
               spawn_reviewer=_make_reviewer(record=record))
    assert "reviewer_calls" in record
    assert record["reviewer_calls"][0]["impl_code"] == sentinel_impl, (
        f"expected coder SDK response to flow verbatim to spawn_reviewer; "
        f"got {record['reviewer_calls'][0]['impl_code']!r}"
    )


def test_happy_path_default_coder_entry_has_role_spec_path(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`coder_output` entry carries the resolved role-spec path
    pointing at coder.md."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"][0]
    assert "role_spec_path" in co
    assert isinstance(co["role_spec_path"], str)
    assert "coder" in co["role_spec_path"], (
        f"role_spec_path must reference coder; got {co['role_spec_path']!r}"
    )


def test_happy_path_default_coder_entry_has_role_spec_hash(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`coder_output` entry carries the role-spec hash."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"][0]
    assert "role_spec_hash" in co
    assert isinstance(co["role_spec_hash"], str)
    assert co["role_spec_hash"] != "", (
        "role_spec_hash must be non-empty"
    )


def test_happy_path_default_coder_entry_carries_story_id(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`coder_output` entry carries the story_id."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    target_id = in_sprint[0]
    sm.execute(target_id,
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"][0]
    assert co["story_id"] == target_id


# ===========================================================================
# Category C — Role spec read from roles/coder.md (5 tests)
#
# The default reads the role-spec markdown file (via the path it
# receives) and includes its content in the user message.
# ===========================================================================


def test_role_spec_content_appears_in_coder_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The text of `roles/coder.md` appears in the coder-stage user
    message content reaching the SDK."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    # Inject test_writer stub so the only SDK call is the coder stage.
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_coder_role_spec_text()
    first_line = next(
        (ln.strip() for ln in role_text.splitlines() if ln.strip()),
        None,
    )
    assert first_line is not None, (
        "coder.md is empty or all-blank — fixture invariant broken"
    )
    assert first_line in msg_text, (
        f"expected role-spec excerpt {first_line!r} in coder message "
        f"content; message starts: {msg_text[:200]!r}"
    )


def test_role_spec_full_content_appears_in_coder_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The ENTIRE coder.md content (verbatim) appears somewhere in the
    coder-stage message — pins that the default reads + injects the
    full file."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_coder_role_spec_text().strip()
    assert role_text in msg_text, (
        f"expected full coder.md role-spec content in coder message; "
        f"first 200 chars of role spec: {role_text[:200]!r}; first 400 "
        f"chars of message: {msg_text[:400]!r}"
    )


def test_role_spec_file_read_from_resolved_path(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """When `resolve_role_spec("coder")` points at a custom file (via
    the staged tmp roles dir), THAT file's content reaches the
    coder-stage message. Verified by writing a sentinel string into the
    staged coder.md."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel = "SENTINEL-STORY-8-CODER-MARKER-5f7e3a"
    staged = isolated_log.parent / "roles" / "coder.md"
    staged.write_text(sentinel + "\nrest of spec...", encoding="utf-8")
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    msg_text = _flatten_message_content(call["messages"])
    assert sentinel in msg_text, (
        f"expected sentinel {sentinel!r} (from staged coder.md) in coder "
        f"message content; default may have read a different file. "
        f"Message: {msg_text[:400]!r}"
    )


def test_role_spec_path_passed_into_coder_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The coder default's `role_spec_path` argument is the absolute
    path to `roles/coder.md`. Verified by spying on the default
    itself."""
    import sm

    captured: dict = {}
    original_default = sm._default_execute_coder_spawn

    def _spy(role_spec_path, story, test_code):
        captured["role_spec_path"] = role_spec_path
        captured["story"] = dict(story)
        captured["test_code"] = test_code
        return original_default(role_spec_path, story, test_code)

    monkeypatch.setattr(sm, "_default_execute_coder_spawn", _spy)
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    assert "role_spec_path" in captured, (
        "expected spy to record role_spec_path"
    )
    path = captured["role_spec_path"]
    assert isinstance(path, str), (
        f"role_spec_path arg must be a string; got {type(path).__name__}"
    )
    assert path.endswith("coder.md"), (
        f"expected path to end with 'coder.md'; got {path!r}"
    )


def test_role_spec_read_failure_propagates_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """If the coder role-spec file is missing/unreadable when the
    default tries to read it, the error propagates. Simulated by
    deleting the staged coder.md just before the default body runs."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    real_default = sm._default_execute_coder_spawn

    def _delete_then_call(role_spec_path, story, test_code):
        try:
            os.remove(role_spec_path)
        except OSError:
            pass
        return real_default(role_spec_path, story, test_code)

    monkeypatch.setattr(sm, "_default_execute_coder_spawn",
                        _delete_then_call)
    # Either FileNotFoundError or CoderAgentError is acceptable — the
    # spec wraps SDK exceptions, but the file read is BEFORE the SDK
    # call.
    with pytest.raises(Exception):  # noqa: PT011
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())


# ===========================================================================
# Category D — Story dict + test_code bundled in SDK message (6 tests)
#
# The full story dict from the backlog AND the test_writer's test_code
# are both included in the user message.
# ===========================================================================


def test_story_dict_appears_in_coder_message_by_id(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `story_id` reaches the coder user message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    target_id = in_sprint[0]
    sm.execute(target_id,
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    msg_text = _flatten_message_content(call["messages"])
    assert target_id in msg_text, (
        f"expected story_id {target_id!r} in coder message content; "
        f"message: {msg_text[:500]!r}"
    )


def test_story_dict_title_appears_in_coder_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `title` reaches the coder user message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    msg_text = _flatten_message_content(call["messages"])
    assert "Story 1" in msg_text, (
        f"expected story title 'Story 1' in coder message; message: "
        f"{msg_text[:500]!r}"
    )


def test_story_dict_acceptance_criteria_in_coder_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `acceptance_criteria` reaches the coder user
    message — the agent needs the AC to know what to implement."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    msg_text = _flatten_message_content(call["messages"])
    assert "Story 1 must pass its tests." in msg_text, (
        f"expected acceptance_criteria text in coder message; message: "
        f"{msg_text[:500]!r}"
    )


def test_test_code_appears_in_coder_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The test_writer's `test_code` string reaches the coder user
    message — the agent needs the failing tests to know what to
    implement against."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel_tests = (
        "# SENTINEL-TC-TO-CODER-3fa9d7e8b1\n"
        "def test_unique_marker():\n"
        "    assert sm_thing() == 42\n"
    )
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(test_code=sentinel_tests),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    msg_text = _flatten_message_content(call["messages"])
    assert sentinel_tests in msg_text, (
        f"expected test_code to appear verbatim in coder message; "
        f"message: {msg_text[:600]!r}"
    )


def test_test_code_passed_to_coder_default_arg(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The coder default receives the test_writer's output as its
    `test_code` arg. Verified by spying on the default."""
    import sm
    captured: dict = {}
    real_default = sm._default_execute_coder_spawn
    sentinel_tests = "# SPY-TC-MARKER\ndef test_x(): pass\n"

    def _spy(role_spec_path, story, test_code):
        captured["test_code"] = test_code
        return real_default(role_spec_path, story, test_code)

    monkeypatch.setattr(sm, "_default_execute_coder_spawn", _spy)
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(test_code=sentinel_tests),
               spawn_reviewer=_make_reviewer())
    assert captured["test_code"] == sentinel_tests, (
        f"expected test_code spy to capture the test_writer output; got "
        f"{captured.get('test_code')!r}"
    )


def test_story_dict_passed_to_coder_default_arg(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The coder default receives the active story dict as its `story`
    arg. Verified by spying on the default."""
    import sm
    captured: dict = {}
    real_default = sm._default_execute_coder_spawn

    def _spy(role_spec_path, story, test_code):
        captured["story"] = dict(story)
        return real_default(role_spec_path, story, test_code)

    monkeypatch.setattr(sm, "_default_execute_coder_spawn", _spy)
    _, in_sprint, _ = _seed_sprint()
    target_id = in_sprint[0]
    _install_fake_anthropic(monkeypatch)
    sm.execute(target_id,
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    assert captured["story"]["story_id"] == target_id
    assert captured["story"]["title"] == "Story 1"
    assert captured["story"]["size"] == "S"


# ===========================================================================
# Category E — resolve_model("coder") wired (4 tests)
#
# With SM_CODER_MODEL override set, the coder-stage SDK call receives
# that model id. With override unset, the default reaches the SDK.
# ===========================================================================


def test_resolve_model_coder_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_CODER_MODEL=custom-coder-model` -> that exact string reaches
    the coder-stage `messages.create(model=...)`."""
    import sm
    monkeypatch.setenv("SM_CODER_MODEL", "custom-coder-model-v9")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    assert call["model"] == "custom-coder-model-v9", (
        f"expected model='custom-coder-model-v9' to reach coder-stage "
        f"SDK; got {call.get('model')!r}"
    )


def test_resolve_model_global_override_reaches_sdk_for_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With per-spawn unset and `SM_MODEL=global-id` set, the global
    fallback reaches the coder-stage SDK."""
    import sm
    monkeypatch.delenv("SM_CODER_MODEL", raising=False)
    monkeypatch.setenv("SM_MODEL", "global-fallback-model-coder")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    assert call["model"] == "global-fallback-model-coder"


def test_resolve_model_default_reaches_sdk_for_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With no env-var overrides, the documented Haiku 4.5 default
    reaches the coder-stage SDK. Pinned via `resolve_model('coder')` so
    the test stays decoupled from the constant's literal value."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    expected = sm.resolve_model("coder")
    assert call["model"] == expected, (
        f"expected default model {expected!r} to reach coder-stage SDK; "
        f"got {call.get('model')!r}"
    )


def test_resolve_model_per_spawn_beats_global_for_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With BOTH `SM_CODER_MODEL` and `SM_MODEL` set, the per-spawn
    var wins for the coder stage."""
    import sm
    monkeypatch.setenv("SM_CODER_MODEL", "per-spawn-wins-coder")
    monkeypatch.setenv("SM_MODEL", "global-loses-coder")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    assert call["model"] == "per-spawn-wins-coder"


# ===========================================================================
# Category F — resolve_max_tokens("coder") wired (4 tests)
# ===========================================================================


def test_resolve_max_tokens_coder_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_CODER_MAX_TOKENS=2048` -> 2048 (int) reaches the coder-stage
    SDK."""
    import sm
    monkeypatch.setenv("SM_CODER_MAX_TOKENS", "2048")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    assert call["max_tokens"] == 2048
    assert isinstance(call["max_tokens"], int), (
        f"max_tokens must be int (not str); got "
        f"{type(call['max_tokens']).__name__}"
    )


def test_resolve_max_tokens_global_override_reaches_sdk_for_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With per-spawn unset and `SM_MAX_TOKENS=8192` set, the global
    fallback reaches the coder-stage SDK as an int."""
    import sm
    monkeypatch.delenv("SM_CODER_MAX_TOKENS", raising=False)
    monkeypatch.setenv("SM_MAX_TOKENS", "8192")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    assert call["max_tokens"] == 8192


def test_resolve_max_tokens_default_reaches_sdk_for_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With no env-var overrides, `resolve_max_tokens('coder')`'s
    default (4096 per Story 3) reaches the coder-stage SDK."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    expected = sm.resolve_max_tokens("coder")
    assert call["max_tokens"] == expected
    assert call["max_tokens"] == 4096


def test_resolve_max_tokens_per_spawn_beats_global_for_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With BOTH `SM_CODER_MAX_TOKENS` and `SM_MAX_TOKENS` set, the
    per-spawn var wins for the coder stage."""
    import sm
    monkeypatch.setenv("SM_CODER_MAX_TOKENS", "1024")
    monkeypatch.setenv("SM_MAX_TOKENS", "8192")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    assert call["max_tokens"] == 1024


# ===========================================================================
# Category G — API key missing (4 tests)
#
# Unset `ANTHROPIC_API_KEY` -> `MissingAPIKeyError` propagates UNCHANGED
# from the coder default; SDK is NOT called for coder. (NOTE: with no
# key, the test_writer default's resolver fires first and raises before
# coder is reached — so MissingAPIKeyError is what propagates in
# end-to-end tests. To isolate "coder-stage" missing-key behavior we
# inject a test_writer stub so the failure ONLY originates in the
# coder default.)
# ===========================================================================


def test_missing_api_key_at_coder_stage_raises(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """With no `ANTHROPIC_API_KEY`, the coder default's
    `resolve_api_key()` raises `MissingAPIKeyError` — propagated
    unchanged, NOT wrapped as CoderAgentError. Test_writer is stubbed
    so the failure originates at the coder stage."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())


def test_missing_api_key_not_wrapped_as_coder_agent_error(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """MissingAPIKeyError raised from the coder default must NOT be
    wrapped as CoderAgentError — the CLI's exit-12 mapping depends on
    the typed class."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())
        pytest.fail("expected MissingAPIKeyError")
    except sm.CoderAgentError as e:
        pytest.fail(
            f"MissingAPIKeyError must propagate UNCHANGED, not be wrapped "
            f"as CoderAgentError; got: {e!s}"
        )
    except sm.MissingAPIKeyError:
        pass


def test_missing_api_key_does_not_call_coder_sdk(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """When `ANTHROPIC_API_KEY` is unset, the SDK is not invoked at the
    coder stage."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())
    assert _FakeAnthropicClient.instances == [], (
        "MissingAPIKeyError must fire BEFORE the coder SDK is constructed"
    )


def test_missing_api_key_error_message_mentions_env_var_coder(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """The `MissingAPIKeyError` message names `ANTHROPIC_API_KEY` so
    the operator knows which var to set."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError) as exc_info:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())
    assert "ANTHROPIC_API_KEY" in str(exc_info.value), (
        f"MissingAPIKeyError message must name ANTHROPIC_API_KEY; got: "
        f"{exc_info.value!s}"
    )


# ===========================================================================
# Category H — ConfigError propagates (3 tests)
#
# Invalid SM_CODER_MAX_TOKENS / SM_MAX_TOKENS int -> ConfigError.
# Must NOT be wrapped as CoderAgentError.
# ===========================================================================


def test_invalid_coder_max_tokens_raises_config_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_CODER_MAX_TOKENS=notanint` -> ConfigError propagates from
    the coder default."""
    import sm
    monkeypatch.setenv("SM_CODER_MAX_TOKENS", "notanint")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.ConfigError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())


def test_config_error_not_wrapped_as_coder_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """ConfigError from the coder default must propagate UNCHANGED,
    not be wrapped as CoderAgentError."""
    import sm
    monkeypatch.setenv("SM_CODER_MAX_TOKENS", "garbage")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())
        pytest.fail("expected ConfigError")
    except sm.CoderAgentError as e:
        pytest.fail(
            f"ConfigError must propagate UNCHANGED, not be wrapped as "
            f"CoderAgentError; got: {e!s}"
        )
    except sm.ConfigError:
        pass


def test_invalid_global_max_tokens_raises_config_error_at_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_MAX_TOKENS=notanint` (per-spawn unset) -> ConfigError at
    coder stage when test_writer is stubbed past the same trap."""
    import sm
    monkeypatch.delenv("SM_CODER_MAX_TOKENS", raising=False)
    monkeypatch.delenv("SM_TEST_WRITER_MAX_TOKENS", raising=False)
    monkeypatch.setenv("SM_MAX_TOKENS", "notanint")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.ConfigError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())


# ===========================================================================
# Category I — SDK exceptions wrapped as CoderAgentError (6 tests)
# ===========================================================================


def test_sdk_network_error_wraps_as_coder_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated network failure at coder stage -> wrapped as
    `CoderAgentError`. Test_writer is stubbed so the SDK is only
    touched once — at the coder stage."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    boom = ConnectionError("ECONNREFUSED 443")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.CoderAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())


def test_sdk_auth_error_wraps_as_coder_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated auth failure at coder stage -> wrapped as
    `CoderAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    class _FakeAuthError(Exception):
        pass

    boom = _FakeAuthError("401 invalid api key")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.CoderAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())


def test_sdk_rate_limit_error_wraps_as_coder_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated rate limit at coder stage -> wrapped as
    `CoderAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    class _FakeRateLimitError(Exception):
        pass

    boom = _FakeRateLimitError("429 rate-limited")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.CoderAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())


def test_sdk_exception_original_chained_via_cause_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The original SDK exception is chained via `__cause__` on the
    `CoderAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    boom = ConnectionError("network down")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.CoderAgentError) as exc_info:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
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
        f"chain on CoderAgentError; chain types "
        f"{[t.__name__ for t in types_in_chain]!r}; direct cause: "
        f"{err.__cause__!r}"
    )


def test_sdk_exception_no_silent_swallow_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK raises at coder stage -> execute() MUST raise (not return
    None / empty dict)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=RuntimeError("boom-coder"))
    with pytest.raises(Exception):  # noqa: PT011
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())


def test_sdk_exception_no_auto_retry_coder(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """On SDK failure at coder stage, the seam is called exactly ONCE —
    no retries. Test_writer stub means total fake-client construction
    count is exactly 1 (the coder stage)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=RuntimeError("boom-no-retry"))
    with pytest.raises(Exception):  # noqa: PT011
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly ONE fake-client construction (coder stage "
        f"only; test_writer was stubbed); got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1, (
        f"expected exactly ONE messages.create() call (no retries); "
        f"got {len(_FakeAnthropicClient.instances[0].messages.calls)}"
    )


# ===========================================================================
# Category J — Raw text returned (no parse_agent_json) (4 tests)
#
# Coder returns implementation code, not JSON. The default MUST NOT
# route the response through parse_agent_json. Any string — even
# malformed JSON, even an empty string — flows through unmodified.
# ===========================================================================


def test_coder_default_returns_raw_text_unmodified(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The coder SDK response text reaches the coder_output entry's
    `output` field BYTE-FOR-BYTE."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    raw = (
        "# coding: utf-8\n"
        "def production_impl():\n"
        "    return 42\n"
    )
    _install_fake_anthropic(monkeypatch, response_text=raw)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"][0]
    assert co["output"] == raw, (
        f"expected output to be SDK response BYTE-FOR-BYTE; got "
        f"{co['output']!r}"
    )


def test_coder_default_does_not_call_parse_agent_json(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Pin that `parse_agent_json` is NOT called during execute()'s
    coder stage — the default returns raw text. Verified by spying on
    parse_agent_json."""
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
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    assert called["count"] == 0, (
        f"parse_agent_json must NOT be called by the Coder default; got "
        f"{called['count']} calls"
    )


def test_coder_malformed_json_response_still_flows_through(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Even if the SDK returns malformed JSON-shaped text at the coder
    stage, the default returns it as-is (no parse, no CoderAgentError
    on shape)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    garbage = "this is { not } valid JSON ((("
    _install_fake_anthropic(monkeypatch, response_text=garbage)
    # Should NOT raise — Coder response is raw text.
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"][0]
    assert co["output"] == garbage


def test_coder_empty_response_flows_through(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Empty-string SDK response at coder stage: default returns it
    unmodified. (Whether downstream reviewer treats empty impl_code as
    failure is THEIR contract, not coder's.)"""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch, response_text="")
    # Should NOT raise from the coder side.
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"][0]
    assert co["output"] == ""


# ===========================================================================
# Category K — Removed NotImplementedError for coder only (4 tests)
#
# `execute(<id>, spawn_reviewer=...)` (test_writer + coder both
# omitted) no longer raises NotImplementedError — the test_writer
# default (Story 7) AND the coder default (Story 8) both fire. But
# reviewer alone (without test_writer/coder) STILL raises
# NotImplementedError because Story 9 hasn't shipped.
# ===========================================================================


def test_coder_default_no_longer_raises_not_implemented(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>, spawn_reviewer=...)` with spawn_test_writer +
    spawn_coder omitted does NOT raise NotImplementedError — Story 8
    inverted the coder default."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_reviewer=_make_reviewer())
    except NotImplementedError as e:
        pytest.fail(
            f"Story 8: execute() with spawn_coder omitted (test_writer "
            f"also omitted) must NOT raise NotImplementedError; got: "
            f"{e!s}"
        )


def test_coder_explicit_none_no_longer_raises_not_implemented(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>, spawn_coder=None, spawn_reviewer=...)` routes
    coder=None to the real default, NOT NotImplementedError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=None,
                   spawn_reviewer=_make_reviewer())
    except NotImplementedError as e:
        pytest.fail(
            f"Story 8: explicit spawn_coder=None must fall through to "
            f"real default, not raise NotImplementedError; got: {e!s}"
        )


def test_reviewer_still_raises_not_implemented_under_story_8(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """spawn_reviewer=None no longer raises NotImplementedError under
    Story 9 — reviewer default is wired.

    This test was a Story-8-era forward-looking guard for "Story 9 will
    wire reviewer." Story 9 has shipped, so the guard inverts: with
    spawn_test_writer=stub, spawn_coder=None, spawn_reviewer=None, the
    coder default fires (real, fake-SDK-backed) AND the reviewer
    default fires (real, fake-SDK-backed). The reviewer-stage fake-SDK
    response is the default impl-code string (not a valid reviewer JSON
    verdict), so the real default's `parse_agent_json` rejects it with
    a `ReviewerAgentError` — but the important thing here is that
    NotImplementedError is NOT raised. Behavior-preserving update —
    see Story 9's cascade list.
    """
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=None,
                   spawn_reviewer=None)
    except NotImplementedError as e:
        pytest.fail(
            f"Story 9: spawn_reviewer=None must fall through to the real "
            f"default, not raise NotImplementedError; got: {e!s}"
        )
    except sm.ReviewerAgentError:
        # Expected: the default impl-code response is not valid JSON, so
        # parse_agent_json (Story 4) raises ReviewerAgentError. The test
        # only pins that NotImplementedError is NOT raised — Story 9 has
        # shipped, the linchpin is closed.
        pass


def test_reviewer_alone_omitted_still_raises_not_implemented(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>, spawn_test_writer=..., spawn_coder=...)` with
    spawn_reviewer omitted no longer raises NotImplementedError under
    Story 9 — the reviewer default is wired.

    This test was a Story-8-era forward-looking guard for "Story 9 will
    wire reviewer." Story 9 has shipped, so the guard inverts: with
    test_writer + coder both injected as stubs, the reviewer default is
    the only real-agent default that fires. The fake SDK returns the
    default impl-code string (not valid reviewer JSON), so the real
    default's `parse_agent_json` raises ReviewerAgentError — but the
    important thing is that NotImplementedError is NOT raised.
    Behavior-preserving update — see Story 9's cascade list.
    """
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=lambda r, s, t: "impl\n")
    except NotImplementedError as e:
        pytest.fail(
            f"Story 9: spawn_reviewer omitted must fall through to the "
            f"real default, not raise NotImplementedError; got: {e!s}"
        )
    except sm.ReviewerAgentError:
        # Expected — fake SDK returns non-JSON; parse_agent_json rejects.
        pass


# ===========================================================================
# Category L — Injectable coder callable still works (5 tests)
# ===========================================================================


def test_injectable_coder_callable_bypasses_real_default(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """Injecting `spawn_coder=callable` bypasses the real coder default
    — even with NO `ANTHROPIC_API_KEY`, the call succeeds (test_writer
    also injected so the whole flow has no SDK touch)."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    def _spawn(role_spec_path, story, test_code):
        return "def injected_impl(): return 1\n"

    # No SDK fake installed — neither stage must trigger any SDK path.
    result = sm.execute(in_sprint[0],
                        spawn_test_writer=_make_test_writer(),
                        spawn_coder=_spawn,
                        spawn_reviewer=_make_reviewer())
    assert isinstance(result, dict)


def test_injectable_coder_callable_does_not_construct_sdk_client(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Even with SDK fake installed + valid API key, an injected coder
    callable means the fake SDK is never touched at the coder stage
    (test_writer is also injected so total SDK touches = 0)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []

    def _spawn(role_spec_path, story, test_code):
        return "def injected_x(): return 1\n"

    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_spawn,
               spawn_reviewer=_make_reviewer())
    assert _FakeAnthropicClient.instances == [], (
        "expected NO fake-SDK construction when both spawn_test_writer "
        "AND spawn_coder are injected"
    )


def test_injectable_coder_callable_receives_three_args(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The injected coder callable receives `(role_spec_path: str,
    story: dict, test_code: str)` — same signature as the real
    default."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target_id = in_sprint[0]

    captured: dict = {}
    tc_marker = "# INJ-CODER-TC-marker\ndef test_y(): pass\n"

    def _spawn(role_spec_path, story, test_code):
        captured["role_spec_path"] = role_spec_path
        captured["story"] = dict(story)
        captured["test_code"] = test_code
        return "def x(): return 1\n"

    sm.execute(target_id,
               spawn_test_writer=_make_test_writer(test_code=tc_marker),
               spawn_coder=_spawn,
               spawn_reviewer=_make_reviewer())
    assert isinstance(captured["role_spec_path"], str)
    assert captured["role_spec_path"].endswith("coder.md")
    assert captured["story"]["story_id"] == target_id
    assert captured["test_code"] == tc_marker


def test_injectable_coder_callable_return_value_used_verbatim(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The injected coder callable's return value reaches the
    coder_output entry verbatim."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    payload = "# CUSTOM-INJECTED-CODER-marker\ndef impl_y(): pass\n"

    def _spawn(role_spec_path, story, test_code):
        return payload

    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_spawn,
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"][0]
    assert co["output"] == payload


def test_injectable_coder_callable_exception_propagates(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """An injected coder callable that raises has its exception
    propagated verbatim — Iter 1 Story 23's contract preserved."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    class _SpawnFailure(RuntimeError):
        pass

    def _spawn(role_spec_path, story, test_code):
        raise _SpawnFailure("custom coder callable failure")

    with pytest.raises(_SpawnFailure):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_spawn,
                   spawn_reviewer=_make_reviewer())


# ===========================================================================
# Category M — Caller-bind via sys.modules (2 tests)
#
# `execute` must bind the coder default by looking up
# `_default_execute_coder_spawn` on `sys.modules[__name__]` so
# monkeypatches via `monkeypatch.setattr(sm, "_default_..", ...)` take
# effect. Mirrors Story 6 / 7's pattern.
# ===========================================================================


def test_monkeypatch_on_coder_default_takes_effect(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`monkeypatch.setattr(sm, "_default_execute_coder_spawn", fake)`
    replaces what `execute` calls when coder is omitted."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    called = {"count": 0}
    payload = "# MONKEYPATCHED-CODER-DEFAULT\ndef impl_mp(): pass\n"

    def _patched(role_spec_path, story, test_code):
        called["count"] += 1
        return payload

    monkeypatch.setattr(sm, "_default_execute_coder_spawn", _patched)
    # No SDK fake installed for the coder stage; test_writer stubbed
    # so no SDK touched at all.
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    assert called["count"] == 1, (
        f"expected the monkeypatched coder default to be called exactly "
        f"once; got {called['count']}"
    )
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"][0]
    assert co["output"] == payload, (
        "monkeypatched coder default's return value must reach the "
        "coder_output entry"
    )


def test_monkeypatch_coder_default_does_not_touch_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """A monkeypatched coder default that doesn't call the SDK leaves
    the fake SDK untouched (for the coder stage) — proves the bind
    goes through sys.modules at call time, not via a closure captured
    at module-import time."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []

    def _patched(role_spec_path, story, test_code):
        return "def x(): pass\n"

    monkeypatch.setattr(sm, "_default_execute_coder_spawn", _patched)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    assert _FakeAnthropicClient.instances == [], (
        "monkeypatched coder default must REPLACE the real default — "
        "with test_writer also stubbed, the fake SDK should NOT be "
        "touched"
    )


# ===========================================================================
# Category N — Message shape (4 tests)
# ===========================================================================


def test_coder_messages_arg_is_a_list(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`messages` reaching the coder-stage SDK is a list."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    assert isinstance(call["messages"], list), (
        f"messages must be a list; got {type(call['messages']).__name__}"
    )


def test_coder_messages_arg_is_non_empty(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`messages` reaching the coder-stage SDK is non-empty (at least
    one turn)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    assert len(call["messages"]) >= 1, (
        f"messages must have at least one turn; got {call['messages']!r}"
    )


def test_coder_messages_have_user_role(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """At least one message has `role='user'` in the coder-stage call."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    roles = [m.get("role") for m in call["messages"]]
    assert "user" in roles, (
        f"expected at least one user-role message; got roles {roles!r}"
    )


def test_coder_messages_carry_spec_story_and_test_code(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The coder-stage user message bundles ALL THREE — role-spec
    content, the story dict, AND the test_code — per Story 8 spec."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel_tc = "# COMBO-SENTINEL-TC\ndef test_combo(): assert True\n"
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(test_code=sentinel_tc),
               spawn_reviewer=_make_reviewer())
    call = _captured_coder_call_with_injected_tw()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_coder_role_spec_text().strip()
    assert role_text in msg_text, (
        "coder role spec content missing from coder messages"
    )
    assert in_sprint[0] in msg_text, (
        "story_id missing from coder messages — story dict not bundled"
    )
    assert sentinel_tc in msg_text, (
        "test_code missing from coder messages — test_writer output "
        "not bundled"
    )


# ===========================================================================
# Category O — Static grep: coder default routes through provider seam
# (3 tests)
# ===========================================================================


def test_coder_default_routes_through_invoke_anthropic_seam(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Runtime: invoking the coder default calls the provider seam."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    assert len(_FakeAnthropicClient.instances) == 1, (
        "expected one fake-client construction at coder stage "
        "(test_writer injected)"
    )
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_coder_default_resolves_api_key_at_call_time(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Two calls with two different `ANTHROPIC_API_KEY` values use the
    LATEST value for each coder-stage construction — pins read at call
    time, not at module-import."""
    import sm
    # First call
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-key-A-coder")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    first_api_key = _FakeAnthropicClient.instances[0].api_key

    # Second call — need a fresh story so the state-gate passes.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-key-B-coder")
    _install_fake_anthropic(monkeypatch)
    sm.execute(in_sprint[1],
               spawn_test_writer=_make_test_writer(),
               spawn_reviewer=_make_reviewer())
    second_api_key = _FakeAnthropicClient.instances[0].api_key

    assert first_api_key == "sk-key-A-coder"
    assert second_api_key == "sk-key-B-coder"


def test_coder_default_does_not_import_anthropic_directly():
    """Static check: `import anthropic` / `from anthropic` inside the
    `_default_execute_coder_spawn` function body is zero. The seam is
    the only legitimate import site."""
    src = _read_sm_source()
    lines = src.splitlines()
    def_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^def\s+_default_execute_coder_spawn\s*\(", line):
            def_idx = i
            break
    if def_idx is None:
        pytest.fail(
            "`_default_execute_coder_spawn` def not found at module-top-"
            "level in sm.py — Story 8's name contract is broken"
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
                f"`_default_execute_coder_spawn` body has a direct "
                f"`anthropic` import: {body_ln!r}. The default must "
                f"route through `_invoke_anthropic`, not import the SDK "
                f"itself."
            )


# ===========================================================================
# Category P — Failure invariants: SDK exception, log shape (4 tests)
# ===========================================================================


def test_coder_sdk_exception_does_not_write_coder_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK exception at coder stage fires BEFORE the coder_output entry
    is written. No coder_output entry in the log after a failure."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=ConnectionError("downed"))
    with pytest.raises(sm.CoderAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"]
    assert len(co) == 0, (
        f"expected NO coder_output entry on SDK failure; got {len(co)} "
        f"entries"
    )


def test_coder_sdk_exception_still_writes_testwriter_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK exception at coder stage means the test_writer stage
    succeeded first — so the testwriter_output entry IS written before
    the coder failure. Truthful audit trail per Story 23's failure
    invariants."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    tw_payload = "# tw-payload-before-failure\ndef test_x(): pass\n"
    _install_fake_anthropic(monkeypatch,
                            raise_exc=ConnectionError("downed"))
    with pytest.raises(sm.CoderAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(test_code=tw_payload),
                   spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"]
    assert len(tw) == 1, (
        f"expected the testwriter_output entry to be written before the "
        f"coder failure; got {len(tw)} entries"
    )
    assert tw[0]["output"] == tw_payload


def test_coder_missing_api_key_does_not_write_coder_entry(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """MissingAPIKeyError at coder stage -> no coder_output entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    co = [e for e in entries if e.get("type") == "coder_output"]
    assert len(co) == 0


def test_coder_sdk_exception_does_not_call_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK exception at coder stage means spawn_reviewer is never
    called."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=ConnectionError("bad"))
    record = {}
    with pytest.raises(sm.CoderAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_reviewer=_make_reviewer(record=record))
    assert "reviewer_calls" not in record, (
        "spawn_reviewer must NOT be called when coder fails"
    )


# ===========================================================================
# Category Q — End-to-end: test_writer (default) -> coder (default)
# pipeline (3 tests)
#
# Both defaults fire under the same fake SDK. The fake serves
# scripted responses so test_writer and coder responses are
# distinguishable. Both stages write their respective entries; the
# coder receives the test_writer's output as its test_code arg.
# ===========================================================================


def test_e2e_both_defaults_fire_and_write_entries(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """End-to-end: execute() with both test_writer and coder defaults
    writes BOTH testwriter_output AND coder_output entries with the
    scripted SDK responses."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    tw_resp = "# E2E-TW-RESP\ndef test_e2e(): assert True\n"
    cd_resp = "# E2E-CD-RESP\ndef e2e_impl(): return 7\n"
    _install_fake_anthropic(monkeypatch,
                            response_sequence=[tw_resp, cd_resp])
    sm.execute(in_sprint[0],
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"]
    co = [e for e in entries if e.get("type") == "coder_output"]
    assert len(tw) == 1
    assert len(co) == 1
    assert tw[0]["output"] == tw_resp
    assert co[0]["output"] == cd_resp


def test_e2e_test_writer_output_flows_to_coder_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """End-to-end: the test_writer default's SDK response reaches the
    coder default as its `test_code` arg AND appears verbatim in the
    coder's SDK message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    tw_resp = "# E2E-PIPE-TW\ndef test_pipe(): assert True\n"
    cd_resp = "# E2E-PIPE-CD\ndef pipe_impl(): return 1\n"
    _install_fake_anthropic(monkeypatch,
                            response_sequence=[tw_resp, cd_resp])
    sm.execute(in_sprint[0],
               spawn_reviewer=_make_reviewer())
    # The coder-stage SDK call must have received tw_resp in its
    # message content.
    coder_call = _captured_coder_call()
    msg_text = _flatten_message_content(coder_call["messages"])
    assert tw_resp in msg_text, (
        f"expected test_writer SDK response ({tw_resp!r}) to flow into "
        f"the coder SDK message content; first 600 chars: "
        f"{msg_text[:600]!r}"
    )


def test_e2e_only_coder_fails_test_writer_succeeds(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Scripted failure: test_writer call succeeds, coder call (#2)
    fails. The testwriter_output entry IS written; the coder_output
    entry is NOT. Final raise is CoderAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    boom = ConnectionError("only-coder-stage-fails")
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_TEST_CODE,
                            raise_exc=boom,
                            raise_on_call=2)
    with pytest.raises(sm.CoderAgentError):
        sm.execute(in_sprint[0],
                   spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"]
    co = [e for e in entries if e.get("type") == "coder_output"]
    assert len(tw) == 1, (
        f"expected test_writer entry to be written before the coder-"
        f"stage failure; got {len(tw)}"
    )
    assert len(co) == 0, (
        f"expected NO coder_output entry on coder-stage SDK failure; "
        f"got {len(co)}"
    )
