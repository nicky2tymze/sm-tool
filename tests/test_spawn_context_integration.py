"""Iter 3 v2 Sprint 1 Story 2 — Spawn-default context-block integration.

This file pins the contract for wiring Story 1's two helpers
(`resolve_context_mode`, `assemble_spawn_context`) into the four spawn
defaults established by Iter 2 Stories 6 / 7 / 8 / 9:

  - `_default_decompose_spawn(role_spec_path, requirements) -> str`
  - `_default_execute_test_writer_spawn(role_spec_path, story) -> str`
  - `_default_execute_coder_spawn(role_spec_path, story, test_code) -> str`
  - `_default_execute_reviewer_spawn(role_spec_path, story, test_code,
    impl_code) -> dict`

CONTRACT INTERPRETATION (locked by TestWriter — Story 2 wires Story 1's
helpers into the spawn defaults; does NOT add a token-budget guard,
does NOT touch the operator-injected callable path):

  - At call time, EACH spawn default calls `resolve_context_mode()`.
    Any `ConfigError` it raises propagates verbatim (operator-typed
    error; matches Stories 7/8/9 ConfigError propagation policy).
  - When `resolve_context_mode()` returns `"full"`:
        context = assemble_spawn_context(sm_path="sm.py")
        context_text = _format_context_for_message(context)
        user_content = (
            role_spec_text
            + "\n\n## Codebase context\n\n"
            + context_text
            + "\n\n"
            + <existing-Story-6/7/8/9-content>
        )
  - When `resolve_context_mode()` returns `"minimal"`:
        user_content = <existing-Story-6/7/8/9-content>  # unchanged
  - When `resolve_context_mode()` returns `"custom"`:
        Story 2 treats "custom" as full's behavior (TestWriter pin —
        future story wires a real custom-bundle source; pinning a
        no-op now would make the env-var operator-invisible).
        Same context block as full.

PINNED DEFAULTS for `assemble_spawn_context` inputs (TestWriter
decision, locked):

  - `sm_path="sm.py"` (relative literal; CWD-relative read happens
    inside `assemble_spawn_context` via `Path(sm_path).read_text`).
    Story 1's helper already accepts strings.
  - `test_files=None` — curated test-file lists belong in a later
    operator-override story; not in spawn defaults.
  - `schemas=None` — schema registry is a separate concern (Iter 4
    candidate per the Sprint 1 plan). Empty dict would be wrong
    (Story 1 preserves `{}` as "intentionally empty" and adds the key;
    None keeps the dict's shape minimal).

PINNED SERIALIZER (TestWriter decision, locked):

  - A NEW PRIVATE helper `_format_context_for_message(context: dict)
    -> str` lives at module scope on `sm`. PRIVATE (leading
    underscore), NOT in `sm.__all__`. Pinned as a separate function
    (testable + reusable across all four spawns + easy to update when
    the bundle gains keys).
  - Signature: `_format_context_for_message(context: dict) -> str`.
    Single positional dict arg.
  - Output shape: a single string. EACH KEY in the context dict
    surfaces as a labeled subsection in the output so a reviewing
    agent can grep / parse without parsing JSON. Specifically:
        - if `"sm_content"` in context: emits a `### sm.py` subsection
          followed by the raw content (or a fenced code block — Coder
          choice; tests pin only the header literal + the content
          must appear in the output).
        - if `"test_snippets"` in context: emits a `### Test
          snippets` subsection. Each snippet emits its `path` as a
          sub-header and its `content` verbatim (tests pin only the
          presence of the header + both path and content strings).
        - if `"schemas"` in context: emits a `### Schemas` subsection
          followed by the dict (tests pin only the header + a
          recognizable bit of the dict content; framing left to
          Coder).
  - Empty-dict input: returns an empty string (or a string containing
    just whitespace — tests pin only that no header appears).
  - Returns a `str`, never `None`.

MESSAGE-SHAPE INVARIANTS (both modes):

  - `messages` passed to `_invoke_anthropic` is still a list of length
    1.
  - The single element is a dict with exactly `{"role": "user",
    "content": <str>}`. The `content` value is a `str`.
  - The role spec text still appears verbatim FIRST in the content
    (preserves Stories 6/7/8/9's lead-with-role-spec framing).

POSTURE / NO-LIVE-SDK invariants (preserved from Stories 6/7/8/9):

  - Tests inject a fake `anthropic` module into `sys.modules` BEFORE
    triggering any spawn default. The session conftest sentinel
    refuses to construct a live `Anthropic()` client; the per-test
    fake supersedes the sentinel for that test only.
  - Each spawn default still produces exactly ONE `_FakeAnthropic`
    construction and ONE `messages.create(...)` call per invocation
    — no retries, no double-fire (Story 2 changes message content,
    not call multiplicity).

CASCADES FLAGGED FOR CODER (not modified here):

  - `tests/test_decompose_real_spawn.py` Category C / D tests assert
    on the EXACT existing user-message framing (role-spec text first,
    requirements JSON after, "## Active iteration requirements"
    header literal). Once Story 2 lands, those tests STILL PASS in
    minimal mode but may now find the requirements section AFTER the
    new "## Codebase context" block in full mode. The Coder must
    decide whether to set `SM_CONTEXT_MODE=minimal` in those tests'
    monkeypatched env (preferred — they are pre-Story-2 tests) or
    update the assertions to accommodate the new section ordering.
  - Same cascade exists for `tests/test_execute_real_test_writer.py`,
    `tests/test_execute_real_coder.py`,
    `tests/test_execute_real_reviewer.py` — any test that pins exact
    user-message framing without explicitly setting SM_CONTEXT_MODE.
  - `tests/test_posture_audit.py` may need a refresh if the new
    `_format_context_for_message` helper is counted by an
    introspection check (none expected — the helper does no I/O and
    reads no env vars).

ANTI-LANE:
  - Do NOT pre-test Story 3's token-budget guard. If the assembled
    bundle is too large, Story 2 happily emits the full sm.py text.
    Story 3's guard will trim later.
  - Do NOT test the `"custom"` branch's source-of-bundle (deferred to
    a later story). Only that `"custom"` mode injects the same shape
    as `"full"`.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import pathlib
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
# Fake Anthropic SDK — mirrors Stories 6/7/8/9's `_install_fake_anthropic`
# pattern. Installed per-test into sys.modules so the lazy import inside
# `_invoke_anthropic` finds the fake.
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    """Minimal stand-in for an Anthropic content block. Carries `.text`."""

    def __init__(self, text: str = "fake response text"):
        self.text = text


class _FakeResponse:
    """Stand-in for the object returned by `client.messages.create(...)`."""

    def __init__(self, text: str = "fake response text"):
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    """Stand-in for `client.messages` — records every `.create(...)` call."""

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
    """Stand-in for `anthropic.Anthropic`. Constructed by the seam on
    every call; records the `api_key` it was constructed with and
    exposes a `.messages` subobject that records every `.create(...)`
    call."""

    instances: list = []

    def __init__(self, api_key=None, **kwargs):
        self.api_key = api_key
        self.ctor_kwargs = kwargs
        self.messages = _FakeMessages()
        _FakeAnthropicClient.instances.append(self)


def _install_fake_anthropic(monkeypatch, response_text=None, raise_exc=None):
    """Build a fake `anthropic` module and install it into `sys.modules`.

    Clears `_FakeAnthropicClient.instances` so each test starts fresh.
    """
    _FakeAnthropicClient.instances = []

    if response_text is None:
        # Sensible default that round-trips through both raw-text
        # callers (test_writer / coder) AND the JSON-routed callers
        # (decompose / reviewer). Tests that need a specific shape pass
        # their own response_text.
        response_text = json.dumps(_canonical_decompose_output())

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
# Canonical agent outputs — mirror Stories 6 + 9's helpers so the fake
# SDK returns JSON shaped for parse_agent_json.
# ---------------------------------------------------------------------------


def _canonical_decompose_output(n: int = 2) -> dict:
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


def _canonical_reviewer_output() -> dict:
    return {"approved": True, "test_result": "12 of 12 passed"}


# ---------------------------------------------------------------------------
# Fixtures
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
    """Set ANTHROPIC_API_KEY so `resolve_api_key()` succeeds."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-12345")
    return "sk-test-key-12345"


@pytest.fixture
def clean_resolver_env(monkeypatch):
    """Unset model + max_tokens env vars so defaults apply."""
    for name in (
        "SM_DECOMPOSE_MODEL", "SM_TEST_WRITER_MODEL",
        "SM_CODER_MODEL", "SM_REVIEWER_MODEL", "SM_MODEL",
        "SM_DECOMPOSE_MAX_TOKENS", "SM_TEST_WRITER_MAX_TOKENS",
        "SM_CODER_MAX_TOKENS", "SM_REVIEWER_MAX_TOKENS", "SM_MAX_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def clean_context_env(monkeypatch):
    """Wipe SM_CONTEXT_MODE so each test sees the documented default
    (`"full"`). Tests that want explicit modes use
    `monkeypatch.setenv("SM_CONTEXT_MODE", ...)` over this baseline.
    """
    monkeypatch.delenv("SM_CONTEXT_MODE", raising=False)
    return monkeypatch


@pytest.fixture
def workdir_at_package_root(monkeypatch):
    """`assemble_spawn_context(sm_path="sm.py")` reads via
    `Path("sm.py").read_text(...)` which is CWD-relative. Pin the CWD
    to the package root so the literal "sm.py" path resolves.
    """
    monkeypatch.chdir(PACKAGE_DIR)
    return PACKAGE_DIR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _captured_messages() -> list:
    """Return the `messages` kwarg of the single `messages.create(...)`
    call recorded by the last fake client. Asserts exactly one fake-
    client construction and exactly one create() call."""
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly one fake-client construction; got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    calls = _FakeAnthropicClient.instances[0].messages.calls
    assert len(calls) == 1, (
        f"expected exactly one create() call; got {len(calls)}"
    )
    return calls[0]["messages"]


def _flatten_message_content(messages: list) -> str:
    """Flatten a `messages` list into a single string for substring
    matching. Mirrors Stories 6/7/8/9's helper."""
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


def _open_iteration(iteration_id: str = "iter-1", requirements=None) -> dict:
    import sm
    if requirements is None:
        requirements = [
            {"requirement_id": "req-1", "title": "T1",
             "description": "D1", "priority": "MUST",
             "acceptance_criteria": "AC1"},
        ]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": "Story 2 test iteration",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return entry


def _seed_backlog(n: int = 5) -> list:
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


def _seed_sprint(n_stories: int = 5, cut_at: int = 3,
                 iteration_id: str = "iter-1") -> tuple:
    import sm
    _open_iteration(iteration_id=iteration_id)
    sids = _seed_backlog(n=n_stories)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at], sids[cut_at:]


def _seed_iteration_for_decompose(iteration_id: str = "iter-1",
                                  requirements=None) -> list:
    """For `decompose()` tests — opens iteration only (no backlog yet)
    and returns the requirements list."""
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
        "iteration_goal": "Story 2 decompose test iteration",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return list(requirements)


def _sample_story() -> dict:
    """Build a representative story dict for the test_writer / coder /
    reviewer spawn-direct tests (no `execute` plumbing)."""
    return {
        "story_id": "abc123",
        "sequence": 1,
        "title": "Sample Story",
        "size": "S",
        "requirement_ids": ["req-1"],
        "acceptance_criteria": "Pass tests.",
    }


def _role_spec_path_for(role: str) -> str:
    """Return the canonical role-spec path on disk for the four
    spawn-default roles. Used as the first positional arg when calling
    a spawn default directly (no `execute` plumbing)."""
    return str(SOURCE_ROLES_DIR / f"{role}.md")


# ===========================================================================
# Category A — Smoke (4 tests)
#
# Each spawn default is still importable + callable with its existing
# signature after Story 2 lands. Wiring context in must not break the
# function shape pinned by Iter 2 Stories 6/7/8/9.
# ===========================================================================


def test_smoke_decompose_spawn_still_callable():
    """`sm._default_decompose_spawn` is still importable, still
    callable, signature unchanged from Iter 2 Story 6."""
    import sm
    assert callable(sm._default_decompose_spawn)
    sig = inspect.signature(sm._default_decompose_spawn)
    names = list(sig.parameters)[:2]
    assert names == ["role_spec_path", "requirements"], (
        f"signature drift on _default_decompose_spawn; got {names!r}"
    )


def test_smoke_test_writer_spawn_still_callable():
    """`sm._default_execute_test_writer_spawn` signature unchanged from
    Iter 2 Story 7."""
    import sm
    assert callable(sm._default_execute_test_writer_spawn)
    sig = inspect.signature(sm._default_execute_test_writer_spawn)
    names = list(sig.parameters)[:2]
    assert names == ["role_spec_path", "story"], (
        f"signature drift on _default_execute_test_writer_spawn; got "
        f"{names!r}"
    )


def test_smoke_coder_spawn_still_callable():
    """`sm._default_execute_coder_spawn` signature unchanged from Iter
    2 Story 8."""
    import sm
    assert callable(sm._default_execute_coder_spawn)
    sig = inspect.signature(sm._default_execute_coder_spawn)
    names = list(sig.parameters)[:3]
    assert names == ["role_spec_path", "story", "test_code"], (
        f"signature drift on _default_execute_coder_spawn; got "
        f"{names!r}"
    )


def test_smoke_reviewer_spawn_still_callable():
    """`sm._default_execute_reviewer_spawn` signature unchanged from
    Iter 2 Story 9."""
    import sm
    assert callable(sm._default_execute_reviewer_spawn)
    sig = inspect.signature(sm._default_execute_reviewer_spawn)
    names = list(sig.parameters)[:4]
    assert names == [
        "role_spec_path", "story", "test_code", "impl_code",
    ], (
        f"signature drift on _default_execute_reviewer_spawn; got "
        f"{names!r}"
    )


# ===========================================================================
# Category B — Full mode injects "## Codebase context" header (6 tests)
#
# When SM_CONTEXT_MODE is unset (default) OR explicitly "full", each
# spawn's user message contains the "## Codebase context" header. The
# header sits AFTER the role spec text but otherwise position-free per
# the contract.
# ===========================================================================


def test_full_mode_decompose_injects_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """`_default_decompose_spawn` user message contains "## Codebase
    context" header in full mode."""
    import sm
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" in flat, (
        f"expected '## Codebase context' header in decompose user "
        f"message; got message text:\n{flat[:1000]}..."
    )


def test_full_mode_test_writer_injects_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """`_default_execute_test_writer_spawn` user message contains
    "## Codebase context" header in full mode."""
    import sm
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" in flat, (
        f"expected '## Codebase context' header in test_writer user "
        f"message; got message text:\n{flat[:1000]}..."
    )


def test_full_mode_coder_injects_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """`_default_execute_coder_spawn` user message contains "## Codebase
    context" header in full mode."""
    import sm
    _install_fake_anthropic(monkeypatch, response_text="def foo(): return 1\n")
    sm._default_execute_coder_spawn(
        _role_spec_path_for("coder"),
        _sample_story(),
        "def test_foo(): assert foo() == 1\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" in flat, (
        f"expected '## Codebase context' header in coder user message; "
        f"got message text:\n{flat[:1000]}..."
    )


def test_full_mode_reviewer_injects_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """`_default_execute_reviewer_spawn` user message contains
    "## Codebase context" header in full mode."""
    import sm
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    sm._default_execute_reviewer_spawn(
        _role_spec_path_for("reviewer"),
        _sample_story(),
        "def test_foo(): assert foo() == 1\n",
        "def foo(): return 1\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" in flat, (
        f"expected '## Codebase context' header in reviewer user "
        f"message; got message text:\n{flat[:1000]}..."
    )


def test_full_mode_via_explicit_env_var_decompose(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Explicit `SM_CONTEXT_MODE=full` injects the context header
    (verifies the explicit-set path matches the default path)."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "full")
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" in flat


def test_full_mode_header_appears_after_role_spec_text(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """The "## Codebase context" header appears AFTER the role-spec
    text in the user message. Pre-Story-2 framing puts role-spec
    first; Story 2 adds the new section but must preserve that
    lead-with-role-spec ordering."""
    import sm
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    flat = _flatten_message_content(_captured_messages())
    role_spec_text = (SOURCE_ROLES_DIR / "test_writer.md").read_text(
        encoding="utf-8"
    )
    # Find a recognizable opening line from the role spec — use the
    # first non-empty line as the anchor.
    anchor_lines = [
        ln for ln in role_spec_text.splitlines() if ln.strip()
    ]
    assert anchor_lines, "test_writer.md should contain non-empty lines"
    anchor = anchor_lines[0]
    idx_role = flat.find(anchor)
    idx_ctx = flat.find("## Codebase context")
    assert idx_role != -1, (
        f"role-spec anchor {anchor!r} missing from user message"
    )
    assert idx_ctx != -1, "context header missing from user message"
    assert idx_role < idx_ctx, (
        f"expected role-spec text BEFORE '## Codebase context'; got "
        f"role_idx={idx_role}, ctx_idx={idx_ctx}"
    )


# ===========================================================================
# Category C — Full mode includes sm.py content (4 tests)
#
# Per the pinned defaults `assemble_spawn_context(sm_path="sm.py")` is
# called when mode=="full". The resulting `sm_content` value is
# inlined into the user message so the spawned agent sees the
# operator's actual sm.py source.
# ===========================================================================


def test_full_mode_decompose_message_contains_sm_content_token(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """The user message contains a recognizable token that only
    appears in sm.py — proves the sm.py content was actually inlined
    rather than the spawn shipping a stub."""
    import sm
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    flat = _flatten_message_content(_captured_messages())
    # `def parse_agent_json` is defined exactly once in sm.py at the
    # source level; the role-spec markdown won't carry it. Mention in
    # the user message proves the bundle pulled sm.py text in.
    assert "def parse_agent_json" in flat, (
        f"expected 'def parse_agent_json' (a sm.py source token) in "
        f"decompose user message; full-mode context appears to be "
        f"missing the sm.py inlining"
    )


def test_full_mode_test_writer_message_contains_sm_content_token(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Same proof for test_writer spawn."""
    import sm
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    flat = _flatten_message_content(_captured_messages())
    assert "class DecomposeAgentError" in flat, (
        f"expected 'class DecomposeAgentError' (a sm.py source token) "
        f"in test_writer user message"
    )


def test_full_mode_coder_message_contains_sm_content_token(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Same proof for coder spawn."""
    import sm
    _install_fake_anthropic(monkeypatch, response_text="def foo(): return 1\n")
    sm._default_execute_coder_spawn(
        _role_spec_path_for("coder"),
        _sample_story(),
        "def test_foo(): pass\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "def parse_agent_json" in flat, (
        f"expected 'def parse_agent_json' (a sm.py source token) in "
        f"coder user message"
    )


def test_full_mode_reviewer_message_contains_sm_content_token(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Same proof for reviewer spawn."""
    import sm
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    sm._default_execute_reviewer_spawn(
        _role_spec_path_for("reviewer"),
        _sample_story(),
        "def test_foo(): pass\n",
        "def foo(): return 1\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "class DecomposeAgentError" in flat, (
        f"expected 'class DecomposeAgentError' (a sm.py source token) "
        f"in reviewer user message"
    )


# ===========================================================================
# Category D — Minimal mode omits context (5 tests)
#
# `SM_CONTEXT_MODE=minimal` → user message MUST NOT contain the
# "## Codebase context" header. Pre-Story-2 message shape is restored
# byte-shape-compatible (TestWriter pin: the minimal-mode output of
# Story 2 equals the pre-Story-2 output).
# ===========================================================================


def test_minimal_mode_decompose_omits_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """`SM_CONTEXT_MODE=minimal` → decompose user message MUST NOT
    contain "## Codebase context" header."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" not in flat, (
        f"expected NO '## Codebase context' header in minimal mode; "
        f"got message text:\n{flat[:1000]}..."
    )


def test_minimal_mode_test_writer_omits_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """`SM_CONTEXT_MODE=minimal` → test_writer user message MUST NOT
    contain the context header."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" not in flat


def test_minimal_mode_coder_omits_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """`SM_CONTEXT_MODE=minimal` → coder user message MUST NOT contain
    the context header."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    _install_fake_anthropic(monkeypatch, response_text="def foo(): return 1\n")
    sm._default_execute_coder_spawn(
        _role_spec_path_for("coder"),
        _sample_story(),
        "def test_foo(): pass\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" not in flat


def test_minimal_mode_reviewer_omits_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """`SM_CONTEXT_MODE=minimal` → reviewer user message MUST NOT
    contain the context header."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    sm._default_execute_reviewer_spawn(
        _role_spec_path_for("reviewer"),
        _sample_story(),
        "def test_foo(): pass\n",
        "def foo(): return 1\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" not in flat


def test_minimal_mode_test_writer_message_omits_sm_content(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """Stronger negative: in minimal mode, sm.py source tokens MUST
    NOT appear in the user message. Belt-and-braces against a Coder
    bug that accidentally inlines sm.py despite the missing header."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    flat = _flatten_message_content(_captured_messages())
    assert "def parse_agent_json" not in flat, (
        f"expected NO 'def parse_agent_json' (a sm.py source token) in "
        f"minimal-mode user message; bundle appears to be leaking"
    )


# ===========================================================================
# Category E — Message shape unchanged (5 tests)
#
# Across both modes, the SDK seam receives messages=[{"role": "user",
# "content": <str>}] — a single user-role message whose content is a
# string. No list-of-blocks, no extra messages.
# ===========================================================================


def test_message_shape_decompose_full_mode(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Decompose full-mode message shape: list[1] of {role,content}."""
    import sm
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    messages = _captured_messages()
    assert isinstance(messages, list), (
        f"expected list messages; got {type(messages).__name__}"
    )
    assert len(messages) == 1, (
        f"expected single user-turn message; got {len(messages)}"
    )
    m = messages[0]
    assert isinstance(m, dict), f"expected dict message; got {type(m).__name__}"
    assert m.get("role") == "user", (
        f"expected role='user'; got {m.get('role')!r}"
    )
    assert isinstance(m.get("content"), str), (
        f"expected str content; got "
        f"{type(m.get('content')).__name__}"
    )


def test_message_shape_test_writer_full_mode(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """TestWriter full-mode message shape: same single-string shape."""
    import sm
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    messages = _captured_messages()
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert isinstance(messages[0]["content"], str)


def test_message_shape_coder_minimal_mode(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """Coder minimal-mode message shape: same single-string shape."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    _install_fake_anthropic(monkeypatch, response_text="def foo(): return 1\n")
    sm._default_execute_coder_spawn(
        _role_spec_path_for("coder"),
        _sample_story(),
        "def test_foo(): pass\n",
    )
    messages = _captured_messages()
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert isinstance(messages[0]["content"], str)


def test_message_shape_reviewer_minimal_mode(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """Reviewer minimal-mode message shape: same single-string shape."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    sm._default_execute_reviewer_spawn(
        _role_spec_path_for("reviewer"),
        _sample_story(),
        "def test_foo(): pass\n",
        "def foo(): return 1\n",
    )
    messages = _captured_messages()
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert isinstance(messages[0]["content"], str)


def test_message_shape_reviewer_full_mode_content_is_str_not_list(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Reviewer full-mode: content is a STRING, not a list-of-blocks.
    Belt-and-braces: a sloppy implementation might append a content
    block dict instead of string-concatenating."""
    import sm
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    sm._default_execute_reviewer_spawn(
        _role_spec_path_for("reviewer"),
        _sample_story(),
        "def test_foo(): pass\n",
        "def foo(): return 1\n",
    )
    messages = _captured_messages()
    content = messages[0]["content"]
    assert isinstance(content, str), (
        f"expected str content in full mode; got "
        f"{type(content).__name__} = {content!r}"
    )


# ===========================================================================
# Category F — ConfigError propagates (4 tests)
#
# `SM_CONTEXT_MODE` set to an invalid value → resolve_context_mode
# raises ConfigError; each spawn default propagates it verbatim. No
# log write (decompose) / no fake-client construction.
# ===========================================================================


def test_config_error_propagates_decompose(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """`_default_decompose_spawn` propagates ConfigError when
    SM_CONTEXT_MODE is invalid."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "INVALID_MODE_xyz")
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm.ConfigError):
        sm.decompose()
    # And: no SDK call fired because the ConfigError raised before the
    # seam was invoked.
    assert len(_FakeAnthropicClient.instances) == 0, (
        f"expected zero fake-client constructions on ConfigError; got "
        f"{len(_FakeAnthropicClient.instances)}"
    )


def test_config_error_propagates_test_writer(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """`_default_execute_test_writer_spawn` propagates ConfigError."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "INVALID_MODE_xyz")
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    with pytest.raises(sm.ConfigError):
        sm._default_execute_test_writer_spawn(
            _role_spec_path_for("test_writer"), _sample_story()
        )
    assert len(_FakeAnthropicClient.instances) == 0


def test_config_error_propagates_coder(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """`_default_execute_coder_spawn` propagates ConfigError."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "INVALID_MODE_xyz")
    _install_fake_anthropic(monkeypatch, response_text="def foo(): return 1\n")
    with pytest.raises(sm.ConfigError):
        sm._default_execute_coder_spawn(
            _role_spec_path_for("coder"),
            _sample_story(),
            "def test_foo(): pass\n",
        )
    assert len(_FakeAnthropicClient.instances) == 0


def test_config_error_propagates_reviewer(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """`_default_execute_reviewer_spawn` propagates ConfigError."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "INVALID_MODE_xyz")
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    with pytest.raises(sm.ConfigError):
        sm._default_execute_reviewer_spawn(
            _role_spec_path_for("reviewer"),
            _sample_story(),
            "def test_foo(): pass\n",
            "def foo(): return 1\n",
        )
    assert len(_FakeAnthropicClient.instances) == 0


# ===========================================================================
# Category G — assemble_spawn_context call observability (5 tests)
#
# Each spawn default in full mode calls `assemble_spawn_context` with
# `sm_path="sm.py"`. Use monkeypatch to install a spy on
# `sm.assemble_spawn_context`; verify the spawn invoked it once with
# the pinned kwargs.
# ===========================================================================


class _SpyContext:
    """Records calls to a stand-in `assemble_spawn_context`."""

    def __init__(self, return_value=None):
        self.calls: list = []
        self.return_value = return_value if return_value is not None else {
            "sm_content": "STUB SM CONTENT (spy)\n",
        }

    def __call__(self, sm_path=None, test_files=None, schemas=None):
        self.calls.append({
            "sm_path": sm_path,
            "test_files": test_files,
            "schemas": schemas,
        })
        return self.return_value


def test_assemble_spawn_context_called_by_decompose(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """In full mode, `_default_decompose_spawn` calls
    `assemble_spawn_context` exactly once with the pinned kwargs."""
    import sm
    spy = _SpyContext()
    monkeypatch.setattr(sm, "assemble_spawn_context", spy)
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    assert len(spy.calls) == 1, (
        f"expected exactly one assemble_spawn_context call; got "
        f"{len(spy.calls)}"
    )
    assert spy.calls[0]["sm_path"] == "sm.py", (
        f"expected sm_path='sm.py'; got "
        f"{spy.calls[0]['sm_path']!r}"
    )


def test_assemble_spawn_context_called_by_test_writer(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """test_writer spawn calls `assemble_spawn_context` once."""
    import sm
    spy = _SpyContext()
    monkeypatch.setattr(sm, "assemble_spawn_context", spy)
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    assert len(spy.calls) == 1
    assert spy.calls[0]["sm_path"] == "sm.py"


def test_assemble_spawn_context_called_by_coder(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """coder spawn calls `assemble_spawn_context` once."""
    import sm
    spy = _SpyContext()
    monkeypatch.setattr(sm, "assemble_spawn_context", spy)
    _install_fake_anthropic(monkeypatch, response_text="def foo(): return 1\n")
    sm._default_execute_coder_spawn(
        _role_spec_path_for("coder"),
        _sample_story(),
        "def test_foo(): pass\n",
    )
    assert len(spy.calls) == 1
    assert spy.calls[0]["sm_path"] == "sm.py"


def test_assemble_spawn_context_called_by_reviewer(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """reviewer spawn calls `assemble_spawn_context` once."""
    import sm
    spy = _SpyContext()
    monkeypatch.setattr(sm, "assemble_spawn_context", spy)
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    sm._default_execute_reviewer_spawn(
        _role_spec_path_for("reviewer"),
        _sample_story(),
        "def test_foo(): pass\n",
        "def foo(): return 1\n",
    )
    assert len(spy.calls) == 1
    assert spy.calls[0]["sm_path"] == "sm.py"


def test_assemble_spawn_context_NOT_called_in_minimal_mode(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """In minimal mode, `assemble_spawn_context` is NOT called at all
    (no bundling work to do). Tests the bypass path."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    spy = _SpyContext()
    monkeypatch.setattr(sm, "assemble_spawn_context", spy)
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    assert len(spy.calls) == 0, (
        f"expected NO assemble_spawn_context call in minimal mode; got "
        f"{len(spy.calls)}"
    )


# ===========================================================================
# Category H — _format_context_for_message helper (5 tests)
#
# Story 2 adds a NEW private helper `_format_context_for_message`
# that converts a context dict (Story 1's `assemble_spawn_context`
# output) into a string suitable for embedding in the user message.
# Pinned as a separate function for testability + reuse across the
# four spawn defaults. PRIVATE, NOT in __all__.
# ===========================================================================


def test_format_context_for_message_exists():
    """`sm._format_context_for_message` is defined at module scope."""
    import sm
    assert hasattr(sm, "_format_context_for_message"), (
        "expected `_format_context_for_message` to be defined on the "
        f"sm module; missing from dir(sm)="
        f"{sorted(n for n in dir(sm) if 'format_context' in n.lower())!r}"
    )


def test_format_context_for_message_is_callable_and_private():
    """The helper is callable, PRIVATE (leading underscore), NOT in
    __all__."""
    import sm
    assert callable(sm._format_context_for_message)
    assert not hasattr(sm, "format_context_for_message"), (
        "expected no public alias `format_context_for_message`"
    )
    assert "_format_context_for_message" not in sm.__all__, (
        f"`_format_context_for_message` must NOT be in sm.__all__; "
        f"got {sm.__all__!r}"
    )


def test_format_context_for_message_returns_str():
    """`_format_context_for_message(dict) -> str`. Returns a string
    (not None, not bytes) for both populated and empty input."""
    import sm
    out_populated = sm._format_context_for_message({
        "sm_content": "def foo(): pass\n",
    })
    assert isinstance(out_populated, str), (
        f"expected str return for populated input; got "
        f"{type(out_populated).__name__}"
    )
    out_empty = sm._format_context_for_message({})
    assert isinstance(out_empty, str), (
        f"expected str return for empty input; got "
        f"{type(out_empty).__name__}"
    )


def test_format_context_for_message_includes_sm_content():
    """When the input dict has `sm_content`, the output string
    contains that content verbatim (or at least a recognizable
    substring)."""
    import sm
    marker = "MARKER_TOKEN_AbCdEfG_unique_8675309\n"
    out = sm._format_context_for_message({"sm_content": marker})
    assert marker.rstrip() in out, (
        f"expected sm_content marker {marker!r} in formatted output; "
        f"got {out!r}"
    )


def test_format_context_for_message_empty_dict_no_header():
    """Empty-dict input → output does NOT contain a sm-content
    sub-header. Pin: when there's nothing to bundle, the formatter
    emits a string with no labeled subsections."""
    import sm
    out = sm._format_context_for_message({})
    # No sm.py sub-header (it would mislead a reader into thinking
    # content was elided).
    assert "### sm.py" not in out, (
        f"expected NO '### sm.py' sub-header for empty-dict input; "
        f"got {out!r}"
    )


# ===========================================================================
# Category I — SDK call multiplicity preserved (4 tests)
#
# Story 2 changes message CONTENT, not call COUNT. Each spawn still
# fires exactly one SDK call (no retries, no double-fire on the new
# code path) — in both full and minimal modes.
# ===========================================================================


def test_decompose_full_mode_fires_exactly_one_sdk_call(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Decompose full mode → exactly one fake-client construction +
    one `messages.create(...)` call."""
    import sm
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly one fake-client construction; got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    calls = _FakeAnthropicClient.instances[0].messages.calls
    assert len(calls) == 1, (
        f"expected exactly one create() call; got {len(calls)}"
    )


def test_test_writer_full_mode_fires_exactly_one_sdk_call(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """TestWriter full mode → exactly one SDK call."""
    import sm
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_coder_minimal_mode_fires_exactly_one_sdk_call(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, monkeypatch):
    """Coder minimal mode → exactly one SDK call."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    _install_fake_anthropic(monkeypatch, response_text="def foo(): return 1\n")
    sm._default_execute_coder_spawn(
        _role_spec_path_for("coder"),
        _sample_story(),
        "def test_foo(): pass\n",
    )
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_reviewer_full_mode_fires_exactly_one_sdk_call(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Reviewer full mode → exactly one SDK call."""
    import sm
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    sm._default_execute_reviewer_spawn(
        _role_spec_path_for("reviewer"),
        _sample_story(),
        "def test_foo(): pass\n",
        "def foo(): return 1\n",
    )
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


# ===========================================================================
# Category J — Pre-Story-2 content preserved (4 tests)
#
# Story 2 ADDS a context section; it must NOT REMOVE the existing
# Stories 6/7/8/9 framing. Each spawn's pre-existing header literal
# (e.g., "## Active iteration requirements" for decompose, "## Active
# story" for test_writer / coder, "## Story under review" for
# reviewer) must still appear in both full AND minimal modes.
# ===========================================================================


def test_decompose_full_mode_preserves_requirements_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Story 6's "## Active iteration requirements" header is preserved
    when Story 2 adds the context block."""
    import sm
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    flat = _flatten_message_content(_captured_messages())
    assert "## Active iteration requirements" in flat, (
        f"Story 2 dropped Story 6's '## Active iteration requirements' "
        f"header; got message text:\n{flat[:1200]}..."
    )


def test_test_writer_full_mode_preserves_active_story_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Story 7's "## Active story" header is preserved."""
    import sm
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Active story" in flat, (
        f"Story 2 dropped Story 7's '## Active story' header"
    )


def test_coder_full_mode_preserves_test_code_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Story 8's "## Test code to implement against" header is
    preserved."""
    import sm
    _install_fake_anthropic(monkeypatch, response_text="def foo(): return 1\n")
    sm._default_execute_coder_spawn(
        _role_spec_path_for("coder"),
        _sample_story(),
        "def test_foo(): pass\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Test code to implement against" in flat, (
        f"Story 2 dropped Story 8's '## Test code to implement against' "
        f"header"
    )


def test_reviewer_full_mode_preserves_under_review_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """Story 9's "## Story under review" header is preserved."""
    import sm
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    sm._default_execute_reviewer_spawn(
        _role_spec_path_for("reviewer"),
        _sample_story(),
        "def test_foo(): pass\n",
        "def foo(): return 1\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Story under review" in flat, (
        f"Story 2 dropped Story 9's '## Story under review' header"
    )


# ===========================================================================
# Category K — Custom mode treated as full (4 tests)
#
# TestWriter decision: `SM_CONTEXT_MODE=custom` is wired through Story
# 1's resolver and accepted as a valid mode. Story 2 has no real
# custom-bundle source yet, so it falls back to the `full`-mode bundle
# shape (same `sm_path="sm.py"` + serializer). Pinning custom-as-full
# now keeps the env-var operator-visible even when the wiring is
# incomplete — a future story will wire a real custom source.
# ===========================================================================


def test_custom_mode_decompose_injects_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """`SM_CONTEXT_MODE=custom` → decompose injects the context
    header (custom behaves like full for Story 2)."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "custom")
    _seed_iteration_for_decompose()
    _install_fake_anthropic(monkeypatch)
    sm.decompose()
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" in flat


def test_custom_mode_test_writer_injects_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """`SM_CONTEXT_MODE=custom` → test_writer injects context header."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "custom")
    _install_fake_anthropic(monkeypatch, response_text="def test_x(): pass\n")
    sm._default_execute_test_writer_spawn(
        _role_spec_path_for("test_writer"), _sample_story()
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" in flat


def test_custom_mode_coder_injects_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """`SM_CONTEXT_MODE=custom` → coder injects context header."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "custom")
    _install_fake_anthropic(monkeypatch, response_text="def foo(): return 1\n")
    sm._default_execute_coder_spawn(
        _role_spec_path_for("coder"),
        _sample_story(),
        "def test_foo(): pass\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" in flat


def test_custom_mode_reviewer_injects_context_header(
        isolated_log, api_key_env, clean_resolver_env,
        clean_context_env, workdir_at_package_root, monkeypatch):
    """`SM_CONTEXT_MODE=custom` → reviewer injects context header."""
    import sm
    monkeypatch.setenv("SM_CONTEXT_MODE", "custom")
    _install_fake_anthropic(
        monkeypatch, response_text=json.dumps(_canonical_reviewer_output())
    )
    sm._default_execute_reviewer_spawn(
        _role_spec_path_for("reviewer"),
        _sample_story(),
        "def test_foo(): pass\n",
        "def foo(): return 1\n",
    )
    flat = _flatten_message_content(_captured_messages())
    assert "## Codebase context" in flat
