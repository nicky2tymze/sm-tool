"""Story 23 — pin the contract of `sm.execute` + the `execute` CLI subcommand.

Story 23 (Sprint 2, NICE, size L) is the TestWriter -> Coder -> Reviewer
execution pipeline that ships at the very end of Iter 1. It introduces a new
public function, a new typed error, and a new CLI subcommand:

    execute(story_id: str,
            spawn_test_writer: Callable | None = None,
            spawn_coder:       Callable | None = None,
            spawn_reviewer:    Callable | None = None) -> dict
    class ExecuteError(ValueError)
    python -m sm execute <story_id>

What this file pins:

  - Function signature and shape:
      `execute(story_id, spawn_test_writer=None, spawn_coder=None,
      spawn_reviewer=None)` — PUBLIC, callable, in `sm.__all__`, importable
      as `from sm import execute`. Returns a final dict (the wrapping
      summary OR the final state_change entry — tests check the keys that
      matter, not the wrapper choice).

  - Default spawn callables (all None) -> NotImplementedError mentioning
    Iter 2. Operators / tests inject stubs to drive the function in Iter 1.

  - Validation cascade — each failure raises `ExecuteError`, log unchanged,
    NO spawn callable invoked:
      * story_id non-string -> TypeError (before any state read)
      * no active iteration -> ExecuteError("no active iteration")
      * no sprint_cut yet -> ExecuteError
      * story_id not in active sprint (unknown OR deferred) -> ExecuteError
      * current state not in {planned, in_progress} -> ExecuteError naming
        the current state

  - Typed error class:
      `sm.ExecuteError` exists, subclasses `ValueError`, is in `sm.__all__`.

  - Pipeline behavior on a planned story (each step writes its own log
    entry — partial pipeline = truthful audit trail):
      Step 1: planned -> in_progress  (story_state_change)
      Step 2: spawn_test_writer       -> testwriter_output entry
      Step 3: spawn_coder             -> coder_output entry
      Step 4: in_progress -> in_review (story_state_change)
      Step 5: spawn_reviewer           -> reviewer_approval entry
              (same shape Story 15 produces)
      Step 6: based on Reviewer outcome:
              approved=True + non-empty test_result -> in_review -> accepted
              approved=False OR empty test_result   -> in_review -> rejected

  - testwriter_output / coder_output entry shape (auto-stamped fields plus):
      story_id, role_spec_path, role_spec_hash, output

  - reviewer_approval entry shape (matches Story 15):
      story_id, approved (bool), test_result (str)

  - Spawn callable contracts (synchronous, injected by operator):
      spawn_test_writer(role_spec_path: str, story: dict) -> str
      spawn_coder(role_spec_path: str, story: dict, test_code: str) -> str
      spawn_reviewer(role_spec_path: str, story: dict, test_code: str,
                     impl_code: str) -> dict {"approved", "test_result"}

  - Role-spec wiring:
      * TestWriter receives sm-tool/roles/test_writer.md
      * Coder receives sm-tool/roles/coder.md
      * Reviewer receives sm-tool/roles/reviewer.md
      * Each output entry carries the corresponding role_spec_path +
        role_spec_hash.

  - Sequential execution: spawn_coder fires only after spawn_test_writer
    returns; spawn_reviewer fires only after spawn_coder returns.

  - CLI surface — `python -m sm execute <story_id>`:
      * Subcommand recognized (not "unknown command").
      * Exit 0 on accepted.
      * Exit EXIT_TRANSITION (9) on rejected (a valid completion that
        wasn't an "accept").
      * Exit non-zero on validation failure.
      * Default (no injection) -> NotImplementedError -> non-zero.

  - Failure invariants:
      * pre-spawn validation failures -> log unchanged, no spawn invoked.
      * post-spawn pipeline partial-failures leave already-written entries
        in place (truthful audit trail).

Tests must FAIL on first run — `execute`, `ExecuteError`, and the `execute`
CLI subcommand do not exist yet. The Coder downstream implements them to
satisfy these tests.
"""

from __future__ import annotations

import inspect
import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid as _uuid

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


SOURCE_ROLES_DIR = PACKAGE_DIR / "roles"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file and stage the canonical
    roles/ directory at tmp_path so `resolve_role_spec` resolves at the
    redirected anchor.
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)

    # Stage roles/ at the new anchor so resolve_role_spec("test_writer"),
    # resolve_role_spec("coder"), and resolve_role_spec("reviewer") all
    # succeed during the pipeline.
    dest = tmp_path / "roles"
    if not dest.exists() and SOURCE_ROLES_DIR.is_dir():
        shutil.copytree(SOURCE_ROLES_DIR, dest)
    return log_file


@pytest.fixture
def cli_log(tmp_path):
    """Return a (log_path, env) tuple for hermetic CLI invocation.

    No roles staging here — the CLI inherits the package's real roles/
    directory because LOG_PATH redirects via SM_LOG_PATH, and the subprocess
    runs with cwd=PACKAGE_DIR so LOG_PATH.parent has the roles/ dir on disk.
    For tmp_path-anchored LOG_PATH the subprocess test needs roles/ too —
    mirror them in.
    """
    log_path = tmp_path / "cli_log.jsonl"
    # Stage roles/ next to the redirected log so resolve_role_spec succeeds
    # in the subprocess (which uses LOG_PATH.parent as the roles anchor).
    dest = tmp_path / "roles"
    if not dest.exists() and SOURCE_ROLES_DIR.is_dir():
        shutil.copytree(SOURCE_ROLES_DIR, dest)
    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(log_path)
    return log_path, env


def _open_iteration(iteration_id: str = "iter-1",
                    requirements=None) -> dict:
    """Append an `iteration_open` entry directly via build_entry +
    _append_entry."""
    import sm
    if requirements is None:
        requirements = [
            {"requirement_id": "req-1", "title": "T1",
             "description": "D1", "priority": "MUST",
             "acceptance_criteria": "AC1"},
        ]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": "Test iteration",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return entry


def _seed_backlog(n: int = 5) -> list:
    """Append a `story_backlog` entry with N canonical stories. Returns the
    list of minted story_ids in sequence order."""
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
    """Open iteration + seed backlog + cut the sprint.
    Returns (story_ids, in_sprint_ids, deferred_ids)."""
    import sm
    _open_iteration(iteration_id=iteration_id)
    sids = _seed_backlog(n=n_stories)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at], sids[cut_at:]


def _make_test_writer(test_code: str = "def test_x(): assert True\n",
                      record=None):
    """Build a spawn_test_writer stub that returns the given test code.

    If `record` is a dict, captures call args (role_spec_path, story) for
    inspection.
    """
    def _spawn(role_spec_path, story):
        if record is not None:
            record["test_writer_calls"] = record.get("test_writer_calls", [])
            record["test_writer_calls"].append({
                "role_spec_path": role_spec_path,
                "story": story,
            })
        return test_code
    return _spawn


def _make_coder(impl_code: str = "def foo(): return 1\n",
                record=None):
    """Build a spawn_coder stub that returns the given impl code."""
    def _spawn(role_spec_path, story, test_code):
        if record is not None:
            record["coder_calls"] = record.get("coder_calls", [])
            record["coder_calls"].append({
                "role_spec_path": role_spec_path,
                "story": story,
                "test_code": test_code,
            })
        return impl_code
    return _spawn


def _make_reviewer(approved: bool = True,
                   test_result: str = "12 of 12 passed",
                   record=None):
    """Build a spawn_reviewer stub that returns a dict with approved /
    test_result."""
    def _spawn(role_spec_path, story, test_code, impl_code):
        if record is not None:
            record["reviewer_calls"] = record.get("reviewer_calls", [])
            record["reviewer_calls"].append({
                "role_spec_path": role_spec_path,
                "story": story,
                "test_code": test_code,
                "impl_code": impl_code,
            })
        return {"approved": approved, "test_result": test_result}
    return _spawn


def _run_cli(env: dict, *args: str,
             timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sm", *args],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _assert_recognized_failure(result: subprocess.CompletedProcess) -> None:
    """Non-zero exit AND not 'unknown command'."""
    assert result.returncode != 0, (
        f"expected non-zero exit;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize the subcommand and fail with a real error, "
        f"not 'unknown command';\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def _entries_of_type(entries: list, etype: str) -> list:
    return [e for e in entries if e.get("type") == etype]


def _last_of_type(entries: list, etype: str):
    matches = _entries_of_type(entries, etype)
    return matches[-1] if matches else None


# ===========================================================================
# Smoke (8) — function exists, callable, public, in __all__, signature
# ===========================================================================


def test_execute_exists_on_module():
    """sm.execute must exist."""
    import sm
    assert hasattr(sm, "execute"), "sm.execute must exist"


def test_execute_is_callable():
    """sm.execute must be callable."""
    import sm
    assert callable(sm.execute)


def test_execute_is_public():
    """No leading underscore on the public function name."""
    import sm
    assert not sm.execute.__name__.startswith("_")
    assert sm.execute.__name__ == "execute"


def test_execute_in_dunder_all():
    """Public function exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "execute" in sm.__all__, (
        f"execute must be in __all__; got {sm.__all__!r}"
    )


def test_execute_importable_directly():
    """`from sm import execute` succeeds."""
    from sm import execute  # noqa: F401
    assert callable(execute)


def test_execute_signature_has_story_id():
    """execute accepts a `story_id` parameter."""
    import sm
    sig = inspect.signature(sm.execute)
    params = list(sig.parameters)
    assert "story_id" in params, (
        f"execute must accept story_id; got params {params!r}"
    )


def test_execute_signature_has_three_spawn_kwargs():
    """execute accepts spawn_test_writer, spawn_coder, spawn_reviewer kwargs."""
    import sm
    sig = inspect.signature(sm.execute)
    params = sig.parameters
    for name in ("spawn_test_writer", "spawn_coder", "spawn_reviewer"):
        assert name in params, (
            f"execute must accept '{name}' kwarg; got params {list(params)!r}"
        )


def test_execute_spawn_kwargs_have_defaults():
    """All three spawn kwargs have default values (so the call shape is
    `execute(story_id)` syntactically)."""
    import sm
    sig = inspect.signature(sm.execute)
    for name in ("spawn_test_writer", "spawn_coder", "spawn_reviewer"):
        p = sig.parameters[name]
        assert p.default is not inspect.Parameter.empty, (
            f"{name} must have a default (None) so execute(story_id) is "
            f"a legal call expression"
        )


# ===========================================================================
# ExecuteError typed (5) — exists, in __all__, ValueError subclass
# ===========================================================================


def test_execute_error_class_exists():
    """sm.ExecuteError must exist."""
    import sm
    assert hasattr(sm, "ExecuteError"), "sm.ExecuteError must exist"


def test_execute_error_subclasses_value_error():
    """ExecuteError subclasses ValueError so existing `except ValueError`
    callers keep working."""
    import sm
    assert issubclass(sm.ExecuteError, ValueError), (
        "ExecuteError must subclass ValueError"
    )


def test_execute_error_in_dunder_all():
    """ExecuteError is exported via __all__."""
    import sm
    assert "ExecuteError" in sm.__all__, (
        f"ExecuteError must be in __all__; got {sm.__all__!r}"
    )


def test_execute_error_is_exception_class():
    """ExecuteError is an exception (sanity)."""
    import sm
    assert isinstance(sm.ExecuteError(), Exception)


def test_execute_error_can_be_caught_as_value_error():
    """Raising and catching ExecuteError works as a ValueError."""
    import sm
    try:
        raise sm.ExecuteError("boom")
    except ValueError as e:
        assert "boom" in str(e)
    else:
        pytest.fail("ExecuteError did not behave as a ValueError")


# ===========================================================================
# Default callables -> NotImplementedError (5)
# ===========================================================================


def test_default_all_none_raises_not_implemented(isolated_log):
    """No spawn callables passed -> NotImplementedError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(NotImplementedError):
        sm.execute(in_sprint[0])


def test_default_explicit_none_raises_not_implemented(isolated_log):
    """Passing all None explicitly -> NotImplementedError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(NotImplementedError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=None,
                   spawn_coder=None,
                   spawn_reviewer=None)


def test_default_only_test_writer_missing_raises(isolated_log, monkeypatch):
    """spawn_test_writer=None (others provided) -> MissingAPIKeyError.

    Iter 2 Story 7 inverted spawn_test_writer's default: None now routes
    to the real `_default_execute_test_writer_spawn`. With no
    ANTHROPIC_API_KEY set (the default state in this isolated fixture),
    the real default raises MissingAPIKeyError instead of the old
    NotImplementedError. Behavior-preserving update: the test intent
    (default refuses silent run) is preserved; only the mechanism
    changed per the established Iter 2 cascade pattern.
    """
    import sm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=None,
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_default_only_coder_missing_raises(isolated_log, monkeypatch):
    """spawn_coder=None (others provided) -> MissingAPIKeyError.

    Iter 2 Story 8 inverted spawn_coder's default: None now routes
    to the real `_default_execute_coder_spawn`. With no
    ANTHROPIC_API_KEY set (the default state in this isolated fixture),
    the real default raises MissingAPIKeyError instead of the old
    NotImplementedError. Behavior-preserving update: the test intent
    (default refuses silent run) is preserved; only the mechanism
    changed per the established Iter 2 cascade pattern.
    """
    import sm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(sm.MissingAPIKeyError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=None,
                   spawn_reviewer=_make_reviewer())


def test_default_only_reviewer_missing_raises(isolated_log):
    """spawn_reviewer=None (others provided) -> NotImplementedError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(NotImplementedError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=None)


def test_default_error_mentions_iter_2(isolated_log):
    """The NotImplementedError message points at Iter 2."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(NotImplementedError) as exc_info:
        sm.execute(in_sprint[0])
    msg = str(exc_info.value).lower()
    assert "iter 2" in msg or "iteration 2" in msg, (
        f"NotImplementedError must mention Iter 2; got: {exc_info.value!s}"
    )


def test_default_writes_no_log_entry(isolated_log):
    """When default spawns raise, no log entry is written."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    seeded = isolated_log.read_bytes()
    with pytest.raises(NotImplementedError):
        sm.execute(in_sprint[0])
    assert isolated_log.read_bytes() == seeded


# ===========================================================================
# Validation (12) — type, state cascade
# ===========================================================================


def test_validation_non_string_story_id_type_error(isolated_log):
    """story_id non-string -> TypeError."""
    import sm
    _seed_sprint()
    with pytest.raises(TypeError):
        sm.execute(12345,
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_none_story_id_type_error(isolated_log):
    """story_id=None -> TypeError."""
    import sm
    _seed_sprint()
    with pytest.raises(TypeError):
        sm.execute(None,
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_bytes_story_id_type_error(isolated_log):
    """story_id as bytes -> TypeError."""
    import sm
    _seed_sprint()
    with pytest.raises(TypeError):
        sm.execute(b"deadbeef",
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_no_active_iteration_raises_execute_error(isolated_log):
    """Empty log -> ExecuteError."""
    import sm
    with pytest.raises(sm.ExecuteError):
        sm.execute("nope-not-a-real-id",
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_no_active_iteration_error_message(isolated_log):
    """Error message mentions 'iteration' on no-active-iter."""
    import sm
    with pytest.raises(sm.ExecuteError) as exc_info:
        sm.execute("nope",
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    assert "iteration" in str(exc_info.value).lower()


def test_validation_no_sprint_cut_raises_execute_error(isolated_log):
    """Active iteration + backlog but no sprint_cut -> ExecuteError."""
    import sm
    _open_iteration()
    sids = _seed_backlog(n=3)
    with pytest.raises(sm.ExecuteError):
        sm.execute(sids[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_story_id_not_in_sprint_raises(isolated_log):
    """Unknown story_id (not in active sprint) -> ExecuteError."""
    import sm
    _seed_sprint()
    with pytest.raises(sm.ExecuteError):
        sm.execute("ffffffffffffffffffffffffffffffff",
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_deferred_story_raises(isolated_log):
    """Deferred (out-of-sprint) story_id -> ExecuteError."""
    import sm
    _, _, deferred = _seed_sprint(n_stories=5, cut_at=3)
    with pytest.raises(sm.ExecuteError):
        sm.execute(deferred[0],
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_current_state_in_review_raises(isolated_log):
    """A story already in 'in_review' is not in {planned, in_progress} ->
    ExecuteError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.transition_story(sid, "in_progress")
    sm.transition_story(sid, "in_review")
    with pytest.raises(sm.ExecuteError):
        sm.execute(sid,
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_current_state_accepted_raises(isolated_log):
    """A story already accepted -> ExecuteError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.transition_story(sid, "in_progress")
    sm.transition_story(sid, "in_review")
    sm.record_review(sid, True, "ok")
    sm.transition_story(sid, "accepted")
    with pytest.raises(sm.ExecuteError):
        sm.execute(sid,
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_current_state_rejected_raises(isolated_log):
    """A story already rejected -> ExecuteError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.transition_story(sid, "in_progress")
    sm.transition_story(sid, "in_review")
    sm.transition_story(sid, "rejected")
    with pytest.raises(sm.ExecuteError):
        sm.execute(sid,
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())


def test_validation_error_message_names_current_state(isolated_log):
    """Error message includes the story's current (terminal/in_review)
    state when validation fails for that reason."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.transition_story(sid, "in_progress")
    sm.transition_story(sid, "in_review")
    with pytest.raises(sm.ExecuteError) as exc_info:
        sm.execute(sid,
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    assert "in_review" in str(exc_info.value), (
        f"error must name current state 'in_review'; "
        f"got: {exc_info.value!s}"
    )


# ===========================================================================
# Validation failure invariants (5)
# ===========================================================================


def test_validation_failure_does_not_call_test_writer(isolated_log):
    """Validation failure -> spawn_test_writer not called."""
    import sm
    record = {}
    tw = _make_test_writer(record=record)
    with pytest.raises((sm.ExecuteError, TypeError)):
        sm.execute("not-in-sprint",
                   spawn_test_writer=tw,
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    assert "test_writer_calls" not in record, (
        f"spawn_test_writer must not be called on validation failure; "
        f"got {record!r}"
    )


def test_validation_failure_does_not_call_coder(isolated_log):
    """Validation failure -> spawn_coder not called."""
    import sm
    record = {}
    with pytest.raises((sm.ExecuteError, TypeError)):
        sm.execute("not-in-sprint",
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(record=record),
                   spawn_reviewer=_make_reviewer())
    assert "coder_calls" not in record


def test_validation_failure_does_not_call_reviewer(isolated_log):
    """Validation failure -> spawn_reviewer not called."""
    import sm
    record = {}
    with pytest.raises((sm.ExecuteError, TypeError)):
        sm.execute("not-in-sprint",
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer(record=record))
    assert "reviewer_calls" not in record


def test_validation_failure_log_unchanged(isolated_log):
    """Validation failure -> log byte-for-byte unchanged."""
    import sm
    _seed_sprint()
    seeded = isolated_log.read_bytes()
    with pytest.raises(sm.ExecuteError):
        sm.execute("ffffffffffffffffffffffffffffffff",
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    assert isolated_log.read_bytes() == seeded


def test_validation_failure_derive_state_unchanged(isolated_log):
    """derive_state() before/after validation failure is equal."""
    import sm
    _seed_sprint()
    before = sm.derive_state()
    with pytest.raises(sm.ExecuteError):
        sm.execute("ffffffffffffffffffffffffffffffff",
                   spawn_test_writer=_make_test_writer(),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    after = sm.derive_state()
    assert before == after


# ===========================================================================
# Happy path — Reviewer approves (15)
# ===========================================================================


def test_happy_approve_returns_dict(isolated_log):
    """A successful approve-run returns a dict."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    out = sm.execute(in_sprint[0],
                     spawn_test_writer=_make_test_writer(),
                     spawn_coder=_make_coder(),
                     spawn_reviewer=_make_reviewer(approved=True,
                                                   test_result="all pass"))
    assert isinstance(out, dict)


def test_happy_approve_writes_testwriter_output_entry(isolated_log):
    """Pipeline writes a testwriter_output log entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    tw_entries = _entries_of_type(entries, "testwriter_output")
    assert len(tw_entries) >= 1, (
        f"expected at least one testwriter_output entry; "
        f"got types {[e['type'] for e in entries]!r}"
    )


def test_happy_approve_writes_coder_output_entry(isolated_log):
    """Pipeline writes a coder_output log entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    coder_entries = _entries_of_type(entries, "coder_output")
    assert len(coder_entries) >= 1, (
        f"expected at least one coder_output entry; "
        f"got types {[e['type'] for e in entries]!r}"
    )


def test_happy_approve_writes_reviewer_approval_entry(isolated_log):
    """Pipeline writes a reviewer_approval log entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="all 12 passed"))
    entries = list(sm.read_entries())
    ra_entries = _entries_of_type(entries, "reviewer_approval")
    assert len(ra_entries) >= 1, (
        f"expected at least one reviewer_approval entry; "
        f"got types {[e['type'] for e in entries]!r}"
    )


def test_happy_approve_reviewer_entry_has_approved_true(isolated_log):
    """reviewer_approval entry carries approved=True."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    ra = _last_of_type(entries, "reviewer_approval")
    assert ra is not None
    assert ra["approved"] is True


def test_happy_approve_reviewer_entry_carries_test_result(isolated_log):
    """reviewer_approval entry carries the test_result string from the
    reviewer's dict."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    msg = "12 of 12 tests passed; coverage 91%"
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result=msg))
    entries = list(sm.read_entries())
    ra = _last_of_type(entries, "reviewer_approval")
    assert ra is not None
    assert ra["test_result"] == msg


def test_happy_approve_reviewer_entry_has_story_id(isolated_log):
    """reviewer_approval entry's story_id matches the executed story."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    ra = _last_of_type(entries, "reviewer_approval")
    assert ra is not None
    assert ra["story_id"] == sid


def test_happy_approve_story_state_is_accepted(isolated_log):
    """After successful approve-run, derive_state shows the story accepted."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    state = sm.derive_state()
    assert state["story_states"][sid] == "accepted"


def test_happy_approve_other_stories_unchanged(isolated_log):
    """Executing one story does not move other stories' states."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    other = in_sprint[1]
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    state = sm.derive_state()
    assert state["story_states"][other] == "planned"


def test_happy_approve_test_writer_called_once(isolated_log):
    """spawn_test_writer is invoked exactly once."""
    import sm
    record = {}
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(record=record),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    assert len(record.get("test_writer_calls", [])) == 1


def test_happy_approve_coder_called_once(isolated_log):
    """spawn_coder is invoked exactly once."""
    import sm
    record = {}
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(record=record),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    assert len(record.get("coder_calls", [])) == 1


def test_happy_approve_reviewer_called_once(isolated_log):
    """spawn_reviewer is invoked exactly once."""
    import sm
    record = {}
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok",
                                             record=record))
    assert len(record.get("reviewer_calls", [])) == 1


def test_happy_approve_test_writer_receives_story_dict(isolated_log):
    """spawn_test_writer receives the story dict (with story_id, title,
    acceptance_criteria)."""
    import sm
    record = {}
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(record=record),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    story = record["test_writer_calls"][0]["story"]
    assert isinstance(story, dict)
    assert story.get("story_id") == sid


def test_happy_approve_coder_receives_test_code(isolated_log):
    """spawn_coder receives the test code produced by spawn_test_writer."""
    import sm
    record = {}
    test_code = "def test_special(): assert 1 == 1\n# marker"
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(test_code=test_code),
               spawn_coder=_make_coder(record=record),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    assert record["coder_calls"][0]["test_code"] == test_code


def test_happy_approve_reviewer_receives_test_and_impl(isolated_log):
    """spawn_reviewer receives both test code and impl code."""
    import sm
    record = {}
    test_code = "def test_marker(): pass\n# TC"
    impl_code = "def implementation(): return 42\n# IC"
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(test_code=test_code),
               spawn_coder=_make_coder(impl_code=impl_code),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok",
                                             record=record))
    call = record["reviewer_calls"][0]
    assert call["test_code"] == test_code
    assert call["impl_code"] == impl_code


# ===========================================================================
# Happy path — Reviewer rejects (8)
# ===========================================================================


def test_happy_reject_returns_dict(isolated_log):
    """A reject-run still returns a dict (rejected is a valid completion)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    out = sm.execute(in_sprint[0],
                     spawn_test_writer=_make_test_writer(),
                     spawn_coder=_make_coder(),
                     spawn_reviewer=_make_reviewer(approved=False,
                                                   test_result="3 fails"))
    assert isinstance(out, dict)


def test_happy_reject_writes_reviewer_approval_entry(isolated_log):
    """Pipeline writes a reviewer_approval entry with approved=False."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=False,
                                             test_result="3 fails"))
    entries = list(sm.read_entries())
    ra = _last_of_type(entries, "reviewer_approval")
    assert ra is not None
    assert ra["approved"] is False


def test_happy_reject_test_result_preserved(isolated_log):
    """reviewer_approval entry preserves the rejection's test_result."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    msg = "smoke regressed: 3 of 12 fails — see details"
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=False,
                                             test_result=msg))
    entries = list(sm.read_entries())
    ra = _last_of_type(entries, "reviewer_approval")
    assert ra["test_result"] == msg


def test_happy_reject_story_state_is_rejected(isolated_log):
    """After reject-run, derive_state shows the story rejected."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=False,
                                             test_result="3 fails"))
    state = sm.derive_state()
    assert state["story_states"][sid] == "rejected"


def test_happy_reject_writes_testwriter_and_coder_entries(isolated_log):
    """Reject-run still produces full testwriter_output + coder_output
    entries (the pipeline ran end-to-end)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=False,
                                             test_result="3 fails"))
    entries = list(sm.read_entries())
    assert _last_of_type(entries, "testwriter_output") is not None
    assert _last_of_type(entries, "coder_output") is not None


def test_happy_reject_empty_test_result_routes_to_rejected(isolated_log):
    """approved=True with empty test_result still routes to rejected (the
    accept gate requires non-empty test_result)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result=""))
    state = sm.derive_state()
    assert state["story_states"][sid] == "rejected"


def test_happy_reject_whitespace_test_result_routes_to_rejected(isolated_log):
    """approved=True with whitespace-only test_result routes to rejected."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="   \t\n   "))
    state = sm.derive_state()
    assert state["story_states"][sid] == "rejected"


def test_happy_reject_other_stories_unchanged(isolated_log):
    """Reject-run does not move other stories' states."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    other = in_sprint[1]
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=False,
                                             test_result="3 fails"))
    state = sm.derive_state()
    assert state["story_states"][other] == "planned"


# ===========================================================================
# Role-spec wiring (6)
# ===========================================================================


def test_role_spec_testwriter_receives_test_writer_md(isolated_log):
    """spawn_test_writer's first arg references roles/test_writer.md."""
    import sm
    record = {}
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(record=record),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    rsp = record["test_writer_calls"][0]["role_spec_path"]
    assert isinstance(rsp, str)
    # Path separators differ across platforms — use os.sep agnostic check.
    norm = rsp.replace("\\", "/").lower()
    assert "roles/test_writer.md" in norm, (
        f"test_writer role_spec_path must end at roles/test_writer.md; "
        f"got {rsp!r}"
    )


def test_role_spec_coder_receives_coder_md(isolated_log):
    """spawn_coder's first arg references roles/coder.md."""
    import sm
    record = {}
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(record=record),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    rsp = record["coder_calls"][0]["role_spec_path"]
    norm = rsp.replace("\\", "/").lower()
    assert "roles/coder.md" in norm, (
        f"coder role_spec_path must end at roles/coder.md; got {rsp!r}"
    )


def test_role_spec_reviewer_receives_reviewer_md(isolated_log):
    """spawn_reviewer's first arg references roles/reviewer.md."""
    import sm
    record = {}
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok",
                                             record=record))
    rsp = record["reviewer_calls"][0]["role_spec_path"]
    norm = rsp.replace("\\", "/").lower()
    assert "roles/reviewer.md" in norm, (
        f"reviewer role_spec_path must end at roles/reviewer.md; "
        f"got {rsp!r}"
    )


def test_role_spec_hash_captured_in_testwriter_output(isolated_log):
    """testwriter_output entry carries role_spec_hash (hex)."""
    import sm
    import re
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    tw = _last_of_type(entries, "testwriter_output")
    assert tw is not None
    assert "role_spec_hash" in tw
    assert isinstance(tw["role_spec_hash"], str)
    assert re.fullmatch(r"[0-9a-f]+", tw["role_spec_hash"]), (
        f"role_spec_hash must be a hex digest; got {tw['role_spec_hash']!r}"
    )


def test_role_spec_hash_captured_in_coder_output(isolated_log):
    """coder_output entry carries role_spec_hash."""
    import sm
    import re
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    coder = _last_of_type(entries, "coder_output")
    assert coder is not None
    assert "role_spec_hash" in coder
    assert isinstance(coder["role_spec_hash"], str)
    assert re.fullmatch(r"[0-9a-f]+", coder["role_spec_hash"])


def test_role_spec_path_captured_in_testwriter_output(isolated_log):
    """testwriter_output entry carries role_spec_path string."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    tw = _last_of_type(entries, "testwriter_output")
    assert tw is not None
    assert "role_spec_path" in tw
    assert isinstance(tw["role_spec_path"], str)
    norm = tw["role_spec_path"].replace("\\", "/").lower()
    assert "roles/test_writer.md" in norm


def test_role_spec_path_captured_in_coder_output(isolated_log):
    """coder_output entry carries role_spec_path string pointing at coder.md."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    coder = _last_of_type(entries, "coder_output")
    assert coder is not None
    assert "role_spec_path" in coder
    norm = coder["role_spec_path"].replace("\\", "/").lower()
    assert "roles/coder.md" in norm


# ===========================================================================
# Story state progression (6)
# ===========================================================================


def test_progression_planned_to_in_progress_first(isolated_log):
    """First state_change in the pipeline is planned -> in_progress."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    before = list(sm.read_entries())
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    after = list(sm.read_entries())
    new_entries = after[len(before):]
    state_changes = [
        e for e in new_entries
        if e.get("type") == "story_state_change"
        and e.get("story_id") == sid
    ]
    assert len(state_changes) >= 1
    first = state_changes[0]
    assert first["from_state"] == "planned"
    assert first["to_state"] == "in_progress"


def test_progression_in_progress_to_in_review(isolated_log):
    """A state_change of in_progress -> in_review is written."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    state_changes = [
        e for e in entries
        if e.get("type") == "story_state_change"
        and e.get("story_id") == sid
        and e.get("from_state") == "in_progress"
        and e.get("to_state") == "in_review"
    ]
    assert len(state_changes) >= 1, (
        f"expected at least one in_progress -> in_review state_change for "
        f"{sid!r}"
    )


def test_progression_in_review_to_accepted_on_approve(isolated_log):
    """Final state_change on approve-run is in_review -> accepted."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    state_changes = [
        e for e in entries
        if e.get("type") == "story_state_change"
        and e.get("story_id") == sid
    ]
    last = state_changes[-1]
    assert last["from_state"] == "in_review"
    assert last["to_state"] == "accepted"


def test_progression_in_review_to_rejected_on_reject(isolated_log):
    """Final state_change on reject-run is in_review -> rejected."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=False,
                                             test_result="3 fails"))
    entries = list(sm.read_entries())
    state_changes = [
        e for e in entries
        if e.get("type") == "story_state_change"
        and e.get("story_id") == sid
    ]
    last = state_changes[-1]
    assert last["from_state"] == "in_review"
    assert last["to_state"] == "rejected"


def test_progression_log_entry_order_on_approve(isolated_log):
    """Entry order on approve-run:
    state_change(planned->in_progress), testwriter_output, coder_output,
    state_change(in_progress->in_review), reviewer_approval,
    state_change(in_review->accepted).
    Test pins relative ordering of types — not strict adjacency, since the
    implementor may interleave fields."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    before_count = len(list(sm.read_entries()))
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())[before_count:]
    types = [e["type"] for e in entries]

    def _idx(t):
        return types.index(t)

    assert _idx("testwriter_output") < _idx("coder_output")
    assert _idx("coder_output") < _idx("reviewer_approval")
    # reviewer_approval precedes the final state_change to accepted
    ra_idx = _idx("reviewer_approval")
    # find the LAST state_change index (the accept transition)
    sc_indices = [i for i, t in enumerate(types) if t == "story_state_change"]
    assert sc_indices, "expected at least one story_state_change"
    last_sc_idx = sc_indices[-1]
    assert ra_idx < last_sc_idx, (
        f"reviewer_approval must precede the final story_state_change; "
        f"got types {types!r}"
    )


def test_progression_starting_from_in_progress_skips_first_transition(
        isolated_log):
    """If story is already in_progress, execute does NOT write a redundant
    planned->in_progress transition (it's already there)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.transition_story(sid, "in_progress")
    before = list(sm.read_entries())
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    after = list(sm.read_entries())
    new_entries = after[len(before):]
    # No new entry should be a from_state=planned transition for this story
    bad = [
        e for e in new_entries
        if e.get("type") == "story_state_change"
        and e.get("story_id") == sid
        and e.get("from_state") == "planned"
    ]
    assert not bad, (
        f"execute on an already-in_progress story must not re-write "
        f"planned->in_progress; got {bad!r}"
    )


# ===========================================================================
# Sequential spawn order (4)
# ===========================================================================


def test_sequential_coder_after_test_writer(isolated_log):
    """spawn_coder fires only after spawn_test_writer has returned."""
    import sm
    timestamps = []

    def tw(role_spec_path, story):
        timestamps.append(("test_writer_called",))
        return "TEST_CODE"

    def coder(role_spec_path, story, test_code):
        timestamps.append(("coder_called", test_code))
        # If coder fired before test_writer returned, test_code wouldn't
        # equal what test_writer returned.
        return "IMPL_CODE"

    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=tw,
               spawn_coder=coder,
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    # test_writer must be called before coder
    tw_idx = next(i for i, t in enumerate(timestamps)
                  if t[0] == "test_writer_called")
    coder_idx = next(i for i, t in enumerate(timestamps)
                     if t[0] == "coder_called")
    assert tw_idx < coder_idx
    # coder must have received the test_writer's output
    assert timestamps[coder_idx][1] == "TEST_CODE"


def test_sequential_reviewer_after_coder(isolated_log):
    """spawn_reviewer fires only after spawn_coder has returned."""
    import sm
    timestamps = []

    def tw(role_spec_path, story):
        timestamps.append(("test_writer_called",))
        return "TC"

    def coder(role_spec_path, story, test_code):
        timestamps.append(("coder_called",))
        return "IC"

    def reviewer(role_spec_path, story, test_code, impl_code):
        timestamps.append(("reviewer_called", test_code, impl_code))
        return {"approved": True, "test_result": "ok"}

    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=tw,
               spawn_coder=coder,
               spawn_reviewer=reviewer)
    coder_idx = next(i for i, t in enumerate(timestamps)
                     if t[0] == "coder_called")
    reviewer_idx = next(i for i, t in enumerate(timestamps)
                        if t[0] == "reviewer_called")
    assert coder_idx < reviewer_idx
    # reviewer must have received coder's output
    assert timestamps[reviewer_idx][1] == "TC"
    assert timestamps[reviewer_idx][2] == "IC"


def test_sequential_full_call_order(isolated_log):
    """Full sequential order: test_writer, then coder, then reviewer."""
    import sm
    order = []

    def tw(role_spec_path, story):
        order.append("tw")
        return "T"

    def coder(role_spec_path, story, test_code):
        order.append("coder")
        return "I"

    def reviewer(role_spec_path, story, test_code, impl_code):
        order.append("reviewer")
        return {"approved": True, "test_result": "ok"}

    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=tw,
               spawn_coder=coder,
               spawn_reviewer=reviewer)
    assert order == ["tw", "coder", "reviewer"], (
        f"expected sequential call order [tw, coder, reviewer]; got {order!r}"
    )


def test_sequential_test_code_threaded_through(isolated_log):
    """The test_code returned by test_writer reaches the reviewer."""
    import sm
    record = {}
    canonical_test_code = "TEST_CODE_FROM_TEST_WRITER_v1\n"
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(
                   test_code=canonical_test_code),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok",
                                             record=record))
    assert record["reviewer_calls"][0]["test_code"] == canonical_test_code


# ===========================================================================
# CLI (8) — subcommand recognized, exit codes, default raises
# ===========================================================================


def test_cli_subcommand_recognized_on_approve(cli_log):
    """`python -m sm execute <id>` is recognized (not 'unknown command')
    even though default callables raise NotImplementedError — the
    recognition test runs in the default-spawn lane."""
    log_path, env = cli_log
    # No data set up. Default spawns will raise NotImplementedError because
    # there's no active iteration to even get to the pipeline.
    result = _run_cli(env, "execute", "deadbeef")
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'execute' subcommand; got combined={combined!r}"
    )


def test_cli_default_spawn_non_zero_exit(cli_log):
    """Default (no operator-injected callables) -> non-zero exit (because
    real-agent integration is Iter 2 only)."""
    log_path, env = cli_log
    # Build a sprint so we get past validation and reach the spawn.
    import sm
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration()
        sids = _seed_backlog(n=3)
        sm.sprint_cut(2)
    finally:
        sm.LOG_PATH = orig_log

    result = _run_cli(env, "execute", sids[0])
    assert result.returncode != 0, (
        f"default-spawn CLI run must exit non-zero;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined


def test_cli_validation_failure_non_zero(cli_log):
    """Validation failure (e.g. story_id not in sprint) -> non-zero exit."""
    log_path, env = cli_log
    result = _run_cli(env, "execute", "definitely-not-a-real-story-id")
    _assert_recognized_failure(result)


def test_cli_missing_positional_arg_non_zero(cli_log):
    """No story_id supplied -> non-zero exit, subcommand recognized."""
    log_path, env = cli_log
    result = _run_cli(env, "execute")
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    # Even on missing-arg path, the CLI must recognize 'execute' — not
    # fall through to 'unknown command'.
    assert "unknown command" not in combined, (
        f"CLI must recognize 'execute' even on missing-arg path;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_help_flag_exits_zero(cli_log):
    """`python -m sm execute --help` exits 0."""
    log_path, env = cli_log
    result = _run_cli(env, "execute", "--help")
    # --help is generic across the suite; tolerate either 0 or "help text"
    # exit. Suite convention: --help returns 0 across other subcommands.
    assert result.returncode == 0, (
        f"--help should exit 0; got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_execute_h_short_flag_exits_zero(cli_log):
    """`python -m sm execute -h` exits 0."""
    log_path, env = cli_log
    result = _run_cli(env, "execute", "-h")
    assert result.returncode == 0


def test_cli_unknown_story_id_log_unchanged(cli_log):
    """CLI validation failure does not modify the log; subcommand
    recognized."""
    log_path, env = cli_log
    # Seed a valid sprint so the log isn't empty.
    import sm
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration()
        _seed_backlog(n=3)
        sm.sprint_cut(2)
    finally:
        sm.LOG_PATH = orig_log
    seeded = log_path.read_bytes()

    result = _run_cli(env, "execute", "ffffffffffffffffffffffffffffffff")
    _assert_recognized_failure(result)
    # Log untouched by the validation-failure exit.
    assert log_path.read_bytes() == seeded


def test_cli_no_active_iteration_non_zero(cli_log):
    """CLI run on empty log (no active iter) -> non-zero exit."""
    log_path, env = cli_log
    result = _run_cli(env, "execute", "ffffffffffffffffffffffffffffffff")
    _assert_recognized_failure(result)


# ===========================================================================
# Post-spawn validation invariants (5)
# ===========================================================================


def test_post_spawn_approve_log_has_six_new_entries(isolated_log):
    """A successful approve-run appends 6 new entries:
    state_change(planned->in_progress), testwriter_output, coder_output,
    state_change(in_progress->in_review), reviewer_approval,
    state_change(in_review->accepted)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    before = list(sm.read_entries())
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    after = list(sm.read_entries())
    new_count = len(after) - len(before)
    assert new_count == 6, (
        f"approve-run must append exactly 6 entries; got {new_count}\n"
        f"new entry types: {[e['type'] for e in after[len(before):]]!r}"
    )


def test_post_spawn_reject_log_has_six_new_entries(isolated_log):
    """A reject-run appends 6 new entries (same as approve)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    before = list(sm.read_entries())
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=False,
                                             test_result="3 fails"))
    after = list(sm.read_entries())
    new_count = len(after) - len(before)
    assert new_count == 6, (
        f"reject-run must append exactly 6 entries; got {new_count}\n"
        f"new entry types: {[e['type'] for e in after[len(before):]]!r}"
    )


def test_post_spawn_already_in_progress_appends_five(isolated_log):
    """If the story is already in_progress, execute writes 5 new entries
    (skipping the first state_change)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.transition_story(sid, "in_progress")
    before = list(sm.read_entries())
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    after = list(sm.read_entries())
    new_count = len(after) - len(before)
    assert new_count == 5, (
        f"in_progress-start approve-run must append exactly 5 entries; "
        f"got {new_count}\n"
        f"new entry types: {[e['type'] for e in after[len(before):]]!r}"
    )


def test_post_spawn_testwriter_output_has_story_id(isolated_log):
    """testwriter_output entry carries the story_id field."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    tw = _last_of_type(entries, "testwriter_output")
    assert tw is not None
    assert tw.get("story_id") == sid


def test_post_spawn_coder_output_has_story_id(isolated_log):
    """coder_output entry carries the story_id field."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    coder = _last_of_type(entries, "coder_output")
    assert coder is not None
    assert coder.get("story_id") == sid


def test_post_spawn_canonical_fields_on_testwriter_output(isolated_log):
    """testwriter_output entry has id, type, timestamp from build_entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    tw = _last_of_type(entries, "testwriter_output")
    assert tw is not None
    assert "id" in tw and isinstance(tw["id"], str) and tw["id"]
    assert "type" in tw and tw["type"] == "testwriter_output"
    assert "timestamp" in tw and isinstance(tw["timestamp"], str)


def test_post_spawn_canonical_fields_on_coder_output(isolated_log):
    """coder_output entry has id, type, timestamp from build_entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    entries = list(sm.read_entries())
    coder = _last_of_type(entries, "coder_output")
    assert coder is not None
    assert "id" in coder and isinstance(coder["id"], str) and coder["id"]
    assert "type" in coder and coder["type"] == "coder_output"
    assert "timestamp" in coder and isinstance(coder["timestamp"], str)


# ===========================================================================
# Multi-story isolation (3) — execute on one story doesn't disturb others
# ===========================================================================


def test_isolation_other_stories_in_planned(isolated_log):
    """Stories not targeted by execute stay in 'planned'."""
    import sm
    _, in_sprint, _ = _seed_sprint(n_stories=5, cut_at=3)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    state = sm.derive_state()
    # The other 2 in-sprint stories
    for sid in in_sprint[1:]:
        assert state["story_states"][sid] == "planned", (
            f"non-target story {sid!r} must stay planned; "
            f"got {state['story_states'][sid]!r}"
        )


def test_isolation_deferred_stories_in_planned(isolated_log):
    """Deferred stories stay in 'planned' too."""
    import sm
    _, in_sprint, deferred = _seed_sprint(n_stories=5, cut_at=3)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    state = sm.derive_state()
    for sid in deferred:
        assert state["story_states"][sid] == "planned"


def test_isolation_two_executes_in_one_sprint(isolated_log):
    """Two consecutive execute calls on different stories: each moves only
    its own story."""
    import sm
    _, in_sprint, _ = _seed_sprint(n_stories=5, cut_at=3)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=True,
                                             test_result="ok"))
    sm.execute(in_sprint[1],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(approved=False,
                                             test_result="3 fails"))
    state = sm.derive_state()
    assert state["story_states"][in_sprint[0]] == "accepted"
    assert state["story_states"][in_sprint[1]] == "rejected"
    assert state["story_states"][in_sprint[2]] == "planned"
