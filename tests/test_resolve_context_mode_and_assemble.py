"""Iter 3 v2 Sprint 1 Story 1 — SM_CONTEXT_MODE env var + context bundling.

This file pins the contract of two new public helpers on `sm.py`:

  1. `resolve_context_mode() -> str`
       Reads the `SM_CONTEXT_MODE` env var. Returns one of `"full"`,
       `"minimal"`, or `"custom"`. Defaults to `"full"` when the env var
       is unset / empty / whitespace-only. Raises `sm.ConfigError` (the
       existing Iter 2 Story 3 typed error) on any other value
       (including case-variant strings like `"FULL"`, `"Full"`).

  2. `assemble_spawn_context(sm_path, test_files, schemas) -> dict`
       Builds the context dict that Story 2 (next sprint) will splice
       into the spawn user message. All three args optional (default
       `None`). Returns:
         - empty dict `{}` when all three args are `None`
         - `{"sm_content": <full text>, ...}` when `sm_path` points to
           a readable file
         - `{"test_snippets": [{"path": str, "content": str}, ...], ...}`
           when `test_files` is a non-empty list
         - `{"schemas": <verbatim dict>, ...}` when `schemas` is
           provided
       `sm_path` pointing to a missing file raises `FileNotFoundError`.

CONTRACT INTERPRETATION (locked by TestWriter — Story 1 only builds
the helpers; Story 2 of the next sprint wires them into spawn defaults):

  - Both helpers public (no leading underscore) and listed in
    `sm.__all__`.
  - `resolve_context_mode()` defaults to `"full"` (Req 1 default mode).
  - Strict case-sensitive comparison: only lowercase `"full"`,
    `"minimal"`, `"custom"` accepted. `"FULL"` / `"Full"` /
    `"partial"` / etc. → ConfigError.
  - Empty string and whitespace-only env-var values default to
    `"full"` (operator typo for "unset").
  - `ConfigError` is the existing typed error from Iter 2 Story 3
    (`ValueError` subclass).
  - `ConfigError` message names `SM_CONTEXT_MODE` AND mentions the
    accepted values so the operator can correct.
  - `assemble_spawn_context` returns ONLY the keys the caller asked
    for: a `None` argument means the corresponding key is ABSENT
    (not present-with-None).
  - For `test_files=[]` (empty list, explicitly provided), Story 1
    TestWriter pins: `test_snippets` is ABSENT from the result. Empty
    list is "no test files to bundle" — semantically equivalent to
    `None` for output shape. (Decision: empty input → empty output =
    no key. Story 2 may revisit if integration surfaces a need.)
  - For `schemas={}` (empty dict), Story 1 TestWriter pins: `schemas`
    key IS present with the empty-dict value. Reason: an explicit dict
    is a deliberate choice ("here are my schemas — there are none");
    the caller can distinguish None (untouched) from {} (intentionally
    empty). Different from `test_files=[]` because the dict ergonomics
    fit a future "merge into context" pattern where empty dicts no-op
    cleanly.
  - `sm_summary` (the alternative key per the acceptance criteria) is
    NOT implemented in Story 1. Only `sm_content` is pinned. Story 1
    of a later sprint or an operator-supplied bundle path may provide
    `sm_summary`. Pinning `sm_summary` now would be testing
    speculative future shape.
  - Posture audit cascade: `SM_CONTEXT_MODE` must be added to
    `_ALLOWED_ENV_VAR_READS` in `tests/test_posture_audit.py`. The
    existing `test_only_sm_log_path_env_var_read` will FAIL until
    Coder updates the allowlist — flagged for Coder, not fixed here.

Every test below is expected to FAIL on first run —
`resolve_context_mode` and `assemble_spawn_context` do not exist yet.
The Coder implements them to drive this suite green.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import pathlib
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
def clean_context_env(monkeypatch):
    """Wipe `SM_CONTEXT_MODE` so the resolver sees only the in-test
    value. Restores prior state via monkeypatch."""
    monkeypatch.delenv("SM_CONTEXT_MODE", raising=False)
    return monkeypatch


def _read_sm_source() -> str:
    """Return sm.py as text. Used by the static posture-cascade check."""
    return SM_PATH.read_text(encoding="utf-8")


# ===========================================================================
# Category A — resolve_context_mode smoke (6 tests)
#
# Exists on the module, public, callable, in __all__, returns str,
# signature accepts no required arguments.
# ===========================================================================


def test_resolve_context_mode_exists_on_module(sm_module):
    """`sm.resolve_context_mode` is defined at module scope."""
    assert hasattr(sm_module, "resolve_context_mode"), (
        "expected `resolve_context_mode` to be defined on the sm "
        f"module; missing from dir(sm)="
        f"{sorted(n for n in dir(sm_module) if not n.startswith('_'))!r}"
    )


def test_resolve_context_mode_is_callable(sm_module):
    """`sm.resolve_context_mode` is callable."""
    obj = getattr(sm_module, "resolve_context_mode", None)
    assert callable(obj), (
        f"expected `sm.resolve_context_mode` to be callable; got "
        f"{type(obj).__name__}"
    )


def test_resolve_context_mode_is_public_name(sm_module):
    """The helper is named `resolve_context_mode` (no leading
    underscore). Public per the contract — operator-facing resolver
    matching the Iter 2 Story 3 pattern."""
    assert hasattr(sm_module, "resolve_context_mode"), (
        "expected the public name `resolve_context_mode`, not a "
        "private `_resolve_context_mode`"
    )


def test_resolve_context_mode_in_all(sm_module):
    """`resolve_context_mode` is listed in `sm.__all__`."""
    assert "resolve_context_mode" in sm_module.__all__, (
        f"`resolve_context_mode` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_resolve_context_mode_signature_no_required_args(sm_module):
    """`resolve_context_mode()` accepts no required positional
    arguments — it reads from `os.environ`, not from a parameter."""
    sig = inspect.signature(sm_module.resolve_context_mode)
    required = [
        p for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    assert len(required) == 0, (
        f"resolve_context_mode must take no required arguments; "
        f"signature {sig!s} requires {[p.name for p in required]!r}"
    )


def test_resolve_context_mode_returns_str(sm_module, clean_context_env):
    """Default-path return is a `str` (not bytes, not None)."""
    got = sm_module.resolve_context_mode()
    assert isinstance(got, str), (
        f"expected str return; got {type(got).__name__}"
    )


# ===========================================================================
# Category B — resolve_context_mode default + happy values (7 tests)
#
# Unset env var, empty, whitespace-only → "full". The three accepted
# values pass through verbatim.
# ===========================================================================


def test_resolve_context_mode_default_when_env_unset(
    sm_module, clean_context_env
):
    """No `SM_CONTEXT_MODE` set → returns `"full"` (the documented
    default per Requirements v2 Req 1)."""
    got = sm_module.resolve_context_mode()
    assert got == "full", (
        f"expected default 'full' when SM_CONTEXT_MODE unset; got "
        f"{got!r}"
    )


def test_resolve_context_mode_default_when_env_empty_string(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=""` → returns `"full"`. Empty string is an
    operator typo for "unset"; fall through to default, do NOT raise
    a parse error."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "")
    got = sm_module.resolve_context_mode()
    assert got == "full", (
        f"expected default 'full' when SM_CONTEXT_MODE empty; got "
        f"{got!r}"
    )


def test_resolve_context_mode_default_when_env_whitespace_only(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE="   "` → returns `"full"`. Whitespace-only is
    operator typo for "unset" (same pattern as `resolve_model`)."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "   ")
    got = sm_module.resolve_context_mode()
    assert got == "full", (
        f"expected default 'full' for whitespace-only env var; got "
        f"{got!r}"
    )


def test_resolve_context_mode_default_when_env_tabs_and_newlines(
    sm_module, clean_context_env
):
    """Whitespace-only includes tabs and newlines. `"\\t\\n"` →
    `"full"`."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "\t\n")
    got = sm_module.resolve_context_mode()
    assert got == "full", (
        f"expected 'full' for tabs/newlines-only env var; got {got!r}"
    )


def test_resolve_context_mode_full_passthrough(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=full` → returns `"full"`."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "full")
    got = sm_module.resolve_context_mode()
    assert got == "full", (
        f"expected 'full' passthrough; got {got!r}"
    )


def test_resolve_context_mode_minimal_passthrough(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=minimal` → returns `"minimal"`."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "minimal")
    got = sm_module.resolve_context_mode()
    assert got == "minimal", (
        f"expected 'minimal' passthrough; got {got!r}"
    )


def test_resolve_context_mode_custom_passthrough(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=custom` → returns `"custom"`."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "custom")
    got = sm_module.resolve_context_mode()
    assert got == "custom", (
        f"expected 'custom' passthrough; got {got!r}"
    )


# ===========================================================================
# Category C — resolve_context_mode strict validation (7 tests)
#
# Case-sensitive: only lowercase 'full' / 'minimal' / 'custom' accepted.
# Anything else → ConfigError naming SM_CONTEXT_MODE + the bad value
# AND the valid options.
# ===========================================================================


def test_resolve_context_mode_rejects_uppercase_full(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=FULL` → ConfigError. Strict case-sensitive
    comparison: uppercase is NOT silently coerced (forces operator to
    use the documented value)."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "FULL")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_context_mode()


def test_resolve_context_mode_rejects_titlecase_full(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=Full` → ConfigError. Title-case rejected for
    the same reason as uppercase."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "Full")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_context_mode()


def test_resolve_context_mode_rejects_uppercase_minimal(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=MINIMAL` → ConfigError. Strict case applies to
    every accepted value, not just 'full'."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "MINIMAL")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_context_mode()


def test_resolve_context_mode_rejects_unknown_value(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=partial` (a plausible-but-unsupported mode) →
    ConfigError."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "partial")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_context_mode()


def test_resolve_context_mode_rejects_garbage_value(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=xyz123` → ConfigError. Any non-accepted string
    raises."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "xyz123")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_context_mode()


def test_resolve_context_mode_rejects_substring_of_accepted_value(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE=ful` (prefix of "full") → ConfigError. Prefix
    matching is NOT allowed — strict equality only."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "ful")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_context_mode()


def test_resolve_context_mode_rejects_value_with_internal_whitespace(
    sm_module, clean_context_env
):
    """`SM_CONTEXT_MODE="full minimal"` → ConfigError. The resolver
    does NOT split-on-whitespace; the value is read as one token (with
    surrounding whitespace stripped for the unset check, but internal
    whitespace makes it invalid)."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "full minimal")
    with pytest.raises(sm_module.ConfigError):
        sm_module.resolve_context_mode()


# ===========================================================================
# Category D — resolve_context_mode typed error (4 tests)
#
# ConfigError is the existing Iter 2 Story 3 typed error. Subclasses
# ValueError. Message names the env var AND the valid set.
# ===========================================================================


def test_resolve_context_mode_raises_config_error_class(
    sm_module, clean_context_env
):
    """The raised exception is precisely `sm.ConfigError` (or a
    subclass) — not a bare ValueError. Catching `except ConfigError`
    must work."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "BAD")
    with pytest.raises(sm_module.ConfigError) as exc_info:
        sm_module.resolve_context_mode()
    assert isinstance(exc_info.value, sm_module.ConfigError), (
        f"expected ConfigError; got {type(exc_info.value).__name__}"
    )


def test_resolve_context_mode_config_error_subclasses_valueerror(
    sm_module, clean_context_env
):
    """`ConfigError` inherits from `ValueError` (already pinned in
    `test_resolve_model.py`; re-pin here so this story is self-
    contained). Existing `except ValueError` handlers keep working."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "BAD")
    with pytest.raises(ValueError):
        sm_module.resolve_context_mode()


def test_resolve_context_mode_error_message_names_env_var(
    sm_module, clean_context_env
):
    """The ConfigError message names `SM_CONTEXT_MODE` so the operator
    knows exactly which env var to fix."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "BAD")
    with pytest.raises(sm_module.ConfigError) as exc_info:
        sm_module.resolve_context_mode()
    msg = str(exc_info.value)
    assert "SM_CONTEXT_MODE" in msg, (
        f"expected env var name in ConfigError message; got {msg!r}"
    )


def test_resolve_context_mode_error_message_names_valid_values(
    sm_module, clean_context_env
):
    """The ConfigError message lists the valid values so the operator
    sees the accepted set. Pin all three lowercase values."""
    clean_context_env.setenv("SM_CONTEXT_MODE", "partial")
    with pytest.raises(sm_module.ConfigError) as exc_info:
        sm_module.resolve_context_mode()
    msg = str(exc_info.value)
    for valid in ("full", "minimal", "custom"):
        assert valid in msg, (
            f"expected valid value {valid!r} in ConfigError message; "
            f"got {msg!r}"
        )


# ===========================================================================
# Category E — assemble_spawn_context smoke (6 tests)
#
# Exists, callable, public, in __all__, signature accepts three
# optional args, all-None returns empty dict.
# ===========================================================================


def test_assemble_spawn_context_exists_on_module(sm_module):
    """`sm.assemble_spawn_context` is defined at module scope."""
    assert hasattr(sm_module, "assemble_spawn_context"), (
        "expected `assemble_spawn_context` on the sm module; missing "
        f"from dir(sm)="
        f"{sorted(n for n in dir(sm_module) if not n.startswith('_'))!r}"
    )


def test_assemble_spawn_context_is_callable(sm_module):
    """`sm.assemble_spawn_context` is callable."""
    obj = getattr(sm_module, "assemble_spawn_context", None)
    assert callable(obj), (
        f"expected `sm.assemble_spawn_context` to be callable; got "
        f"{type(obj).__name__}"
    )


def test_assemble_spawn_context_in_all(sm_module):
    """`assemble_spawn_context` is listed in `sm.__all__`."""
    assert "assemble_spawn_context" in sm_module.__all__, (
        f"`assemble_spawn_context` missing from sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_assemble_spawn_context_signature_three_optional_args(sm_module):
    """`assemble_spawn_context(sm_path=None, test_files=None,
    schemas=None)` — three optional positional-or-keyword
    parameters."""
    sig = inspect.signature(sm_module.assemble_spawn_context)
    params = list(sig.parameters.values())
    # All-None call must succeed (covered separately); here pin the
    # signature shape so the call-site contract is observable.
    names = [p.name for p in params]
    assert "sm_path" in names, (
        f"expected parameter `sm_path`; got {names!r}"
    )
    assert "test_files" in names, (
        f"expected parameter `test_files`; got {names!r}"
    )
    assert "schemas" in names, (
        f"expected parameter `schemas`; got {names!r}"
    )
    # All three must have defaults (so the all-None call succeeds).
    for name in ("sm_path", "test_files", "schemas"):
        param = sig.parameters[name]
        assert param.default is None, (
            f"expected parameter {name!r} to default to None; got "
            f"{param.default!r}"
        )


def test_assemble_spawn_context_all_none_returns_empty_dict(sm_module):
    """Calling `assemble_spawn_context()` with no args (or all-None
    explicitly) returns an empty dict. No keys present when no inputs
    provided."""
    got = sm_module.assemble_spawn_context()
    assert got == {}, (
        f"expected empty dict for no-args call; got {got!r}"
    )
    # Belt-and-braces: explicit None-None-None.
    got2 = sm_module.assemble_spawn_context(None, None, None)
    assert got2 == {}, (
        f"expected empty dict for all-None call; got {got2!r}"
    )


def test_assemble_spawn_context_returns_dict_not_none(sm_module):
    """Return type is `dict`, never `None`. Callers will splice into a
    message dict; a None return would be a downstream type error."""
    got = sm_module.assemble_spawn_context()
    assert isinstance(got, dict), (
        f"expected dict return; got {type(got).__name__} with value "
        f"{got!r}"
    )
    assert got is not None


# ===========================================================================
# Category F — assemble_spawn_context sm_path branch (7 tests)
#
# Valid path returns 'sm_content' key with full file text. Missing file
# raises FileNotFoundError. sm_path=None → key absent.
# ===========================================================================


def test_assemble_spawn_context_sm_path_returns_sm_content_key(
    sm_module, tmp_path
):
    """A valid `sm_path` adds a `sm_content` key to the returned dict."""
    fake_sm = tmp_path / "fake_sm.py"
    fake_sm.write_text("print('hello from fake sm')\n", encoding="utf-8")
    got = sm_module.assemble_spawn_context(sm_path=str(fake_sm))
    assert "sm_content" in got, (
        f"expected 'sm_content' key when sm_path provided; got keys="
        f"{sorted(got.keys())!r}"
    )


def test_assemble_spawn_context_sm_content_value_is_str(
    sm_module, tmp_path
):
    """`sm_content` value is a `str`, not bytes / not None."""
    fake_sm = tmp_path / "fake_sm.py"
    fake_sm.write_text("x = 1\n", encoding="utf-8")
    got = sm_module.assemble_spawn_context(sm_path=str(fake_sm))
    assert isinstance(got["sm_content"], str), (
        f"expected str sm_content; got {type(got['sm_content']).__name__}"
    )


def test_assemble_spawn_context_sm_content_matches_file_text(
    sm_module, tmp_path
):
    """`sm_content` equals the verbatim file text — no normalization."""
    fake_sm = tmp_path / "fake_sm.py"
    content = "def foo():\n    return 42\n# trailing comment\n"
    fake_sm.write_text(content, encoding="utf-8")
    got = sm_module.assemble_spawn_context(sm_path=str(fake_sm))
    assert got["sm_content"] == content, (
        f"expected sm_content to equal file text verbatim; got "
        f"{got['sm_content']!r}"
    )


def test_assemble_spawn_context_sm_content_preserves_bytes_via_hash(
    sm_module, tmp_path
):
    """Stronger invariant: the returned text hashed equals the file
    read hashed. Belt-and-braces against accidental newline
    munging."""
    fake_sm = tmp_path / "fake_sm.py"
    content = "alpha\nbeta\r\ngamma\n"
    fake_sm.write_text(content, encoding="utf-8")
    got = sm_module.assemble_spawn_context(sm_path=str(fake_sm))
    direct = fake_sm.read_text(encoding="utf-8")
    h_got = hashlib.sha256(got["sm_content"].encode("utf-8")).hexdigest()
    h_direct = hashlib.sha256(direct.encode("utf-8")).hexdigest()
    assert h_got == h_direct, (
        f"sm_content hash mismatch: got {h_got!r}, direct read "
        f"{h_direct!r} — text was normalized"
    )


def test_assemble_spawn_context_missing_sm_path_raises(
    sm_module, tmp_path
):
    """`sm_path` pointing to a non-existent file raises
    `FileNotFoundError`."""
    nope = tmp_path / "does_not_exist.py"
    assert not nope.exists()
    with pytest.raises(FileNotFoundError):
        sm_module.assemble_spawn_context(sm_path=str(nope))


def test_assemble_spawn_context_sm_path_none_absent_key(sm_module):
    """`sm_path=None` → no `sm_content` key in the returned dict.
    None means "not provided", not "provided as None"."""
    got = sm_module.assemble_spawn_context(sm_path=None)
    assert "sm_content" not in got, (
        f"expected no 'sm_content' key when sm_path=None; got keys="
        f"{sorted(got.keys())!r}"
    )


def test_assemble_spawn_context_real_sm_py_loads(sm_module):
    """Smoke: the real `sm.py` is readable via `assemble_spawn_context`.
    Pins that the helper handles real-file sizes (3k+ lines) without
    blowing up."""
    got = sm_module.assemble_spawn_context(sm_path=str(SM_PATH))
    assert "sm_content" in got
    assert isinstance(got["sm_content"], str)
    assert len(got["sm_content"]) > 1000, (
        f"expected real sm.py to be non-trivial; got len="
        f"{len(got['sm_content'])}"
    )


# ===========================================================================
# Category G — assemble_spawn_context test_files branch (7 tests)
#
# Non-empty list → 'test_snippets' key with list of {path, content}
# dicts. None / empty list → key absent. Order preserved.
# ===========================================================================


def test_assemble_spawn_context_test_files_returns_test_snippets_key(
    sm_module, tmp_path
):
    """A non-empty `test_files` list adds a `test_snippets` key."""
    f1 = tmp_path / "test_a.py"
    f1.write_text("def test_a(): assert True\n", encoding="utf-8")
    got = sm_module.assemble_spawn_context(test_files=[str(f1)])
    assert "test_snippets" in got, (
        f"expected 'test_snippets' key when test_files non-empty; got "
        f"keys={sorted(got.keys())!r}"
    )


def test_assemble_spawn_context_test_snippets_is_list(sm_module, tmp_path):
    """`test_snippets` is a `list` (not dict, not tuple — list so the
    spawn message can JSON-serialize in deterministic order)."""
    f1 = tmp_path / "test_a.py"
    f1.write_text("x = 1\n", encoding="utf-8")
    got = sm_module.assemble_spawn_context(test_files=[str(f1)])
    assert isinstance(got["test_snippets"], list), (
        f"expected list test_snippets; got "
        f"{type(got['test_snippets']).__name__}"
    )


def test_assemble_spawn_context_test_snippets_dict_shape(
    sm_module, tmp_path
):
    """Each entry in `test_snippets` is a dict with `path` (str) and
    `content` (str) keys."""
    f1 = tmp_path / "test_a.py"
    body = "def test_one():\n    pass\n"
    f1.write_text(body, encoding="utf-8")
    got = sm_module.assemble_spawn_context(test_files=[str(f1)])
    snip = got["test_snippets"][0]
    assert isinstance(snip, dict), (
        f"expected dict entry; got {type(snip).__name__}"
    )
    assert "path" in snip, f"missing 'path' key; got {sorted(snip)!r}"
    assert "content" in snip, (
        f"missing 'content' key; got {sorted(snip)!r}"
    )
    assert isinstance(snip["path"], str), (
        f"expected str path; got {type(snip['path']).__name__}"
    )
    assert isinstance(snip["content"], str), (
        f"expected str content; got {type(snip['content']).__name__}"
    )
    assert snip["content"] == body, (
        f"expected verbatim content; got {snip['content']!r}"
    )


def test_assemble_spawn_context_test_snippets_order_preserved(
    sm_module, tmp_path
):
    """The order of `test_files` is preserved in `test_snippets`."""
    paths = []
    for i, name in enumerate(("test_zzz.py", "test_aaa.py", "test_mmm.py")):
        p = tmp_path / name
        p.write_text(f"# file {i}\n", encoding="utf-8")
        paths.append(str(p))
    got = sm_module.assemble_spawn_context(test_files=paths)
    snippet_paths = [s["path"] for s in got["test_snippets"]]
    assert snippet_paths == paths, (
        f"expected order-preserving snippets; got {snippet_paths!r}, "
        f"expected {paths!r}"
    )


def test_assemble_spawn_context_test_files_none_absent_key(sm_module):
    """`test_files=None` → no `test_snippets` key in the returned
    dict."""
    got = sm_module.assemble_spawn_context(test_files=None)
    assert "test_snippets" not in got, (
        f"expected no 'test_snippets' key when test_files=None; got "
        f"keys={sorted(got.keys())!r}"
    )


def test_assemble_spawn_context_test_files_empty_list_absent_key(
    sm_module,
):
    """`test_files=[]` (empty list, explicitly provided) → no
    `test_snippets` key. Empty input = empty output = no key.
    (TestWriter decision: empty list semantically equivalent to None
    for output shape.)"""
    got = sm_module.assemble_spawn_context(test_files=[])
    assert "test_snippets" not in got, (
        f"expected no 'test_snippets' key for empty test_files list; "
        f"got keys={sorted(got.keys())!r}"
    )


def test_assemble_spawn_context_multiple_test_files_all_included(
    sm_module, tmp_path
):
    """Multiple test files → all appear in `test_snippets` (no
    truncation, no dedupe)."""
    paths = []
    for i in range(5):
        p = tmp_path / f"test_{i}.py"
        p.write_text(f"# test number {i}\n", encoding="utf-8")
        paths.append(str(p))
    got = sm_module.assemble_spawn_context(test_files=paths)
    assert len(got["test_snippets"]) == 5, (
        f"expected 5 snippets; got {len(got['test_snippets'])}"
    )


# ===========================================================================
# Category H — assemble_spawn_context schemas branch (4 tests)
#
# Schemas dict passed through verbatim. None → key absent. Empty dict
# → key present with empty-dict value (TestWriter decision).
# ===========================================================================


def test_assemble_spawn_context_schemas_returns_schemas_key(sm_module):
    """A non-empty `schemas` dict adds a `schemas` key."""
    schemas = {
        "ingest": {"type": "object"},
        "decompose": {"type": "object"},
    }
    got = sm_module.assemble_spawn_context(schemas=schemas)
    assert "schemas" in got, (
        f"expected 'schemas' key when schemas dict provided; got "
        f"keys={sorted(got.keys())!r}"
    )


def test_assemble_spawn_context_schemas_value_verbatim(sm_module):
    """`schemas` value equals the input dict — no transformation."""
    schemas = {
        "ingest": {"type": "object", "required": ["iteration_id"]},
        "decompose": {"type": "object"},
        "story_transition": {"verb": "string"},
    }
    got = sm_module.assemble_spawn_context(schemas=schemas)
    assert got["schemas"] == schemas, (
        f"expected schemas verbatim; got {got['schemas']!r}, expected "
        f"{schemas!r}"
    )


def test_assemble_spawn_context_schemas_none_absent_key(sm_module):
    """`schemas=None` → no `schemas` key in the returned dict."""
    got = sm_module.assemble_spawn_context(schemas=None)
    assert "schemas" not in got, (
        f"expected no 'schemas' key when schemas=None; got keys="
        f"{sorted(got.keys())!r}"
    )


def test_assemble_spawn_context_schemas_empty_dict_present(sm_module):
    """`schemas={}` (explicit empty dict) → `schemas` key IS present
    with the empty-dict value. Caller may distinguish None
    ("untouched") from {} ("intentionally empty"). TestWriter
    decision: dict ergonomics fit a future "merge into context"
    pattern; empty dict no-ops cleanly."""
    got = sm_module.assemble_spawn_context(schemas={})
    assert "schemas" in got, (
        f"expected 'schemas' key present for explicit empty dict; got "
        f"keys={sorted(got.keys())!r}"
    )
    assert got["schemas"] == {}, (
        f"expected empty dict value; got {got['schemas']!r}"
    )


# ===========================================================================
# Category I — assemble_spawn_context combined (3 tests)
#
# All three args provided → all three keys present. Two of three →
# exactly those two keys.
# ===========================================================================


def test_assemble_spawn_context_all_three_keys_present(
    sm_module, tmp_path
):
    """All three args provided → returned dict has all three keys
    (`sm_content`, `test_snippets`, `schemas`)."""
    fake_sm = tmp_path / "sm.py"
    fake_sm.write_text("# sm\n", encoding="utf-8")
    fake_test = tmp_path / "test_x.py"
    fake_test.write_text("# test\n", encoding="utf-8")
    got = sm_module.assemble_spawn_context(
        sm_path=str(fake_sm),
        test_files=[str(fake_test)],
        schemas={"a": 1},
    )
    for key in ("sm_content", "test_snippets", "schemas"):
        assert key in got, (
            f"expected '{key}' key; got keys={sorted(got.keys())!r}"
        )


def test_assemble_spawn_context_two_of_three(sm_module, tmp_path):
    """sm_path + schemas provided, test_files=None → only sm_content +
    schemas keys present."""
    fake_sm = tmp_path / "sm.py"
    fake_sm.write_text("# sm\n", encoding="utf-8")
    got = sm_module.assemble_spawn_context(
        sm_path=str(fake_sm),
        test_files=None,
        schemas={"a": 1},
    )
    assert "sm_content" in got
    assert "schemas" in got
    assert "test_snippets" not in got, (
        f"unexpected 'test_snippets' key when test_files=None; got "
        f"keys={sorted(got.keys())!r}"
    )


def test_assemble_spawn_context_returns_independent_dict(
    sm_module, tmp_path
):
    """The returned dict is a fresh dict; mutating it does NOT leak
    back into the resolver. (Caller is free to add the context block
    to a larger message dict.)"""
    schemas = {"a": 1}
    got = sm_module.assemble_spawn_context(schemas=schemas)
    got["extra"] = "added by caller"
    # Re-call: extra key must not persist.
    again = sm_module.assemble_spawn_context(schemas=schemas)
    assert "extra" not in again, (
        f"resolver leaked caller mutation; got keys={sorted(again)!r}"
    )


# ===========================================================================
# Category J — posture audit cascade (2 tests)
#
# `SM_CONTEXT_MODE` must be added to the posture audit's
# `_ALLOWED_ENV_VAR_READS` set. These two tests pin the cascade so
# Coder knows what to update. The existing
# `test_only_sm_log_path_env_var_read` will FAIL until Coder updates
# the allowlist — flagged for Coder; this story does NOT modify
# existing tests.
# ===========================================================================


def test_posture_audit_allowlist_includes_sm_context_mode():
    """Pin the cascade target: when Coder lands Story 1, the posture
    audit allowlist in `tests/test_posture_audit.py` must include
    `SM_CONTEXT_MODE`. This test reads the posture-audit source as
    text and verifies the name appears in `_ALLOWED_ENV_VAR_READS`.

    This test FAILS until Coder updates the allowlist. The existing
    `test_only_sm_log_path_env_var_read` in
    `tests/test_posture_audit.py` will ALSO fail until the allowlist
    is updated — that is the expected cascade flagged for Coder."""
    posture_path = PACKAGE_DIR / "tests" / "test_posture_audit.py"
    assert posture_path.is_file(), (
        f"posture audit test file not found at {posture_path}"
    )
    text = posture_path.read_text(encoding="utf-8")
    # Look for the allowlist set literal and the entry inside it.
    assert "_ALLOWED_ENV_VAR_READS" in text, (
        "posture audit's _ALLOWED_ENV_VAR_READS set has been renamed; "
        "this cascade test must be updated"
    )
    assert '"SM_CONTEXT_MODE"' in text or "'SM_CONTEXT_MODE'" in text, (
        "expected `SM_CONTEXT_MODE` to be added to "
        "`_ALLOWED_ENV_VAR_READS` in tests/test_posture_audit.py "
        "(cascade from Iter 3 v2 Sprint 1 Story 1); not found in "
        "posture audit source"
    )


def test_sm_module_reads_sm_context_mode_env_var():
    """Pin that sm.py reads `SM_CONTEXT_MODE` from the environment —
    the read happens inside `resolve_context_mode`. A grep of sm.py
    finds the literal env var name at least once.

    This is the inverse of the posture-audit allowlist check: the
    allowlist exists to permit reads that sm.py actually performs.
    Both must move in lockstep."""
    text = _read_sm_source()
    assert "SM_CONTEXT_MODE" in text, (
        "expected `SM_CONTEXT_MODE` to appear in sm.py (read by "
        "`resolve_context_mode`); not found"
    )
