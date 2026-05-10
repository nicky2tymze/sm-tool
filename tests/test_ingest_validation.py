"""Story 6 — pin the DELTAS over Story 5: duplicate-iteration-id detection,
and distinct exit codes per error class on the CLI.

Story 5 already pinned the bulk of ingestion validation (path errors, JSON
parse errors, top-level shape, per-requirement validation, single-active
enforcement, failure invariants). This file pins ONLY what Story 6 adds:

  1. DUPLICATE-ITERATION-ID detection. ingest() must raise `ValueError`
     when the handoff's iteration_id matches the iteration_id on ANY
     prior `iteration_open` log entry — *including iterations that have
     since been closed or force-closed*. This is distinct from the
     single-active-iteration check (Story 7 / already-pinned in
     test_ingest.py): the single-active check fires when SOMETHING is
     currently open; the duplicate-id check fires when the proposed
     iteration_id was EVER used. The error message must name the
     duplicate iteration_id. No log write on failure.

  2. DISTINCT EXIT CODES per error class on the CLI surface. The
     `python -m sm ingest <path>` command must surface a documented,
     non-zero exit code per failure class:

         0 = success
         1 = unexpected / other error (catch-all)
         2 = path error      (FileNotFoundError, IsADirectoryError)
         3 = JSON parse error
         4 = handoff shape error (top-level missing fields, bad types,
                                  bad/duplicate requirements)
         5 = duplicate iteration_id (same id appeared in a prior
                                     iteration_open, open or closed)
         6 = single-active-iteration violation (an iteration is open;
                                               Story 7 will reuse this code)

     Each class must produce its own exit code, distinct from every
     other class and consistent across multiple invocations.

  3. DOCUMENTATION invariant: the exit codes must be discoverable —
     either through `python -m sm ingest --help` (or `python -m sm
     --help`) output, or in the README. We test BOTH avenues lightly;
     the test passes if either surface documents the codes.

ANTI-DUPLICATION: this file does NOT re-pin happy-path / shape /
per-requirement / single-active behavior already covered by
test_ingest.py. Read that file first; this one is the delta only.

These tests must FAIL on first run — duplicate-id detection and
distinct exit codes do not exist yet. Once Coder implements them,
they pass.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# Documented exit codes (Story 6 contract — Test Writer's call).
EXIT_OK = 0
EXIT_OTHER = 1
EXIT_PATH = 2
EXIT_JSON = 3
EXIT_SHAPE = 4
EXIT_DUP_ID = 5
EXIT_SINGLE_ACTIVE = 6


# ---------------------------------------------------------------------------
# Fixtures + helpers (mirrors test_ingest.py conventions)
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file."""
    import sm
    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _canonical_requirement(req_id: str = "req-1",
                           title: str = "Do the thing",
                           description: str = "A description.",
                           priority: str = "MUST",
                           acceptance_criteria: str = "AC: it works"
                           ) -> dict:
    return {
        "requirement_id": req_id,
        "title": title,
        "description": description,
        "priority": priority,
        "acceptance_criteria": acceptance_criteria,
    }


def _canonical_handoff(iteration_id: str = "iter-1",
                       iteration_goal: str = "Ship the thing.",
                       requirements=None) -> dict:
    if requirements is None:
        requirements = [_canonical_requirement("req-1", "Title 1")]
    return {
        "iteration_id": iteration_id,
        "iteration_goal": iteration_goal,
        "requirements": list(requirements),
    }


def _write_handoff(tmp_path: pathlib.Path,
                   handoff: dict,
                   name: str = "handoff.json") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(json.dumps(handoff), encoding="utf-8")
    return p


def _write_raw(tmp_path: pathlib.Path,
               raw: str,
               name: str = "handoff.json") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(raw, encoding="utf-8")
    return p


def _close_active_iteration(reason=None) -> None:
    """Append a synthetic iteration_close entry directly via the canonical
    builder (Story 14 will provide the real close command — Story 6 tests
    only need a way to free the active slot to exercise duplicate-id checks
    against a CLOSED iteration)."""
    import sm
    close = sm.build_entry("iteration_close", {
        "closed_by": "test-harness",
        "reason": reason,
        "accepted_count": 0,
        "rejected_count": 0,
        "force_closed_count": 0,
    })
    sm._append_entry(close)


def _run_cli(handoff_path, tmp_path, log_name="cli_log.jsonl"):
    """Invoke `python -m sm ingest <path>` in a hermetic env with
    SM_LOG_PATH redirected. Returns the CompletedProcess.
    """
    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(tmp_path / log_name)
    return subprocess.run(
        [sys.executable, "-m", "sm", "ingest", str(handoff_path)],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _seed_cli_with_open_iteration(tmp_path, iter_id: str,
                                  log_name="cli_log.jsonl"):
    """Run a successful CLI ingest first so that the SM_LOG_PATH log
    contains an `iteration_open` entry. Returns the env dict that
    points at the same log file (so a second CLI call sees the
    seeded state).
    """
    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(tmp_path / log_name)
    h = _canonical_handoff(iteration_id=iter_id)
    p = tmp_path / f"seed_{iter_id}.json"
    p.write_text(json.dumps(h), encoding="utf-8")
    res = subprocess.run(
        [sys.executable, "-m", "sm", "ingest", str(p)],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == EXIT_OK, (
        f"seed ingest failed: rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )
    return env


def _seed_log_bytes(isolated_log_path: pathlib.Path) -> bytes:
    """Return current bytes of the redirected log (after some prior write)."""
    if not isolated_log_path.exists():
        return b""
    return isolated_log_path.read_bytes()


# ===========================================================================
# Smoke (2)
# ===========================================================================

def test_module_imports():
    """sm imports cleanly — pins the test file is wired to the right module."""
    import sm
    assert hasattr(sm, "ingest")


def test_test_ingest_module_independent():
    """This file does not collide with test_ingest.py — they share helper
    names but live in independent modules."""
    # pytest treats each test_*.py as its own module — no shared state.
    # Just confirm both files exist under tests/.
    here = pathlib.Path(__file__).parent
    assert (here / "test_ingest.py").exists()
    assert (here / "test_ingest_validation.py").exists()


# ===========================================================================
# Duplicate-iteration-id detection — programmatic ingest() (8+)
# ===========================================================================

def test_duplicate_iter_id_after_close_raises_value_error(
    isolated_log, tmp_path
):
    """Ingesting an iteration_id that matches a CLOSED iteration's id
    raises ValueError — the duplicate-id check fires even after the
    prior iteration is closed."""
    import sm

    # Open and close iter-1.
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()

    # Now try to re-ingest with the same iteration_id.
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)


def test_duplicate_iter_id_after_force_close_raises_value_error(
    isolated_log, tmp_path
):
    """Even a force-closed (close-with-reason) prior iteration counts as
    'used' for the duplicate-id check."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-fc"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration(reason="force_closed_by_test")

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-fc"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)


def test_duplicate_iter_id_error_names_the_id(isolated_log, tmp_path):
    """The duplicate-id error message names the offending iteration_id."""
    import sm

    p1 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-named-dup"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()

    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-named-dup"),
                        name="h2.json")
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p2)
    assert "iter-named-dup" in str(exc_info.value), (
        f"duplicate-id error must name the id; got {exc_info.value!r}"
    )


def test_duplicate_iter_id_does_not_modify_log(isolated_log, tmp_path):
    """Failure invariant: a duplicate-id failure does NOT append to the log."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()
    bytes_before = isolated_log.read_bytes()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)
    assert isolated_log.read_bytes() == bytes_before, (
        "duplicate-id failure must not change the log"
    )


def test_distinct_iter_id_after_close_succeeds(isolated_log, tmp_path):
    """After close, a NEW (non-duplicate) iteration_id ingests cleanly —
    proves the check is duplicate-id-specific, not a blanket close-and-done."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    sm.ingest(p2)

    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-2"


def test_empty_log_dup_check_is_noop(isolated_log, tmp_path):
    """No prior opens → duplicate-id check is a no-op; first ingest succeeds."""
    import sm
    assert not isolated_log.exists()
    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="first-ever"))
    sm.ingest(p)
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "first-ever"


def test_dup_check_against_first_of_multiple_prior_opens(
    isolated_log, tmp_path
):
    """With multiple prior opens (each closed), a duplicate of the FIRST
    one still trips the check."""
    import sm

    # Open + close iter-A.
    pa = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-A"),
                        name="ha.json")
    sm.ingest(pa)
    _close_active_iteration()

    # Open + close iter-B.
    pb = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-B"),
                        name="hb.json")
    sm.ingest(pb)
    _close_active_iteration()

    # Try to re-use iter-A.
    p_dup = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-A"),
                           name="hdup.json")
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p_dup)
    assert "iter-A" in str(exc_info.value)


def test_dup_check_against_second_of_multiple_prior_opens(
    isolated_log, tmp_path
):
    """A duplicate of the MOST RECENT closed iteration also trips the check."""
    import sm

    pa = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-A"),
                        name="ha.json")
    sm.ingest(pa)
    _close_active_iteration()

    pb = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-B"),
                        name="hb.json")
    sm.ingest(pb)
    _close_active_iteration()

    p_dup = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-B"),
                           name="hdup.json")
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p_dup)
    assert "iter-B" in str(exc_info.value)


def test_dup_check_unicode_id(isolated_log, tmp_path):
    """Duplicate-id check works for unicode iteration_ids."""
    import sm
    iid = "iter-α-β-π"

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id=iid),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id=iid),
                        name="h2.json")
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p2)
    assert iid in str(exc_info.value)


def test_dup_check_is_case_sensitive(isolated_log, tmp_path):
    """iteration_id 'iter-X' and 'ITER-X' are distinct (case-sensitive
    comparison). Pin this to lock the contract."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-X"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()

    # Different case → not a duplicate; should succeed.
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="ITER-X"),
                        name="h2.json")
    sm.ingest(p2)
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "ITER-X"


# ===========================================================================
# Distinct exit codes per error class — CLI subprocess (10+)
# ===========================================================================

def test_cli_success_exits_zero(tmp_path):
    """Happy path → exit 0."""
    handoff = _canonical_handoff(iteration_id="cli-success")
    p = _write_handoff(tmp_path, handoff, name="ok.json")
    res = _run_cli(p, tmp_path)
    assert res.returncode == EXIT_OK, (
        f"success must exit 0; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_missing_path_exits_path_code(tmp_path):
    """Missing file → exit 2 (path error)."""
    missing = tmp_path / "does_not_exist.json"
    res = _run_cli(missing, tmp_path)
    assert res.returncode == EXIT_PATH, (
        f"missing-path exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_directory_path_exits_path_code(tmp_path):
    """Directory path → exit 2 (path error class — same as missing)."""
    d = tmp_path / "subdir"
    d.mkdir()
    res = _run_cli(d, tmp_path)
    assert res.returncode == EXIT_PATH, (
        f"directory-path exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_invalid_json_exits_json_code(tmp_path):
    """Malformed JSON → exit 3 (JSON parse error)."""
    p = _write_raw(tmp_path, "{this is not valid json", name="bad.json")
    res = _run_cli(p, tmp_path)
    assert res.returncode == EXIT_JSON, (
        f"invalid-json exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_empty_file_exits_json_code(tmp_path):
    """Empty file → exit 3 (JSON parse error class)."""
    p = _write_raw(tmp_path, "", name="empty.json")
    res = _run_cli(p, tmp_path)
    assert res.returncode == EXIT_JSON, (
        f"empty-file exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_top_level_array_exits_shape_code(tmp_path):
    """JSON array at top level → exit 4 (handoff shape error)."""
    p = _write_raw(tmp_path, json.dumps([1, 2, 3]), name="arr.json")
    res = _run_cli(p, tmp_path)
    assert res.returncode == EXIT_SHAPE, (
        f"top-level-array exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_missing_iteration_id_exits_shape_code(tmp_path):
    """Missing iteration_id → exit 4 (shape error)."""
    h = _canonical_handoff()
    del h["iteration_id"]
    p = _write_handoff(tmp_path, h, name="no_id.json")
    res = _run_cli(p, tmp_path)
    assert res.returncode == EXIT_SHAPE, (
        f"missing-iteration_id exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_missing_requirements_exits_shape_code(tmp_path):
    """Missing requirements → exit 4 (shape error)."""
    h = _canonical_handoff()
    del h["requirements"]
    p = _write_handoff(tmp_path, h, name="no_reqs.json")
    res = _run_cli(p, tmp_path)
    assert res.returncode == EXIT_SHAPE, (
        f"missing-requirements exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_empty_requirements_exits_shape_code(tmp_path):
    """Empty requirements list → exit 4 (shape error class)."""
    h = _canonical_handoff(requirements=[])
    p = _write_handoff(tmp_path, h, name="empty_reqs.json")
    res = _run_cli(p, tmp_path)
    assert res.returncode == EXIT_SHAPE, (
        f"empty-requirements exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_duplicate_requirement_ids_exits_shape_code(tmp_path):
    """Duplicate requirement_ids inside one handoff → exit 4 (shape error)."""
    h = _canonical_handoff()
    h["requirements"] = [
        _canonical_requirement("dup"),
        _canonical_requirement("dup"),
    ]
    p = _write_handoff(tmp_path, h, name="dup_req.json")
    res = _run_cli(p, tmp_path)
    assert res.returncode == EXIT_SHAPE, (
        f"duplicate-requirement-ids exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_duplicate_iteration_id_exits_dup_id_code(tmp_path):
    """Duplicate iteration_id (after prior close) → exit 5."""
    log_name = "dup_id_log.jsonl"
    env = _seed_cli_with_open_iteration(tmp_path, "iter-X", log_name=log_name)

    # Manually close iter-X by appending an iteration_close entry directly
    # to the SM_LOG_PATH file the CLI is using. We do this by importing sm
    # and pointing it at the same file.
    import sm
    from pathlib import Path as _P
    target_log = _P(env["SM_LOG_PATH"])
    # Build close entry via the canonical builder, then append manually
    # so the on-disk log gains an iteration_close record.
    close = sm.build_entry("iteration_close", {
        "closed_by": "test",
        "reason": None,
        "accepted_count": 0,
        "rejected_count": 0,
        "force_closed_count": 0,
    })
    # Append directly — bypass _append_entry's LOG_PATH dependency.
    line = json.dumps(close, ensure_ascii=False) + "\n"
    with open(target_log, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
        fh.flush()

    # Now CLI-ingest a handoff with the same iteration_id.
    h = _canonical_handoff(iteration_id="iter-X")
    p = tmp_path / "dup.json"
    p.write_text(json.dumps(h), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "sm", "ingest", str(p)],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == EXIT_DUP_ID, (
        f"duplicate-iteration-id exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


def test_cli_single_active_iteration_exits_single_active_code(tmp_path):
    """Trying to ingest while another iteration is open → exit 6."""
    log_name = "active_log.jsonl"
    env = _seed_cli_with_open_iteration(tmp_path, "iter-open",
                                        log_name=log_name)

    # Without closing, try a different iteration_id.
    h = _canonical_handoff(iteration_id="iter-other")
    p = tmp_path / "other.json"
    p.write_text(json.dumps(h), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "sm", "ingest", str(p)],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == EXIT_SINGLE_ACTIVE, (
        f"single-active exit code mismatch; got rc={res.returncode}\n"
        f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    )


# ----- Codes are mutually distinct -----

def test_cli_exit_codes_are_mutually_distinct(tmp_path):
    """The set of error-class exit codes contains no duplicates — and each
    is non-zero."""
    codes = {EXIT_PATH, EXIT_JSON, EXIT_SHAPE, EXIT_DUP_ID, EXIT_SINGLE_ACTIVE}
    assert len(codes) == 5, (
        f"error-class codes must be mutually distinct; got {codes}"
    )
    for c in codes:
        assert c != EXIT_OK, f"error code {c} collides with success (0)"


def test_cli_path_code_distinct_from_json_code(tmp_path):
    """Empirically: missing-path exit code != invalid-json exit code."""
    missing = tmp_path / "nope.json"
    bad_json = _write_raw(tmp_path, "{nope", name="bad.json")
    res_path = _run_cli(missing, tmp_path, log_name="a.jsonl")
    res_json = _run_cli(bad_json, tmp_path, log_name="b.jsonl")
    assert res_path.returncode != res_json.returncode, (
        f"path and json exit codes must differ; both = {res_path.returncode}\n"
        f"path stderr: {res_path.stderr!r}\njson stderr: {res_json.stderr!r}"
    )


def test_cli_json_code_distinct_from_shape_code(tmp_path):
    """Empirically: invalid-json exit code != missing-iteration_id exit code."""
    bad_json = _write_raw(tmp_path, "{nope", name="bad.json")
    h = _canonical_handoff()
    del h["iteration_id"]
    shape = _write_handoff(tmp_path, h, name="noid.json")
    res_json = _run_cli(bad_json, tmp_path, log_name="a.jsonl")
    res_shape = _run_cli(shape, tmp_path, log_name="b.jsonl")
    assert res_json.returncode != res_shape.returncode, (
        f"json and shape exit codes must differ; both = {res_json.returncode}"
    )


def test_cli_shape_code_distinct_from_path_code(tmp_path):
    """Empirically: shape error != path error."""
    h = _canonical_handoff()
    del h["iteration_id"]
    shape = _write_handoff(tmp_path, h, name="noid.json")
    missing = tmp_path / "nope.json"
    res_shape = _run_cli(shape, tmp_path, log_name="a.jsonl")
    res_path = _run_cli(missing, tmp_path, log_name="b.jsonl")
    assert res_shape.returncode != res_path.returncode


# ----- Codes are stable across invocations -----

def test_cli_path_code_consistent_across_invocations(tmp_path):
    """The path-error exit code is the SAME on every invocation (stable
    across calls — not flaky)."""
    missing = tmp_path / "still_missing.json"
    rcs = []
    for i in range(3):
        res = _run_cli(missing, tmp_path, log_name=f"log_{i}.jsonl")
        rcs.append(res.returncode)
    assert len(set(rcs)) == 1, (
        f"path-error exit code must be stable; got {rcs}"
    )


def test_cli_json_code_consistent_across_invocations(tmp_path):
    """JSON parse error exit code stable across invocations."""
    p = _write_raw(tmp_path, "{not json", name="bad.json")
    rcs = []
    for i in range(3):
        res = _run_cli(p, tmp_path, log_name=f"log_{i}.jsonl")
        rcs.append(res.returncode)
    assert len(set(rcs)) == 1, (
        f"json-error exit code must be stable; got {rcs}"
    )


def test_cli_shape_code_consistent_across_invocations(tmp_path):
    """Shape-error exit code stable across invocations."""
    h = _canonical_handoff()
    del h["iteration_id"]
    p = _write_handoff(tmp_path, h, name="noid.json")
    rcs = []
    for i in range(3):
        res = _run_cli(p, tmp_path, log_name=f"log_{i}.jsonl")
        rcs.append(res.returncode)
    assert len(set(rcs)) == 1, (
        f"shape-error exit code must be stable; got {rcs}"
    )


def test_cli_success_code_consistent_across_invocations(tmp_path):
    """Success exit code is stably 0 across multiple distinct happy paths."""
    rcs = []
    for i in range(3):
        h = _canonical_handoff(iteration_id=f"iter-stable-{i}")
        p = _write_handoff(tmp_path, h, name=f"ok_{i}.json")
        res = _run_cli(p, tmp_path, log_name=f"log_{i}.jsonl")
        rcs.append(res.returncode)
    assert rcs == [EXIT_OK, EXIT_OK, EXIT_OK], (
        f"success must be stably 0; got {rcs}"
    )


# ----- All non-zero codes ARE non-zero -----

def test_cli_all_error_codes_nonzero(tmp_path):
    """Every documented error code is non-zero (sanity)."""
    for c in (EXIT_OTHER, EXIT_PATH, EXIT_JSON, EXIT_SHAPE,
              EXIT_DUP_ID, EXIT_SINGLE_ACTIVE):
        assert c != 0, f"error code {c} must not be 0"


# ===========================================================================
# Failure invariants — duplicate-id failure leaves log byte-for-byte unchanged
# (4+)
# ===========================================================================

def test_dup_id_after_close_log_unchanged(isolated_log, tmp_path):
    """Programmatic: duplicate-id failure after a close leaves the log
    byte-for-byte unchanged."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()
    bytes_before = isolated_log.read_bytes()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)
    assert isolated_log.read_bytes() == bytes_before


def test_dup_id_after_force_close_log_unchanged(isolated_log, tmp_path):
    """Same invariant for a force-closed prior iteration."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration(reason="force")
    bytes_before = isolated_log.read_bytes()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)
    assert isolated_log.read_bytes() == bytes_before


def test_dup_id_failure_does_not_append_any_entry(isolated_log, tmp_path):
    """After a dup-id failure, the count of log entries is unchanged."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()
    count_before = sum(1 for _ in sm.read_entries())

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)
    count_after = sum(1 for _ in sm.read_entries())
    assert count_after == count_before, (
        f"dup-id failure must not append; before={count_before}, "
        f"after={count_after}"
    )


def test_dup_id_failure_does_not_call_append_entry(
    isolated_log, tmp_path, monkeypatch
):
    """Wire-up check: on dup-id failure, _append_entry is NOT invoked."""
    import sm

    # Set up a closed prior iteration normally.
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()

    # Now monkey-patch _append_entry and attempt the dup ingest.
    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)
    assert calls["n"] == 0, (
        f"_append_entry must NOT be called on dup-id failure; got {calls['n']}"
    )


def test_dup_id_failure_does_not_call_build_entry(
    isolated_log, tmp_path, monkeypatch
):
    """Wire-up check: on dup-id failure, build_entry is NOT invoked.
    The validation must short-circuit BEFORE the entry would be built."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()

    calls = {"n": 0}
    real = sm.build_entry

    def fake(type_, content):
        calls["n"] += 1
        return real(type_, content)

    monkeypatch.setattr(sm, "build_entry", fake)
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)
    assert calls["n"] == 0, (
        f"build_entry must NOT be called on dup-id failure; got {calls['n']}"
    )


# ===========================================================================
# Round-trip — duplicate-id failure leaves prior derive_state untouched (3+)
# ===========================================================================

def test_round_trip_after_dup_id_failure_close_status_intact(
    isolated_log, tmp_path
):
    """After a dup-id failure, derive_state still reflects the closed
    prior iteration (close_status set, no active iteration)."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration(reason="completed")

    state_before = sm.derive_state()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)

    state_after = sm.derive_state()
    assert state_after == state_before, (
        "derive_state must be unchanged after a duplicate-id failure"
    )
    # Double-pin specifics:
    assert state_after["active_iteration"] is None
    assert state_after["close_status"] is not None


def test_round_trip_after_dup_id_no_active_iteration(isolated_log, tmp_path):
    """After dup-id failure, no iteration is active (the failed ingest
    didn't slip an iteration_open into the log)."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)

    state = sm.derive_state()
    assert state["active_iteration"] is None


def test_round_trip_can_succeed_after_dup_failure_with_fresh_id(
    isolated_log, tmp_path
):
    """After a dup-id failure, a subsequent ingest with a FRESH id
    proceeds normally — the failure didn't poison the log state."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                        name="h1.json")
    sm.ingest(p1)
    _close_active_iteration()

    # Trigger and absorb the dup failure.
    p_dup = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid"),
                           name="hdup.json")
    with pytest.raises(ValueError):
        sm.ingest(p_dup)

    # Now a different id ingests cleanly.
    p3 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iid-fresh"),
                        name="h3.json")
    sm.ingest(p3)
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iid-fresh"


# ===========================================================================
# Documentation invariant — exit codes documented somewhere (1)
# ===========================================================================

def test_exit_codes_documented_somewhere():
    """At least one of: README.md or `python -m sm --help` / `python -m
    sm ingest --help` mentions the documented exit codes.

    This test is intentionally lenient — it passes as soon as ANY of the
    expected non-zero codes (2, 3, 4, 5, 6) appears in the README OR in a
    --help surface. If neither documents the codes, the test fails and
    the Coder must add at least minimal docs.
    """
    candidates: list[str] = []

    # 1) README.
    readme = PACKAGE_DIR / "README.md"
    if readme.exists():
        candidates.append(readme.read_text(encoding="utf-8"))

    # 2) `python -m sm --help`.
    try:
        res = subprocess.run(
            [sys.executable, "-m", "sm", "--help"],
            cwd=str(PACKAGE_DIR),
            capture_output=True,
            text=True,
            timeout=15,
        )
        candidates.append((res.stdout or "") + (res.stderr or ""))
    except (subprocess.TimeoutExpired, OSError):
        pass

    # 3) `python -m sm ingest --help`.
    try:
        res2 = subprocess.run(
            [sys.executable, "-m", "sm", "ingest", "--help"],
            cwd=str(PACKAGE_DIR),
            capture_output=True,
            text=True,
            timeout=15,
        )
        candidates.append((res2.stdout or "") + (res2.stderr or ""))
    except (subprocess.TimeoutExpired, OSError):
        pass

    haystack = "\n".join(candidates).lower()

    # The codes themselves must be referenced; we accept the digit appearing
    # in proximity to the word "exit" or "code" anywhere in the docs OR
    # all five non-zero codes appearing (loose heuristic).
    referenced = sum(
        1 for c in ("2", "3", "4", "5", "6") if c in haystack
    )
    has_keyword = ("exit" in haystack) or ("returncode" in haystack) or \
                  ("return code" in haystack)
    assert (referenced >= 5 and has_keyword), (
        "Story 6 documentation invariant: at least one of README.md, "
        "`python -m sm --help`, or `python -m sm ingest --help` must "
        "document the non-zero exit codes (2,3,4,5,6) and use the word "
        "'exit' / 'return code'. Add docs to README or wire up --help."
    )
