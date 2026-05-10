"""Story 8 — pin the contract of `sm.resolve_role_spec` and the
private `sm._role_spec_hash`, plus the role-spec file fixtures and the
"no inline prompt assembly" grep invariant on `sm.py`.

What this file pins:

  - Function signature and shape:
      `resolve_role_spec(role: str) -> Path`
    PUBLIC, callable, in `sm.__all__`, importable as
    `from sm import resolve_role_spec`. Returns an absolute `pathlib.Path`
    to a checked-in role-spec file in `<package_dir>/roles/<role>.md`.

  - Canonical roles (the only valid `role` strings):
        "sm_agent", "test_writer", "coder", "reviewer"
    Every other string raises `ValueError` naming the offender.

  - Path resolution anchor: the package directory, expressed as
    `LOG_PATH.parent`. The resolver uses this anchor so that monkeypatching
    `sm.LOG_PATH` redirects role-spec lookup the same way it redirects log
    lookup (consistent with the rest of the suite).

  - Missing-file behavior: if the resolved path does not exist on disk, the
    resolver raises `RoleSpecNotFoundError` — a structured error class
    exported on `sm` and a subclass of `FileNotFoundError` (so existing
    `except FileNotFoundError` callers keep working).

  - Empty/whitespace/non-string `role`:
        non-string  → `TypeError`
        empty/blank → `ValueError`

  - Content hash helper: a private `sm._role_spec_hash(role: str) -> str`
    returning a deterministic hex digest of the role-spec file content.
    Same role → same hash. Different roles → different hashes. Editing the
    underlying file → hash changes. Must NOT be in `sm.__all__`.

  - Grep invariant: `sm.py` source contains no large triple-quoted string
    literals (>200 chars) carrying the role-spec markers ("ROLE:", "LANE:",
    "ANTI-LANE:") inline. This pins "agent prompts live in role-spec
    files, not concatenated in code."

  - Role-spec file content sanity: each of the four checked-in role-spec
    files contains the role-spec format markers — at minimum "ROLE:",
    "LANE:", "ANTI-LANE:", and "OUTPUT FORMAT:".

Tests must FAIL on first run — `resolve_role_spec`, `_role_spec_hash`,
`RoleSpecNotFoundError`, and the four `roles/*.md` files do not exist yet.
The Coder downstream creates the resolver, the hash helper, the error
class, and the role-spec files to satisfy these tests.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


CANONICAL_ROLES = ("sm_agent", "test_writer", "coder", "reviewer")
ROLE_SPEC_MARKERS = ("ROLE:", "LANE:", "ANTI-LANE:", "OUTPUT FORMAT:")


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_roles_dir(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` so the resolver's package-anchor lands in
    `tmp_path`. Pre-creates a `roles/` subdir under `tmp_path` so callers
    can drop role-spec files into it.

    Returns the `tmp_path / "roles"` directory.

    Mirrors the suite convention: `monkeypatch.setattr(sm, "LOG_PATH", ...)`
    redirects everything path-anchored under the package dir.
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    roles = tmp_path / "roles"
    roles.mkdir()
    return roles


def _write_canonical_role_spec(roles_dir: pathlib.Path, role: str,
                               extra: str = "") -> pathlib.Path:
    """Write a minimally-valid role-spec file with all four markers."""
    p = roles_dir / f"{role}.md"
    body = (
        f"# {role}\n\n"
        f"ROLE: {role} stub for tests\n"
        f"LANE: do the thing for tests\n"
        f"ANTI-LANE: don't do the other thing\n"
        f"OUTPUT FORMAT: a thing that looks like the right shape\n"
    )
    if extra:
        body += extra
    p.write_text(body, encoding="utf-8")
    return p


# ===========================================================================
# Smoke (5+)
# ===========================================================================


def test_function_exists_on_module():
    import sm
    assert hasattr(sm, "resolve_role_spec"), \
        "sm.resolve_role_spec must exist"


def test_function_is_callable():
    import sm
    assert callable(sm.resolve_role_spec)


def test_function_name_is_public():
    """No leading underscore — public API."""
    import sm
    assert not sm.resolve_role_spec.__name__.startswith("_")
    assert sm.resolve_role_spec.__name__ == "resolve_role_spec"


def test_function_importable_directly():
    """`from sm import resolve_role_spec` succeeds — public-import form."""
    from sm import resolve_role_spec  # noqa: F401
    assert callable(resolve_role_spec)


def test_function_returns_path_object():
    """Returns a pathlib.Path (not a str, not anything else)."""
    import sm
    result = sm.resolve_role_spec("sm_agent")
    assert isinstance(result, pathlib.PurePath), (
        f"resolve_role_spec must return a pathlib.Path, got "
        f"{result.__class__.__name__}"
    )


def test_function_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "resolve_role_spec" in sm.__all__, (
        f"resolve_role_spec must be in __all__; got {sm.__all__!r}"
    )


# ===========================================================================
# Valid role resolution (8+)
# ===========================================================================


def test_resolves_sm_agent():
    import sm
    p = sm.resolve_role_spec("sm_agent")
    assert p.name == "sm_agent.md"


def test_resolves_test_writer():
    import sm
    p = sm.resolve_role_spec("test_writer")
    assert p.name == "test_writer.md"


def test_resolves_coder():
    import sm
    p = sm.resolve_role_spec("coder")
    assert p.name == "coder.md"


def test_resolves_reviewer():
    import sm
    p = sm.resolve_role_spec("reviewer")
    assert p.name == "reviewer.md"


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_canonical_role_file_exists_on_disk(role):
    """Each canonical role spec is checked into the repo."""
    import sm
    p = sm.resolve_role_spec(role)
    assert p.is_file(), (
        f"role-spec file for {role!r} must exist on disk at {p}"
    )


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_resolved_path_is_absolute(role):
    """Returned path is absolute, regardless of LOG_PATH state."""
    import sm
    p = sm.resolve_role_spec(role)
    assert p.is_absolute(), (
        f"resolve_role_spec({role!r}) must return an absolute path; "
        f"got {p!s}"
    )


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_resolved_path_lives_in_roles_dir(role):
    """Resolved path's parent is the package's `roles/` dir."""
    import sm
    p = sm.resolve_role_spec(role)
    assert p.parent.name == "roles", (
        f"resolved path must live in 'roles/'; got parent {p.parent!s}"
    )


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_resolved_file_is_readable(role):
    """Each role-spec file is non-empty and readable as utf-8 text."""
    import sm
    p = sm.resolve_role_spec(role)
    text = p.read_text(encoding="utf-8")
    assert len(text) > 0, f"role-spec for {role!r} must be non-empty"


def test_resolved_path_anchors_under_log_path_parent():
    """Resolution anchors at LOG_PATH.parent (package-dir convention)."""
    import sm
    p = sm.resolve_role_spec("sm_agent")
    package_dir = pathlib.Path(sm.LOG_PATH).resolve().parent
    assert p.resolve().parent.parent == package_dir, (
        f"resolved path must live under LOG_PATH.parent ({package_dir}); "
        f"got {p}"
    )


# ===========================================================================
# Invalid role rejection (8+)
# ===========================================================================


def test_empty_string_role_raises_value_error():
    import sm
    with pytest.raises(ValueError):
        sm.resolve_role_spec("")


def test_whitespace_only_role_raises_value_error():
    import sm
    with pytest.raises(ValueError):
        sm.resolve_role_spec("   ")


def test_tab_only_role_raises_value_error():
    import sm
    with pytest.raises(ValueError):
        sm.resolve_role_spec("\t\t")


def test_none_role_raises_type_error():
    import sm
    with pytest.raises(TypeError):
        sm.resolve_role_spec(None)


def test_int_role_raises_type_error():
    import sm
    with pytest.raises(TypeError):
        sm.resolve_role_spec(42)


def test_list_role_raises_type_error():
    import sm
    with pytest.raises(TypeError):
        sm.resolve_role_spec(["sm_agent"])


def test_path_role_raises_type_error():
    """Even a Path looks-like-a-string-ish object is rejected — strict str."""
    import sm
    with pytest.raises(TypeError):
        sm.resolve_role_spec(pathlib.Path("sm_agent"))


def test_unknown_role_raises_value_error_naming_offender():
    """Unknown role name → ValueError that names the bad role string."""
    import sm
    with pytest.raises(ValueError) as exc_info:
        sm.resolve_role_spec("not_a_real_role")
    assert "not_a_real_role" in str(exc_info.value), (
        f"error must name the offending role; got: {exc_info.value!s}"
    )


def test_unknown_role_close_to_canonical_still_rejected():
    """Near-miss canonical names (e.g. typos) are still rejected."""
    import sm
    with pytest.raises(ValueError):
        sm.resolve_role_spec("sm_agents")  # trailing s


def test_uppercase_canonical_role_rejected():
    """Role names are case-sensitive — strict canonical set."""
    import sm
    with pytest.raises(ValueError):
        sm.resolve_role_spec("SM_AGENT")


# ===========================================================================
# Missing-file behavior (3+)
# ===========================================================================


def test_missing_file_raises_role_spec_not_found_error(temp_roles_dir):
    """When the role-spec file is absent, raises RoleSpecNotFoundError."""
    import sm
    # temp_roles_dir is empty — no role files written
    with pytest.raises(sm.RoleSpecNotFoundError):
        sm.resolve_role_spec("sm_agent")


def test_role_spec_not_found_error_subclasses_file_not_found_error():
    """Existing `except FileNotFoundError` callers must keep working."""
    import sm
    assert issubclass(sm.RoleSpecNotFoundError, FileNotFoundError), (
        "RoleSpecNotFoundError must subclass FileNotFoundError"
    )


def test_role_spec_not_found_error_in_dunder_all():
    """Structured error is part of the public surface."""
    import sm
    assert "RoleSpecNotFoundError" in sm.__all__, (
        f"RoleSpecNotFoundError must be in __all__; got {sm.__all__!r}"
    )


def test_missing_file_error_message_names_role(temp_roles_dir):
    """The error message identifies which role was missing."""
    import sm
    with pytest.raises(sm.RoleSpecNotFoundError) as exc_info:
        sm.resolve_role_spec("coder")
    assert "coder" in str(exc_info.value), (
        f"missing-file error must name the role; got: {exc_info.value!s}"
    )


def test_missing_file_caught_as_file_not_found_error(temp_roles_dir):
    """A bare `except FileNotFoundError` clause catches it."""
    import sm
    caught = False
    try:
        sm.resolve_role_spec("reviewer")
    except FileNotFoundError:
        caught = True
    assert caught, (
        "RoleSpecNotFoundError must be catchable as FileNotFoundError"
    )


# ===========================================================================
# Path resolution discipline (4+)
# ===========================================================================


def test_resolution_uses_log_path_parent_anchor(temp_roles_dir):
    """Monkeypatched LOG_PATH redirects role lookup to a temp tree."""
    import sm
    _write_canonical_role_spec(temp_roles_dir, "sm_agent")
    p = sm.resolve_role_spec("sm_agent")
    assert p.parent == temp_roles_dir, (
        f"resolution must anchor at LOG_PATH.parent/roles; got {p}"
    )


def test_resolution_works_after_monkeypatch(temp_roles_dir):
    """Sanity: the monkeypatched anchor actually changes the result."""
    import sm
    _write_canonical_role_spec(temp_roles_dir, "test_writer")
    p = sm.resolve_role_spec("test_writer")
    assert p.is_file(), f"resolved file must exist at {p}"
    assert "ROLE:" in p.read_text(encoding="utf-8")


def test_returned_path_is_absolute_after_relative_log_path(
        tmp_path, monkeypatch):
    """Even if LOG_PATH is relative, the returned role-spec path is absolute."""
    import sm
    import os

    # Build a relative-looking LOG_PATH from CWD into tmp_path.
    monkeypatch.chdir(tmp_path)
    relative_log = pathlib.Path("log.jsonl")
    monkeypatch.setattr(sm, "LOG_PATH", relative_log)

    roles = tmp_path / "roles"
    roles.mkdir()
    _write_canonical_role_spec(roles, "coder")

    p = sm.resolve_role_spec("coder")
    assert p.is_absolute(), (
        f"resolved path must be absolute even when LOG_PATH is relative; "
        f"got {p!s} (cwd={os.getcwd()!s})"
    )


def test_two_calls_return_equal_paths():
    """Pure: same input → same Path on repeated calls."""
    import sm
    a = sm.resolve_role_spec("sm_agent")
    b = sm.resolve_role_spec("sm_agent")
    assert a == b


def test_call_does_not_mutate_log_path():
    """Resolver is a pure read — does not touch LOG_PATH."""
    import sm
    before = sm.LOG_PATH
    sm.resolve_role_spec("reviewer")
    after = sm.LOG_PATH
    assert before == after


# ===========================================================================
# Content hash (6+)
# ===========================================================================


def test_role_spec_hash_exists_as_private():
    import sm
    assert hasattr(sm, "_role_spec_hash"), \
        "sm._role_spec_hash must exist (private hash helper)"


def test_role_spec_hash_is_callable():
    import sm
    assert callable(sm._role_spec_hash)


def test_role_spec_hash_returns_str():
    import sm
    h = sm._role_spec_hash("sm_agent")
    assert isinstance(h, str), (
        f"_role_spec_hash must return str; got {h.__class__.__name__}"
    )


def test_role_spec_hash_returns_hex_digest():
    """Hex digest — only [0-9a-f] characters, non-empty."""
    import sm
    h = sm._role_spec_hash("sm_agent")
    assert len(h) > 0
    assert re.fullmatch(r"[0-9a-f]+", h), (
        f"_role_spec_hash must return a hex digest; got {h!r}"
    )


def test_role_spec_hash_deterministic_same_role():
    """Same role → same hash on repeated calls."""
    import sm
    a = sm._role_spec_hash("sm_agent")
    b = sm._role_spec_hash("sm_agent")
    assert a == b


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_role_spec_hash_works_for_each_canonical_role(role):
    import sm
    h = sm._role_spec_hash(role)
    assert isinstance(h, str) and len(h) > 0


def test_role_spec_hash_differs_for_different_roles(temp_roles_dir):
    """Distinct content → distinct hashes."""
    import sm
    _write_canonical_role_spec(temp_roles_dir, "sm_agent",
                               extra="\nsm-agent-tail\n")
    _write_canonical_role_spec(temp_roles_dir, "coder",
                               extra="\ncoder-tail\n")
    a = sm._role_spec_hash("sm_agent")
    b = sm._role_spec_hash("coder")
    assert a != b, (
        f"hashes must differ across roles; both came back as {a!r}"
    )


def test_role_spec_hash_changes_when_content_changes(temp_roles_dir):
    """Editing the file flips the hash — pin freshness."""
    import sm
    _write_canonical_role_spec(temp_roles_dir, "test_writer")
    h1 = sm._role_spec_hash("test_writer")
    # Mutate the underlying file.
    _write_canonical_role_spec(temp_roles_dir, "test_writer",
                               extra="\n\nADDED LATER\n")
    h2 = sm._role_spec_hash("test_writer")
    assert h1 != h2, (
        f"hash must change when file content changes; both were {h1!r}"
    )


def test_role_spec_hash_matches_sha256_of_file_bytes(temp_roles_dir):
    """Pin SHA-256 specifically — the de-facto hex digest for content hashes
    in this suite. (If a future story wants a different digest, this test
    documents the contract that needs renegotiating.)"""
    import sm
    p = _write_canonical_role_spec(temp_roles_dir, "reviewer")
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    actual = sm._role_spec_hash("reviewer")
    assert actual == expected, (
        f"_role_spec_hash must match sha256(file_bytes); "
        f"expected {expected!r}, got {actual!r}"
    )


def test_role_spec_hash_raises_for_unknown_role():
    """Unknown role name flows the same ValueError discipline."""
    import sm
    with pytest.raises(ValueError):
        sm._role_spec_hash("not_a_real_role")


def test_role_spec_hash_raises_for_missing_file(temp_roles_dir):
    """Missing role-spec file flows RoleSpecNotFoundError."""
    import sm
    with pytest.raises(sm.RoleSpecNotFoundError):
        sm._role_spec_hash("sm_agent")


# ===========================================================================
# Grep invariant (3+)
# ===========================================================================


def _read_sm_source() -> str:
    sm_py = PACKAGE_DIR / "sm.py"
    return sm_py.read_text(encoding="utf-8")


def _triple_quoted_strings(source: str) -> list:
    # Return all triple-quoted string literals in `source`. Naive but
    # sufficient for this invariant: matches both triple-double-quoted and
    # triple-single-quoted blobs non-greedily, across newlines. We're
    # hunting for big inline prompt blobs, not parsing Python.
    out: list = []
    out.extend(re.findall(r'"""[\s\S]*?"""', source))
    out.extend(re.findall(r"'''[\s\S]*?'''", source))
    return out


def test_sm_source_has_no_long_role_spec_strings():
    """No triple-quoted literal in `sm.py` longer than 200 chars carries
    role-spec markers inline. Pins the "no inline prompt assembly" rule."""
    source = _read_sm_source()
    offenders: list = []
    for blob in _triple_quoted_strings(source):
        if len(blob) <= 200:
            continue
        if any(marker in blob for marker in ROLE_SPEC_MARKERS):
            offenders.append(blob[:120] + "...")
    assert not offenders, (
        f"sm.py contains {len(offenders)} long triple-quoted string(s) "
        f"with role-spec markers — role text must live in roles/*.md, not "
        f"inline. First offender (truncated): {offenders[0] if offenders else ''!r}"
    )


def test_sm_source_has_no_inline_anti_lane_marker():
    """Spot-check: the literal token 'ANTI-LANE:' must not appear in `sm.py`
    at all (it's a role-spec format marker, only legal in roles/*.md)."""
    source = _read_sm_source()
    assert "ANTI-LANE:" not in source, (
        "sm.py must not contain the 'ANTI-LANE:' marker — that token "
        "belongs to role-spec files, not source code"
    )


def test_sm_source_has_no_inline_role_marker_combo():
    """Pins: no triple-quoted string in `sm.py` carries BOTH 'ROLE:' and
    'LANE:' tokens (the load-bearing role-spec signature)."""
    source = _read_sm_source()
    for blob in _triple_quoted_strings(source):
        if "ROLE:" in blob and "LANE:" in blob:
            pytest.fail(
                f"sm.py contains a triple-quoted string with both 'ROLE:' "
                f"and 'LANE:' markers — role text belongs in roles/*.md"
            )


def test_sm_source_has_no_inline_output_format_marker():
    """Spot-check: the literal token 'OUTPUT FORMAT:' must not appear in
    `sm.py` source — role-spec format marker only."""
    source = _read_sm_source()
    assert "OUTPUT FORMAT:" not in source, (
        "sm.py must not contain the 'OUTPUT FORMAT:' marker — role-spec "
        "marker only, belongs in roles/*.md"
    )


# ===========================================================================
# __all__ hygiene (2+)
# ===========================================================================


def test_resolve_role_spec_in_dunder_all():
    import sm
    assert "resolve_role_spec" in sm.__all__, (
        f"resolve_role_spec must be exported via __all__; got {sm.__all__!r}"
    )


def test_role_spec_hash_not_in_dunder_all():
    """Private — must NOT leak into the public surface."""
    import sm
    assert "_role_spec_hash" not in sm.__all__, (
        f"_role_spec_hash is private — must NOT appear in __all__; "
        f"got {sm.__all__!r}"
    )


def test_role_spec_not_found_error_exported():
    """Public typed error — exported."""
    import sm
    assert "RoleSpecNotFoundError" in sm.__all__


# ===========================================================================
# Role-spec file content sanity (4+)
# ===========================================================================


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_role_spec_contains_role_marker(role):
    """Every checked-in role-spec file contains the 'ROLE:' marker."""
    import sm
    p = sm.resolve_role_spec(role)
    text = p.read_text(encoding="utf-8")
    assert "ROLE:" in text, (
        f"role-spec for {role!r} at {p} must contain 'ROLE:' marker"
    )


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_role_spec_contains_lane_marker(role):
    """Every checked-in role-spec file contains the 'LANE:' marker."""
    import sm
    p = sm.resolve_role_spec(role)
    text = p.read_text(encoding="utf-8")
    assert "LANE:" in text, (
        f"role-spec for {role!r} at {p} must contain 'LANE:' marker"
    )


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_role_spec_contains_anti_lane_marker(role):
    """Every checked-in role-spec file contains the 'ANTI-LANE:' marker."""
    import sm
    p = sm.resolve_role_spec(role)
    text = p.read_text(encoding="utf-8")
    assert "ANTI-LANE:" in text, (
        f"role-spec for {role!r} at {p} must contain 'ANTI-LANE:' marker"
    )


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_role_spec_contains_output_format_marker(role):
    """Every checked-in role-spec file contains the 'OUTPUT FORMAT:'
    marker."""
    import sm
    p = sm.resolve_role_spec(role)
    text = p.read_text(encoding="utf-8")
    assert "OUTPUT FORMAT:" in text, (
        f"role-spec for {role!r} at {p} must contain 'OUTPUT FORMAT:' "
        f"marker"
    )


@pytest.mark.parametrize("role", CANONICAL_ROLES)
def test_role_spec_is_non_trivial(role):
    """Each role-spec is at least 200 chars — guards against placeholder
    stubs sneaking through. Real specs are paragraphs, not one-liners."""
    import sm
    p = sm.resolve_role_spec(role)
    text = p.read_text(encoding="utf-8")
    assert len(text) >= 200, (
        f"role-spec for {role!r} at {p} is too short ({len(text)} chars); "
        f"checked-in specs should be substantive"
    )
