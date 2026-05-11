"""Iter 2 Story 3 — Model and max_tokens resolution with precedence.

This file pins the contract of `sm.resolve_model(role)` and
`sm.resolve_max_tokens(role)`, the module-level Haiku 4.5 model
identifier constant, the default-max-tokens constant, and the typed
`sm.ConfigError` raised on invalid integer env-var values.

Pinned clauses (verbatim from `iter2/Stories_v1.md`, Story 3):

  1. `resolve_model(role: str) -> str` returns the model id with
     precedence: per-spawn env var
     (`SM_DECOMPOSE_MODEL` / `SM_TEST_WRITER_MODEL` / `SM_CODER_MODEL`
     / `SM_REVIEWER_MODEL`) > `SM_MODEL` global > Claude Haiku 4.5
     default.
  2. `resolve_max_tokens(role: str) -> int` returns the cap with the
     same precedence pattern: per-spawn env var
     (`SM_DECOMPOSE_MAX_TOKENS` / `SM_TEST_WRITER_MAX_TOKENS` /
     `SM_CODER_MAX_TOKENS` / `SM_REVIEWER_MAX_TOKENS`) >
     `SM_MAX_TOKENS` global > `4096` default.
  3. The Haiku 4.5 default is a single module-level constant pinning
     the exact SDK model identifier per ASSUMPTION 2.
  4. Invalid integer values for any `*_MAX_TOKENS` env var raise a
     typed configuration error before any SDK call.
  5. A unit-level test fixture can vary env vars per call and observe
     the resolved values; no spawn site reads model or max_tokens env
     vars directly.

CONTRACT INTERPRETATION (locked by TestWriter):

  - Public surface: both `resolve_model` and `resolve_max_tokens`
    listed in `sm.__all__`. Typed error `ConfigError` also public.
  - Valid roles: {"decompose", "test_writer", "coder", "reviewer"}.
    Invalid role → ValueError naming the valid set.
  - Haiku 4.5 SDK identifier: `claude-haiku-4-5-20251001`.
  - Default max_tokens: 4096 (integer).
  - Empty string or whitespace-only env var = treated as "not set"
    (falls through to next precedence level).
  - Invalid int for any `*_MAX_TOKENS` → `ConfigError(ValueError)`.
  - Negative max_tokens rejected; zero allowed (operator's call).
  - Single-source-of-truth: no spawn site reads model/max_tokens env
    vars directly outside the two resolvers; grep proves it.

Every test below FAILS on first run — `resolve_model`,
`resolve_max_tokens`, `ConfigError`, and the constants do not exist
yet. The Coder implements them to drive this suite green.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import re
import sys

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# Per-spawn env vars matched to canonical role names. Used by many
# tests and by the precedence sweeps below.
_ROLE_MODEL_ENVS = {
    "decompose": "SM_DECOMPOSE_MODEL",
    "test_writer": "SM_TEST_WRITER_MODEL",
    "coder": "SM_CODER_MODEL",
    "reviewer": "SM_REVIEWER_MODEL",
}

_ROLE_MAX_TOKENS_ENVS = {
    "decompose": "SM_DECOMPOSE_MAX_TOKENS",
    "test_writer": "SM_TEST_WRITER_MAX_TOKENS",
    "coder": "SM_CODER_MAX_TOKENS",
    "reviewer": "SM_REVIEWER_MAX_TOKENS",
}

# Every env var the resolvers may read. The clean fixture wipes all of
# them so the test observes the default precedence cleanly.
_ALL_RESOLVER_ENVS = (
    "SM_MODEL",
    "SM_MAX_TOKENS",
    *_ROLE_MODEL_ENVS.values(),
    *_ROLE_MAX_TOKENS_ENVS.values(),
)

# Pinned per LOCKED_DECISION / ASSUMPTION 2: the exact Anthropic SDK
# identifier for Claude Haiku 4.5. The Coder must place this string in
# a module-level constant; the tests assert the constant's value and
# that the default-path return equals this string.
_EXPECTED_HAIKU_4_5_MODEL = "claude-haiku-4-5-20251001"
_EXPECTED_DEFAULT_MAX_TOKENS = 4096


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


@pytest.fixture
def clean_resolver_env(monkeypatch):
    """Wipe every model/max_tokens env var so the resolvers see only
    the in-test values. Restores prior state via monkeypatch."""
    for name in _ALL_RESOLVER_ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _read_sm_source() -> str:
    """Return sm.py as text. Used by static grep tests."""
    return SM_PATH.read_text(encoding="utf-8")


# ===========================================================================
# Category A — Smoke for resolve_model (5 tests)
#
# Exists on the module, public, callable, in __all__, signature accepts
# `role: str`.
# ===========================================================================


def test_resolve_model_exists_on_module(sm_module):
    """`sm.resolve_model` is defined at module scope."""
    assert hasattr(sm_module, "resolve_model"), (
        "expected `resolve_model` to be defined on the sm module; "
        f"missing from dir(sm)={sorted(n for n in dir(sm_module) if not n.startswith('_'))!r}"
    )


def test_resolve_model_is_callable(sm_module):
    """`sm.resolve_model` is callable (function or callable object)."""
    obj = getattr(sm_module, "resolve_model", None)
    assert callable(obj), (
        f"expected `sm.resolve_model` to be callable; got "
        f"{type(obj).__name__}"
    )


def test_resolve_model_is_public_name(sm_module):
    """The helper is named `resolve_model` (no leading underscore).
    Public per the contract — operator-facing resolver."""
    assert hasattr(sm_module, "resolve_model"), (
        "expected the public name `resolve_model`, not a private "
        "`_resolve_model`"
    )


def test_resolve_model_in_all(sm_module):
    """`resolve_model` is listed in `sm.__all__` so wildcard imports
    pick it up and the public surface is documented in one place."""
    assert "resolve_model" in sm_module.__all__, (
        f"`resolve_model` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_resolve_model_signature_accepts_role(sm_module):
    """`resolve_model` accepts a single positional `role` parameter.
    The unit-level test fixture invariant requires the signature to be
    callable as `resolve_model("decompose")`."""
    sig = inspect.signature(sm_module.resolve_model)
    params = list(sig.parameters.values())
    assert len(params) >= 1, (
        f"resolve_model must accept at least one parameter; got "
        f"signature {sig!s}"
    )
    first = params[0]
    assert first.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ), (
        f"first parameter of resolve_model must be positional; got "
        f"{first.kind!s} for parameter {first.name!r}"
    )


# ===========================================================================
# Category B — Smoke for resolve_max_tokens (5 tests)
#
# Same as Category A, mirrored for the max_tokens resolver.
# ===========================================================================


def test_resolve_max_tokens_exists_on_module(sm_module):
    """`sm.resolve_max_tokens` is defined at module scope."""
    assert hasattr(sm_module, "resolve_max_tokens"), (
        "expected `resolve_max_tokens` to be defined on the sm module; "
        f"missing from dir(sm)={sorted(n for n in dir(sm_module) if not n.startswith('_'))!r}"
    )


def test_resolve_max_tokens_is_callable(sm_module):
    """`sm.resolve_max_tokens` is callable."""
    obj = getattr(sm_module, "resolve_max_tokens", None)
    assert callable(obj), (
        f"expected `sm.resolve_max_tokens` to be callable; got "
        f"{type(obj).__name__}"
    )


def test_resolve_max_tokens_is_public_name(sm_module):
    """The helper is named `resolve_max_tokens` (no leading
    underscore). Public per the contract."""
    assert hasattr(sm_module, "resolve_max_tokens"), (
        "expected the public name `resolve_max_tokens`, not a private "
        "`_resolve_max_tokens`"
    )


def test_resolve_max_tokens_in_all(sm_module):
    """`resolve_max_tokens` is listed in `sm.__all__`."""
    assert "resolve_max_tokens" in sm_module.__all__, (
        f"`resolve_max_tokens` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_resolve_max_tokens_signature_accepts_role(sm_module):
    """`resolve_max_tokens` accepts a single positional `role`
    parameter."""
    sig = inspect.signature(sm_module.resolve_max_tokens)
    params = list(sig.parameters.values())
    assert len(params) >= 1, (
        f"resolve_max_tokens must accept at least one parameter; got "
        f"signature {sig!s}"
    )
    first = params[0]
    assert first.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ), (
        f"first parameter of resolve_max_tokens must be positional; "
        f"got {first.kind!s} for parameter {first.name!r}"
    )


# ===========================================================================
# Category C — ConfigError typed (4 tests)
#
# Class exists, listed in __all__, subclasses ValueError (so existing
# `except ValueError` handlers still catch it), distinct identity from
# ValueError itself.
# ===========================================================================


def test_config_error_exists(sm_module):
    """`sm.ConfigError` is defined on the module."""
    assert hasattr(sm_module, "ConfigError"), (
        "expected `ConfigError` to be defined on the sm module"
    )


def test_config_error_in_all(sm_module):
    """`ConfigError` is in `sm.__all__` — public surface."""
    assert "ConfigError" in sm_module.__all__, (
        f"`ConfigError` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_config_error_subclasses_valueerror(sm_module):
    """`ConfigError` inherits from `ValueError` so existing
    `except ValueError` handlers in the codebase still catch it."""
    cls = sm_module.ConfigError
    assert issubclass(cls, ValueError), (
        f"ConfigError must subclass ValueError; mro={cls.__mro__!r}"
    )


def test_config_error_distinct_identity(sm_module):
    """`ConfigError` is its own class, not an alias for ValueError.
    Catching it specifically must be possible."""
    cls = sm_module.ConfigError
    assert cls is not ValueError, (
        "ConfigError must be a distinct class, not ValueError itself"
    )
    assert not isinstance(ValueError("x"), cls), (
        "ConfigError must be a strict subclass — plain ValueError "
        "instances must not match it"
    )


# ===========================================================================
# Category D — Default model resolution (5 tests)
#
# With no env vars set, every valid role resolves to the Haiku 4.5
# constant.
# ===========================================================================


def test_resolve_model_default_for_decompose(sm_module, clean_resolver_env):
    """No env vars set → `resolve_model("decompose")` returns the
    Haiku 4.5 constant."""
    got = sm_module.resolve_model("decompose")
    assert got == _EXPECTED_HAIKU_4_5_MODEL, (
        f"expected Haiku 4.5 default {_EXPECTED_HAIKU_4_5_MODEL!r} "
        f"for role 'decompose'; got {got!r}"
    )


def test_resolve_model_default_for_test_writer(sm_module, clean_resolver_env):
    """No env vars set → `resolve_model("test_writer")` returns the
    Haiku 4.5 constant."""
    got = sm_module.resolve_model("test_writer")
    assert got == _EXPECTED_HAIKU_4_5_MODEL, (
        f"expected Haiku 4.5 default {_EXPECTED_HAIKU_4_5_MODEL!r} "
        f"for role 'test_writer'; got {got!r}"
    )


def test_resolve_model_default_for_coder(sm_module, clean_resolver_env):
    """No env vars set → `resolve_model("coder")` returns the Haiku
    4.5 constant."""
    got = sm_module.resolve_model("coder")
    assert got == _EXPECTED_HAIKU_4_5_MODEL, (
        f"expected Haiku 4.5 default {_EXPECTED_HAIKU_4_5_MODEL!r} "
        f"for role 'coder'; got {got!r}"
    )


def test_resolve_model_default_for_reviewer(sm_module, clean_resolver_env):
    """No env vars set → `resolve_model("reviewer")` returns the
    Haiku 4.5 constant."""
    got = sm_module.resolve_model("reviewer")
    assert got == _EXPECTED_HAIKU_4_5_MODEL, (
        f"expected Haiku 4.5 default {_EXPECTED_HAIKU_4_5_MODEL!r} "
        f"for role 'reviewer'; got {got!r}"
    )


def test_resolve_model_default_returns_str(sm_module, clean_resolver_env):
    """Default-path return is a `str` (not bytes, not None)."""
    got = sm_module.resolve_model("decompose")
    assert isinstance(got, str), (
        f"expected str return; got {type(got).__name__}"
    )


# ===========================================================================
# Category E — Default max_tokens resolution (4 tests)
#
# With no env vars set, every valid role resolves to 4096.
# ===========================================================================


def test_resolve_max_tokens_default_for_decompose(
    sm_module, clean_resolver_env
):
    """No env vars set → `resolve_max_tokens("decompose")` returns
    4096."""
    got = sm_module.resolve_max_tokens("decompose")
    assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
        f"expected default {_EXPECTED_DEFAULT_MAX_TOKENS} for role "
        f"'decompose'; got {got!r}"
    )


def test_resolve_max_tokens_default_for_test_writer(
    sm_module, clean_resolver_env
):
    """No env vars set → `resolve_max_tokens("test_writer")` returns
    4096."""
    got = sm_module.resolve_max_tokens("test_writer")
    assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
        f"expected default {_EXPECTED_DEFAULT_MAX_TOKENS} for role "
        f"'test_writer'; got {got!r}"
    )


def test_resolve_max_tokens_default_for_coder(sm_module, clean_resolver_env):
    """No env vars set → `resolve_max_tokens("coder")` returns 4096."""
    got = sm_module.resolve_max_tokens("coder")
    assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
        f"expected default {_EXPECTED_DEFAULT_MAX_TOKENS} for role "
        f"'coder'; got {got!r}"
    )


def test_resolve_max_tokens_default_for_reviewer(
    sm_module, clean_resolver_env
):
    """No env vars set → `resolve_max_tokens("reviewer")` returns
    4096; return type is `int` (not str)."""
    got = sm_module.resolve_max_tokens("reviewer")
    assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
        f"expected default {_EXPECTED_DEFAULT_MAX_TOKENS} for role "
        f"'reviewer'; got {got!r}"
    )
    assert isinstance(got, int) and not isinstance(got, bool), (
        f"expected int return (not bool); got {type(got).__name__}"
    )


# ===========================================================================
# Category F — Global SM_MODEL override (5 tests)
#
# Setting `SM_MODEL=X` causes every role to return X (in the absence
# of per-spawn overrides).
# ===========================================================================


def test_resolve_model_global_override_decompose(
    sm_module, clean_resolver_env
):
    """SM_MODEL set, no per-spawn override → decompose returns
    SM_MODEL."""
    clean_resolver_env.setenv("SM_MODEL", "claude-opus-4-7-20260101")
    got = sm_module.resolve_model("decompose")
    assert got == "claude-opus-4-7-20260101", (
        f"expected SM_MODEL value for decompose; got {got!r}"
    )


def test_resolve_model_global_override_test_writer(
    sm_module, clean_resolver_env
):
    """SM_MODEL set → test_writer returns SM_MODEL."""
    clean_resolver_env.setenv("SM_MODEL", "claude-opus-4-7-20260101")
    got = sm_module.resolve_model("test_writer")
    assert got == "claude-opus-4-7-20260101", (
        f"expected SM_MODEL value for test_writer; got {got!r}"
    )


def test_resolve_model_global_override_coder(sm_module, clean_resolver_env):
    """SM_MODEL set → coder returns SM_MODEL."""
    clean_resolver_env.setenv("SM_MODEL", "claude-opus-4-7-20260101")
    got = sm_module.resolve_model("coder")
    assert got == "claude-opus-4-7-20260101", (
        f"expected SM_MODEL value for coder; got {got!r}"
    )


def test_resolve_model_global_override_reviewer(
    sm_module, clean_resolver_env
):
    """SM_MODEL set → reviewer returns SM_MODEL."""
    clean_resolver_env.setenv("SM_MODEL", "claude-opus-4-7-20260101")
    got = sm_module.resolve_model("reviewer")
    assert got == "claude-opus-4-7-20260101", (
        f"expected SM_MODEL value for reviewer; got {got!r}"
    )


def test_resolve_model_global_override_uniform_all_roles(
    sm_module, clean_resolver_env
):
    """SM_MODEL alone applies UNIFORMLY to every canonical role —
    a single point of control over the global model."""
    clean_resolver_env.setenv("SM_MODEL", "claude-sonnet-test-id")
    results = {
        role: sm_module.resolve_model(role) for role in _ROLE_MODEL_ENVS
    }
    assert all(v == "claude-sonnet-test-id" for v in results.values()), (
        f"expected SM_MODEL to apply uniformly to all 4 roles; got "
        f"{results!r}"
    )


# ===========================================================================
# Category G — Global SM_MAX_TOKENS override (4 tests)
#
# Setting `SM_MAX_TOKENS=N` causes every role to return N.
# ===========================================================================


def test_resolve_max_tokens_global_override_decompose(
    sm_module, clean_resolver_env
):
    """SM_MAX_TOKENS set → decompose returns the integer."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "8192")
    got = sm_module.resolve_max_tokens("decompose")
    assert got == 8192, (
        f"expected SM_MAX_TOKENS value 8192 for decompose; got {got!r}"
    )
    assert isinstance(got, int) and not isinstance(got, bool), (
        f"expected int return; got {type(got).__name__}"
    )


def test_resolve_max_tokens_global_override_test_writer(
    sm_module, clean_resolver_env
):
    """SM_MAX_TOKENS set → test_writer returns the integer."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "8192")
    got = sm_module.resolve_max_tokens("test_writer")
    assert got == 8192, (
        f"expected SM_MAX_TOKENS value 8192 for test_writer; got "
        f"{got!r}"
    )


def test_resolve_max_tokens_global_override_uniform_all_roles(
    sm_module, clean_resolver_env
):
    """SM_MAX_TOKENS alone applies UNIFORMLY to every canonical role."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "2048")
    results = {
        role: sm_module.resolve_max_tokens(role)
        for role in _ROLE_MAX_TOKENS_ENVS
    }
    assert all(v == 2048 for v in results.values()), (
        f"expected SM_MAX_TOKENS to apply uniformly to all 4 roles; "
        f"got {results!r}"
    )


def test_resolve_max_tokens_global_override_returns_int_not_str(
    sm_module, clean_resolver_env
):
    """SM_MAX_TOKENS is parsed to int — not returned as a string. A
    spawn callsite asks for an int and must get an int."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "1024")
    got = sm_module.resolve_max_tokens("coder")
    assert isinstance(got, int) and not isinstance(got, bool), (
        f"expected int return; got {type(got).__name__} with value "
        f"{got!r}"
    )
    assert got == 1024, f"expected 1024; got {got!r}"


# ===========================================================================
# Category H — Per-spawn model override (4 tests)
#
# Setting one per-spawn env var affects only that role; the other
# three keep the default.
# ===========================================================================


def test_resolve_model_per_spawn_decompose_isolated(
    sm_module, clean_resolver_env
):
    """SM_DECOMPOSE_MODEL set → decompose returns it; other 3 roles
    fall through to default."""
    clean_resolver_env.setenv("SM_DECOMPOSE_MODEL", "decompose-only-id")
    assert sm_module.resolve_model("decompose") == "decompose-only-id"
    for other in ("test_writer", "coder", "reviewer"):
        got = sm_module.resolve_model(other)
        assert got == _EXPECTED_HAIKU_4_5_MODEL, (
            f"SM_DECOMPOSE_MODEL leaked to role {other!r}; got {got!r}"
        )


def test_resolve_model_per_spawn_test_writer_isolated(
    sm_module, clean_resolver_env
):
    """SM_TEST_WRITER_MODEL set → only test_writer is affected."""
    clean_resolver_env.setenv("SM_TEST_WRITER_MODEL", "tw-only-id")
    assert sm_module.resolve_model("test_writer") == "tw-only-id"
    for other in ("decompose", "coder", "reviewer"):
        got = sm_module.resolve_model(other)
        assert got == _EXPECTED_HAIKU_4_5_MODEL, (
            f"SM_TEST_WRITER_MODEL leaked to role {other!r}; got "
            f"{got!r}"
        )


def test_resolve_model_per_spawn_coder_isolated(
    sm_module, clean_resolver_env
):
    """SM_CODER_MODEL set → only coder is affected."""
    clean_resolver_env.setenv("SM_CODER_MODEL", "coder-only-id")
    assert sm_module.resolve_model("coder") == "coder-only-id"
    for other in ("decompose", "test_writer", "reviewer"):
        got = sm_module.resolve_model(other)
        assert got == _EXPECTED_HAIKU_4_5_MODEL, (
            f"SM_CODER_MODEL leaked to role {other!r}; got {got!r}"
        )


def test_resolve_model_per_spawn_reviewer_isolated(
    sm_module, clean_resolver_env
):
    """SM_REVIEWER_MODEL set → only reviewer is affected."""
    clean_resolver_env.setenv("SM_REVIEWER_MODEL", "reviewer-only-id")
    assert sm_module.resolve_model("reviewer") == "reviewer-only-id"
    for other in ("decompose", "test_writer", "coder"):
        got = sm_module.resolve_model(other)
        assert got == _EXPECTED_HAIKU_4_5_MODEL, (
            f"SM_REVIEWER_MODEL leaked to role {other!r}; got {got!r}"
        )


# ===========================================================================
# Category I — Per-spawn max_tokens override (4 tests)
#
# Setting one per-spawn max_tokens env var affects only that role.
# ===========================================================================


def test_resolve_max_tokens_per_spawn_decompose_isolated(
    sm_module, clean_resolver_env
):
    """SM_DECOMPOSE_MAX_TOKENS set → only decompose is affected."""
    clean_resolver_env.setenv("SM_DECOMPOSE_MAX_TOKENS", "12345")
    assert sm_module.resolve_max_tokens("decompose") == 12345
    for other in ("test_writer", "coder", "reviewer"):
        got = sm_module.resolve_max_tokens(other)
        assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
            f"SM_DECOMPOSE_MAX_TOKENS leaked to role {other!r}; got "
            f"{got!r}"
        )


def test_resolve_max_tokens_per_spawn_test_writer_isolated(
    sm_module, clean_resolver_env
):
    """SM_TEST_WRITER_MAX_TOKENS set → only test_writer is affected."""
    clean_resolver_env.setenv("SM_TEST_WRITER_MAX_TOKENS", "555")
    assert sm_module.resolve_max_tokens("test_writer") == 555
    for other in ("decompose", "coder", "reviewer"):
        got = sm_module.resolve_max_tokens(other)
        assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
            f"SM_TEST_WRITER_MAX_TOKENS leaked to role {other!r}; "
            f"got {got!r}"
        )


def test_resolve_max_tokens_per_spawn_coder_isolated(
    sm_module, clean_resolver_env
):
    """SM_CODER_MAX_TOKENS set → only coder is affected."""
    clean_resolver_env.setenv("SM_CODER_MAX_TOKENS", "777")
    assert sm_module.resolve_max_tokens("coder") == 777
    for other in ("decompose", "test_writer", "reviewer"):
        got = sm_module.resolve_max_tokens(other)
        assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
            f"SM_CODER_MAX_TOKENS leaked to role {other!r}; got "
            f"{got!r}"
        )


def test_resolve_max_tokens_per_spawn_reviewer_isolated(
    sm_module, clean_resolver_env
):
    """SM_REVIEWER_MAX_TOKENS set → only reviewer is affected."""
    clean_resolver_env.setenv("SM_REVIEWER_MAX_TOKENS", "999")
    assert sm_module.resolve_max_tokens("reviewer") == 999
    for other in ("decompose", "test_writer", "coder"):
        got = sm_module.resolve_max_tokens(other)
        assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
            f"SM_REVIEWER_MAX_TOKENS leaked to role {other!r}; got "
            f"{got!r}"
        )


# ===========================================================================
# Category J — Precedence: per-spawn beats global (5 tests)
#
# Both per-spawn and global set → per-spawn wins (model + max_tokens).
# ===========================================================================


def test_resolve_model_precedence_per_spawn_beats_global_decompose(
    sm_module, clean_resolver_env
):
    """SM_DECOMPOSE_MODEL + SM_MODEL both set → decompose returns the
    per-spawn value."""
    clean_resolver_env.setenv("SM_MODEL", "global-id")
    clean_resolver_env.setenv("SM_DECOMPOSE_MODEL", "per-spawn-id")
    got = sm_module.resolve_model("decompose")
    assert got == "per-spawn-id", (
        f"per-spawn must beat global; expected 'per-spawn-id', got "
        f"{got!r}"
    )


def test_resolve_model_precedence_per_spawn_beats_global_reviewer(
    sm_module, clean_resolver_env
):
    """SM_REVIEWER_MODEL + SM_MODEL both set → reviewer returns the
    per-spawn value."""
    clean_resolver_env.setenv("SM_MODEL", "global-id")
    clean_resolver_env.setenv("SM_REVIEWER_MODEL", "reviewer-id")
    got = sm_module.resolve_model("reviewer")
    assert got == "reviewer-id", (
        f"per-spawn must beat global; expected 'reviewer-id', got "
        f"{got!r}"
    )


def test_resolve_max_tokens_precedence_per_spawn_beats_global_coder(
    sm_module, clean_resolver_env
):
    """SM_CODER_MAX_TOKENS + SM_MAX_TOKENS both set → coder returns
    the per-spawn integer."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "1000")
    clean_resolver_env.setenv("SM_CODER_MAX_TOKENS", "9999")
    got = sm_module.resolve_max_tokens("coder")
    assert got == 9999, (
        f"per-spawn must beat global; expected 9999, got {got!r}"
    )


def test_resolve_max_tokens_precedence_per_spawn_beats_global_test_writer(
    sm_module, clean_resolver_env
):
    """SM_TEST_WRITER_MAX_TOKENS + SM_MAX_TOKENS both set →
    test_writer returns per-spawn."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "1000")
    clean_resolver_env.setenv("SM_TEST_WRITER_MAX_TOKENS", "256")
    got = sm_module.resolve_max_tokens("test_writer")
    assert got == 256, (
        f"per-spawn must beat global; expected 256, got {got!r}"
    )


def test_precedence_all_three_levels_set_per_spawn_wins(
    sm_module, clean_resolver_env
):
    """All three precedence levels are 'set' (default is always
    there) — per-spawn is the winner for both resolvers."""
    clean_resolver_env.setenv("SM_MODEL", "global-model")
    clean_resolver_env.setenv("SM_DECOMPOSE_MODEL", "spawn-model")
    clean_resolver_env.setenv("SM_MAX_TOKENS", "100")
    clean_resolver_env.setenv("SM_DECOMPOSE_MAX_TOKENS", "200")
    assert sm_module.resolve_model("decompose") == "spawn-model"
    assert sm_module.resolve_max_tokens("decompose") == 200


# ===========================================================================
# Category K — Precedence: global beats default (4 tests)
#
# Global set, no per-spawn → global wins.
# ===========================================================================


def test_resolve_model_precedence_global_beats_default_decompose(
    sm_module, clean_resolver_env
):
    """SM_MODEL set, no SM_DECOMPOSE_MODEL → decompose returns
    SM_MODEL (not the Haiku default)."""
    clean_resolver_env.setenv("SM_MODEL", "global-only-id")
    got = sm_module.resolve_model("decompose")
    assert got == "global-only-id", (
        f"global must beat default; expected 'global-only-id', got "
        f"{got!r}"
    )
    assert got != _EXPECTED_HAIKU_4_5_MODEL, (
        "default leaked through despite SM_MODEL being set"
    )


def test_resolve_model_precedence_global_beats_default_coder(
    sm_module, clean_resolver_env
):
    """SM_MODEL set, no SM_CODER_MODEL → coder returns SM_MODEL."""
    clean_resolver_env.setenv("SM_MODEL", "global-only-id")
    got = sm_module.resolve_model("coder")
    assert got == "global-only-id", (
        f"global must beat default; expected 'global-only-id', got "
        f"{got!r}"
    )


def test_resolve_max_tokens_precedence_global_beats_default_decompose(
    sm_module, clean_resolver_env
):
    """SM_MAX_TOKENS set, no SM_DECOMPOSE_MAX_TOKENS → decompose
    returns SM_MAX_TOKENS (not 4096)."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "16384")
    got = sm_module.resolve_max_tokens("decompose")
    assert got == 16384, (
        f"global must beat default; expected 16384, got {got!r}"
    )
    assert got != _EXPECTED_DEFAULT_MAX_TOKENS, (
        "default leaked through despite SM_MAX_TOKENS being set"
    )


def test_resolve_max_tokens_precedence_global_beats_default_reviewer(
    sm_module, clean_resolver_env
):
    """SM_MAX_TOKENS set, no SM_REVIEWER_MAX_TOKENS → reviewer returns
    SM_MAX_TOKENS."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "16384")
    got = sm_module.resolve_max_tokens("reviewer")
    assert got == 16384, (
        f"global must beat default; expected 16384, got {got!r}"
    )


# ===========================================================================
# Category L — Empty string treated as unset (4 tests)
#
# Empty SM_MODEL / per-spawn env var falls through to the next
# precedence level. Same for max_tokens — but note empty-string for
# max_tokens is "unset", NOT "invalid" (operator's clear intent is to
# not set; we don't raise on empty, we fall through).
# ===========================================================================


def test_resolve_model_empty_string_global_falls_through_to_default(
    sm_module, clean_resolver_env
):
    """`SM_MODEL=""` → falls through to default. Empty string cannot
    be a real model id."""
    clean_resolver_env.setenv("SM_MODEL", "")
    got = sm_module.resolve_model("decompose")
    assert got == _EXPECTED_HAIKU_4_5_MODEL, (
        f"empty SM_MODEL must fall through; expected default, got "
        f"{got!r}"
    )


def test_resolve_model_empty_string_per_spawn_falls_through_to_global(
    sm_module, clean_resolver_env
):
    """`SM_DECOMPOSE_MODEL=""` with SM_MODEL set → falls through to
    SM_MODEL."""
    clean_resolver_env.setenv("SM_MODEL", "global-id")
    clean_resolver_env.setenv("SM_DECOMPOSE_MODEL", "")
    got = sm_module.resolve_model("decompose")
    assert got == "global-id", (
        f"empty SM_DECOMPOSE_MODEL must fall through to SM_MODEL; got "
        f"{got!r}"
    )


def test_resolve_max_tokens_empty_string_global_falls_through_to_default(
    sm_module, clean_resolver_env
):
    """`SM_MAX_TOKENS=""` → falls through to default 4096. Empty
    string is an unset signal, not a parse error."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "")
    got = sm_module.resolve_max_tokens("decompose")
    assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
        f"empty SM_MAX_TOKENS must fall through; expected default "
        f"{_EXPECTED_DEFAULT_MAX_TOKENS}, got {got!r}"
    )


def test_resolve_max_tokens_empty_string_per_spawn_falls_through_to_global(
    sm_module, clean_resolver_env
):
    """`SM_DECOMPOSE_MAX_TOKENS=""` with SM_MAX_TOKENS set → falls
    through to SM_MAX_TOKENS."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "2222")
    clean_resolver_env.setenv("SM_DECOMPOSE_MAX_TOKENS", "")
    got = sm_module.resolve_max_tokens("decompose")
    assert got == 2222, (
        f"empty per-spawn must fall through to SM_MAX_TOKENS; got "
        f"{got!r}"
    )


# ===========================================================================
# Category M — Whitespace-only treated as unset (4 tests)
#
# Whitespace-only env var values are not valid model ids or integers;
# they're an operator typo for "unset". Fall through.
# ===========================================================================


def test_resolve_model_whitespace_global_falls_through(
    sm_module, clean_resolver_env
):
    """`SM_MODEL="   "` → falls through to default."""
    clean_resolver_env.setenv("SM_MODEL", "   ")
    got = sm_module.resolve_model("decompose")
    assert got == _EXPECTED_HAIKU_4_5_MODEL, (
        f"whitespace SM_MODEL must fall through; expected default, "
        f"got {got!r}"
    )


def test_resolve_model_whitespace_per_spawn_falls_through(
    sm_module, clean_resolver_env
):
    """`SM_CODER_MODEL="\\t  "` with SM_MODEL set → falls through to
    SM_MODEL."""
    clean_resolver_env.setenv("SM_MODEL", "global-id")
    clean_resolver_env.setenv("SM_CODER_MODEL", "\t  ")
    got = sm_module.resolve_model("coder")
    assert got == "global-id", (
        f"whitespace per-spawn must fall through to SM_MODEL; got "
        f"{got!r}"
    )


def test_resolve_max_tokens_whitespace_global_falls_through(
    sm_module, clean_resolver_env
):
    """`SM_MAX_TOKENS="   "` → falls through to default. Whitespace
    is treated as unset, not as a parse error."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "   ")
    got = sm_module.resolve_max_tokens("reviewer")
    assert got == _EXPECTED_DEFAULT_MAX_TOKENS, (
        f"whitespace SM_MAX_TOKENS must fall through; expected default "
        f"{_EXPECTED_DEFAULT_MAX_TOKENS}, got {got!r}"
    )


def test_resolve_max_tokens_whitespace_per_spawn_falls_through(
    sm_module, clean_resolver_env
):
    """`SM_TEST_WRITER_MAX_TOKENS="\\n"` with SM_MAX_TOKENS set →
    falls through to SM_MAX_TOKENS, not raised as ConfigError."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "3333")
    clean_resolver_env.setenv("SM_TEST_WRITER_MAX_TOKENS", "\n")
    got = sm_module.resolve_max_tokens("test_writer")
    assert got == 3333, (
        f"whitespace per-spawn must fall through to SM_MAX_TOKENS; "
        f"got {got!r}"
    )


# ===========================================================================
# Category N — Invalid integer for max_tokens (6 tests)
#
# Non-numeric, alphabetic, mixed, negative — every invalid form raises
# `ConfigError` naming the bad env var + value. SDK is not touched.
# ===========================================================================


def test_resolve_max_tokens_alphabetic_raises_config_error(
    sm_module, clean_resolver_env
):
    """`SM_MAX_TOKENS="abc"` → raises ConfigError. Cannot parse as
    int."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "abc")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_max_tokens("decompose")


def test_resolve_max_tokens_mixed_alphanumeric_raises_config_error(
    sm_module, clean_resolver_env
):
    """`SM_CODER_MAX_TOKENS="123abc"` → raises ConfigError."""
    clean_resolver_env.setenv("SM_CODER_MAX_TOKENS", "123abc")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_max_tokens("coder")


def test_resolve_max_tokens_decimal_raises_config_error(
    sm_module, clean_resolver_env
):
    """`SM_MAX_TOKENS="42.5"` → raises ConfigError. A float-string is
    not an integer."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "42.5")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_max_tokens("decompose")


def test_resolve_max_tokens_negative_raises_config_error(
    sm_module, clean_resolver_env
):
    """`SM_MAX_TOKENS="-100"` → raises ConfigError. Negative caps are
    nonsensical for an LLM `max_tokens` argument."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "-100")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_max_tokens("decompose")


def test_resolve_max_tokens_config_error_names_env_var_and_value(
    sm_module, clean_resolver_env
):
    """The ConfigError message names BOTH the bad env var and the bad
    value so the operator knows exactly which env var to fix."""
    clean_resolver_env.setenv("SM_REVIEWER_MAX_TOKENS", "nope")
    with pytest.raises(sm_module.ConfigError) as exc_info:
        sm_module.resolve_max_tokens("reviewer")
    msg = str(exc_info.value)
    assert "SM_REVIEWER_MAX_TOKENS" in msg, (
        f"expected env var name in ConfigError message; got {msg!r}"
    )
    assert "nope" in msg, (
        f"expected bad value 'nope' in ConfigError message; got {msg!r}"
    )


def test_resolve_max_tokens_per_spawn_invalid_raises_before_fallthrough(
    sm_module, clean_resolver_env
):
    """If SM_DECOMPOSE_MAX_TOKENS is invalid, ConfigError is raised —
    we do NOT silently fall through to SM_MAX_TOKENS or the default.
    Invalid means invalid; the operator gets told."""
    clean_resolver_env.setenv("SM_MAX_TOKENS", "2048")
    clean_resolver_env.setenv("SM_DECOMPOSE_MAX_TOKENS", "garbage")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_max_tokens("decompose")


# ===========================================================================
# Category O — Invalid role argument (4 tests)
#
# Unknown role string → ValueError naming the valid set. Empty string
# and non-string types also raise (TypeError for non-str is fine, but
# raising consistently is the contract — accept either).
# ===========================================================================


def test_resolve_model_unknown_role_raises_value_error(
    sm_module, clean_resolver_env
):
    """`resolve_model("planner")` (unknown) → ValueError. Message
    names the valid role set so the operator can correct."""
    with pytest.raises(ValueError) as exc_info:
        sm_module.resolve_model("planner")
    msg = str(exc_info.value)
    # Must mention at least one canonical role so the error is useful.
    canonical_mentioned = any(
        role in msg for role in _ROLE_MODEL_ENVS
    )
    assert canonical_mentioned, (
        f"expected ValueError message to name the valid role set; "
        f"got {msg!r}"
    )


def test_resolve_max_tokens_unknown_role_raises_value_error(
    sm_module, clean_resolver_env
):
    """`resolve_max_tokens("planner")` → ValueError naming the valid
    roles."""
    with pytest.raises(ValueError) as exc_info:
        sm_module.resolve_max_tokens("planner")
    msg = str(exc_info.value)
    canonical_mentioned = any(
        role in msg for role in _ROLE_MAX_TOKENS_ENVS
    )
    assert canonical_mentioned, (
        f"expected ValueError message to name the valid role set; "
        f"got {msg!r}"
    )


def test_resolve_model_empty_string_role_raises(
    sm_module, clean_resolver_env
):
    """`resolve_model("")` (empty role) → raises. An empty role
    cannot map to any per-spawn env var."""
    with pytest.raises((ValueError, TypeError)):
        sm_module.resolve_model("")


def test_resolve_model_non_string_role_raises(
    sm_module, clean_resolver_env
):
    """`resolve_model(None)` → raises (TypeError or ValueError).
    Non-string roles are not valid. Same with int."""
    with pytest.raises((ValueError, TypeError)):
        sm_module.resolve_model(None)  # type: ignore[arg-type]
    with pytest.raises((ValueError, TypeError)):
        sm_module.resolve_model(42)  # type: ignore[arg-type]


# ===========================================================================
# Category P — Module-level constants (4 tests)
#
# Haiku 4.5 identifier and default max_tokens are module-level
# constants with the documented values. Either `_HAIKU_4_5_MODEL` or
# `_DEFAULT_MODEL` is accepted as the name (Coder's call). The default
# max_tokens constant is `_DEFAULT_MAX_TOKENS`.
# ===========================================================================


def test_default_model_constant_exists(sm_module):
    """A module-level constant pins the default model identifier per
    ASSUMPTION 2. Accept either `_HAIKU_4_5_MODEL` or `_DEFAULT_MODEL`
    as the name — the contract is 'a single named constant'."""
    has_haiku = hasattr(sm_module, "_HAIKU_4_5_MODEL")
    has_default = hasattr(sm_module, "_DEFAULT_MODEL")
    assert has_haiku or has_default, (
        "expected a module-level default-model constant named "
        "`_HAIKU_4_5_MODEL` or `_DEFAULT_MODEL` on sm.py; found neither"
    )


def test_default_model_constant_value(sm_module):
    """Whichever name was chosen, the constant equals the exact SDK
    identifier for Claude Haiku 4.5."""
    val = getattr(
        sm_module,
        "_HAIKU_4_5_MODEL",
        getattr(sm_module, "_DEFAULT_MODEL", None),
    )
    assert val == _EXPECTED_HAIKU_4_5_MODEL, (
        f"default-model constant must equal "
        f"{_EXPECTED_HAIKU_4_5_MODEL!r}; got {val!r}"
    )


def test_default_max_tokens_constant_exists(sm_module):
    """`_DEFAULT_MAX_TOKENS` is defined at module scope."""
    assert hasattr(sm_module, "_DEFAULT_MAX_TOKENS"), (
        "expected `_DEFAULT_MAX_TOKENS` constant on sm.py"
    )


def test_default_max_tokens_constant_value(sm_module):
    """`_DEFAULT_MAX_TOKENS == 4096` per Story 3 acceptance."""
    val = sm_module._DEFAULT_MAX_TOKENS
    assert val == _EXPECTED_DEFAULT_MAX_TOKENS, (
        f"_DEFAULT_MAX_TOKENS must equal "
        f"{_EXPECTED_DEFAULT_MAX_TOKENS}; got {val!r}"
    )
    assert isinstance(val, int) and not isinstance(val, bool), (
        f"_DEFAULT_MAX_TOKENS must be an int (not bool); got "
        f"{type(val).__name__}"
    )


# ===========================================================================
# Category Q — Single-source-of-truth grep (4 tests)
#
# No spawn site reads model/max_tokens env vars directly. A grep
# across sm.py finds the env var names ONLY inside the two resolver
# functions. Outside the resolvers, zero hits.
# ===========================================================================


def test_grep_audit_scoped_to_production_module_only():
    """Document scope: the single-source-of-truth audit looks at
    `sm.py`, NOT the tests tree. If a future refactor splits sm.py
    into a package, the audit's scope must be expanded deliberately."""
    assert SM_PATH.is_file(), (
        f"sm.py not found at {SM_PATH}; the grep audit can't run"
    )
    assert not (PACKAGE_DIR / "sm").is_dir(), (
        "sm-tool has grown an `sm/` package directory — the grep "
        "audit in this file only scans `sm.py`. Expand the scope of "
        "the grep tests to walk the package."
    )


def test_only_resolvers_read_per_spawn_model_env_vars():
    """A grep across sm.py for each per-spawn model env var name
    finds at most one read each (inside `resolve_model`). Any second
    hit is a spawn-site leak that breaks single-source-of-truth."""
    text = _read_sm_source()
    for env_name in _ROLE_MODEL_ENVS.values():
        pattern = re.compile(
            rf"os\.environ(?:\[\s*['\"]" + env_name + r"['\"]\s*\]"
            rf"|\.get\(\s*['\"]" + env_name + r"['\"])"
            rf"|getenv\(\s*['\"]" + env_name + r"['\"]"
        )
        hits = pattern.findall(text)
        assert len(hits) <= 1, (
            f"expected at most ONE read of env var {env_name!r} in "
            f"sm.py (inside `resolve_model`); found {len(hits)}: "
            f"{hits!r}"
        )


def test_only_resolvers_read_per_spawn_max_tokens_env_vars():
    """A grep across sm.py for each per-spawn max_tokens env var name
    finds at most one read each (inside `resolve_max_tokens`)."""
    text = _read_sm_source()
    for env_name in _ROLE_MAX_TOKENS_ENVS.values():
        pattern = re.compile(
            rf"os\.environ(?:\[\s*['\"]" + env_name + r"['\"]\s*\]"
            rf"|\.get\(\s*['\"]" + env_name + r"['\"])"
            rf"|getenv\(\s*['\"]" + env_name + r"['\"]"
        )
        hits = pattern.findall(text)
        assert len(hits) <= 1, (
            f"expected at most ONE read of env var {env_name!r} in "
            f"sm.py (inside `resolve_max_tokens`); found {len(hits)}: "
            f"{hits!r}"
        )


def test_only_resolvers_read_global_model_and_max_tokens_env_vars():
    """A grep across sm.py for `SM_MODEL` and `SM_MAX_TOKENS` global
    env-var reads finds at most one read each (inside the two
    resolvers). Any second hit is a leak."""
    text = _read_sm_source()
    for env_name in ("SM_MODEL", "SM_MAX_TOKENS"):
        pattern = re.compile(
            rf"os\.environ(?:\[\s*['\"]" + env_name + r"['\"]\s*\]"
            rf"|\.get\(\s*['\"]" + env_name + r"['\"])"
            rf"|getenv\(\s*['\"]" + env_name + r"['\"]"
        )
        hits = pattern.findall(text)
        assert len(hits) <= 1, (
            f"expected at most ONE read of env var {env_name!r} in "
            f"sm.py (inside the resolver); found {len(hits)}: {hits!r}"
        )
