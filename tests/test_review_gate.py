"""Story 15 — pin the contract of `record_review` + the accept-time test-pass gate.

Story 15 (Sprint 2, size M) extends Story 14's `accept` command. It ties
acceptance to a reviewer-supplied test result by introducing a new public
function and CLI subcommand:

    record_review(story_id: str, approved: bool, test_result: str) -> dict
    python -m sm record-review <story_id> --approved <true|false> \\
        --test-result "<text>"

What this file pins:

  - Function signature and shape:
      `record_review(story_id, approved, test_result)` — PUBLIC, callable,
      in `sm.__all__`, importable as `from sm import record_review`. Returns
      the appended `reviewer_approval` log entry.

  - `reviewer_approval` entry shape:
        {
          "id", "type": "reviewer_approval", "timestamp" (auto-stamped),
          "story_id":    "<uuid hex>",
          "approved":    true | false,
          "test_result": "<non-empty-after-strip string>"
        }

  - Validation in `record_review`:
      * story_id non-string -> TypeError
      * approved non-bool   -> TypeError (int 1 is NOT bool here)
      * test_result non-str -> TypeError
      * empty / whitespace-only test_result -> ReviewError
      * story_id must reference a story in the active sprint
        (recommended; pinned here as the same StoryTransitionError-style
        shape - we surface it through ReviewError so callers can branch).

  - Typed exceptions:
      * `ReviewError(ValueError)` — record-review failures.
      * `AcceptGateError(StoryTransitionError)` — accept fired without a
        valid prior `reviewer_approval`.

  - `accept` gate (Story 14 extension, lifecycle-level):
      * `transition_story(story_id, "accepted")` MUST find at least one
        `reviewer_approval` entry for that story_id with
        `approved is True` AND non-empty `test_result` (after strip).
      * No matching entry -> AcceptGateError; log unchanged.
      * Latest reviewer_approval per story_id wins on replay (false-then-
        true succeeds; true-then-false fails).
      * `reject` / `start` / `submit` are NOT gated.

  - CLI subcommand `record-review`:
      * Recognized (not "unknown command").
      * Writes a `reviewer_approval` entry on success.
      * Validation failure -> non-zero exit, log unchanged.

  - Failure invariant: log.jsonl is byte-for-byte unchanged on every
    validation/argument failure.

  - Cross-story isolation: an approval for story A does NOT satisfy the
    accept gate for story B.

Tests must FAIL on first run — `record_review`, `ReviewError`,
`AcceptGateError`, the gate inside `transition_story`, and the
`record-review` CLI subcommand do not exist yet. The Coder downstream
implements them to satisfy these tests.
"""

from __future__ import annotations

import inspect
import json
import os
import pathlib
import subprocess
import sys
import uuid as _uuid

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers — mirror the in-process pattern from
# test_transition_story.py and the subprocess pattern from
# test_lifecycle_commands.py.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file. Mirrors suite
    convention from test_transition_story.py."""
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


@pytest.fixture
def cli_log(tmp_path):
    """Return a (log_path, env) tuple for hermetic CLI invocation. Mirrors
    test_lifecycle_commands.py `cli_log`."""
    log_path = tmp_path / "cli_log.jsonl"
    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(log_path)
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
    """Append a `story_backlog` entry with N canonical stories. Returns
    the list of minted story_ids (in sequence order)."""
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
            "acceptance_criteria": f"Story {i} must pass.",
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
    """Convenience: open iteration + seed backlog + cut the sprint.
    Returns (story_ids, in_sprint_ids, deferred_ids)."""
    import sm
    _open_iteration(iteration_id=iteration_id)
    sids = _seed_backlog(n=n_stories)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at], sids[cut_at:]


def _drive_to_in_review(story_id: str) -> None:
    """Move a planned story through start -> submit so it sits in in_review,
    ready for the accept-gate tests."""
    import sm
    sm.transition_story(story_id, "in_progress")
    sm.transition_story(story_id, "in_review")


# --- subprocess helpers (Story 14 pattern) ---


def _open_iteration_at(log_path: pathlib.Path,
                       iteration_id: str = "iter-1",
                       requirements=None) -> dict:
    import sm
    if requirements is None:
        requirements = [
            {"requirement_id": "req-1", "title": "T1",
             "description": "D1", "priority": "MUST",
             "acceptance_criteria": "AC1"},
        ]
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        entry = sm.build_entry("iteration_open", {
            "iteration_id": iteration_id,
            "iteration_goal": "Test iteration",
            "requirements": list(requirements),
        })
        sm._append_entry(entry)
        return entry
    finally:
        sm.LOG_PATH = orig_log


def _seed_backlog_at(log_path: pathlib.Path, n: int = 5) -> list:
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
            "acceptance_criteria": f"Story {i} must pass.",
        })
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        entry = sm.build_entry("story_backlog", {
            "stories": stories,
            "role_spec_path": "<test-stub>",
            "role_spec_hash": "<test-stub>",
        })
        sm._append_entry(entry)
    finally:
        sm.LOG_PATH = orig_log
    return story_ids


def _seed_sprint_at(log_path: pathlib.Path,
                    n_stories: int = 5,
                    cut_at: int = 3,
                    iteration_id: str = "iter-1") -> tuple:
    import sm
    _open_iteration_at(log_path, iteration_id=iteration_id)
    sids = _seed_backlog_at(log_path, n=n_stories)
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        sm.sprint_cut(cut_at)
    finally:
        sm.LOG_PATH = orig_log
    return sids, sids[:cut_at], sids[cut_at:]


def _advance_at(log_path: pathlib.Path, story_id: str,
                *target_states: str) -> None:
    import sm
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        for to_state in target_states:
            sm.transition_story(story_id, to_state)
    finally:
        sm.LOG_PATH = orig_log


def _record_review_at(log_path: pathlib.Path,
                      story_id: str,
                      approved: bool,
                      test_result: str) -> dict:
    """Direct in-process call to `sm.record_review` against a specific log
    file (for staging gate-test fixtures via the public API itself, mirroring
    how `_advance` uses transition_story in test_lifecycle_commands.py)."""
    import sm
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        return sm.record_review(story_id, approved, test_result)
    finally:
        sm.LOG_PATH = orig_log


def _append_raw_entry(log_path: pathlib.Path, entry: dict) -> None:
    """Direct append bypassing record_review — used to plant a synthetic
    reviewer_approval whose test_result is whitespace-only (impossible via
    the public API, since record_review rejects it). Tests the gate's
    reading discipline, not the writer's."""
    import sm
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        sm._append_entry(entry)
    finally:
        sm.LOG_PATH = orig_log


def _derive_state_at(log_path: pathlib.Path) -> dict:
    import sm
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        return sm.derive_state()
    finally:
        sm.LOG_PATH = orig_log


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
    """Non-zero exit AND not 'unknown command'. Mirrors the test_lifecycle
    helper of the same name."""
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


# ===========================================================================
# Smoke (8) — record_review + ReviewError + AcceptGateError exist & public
# ===========================================================================


def test_record_review_exists_on_module():
    """sm.record_review must exist."""
    import sm
    assert hasattr(sm, "record_review"), "sm.record_review must exist"


def test_record_review_is_callable():
    """sm.record_review must be callable."""
    import sm
    assert callable(sm.record_review)


def test_record_review_is_public():
    """No leading underscore on the public function name."""
    import sm
    assert not sm.record_review.__name__.startswith("_")
    assert sm.record_review.__name__ == "record_review"


def test_record_review_in_dunder_all():
    """Public function exported via __all__."""
    import sm
    assert "record_review" in sm.__all__, (
        f"record_review must be in __all__; got {sm.__all__!r}"
    )


def test_record_review_importable_directly():
    """`from sm import record_review` succeeds."""
    from sm import record_review  # noqa: F401
    assert callable(record_review)


def test_record_review_signature_three_params():
    """record_review takes (story_id, approved, test_result)."""
    import sm
    sig = inspect.signature(sm.record_review)
    params = list(sig.parameters)
    assert len(params) >= 3, (
        f"record_review must accept at least three parameters; "
        f"got params {params!r}"
    )


def test_review_error_class_exists_and_is_value_error():
    """ReviewError is exposed and subclasses ValueError so existing
    `except ValueError` callers keep working."""
    import sm
    assert hasattr(sm, "ReviewError"), "sm.ReviewError must exist"
    assert issubclass(sm.ReviewError, ValueError), (
        "ReviewError must subclass ValueError"
    )


def test_accept_gate_error_class_exists_and_is_transition_error():
    """AcceptGateError is exposed and subclasses StoryTransitionError so
    existing transition-error catches still match it."""
    import sm
    assert hasattr(sm, "AcceptGateError"), "sm.AcceptGateError must exist"
    assert issubclass(sm.AcceptGateError, sm.StoryTransitionError), (
        "AcceptGateError must subclass StoryTransitionError"
    )


# ===========================================================================
# record_review happy path (10)
# ===========================================================================


def test_record_review_returns_dict(isolated_log):
    """A successful record_review returns a dict (the appended entry)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    out = sm.record_review(target, True, "all 12 tests passed")
    assert isinstance(out, dict), (
        f"record_review must return a dict; got {type(out).__name__}"
    )


def test_record_review_returned_entry_type_is_reviewer_approval(isolated_log):
    """Returned entry has type 'reviewer_approval'."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    out = sm.record_review(in_sprint[0], True, "ok")
    assert out["type"] == "reviewer_approval", (
        f"entry type must be 'reviewer_approval'; got {out['type']!r}"
    )


def test_record_review_returned_entry_has_id_and_timestamp(isolated_log):
    """Returned entry carries auto-stamped id and timestamp."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    out = sm.record_review(in_sprint[0], True, "ok")
    assert "id" in out and isinstance(out["id"], str) and out["id"]
    assert "timestamp" in out and isinstance(out["timestamp"], str)


def test_record_review_returned_entry_preserves_story_id(isolated_log):
    """story_id field on the entry equals the argument."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    out = sm.record_review(target, True, "ok")
    assert out["story_id"] == target


def test_record_review_returned_entry_preserves_approved_true(isolated_log):
    """approved field equals the argument (True case)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    out = sm.record_review(in_sprint[0], True, "ok")
    assert out["approved"] is True


def test_record_review_returned_entry_preserves_approved_false(isolated_log):
    """approved field equals the argument (False case)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    out = sm.record_review(in_sprint[0], False, "smoke regressed: 3 fails")
    assert out["approved"] is False


def test_record_review_returned_entry_preserves_test_result(isolated_log):
    """test_result field equals the argument verbatim."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    msg = "12 of 12 passed; coverage 87%"
    out = sm.record_review(in_sprint[0], True, msg)
    assert out["test_result"] == msg


def test_record_review_appends_one_log_entry(isolated_log):
    """A successful record_review appends exactly one new log line."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    before = isolated_log.read_bytes()
    sm.record_review(in_sprint[0], True, "ok")
    new_bytes = isolated_log.read_bytes()[len(before):]
    new_lines = [
        ln for ln in new_bytes.decode("utf-8").splitlines() if ln.strip()
    ]
    assert len(new_lines) == 1, (
        f"record_review must append exactly one line; got {len(new_lines)}"
    )


def test_record_review_persisted_entry_matches_returned(isolated_log):
    """The appended log entry is the dict returned by the call."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    before = isolated_log.read_bytes()
    out = sm.record_review(in_sprint[0], True, "ok")
    new_lines = [
        ln for ln in
        isolated_log.read_bytes()[len(before):].decode("utf-8").splitlines()
        if ln.strip()
    ]
    persisted = json.loads(new_lines[-1])
    assert persisted["id"] == out["id"]
    assert persisted["type"] == "reviewer_approval"
    assert persisted["story_id"] == out["story_id"]
    assert persisted["approved"] is out["approved"]
    assert persisted["test_result"] == out["test_result"]


def test_record_review_allows_leading_trailing_whitespace_with_content(
        isolated_log):
    """test_result with surrounding whitespace but non-empty after strip
    is accepted — only fully-whitespace strings are rejected."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    msg = "   tests passed   "
    out = sm.record_review(in_sprint[0], True, msg)
    # Implementation may preserve or strip — both are reasonable. Pin
    # only that the call SUCCEEDS, since "leading/trailing whitespace
    # allowed if there's content" is the spec rule.
    assert out["type"] == "reviewer_approval"
    assert out["test_result"].strip() == "tests passed"


# ===========================================================================
# record_review validation (12) — type/value rejections
# ===========================================================================


def test_record_review_non_string_story_id_raises_type_error(isolated_log):
    """story_id must be str; non-string -> TypeError."""
    import sm
    _seed_sprint()
    with pytest.raises(TypeError):
        sm.record_review(123, True, "ok")


def test_record_review_none_story_id_raises_type_error(isolated_log):
    """story_id None -> TypeError."""
    import sm
    _seed_sprint()
    with pytest.raises(TypeError):
        sm.record_review(None, True, "ok")


def test_record_review_list_story_id_raises_type_error(isolated_log):
    """story_id list -> TypeError."""
    import sm
    _seed_sprint()
    with pytest.raises(TypeError):
        sm.record_review(["abc"], True, "ok")


def test_record_review_int_approved_raises_type_error(isolated_log):
    """approved=1 (int, not bool) -> TypeError. Spec: isinstance(x, bool)
    explicitly — int subclass relationship doesn't satisfy the gate."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(TypeError):
        sm.record_review(in_sprint[0], 1, "ok")


def test_record_review_zero_approved_raises_type_error(isolated_log):
    """approved=0 (int, not bool) -> TypeError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(TypeError):
        sm.record_review(in_sprint[0], 0, "ok")


def test_record_review_string_approved_raises_type_error(isolated_log):
    """approved='true' (string) -> TypeError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(TypeError):
        sm.record_review(in_sprint[0], "true", "ok")


def test_record_review_none_approved_raises_type_error(isolated_log):
    """approved=None -> TypeError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(TypeError):
        sm.record_review(in_sprint[0], None, "ok")


def test_record_review_non_string_test_result_raises_type_error(
        isolated_log):
    """test_result must be str; int -> TypeError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(TypeError):
        sm.record_review(in_sprint[0], True, 12)


def test_record_review_none_test_result_raises_type_error(isolated_log):
    """test_result None -> TypeError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(TypeError):
        sm.record_review(in_sprint[0], True, None)


def test_record_review_empty_test_result_raises_review_error(isolated_log):
    """test_result '' -> ReviewError (semantic, not type)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(sm.ReviewError):
        sm.record_review(in_sprint[0], True, "")


def test_record_review_whitespace_only_test_result_raises_review_error(
        isolated_log):
    """test_result '   ' (spaces only) -> ReviewError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(sm.ReviewError):
        sm.record_review(in_sprint[0], True, "   ")


def test_record_review_tab_newline_test_result_raises_review_error(
        isolated_log):
    """test_result '\\t\\n  \\r' (mixed whitespace) -> ReviewError."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    with pytest.raises(sm.ReviewError):
        sm.record_review(in_sprint[0], True, "\t\n  \r")


# ===========================================================================
# record_review failure invariants (4) — log unchanged on every failure
# ===========================================================================


def test_record_review_type_error_log_unchanged(isolated_log):
    """A TypeError on bad approved leaves the log byte-for-byte unchanged."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.record_review(in_sprint[0], 1, "ok")
    assert isolated_log.read_bytes() == bytes_before


def test_record_review_empty_test_result_log_unchanged(isolated_log):
    """A ReviewError on empty test_result leaves the log unchanged."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.ReviewError):
        sm.record_review(in_sprint[0], True, "")
    assert isolated_log.read_bytes() == bytes_before


def test_record_review_whitespace_test_result_log_unchanged(isolated_log):
    """A ReviewError on whitespace-only test_result leaves the log
    unchanged."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.ReviewError):
        sm.record_review(in_sprint[0], True, "  \t\n  ")
    assert isolated_log.read_bytes() == bytes_before


def test_record_review_non_string_story_id_log_unchanged(isolated_log):
    """A TypeError on non-string story_id leaves the log unchanged."""
    import sm
    _seed_sprint()
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.record_review(42, True, "ok")
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# accept gate happy path (5) — record_review then accept succeeds
# ===========================================================================


def test_accept_after_approval_succeeds(isolated_log):
    """A reviewer_approval with approved=True and non-empty test_result
    satisfies the gate; transition_story(... 'accepted') succeeds."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, True, "all tests pass")
    out = sm.transition_story(target, "accepted")
    assert out["to_state"] == "accepted"


def test_accept_after_approval_updates_story_state(isolated_log):
    """After approval + accept, derive_state shows the story accepted."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, True, "tests pass")
    sm.transition_story(target, "accepted")
    state = sm.derive_state()
    assert state["story_states"][target] == "accepted"


def test_accept_after_approval_writes_one_state_change(isolated_log):
    """Accept after approval writes exactly one new story_state_change."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, True, "ok")
    before = isolated_log.read_bytes()
    sm.transition_story(target, "accepted")
    new_bytes = isolated_log.read_bytes()[len(before):]
    new_lines = [
        ln for ln in new_bytes.decode("utf-8").splitlines() if ln.strip()
    ]
    state_changes = [
        json.loads(ln) for ln in new_lines
        if json.loads(ln).get("type") == "story_state_change"
    ]
    assert len(state_changes) == 1
    assert state_changes[0]["to_state"] == "accepted"


def test_full_chain_start_submit_review_accept(isolated_log):
    """End-to-end happy chain: start -> submit -> record_review -> accept,
    all in-process, all green."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    sm.record_review(target, True, "all 17 tests pass")
    sm.transition_story(target, "accepted")
    state = sm.derive_state()
    assert state["story_states"][target] == "accepted"


def test_accept_with_minimal_non_empty_test_result(isolated_log):
    """Even a single character passes the test_result gate (non-empty
    after strip is the rule, not 'meaningful prose')."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, True, "x")
    sm.transition_story(target, "accepted")
    state = sm.derive_state()
    assert state["story_states"][target] == "accepted"


# ===========================================================================
# accept gate failures (10) — accept blocked + log unchanged
# ===========================================================================


def test_accept_without_any_approval_raises(isolated_log):
    """accept on in_review without any reviewer_approval raises
    AcceptGateError (which is also a StoryTransitionError)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    with pytest.raises(sm.AcceptGateError):
        sm.transition_story(target, "accepted")


def test_accept_without_any_approval_also_catchable_as_transition_error(
        isolated_log):
    """AcceptGateError is a StoryTransitionError subclass — existing
    callers can still catch it via the parent class."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")


def test_accept_without_approval_log_unchanged(isolated_log):
    """No-approval accept failure leaves log byte-for-byte unchanged."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")
    assert isolated_log.read_bytes() == bytes_before


def test_accept_without_approval_state_unchanged(isolated_log):
    """No-approval accept failure leaves the story in in_review."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")
    state = sm.derive_state()
    assert state["story_states"][target] == "in_review"


def test_accept_with_only_rejected_approval_raises(isolated_log):
    """A reviewer_approval with approved=False does NOT satisfy the gate."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, False, "smoke regression: 4 failures")
    with pytest.raises(sm.AcceptGateError):
        sm.transition_story(target, "accepted")


def test_accept_with_only_rejected_approval_log_unchanged(isolated_log):
    """The failing accept (with only a False approval present) leaves the
    log byte-for-byte unchanged after the failure point."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, False, "fail")
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")
    assert isolated_log.read_bytes() == bytes_before


def test_accept_with_synthetic_whitespace_test_result_ignored(isolated_log):
    """If a reviewer_approval entry exists with whitespace-only test_result
    (impossible via record_review, but craftable directly into the log),
    the gate must NOT count it as satisfying. record_review's writer rule
    is the policy; the gate's reader rule must agree."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    # Plant a synthetic approval whose test_result is whitespace-only.
    synthetic = sm.build_entry("reviewer_approval", {
        "story_id": target,
        "approved": True,
        "test_result": "   \t\n  ",
    })
    sm._append_entry(synthetic)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")


def test_accept_with_synthetic_empty_test_result_ignored(isolated_log):
    """An approval entry with empty test_result is also ignored by the
    gate (defense in depth — same rule as the writer)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    synthetic = sm.build_entry("reviewer_approval", {
        "story_id": target,
        "approved": True,
        "test_result": "",
    })
    sm._append_entry(synthetic)
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")


def test_accept_error_message_names_missing_prerequisite(isolated_log):
    """The gate-failure exception message names the missing prerequisite
    (per spec: 'error names the missing prerequisite')."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    with pytest.raises(sm.StoryTransitionError) as exc_info:
        sm.transition_story(target, "accepted")
    msg = str(exc_info.value).lower()
    # Look for at least one signal of "what's missing": the words review,
    # approval, or test result. Don't pin exact wording.
    assert any(token in msg for token in (
        "review", "approval", "test_result", "test result", "approve",
    )), f"gate-failure message must name the missing prerequisite; got {msg!r}"


def test_accept_error_message_no_log_write_after_message(isolated_log):
    """Failure message generation does not write to the log
    (combined invariant)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    bytes_before = isolated_log.read_bytes()
    try:
        sm.transition_story(target, "accepted")
    except sm.StoryTransitionError:
        pass
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# Latest approval wins (6) — replay correctly associates LATEST per story
# ===========================================================================


def test_latest_approval_false_then_true_succeeds(isolated_log):
    """Two approvals: false then true — accept succeeds (latest wins)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, False, "fail run 1")
    sm.record_review(target, True, "pass run 2")
    sm.transition_story(target, "accepted")
    state = sm.derive_state()
    assert state["story_states"][target] == "accepted"


def test_latest_approval_true_then_false_fails(isolated_log):
    """Two approvals: true then false — accept fails (latest wins)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, True, "pass run 1")
    sm.record_review(target, False, "regression run 2")
    with pytest.raises(sm.AcceptGateError):
        sm.transition_story(target, "accepted")


def test_latest_approval_three_all_true_succeeds(isolated_log):
    """Three true approvals — accept succeeds."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, True, "pass 1")
    sm.record_review(target, True, "pass 2")
    sm.record_review(target, True, "pass 3")
    sm.transition_story(target, "accepted")
    state = sm.derive_state()
    assert state["story_states"][target] == "accepted"


def test_latest_approval_true_false_true_succeeds(isolated_log):
    """Three approvals true/false/true — accept succeeds (latest is true)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, True, "pass 1")
    sm.record_review(target, False, "fail 2")
    sm.record_review(target, True, "pass 3")
    sm.transition_story(target, "accepted")
    state = sm.derive_state()
    assert state["story_states"][target] == "accepted"


def test_latest_approval_false_true_false_fails(isolated_log):
    """Three approvals false/true/false — accept fails (latest is false)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, False, "fail 1")
    sm.record_review(target, True, "pass 2")
    sm.record_review(target, False, "fail 3")
    with pytest.raises(sm.AcceptGateError):
        sm.transition_story(target, "accepted")


def test_latest_approval_false_true_false_log_unchanged(isolated_log):
    """The failed accept (latest=false) leaves the log unchanged after
    the failure point."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, False, "fail 1")
    sm.record_review(target, True, "pass 2")
    sm.record_review(target, False, "fail 3")
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(target, "accepted")
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# Cross-story isolation (4) — approval for A doesn't satisfy accept on B
# ===========================================================================


def test_approval_for_other_story_does_not_satisfy_accept(isolated_log):
    """A true reviewer_approval for story A does NOT let story B be
    accepted."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    a, b = in_sprint[0], in_sprint[1]
    _drive_to_in_review(a)
    _drive_to_in_review(b)
    sm.record_review(a, True, "A passed")
    with pytest.raises(sm.AcceptGateError):
        sm.transition_story(b, "accepted")


def test_approval_for_other_story_log_unchanged_on_b_accept(isolated_log):
    """The cross-story failed accept leaves the log unchanged after that
    point."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    a, b = in_sprint[0], in_sprint[1]
    _drive_to_in_review(a)
    _drive_to_in_review(b)
    sm.record_review(a, True, "A passed")
    bytes_before = isolated_log.read_bytes()
    with pytest.raises(sm.StoryTransitionError):
        sm.transition_story(b, "accepted")
    assert isolated_log.read_bytes() == bytes_before


def test_each_story_needs_its_own_approval(isolated_log):
    """A and B each need their own approval; once both have it, both
    accept independently."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    a, b = in_sprint[0], in_sprint[1]
    _drive_to_in_review(a)
    _drive_to_in_review(b)
    sm.record_review(a, True, "A pass")
    sm.record_review(b, True, "B pass")
    sm.transition_story(a, "accepted")
    sm.transition_story(b, "accepted")
    state = sm.derive_state()
    assert state["story_states"][a] == "accepted"
    assert state["story_states"][b] == "accepted"


def test_b_approval_doesnt_help_a_after_a_was_rejected_approval(isolated_log):
    """A has a False approval, B has a True approval. accept(A) still
    fails — only A's own latest approval determines A's gate."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    a, b = in_sprint[0], in_sprint[1]
    _drive_to_in_review(a)
    _drive_to_in_review(b)
    sm.record_review(a, False, "A failed")
    sm.record_review(b, True, "B passed")
    with pytest.raises(sm.AcceptGateError):
        sm.transition_story(a, "accepted")


# ===========================================================================
# Reject + start + submit are NOT gated (4) — only accept is
# ===========================================================================


def test_reject_works_without_any_approval(isolated_log):
    """reject does NOT require a reviewer_approval — the gate is
    accept-only per spec."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.transition_story(target, "rejected")
    state = sm.derive_state()
    assert state["story_states"][target] == "rejected"


def test_reject_works_with_only_false_approval(isolated_log):
    """reject works even with only a False approval present
    (it's still not gated)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    _drive_to_in_review(target)
    sm.record_review(target, False, "fail")
    sm.transition_story(target, "rejected")
    state = sm.derive_state()
    assert state["story_states"][target] == "rejected"


def test_start_works_without_any_approval(isolated_log):
    """start (planned -> in_progress) is not gated."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    state = sm.derive_state()
    assert state["story_states"][target] == "in_progress"


def test_submit_works_without_any_approval(isolated_log):
    """submit (in_progress -> in_review) is not gated."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    target = in_sprint[0]
    sm.transition_story(target, "in_progress")
    sm.transition_story(target, "in_review")
    state = sm.derive_state()
    assert state["story_states"][target] == "in_review"


# ===========================================================================
# record-review CLI (7)
# ===========================================================================


def test_cli_record_review_command_known(cli_log):
    """`python -m sm record-review ...` is NOT 'unknown command'."""
    log_path, env = cli_log
    result = _run_cli(env, "record-review", _uuid.uuid4().hex,
                      "--approved", "true", "--test-result", "ok")
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'record-review' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_record_review_happy_exits_zero(cli_log):
    """Valid record-review call exits 0."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance_at(log_path, target, "in_progress", "in_review")
    result = _run_cli(env, "record-review", target,
                      "--approved", "true",
                      "--test-result", "all 12 tests passed")
    assert result.returncode == 0, (
        f"valid record-review must exit 0;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_record_review_writes_reviewer_approval(cli_log):
    """A successful CLI record-review writes one reviewer_approval entry."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance_at(log_path, target, "in_progress", "in_review")
    before = log_path.read_bytes()
    result = _run_cli(env, "record-review", target,
                      "--approved", "true",
                      "--test-result", "12 of 12 pass")
    assert result.returncode == 0
    new_bytes = log_path.read_bytes()[len(before):]
    new_lines = [
        ln for ln in new_bytes.decode("utf-8").splitlines() if ln.strip()
    ]
    approvals = [
        json.loads(ln) for ln in new_lines
        if json.loads(ln).get("type") == "reviewer_approval"
    ]
    assert len(approvals) == 1
    assert approvals[0]["story_id"] == target
    assert approvals[0]["approved"] is True
    assert approvals[0]["test_result"] == "12 of 12 pass"


def test_cli_record_review_then_accept_chain(cli_log):
    """CLI chain: record-review --approved true ... ; accept <id> -> green."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    # Drive to in_review via the existing CLI subcommands.
    assert _run_cli(env, "start", target).returncode == 0
    assert _run_cli(env, "submit", target).returncode == 0
    # Record approval.
    r1 = _run_cli(env, "record-review", target,
                  "--approved", "true",
                  "--test-result", "all green")
    assert r1.returncode == 0, (
        f"record-review must succeed;\n"
        f"stdout={r1.stdout!r}\nstderr={r1.stderr!r}"
    )
    # Now accept must succeed.
    r2 = _run_cli(env, "accept", target)
    assert r2.returncode == 0, (
        f"accept after approval must succeed;\n"
        f"stdout={r2.stdout!r}\nstderr={r2.stderr!r}"
    )
    state = _derive_state_at(log_path)
    assert state["story_states"][target] == "accepted"


def test_cli_record_review_empty_test_result_exits_nonzero(cli_log):
    """CLI record-review with empty test_result fails (non-zero, recognized)."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance_at(log_path, target, "in_progress", "in_review")
    result = _run_cli(env, "record-review", target,
                      "--approved", "true",
                      "--test-result", "")
    _assert_recognized_failure(result)


def test_cli_record_review_whitespace_test_result_exits_nonzero(cli_log):
    """CLI record-review with whitespace-only test_result fails."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance_at(log_path, target, "in_progress", "in_review")
    result = _run_cli(env, "record-review", target,
                      "--approved", "true",
                      "--test-result", "   \t  ")
    _assert_recognized_failure(result)


def test_cli_record_review_accept_without_record_review_fails(cli_log):
    """Without any record-review CLI call, `accept` fails (non-zero,
    recognized) — pure subprocess version of the in-process gate test."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    assert _run_cli(env, "start", target).returncode == 0
    assert _run_cli(env, "submit", target).returncode == 0
    result = _run_cli(env, "accept", target)
    _assert_recognized_failure(result)


# ===========================================================================
# CLI failure invariants (3) — log unchanged on every CLI failure path
# ===========================================================================


def test_cli_record_review_empty_test_result_log_unchanged(cli_log):
    """Empty test_result CLI failure leaves log byte-for-byte unchanged."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance_at(log_path, target, "in_progress", "in_review")
    bytes_before = log_path.read_bytes()
    result = _run_cli(env, "record-review", target,
                      "--approved", "true",
                      "--test-result", "")
    _assert_recognized_failure(result)
    assert log_path.read_bytes() == bytes_before


def test_cli_record_review_whitespace_test_result_log_unchanged(cli_log):
    """Whitespace-only CLI failure leaves the log unchanged."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance_at(log_path, target, "in_progress", "in_review")
    bytes_before = log_path.read_bytes()
    result = _run_cli(env, "record-review", target,
                      "--approved", "true",
                      "--test-result", "   ")
    _assert_recognized_failure(result)
    assert log_path.read_bytes() == bytes_before


def test_cli_accept_without_record_review_log_unchanged(cli_log):
    """`accept` without a prior approval leaves the log unchanged."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    assert _run_cli(env, "start", target).returncode == 0
    assert _run_cli(env, "submit", target).returncode == 0
    bytes_before = log_path.read_bytes()
    result = _run_cli(env, "accept", target)
    _assert_recognized_failure(result)
    assert log_path.read_bytes() == bytes_before
