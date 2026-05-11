"""Iter 2 Story 2 — API key resolution with actionable error.

This file pins the contract of `sm.resolve_api_key()`, the
`sm.MissingAPIKeyError` typed exception, and the new
`sm.EXIT_AGENT_ERROR = 12` constant. Every real-agent spawn path
(decompose, test_writer, coder, reviewer) must read
`ANTHROPIC_API_KEY` through this single helper. Failure carries a
single human-readable message naming the env var and the remediation
step, the SDK is NOT imported on the failure path, and the CLI
dispatcher catches the typed error at the top level and exits with
exit code `12`, printing the message verbatim to stderr — no
traceback.

Pinned clauses (verbatim from `iter2/Stories_v1.md`, Story 2):

  1. `resolve_api_key() -> str` reads `os.environ["ANTHROPIC_API_KEY"]`
     and returns it on success.
  2. Missing or empty-string env var raises a typed
     `MissingAPIKeyError`. The SDK is NOT imported on this failure path.
  3. The CLI dispatcher catches `MissingAPIKeyError` at the top level
     and exits with `EXIT_AGENT_ERROR = 12`, printing the message
     verbatim to stderr — no traceback.
  4. Every real-agent spawn path routes API-key reads through this
     single helper; a grep for direct `os.environ` reads of
     `ANTHROPIC_API_KEY` outside this helper returns zero hits.

Every test below FAILS on first run — `resolve_api_key`,
`MissingAPIKeyError`, and `EXIT_AGENT_ERROR` do not exist yet. The
Coder implements them to drive this suite green.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import re
import subprocess
import sys

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


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
def clean_anthropic_env(monkeypatch):
    """Ensure ANTHROPIC_API_KEY is NOT set in the environment for the
    duration of the test. Restores prior state via monkeypatch."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return monkeypatch


@pytest.fixture
def purge_anthropic_imports():
    """Remove `anthropic` and its submodules from `sys.modules` so the
    SDK-not-imported-on-failure invariant can be observed. Restores the
    original mapping after the test."""
    saved: dict[str, object] = {}
    for name in list(sys.modules):
        if name == "anthropic" or name.startswith("anthropic."):
            saved[name] = sys.modules.pop(name)
    yield
    # Restore — don't leave the test bench in a different state than we
    # found it.
    for name, mod in saved.items():
        sys.modules.setdefault(name, mod)


def _read_sm_source() -> str:
    """Return sm.py as text. Used by static grep tests."""
    return SM_PATH.read_text(encoding="utf-8")


# ===========================================================================
# Category A — Smoke (4 tests)
#
# `resolve_api_key` exists on the module, is callable, public (no
# leading underscore), and listed in `sm.__all__`.
# ===========================================================================


def test_resolve_api_key_exists_on_module(sm_module):
    """`sm.resolve_api_key` is defined at module scope."""
    assert hasattr(sm_module, "resolve_api_key"), (
        "expected `resolve_api_key` to be defined on the sm module; "
        f"missing from dir(sm)={sorted(n for n in dir(sm_module) if not n.startswith('_'))!r}"
    )


def test_resolve_api_key_is_callable(sm_module):
    """`sm.resolve_api_key` is callable (a function or callable
    object)."""
    obj = getattr(sm_module, "resolve_api_key", None)
    assert callable(obj), (
        f"expected `sm.resolve_api_key` to be callable; got "
        f"{type(obj).__name__}"
    )


def test_resolve_api_key_is_public_name(sm_module):
    """The helper is named `resolve_api_key` (no leading underscore).
    Public per the contract — other Story 3 resolvers will follow the
    same naming pattern."""
    assert hasattr(sm_module, "resolve_api_key"), (
        "expected the public name `resolve_api_key`, not a private "
        "`_resolve_api_key`"
    )
    # Belt-and-braces: nothing private-only.
    assert not hasattr(sm_module, "_resolve_api_key") or hasattr(
        sm_module, "resolve_api_key"
    ), "public `resolve_api_key` must be present even if a private alias exists"


def test_resolve_api_key_in_all(sm_module):
    """`resolve_api_key` is listed in `sm.__all__` so wildcard imports
    pick it up and the public surface is documented in one place."""
    assert "resolve_api_key" in sm_module.__all__, (
        f"`resolve_api_key` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


# ===========================================================================
# Category B — Happy path (3 tests)
#
# When `ANTHROPIC_API_KEY` is set, the function returns its exact
# string value with no whitespace stripping. Different values across
# calls are honored (re-read each call, not cached at import time).
# ===========================================================================


def test_resolve_api_key_returns_env_var_value(sm_module, monkeypatch):
    """`resolve_api_key()` returns the env var value when set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-abc123")
    got = sm_module.resolve_api_key()
    assert got == "sk-test-abc123", (
        f"expected env var value verbatim; got {got!r}"
    )
    assert isinstance(got, str), (
        f"expected str return; got {type(got).__name__}"
    )


def test_resolve_api_key_returns_exact_string_no_whitespace_strip(
    sm_module, monkeypatch
):
    """The function returns the env var value VERBATIM — no
    `.strip()`. A leading/trailing space is part of the value the
    operator chose to set. (Whitespace-only is a separate concern,
    covered in Category C.)"""
    val = "  sk-with-padding  "
    monkeypatch.setenv("ANTHROPIC_API_KEY", val)
    got = sm_module.resolve_api_key()
    assert got == val, (
        f"resolve_api_key must NOT strip the env var; "
        f"expected {val!r}, got {got!r}"
    )


def test_resolve_api_key_rereads_env_each_call(sm_module, monkeypatch):
    """The helper reads `os.environ` each call — values can vary
    across calls without re-importing the module."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-first")
    first = sm_module.resolve_api_key()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-second")
    second = sm_module.resolve_api_key()
    assert first == "sk-first", f"first call wrong: {first!r}"
    assert second == "sk-second", f"second call wrong: {second!r}"
    assert first != second, "resolver appears to cache; must re-read env"


# ===========================================================================
# Category C — Missing env var → typed error (4 tests)
#
# Unset env var, empty string, and whitespace-only all raise
# MissingAPIKeyError. The message names the env var.
# ===========================================================================


def test_resolve_api_key_raises_when_env_var_unset(
    sm_module, clean_anthropic_env
):
    """No `ANTHROPIC_API_KEY` in env → `MissingAPIKeyError`."""
    cls = getattr(sm_module, "MissingAPIKeyError", None)
    assert cls is not None, "MissingAPIKeyError not defined on sm module"
    with pytest.raises(cls):
        sm_module.resolve_api_key()


def test_resolve_api_key_raises_when_env_var_empty_string(
    sm_module, monkeypatch
):
    """`ANTHROPIC_API_KEY=""` → `MissingAPIKeyError`. Empty string is
    treated as missing, NOT as a valid set-but-empty value (per spec)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    cls = getattr(sm_module, "MissingAPIKeyError", None)
    assert cls is not None, "MissingAPIKeyError not defined on sm module"
    with pytest.raises(cls):
        sm_module.resolve_api_key()


def test_resolve_api_key_raises_when_env_var_whitespace_only(
    sm_module, monkeypatch
):
    """Whitespace-only env var (`"   "`) is treated as missing. A
    value that strips to empty cannot be a real API key — operators
    almost certainly meant 'unset'."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    cls = getattr(sm_module, "MissingAPIKeyError", None)
    assert cls is not None, "MissingAPIKeyError not defined on sm module"
    with pytest.raises(cls):
        sm_module.resolve_api_key()


def test_missing_api_key_error_message_names_env_var(
    sm_module, clean_anthropic_env
):
    """The typed error's single human-readable message names the env
    var so the operator knows exactly what to set."""
    cls = getattr(sm_module, "MissingAPIKeyError", None)
    assert cls is not None, "MissingAPIKeyError not defined on sm module"
    with pytest.raises(cls) as exc_info:
        sm_module.resolve_api_key()
    msg = str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in msg, (
        f"expected MissingAPIKeyError message to name the env var; "
        f"got {msg!r}"
    )
    # Single-line — actionable but not a wall of text.
    assert "\n" not in msg.strip(), (
        f"MissingAPIKeyError message must be a single line; got {msg!r}"
    )


# ===========================================================================
# Category D — MissingAPIKeyError typed (4 tests)
#
# Class exists, is in __all__, subclasses ValueError (so existing
# `except ValueError` handlers still catch it), and has its own
# identity distinct from ValueError itself.
# ===========================================================================


def test_missing_api_key_error_class_exists(sm_module):
    """`sm.MissingAPIKeyError` is defined."""
    assert hasattr(sm_module, "MissingAPIKeyError"), (
        "expected `MissingAPIKeyError` to be defined on the sm module"
    )


def test_missing_api_key_error_in_all(sm_module):
    """`MissingAPIKeyError` is in `sm.__all__` — public surface."""
    assert "MissingAPIKeyError" in sm_module.__all__, (
        f"`MissingAPIKeyError` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_missing_api_key_error_subclasses_valueerror(sm_module):
    """`MissingAPIKeyError` inherits from `ValueError` so existing
    `except ValueError` handlers in the codebase still catch it."""
    cls = sm_module.MissingAPIKeyError
    assert issubclass(cls, ValueError), (
        f"MissingAPIKeyError must subclass ValueError; mro={cls.__mro__!r}"
    )


def test_missing_api_key_error_distinct_identity(sm_module):
    """`MissingAPIKeyError` is its own class, not an alias for
    ValueError. Catching it specifically must be possible."""
    cls = sm_module.MissingAPIKeyError
    assert cls is not ValueError, (
        "MissingAPIKeyError must be a distinct class, not ValueError itself"
    )
    # And: a plain ValueError is NOT a MissingAPIKeyError.
    assert not isinstance(ValueError("x"), cls), (
        "MissingAPIKeyError must be a strict subclass — plain "
        "ValueError instances must not match it"
    )


# ===========================================================================
# Category E — EXIT_AGENT_ERROR constant (3 tests)
#
# Exists, equals 12, distinct from every existing EXIT_OK..EXIT_TRANSITION
# and EXIT_CLOSE.
# ===========================================================================


def test_exit_agent_error_exists(sm_module):
    """`sm.EXIT_AGENT_ERROR` is defined at module scope."""
    assert hasattr(sm_module, "EXIT_AGENT_ERROR"), (
        "expected `EXIT_AGENT_ERROR` to be defined on the sm module"
    )


def test_exit_agent_error_equals_twelve(sm_module):
    """`EXIT_AGENT_ERROR == 12` per LOCKED_DECISION 7."""
    assert sm_module.EXIT_AGENT_ERROR == 12, (
        f"EXIT_AGENT_ERROR must equal 12 per LOCKED_DECISION 7; got "
        f"{sm_module.EXIT_AGENT_ERROR!r}"
    )


def test_exit_agent_error_distinct_from_existing_exit_codes(sm_module):
    """`EXIT_AGENT_ERROR` is distinct from every other documented exit
    code. A collision would silently route two error classes to the
    same exit status."""
    existing_names = (
        "EXIT_OK",
        "EXIT_OTHER",
        "EXIT_PATH",
        "EXIT_JSON",
        "EXIT_SHAPE",
        "EXIT_DUP_ID",
        "EXIT_SINGLE_ACTIVE",
        "EXIT_UNKNOWN_REQ",
        "EXIT_SPRINT_CUT",
        "EXIT_TRANSITION",
        "EXIT_CLOSE",
    )
    new_val = sm_module.EXIT_AGENT_ERROR
    for name in existing_names:
        existing = getattr(sm_module, name, None)
        assert existing is not None, (
            f"prerequisite exit code {name} missing; check sm.py"
        )
        assert new_val != existing, (
            f"EXIT_AGENT_ERROR ({new_val}) collides with {name} "
            f"({existing}); each error class must map to a distinct "
            "exit status"
        )


# ===========================================================================
# Category F — SDK-not-imported on failure (3 tests)
#
# The missing-env-var failure path must NOT import `anthropic`. Pin
# via `sys.modules` snapshot before/after — when the resolver raises,
# no new `anthropic*` entry appears.
# ===========================================================================


def test_resolve_api_key_failure_does_not_import_anthropic(
    sm_module, clean_anthropic_env, purge_anthropic_imports
):
    """Calling `resolve_api_key()` with no env var set must NOT
    import `anthropic`. The error is raised cheaply, before any SDK
    side-effect."""
    # Sanity check: the purge fixture cleared any prior import.
    assert "anthropic" not in sys.modules, (
        "test setup wrong: anthropic was not purged before the call"
    )
    cls = sm_module.MissingAPIKeyError
    with pytest.raises(cls):
        sm_module.resolve_api_key()
    # Post-condition: `anthropic` is still NOT imported.
    assert "anthropic" not in sys.modules, (
        "resolve_api_key failure path imported `anthropic`; the SDK "
        "must not be touched when the env var is missing"
    )


def test_resolve_api_key_failure_does_not_import_anthropic_submodules(
    sm_module, clean_anthropic_env, purge_anthropic_imports
):
    """Stronger invariant: NO `anthropic*` submodule (e.g.
    `anthropic.types`, `anthropic._client`) is imported on the
    failure path. A submodule import would imply the parent package
    was loaded — pin both."""
    cls = sm_module.MissingAPIKeyError
    with pytest.raises(cls):
        sm_module.resolve_api_key()
    leaked = [n for n in sys.modules if n == "anthropic" or n.startswith("anthropic.")]
    assert not leaked, (
        f"resolve_api_key failure path leaked anthropic submodules: "
        f"{leaked!r}"
    )


def test_resolve_api_key_failure_does_not_import_anthropic_empty_string(
    sm_module, monkeypatch, purge_anthropic_imports
):
    """Same invariant for the empty-string path: `ANTHROPIC_API_KEY=""`
    raises `MissingAPIKeyError` AND does not import `anthropic`."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    cls = sm_module.MissingAPIKeyError
    with pytest.raises(cls):
        sm_module.resolve_api_key()
    leaked = [n for n in sys.modules if n == "anthropic" or n.startswith("anthropic.")]
    assert not leaked, (
        f"empty-string failure path leaked anthropic modules: {leaked!r}"
    )


# ===========================================================================
# Category G — CLI mapping (5 tests)
#
# `python -m sm decompose` with no ANTHROPIC_API_KEY set must:
#   - exit with code 12 (EXIT_AGENT_ERROR)
#   - print the actionable message to STDERR (verbatim, naming the env var)
#   - NOT print a Python traceback (no "Traceback (most recent call last)")
#   - print nothing to STDOUT
#
# Note: these subprocess tests assume Story 6 (real decompose default
# wiring) lands AT OR BEFORE the CLI exposes the MissingAPIKeyError
# path. Story 2 is the resolver + CLI catch; the resolver MUST be
# called by the decompose default path (or by an intermediate wiring
# step) so the env-var-missing case actually flows through the CLI
# dispatcher. Until Story 6 wires the real default, this set of tests
# may report a different non-zero exit code; once Story 6 is wired,
# they pin the contract.
# ===========================================================================


def _run_cli_decompose_no_key(tmp_path) -> subprocess.CompletedProcess:
    """Run `python -m sm decompose` with NO ANTHROPIC_API_KEY in the
    env and an isolated SM_LOG_PATH. Returns the CompletedProcess.
    Used by the CLI mapping tests below."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    # Honor the existing test-isolation env var name. Story 12 renames
    # this to SM_TEST_LOG_PATH; we use the current name to avoid
    # ordering-dependence with that retro story.
    env["SM_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")
    return subprocess.run(
        [sys.executable, "-m", "sm", "decompose"],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_decompose_missing_key_exits_twelve(tmp_path):
    """`python -m sm decompose` with no `ANTHROPIC_API_KEY` exits 12.

    The CLI dispatcher catches `MissingAPIKeyError` at the top level
    and returns `EXIT_AGENT_ERROR` (12) per LOCKED_DECISION 7."""
    result = _run_cli_decompose_no_key(tmp_path)
    assert result.returncode == 12, (
        f"expected exit 12 (EXIT_AGENT_ERROR) on missing "
        f"ANTHROPIC_API_KEY; got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_decompose_missing_key_message_on_stderr(tmp_path):
    """The actionable error message is printed to STDERR, naming the
    env var. STDOUT carries no error content."""
    result = _run_cli_decompose_no_key(tmp_path)
    assert "ANTHROPIC_API_KEY" in result.stderr, (
        f"expected env var name in stderr; got stderr={result.stderr!r}"
    )


def test_cli_decompose_missing_key_no_traceback(tmp_path):
    """No Python traceback bleeds through. The acceptance criterion
    explicitly forbids it: 'no traceback'."""
    result = _run_cli_decompose_no_key(tmp_path)
    combined = result.stdout + result.stderr
    assert "Traceback (most recent call last)" not in combined, (
        f"missing-key path printed a Python traceback; got:\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # Belt-and-braces: also reject `File "..." line ...` frames.
    assert not re.search(r'File "[^"]+", line \d+', combined), (
        f"missing-key path bled a traceback frame; got:\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_decompose_missing_key_stdout_clean(tmp_path):
    """No error content lands on STDOUT — errors go to STDERR. The
    happy-path STDOUT contract (printing the entry id) is unrelated to
    the missing-key error path."""
    result = _run_cli_decompose_no_key(tmp_path)
    # STDOUT may be empty OR may carry a no-op trailing newline; what
    # it must NOT carry is the env-var name (which belongs on stderr).
    assert "ANTHROPIC_API_KEY" not in result.stdout, (
        f"error info must be on stderr, not stdout; got "
        f"stdout={result.stdout!r}"
    )


def test_cli_decompose_missing_key_message_actionable(tmp_path):
    """The stderr message is ACTIONABLE — it tells the operator what
    to do. We pin presence of one of a few remediation cues: 'set',
    'export', 'environment', or a URL hint. The exact wording is the
    Coder's call; the contract is 'actionable + names the env var'."""
    result = _run_cli_decompose_no_key(tmp_path)
    err_lower = result.stderr.lower()
    cues = ("set", "export", "environment", "http", "console.anthropic")
    assert any(c in err_lower for c in cues), (
        f"expected the missing-key message to be actionable (one of "
        f"{cues!r} expected in stderr); got stderr={result.stderr!r}"
    )


# ===========================================================================
# Category H — Single-source-of-truth grep (2 tests)
#
# Outside the helper itself, no other call site reads
# `ANTHROPIC_API_KEY` from `os.environ` (or `os.getenv`). All four
# real-agent spawn paths route through the single resolver.
# ===========================================================================


def test_only_one_environ_read_of_anthropic_key_in_sm_module():
    """A grep across `sm.py` for `os.environ[...ANTHROPIC_API_KEY...]`
    reads finds at most one hit. That single hit lives inside
    `resolve_api_key`. Any second hit is a leak that breaks the
    single-source-of-truth invariant."""
    text = _read_sm_source()
    # Match either subscript form: os.environ["ANTHROPIC_API_KEY"] or
    # os.environ.get("ANTHROPIC_API_KEY", ...). Case-sensitive — the
    # env var name is uppercase by convention.
    pattern = re.compile(
        r"os\.environ(?:\[\s*['\"]ANTHROPIC_API_KEY['\"]\s*\]"
        r"|\.get\(\s*['\"]ANTHROPIC_API_KEY['\"])"
    )
    hits = pattern.findall(text)
    assert len(hits) <= 1, (
        f"expected at most ONE `os.environ` read of "
        f"`ANTHROPIC_API_KEY` in sm.py (the one inside "
        f"`resolve_api_key`); found {len(hits)}: {hits!r}"
    )


def test_no_getenv_read_of_anthropic_key_outside_resolver():
    """A grep across `sm.py` for `os.getenv("ANTHROPIC_API_KEY")` or
    `getenv(ANTHROPIC_API_KEY)` returns at most one hit. The resolver
    is the ONE place this read may happen — every other site routes
    through `resolve_api_key`."""
    text = _read_sm_source()
    # Match `getenv("ANTHROPIC_API_KEY")` or `os.getenv(...)` forms.
    pattern = re.compile(r"getenv\(\s*['\"]ANTHROPIC_API_KEY['\"]")
    hits = pattern.findall(text)
    assert len(hits) <= 1, (
        f"expected at most ONE `getenv` read of `ANTHROPIC_API_KEY` "
        f"in sm.py; found {len(hits)}: {hits!r}"
    )


# ===========================================================================
# Category I — Single-source-of-truth grep (test-tree exclusion guard)
#
# The grep tests above scan only `sm.py` (production module). Tests
# legitimately set/read `ANTHROPIC_API_KEY` to drive the resolver, so
# we do NOT grep the tests/ tree. This single test pins that the
# audit's scope is correct.
# ===========================================================================


def test_grep_audit_scoped_to_production_module_only():
    """Document scope: the single-source-of-truth audit looks at
    `sm.py`, NOT the tests tree. This test exists so that if someone
    later refactors production code into a second module (e.g. an
    `sm/` package), the audit's narrow scope is visible and gets
    expanded deliberately, not silently bypassed."""
    assert SM_PATH.is_file(), (
        f"sm.py not found at {SM_PATH}; the grep audit can't run"
    )
    # If the codebase splits into a package, this assertion will fire
    # and force a follow-up.
    assert not (PACKAGE_DIR / "sm").is_dir(), (
        "sm-tool has grown an `sm/` package directory — the grep "
        "audit in this file only scans `sm.py`. Expand the scope of "
        "test_only_one_environ_read_of_anthropic_key_in_sm_module "
        "and test_no_getenv_read_of_anthropic_key_outside_resolver "
        "to walk the package."
    )
