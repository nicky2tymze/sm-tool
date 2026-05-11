"""Iter 3 v2 Sprint 1 Story 3 — Token budget guard for spawn context.

This file pins the contract for the pre-spawn token-budget guard that
runs in each of the four real-agent spawn defaults established by Iter
2 Stories 6 / 7 / 8 / 9. After Story 2 wired the codebase-context block
into the user message, a poorly-configured spawn could emit a message
larger than Claude's input window. Story 3 catches that BEFORE the SDK
round-trip and raises a typed error so the operator can diagnose
without paying for a 100k+-token rejected request.

CONTRACT INTERPRETATION (locked by TestWriter):

  1. NEW PUBLIC function `count_input_tokens(messages: list[dict]) -> int`
     in `sm.__all__`:
       - Accepts a list of message dicts in the shape used by
         `_invoke_anthropic` (`[{"role": "user", "content": "..."}]`).
       - Returns an `int` estimate of input tokens.
       - Heuristic (pinned by TestWriter): `len(content) // 4` summed
         across messages where `content` is the message's string body.
         This is the Anthropic-documented "4 characters per token"
         rule of thumb; it is deterministic, stdlib-only, and matches
         the no-network posture (no SDK `count_tokens` call).
       - Empty list → returns 0.
       - Empty content string → contributes 0.
       - Unicode handling: counts CHARACTERS (Python `len(str)`), not
         bytes. A surrogate-pair-containing string still counts each
         code-point as one character.

  2. NEW PUBLIC exception class `TokenBudgetExceeded(ValueError)` in
     `sm.__all__`:
       - Subclasses `ValueError` (matches `MissingAPIKeyError` /
         `ConfigError` pattern — existing `except ValueError` callers
         continue to catch it).
       - Constructor: `TokenBudgetExceeded(limit: int, actual: int)`.
         Both kwargs REQUIRED.
       - `.limit` and `.actual` attributes carry the integer values.
       - Default `str(exc)` message mentions BOTH numbers so a CLI log
         line surfaces the budget and the overshoot without extra
         attribute lookups.

  3. NEW MODULE-LEVEL CONSTANT `_DEFAULT_TOKEN_BUDGET: int = 100_000`.
     The Story acceptance criterion pins this number — Claude 3.5 /
     Haiku 4.5 input windows comfortably exceed it, leaving headroom
     for the assistant response inside the SDK's combined budget.

  4. NEW ENV VAR `SM_TOKEN_BUDGET` (optional override, GLOBAL only —
     no per-spawn override; the four spawns share the same budget):
       - Read via `os.environ.get("SM_TOKEN_BUDGET", "")`.
       - Empty / whitespace-only → falls through to
         `_DEFAULT_TOKEN_BUDGET` (operator typo for "unset", same
         pattern as `SM_MAX_TOKENS`).
       - Invalid integer → raises `ConfigError` (a ValueError subclass)
         naming both env var and value, BEFORE any SDK call.
       - Negative budgets rejected; zero allowed (operator's call —
         every spawn will then trip the guard).
       - Added to `tests/test_posture_audit.py::_ALLOWED_ENV_VAR_READS`
         (cascade pin — same expansion pattern Story 1 used for
         `SM_CONTEXT_MODE`, Iter 2 Stories 2/3 used for
         `ANTHROPIC_API_KEY` / `SM_MODEL` / `SM_MAX_TOKENS`).

  5. NEW PUBLIC function `resolve_token_budget() -> int` in
     `sm.__all__`:
       - Reads `SM_TOKEN_BUDGET` with the precedence: env var >
         `_DEFAULT_TOKEN_BUDGET`. No per-spawn precedence (single
         global cap).
       - Returns an `int` (NOT a str).
       - Mirrors `resolve_max_tokens`'s parse / error handling
         shape — empty/whitespace → default, invalid → ConfigError,
         negative → ConfigError.

  6. `count_input_tokens(messages)` is INVOKED IN EACH OF THE FOUR
     SPAWN DEFAULTS, AFTER context assembly + message build, but
     BEFORE the `_invoke_anthropic` call:
       - decompose: `_default_decompose_spawn`
       - test_writer: `_default_execute_test_writer_spawn`
       - coder: `_default_execute_coder_spawn`
       - reviewer: `_default_execute_reviewer_spawn`
     If `count_input_tokens(messages) > resolve_token_budget()`:
        raise TokenBudgetExceeded(limit=budget, actual=count)
     The `TokenBudgetExceeded` exception PROPAGATES UNCHANGED — it
     does NOT get wrapped as `DecomposeAgentError` /
     `TestWriterAgentError` / etc. (matches `MissingAPIKeyError` and
     `ConfigError` propagation policy).

MESSAGE-SHAPE INVARIANTS (preserved from Stories 6/7/8/9 + Story 2):

  - When the guard fires, `_invoke_anthropic` is NEVER called — no
    fake-client construction, no `messages.create(...)` call. A test
    confirms by inspecting `_FakeAnthropicClient.instances` AFTER the
    raise.
  - When the guard does NOT fire (normal small-message path), the
    spawn proceeds identically to Story 2's behavior: exactly one
    fake-client construction + exactly one `messages.create(...)`
    call.

POSTURE / NO-LIVE-SDK invariants (preserved):

  - The cascade test `test_posture_audit.py` expects `SM_TOKEN_BUDGET`
    in the `_ALLOWED_ENV_VAR_READS` set. The Coder MUST add it.
  - `count_input_tokens` performs no I/O and reads no env vars; the
    posture audit does not gate it further.

ANTI-LANE (locked by TestWriter):

  - Do NOT pre-implement Story 4's mocked-SDK tests for context
    passing. This file tests the GUARD, not the context-content path.
  - Do NOT modify `sm.py`. Coder lands the implementation in response
    to these tests.
  - Do NOT touch existing tests. Cascade fixes (e.g. the
    `test_posture_audit.py` allowlist expansion) are the Coder's
    responsibility once these tests force the change.

Every test below FAILS on first run — `count_input_tokens`,
`TokenBudgetExceeded`, `_DEFAULT_TOKEN_BUDGET`, `resolve_token_budget`,
and the four-spawn guard wiring do not exist yet. The Coder
implements them to drive this suite green.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pathlib
import shutil
import sys
import types

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"
SOURCE_ROLES_DIR = PACKAGE_DIR / "roles"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# Pinned by Story 3 acceptance.
_EXPECTED_DEFAULT_TOKEN_BUDGET = 100_000

# Env vars the resolver tests need to wipe so each test sees the
# documented default precedence.
_ALL_BUDGET_ENVS = ("SM_TOKEN_BUDGET",)

# Env vars the spawn-default tests need to wipe so resolve_model /
# resolve_max_tokens / resolve_context_mode / resolve_token_budget
# all observe defaults.
_ALL_SPAWN_ENVS = (
    "SM_MODEL", "SM_MAX_TOKENS",
    "SM_DECOMPOSE_MODEL", "SM_TEST_WRITER_MODEL",
    "SM_CODER_MODEL", "SM_REVIEWER_MODEL",
    "SM_DECOMPOSE_MAX_TOKENS", "SM_TEST_WRITER_MAX_TOKENS",
    "SM_CODER_MAX_TOKENS", "SM_REVIEWER_MAX_TOKENS",
    "SM_CONTEXT_MODE", "SM_TOKEN_BUDGET",
)


# ===========================================================================
# Fake Anthropic SDK — mirrors Story 2's `_install_fake_anthropic`
# pattern. Tests that want to confirm the guard skips the SDK install
# this fake first, then assert it was never called.
# ===========================================================================


class _FakeContentBlock:
    def __init__(self, text: str = "fake response text"):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str = "fake response text"):
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
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
    instances: list = []

    def __init__(self, api_key=None, **kwargs):
        self.api_key = api_key
        self.ctor_kwargs = kwargs
        self.messages = _FakeMessages()
        _FakeAnthropicClient.instances.append(self)


def _install_fake_anthropic(monkeypatch, response_text=None):
    _FakeAnthropicClient.instances = []
    if response_text is None:
        response_text = json.dumps({"stories": []})

    class _BoundClient(_FakeAnthropicClient):
        def __init__(self, api_key=None, **kwargs):
            super().__init__(api_key=api_key, **kwargs)
            self.messages = _FakeMessages(
                response=_FakeResponse(response_text),
            )

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _BoundClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return fake_module


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def sm_module():
    """Return a freshly imported `sm` module so the test observes the
    current source state rather than a cached import."""
    if "sm" in sys.modules:
        return importlib.reload(sys.modules["sm"])
    import sm  # noqa: PLC0415
    return sm


@pytest.fixture
def clean_budget_env(monkeypatch):
    """Wipe `SM_TOKEN_BUDGET` so the resolver observes its default."""
    for name in _ALL_BUDGET_ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def clean_spawn_env(monkeypatch):
    """Wipe every env var the four spawn defaults read — model,
    max_tokens, context mode, AND token budget — so the spawn tests
    observe documented defaults."""
    for name in _ALL_SPAWN_ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def api_key_env(monkeypatch):
    """Set ANTHROPIC_API_KEY so `resolve_api_key()` succeeds."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-12345")
    return "sk-test-key-12345"


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file AND mirror the
    package `roles/` dir under `tmp_path/roles/` so `resolve_role_spec`
    finds the canonical role-spec markdown files."""
    import sm
    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    dest = tmp_path / "roles"
    if not dest.exists() and SOURCE_ROLES_DIR.is_dir():
        shutil.copytree(SOURCE_ROLES_DIR, dest)
    return log_file


@pytest.fixture
def workdir_at_package_root(monkeypatch):
    """`assemble_spawn_context(sm_path="sm.py")` reads via
    `Path("sm.py").read_text(...)` which is CWD-relative. Pin the CWD
    to the package root so the literal "sm.py" path resolves."""
    monkeypatch.chdir(PACKAGE_DIR)
    return PACKAGE_DIR


def _read_sm_source() -> str:
    return SM_PATH.read_text(encoding="utf-8")


def _role_spec_path_for(role: str) -> str:
    return str(SOURCE_ROLES_DIR / f"{role}.md")


def _sample_story() -> dict:
    return {
        "story_id": "abc123",
        "sequence": 1,
        "title": "Sample Story",
        "size": "S",
        "requirement_ids": ["req-1"],
        "acceptance_criteria": "Pass tests.",
    }


def _sample_requirements() -> list:
    return [
        {
            "requirement_id": "req-1",
            "title": "T1",
            "description": "D1",
            "priority": "MUST",
            "acceptance_criteria": "AC1",
        }
    ]


# ===========================================================================
# Category A — count_input_tokens smoke (5 tests)
# ===========================================================================


def test_count_input_tokens_exists_on_module(sm_module):
    """`sm.count_input_tokens` is defined at module scope."""
    assert hasattr(sm_module, "count_input_tokens"), (
        "expected `count_input_tokens` to be defined on the sm module"
    )


def test_count_input_tokens_is_callable(sm_module):
    """`sm.count_input_tokens` is callable."""
    obj = getattr(sm_module, "count_input_tokens", None)
    assert callable(obj), (
        f"expected `sm.count_input_tokens` callable; got "
        f"{type(obj).__name__}"
    )


def test_count_input_tokens_in_all(sm_module):
    """`count_input_tokens` is listed in `sm.__all__` — public
    surface."""
    assert "count_input_tokens" in sm_module.__all__, (
        f"`count_input_tokens` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_count_input_tokens_empty_list_returns_zero(sm_module):
    """An empty `messages` list contributes no characters → 0
    tokens."""
    got = sm_module.count_input_tokens([])
    assert got == 0, (
        f"empty messages list must yield 0 tokens; got {got!r}"
    )
    assert isinstance(got, int) and not isinstance(got, bool), (
        f"expected int return (not bool); got {type(got).__name__}"
    )


def test_count_input_tokens_returns_int(sm_module):
    """`count_input_tokens` returns a Python `int` (not a float, not a
    bool, not a string). The guard arithmetic requires int."""
    messages = [{"role": "user", "content": "hello"}]
    got = sm_module.count_input_tokens(messages)
    assert isinstance(got, int) and not isinstance(got, bool), (
        f"expected int return; got {type(got).__name__} value {got!r}"
    )


# ===========================================================================
# Category B — count_input_tokens heuristic (5 tests)
#
# Heuristic pinned by TestWriter: `len(content) // 4` summed across
# messages. 4 characters per token is the Anthropic-documented rule of
# thumb. Empty content → 0. Unicode counts by characters (Python
# `len(str)`), not bytes.
# ===========================================================================


def test_count_input_tokens_heuristic_four_chars_per_token(sm_module):
    """A 400-character message yields exactly 100 tokens under the
    `len // 4` heuristic. Anchor fixture pins the formula."""
    msg = {"role": "user", "content": "a" * 400}
    got = sm_module.count_input_tokens([msg])
    assert got == 100, (
        f"expected 100 tokens for 400-char message under len//4 "
        f"heuristic; got {got!r}"
    )


def test_count_input_tokens_single_message_nonzero(sm_module):
    """A non-trivial single message produces a nonzero count."""
    msg = {"role": "user", "content": "the quick brown fox jumps"}
    got = sm_module.count_input_tokens([msg])
    assert got > 0, (
        f"expected nonzero token count for non-empty content; got "
        f"{got!r}"
    )


def test_count_input_tokens_sums_across_messages(sm_module):
    """Token counts SUM across messages in the list — a 2-message
    list with 400 + 800 chars yields 100 + 200 = 300."""
    messages = [
        {"role": "user", "content": "a" * 400},
        {"role": "assistant", "content": "b" * 800},
    ]
    got = sm_module.count_input_tokens(messages)
    assert got == 300, (
        f"expected 300 tokens (100 + 200) for two messages of 400 + "
        f"800 chars; got {got!r}"
    )


def test_count_input_tokens_empty_content_string_is_zero(sm_module):
    """A message whose `content` is the empty string contributes 0
    tokens (NOT a sentinel value, NOT a raise)."""
    msg = {"role": "user", "content": ""}
    got = sm_module.count_input_tokens([msg])
    assert got == 0, (
        f"empty content string must yield 0 tokens; got {got!r}"
    )


def test_count_input_tokens_unicode_counted_by_characters(sm_module):
    """Unicode is counted by CHARACTERS (Python `len(str)`), not
    bytes. A 400-character string of multibyte glyphs still yields
    100 tokens under the heuristic, even though it would be more
    bytes in UTF-8."""
    # "あ" is one code point, three bytes in UTF-8. Pin len-based
    # counting: 400 chars → 100 tokens regardless of byte width.
    msg = {"role": "user", "content": "あ" * 400}
    got = sm_module.count_input_tokens([msg])
    assert got == 100, (
        f"expected 100 tokens for 400 unicode chars (chars, not "
        f"bytes); got {got!r}"
    )


# ===========================================================================
# Category C — TokenBudgetExceeded class (6 tests)
# ===========================================================================


def test_token_budget_exceeded_exists(sm_module):
    """`sm.TokenBudgetExceeded` is defined on the module."""
    assert hasattr(sm_module, "TokenBudgetExceeded"), (
        "expected `TokenBudgetExceeded` defined on the sm module"
    )


def test_token_budget_exceeded_in_all(sm_module):
    """`TokenBudgetExceeded` is in `sm.__all__` — public surface."""
    assert "TokenBudgetExceeded" in sm_module.__all__, (
        f"`TokenBudgetExceeded` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_token_budget_exceeded_subclasses_valueerror(sm_module):
    """`TokenBudgetExceeded` inherits from `ValueError` — matches
    `MissingAPIKeyError` / `ConfigError` pattern so existing
    `except ValueError` callers still catch it."""
    cls = sm_module.TokenBudgetExceeded
    assert issubclass(cls, ValueError), (
        f"TokenBudgetExceeded must subclass ValueError; mro="
        f"{cls.__mro__!r}"
    )


def test_token_budget_exceeded_constructor_takes_limit_and_actual(
    sm_module,
):
    """`TokenBudgetExceeded(limit=..., actual=...)` accepts both
    kwargs and assigns matching attributes."""
    exc = sm_module.TokenBudgetExceeded(limit=100_000, actual=200_000)
    assert exc.limit == 100_000, (
        f"expected .limit == 100000; got {exc.limit!r}"
    )
    assert exc.actual == 200_000, (
        f"expected .actual == 200000; got {exc.actual!r}"
    )


def test_token_budget_exceeded_default_message_mentions_numbers(
    sm_module,
):
    """`str(TokenBudgetExceeded(limit=A, actual=B))` mentions BOTH
    numbers so a CLI log line surfaces the budget and overshoot."""
    exc = sm_module.TokenBudgetExceeded(limit=100_000, actual=150_000)
    msg = str(exc)
    assert "100000" in msg or "100_000" in msg, (
        f"expected limit '100000' in default message; got {msg!r}"
    )
    assert "150000" in msg or "150_000" in msg, (
        f"expected actual '150000' in default message; got {msg!r}"
    )


def test_token_budget_exceeded_distinct_identity(sm_module):
    """`TokenBudgetExceeded` is its own class, not an alias for
    ValueError. Plain `ValueError("x")` must NOT match an
    `except TokenBudgetExceeded` clause."""
    cls = sm_module.TokenBudgetExceeded
    assert cls is not ValueError, (
        "TokenBudgetExceeded must be a distinct class, not ValueError"
    )
    assert not isinstance(ValueError("x"), cls), (
        "plain ValueError instances must not match TokenBudgetExceeded"
    )


# ===========================================================================
# Category D — resolve_token_budget (6 tests)
# ===========================================================================


def test_resolve_token_budget_exists(sm_module):
    """`sm.resolve_token_budget` is defined at module scope."""
    assert hasattr(sm_module, "resolve_token_budget"), (
        "expected `resolve_token_budget` defined on the sm module"
    )


def test_resolve_token_budget_in_all(sm_module):
    """`resolve_token_budget` is listed in `sm.__all__`."""
    assert "resolve_token_budget" in sm_module.__all__, (
        f"`resolve_token_budget` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_resolve_token_budget_default_when_env_unset(
    sm_module, clean_budget_env
):
    """No env var set → returns `_DEFAULT_TOKEN_BUDGET` (100000)."""
    got = sm_module.resolve_token_budget()
    assert got == _EXPECTED_DEFAULT_TOKEN_BUDGET, (
        f"expected default {_EXPECTED_DEFAULT_TOKEN_BUDGET}; got "
        f"{got!r}"
    )
    assert isinstance(got, int) and not isinstance(got, bool), (
        f"expected int return; got {type(got).__name__}"
    )


def test_resolve_token_budget_env_override(
    sm_module, clean_budget_env
):
    """`SM_TOKEN_BUDGET="50000"` → returns 50000 (int, not str)."""
    clean_budget_env.setenv("SM_TOKEN_BUDGET", "50000")
    got = sm_module.resolve_token_budget()
    assert got == 50000, (
        f"expected 50000 from SM_TOKEN_BUDGET=50000; got {got!r}"
    )
    assert isinstance(got, int) and not isinstance(got, bool), (
        f"expected int return (parsed); got {type(got).__name__}"
    )


def test_resolve_token_budget_empty_string_falls_through(
    sm_module, clean_budget_env
):
    """`SM_TOKEN_BUDGET=""` → falls through to default. Empty string
    is "unset", not a parse error (matches SM_MAX_TOKENS pattern)."""
    clean_budget_env.setenv("SM_TOKEN_BUDGET", "")
    got = sm_module.resolve_token_budget()
    assert got == _EXPECTED_DEFAULT_TOKEN_BUDGET, (
        f"empty SM_TOKEN_BUDGET must fall through to default; got "
        f"{got!r}"
    )


def test_resolve_token_budget_whitespace_falls_through(
    sm_module, clean_budget_env
):
    """`SM_TOKEN_BUDGET="   "` → falls through to default. Whitespace
    is "unset", not a parse error."""
    clean_budget_env.setenv("SM_TOKEN_BUDGET", "   ")
    got = sm_module.resolve_token_budget()
    assert got == _EXPECTED_DEFAULT_TOKEN_BUDGET, (
        f"whitespace SM_TOKEN_BUDGET must fall through to default; "
        f"got {got!r}"
    )


def test_resolve_token_budget_invalid_int_raises_config_error(
    sm_module, clean_budget_env
):
    """`SM_TOKEN_BUDGET="notanumber"` → raises `ConfigError`. Invalid
    means invalid; no silent fall-through to default."""
    clean_budget_env.setenv("SM_TOKEN_BUDGET", "notanumber")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_token_budget()


def test_resolve_token_budget_negative_raises_config_error(
    sm_module, clean_budget_env
):
    """`SM_TOKEN_BUDGET="-1"` → raises `ConfigError`. Negative budgets
    are nonsensical."""
    clean_budget_env.setenv("SM_TOKEN_BUDGET", "-1")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_token_budget()


# ===========================================================================
# Category E — _DEFAULT_TOKEN_BUDGET constant (2 tests)
# ===========================================================================


def test_default_token_budget_constant_exists(sm_module):
    """`_DEFAULT_TOKEN_BUDGET` is defined at module scope."""
    assert hasattr(sm_module, "_DEFAULT_TOKEN_BUDGET"), (
        "expected `_DEFAULT_TOKEN_BUDGET` constant on sm.py"
    )


def test_default_token_budget_constant_value(sm_module):
    """`_DEFAULT_TOKEN_BUDGET == 100000` per Story 3 acceptance."""
    val = sm_module._DEFAULT_TOKEN_BUDGET
    assert val == _EXPECTED_DEFAULT_TOKEN_BUDGET, (
        f"_DEFAULT_TOKEN_BUDGET must equal "
        f"{_EXPECTED_DEFAULT_TOKEN_BUDGET}; got {val!r}"
    )
    assert isinstance(val, int) and not isinstance(val, bool), (
        f"expected int constant (not bool); got {type(val).__name__}"
    )


# ===========================================================================
# Category F — Guard wired into each spawn default (8 tests)
#
# Each test drives the budget down to a tiny number (10) so the
# assembled message — which includes the full sm.py content in full
# mode — easily exceeds the cap. We then assert TokenBudgetExceeded
# fires AND the fake SDK was never invoked (no Anthropic ctor, no
# messages.create call).
# ===========================================================================


def test_guard_decompose_raises_token_budget_exceeded(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """`_default_decompose_spawn` raises `TokenBudgetExceeded` when
    the assembled message exceeds the budget."""
    monkeypatch.setenv("SM_TOKEN_BUDGET", "10")
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm_module.TokenBudgetExceeded):
        sm_module._default_decompose_spawn(
            _role_spec_path_for("sm_agent"),
            _sample_requirements(),
        )


def test_guard_test_writer_raises_token_budget_exceeded(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """`_default_execute_test_writer_spawn` raises
    `TokenBudgetExceeded` when assembled message exceeds budget."""
    monkeypatch.setenv("SM_TOKEN_BUDGET", "10")
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm_module.TokenBudgetExceeded):
        sm_module._default_execute_test_writer_spawn(
            _role_spec_path_for("test_writer"),
            _sample_story(),
        )


def test_guard_coder_raises_token_budget_exceeded(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """`_default_execute_coder_spawn` raises `TokenBudgetExceeded`
    when assembled message exceeds budget."""
    monkeypatch.setenv("SM_TOKEN_BUDGET", "10")
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm_module.TokenBudgetExceeded):
        sm_module._default_execute_coder_spawn(
            _role_spec_path_for("coder"),
            _sample_story(),
            "def test_x(): pass\n",
        )


def test_guard_reviewer_raises_token_budget_exceeded(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """`_default_execute_reviewer_spawn` raises `TokenBudgetExceeded`
    when assembled message exceeds budget."""
    monkeypatch.setenv("SM_TOKEN_BUDGET", "10")
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm_module.TokenBudgetExceeded):
        sm_module._default_execute_reviewer_spawn(
            _role_spec_path_for("reviewer"),
            _sample_story(),
            "def test_x(): pass\n",
            "def x(): return 1\n",
        )


def test_guard_decompose_propagates_unwrapped(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """`TokenBudgetExceeded` propagates UNCHANGED from
    `_default_decompose_spawn` — it is NOT wrapped as
    `DecomposeAgentError`. Matches `MissingAPIKeyError` and
    `ConfigError` propagation policy."""
    monkeypatch.setenv("SM_TOKEN_BUDGET", "10")
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm_module.TokenBudgetExceeded) as exc_info:
        sm_module._default_decompose_spawn(
            _role_spec_path_for("sm_agent"),
            _sample_requirements(),
        )
    # The caught exception's type is EXACTLY TokenBudgetExceeded,
    # not DecomposeAgentError (which would also catch as ValueError).
    assert type(exc_info.value) is sm_module.TokenBudgetExceeded, (
        f"expected exact type TokenBudgetExceeded (not a wrapping "
        f"DecomposeAgentError); got {type(exc_info.value).__name__}"
    )
    # And it must NOT be an instance of DecomposeAgentError.
    assert not isinstance(
        exc_info.value, sm_module.DecomposeAgentError
    ), "TokenBudgetExceeded must not be wrapped as DecomposeAgentError"


def test_guard_test_writer_propagates_unwrapped(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """`TokenBudgetExceeded` propagates UNCHANGED from
    `_default_execute_test_writer_spawn`."""
    monkeypatch.setenv("SM_TOKEN_BUDGET", "10")
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm_module.TokenBudgetExceeded) as exc_info:
        sm_module._default_execute_test_writer_spawn(
            _role_spec_path_for("test_writer"),
            _sample_story(),
        )
    assert not isinstance(
        exc_info.value, sm_module.TestWriterAgentError
    ), (
        "TokenBudgetExceeded must not be wrapped as "
        "TestWriterAgentError"
    )


def test_guard_fires_before_sdk_call(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """When the guard fires, `_invoke_anthropic` is NEVER called —
    no fake-client construction, no messages.create call. Pinning
    that the guard is a PRE-spawn check, not a post-spawn check."""
    monkeypatch.setenv("SM_TOKEN_BUDGET", "10")
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm_module.TokenBudgetExceeded):
        sm_module._default_decompose_spawn(
            _role_spec_path_for("sm_agent"),
            _sample_requirements(),
        )
    # Zero fake-client constructions = SDK seam was never reached.
    assert len(_FakeAnthropicClient.instances) == 0, (
        f"expected zero fake-client constructions when guard fires; "
        f"got {len(_FakeAnthropicClient.instances)}"
    )


def test_guard_normal_path_unaffected_decompose(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """When the assembled message is well under the budget (default
    100k tokens, with `SM_CONTEXT_MODE=minimal` so no sm.py content
    is bundled), the guard does NOT fire and the spawn proceeds
    normally — exactly one fake-client construction + one
    messages.create call."""
    # Minimal mode skips the codebase-context block, so the message
    # is just the role spec + requirements JSON — well under 100k
    # tokens (heuristic = chars // 4).
    monkeypatch.setenv("SM_CONTEXT_MODE", "minimal")
    _install_fake_anthropic(monkeypatch)
    # Should NOT raise — well under budget.
    sm_module._default_decompose_spawn(
        _role_spec_path_for("sm_agent"),
        _sample_requirements(),
    )
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly one fake-client construction on normal "
        f"path; got {len(_FakeAnthropicClient.instances)}"
    )
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1, (
        f"expected exactly one messages.create call on normal path; "
        f"got "
        f"{len(_FakeAnthropicClient.instances[0].messages.calls)}"
    )


# ===========================================================================
# Category G — Guard error message carries actionable detail (2 tests)
#
# The raised exception's `.limit` and `.actual` attributes carry the
# operator-actionable numbers. The CLI maps these to a diagnostic.
# ===========================================================================


def test_guard_token_budget_exceeded_carries_limit_attribute(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """The raised `TokenBudgetExceeded` carries `.limit` == the
    configured budget (10 in this test) so the operator can correlate
    with `SM_TOKEN_BUDGET`."""
    monkeypatch.setenv("SM_TOKEN_BUDGET", "10")
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm_module.TokenBudgetExceeded) as exc_info:
        sm_module._default_decompose_spawn(
            _role_spec_path_for("sm_agent"),
            _sample_requirements(),
        )
    assert exc_info.value.limit == 10, (
        f"expected .limit == 10 (the configured SM_TOKEN_BUDGET); "
        f"got {exc_info.value.limit!r}"
    )


def test_guard_token_budget_exceeded_carries_actual_attribute(
    sm_module, isolated_log, api_key_env, clean_spawn_env,
    workdir_at_package_root, monkeypatch
):
    """The raised `TokenBudgetExceeded` carries `.actual` > `.limit`
    — the count that triggered the raise. Exact value depends on
    sm.py size; we pin only the inequality so this test is robust to
    sm.py growth."""
    monkeypatch.setenv("SM_TOKEN_BUDGET", "10")
    _install_fake_anthropic(monkeypatch)
    with pytest.raises(sm_module.TokenBudgetExceeded) as exc_info:
        sm_module._default_decompose_spawn(
            _role_spec_path_for("sm_agent"),
            _sample_requirements(),
        )
    assert exc_info.value.actual > exc_info.value.limit, (
        f"expected .actual > .limit; got actual="
        f"{exc_info.value.actual!r}, limit={exc_info.value.limit!r}"
    )
    assert isinstance(exc_info.value.actual, int), (
        f"expected .actual to be int; got "
        f"{type(exc_info.value.actual).__name__}"
    )


# ===========================================================================
# Category H — Cascade pin: SM_TOKEN_BUDGET in posture audit allowlist
# (2 tests)
#
# The posture audit in `tests/test_posture_audit.py` enforces that
# `sm.py` reads ONLY env vars in its allowlist. Adding a new env var
# requires expanding the allowlist as a deliberate posture-review
# step. These tests pin that expansion.
# ===========================================================================


def test_cascade_sm_token_budget_in_posture_allowlist():
    """`SM_TOKEN_BUDGET` is listed in
    `test_posture_audit._ALLOWED_ENV_VAR_READS`. The Coder MUST add
    it — Story 3 introduces a new env-var read in sm.py, and the
    posture audit fails until the allowlist is expanded."""
    # Read the posture audit test file to inspect its allowlist literal.
    posture_text = (
        PACKAGE_DIR / "tests" / "test_posture_audit.py"
    ).read_text(encoding="utf-8")
    assert "SM_TOKEN_BUDGET" in posture_text, (
        "expected `SM_TOKEN_BUDGET` to be added to "
        "_ALLOWED_ENV_VAR_READS in tests/test_posture_audit.py — "
        "Story 3 introduces a new env-var read and the allowlist "
        "must be expanded as a cascade pin"
    )


def test_cascade_sm_token_budget_actually_read_in_sm_py():
    """`sm.py` reads `SM_TOKEN_BUDGET` (literal string) — positive
    control on the cascade. Story 3's `resolve_token_budget` must
    perform exactly one `os.environ.get("SM_TOKEN_BUDGET", ...)`
    call inside its body."""
    src = _read_sm_source()
    # Match `os.environ.get("SM_TOKEN_BUDGET"...)` or
    # `os.environ["SM_TOKEN_BUDGET"]` or `os.getenv("SM_TOKEN_BUDGET"...)`.
    import re
    pattern = re.compile(
        r"""os\.environ(?:\.get\(\s*["']SM_TOKEN_BUDGET["']"""
        r"""|\[\s*["']SM_TOKEN_BUDGET["']\s*\])"""
        r"""|os\.getenv\(\s*["']SM_TOKEN_BUDGET["']"""
    )
    hits = pattern.findall(src)
    assert len(hits) >= 1, (
        "expected at least one literal read of SM_TOKEN_BUDGET in "
        "sm.py (inside `resolve_token_budget`); found zero"
    )
