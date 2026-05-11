"""Iter 2 Story 9 — Real `spawn_reviewer` default in `execute`.

This file pins the contract of `execute`'s real (non-injected) Reviewer
spawn default — the LAST real-agent linchpin of Iter 2 (after Story 6
wired `decompose`, Story 7 wired the TestWriter stage, Story 8 wired the
Coder stage). Story 9 wires `spawn_reviewer` — the third agent that
returns JSON (like decompose, unlike test_writer/coder which return raw
text) — AND it adds **shape validation** because the downstream
accept/reject branch in `execute()` depends on a `{"approved": bool,
"test_result": str}` shape.

Pinned clauses (Story 9 acceptance, paraphrased from Stories_v1.md):

  1. Replaces the `NotImplementedError` default for `spawn_reviewer` in
     `execute` with a real implementation matching the existing
     injectable-callable signature
     `(role_spec_path: str, story: dict, test_code: str,
       impl_code: str) -> dict` exactly — no signature drift, no
     downstream ripple.
  2. The real default reads `roles/reviewer.md` via Iter 1 Story 8's
     `resolve_role_spec` (the caller passes the resolved path; the
     default reads it), packages it plus the active story's dict (as
     JSON) plus the test_code string plus the impl_code string into a
     single user message, calls the provider seam (Story 5) with
     `resolve_model("reviewer")` and `resolve_max_tokens("reviewer")`,
     then routes the response through `parse_agent_json(..., role=
     "reviewer")` — this is the THIRD real-agent default that goes
     through parse_agent_json (after Story 6's decompose; Stories 7/8
     do not).
  3. **Shape validation**: the parsed object MUST be a dict with
     EXACTLY two keys `approved` (bool) and `test_result` (str). Extra
     keys, missing keys, wrong types, or top-level-not-a-dict all
     raise `ReviewerAgentError` with a descriptive message naming the
     shape violation. The validated dict is returned (not the raw
     text).
  4. On SDK-level failure (network, auth, rate-limit, generic Exception
     that is NOT MissingAPIKeyError / ConfigError /
     ReviewerAgentError), the exception wraps as `ReviewerAgentError`
     with the original chained via `__cause__`. `MissingAPIKeyError`
     and `ConfigError` propagate UNCHANGED so the CLI maps
     `MissingAPIKeyError` to exit 12 and config errors are
     diagnosable. `parse_agent_json` failures are ALREADY typed as
     `ReviewerAgentError` per Story 4 — they propagate UNCHANGED (do
     NOT double-wrap).
  5. No auto-retry — exactly one SDK call per reviewer-stage invocation
     inside `execute`.
  6. End-to-end (with mocked SDK): `execute <story_id>` against an
     active iteration with a story_backlog and a cut sprint, with NO
     injected `spawn_test_writer` and NO injected `spawn_coder` and
     NO injected `spawn_reviewer`, drives the pipeline
     test_writer (default) -> coder (default) -> reviewer (default)
     and writes the appropriate `reviewer_approval` + final
     `story_state_change` entries per the existing Iter 1 branch
     logic. Approved -> accepted entry. Rejected -> rejected entry.

CONTRACT INTERPRETATION (locked by TestWriter):

  - PRIVATE name: the real default is `_default_execute_reviewer_spawn`
    at module scope on `sm`. NOT in `sm.__all__`. The spawn defaults
    are internal implementation; only their wired-up signatures are
    public surface.
  - Signature is `_default_execute_reviewer_spawn(role_spec_path: str,
    story: dict, test_code: str, impl_code: str) -> dict` — exact
    match with the existing injectable-callable signature pinned by
    Iter 1 Story 23.
  - The default reads `role_spec_path` content from disk (the caller
    in `execute` already calls `resolve_role_spec("reviewer")` and
    passes the path; the default reads it). Mirrors Stories 6 / 7 / 8.
  - Message shape: a single user-turn message whose `content` is a
    string that contains ALL FOUR pieces — the role-spec text, the
    story dict (as JSON), the test_code, AND the impl_code — plus an
    instruction to return a JSON verdict. Exact framing is the
    Reviewer's call; tests verify all four pieces appear.
  - Model/max_tokens are read at call time via
    `resolve_model("reviewer")` and `resolve_max_tokens("reviewer")`
    — env-var overrides (`SM_REVIEWER_MODEL`, `SM_REVIEWER_MAX_TOKENS`)
    are honored on every call. No caching.
  - API key is read via `resolve_api_key()` — missing key raises
    `MissingAPIKeyError` before any SDK work.
  - Provider-seam invocation: the default calls `_invoke_anthropic(
    messages=..., model=..., max_tokens=..., api_key=...)`. Anthropic
    SDK is NOT imported by the default itself — only by the seam.
  - Routes through `parse_agent_json(raw, role="reviewer")` — this is
    the differentiator from Stories 7/8. After parse, the default
    validates the shape: dict with exactly `approved` (bool) and
    `test_result` (str). Shape violations raise `ReviewerAgentError`
    with a descriptive message.
  - SDK exception wrapping: when `_invoke_anthropic` raises (network /
    auth / rate-limit / generic Exception that is NOT a
    `MissingAPIKeyError` / `ConfigError` and NOT already a
    `ReviewerAgentError`), the default wraps it as a
    `ReviewerAgentError` with the original chained via `__cause__`.
    `MissingAPIKeyError`, `ConfigError`, AND `ReviewerAgentError`
    (parse failure already typed) propagate unchanged.
  - No auto-retry: one SDK call per reviewer-stage invocation inside
    `execute`.
  - Caller-bind contract: `execute` falls back to the real default by
    looking up `_default_execute_reviewer_spawn` on
    `sys.modules[__name__]` so monkeypatches in tests
    (`monkeypatch.setattr(sm, "_default_execute_reviewer_spawn", ...)`)
    take effect. Mirrors Stories 6 / 7 / 8.
  - Injectable callable preserved: `execute(spawn_reviewer=callable,
    ...)` continues to bypass the real default entirely. No regression
    on Iter 1 Story 23's injectable contract.

CRITICAL — tests must NOT make real API calls. Every test that
triggers the default path injects a fake `anthropic` module into
`sys.modules` via `monkeypatch.setitem` BEFORE the call. The lazy
import inside `_invoke_anthropic` finds the fake and never touches the
real SDK.

Because reaching the reviewer stage requires test_writer and coder
stages to succeed first, tests that exercise the reviewer default ALSO
need test_writer + coder sources. The default test_writer (Story 7)
and coder (Story 8) ALSO use the fake SDK, so the fake will be called
THREE times per execute() invocation: once for test_writer, once for
coder, once for reviewer. Helpers in this file account for all three
calls explicitly via a `response_sequence` of three texts.

Iter 1 cascade note: `test_execute.py` has multiple tests that pin the
OLD `NotImplementedError` defaults for all three spawn callables. The
Coder resolves those cascades per Iter 2's behavior-preserving update
pattern; this file does NOT modify them (anti-lane). See the cascade
list in the final report for line numbers.
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
# Fake Anthropic SDK — mirrors Story 8's tests. Installed into sys.modules
# per-test.
#
# Story 9 wrinkle: reaching the reviewer stage means BOTH test_writer
# AND coder defaults fire first. All three stages route through the same
# fake SDK. We support:
#   - a "shared" fake that returns the SAME text for every .create() call
#     (good enough when tests don't differentiate stages);
#   - a "scripted" fake that returns a SEQUENCE of texts so test_writer /
#     coder / reviewer responses can be distinguished.
#
# For shape-violation tests on the reviewer stage, the test_writer +
# coder responses can be anything (their defaults don't parse), but the
# reviewer response is the focus.
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    """Minimal stand-in for an Anthropic content block. Carries `.text`."""

    def __init__(self, text: str = '{"approved": true, "test_result": "ok"}'):
        self.text = text


class _FakeResponse:
    """Stand-in for the object returned by `client.messages.create(...)`.

    Mirrors the real Messages API: `.content` is a list of content
    blocks; each block has a `.text` attribute.
    """

    def __init__(self, text: str = '{"approved": true, "test_result": "ok"}'):
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    """Stand-in for `client.messages` — the `.create` subobject. Records
    every call into `self.calls` as a dict of kwargs.
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
            return self._sequence[-1]
        return self._response


class _FakeAnthropicClient:
    """Stand-in for `anthropic.Anthropic`. Records the `api_key` it was
    constructed with and exposes a `.messages` subobject that records
    every `.create(...)` call.

    NOTE: a single `execute()` invocation that uses ALL THREE defaults
    will construct the client THREE TIMES (once per stage). The
    fake-client class tracks every construction in `instances`.
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

# A valid JSON reviewer verdict that satisfies the shape contract
# (dict with exactly `approved` bool + `test_result` non-empty str).
_DEFAULT_REVIEWER_JSON = (
    '{"approved": true, "test_result": "all tests pass"}'
)

_DEFAULT_REVIEWER_REJECT_JSON = (
    '{"approved": false, "test_result": "3 tests failed"}'
)


def _install_fake_anthropic(monkeypatch,
                            response_text=None,
                            raise_exc=None,
                            response_sequence=None,
                            raise_on_call=None):
    """Build a fake `anthropic` module and install it into `sys.modules`.

    `response_text` controls the `.text` returned by every
    `client.messages.create(...)` call.

    `response_sequence`, if set, is a list of response TEXT strings
    returned one-per-call in order (sticky on the last after
    exhaustion). Use this when test_writer + coder + reviewer
    responses need to differ.

    `raise_exc`, if set, causes EVERY `.create(...)` call to raise the
    given exception.

    `raise_on_call`, if set, is the 1-based call index at which to
    raise `raise_exc`. Used to make ONLY the reviewer-stage call fail
    while the test_writer + coder calls succeed.

    NOTE: clears `_FakeAnthropicClient.instances` so each test starts
    with a clean record.
    """
    _FakeAnthropicClient.instances = []

    if response_sequence is not None:
        seq_responses = [_FakeResponse(t) for t in response_sequence]
    else:
        seq_responses = None

    if response_text is None and seq_responses is None:
        response_text = _DEFAULT_REVIEWER_JSON

    # Track global call index across ALL THREE client constructions
    # inside one execute() invocation.
    call_state = {"global_idx": 0}

    class _BoundClient(_FakeAnthropicClient):
        def __init__(self, api_key=None, **kwargs):
            super().__init__(api_key=api_key, **kwargs)

            outer_seq = seq_responses
            outer_text = response_text
            outer_raise = raise_exc
            outer_raise_on = raise_on_call

            class _StageMessages(_FakeMessages):
                def create(self_inner, **kwargs):
                    call_state["global_idx"] += 1
                    idx = call_state["global_idx"]
                    self_inner.calls.append(kwargs)
                    if outer_raise_on is not None and outer_raise is not None:
                        if idx == outer_raise_on:
                            raise outer_raise
                    elif outer_raise is not None:
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
    """Ensure `ANTHROPIC_API_KEY` is UNSET."""
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
        "iteration_goal": "Story 9 test iteration",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return entry


def _seed_backlog(n: int = 5) -> list:
    """Append a `story_backlog` entry with N canonical stories."""
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
    """Open iteration + seed backlog + cut the sprint."""
    import sm
    _open_iteration(iteration_id=iteration_id)
    sids = _seed_backlog(n=n_stories)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at], sids[cut_at:]


def _make_test_writer(test_code: str = _DEFAULT_TEST_CODE, record=None):
    """Build a spawn_test_writer stub for tests that bypass the
    test_writer default to isolate the reviewer stage."""
    def _spawn(role_spec_path, story):
        if record is not None:
            record.setdefault("test_writer_calls", []).append({
                "role_spec_path": role_spec_path,
                "story": story,
            })
        return test_code
    return _spawn


def _make_coder(impl_code: str = _DEFAULT_IMPL_CODE, record=None):
    """Build a spawn_coder stub for tests that bypass the coder default
    to isolate the reviewer stage."""
    def _spawn(role_spec_path, story, test_code):
        if record is not None:
            record.setdefault("coder_calls", []).append({
                "role_spec_path": role_spec_path,
                "story": story,
                "test_code": test_code,
            })
        return impl_code
    return _spawn


def _read_sm_source() -> str:
    """Return sm.py as text. Used by static grep tests."""
    return SM_PATH.read_text(encoding="utf-8")


def _read_reviewer_role_spec_text() -> str:
    """Return the canonical reviewer.md role-spec content."""
    return (SOURCE_ROLES_DIR / "reviewer.md").read_text(encoding="utf-8")


def _captured_reviewer_call_three_stage() -> dict:
    """Return the kwargs of the reviewer-stage `messages.create(...)`
    call when ALL THREE defaults fire.

    Asserts the fake SDK was constructed exactly three times AND that
    each construction made exactly one `.create` call. Returns the
    THIRD `.create` call's kwargs (the reviewer-stage call).
    """
    assert len(_FakeAnthropicClient.instances) == 3, (
        f"expected exactly THREE fake-client constructions (one per "
        f"stage: test_writer then coder then reviewer); got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    tw_calls = _FakeAnthropicClient.instances[0].messages.calls
    cd_calls = _FakeAnthropicClient.instances[1].messages.calls
    rv_calls = _FakeAnthropicClient.instances[2].messages.calls
    assert len(tw_calls) == 1
    assert len(cd_calls) == 1
    assert len(rv_calls) == 1, (
        f"expected exactly one reviewer create() call; got {len(rv_calls)}"
    )
    return rv_calls[0]


def _captured_reviewer_call_with_stubs() -> dict:
    """Variant for tests that inject stub test_writer + coder (so the
    fake SDK is only constructed ONCE — for the reviewer stage).
    """
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly ONE fake-client construction (only reviewer "
        f"stage uses the SDK; test_writer + coder were injected); got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    rv_calls = _FakeAnthropicClient.instances[0].messages.calls
    assert len(rv_calls) == 1, (
        f"expected exactly one reviewer create() call; got {len(rv_calls)}"
    )
    return rv_calls[0]


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


def _default_three_stage_sequence(
    reviewer_json: str = _DEFAULT_REVIEWER_JSON,
) -> list:
    """Build a 3-element response sequence for end-to-end pipelines:
    [test_writer_text, coder_text, reviewer_json].
    """
    return [_DEFAULT_TEST_CODE, _DEFAULT_IMPL_CODE, reviewer_json]


# ===========================================================================
# Category A — Smoke (6 tests)
#
# `_default_execute_reviewer_spawn` exists on the module, is PRIVATE, is
# NOT in `sm.__all__`, is callable, has the right signature (4 params).
# ===========================================================================


def test_default_reviewer_spawn_exists_on_module():
    """`sm._default_execute_reviewer_spawn` is defined at module scope."""
    import sm
    assert hasattr(sm, "_default_execute_reviewer_spawn"), (
        "expected `_default_execute_reviewer_spawn` to be defined on the "
        "sm module; missing from dir(sm)="
        f"{sorted(n for n in dir(sm) if 'reviewer' in n.lower())!r}"
    )


def test_default_reviewer_spawn_is_callable():
    """`sm._default_execute_reviewer_spawn` is callable."""
    import sm
    obj = getattr(sm, "_default_execute_reviewer_spawn", None)
    assert callable(obj), (
        f"expected `sm._default_execute_reviewer_spawn` to be callable; "
        f"got {type(obj).__name__}"
    )


def test_default_reviewer_spawn_is_private_name():
    """The default is `_default_execute_reviewer_spawn` (leading
    underscore)."""
    import sm
    assert hasattr(sm, "_default_execute_reviewer_spawn"), (
        "expected the private name `_default_execute_reviewer_spawn` to "
        "exist"
    )
    assert not hasattr(sm, "default_execute_reviewer_spawn"), (
        "expected no public `default_execute_reviewer_spawn`; the default "
        "is private."
    )


def test_default_reviewer_spawn_not_in_all():
    """`_default_execute_reviewer_spawn` is NOT in `sm.__all__`."""
    import sm
    assert "_default_execute_reviewer_spawn" not in sm.__all__, (
        f"`_default_execute_reviewer_spawn` must NOT be in sm.__all__; "
        f"got {sm.__all__!r}"
    )


def test_default_reviewer_spawn_signature_four_positional_params():
    """`_default_execute_reviewer_spawn` accepts (role_spec_path, story,
    test_code, impl_code) — FOUR positional parameters matching the
    injectable signature pinned by Iter 1 Story 23."""
    import sm
    sig = inspect.signature(sm._default_execute_reviewer_spawn)
    params = list(sig.parameters.values())
    assert len(params) >= 4, (
        f"expected at least 4 parameters (role_spec_path, story, "
        f"test_code, impl_code); got signature {sig!s}"
    )


def test_default_reviewer_spawn_signature_parameter_names():
    """Parameter names are exactly `role_spec_path`, `story`,
    `test_code`, `impl_code` in that order."""
    import sm
    sig = inspect.signature(sm._default_execute_reviewer_spawn)
    names = list(sig.parameters)[:4]
    assert names == ["role_spec_path", "story", "test_code", "impl_code"], (
        f"_default_execute_reviewer_spawn parameter names must be "
        f"['role_spec_path', 'story', 'test_code', 'impl_code']; got "
        f"{names!r}"
    )


# ===========================================================================
# Category B — Happy path with mocked SDK (8 tests)
# ===========================================================================


def test_happy_path_default_reviewer_returns_final_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>)` with NO spawn callables returns the final state-
    change entry. All three defaults fire under the same fake SDK."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    result = sm.execute(in_sprint[0])
    assert isinstance(result, dict), (
        f"expected dict return; got {type(result).__name__}"
    )


def test_happy_path_default_reviewer_writes_reviewer_approval_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The real reviewer default fires -> `reviewer_approval` entry is
    appended with approved=True."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    sm.execute(in_sprint[0])
    entries = list(sm.read_entries())
    rev = [e for e in entries if e.get("type") == "reviewer_approval"]
    assert len(rev) == 1, (
        f"expected exactly one reviewer_approval entry; got {len(rev)}"
    )
    assert rev[0]["approved"] is True, (
        f"expected approved=True; got {rev[0]['approved']!r}"
    )


def test_happy_path_default_reviewer_calls_sdk_for_reviewer_stage(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """One `execute()` with all three defaults -> exactly THREE fake-
    client constructions and THREE `.create` calls total. No retries."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    sm.execute(in_sprint[0])
    assert len(_FakeAnthropicClient.instances) == 3, (
        f"expected exactly three fake-client constructions; got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    for i, inst in enumerate(_FakeAnthropicClient.instances):
        assert len(inst.messages.calls) == 1, (
            f"client #{i} should have exactly one create() call; got "
            f"{len(inst.messages.calls)}"
        )


def test_happy_path_default_reviewer_constructs_client_with_resolved_api_key(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The reviewer-stage client construction uses the resolved api
    key."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-roundtrip-reviewer-99999")
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    sm.execute(in_sprint[0])
    assert _FakeAnthropicClient.instances[2].api_key == \
        "sk-roundtrip-reviewer-99999"


def test_happy_path_default_reviewer_approve_writes_accepted_state(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Reviewer returns approved=True -> final state change is
    `accepted`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    sm.execute(in_sprint[0])
    entries = list(sm.read_entries())
    state_changes = [e for e in entries
                     if e.get("type") == "story_state_change"
                     and e.get("story_id") == in_sprint[0]]
    final = state_changes[-1]
    assert final["new_state"] == "accepted", (
        f"expected final state 'accepted' on approve path; got "
        f"{final['new_state']!r}"
    )


def test_happy_path_default_reviewer_reject_writes_rejected_state(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Reviewer returns approved=False -> final state change is
    `rejected`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(
        monkeypatch,
        response_sequence=_default_three_stage_sequence(
            reviewer_json=_DEFAULT_REVIEWER_REJECT_JSON,
        ),
    )
    sm.execute(in_sprint[0])
    entries = list(sm.read_entries())
    state_changes = [e for e in entries
                     if e.get("type") == "story_state_change"
                     and e.get("story_id") == in_sprint[0]]
    final = state_changes[-1]
    assert final["new_state"] == "rejected", (
        f"expected final state 'rejected' on reject path; got "
        f"{final['new_state']!r}"
    )


def test_happy_path_default_reviewer_returns_validated_dict_from_isolated_call(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Calling `_default_execute_reviewer_spawn` directly returns the
    parsed + validated dict."""
    import sm
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    reviewer_path = sm.resolve_role_spec("reviewer")
    result = sm._default_execute_reviewer_spawn(
        str(reviewer_path),
        {"story_id": "test-story", "title": "T",
         "acceptance_criteria": "AC"},
        _DEFAULT_TEST_CODE,
        _DEFAULT_IMPL_CODE,
    )
    assert isinstance(result, dict), (
        f"expected dict return; got {type(result).__name__}"
    )
    assert result == {"approved": True, "test_result": "all tests pass"}


def test_happy_path_default_reviewer_test_result_in_approval_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`test_result` text from the reviewer flows to the
    reviewer_approval entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel = "SENTINEL-TR-9b3c1f-passed-all-70"
    reviewer_resp = json.dumps({"approved": True, "test_result": sentinel})
    _install_fake_anthropic(
        monkeypatch,
        response_sequence=[_DEFAULT_TEST_CODE, _DEFAULT_IMPL_CODE,
                           reviewer_resp],
    )
    sm.execute(in_sprint[0])
    entries = list(sm.read_entries())
    rev = [e for e in entries if e.get("type") == "reviewer_approval"][0]
    assert rev["test_result"] == sentinel, (
        f"expected test_result {sentinel!r} in approval entry; got "
        f"{rev['test_result']!r}"
    )


# ===========================================================================
# Category C — Role spec read from roles/reviewer.md (5 tests)
# ===========================================================================


def test_role_spec_content_appears_in_reviewer_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The text of `roles/reviewer.md` appears in the reviewer-stage
    user message content."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_reviewer_role_spec_text()
    first_line = next(
        (ln.strip() for ln in role_text.splitlines() if ln.strip()),
        None,
    )
    assert first_line is not None
    assert first_line in msg_text, (
        f"expected role-spec excerpt {first_line!r} in reviewer message; "
        f"message starts: {msg_text[:200]!r}"
    )


def test_role_spec_full_content_appears_in_reviewer_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The ENTIRE reviewer.md content (verbatim) appears in the
    reviewer-stage message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_reviewer_role_spec_text().strip()
    assert role_text in msg_text, (
        f"expected full reviewer.md role-spec content in reviewer message; "
        f"first 200 chars of role spec: {role_text[:200]!r}"
    )


def test_role_spec_file_read_from_resolved_path_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """When `resolve_role_spec("reviewer")` points at a custom file,
    THAT file's content reaches the reviewer-stage message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel = "SENTINEL-STORY-9-REVIEWER-MARKER-5f7e3a"
    staged = isolated_log.parent / "roles" / "reviewer.md"
    staged.write_text(sentinel + "\nrest of spec...", encoding="utf-8")
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    msg_text = _flatten_message_content(call["messages"])
    assert sentinel in msg_text, (
        f"expected sentinel {sentinel!r} in reviewer message; default may "
        f"have read a different file. Message: {msg_text[:400]!r}"
    )


def test_role_spec_path_passed_into_reviewer_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The reviewer default's `role_spec_path` arg is the absolute path
    to `roles/reviewer.md`."""
    import sm

    captured: dict = {}
    original_default = sm._default_execute_reviewer_spawn

    def _spy(role_spec_path, story, test_code, impl_code):
        captured["role_spec_path"] = role_spec_path
        captured["story"] = dict(story)
        captured["test_code"] = test_code
        captured["impl_code"] = impl_code
        return original_default(role_spec_path, story, test_code,
                                impl_code)

    monkeypatch.setattr(sm, "_default_execute_reviewer_spawn", _spy)
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    assert "role_spec_path" in captured
    path = captured["role_spec_path"]
    assert isinstance(path, str), (
        f"role_spec_path arg must be a string; got {type(path).__name__}"
    )
    assert path.endswith("reviewer.md"), (
        f"expected path to end with 'reviewer.md'; got {path!r}"
    )


def test_role_spec_read_failure_propagates_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """If reviewer role-spec file is missing when default reads it, the
    error propagates."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    real_default = sm._default_execute_reviewer_spawn

    def _delete_then_call(role_spec_path, story, test_code, impl_code):
        try:
            os.remove(role_spec_path)
        except OSError:
            pass
        return real_default(role_spec_path, story, test_code, impl_code)

    monkeypatch.setattr(sm, "_default_execute_reviewer_spawn",
                        _delete_then_call)
    with pytest.raises(Exception):  # noqa: PT011
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


# ===========================================================================
# Category D — Story dict + test_code + impl_code bundled (7 tests)
# ===========================================================================


def test_story_dict_appears_in_reviewer_message_by_id(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `story_id` reaches the reviewer user message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    target_id = in_sprint[0]
    sm.execute(target_id,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    msg_text = _flatten_message_content(call["messages"])
    assert target_id in msg_text


def test_story_dict_title_appears_in_reviewer_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `title` reaches the reviewer user message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    msg_text = _flatten_message_content(call["messages"])
    assert "Story 1" in msg_text


def test_story_dict_acceptance_criteria_in_reviewer_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The active story's `acceptance_criteria` reaches the reviewer
    user message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    msg_text = _flatten_message_content(call["messages"])
    assert "Story 1 must pass its tests." in msg_text


def test_test_code_appears_in_reviewer_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The test_writer's `test_code` reaches the reviewer user
    message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel_tc = (
        "# SENTINEL-TC-TO-REVIEWER-3fa9d7e8b1\n"
        "def test_unique_marker():\n"
        "    assert sm_thing() == 42\n"
    )
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(test_code=sentinel_tc),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    msg_text = _flatten_message_content(call["messages"])
    assert sentinel_tc in msg_text


def test_impl_code_appears_in_reviewer_message(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The coder's `impl_code` reaches the reviewer user message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel_impl = (
        "# SENTINEL-IMPL-TO-REVIEWER-ab12cd34\n"
        "def sm_thing():\n"
        "    return 42\n"
    )
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(impl_code=sentinel_impl))
    call = _captured_reviewer_call_with_stubs()
    msg_text = _flatten_message_content(call["messages"])
    assert sentinel_impl in msg_text


def test_test_code_and_impl_code_passed_to_reviewer_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The reviewer default receives both test_code AND impl_code args.
    Verified by spying on the default."""
    import sm
    captured: dict = {}
    real_default = sm._default_execute_reviewer_spawn
    sentinel_tc = "# SPY-TC\ndef test_x(): pass\n"
    sentinel_ic = "# SPY-IC\ndef impl_x(): pass\n"

    def _spy(role_spec_path, story, test_code, impl_code):
        captured["test_code"] = test_code
        captured["impl_code"] = impl_code
        return real_default(role_spec_path, story, test_code, impl_code)

    monkeypatch.setattr(sm, "_default_execute_reviewer_spawn", _spy)
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(test_code=sentinel_tc),
               spawn_coder=_make_coder(impl_code=sentinel_ic))
    assert captured["test_code"] == sentinel_tc
    assert captured["impl_code"] == sentinel_ic


def test_story_dict_passed_to_reviewer_default_arg(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The reviewer default receives the active story dict as its
    `story` arg."""
    import sm
    captured: dict = {}
    real_default = sm._default_execute_reviewer_spawn

    def _spy(role_spec_path, story, test_code, impl_code):
        captured["story"] = dict(story)
        return real_default(role_spec_path, story, test_code, impl_code)

    monkeypatch.setattr(sm, "_default_execute_reviewer_spawn", _spy)
    _, in_sprint, _ = _seed_sprint()
    target_id = in_sprint[0]
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(target_id,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    assert captured["story"]["story_id"] == target_id
    assert captured["story"]["title"] == "Story 1"


# ===========================================================================
# Category E — resolve_model("reviewer") wired (4 tests)
# ===========================================================================


def test_resolve_model_reviewer_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_REVIEWER_MODEL=custom-rev-model` -> that exact string
    reaches the reviewer-stage `messages.create(model=...)`."""
    import sm
    monkeypatch.setenv("SM_REVIEWER_MODEL", "custom-reviewer-model-v9")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    assert call["model"] == "custom-reviewer-model-v9"


def test_resolve_model_global_override_reaches_sdk_for_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With per-spawn unset and `SM_MODEL=global-id` set, the global
    fallback reaches the reviewer-stage SDK."""
    import sm
    monkeypatch.delenv("SM_REVIEWER_MODEL", raising=False)
    monkeypatch.setenv("SM_MODEL", "global-fallback-model-reviewer")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    assert call["model"] == "global-fallback-model-reviewer"


def test_resolve_model_default_reaches_sdk_for_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With no env overrides, `resolve_model('reviewer')`'s default
    reaches the reviewer-stage SDK."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    expected = sm.resolve_model("reviewer")
    assert call["model"] == expected


def test_resolve_model_per_spawn_beats_global_for_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With BOTH `SM_REVIEWER_MODEL` and `SM_MODEL` set, the per-spawn
    var wins."""
    import sm
    monkeypatch.setenv("SM_REVIEWER_MODEL", "per-spawn-wins-reviewer")
    monkeypatch.setenv("SM_MODEL", "global-loses-reviewer")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    assert call["model"] == "per-spawn-wins-reviewer"


# ===========================================================================
# Category F — resolve_max_tokens("reviewer") wired (4 tests)
# ===========================================================================


def test_resolve_max_tokens_reviewer_override_reaches_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_REVIEWER_MAX_TOKENS=2048` -> 2048 (int) reaches the
    reviewer-stage SDK."""
    import sm
    monkeypatch.setenv("SM_REVIEWER_MAX_TOKENS", "2048")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    assert call["max_tokens"] == 2048
    assert isinstance(call["max_tokens"], int)


def test_resolve_max_tokens_global_override_reaches_sdk_for_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With per-spawn unset and `SM_MAX_TOKENS=8192` set, the global
    fallback reaches the reviewer-stage SDK as an int."""
    import sm
    monkeypatch.delenv("SM_REVIEWER_MAX_TOKENS", raising=False)
    monkeypatch.setenv("SM_MAX_TOKENS", "8192")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    assert call["max_tokens"] == 8192


def test_resolve_max_tokens_default_reaches_sdk_for_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With no env overrides, `resolve_max_tokens('reviewer')`'s
    default (4096) reaches the reviewer-stage SDK."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    expected = sm.resolve_max_tokens("reviewer")
    assert call["max_tokens"] == expected
    assert call["max_tokens"] == 4096


def test_resolve_max_tokens_per_spawn_beats_global_for_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With BOTH `SM_REVIEWER_MAX_TOKENS` and `SM_MAX_TOKENS` set, the
    per-spawn var wins."""
    import sm
    monkeypatch.setenv("SM_REVIEWER_MAX_TOKENS", "1024")
    monkeypatch.setenv("SM_MAX_TOKENS", "8192")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    assert call["max_tokens"] == 1024


# ===========================================================================
# Category G — API key missing (4 tests)
# ===========================================================================


def test_missing_api_key_at_reviewer_stage_raises(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """With no `ANTHROPIC_API_KEY`, the reviewer default raises
    `MissingAPIKeyError` — propagated unchanged. Test_writer + coder
    are stubbed so the failure originates at the reviewer stage."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_missing_api_key_not_wrapped_as_reviewer_agent_error(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """`MissingAPIKeyError` from the reviewer default must NOT be
    wrapped as `ReviewerAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
        pytest.fail("expected MissingAPIKeyError")
    except sm.ReviewerAgentError as e:
        pytest.fail(
            f"MissingAPIKeyError must propagate UNCHANGED, not wrapped "
            f"as ReviewerAgentError; got: {e!s}"
        )
    except sm.MissingAPIKeyError:
        pass


def test_missing_api_key_does_not_call_reviewer_sdk(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """When `ANTHROPIC_API_KEY` is unset, the reviewer SDK is not
    invoked."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    _FakeAnthropicClient.instances = []
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    assert _FakeAnthropicClient.instances == [], (
        "MissingAPIKeyError must fire BEFORE the reviewer SDK is "
        "constructed"
    )


def test_missing_api_key_error_message_mentions_env_var_reviewer(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """The `MissingAPIKeyError` message names `ANTHROPIC_API_KEY`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    with pytest.raises(sm.MissingAPIKeyError) as exc_info:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    assert "ANTHROPIC_API_KEY" in str(exc_info.value)


# ===========================================================================
# Category H — ConfigError propagates (3 tests)
# ===========================================================================


def test_invalid_reviewer_max_tokens_raises_config_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_REVIEWER_MAX_TOKENS=notanint` -> ConfigError propagates."""
    import sm
    monkeypatch.setenv("SM_REVIEWER_MAX_TOKENS", "notanint")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    with pytest.raises(sm.ConfigError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_config_error_not_wrapped_as_reviewer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """ConfigError from the reviewer default must propagate UNCHANGED,
    not wrapped as ReviewerAgentError."""
    import sm
    monkeypatch.setenv("SM_REVIEWER_MAX_TOKENS", "garbage")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
        pytest.fail("expected ConfigError")
    except sm.ReviewerAgentError as e:
        pytest.fail(
            f"ConfigError must propagate UNCHANGED, not wrapped as "
            f"ReviewerAgentError; got: {e!s}"
        )
    except sm.ConfigError:
        pass


def test_invalid_global_max_tokens_raises_config_error_at_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`SM_MAX_TOKENS=notanint` -> ConfigError at reviewer stage when
    earlier stages are stubbed past the same trap."""
    import sm
    monkeypatch.delenv("SM_REVIEWER_MAX_TOKENS", raising=False)
    monkeypatch.delenv("SM_TEST_WRITER_MAX_TOKENS", raising=False)
    monkeypatch.delenv("SM_CODER_MAX_TOKENS", raising=False)
    monkeypatch.setenv("SM_MAX_TOKENS", "notanint")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    with pytest.raises(sm.ConfigError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


# ===========================================================================
# Category I — SDK exceptions wrapped as ReviewerAgentError (6 tests)
# ===========================================================================


def test_sdk_network_error_wraps_as_reviewer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated network failure at reviewer stage -> wrapped as
    `ReviewerAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    boom = ConnectionError("ECONNREFUSED 443")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_sdk_auth_error_wraps_as_reviewer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated auth failure at reviewer stage -> wrapped as
    `ReviewerAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    class _FakeAuthError(Exception):
        pass

    boom = _FakeAuthError("401 invalid api key")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_sdk_rate_limit_error_wraps_as_reviewer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Simulated rate limit at reviewer stage -> wrapped as
    `ReviewerAgentError`."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    class _FakeRateLimitError(Exception):
        pass

    boom = _FakeRateLimitError("429 rate-limited")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_sdk_exception_original_chained_via_cause_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The original SDK exception is chained via `__cause__`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    boom = ConnectionError("network down")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(sm.ReviewerAgentError) as exc_info:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    err = exc_info.value
    chained = []
    cur = err
    while cur is not None:
        chained.append(cur)
        cur = cur.__cause__
    types_in_chain = {type(c) for c in chained}
    assert ConnectionError in types_in_chain or err.__cause__ is boom, (
        f"expected the original ConnectionError reachable via __cause__ "
        f"chain on ReviewerAgentError; chain types "
        f"{[t.__name__ for t in types_in_chain]!r}; direct cause: "
        f"{err.__cause__!r}"
    )


def test_sdk_exception_no_silent_swallow_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK raises at reviewer stage -> execute() MUST raise."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=RuntimeError("boom-reviewer"))
    with pytest.raises(Exception):  # noqa: PT011
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_sdk_exception_no_auto_retry_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """On SDK failure at reviewer stage, the seam is called exactly
    ONCE — no retries. With test_writer + coder stubbed, total fake-
    client construction count is exactly 1."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=RuntimeError("boom-no-retry"))
    with pytest.raises(Exception):  # noqa: PT011
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


# ===========================================================================
# Category J — Routes through parse_agent_json (4 tests)
#
# This is the DIFFERENTIATOR from Stories 7/8: the reviewer default
# MUST route the SDK response through parse_agent_json(role="reviewer").
# ===========================================================================


def test_reviewer_default_calls_parse_agent_json(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The reviewer default routes the SDK response through
    `parse_agent_json(..., role='reviewer')` — at least one call.
    Verified by spying."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    called: list = []
    real_parse = sm.parse_agent_json

    def _spy(raw, role):
        called.append({"raw": raw, "role": role})
        return real_parse(raw, role)

    monkeypatch.setattr(sm, "parse_agent_json", _spy)
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    reviewer_calls = [c for c in called if c["role"] == "reviewer"]
    assert len(reviewer_calls) >= 1, (
        f"expected at least one parse_agent_json call with role='reviewer'; "
        f"got calls {[c['role'] for c in called]!r}"
    )


def test_reviewer_default_parse_agent_json_receives_sdk_response(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The `raw` arg to parse_agent_json is the reviewer-stage SDK
    response text VERBATIM."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    captured: list = []
    real_parse = sm.parse_agent_json
    sentinel_resp = json.dumps({"approved": True,
                                "test_result": "VERBATIM-CHECK"})

    def _spy(raw, role):
        if role == "reviewer":
            captured.append(raw)
        return real_parse(raw, role)

    monkeypatch.setattr(sm, "parse_agent_json", _spy)
    _install_fake_anthropic(monkeypatch, response_text=sentinel_resp)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    assert sentinel_resp in captured, (
        f"expected parse_agent_json to receive {sentinel_resp!r} VERBATIM; "
        f"got captured calls: {captured!r}"
    )


def test_reviewer_default_parse_role_is_reviewer(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The `role` arg to parse_agent_json is exactly the string
    'reviewer' (not 'review' or 'reviewers')."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    seen_roles: list = []
    real_parse = sm.parse_agent_json

    def _spy(raw, role):
        seen_roles.append(role)
        return real_parse(raw, role)

    monkeypatch.setattr(sm, "parse_agent_json", _spy)
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    assert "reviewer" in seen_roles, (
        f"expected at least one parse_agent_json(role='reviewer') call; "
        f"got roles {seen_roles!r}"
    )


def test_reviewer_default_does_not_use_other_role_for_parse(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The reviewer-stage parse_agent_json call uses role='reviewer',
    NOT 'decompose' / 'test_writer' / 'coder'."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    seen_roles: list = []
    real_parse = sm.parse_agent_json

    def _spy(raw, role):
        seen_roles.append(role)
        return real_parse(raw, role)

    monkeypatch.setattr(sm, "parse_agent_json", _spy)
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    # In end-to-end with test_writer + coder stubbed, the ONLY
    # parse_agent_json call should be 'reviewer'. (Stories 7/8 don't
    # parse, and stubs skip the defaults entirely.)
    for r in seen_roles:
        assert r == "reviewer", (
            f"only 'reviewer' parse calls expected when other stages "
            f"are stubbed; saw {r!r} in {seen_roles!r}"
        )


# ===========================================================================
# Category K — Shape validation (12 tests)
#
# The parsed JSON object MUST be a dict with exactly `approved` (bool)
# and `test_result` (str). Violations raise ReviewerAgentError with a
# descriptive message naming the violation.
# ===========================================================================


def test_shape_violation_missing_approved_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Missing `approved` key -> ReviewerAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({"test_result": "all good"})
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_violation_missing_approved_message_names_key(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Missing `approved` -> error message mentions `approved`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({"test_result": "all good"})
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError) as exc_info:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    assert "approved" in str(exc_info.value).lower(), (
        f"expected error message to mention 'approved'; got: "
        f"{exc_info.value!s}"
    )


def test_shape_violation_missing_test_result_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Missing `test_result` key -> ReviewerAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({"approved": True})
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_violation_missing_test_result_message_names_key(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Missing `test_result` -> error message mentions `test_result`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({"approved": True})
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError) as exc_info:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    assert "test_result" in str(exc_info.value).lower(), (
        f"expected error message to mention 'test_result'; got: "
        f"{exc_info.value!s}"
    )


def test_shape_violation_extra_key_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Extra unknown key -> ReviewerAgentError. Exact-shape contract:
    the dict must contain EXACTLY `approved` and `test_result`, no
    more."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({
        "approved": True,
        "test_result": "all good",
        "extra_unknown_key": "noise",
    })
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_violation_approved_not_bool_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`approved` value is a string instead of bool -> ReviewerAgentError.
    The contract is STRICT: stringly-typed 'true' is NOT bool True."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({"approved": "true", "test_result": "ok"})
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_violation_approved_int_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`approved` value is an int instead of bool -> ReviewerAgentError.
    `1` is NOT the same as `True` under strict shape validation. (Note:
    in Python `True == 1` and `isinstance(True, int)`; the contract
    requires bool specifically, so the test pins `bool` type rather
    than truthiness.)"""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({"approved": 1, "test_result": "ok"})
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_violation_test_result_not_str_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`test_result` value is an int instead of str ->
    ReviewerAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({"approved": True, "test_result": 0})
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_violation_top_level_is_list_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Top-level is a list (valid JSON) -> ReviewerAgentError. The
    contract requires a dict."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps([{"approved": True, "test_result": "ok"}])
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_violation_top_level_is_int_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Top-level is an int (valid JSON) -> ReviewerAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = "42"
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_violation_top_level_is_null_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Top-level is null/None (valid JSON) -> ReviewerAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = "null"
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_violation_top_level_is_string_raises(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Top-level is a JSON string (valid JSON) -> ReviewerAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = '"just a string"'
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_shape_validation_approve_false_is_valid(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`{"approved": false, "test_result": "..."}` is a VALID shape
    (the contract allows false; only structural problems are errors)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    good = json.dumps({"approved": False, "test_result": "3 failed"})
    _install_fake_anthropic(monkeypatch, response_text=good)
    # Should NOT raise — false is a valid approved value.
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())


# ===========================================================================
# Category L — Parse failures still typed as ReviewerAgentError (5 tests)
#
# Story 4 contract: parse_agent_json(raw, role="reviewer") on malformed
# JSON raises ReviewerAgentError. The reviewer default must NOT double-
# wrap that error.
# ===========================================================================


def test_malformed_json_raises_reviewer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Reviewer SDK returns non-JSON garbage -> ReviewerAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text="this is not valid JSON {{{")
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_empty_reviewer_response_raises_reviewer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Empty-string reviewer response -> ReviewerAgentError (json.loads
    fails on empty string)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch, response_text="")
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_malformed_json_not_double_wrapped(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Parse failure already typed as ReviewerAgentError by Story 4's
    helper — the reviewer default must NOT catch + re-wrap it (no
    `from inner` chaining where the inner is already a
    ReviewerAgentError)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch, response_text="garbage{")
    with pytest.raises(sm.ReviewerAgentError) as exc_info:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    err = exc_info.value
    # If the default double-wrapped, err.__cause__ would itself be a
    # ReviewerAgentError. Pin that this is NOT the case.
    cause = err.__cause__
    if isinstance(cause, sm.ReviewerAgentError):
        pytest.fail(
            f"reviewer default must not double-wrap parse failures; "
            f"got nested ReviewerAgentError chain: outer={err!s}, "
            f"cause={cause!s}"
        )


def test_partial_json_object_raises_reviewer_agent_error(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """A truncated JSON object (parse failure) -> ReviewerAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text='{"approved": true, "test_result"')
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())


def test_reviewer_agent_error_is_value_error_subclass():
    """`ReviewerAgentError` must subclass `ValueError` so existing
    `except ValueError` callers keep working."""
    import sm
    assert issubclass(sm.ReviewerAgentError, ValueError)


# ===========================================================================
# Category M — Removed NotImplementedError for reviewer (4 tests)
#
# `execute(<id>)` with all defaults no longer raises
# NotImplementedError — Story 9 inverted the reviewer default. ALL three
# real-agent defaults are now live.
# ===========================================================================


def test_reviewer_default_no_longer_raises_not_implemented(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>)` with all spawn callables omitted does NOT raise
    NotImplementedError — Story 9 inverted the reviewer default."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    try:
        sm.execute(in_sprint[0])
    except NotImplementedError as e:
        pytest.fail(
            f"Story 9: execute() with reviewer omitted must NOT raise "
            f"NotImplementedError; got: {e!s}"
        )


def test_reviewer_explicit_none_no_longer_raises_not_implemented(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`execute(<id>, spawn_reviewer=None)` routes reviewer=None to the
    real default, NOT NotImplementedError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=None)
    except NotImplementedError as e:
        pytest.fail(
            f"Story 9: explicit spawn_reviewer=None must fall through to "
            f"real default, not raise NotImplementedError; got: {e!s}"
        )


def test_all_three_defaults_no_longer_raise_not_implemented(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """All three spawn callables omitted -> ALL three defaults fire
    (test_writer + coder + reviewer). No NotImplementedError anywhere
    in the pipeline."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    try:
        sm.execute(in_sprint[0])
    except NotImplementedError as e:
        pytest.fail(
            f"Story 9 closes the last NotImplementedError linchpin; "
            f"got: {e!s}"
        )


def test_all_three_defaults_explicit_none_no_longer_raise(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """All three spawn callables explicitly None -> no
    NotImplementedError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    try:
        sm.execute(in_sprint[0],
                   spawn_test_writer=None,
                   spawn_coder=None,
                   spawn_reviewer=None)
    except NotImplementedError as e:
        pytest.fail(
            f"Story 9: all three explicit None must fall through to real "
            f"defaults; got: {e!s}"
        )


# ===========================================================================
# Category N — Injectable reviewer callable still works (5 tests)
# ===========================================================================


def test_injectable_reviewer_callable_bypasses_real_default(
        isolated_log, no_api_key_env, clean_resolver_env, monkeypatch):
    """Injecting `spawn_reviewer=callable` bypasses the real reviewer
    default — even with NO `ANTHROPIC_API_KEY`, the call succeeds
    (test_writer + coder also injected)."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    def _spawn(role_spec_path, story, test_code, impl_code):
        return {"approved": True, "test_result": "injected ok"}

    result = sm.execute(in_sprint[0],
                        spawn_test_writer=_make_test_writer(),
                        spawn_coder=_make_coder(),
                        spawn_reviewer=_spawn)
    assert isinstance(result, dict)


def test_injectable_reviewer_callable_does_not_construct_sdk_client(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Injected reviewer callable means the fake SDK is never touched
    at the reviewer stage (with all stages stubbed)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    _FakeAnthropicClient.instances = []

    def _spawn(role_spec_path, story, test_code, impl_code):
        return {"approved": True, "test_result": "injected ok"}

    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_spawn)
    assert _FakeAnthropicClient.instances == [], (
        "expected NO fake-SDK construction when all spawn callables are "
        "injected"
    )


def test_injectable_reviewer_callable_receives_four_args(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The injected reviewer callable receives `(role_spec_path,
    story, test_code, impl_code)`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target_id = in_sprint[0]
    captured: dict = {}
    tc_marker = "# INJ-REV-TC\ndef test_y(): pass\n"
    ic_marker = "# INJ-REV-IC\ndef impl_y(): pass\n"

    def _spawn(role_spec_path, story, test_code, impl_code):
        captured["role_spec_path"] = role_spec_path
        captured["story"] = dict(story)
        captured["test_code"] = test_code
        captured["impl_code"] = impl_code
        return {"approved": True, "test_result": "ok"}

    sm.execute(target_id,
               spawn_test_writer=_make_test_writer(test_code=tc_marker),
               spawn_coder=_make_coder(impl_code=ic_marker),
               spawn_reviewer=_spawn)
    assert isinstance(captured["role_spec_path"], str)
    assert captured["role_spec_path"].endswith("reviewer.md")
    assert captured["story"]["story_id"] == target_id
    assert captured["test_code"] == tc_marker
    assert captured["impl_code"] == ic_marker


def test_injectable_reviewer_callable_return_value_used(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The injected reviewer callable's return value drives the
    accept/reject branch."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    def _spawn(role_spec_path, story, test_code, impl_code):
        return {"approved": False, "test_result": "1 failure"}

    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_spawn)
    entries = list(sm.read_entries())
    state_changes = [e for e in entries
                     if e.get("type") == "story_state_change"
                     and e.get("story_id") == in_sprint[0]]
    final = state_changes[-1]
    assert final["new_state"] == "rejected", (
        f"injected reject must drive final state to rejected; got "
        f"{final['new_state']!r}"
    )


def test_injectable_reviewer_callable_exception_propagates(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """An injected reviewer callable that raises has its exception
    propagated verbatim."""
    import sm
    _, in_sprint, _ = _seed_sprint()

    class _SpawnFailure(RuntimeError):
        pass

    def _spawn(role_spec_path, story, test_code, impl_code):
        raise _SpawnFailure("custom reviewer callable failure")

    with pytest.raises(_SpawnFailure):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_spawn)


# ===========================================================================
# Category O — Caller-bind via sys.modules (2 tests)
# ===========================================================================


def test_monkeypatch_on_reviewer_default_takes_effect(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`monkeypatch.setattr(sm, "_default_execute_reviewer_spawn",
    fake)` replaces what `execute` calls when reviewer is omitted."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    called = {"count": 0}

    def _patched(role_spec_path, story, test_code, impl_code):
        called["count"] += 1
        return {"approved": True, "test_result": "monkeypatched ok"}

    monkeypatch.setattr(sm, "_default_execute_reviewer_spawn", _patched)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    assert called["count"] == 1, (
        f"expected monkeypatched reviewer default to be called exactly "
        f"once; got {called['count']}"
    )


def test_monkeypatch_reviewer_default_does_not_touch_sdk(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """A monkeypatched reviewer default that doesn't call the SDK
    leaves the fake SDK untouched at the reviewer stage."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    _FakeAnthropicClient.instances = []

    def _patched(role_spec_path, story, test_code, impl_code):
        return {"approved": True, "test_result": "patched"}

    monkeypatch.setattr(sm, "_default_execute_reviewer_spawn", _patched)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    assert _FakeAnthropicClient.instances == [], (
        "monkeypatched reviewer default must REPLACE the real default; "
        "with test_writer + coder also stubbed, the fake SDK should NOT "
        "be touched"
    )


# ===========================================================================
# Category P — Message shape (5 tests)
# ===========================================================================


def test_reviewer_messages_arg_is_a_list(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`messages` reaching the reviewer-stage SDK is a list."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    assert isinstance(call["messages"], list)


def test_reviewer_messages_arg_is_non_empty(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """`messages` reaching the reviewer-stage SDK is non-empty."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    assert len(call["messages"]) >= 1


def test_reviewer_messages_have_user_role(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """At least one message has `role='user'`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    roles = [m.get("role") for m in call["messages"]]
    assert "user" in roles


def test_reviewer_messages_carry_all_four_pieces(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The reviewer-stage user message bundles ALL FOUR — role-spec,
    story dict, test_code, AND impl_code."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel_tc = "# COMBO-SENTINEL-TC\ndef test_combo(): assert True\n"
    sentinel_ic = "# COMBO-SENTINEL-IC\ndef combo_impl(): return 1\n"
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(test_code=sentinel_tc),
               spawn_coder=_make_coder(impl_code=sentinel_ic))
    call = _captured_reviewer_call_with_stubs()
    msg_text = _flatten_message_content(call["messages"])
    role_text = _read_reviewer_role_spec_text().strip()
    assert role_text in msg_text, "role spec missing"
    assert in_sprint[0] in msg_text, "story_id missing"
    assert sentinel_tc in msg_text, "test_code missing"
    assert sentinel_ic in msg_text, "impl_code missing"


def test_reviewer_messages_have_user_first(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The first message has role 'user' (no system-turn first)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    call = _captured_reviewer_call_with_stubs()
    assert call["messages"][0].get("role") == "user", (
        f"expected first message role 'user'; got "
        f"{call['messages'][0].get('role')!r}"
    )


# ===========================================================================
# Category Q — Static grep: reviewer default routes through provider
# seam (3 tests)
# ===========================================================================


def test_reviewer_default_routes_through_invoke_anthropic_seam(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Runtime: invoking the reviewer default calls the provider seam."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_reviewer_default_resolves_api_key_at_call_time(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Two calls with two different `ANTHROPIC_API_KEY` values use the
    LATEST value for each reviewer-stage construction."""
    import sm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-key-A-reviewer")
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    first_api_key = _FakeAnthropicClient.instances[0].api_key

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-key-B-reviewer")
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[1],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    second_api_key = _FakeAnthropicClient.instances[0].api_key

    assert first_api_key == "sk-key-A-reviewer"
    assert second_api_key == "sk-key-B-reviewer"


def test_reviewer_default_does_not_import_anthropic_directly():
    """Static check: `import anthropic` / `from anthropic` inside the
    `_default_execute_reviewer_spawn` function body is zero."""
    src = _read_sm_source()
    lines = src.splitlines()
    def_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^def\s+_default_execute_reviewer_spawn\s*\(", line):
            def_idx = i
            break
    if def_idx is None:
        pytest.fail(
            "`_default_execute_reviewer_spawn` def not found at module-top-"
            "level in sm.py — Story 9's name contract is broken"
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
                f"`_default_execute_reviewer_spawn` body has a direct "
                f"`anthropic` import: {body_ln!r}. The default must "
                f"route through `_invoke_anthropic`, not import the SDK "
                f"itself."
            )


# ===========================================================================
# Category R — Failure invariants: no log entry on failure (4 tests)
# ===========================================================================


def test_reviewer_sdk_exception_does_not_write_approval_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """SDK exception at reviewer stage -> no reviewer_approval entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=ConnectionError("downed"))
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    entries = list(sm.read_entries())
    rev = [e for e in entries if e.get("type") == "reviewer_approval"]
    assert len(rev) == 0, (
        f"expected NO reviewer_approval entry on SDK failure; got "
        f"{len(rev)}"
    )


def test_reviewer_shape_violation_does_not_write_approval_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Shape violation at reviewer stage -> no reviewer_approval
    entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({"approved": "true", "test_result": "ok"})
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    entries = list(sm.read_entries())
    rev = [e for e in entries if e.get("type") == "reviewer_approval"]
    assert len(rev) == 0


def test_reviewer_parse_failure_does_not_write_approval_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Parse failure at reviewer stage -> no reviewer_approval entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch, response_text="garbage{")
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    entries = list(sm.read_entries())
    rev = [e for e in entries if e.get("type") == "reviewer_approval"]
    assert len(rev) == 0


def test_reviewer_failure_keeps_earlier_entries(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Reviewer-stage failure happens AFTER testwriter_output +
    coder_output were written by their stubs. The audit trail must
    keep those entries (truthful audit trail). With injected stubs the
    earlier stages don't write log entries themselves — the entries
    are written by `execute()` itself between stages — so on reviewer
    failure they MUST persist."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            raise_exc=ConnectionError("downed"))
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"]
    co = [e for e in entries if e.get("type") == "coder_output"]
    assert len(tw) == 1, (
        f"expected testwriter_output entry to persist after reviewer "
        f"failure; got {len(tw)}"
    )
    assert len(co) == 1, (
        f"expected coder_output entry to persist after reviewer "
        f"failure; got {len(co)}"
    )


# ===========================================================================
# Category S — End-to-end: full 3-stage pipeline, approve path (3 tests)
# ===========================================================================


def test_e2e_all_three_defaults_fire_approve_path(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """End-to-end: execute() with all three defaults writes
    testwriter_output, coder_output, AND reviewer_approval entries
    with approved=True; final state is accepted."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    tw_resp = "# E2E-TW-9\ndef test_e2e(): assert True\n"
    cd_resp = "# E2E-CD-9\ndef e2e_impl(): return 7\n"
    rv_resp = json.dumps({"approved": True,
                          "test_result": "10 of 10 passed"})
    _install_fake_anthropic(monkeypatch,
                            response_sequence=[tw_resp, cd_resp, rv_resp])
    sm.execute(in_sprint[0])
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"]
    co = [e for e in entries if e.get("type") == "coder_output"]
    rv = [e for e in entries if e.get("type") == "reviewer_approval"]
    assert len(tw) == 1
    assert len(co) == 1
    assert len(rv) == 1
    assert tw[0]["output"] == tw_resp
    assert co[0]["output"] == cd_resp
    assert rv[0]["approved"] is True
    assert rv[0]["test_result"] == "10 of 10 passed"

    state_changes = [e for e in entries
                     if e.get("type") == "story_state_change"
                     and e.get("story_id") == in_sprint[0]]
    assert state_changes[-1]["new_state"] == "accepted"


def test_e2e_coder_output_flows_to_reviewer_default(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """End-to-end: the coder default's SDK response reaches the
    reviewer default as its `impl_code` arg AND appears verbatim in
    the reviewer's SDK message."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    tw_resp = "# E2E-PIPE-TW\ndef test_pipe(): assert True\n"
    cd_resp = "# E2E-PIPE-CD\ndef pipe_impl(): return 1\n"
    rv_resp = json.dumps({"approved": True, "test_result": "passed"})
    _install_fake_anthropic(monkeypatch,
                            response_sequence=[tw_resp, cd_resp, rv_resp])
    sm.execute(in_sprint[0])
    reviewer_call = _captured_reviewer_call_three_stage()
    msg_text = _flatten_message_content(reviewer_call["messages"])
    assert cd_resp in msg_text, (
        f"expected coder SDK response ({cd_resp!r}) to flow into the "
        f"reviewer SDK message content; first 600 chars: "
        f"{msg_text[:600]!r}"
    )
    assert tw_resp in msg_text, (
        f"expected test_writer SDK response ({tw_resp!r}) to flow into "
        f"the reviewer SDK message content as test_code"
    )


def test_e2e_full_pipeline_writes_review_approved_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """End-to-end happy path writes a single `reviewer_approval` entry
    with approved=True (i.e. the 'review_approved' branch in
    execute())."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    sm.execute(in_sprint[0])
    entries = list(sm.read_entries())
    rev = [e for e in entries if e.get("type") == "reviewer_approval"]
    assert len(rev) == 1
    assert rev[0]["approved"] is True


# ===========================================================================
# Category T — End-to-end: full 3-stage pipeline, reject path (3 tests)
# ===========================================================================


def test_e2e_all_three_defaults_fire_reject_path(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """End-to-end with reviewer returning approved=False -> final state
    is rejected; reviewer_approval entry has approved=False."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    rv_resp = json.dumps({"approved": False, "test_result": "3 failed"})
    _install_fake_anthropic(
        monkeypatch,
        response_sequence=[_DEFAULT_TEST_CODE, _DEFAULT_IMPL_CODE, rv_resp],
    )
    sm.execute(in_sprint[0])
    entries = list(sm.read_entries())
    rev = [e for e in entries if e.get("type") == "reviewer_approval"]
    assert len(rev) == 1
    assert rev[0]["approved"] is False
    state_changes = [e for e in entries
                     if e.get("type") == "story_state_change"
                     and e.get("story_id") == in_sprint[0]]
    assert state_changes[-1]["new_state"] == "rejected"


def test_e2e_reject_path_test_result_in_approval_entry(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """The reviewer's `test_result` text on a reject reaches the
    reviewer_approval entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sentinel = "REJECT-TR-SENTINEL-fef9c2-3-tests-failed"
    rv_resp = json.dumps({"approved": False, "test_result": sentinel})
    _install_fake_anthropic(
        monkeypatch,
        response_sequence=[_DEFAULT_TEST_CODE, _DEFAULT_IMPL_CODE, rv_resp],
    )
    sm.execute(in_sprint[0])
    entries = list(sm.read_entries())
    rev = [e for e in entries if e.get("type") == "reviewer_approval"][0]
    assert rev["test_result"] == sentinel


def test_e2e_only_reviewer_fails_earlier_stages_succeed(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """Scripted: test_writer + coder succeed, reviewer (#3) SDK fails.
    testwriter_output + coder_output entries persist; reviewer_approval
    does NOT. Final raise is ReviewerAgentError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    boom = ConnectionError("only-reviewer-stage-fails")
    _install_fake_anthropic(
        monkeypatch,
        response_text=_DEFAULT_REVIEWER_JSON,
        raise_exc=boom,
        raise_on_call=3,
    )
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0])
    entries = list(sm.read_entries())
    tw = [e for e in entries if e.get("type") == "testwriter_output"]
    co = [e for e in entries if e.get("type") == "coder_output"]
    rv = [e for e in entries if e.get("type") == "reviewer_approval"]
    assert len(tw) == 1, (
        f"expected test_writer entry before reviewer-stage failure; got "
        f"{len(tw)}"
    )
    assert len(co) == 1, (
        f"expected coder entry before reviewer-stage failure; got "
        f"{len(co)}"
    )
    assert len(rv) == 0, (
        f"expected NO reviewer_approval entry on reviewer-stage SDK "
        f"failure; got {len(rv)}"
    )


# ===========================================================================
# Category U — No-retry pin: exactly one .create call per stage (3 tests)
# ===========================================================================


def test_no_retry_one_create_call_per_stage_three_stage(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With all three defaults firing, exactly ONE .create call per
    stage — no retries, no extras."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_sequence=_default_three_stage_sequence())
    sm.execute(in_sprint[0])
    assert len(_FakeAnthropicClient.instances) == 3
    for i, inst in enumerate(_FakeAnthropicClient.instances):
        assert len(inst.messages.calls) == 1, (
            f"stage {i} should have exactly one create() call; got "
            f"{len(inst.messages.calls)}"
        )


def test_no_retry_reviewer_stage_alone(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """With test_writer + coder stubbed, exactly ONE reviewer-stage
    .create call — no retries."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    _install_fake_anthropic(monkeypatch,
                            response_text=_DEFAULT_REVIEWER_JSON)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder())
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_no_retry_shape_violation_one_call_only(
        isolated_log, api_key_env, clean_resolver_env, monkeypatch):
    """On shape violation at reviewer stage, the SDK is called exactly
    ONCE — the default does NOT retry hoping for a valid shape."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad = json.dumps({"approved": "yes", "test_result": "ok"})
    _install_fake_anthropic(monkeypatch, response_text=bad)
    with pytest.raises(sm.ReviewerAgentError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder())
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1, (
        f"expected ONE create call on shape violation (no retry); got "
        f"{len(_FakeAnthropicClient.instances[0].messages.calls)}"
    )
