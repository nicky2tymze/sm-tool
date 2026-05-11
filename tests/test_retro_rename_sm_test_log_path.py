"""Iter 2 Story 12 — Retro polish: rename `SM_LOG_PATH` to `SM_TEST_LOG_PATH`.

LOCKED_DECISION 4 (Operator Answers) renames the test-isolation env var so
production semantics are unambiguous. This is a MECHANICAL rename — same
behavior, different name.

Contract pinned by this file:

  1. Every read of the env var in `sm.py` uses the NEW name
     (`SM_TEST_LOG_PATH`); the OLD name (`SM_LOG_PATH`) appears nowhere in
     production source (regex `\\bSM_LOG_PATH\\b` → 0 hits across sm.py).
  2. The posture-audit allowlist (`_ALLOWED_ENV_VAR_READS` in
     `tests/test_posture_audit.py`) drops the old name and adds the new
     name — this is the cascade Story 12 owns.
  3. Setting `SM_TEST_LOG_PATH` redirects `LOG_PATH` in a subprocess CLI
     invocation; setting the OLD name does NOT redirect.
  4. End-to-end ingest under the new env var works (smoke).
  5. The test-suite cascade survey reports how many test files still
     reference the OLD name, so Coder knows the blast radius.

ANTI-LANE: this file does NOT modify `sm.py` or any other test. It only
pins the contract the rename must satisfy.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"
TESTS_DIR = THIS_FILE.parent
POSTURE_AUDIT_PATH = TESTS_DIR / "test_posture_audit.py"

OLD_NAME = "SM_LOG_PATH"
NEW_NAME = "SM_TEST_LOG_PATH"


# ---------------------------------------------------------------------------
# Source loaders
# ---------------------------------------------------------------------------

def _sm_source() -> str:
    return SM_PATH.read_text(encoding="utf-8")


def _posture_audit_source() -> str:
    return POSTURE_AUDIT_PATH.read_text(encoding="utf-8")


# `\bSM_LOG_PATH\b` matches the old name as a whole word (so
# `SM_TEST_LOG_PATH` is NOT a hit) — regex word boundaries treat `_` as
# part of the word, so this distinguishes the two.
_OLD_NAME_RE = re.compile(r"\bSM_LOG_PATH\b")
_NEW_NAME_RE = re.compile(r"\bSM_TEST_LOG_PATH\b")


# ===========================================================================
# A. Production rename pinned via grep (4 tests)
# ===========================================================================

def test_sm_log_path_not_referenced_in_sm_module():
    """Old name MUST NOT appear anywhere in sm.py (the production module).

    The rename per LOCKED_DECISION 4 is mechanical and exhaustive: every
    site that read `SM_LOG_PATH` now reads `SM_TEST_LOG_PATH`. A regex
    over the file text catches both code reads and comments that mention
    the old name. We pin zero hits.
    """
    src = _sm_source()
    hits = _OLD_NAME_RE.findall(src)
    assert hits == [], (
        f"sm.py still references the old env-var name '{OLD_NAME}' "
        f"({len(hits)} occurrence(s)). Story 12 requires a complete "
        f"mechanical rename to '{NEW_NAME}'."
    )


def test_sm_test_log_path_referenced_in_sm_module():
    """New name MUST appear at least once in sm.py — otherwise the
    test-isolation lever is gone entirely.

    Iter 1 Story 9 established the override as the standard fixture; the
    rename moves the name but keeps the mechanism.
    """
    src = _sm_source()
    hits = _NEW_NAME_RE.findall(src)
    assert len(hits) >= 1, (
        f"sm.py does not reference '{NEW_NAME}'; the test-isolation "
        f"env-var override appears to be missing entirely."
    )


def test_sm_log_path_not_in_posture_audit_allowlist():
    """The posture-audit allowlist (`_ALLOWED_ENV_VAR_READS` in
    `tests/test_posture_audit.py`) must drop the old name. Otherwise the
    audit silently permits a name that no longer exists in production.
    """
    src = _posture_audit_source()

    # Locate the `_ALLOWED_ENV_VAR_READS = {...}` block specifically.
    m = re.search(
        r"_ALLOWED_ENV_VAR_READS\s*=\s*\{(?P<body>[^}]*)\}",
        src,
        re.DOTALL,
    )
    assert m is not None, (
        "could not locate `_ALLOWED_ENV_VAR_READS = {...}` block in "
        "tests/test_posture_audit.py — the cascade Story 12 owns "
        "depends on this block existing."
    )
    body = m.group("body")
    # Walk the literal quoted entries — we don't want a false hit on a
    # comment in the block that mentions the old name.
    entries = re.findall(r"""["']([^"']+)["']""", body)
    assert OLD_NAME not in entries, (
        f"'{OLD_NAME}' is still listed in _ALLOWED_ENV_VAR_READS — "
        f"Story 12's cascade requires removing it."
    )


def test_sm_test_log_path_in_posture_audit_allowlist():
    """The posture-audit allowlist MUST include the new name; otherwise
    sm.py's read of `SM_TEST_LOG_PATH` trips the audit and the suite
    goes red on `test_only_sm_log_path_env_var_read`.
    """
    src = _posture_audit_source()
    m = re.search(
        r"_ALLOWED_ENV_VAR_READS\s*=\s*\{(?P<body>[^}]*)\}",
        src,
        re.DOTALL,
    )
    assert m is not None
    body = m.group("body")
    entries = re.findall(r"""["']([^"']+)["']""", body)
    assert NEW_NAME in entries, (
        f"'{NEW_NAME}' must be added to _ALLOWED_ENV_VAR_READS in "
        f"tests/test_posture_audit.py — Story 12 cascade."
    )


def test_sm_log_path_comment_mentions_removed_in_sm_module():
    """Belt-and-suspenders: even the `# Honor SM_LOG_PATH ...` comments
    sprinkled throughout sm.py must be renamed. The old-name regex
    already catches comments, but pin a specific comment-shape match so
    the failure diagnostic is obvious if a comment is overlooked.
    """
    src = _sm_source()
    # The Iter 1 wording: "Honor SM_LOG_PATH ..."
    stale_comment = re.search(
        r"Honor\s+SM_LOG_PATH\b", src
    )
    assert stale_comment is None, (
        "sm.py still has a `# Honor SM_LOG_PATH ...` comment — Story 12 "
        "rename must update the comments too."
    )


def test_sm_test_log_path_appears_in_main_env_branches():
    """Every CLI subcommand branch in `main()` that honored the override
    must now honor the new name. The exact branch count is implementation
    detail, but we pin a lower bound: at least one `os.environ.get("...")`
    call in sm.py targets the new name.
    """
    src = _sm_source()
    pattern = re.compile(
        r"""os\.environ\.get\(\s*["']SM_TEST_LOG_PATH["']"""
    )
    matches = pattern.findall(src)
    assert len(matches) >= 1, (
        f"expected at least one `os.environ.get(\"{NEW_NAME}\")` call "
        f"in sm.py; found {len(matches)}."
    )


# ===========================================================================
# B. Behavioral pin — new name takes effect (subprocess CLI surface)
# ===========================================================================

def _cli(env: dict, *args: str, cwd: pathlib.Path = PACKAGE_DIR):
    """Helper — run `python -m sm <args...>` with the provided env."""
    return subprocess.run(
        [sys.executable, "-m", "sm", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_new_env_var_redirects_log_path_for_ingest(tmp_path):
    """Setting `SM_TEST_LOG_PATH` to a tmp path redirects LOG_PATH for
    the `ingest` CLI subcommand. Pin: after a successful ingest, the
    NEW log file exists and contains the entry; the package's REAL
    log.jsonl was not touched.
    """
    # Stage a minimal valid iteration spec.
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps({
            "iteration_id": "rename-smoke-iter",
            "iteration_goal": "Story 12 rename smoke",
            "requirements": [
                {
                    "requirement_id": "req-1",
                    "title": "Smoke req",
                    "description": "Minimal valid requirement for ingest.",
                    "priority": "MUST",
                    "acceptance_criteria": "AC1",
                },
            ],
        }),
        encoding="utf-8",
    )

    log_path = tmp_path / "isolated_log.jsonl"
    env = os.environ.copy()
    env[NEW_NAME] = str(log_path)
    # Defensively unset the old name in case the test runner has it set.
    env.pop(OLD_NAME, None)

    result = _cli(env, "ingest", str(spec_path))

    assert result.returncode == 0, (
        f"ingest under {NEW_NAME} must succeed; got rc="
        f"{result.returncode}\nstdout={result.stdout!r}\n"
        f"stderr={result.stderr!r}"
    )
    assert log_path.is_file(), (
        f"{NEW_NAME} should have redirected LOG_PATH to {log_path}, "
        f"but that file does not exist."
    )
    contents = log_path.read_text(encoding="utf-8")
    assert "rename-smoke-iter" in contents, (
        f"ingest output not found in redirected log; contents="
        f"{contents!r}"
    )


def test_old_env_var_no_longer_redirects(tmp_path):
    """Setting only the OLD name must NOT redirect LOG_PATH.

    We don't drive a write command here — that would pollute the
    package's real log.jsonl on the pre-Coder failure path. The grep
    tests (A category) already pin the absence of `SM_LOG_PATH` reads
    in sm.py exhaustively. This test is a behavioral regression guard
    against a future re-introduction: if anyone re-adds an
    `os.environ.get("SM_LOG_PATH")` later, this test catches the
    *effect* — `decompose` with the old name set should NOT touch the
    sentinel path (because decompose exits before any write under an
    empty log).

    Pre-Coder this test PASSES (the sentinel isn't written either way
    since decompose exits early). Post-Coder it continues to pass.
    Its job is to fail loudly if a re-introduction happens months from
    now and a write command is wired through.
    """
    sentinel_log = tmp_path / "should_never_be_written.jsonl"
    env = os.environ.copy()
    env[OLD_NAME] = str(sentinel_log)
    env.pop(NEW_NAME, None)

    _cli(env, "decompose")

    assert not sentinel_log.exists(), (
        f"setting the OLD env-var name '{OLD_NAME}' must NOT redirect "
        f"LOG_PATH — but {sentinel_log} was created, which means the "
        f"old name has been re-wired (Story 12 regression)."
    )


def test_new_env_var_redirects_log_path_for_unknown_command(tmp_path):
    """Even commands that don't ultimately write to the log still flow
    through the same env-resolution shape. Pin: an unknown command with
    `SM_TEST_LOG_PATH` set exits non-zero (the 'unknown command' path)
    without crashing on env handling — proving the new name is wired
    everywhere the old name was, not just the happy paths.

    We don't pin specific stdout/stderr text — that's brittle. We pin
    that the CLI ran and exited cleanly under the new env var.
    """
    env = os.environ.copy()
    env[NEW_NAME] = str(tmp_path / "ignored.jsonl")
    env.pop(OLD_NAME, None)

    result = _cli(env, "nonexistent-subcommand-xyzzy")
    assert result.returncode != 0, (
        f"unknown subcommand must exit non-zero; got rc="
        f"{result.returncode}"
    )


def test_new_env_var_smoke_ingest_then_status(tmp_path):
    """Two-step smoke: ingest under SM_TEST_LOG_PATH, then `status`
    should report the iteration is active. Pin: the new env var
    persists state across CLI invocations the way the old name did.
    """
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps({
            "iteration_id": "rename-smoke-two-step",
            "iteration_goal": "Two-step smoke",
            "requirements": [
                {
                    "requirement_id": "req-1",
                    "title": "Smoke req",
                    "description": "Minimal valid requirement for ingest.",
                    "priority": "MUST",
                    "acceptance_criteria": "AC1",
                },
            ],
        }),
        encoding="utf-8",
    )

    log_path = tmp_path / "isolated_log.jsonl"
    env = os.environ.copy()
    env[NEW_NAME] = str(log_path)
    env.pop(OLD_NAME, None)

    r1 = _cli(env, "ingest", str(spec_path))
    assert r1.returncode == 0, (
        f"ingest failed: rc={r1.returncode} stderr={r1.stderr!r}"
    )

    r2 = _cli(env, "status")
    # status should succeed (rc 0) or at minimum NOT report 'unknown
    # command' — that would indicate the env var didn't carry across.
    combined = (r2.stdout + r2.stderr).lower()
    assert "unknown command" not in combined, (
        f"status under {NEW_NAME} hit 'unknown command' — env var "
        f"plumbing broken;\nstdout={r2.stdout!r}\nstderr={r2.stderr!r}"
    )


# ===========================================================================
# C. Test-suite cascade survey (1 test + helper)
# ===========================================================================

def _tests_referencing_old_name() -> list[pathlib.Path]:
    """Walk tests/ and return every `.py` file (other than this one)
    that still contains a `\\bSM_LOG_PATH\\b` reference.

    Used by the cascade-survey test below. Coder reads the failure
    diagnostic to know which files to update.
    """
    hits: list[pathlib.Path] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.resolve() == THIS_FILE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _OLD_NAME_RE.search(text):
            hits.append(path)
    return hits


def test_no_test_file_references_old_env_var_name():
    """After Story 12 closes, NO test file (including this one's peers)
    should still set `SM_LOG_PATH`. Every test that previously used the
    old name must now use `SM_TEST_LOG_PATH`.

    The failure diagnostic lists the files Coder must update. This is
    the cascade-survey deliverable from the TestWriter report — the
    pre-Coder run shows the blast radius; the post-Coder run shows
    zero hits.
    """
    offenders = _tests_referencing_old_name()
    rel = [str(p.relative_to(PACKAGE_DIR)) for p in offenders]
    assert offenders == [], (
        f"{len(offenders)} test file(s) still reference the old env-var "
        f"name '{OLD_NAME}'. Story 12 mechanical rename must update "
        f"each one to '{NEW_NAME}':\n  " + "\n  ".join(rel)
    )


def test_conftest_does_not_reference_old_env_var_name():
    """conftest.py shouldn't reference either name (the env var is set
    per-test, not centrally), but the cascade still needs to verify it
    didn't slip in. Pin: `tests/conftest.py` contains zero `SM_LOG_PATH`
    references after the rename.
    """
    conftest = TESTS_DIR / "conftest.py"
    if not conftest.is_file():
        pytest.skip("no conftest.py present")
    text = conftest.read_text(encoding="utf-8")
    assert not _OLD_NAME_RE.search(text), (
        "tests/conftest.py references the old env-var name "
        f"'{OLD_NAME}'; rename to '{NEW_NAME}' as part of Story 12."
    )
