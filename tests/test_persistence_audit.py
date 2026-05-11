"""Story 22 — JSONL-only persistence audit.

Story 22 (Sprint 2, size S) is a verification-only story that pins the
persistence posture of the sm-tool: the operator-facing package directory
must contain exactly two file artifacts after a full pipeline run —
`log.jsonl` (appended throughout) and the one close handoff JSON written
at close. No SQLite, no `.state`, no `.cache`, no sidecar JSON at any
point. The only modules that write to disk are the writer module
(Story 1's `_append_entry`) and the close handoff producer (Story 18's
`close_iteration`).

What this file pins:

  - End-to-end file artifact count: full ingest -> decompose -> sprint-cut
    -> start -> submit -> record-review -> accept -> close pipeline
    leaves only `log.jsonl` and `close_handoff_<id>.json` in
    `LOG_PATH.parent` (excluding test infra such as `tests/`, `roles/`,
    `iter1/`, `pyproject.toml`, etc., which are tracked package files
    and not runtime persistence artifacts).

  - No sidecars during any phase: after each command (ingest, decompose,
    sprint-cut, start, submit, accept, reject, status, record-review,
    close, force-close), the directory listing contains no `.tmp`,
    `.bak`, `.lock`, `.swp`, `.cache`, `.sqlite`, `.db`, or unexpected
    `.json` files (other than the close handoff at the right time).

  - Static writer audit: there is exactly one write-mode `open(...)` in
    `sm.py` (the `_append_entry` append at the LOG_PATH), and exactly
    one `Path.write_text(...)` (the close handoff producer in
    `close_iteration`). No `Path.touch`, no `Path.write_bytes`, no
    `.write(...)` writes anywhere else in production.

  - Forbidden persistence types: `sm.py` imports neither SQLite nor
    SQLAlchemy, neither `shelve` nor `dbm`, and does not use the
    `pickle` module for persistence (anything emitting bytes-to-disk
    via a non-JSONL channel).

  - log.jsonl + handoff JSON only: no string literal in `sm.py`
    references any other persistent file extension (`.cache`, `.state`,
    `.db`, `.sqlite`, `.pickle`, `.pkl`, etc.).

  - pyproject + config audit: `pyproject.toml` declares no DB / ORM
    runtime dependencies; no `.toml` / `.yaml` / `.ini` config files
    appear in the package directory besides `pyproject.toml` itself.

These tests are mostly static — they read `sm.py` as text and apply
regex / substring / AST checks — plus one end-to-end pipeline test that
exercises every CLI command via the in-process Python API. They run in
milliseconds and require no fixtures other than `isolated_log`.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
import uuid as _uuid

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Source loader + small helpers
# ---------------------------------------------------------------------------


def _source() -> str:
    """Read sm.py once per call. Small file; no caching needed."""
    return SM_PATH.read_text(encoding="utf-8")


def _ast() -> ast.Module:
    """Parse sm.py to an AST for structural audits."""
    return ast.parse(_source(), filename=str(SM_PATH))


def _has_import(src: str, module: str) -> bool:
    """True iff sm.py imports `module` at top-level or via `from module import ...`."""
    pattern = re.compile(
        rf"^\s*(?:import\s+{re.escape(module)}(?:\.|\s|,|$)"
        rf"|from\s+{re.escape(module)}(?:\.|\s)+import\s)",
        re.MULTILINE,
    )
    return bool(pattern.search(src))


def _strip_comments(src: str) -> str:
    """Strip `# ...` line-comments and triple-quoted strings from source
    so we can scan for code-level references without docstring / comment
    false-positives.
    """
    # Drop triple-quoted strings (both ''' and """). Non-greedy, dotall.
    src = re.sub(r"'''.*?'''", "''", src, flags=re.DOTALL)
    src = re.sub(r'""".*?"""', '""', src, flags=re.DOTALL)
    # Drop line comments.
    out_lines: list[str] = []
    for line in src.splitlines():
        # A '#' inside a string literal is rare in this codebase; treat
        # any '#' as a comment marker for the purpose of this audit.
        # Be defensive: ignore '#' that appears inside a quoted region.
        out_lines.append(_strip_line_comment(line))
    return "\n".join(out_lines)


def _strip_line_comment(line: str) -> str:
    """Remove a trailing '# ...' comment if present, ignoring '#' inside
    a quoted string region."""
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "#" and not in_single and not in_double:
            return line[:i].rstrip()
        i += 1
    return line


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file. Mirrors suite convention."""
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _list_dir(p: pathlib.Path) -> list[str]:
    """Sorted list of names directly under `p` (1 level deep)."""
    return sorted([child.name for child in p.iterdir()])


def _stage_roles(tmp_path: pathlib.Path) -> None:
    """Mirror the package's `roles/` dir under tmp_path so decompose can
    resolve role-spec files when LOG_PATH is monkeypatched into tmp_path.
    """
    import shutil

    src_roles = PACKAGE_DIR / "roles"
    dest = tmp_path / "roles"
    if not dest.exists() and src_roles.is_dir():
        shutil.copytree(src_roles, dest)


# ---------------------------------------------------------------------------
# Sanity (3) — source readable, AST parses, audit foundation alive
# ---------------------------------------------------------------------------


def test_sm_py_exists():
    """sm.py is present at the package root."""
    assert SM_PATH.is_file(), f"sm.py not found at {SM_PATH}"


def test_sm_py_parses_as_python():
    """sm.py is syntactically valid Python — AST scan below is sound."""
    tree = _ast()
    assert isinstance(tree, ast.Module)


def test_sm_py_is_substantive():
    """Guard: if sm.py is empty / stub, persistence audit is vacuous."""
    assert len(_source()) > 1000, (
        f"sm.py is too small ({len(_source())} bytes); audit may be vacuous"
    )


# ===========================================================================
# Category A — Static writer audit (6)
#
# Pin the exact set of write-mode primitives in sm.py: one append-mode
# open() in _append_entry, one Path.write_text() in close_iteration. No
# other byte-emitting writers anywhere.
# ===========================================================================


def test_exactly_one_write_mode_open_in_sm_py():
    """Exactly one `open(...)` call in sm.py uses a write-mode string —
    inside `_append_entry`. No other write/append/exclusive-create modes.

    Walks the AST: for every `open(...)` Call, inspect its second
    positional arg (the mode). If that arg is a string literal
    containing any of `a`, `w`, or `x`, count it as a write-mode open.
    """
    tree = _ast()
    write_opens: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "open"):
            continue
        # Mode is either the 2nd positional arg or a `mode=` kwarg.
        mode_arg: ast.AST | None = None
        if len(node.args) >= 2:
            mode_arg = node.args[1]
        else:
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode_arg = kw.value
                    break
        if mode_arg is None:
            # Single-arg open() defaults to 'r' — read-mode, skip.
            continue
        if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
            mode = mode_arg.value
            if any(c in mode for c in "awx"):
                write_opens.append((node.lineno, mode))

    assert len(write_opens) == 1, (
        f"expected exactly one write-mode open(...) in sm.py; got "
        f"{len(write_opens)}: {write_opens!r}"
    )


def test_the_one_write_open_lives_in_append_entry():
    """The single write-mode open() must be inside the `_append_entry`
    function. AST-walk: find FunctionDef `_append_entry`, scan its body
    for an `open(...)` Call.
    """
    tree = _ast()
    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_append_entry":
            target = node
            break
    assert target is not None, "no `_append_entry` function found in sm.py"

    found = False
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "open":
                found = True
                break
    assert found, "_append_entry must contain a literal `open(...)` call"


def test_exactly_one_path_write_text_in_sm_py():
    """`.write_text(` appears exactly once — the close handoff producer
    in `close_iteration`."""
    src = _strip_comments(_source())
    hits = re.findall(r"\.write_text\s*\(", src)
    assert len(hits) == 1, (
        f"expected exactly one `.write_text(` call in sm.py; got "
        f"{len(hits)}: {hits!r}"
    )


def test_no_path_write_bytes_calls():
    """No `.write_bytes(` anywhere — sm.py is text-only persistence."""
    src = _strip_comments(_source())
    assert ".write_bytes(" not in src, (
        "sm.py must not call Path.write_bytes; persistence is text-only"
    )


def test_no_path_touch_calls():
    """No `.touch(` — sm.py must not create empty placeholder files."""
    src = _strip_comments(_source())
    assert ".touch(" not in src, (
        "sm.py must not call Path.touch; no placeholder files allowed"
    )


def test_no_file_write_calls_outside_append_entry():
    """`.write(...)` calls are only allowed inside `_append_entry` (the
    JSONL log appender), `write_agent_output` (the Story 6 atomic
    tempfile write that materializes agent output to disk), and
    `_atomic_write_bytes` (the Story 7 helper that both the greenfield
    and the .candidate-sidecar codepaths funnel through). Any
    `.write(...)` elsewhere in sm.py is a forbidden write site.

    Story 6 (Iter 3 v2 Sprint 1) added `write_agent_output`, which uses
    a `NamedTemporaryFile.write(...)` in its atomic-write pattern (write
    to temp sibling, then `os.replace` rename). Story 7 lifted that
    write pattern into a private `_atomic_write_bytes(path, data)` helper
    so the collision codepath (.candidate + .candidate.diff) reuses the
    exact same atomic semantics as greenfield. The helper is the
    THIRD legal write site, pinned here.
    """
    tree = _ast()

    # Build a set of node ids that belong to each allowed writer.
    allowed_writers = {
        "_append_entry", "write_agent_output", "_atomic_write_bytes"
    }
    inside_allowed: set[int] = set()
    found_writers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in allowed_writers:
            found_writers.add(node.name)
            for inner in ast.walk(node):
                inside_allowed.add(id(inner))
    missing = allowed_writers - found_writers
    assert not missing, (
        f"missing required writer functions in sm.py: {sorted(missing)!r}"
    )

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "write":
                # Skip allowed names: write_text / write_bytes are
                # different attribute names entirely, so they don't
                # match here.
                if id(node) not in inside_allowed:
                    offenders.append(
                        f"line {getattr(node, 'lineno', '?')}"
                    )
    assert offenders == [], (
        f"only `_append_entry` and `write_agent_output` may use "
        f"`.write(...)`; got writes at: {offenders!r}"
    )


# ===========================================================================
# Category B — Forbidden persistence types (6)
#
# No SQLite, no SQLAlchemy, no shelve, no dbm, no pickle in production code.
# ===========================================================================


def test_no_sqlite3_import():
    assert not _has_import(_source(), "sqlite3"), (
        "sm.py must not import sqlite3 — JSONL is the sole store"
    )


def test_no_sqlalchemy_import():
    assert not _has_import(_source(), "sqlalchemy"), (
        "sm.py must not import sqlalchemy — JSONL is the sole store"
    )


def test_no_shelve_import():
    assert not _has_import(_source(), "shelve"), (
        "sm.py must not import shelve — JSONL is the sole store"
    )


def test_no_dbm_import():
    assert not _has_import(_source(), "dbm"), (
        "sm.py must not import dbm — JSONL is the sole store"
    )


def test_no_pickle_import():
    """`pickle` is forbidden in production — it's a non-JSON persistence
    channel that bypasses the JSONL invariant."""
    assert not _has_import(_source(), "pickle"), (
        "sm.py must not import pickle — JSONL is the sole store"
    )


def test_no_marshal_or_dill_imports():
    """Other binary serializers — marshal (stdlib) and dill (PyPI) — are
    equally forbidden as persistence channels."""
    src = _source()
    for mod in ("marshal", "dill", "joblib"):
        assert not _has_import(src, mod), (
            f"sm.py must not import {mod} — JSONL is the sole store"
        )


# ===========================================================================
# Category C — log.jsonl + handoff JSON only (4)
#
# Static scan: the only persistent file paths referenced by sm.py are
# `log.jsonl` (Story 1) and `close_handoff_<id>.json` (Story 18). No
# `.cache`, `.state`, `.db`, etc., literals appear in production source.
# ===========================================================================


def test_only_jsonl_extension_referenced_in_production():
    """The only `.<ext>` string literal that names a persistence
    extension in sm.py is `.jsonl` (the log) and `.json` (the close
    handoff). No `.cache`, `.state`, `.db`, `.sqlite`, `.pickle`,
    `.pkl`, `.tmp`, `.bak`, `.lock`, `.swp`.
    """
    src = _strip_comments(_source())
    forbidden_exts = (
        ".cache", ".state", ".db", ".sqlite", ".sqlite3", ".pickle",
        ".pkl", ".bak", ".swp", ".lock",
    )
    for ext in forbidden_exts:
        # Look for the extension as a literal — quoted in a string.
        # Permit it inside a docstring (already stripped) but never in
        # live code.
        for quote in ("'", '"'):
            needle = f"{quote}{ext}"
            assert needle not in src, (
                f"sm.py contains a {ext!r} string literal — no extra "
                f"persistence channels permitted"
            )


def test_log_jsonl_literal_present():
    """Positive control — `log.jsonl` is the canonical store name and
    must appear in sm.py source."""
    assert "log.jsonl" in _source(), (
        "sm.py must reference `log.jsonl` (the canonical store name)"
    )


def test_close_handoff_naming_convention_present():
    """Positive control — `close_handoff_` prefix (Story 18) is wired."""
    src = _source()
    assert "close_handoff_" in src, (
        "sm.py must reference the `close_handoff_<id>.json` filename "
        "convention from Story 18"
    )


def test_no_tmp_file_naming_in_production():
    """No `.tmp`, `~`, `.swp`, `.bak` suffix string literals in
    production source — atomic-replace patterns aren't used here, and
    their absence is the contract."""
    src = _strip_comments(_source())
    for token in (".tmp'", '.tmp"', '~"', "~'", ".swp'", '.swp"',
                  ".bak'", '.bak"'):
        assert token not in src, (
            f"sm.py contains a {token!r} string literal — temp/backup "
            f"file naming not permitted (no atomic-replace persistence)"
        )


# ===========================================================================
# Category D — pyproject + config audit (4)
#
# pyproject.toml declares no DB / ORM dependencies; no other config
# files (.toml/.yaml/.ini) live in the package root besides pyproject.toml.
# ===========================================================================


def test_pyproject_declares_no_database_dependencies():
    """pyproject.toml must not declare any DB / ORM / persistence
    runtime dependencies. The audit is loose — even an empty list is
    fine — but `sqlite`, `sqlalchemy`, `psycopg`, etc. names anywhere
    in the deps block trip the assertion.
    """
    pyproject = PACKAGE_DIR / "pyproject.toml"
    assert pyproject.is_file()
    text = pyproject.read_text(encoding="utf-8").lower()
    m = re.search(
        r"^\s*dependencies\s*=\s*\[([^\]]*)\]",
        text,
        re.MULTILINE,
    )
    if m is None:
        # No dependencies key at all — vacuously safe.
        return
    inside = m.group(1)
    for forbidden in (
        "sqlite", "sqlalchemy", "psycopg", "pymysql", "redis",
        "mongo", "shelve", "dbm", "tinydb", "lmdb",
    ):
        assert forbidden not in inside, (
            f"pyproject.toml declares a dependency on {forbidden!r}; "
            f"sm-tool must remain JSONL-only / stdlib-only"
        )


def test_pyproject_declares_no_optional_db_dependencies():
    """Same audit applied to `[project.optional-dependencies]` block."""
    pyproject = PACKAGE_DIR / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8").lower()
    # Find any extras block — be conservative and just scan the lower
    # half of the file for forbidden names.
    for forbidden in (
        "sqlite", "sqlalchemy", "psycopg", "pymysql", "redis",
        "mongo", "shelve", "dbm", "tinydb", "lmdb",
    ):
        assert forbidden not in text, (
            f"pyproject.toml mentions {forbidden!r}; sm-tool must "
            f"remain JSONL-only / stdlib-only"
        )


def test_no_extra_config_files_in_package_root():
    """No `.toml` / `.yaml` / `.yml` / `.ini` config files in the
    package root besides `pyproject.toml`. Anything else implies a
    secondary configuration channel that bypasses the JSONL store.
    """
    extras: list[str] = []
    for child in PACKAGE_DIR.iterdir():
        if not child.is_file():
            continue
        name = child.name.lower()
        if name == "pyproject.toml":
            continue
        if name.endswith((".toml", ".yaml", ".yml", ".ini", ".cfg")):
            extras.append(child.name)
    assert extras == [], (
        f"unexpected config files in package root: {extras!r}. Only "
        f"pyproject.toml is permitted."
    )


def test_no_database_url_strings_in_pyproject():
    """No `postgres://` / `sqlite:///` / `mysql://` URL strings in
    pyproject.toml — those indicate a hidden DB wiring."""
    pyproject = PACKAGE_DIR / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8").lower()
    for scheme in ("postgres://", "postgresql://", "sqlite:///",
                   "mysql://", "mongodb://", "redis://"):
        assert scheme not in text, (
            f"pyproject.toml contains a {scheme!r} URL — no DB wiring "
            f"permitted under JSONL-only posture"
        )


# ===========================================================================
# Category E — No sidecars during any phase (9)
#
# Drive the pipeline command by command, checking the directory listing
# after each one. The only artifacts that may appear are `log.jsonl`
# and, at the very end, `close_handoff_<id>.json`.
# ===========================================================================


def _allowed_after_each(tmp_path: pathlib.Path,
                        expected: set[str]) -> None:
    """Assert tmp_path contains exactly `expected` + the `roles/`
    directory we staged for decompose. Any other entry is a sidecar
    leak.
    """
    got = set(_list_dir(tmp_path))
    # Drop the staged roles/ tree (test infrastructure, not runtime
    # persistence).
    got.discard("roles")
    assert got == expected, (
        f"unexpected directory contents.\n"
        f"expected: {sorted(expected)!r}\n"
        f"got:      {sorted(got)!r}"
    )


def _no_forbidden_extensions(tmp_path: pathlib.Path) -> None:
    """Assert no file under tmp_path uses a forbidden persistence
    extension. Excludes the staged `roles/` directory and its .md files.
    """
    for child in tmp_path.iterdir():
        if child.name == "roles":
            continue
        n = child.name.lower()
        for ext in (".tmp", ".bak", ".lock", ".swp", ".cache",
                    ".sqlite", ".sqlite3", ".db", ".pickle", ".pkl",
                    ".state"):
            assert not n.endswith(ext), (
                f"forbidden sidecar extension appeared: {child.name!r}"
            )


def _seed_handoff(tmp_path: pathlib.Path,
                  iteration_id: str = "iter-audit") -> pathlib.Path:
    """Write a minimal handoff JSON file at `tmp_path/handoff.json` and
    return the path. The handoff file itself lives at the tmp_path root
    — it gets consumed by ingest, but it's a pre-existing input, not a
    persistence artifact written by sm-tool.
    """
    handoff = {
        "iteration_id": iteration_id,
        "iteration_goal": "Test iteration",
        "requirements": [
            {"requirement_id": "req-1", "title": "T1",
             "description": "D1", "priority": "MUST",
             "acceptance_criteria": "AC1"},
        ],
    }
    p = tmp_path / "handoff.json"
    p.write_text(json.dumps(handoff), encoding="utf-8")
    return p


def _fake_spawn(role_spec_path: str, requirements: list) -> str:
    """Stub for decompose's `spawn_agent` — returns a 3-story backlog
    rolling up to the single requirement-id in the seed handoff."""
    stories = []
    for i in range(1, 4):
        stories.append({
            "story_id": _uuid.uuid4().hex,
            "sequence": i,
            "title": f"Story {i}",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": f"AC{i}",
        })
    return json.dumps({"stories": stories})


def test_no_sidecar_after_initial_empty_state(isolated_log, tmp_path):
    """Pre-pipeline: nothing in tmp_path. Drop roles/ for downstream."""
    _stage_roles(tmp_path)
    got = set(_list_dir(tmp_path))
    got.discard("roles")
    assert got == set(), (
        f"tmp_path should be empty before pipeline starts; got {got!r}"
    )


def test_no_sidecar_after_ingest(isolated_log, tmp_path):
    """After `ingest`, the only sm-tool artifact is `log.jsonl`. The
    seed handoff stays at tmp_path but that's a pre-existing input."""
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-ingest")
    sm.ingest(tmp_path / "handoff.json")
    _allowed_after_each(tmp_path, {"log.jsonl", "handoff.json"})
    _no_forbidden_extensions(tmp_path)


def test_no_sidecar_after_decompose(isolated_log, tmp_path):
    """After `decompose`, still only log.jsonl (+ pre-existing handoff)."""
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-decomp")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    _allowed_after_each(tmp_path, {"log.jsonl", "handoff.json"})
    _no_forbidden_extensions(tmp_path)


def test_no_sidecar_after_sprint_cut(isolated_log, tmp_path):
    """After `sprint_cut`, still only log.jsonl."""
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-cut")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    _allowed_after_each(tmp_path, {"log.jsonl", "handoff.json"})
    _no_forbidden_extensions(tmp_path)


def test_no_sidecar_after_transitions(isolated_log, tmp_path):
    """After `start` + `submit` (transition_story), still only log.jsonl."""
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-trans")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    state = sm.derive_state()
    in_sprint = list(state["story_states"].keys())[:3]
    sm.transition_story(in_sprint[0], "in_progress")
    sm.transition_story(in_sprint[0], "in_review")
    _allowed_after_each(tmp_path, {"log.jsonl", "handoff.json"})
    _no_forbidden_extensions(tmp_path)


def test_no_sidecar_after_record_review(isolated_log, tmp_path):
    """After `record_review`, still only log.jsonl."""
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-review")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    state = sm.derive_state()
    in_sprint = list(state["story_states"].keys())[:3]
    sm.transition_story(in_sprint[0], "in_progress")
    sm.transition_story(in_sprint[0], "in_review")
    sm.record_review(in_sprint[0], True, "ok")
    _allowed_after_each(tmp_path, {"log.jsonl", "handoff.json"})
    _no_forbidden_extensions(tmp_path)


def test_no_sidecar_after_accept(isolated_log, tmp_path):
    """After `accept` (terminal), still only log.jsonl."""
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-acc")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    state = sm.derive_state()
    in_sprint = list(state["story_states"].keys())[:3]
    for sid in in_sprint:
        sm.transition_story(sid, "in_progress")
        sm.transition_story(sid, "in_review")
        sm.record_review(sid, True, "ok")
        sm.transition_story(sid, "accepted")
    _allowed_after_each(tmp_path, {"log.jsonl", "handoff.json"})
    _no_forbidden_extensions(tmp_path)


def test_no_sidecar_after_status_pure_read(isolated_log, tmp_path):
    """`status` is a pure read — must not create any files."""
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-stat")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    before = set(_list_dir(tmp_path))
    out = sm.status()
    after = set(_list_dir(tmp_path))
    assert isinstance(out, str)
    assert before == after, (
        f"status() must not create files; diff: "
        f"{(after - before) | (before - after)!r}"
    )


def test_no_sidecar_after_reject(isolated_log, tmp_path):
    """After a `reject` terminal transition, still only log.jsonl."""
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-rej")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    state = sm.derive_state()
    in_sprint = list(state["story_states"].keys())[:3]
    sm.transition_story(in_sprint[0], "in_progress")
    sm.transition_story(in_sprint[0], "in_review")
    sm.transition_story(in_sprint[0], "rejected")
    _allowed_after_each(tmp_path, {"log.jsonl", "handoff.json"})
    _no_forbidden_extensions(tmp_path)


def test_no_sidecar_after_force_close(isolated_log, tmp_path):
    """`force_close` produces the close handoff JSON but no other
    sidecars (no .tmp during the write-then-rename, since there is no
    atomic-replace pattern)."""
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-fc")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    sm.force_close(reason="audit test")
    expected = {
        "log.jsonl",
        "handoff.json",
        "close_handoff_iter-fc.json",
    }
    _allowed_after_each(tmp_path, expected)
    _no_forbidden_extensions(tmp_path)


# ===========================================================================
# Category F — End-to-end file artifact count (3)
#
# Full pipeline produces exactly two artifacts written by sm-tool:
# log.jsonl and the one close handoff JSON.
# ===========================================================================


def test_end_to_end_only_two_sm_tool_artifacts(isolated_log, tmp_path):
    """Full ingest -> decompose -> cut -> drive -> close cycle leaves
    only `log.jsonl` and `close_handoff_<id>.json` as artifacts written
    by sm-tool. The pre-existing seed handoff (input) and the staged
    `roles/` (test infra) are excluded.
    """
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-e2e")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    state = sm.derive_state()
    in_sprint = list(state["story_states"].keys())[:3]
    for sid in in_sprint:
        sm.transition_story(sid, "in_progress")
        sm.transition_story(sid, "in_review")
        sm.record_review(sid, True, "ok")
        sm.transition_story(sid, "accepted")
    sm.close_iteration()

    got = set(_list_dir(tmp_path))
    # Drop test infra + the pre-existing input handoff.
    got.discard("roles")
    got.discard("handoff.json")
    expected = {"log.jsonl", "close_handoff_iter-e2e.json"}
    assert got == expected, (
        f"end-to-end pipeline must produce exactly two sm-tool "
        f"artifacts; expected {sorted(expected)!r}, got {sorted(got)!r}"
    )


def test_end_to_end_log_has_all_phases(isolated_log, tmp_path):
    """Belt-and-suspenders: log.jsonl after a full cycle must contain
    every expected entry type — iteration_open, story_backlog,
    sprint_cut, story_state_change, reviewer_approval, iteration_close.
    Confirms the file we see IS the JSONL log, not a sidecar in
    disguise.
    """
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-phases")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    state = sm.derive_state()
    in_sprint = list(state["story_states"].keys())[:3]
    for sid in in_sprint:
        sm.transition_story(sid, "in_progress")
        sm.transition_story(sid, "in_review")
        sm.record_review(sid, True, "ok")
        sm.transition_story(sid, "accepted")
    sm.close_iteration()

    types_seen = set()
    for entry in sm.read_entries():
        types_seen.add(entry.get("type"))
    expected_types = {
        "iteration_open",
        "story_backlog",
        "sprint_cut",
        "story_state_change",
        "reviewer_approval",
        "iteration_close",
    }
    missing = expected_types - types_seen
    assert not missing, (
        f"log.jsonl missing expected entry types {sorted(missing)!r}; "
        f"saw {sorted(types_seen)!r}"
    )


def test_end_to_end_handoff_is_well_formed_json(isolated_log, tmp_path):
    """The one close handoff JSON file must parse as JSON. Confirms it
    is a JSON artifact, not a binary sidecar misnamed `.json`.
    """
    import sm

    _stage_roles(tmp_path)
    _seed_handoff(tmp_path, iteration_id="iter-wellformed")
    sm.ingest(tmp_path / "handoff.json")
    sm.decompose(spawn_agent=_fake_spawn)
    sm.sprint_cut(3)
    state = sm.derive_state()
    in_sprint = list(state["story_states"].keys())[:3]
    for sid in in_sprint:
        sm.transition_story(sid, "in_progress")
        sm.transition_story(sid, "in_review")
        sm.record_review(sid, True, "ok")
        sm.transition_story(sid, "accepted")
    sm.close_iteration()

    handoff_path = tmp_path / "close_handoff_iter-wellformed.json"
    assert handoff_path.is_file()
    payload = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert payload.get("iteration_id") == "iter-wellformed"
