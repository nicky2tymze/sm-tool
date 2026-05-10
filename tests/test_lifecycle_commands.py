"""Story 14 — pin the contract of the four per-story lifecycle subcommands.

Story 14 (Sprint 2, size M) wires CLI subcommands `start`, `submit`,
`accept`, `reject` on top of Story 13's `transition_story` writer. Each
subcommand takes one positional arg (story_id) and routes to
`transition_story(story_id, <target>)` with the appropriate target state:

    start  <id>  -> transition_story(id, "in_progress")
    submit <id>  -> transition_story(id, "in_review")
    accept <id>  -> transition_story(id, "accepted")
    reject <id>  -> transition_story(id, "rejected")

What this file pins:

  - Each subcommand is RECOGNIZED by the CLI (not "unknown command").
  - Happy path: each subcommand exits 0 on a legal transition, and
    `derive_state()` reflects the new state afterward.
  - Illegal transitions surface a structured non-zero exit (the suite
    standardizes on EXIT_TRANSITION = 9 for state-machine failures, but
    these tests only assert non-zero so a future map to EXIT_OTHER is also
    tolerated — the contract pin is "non-zero", per Story 14 acceptance).
  - Missing positional arg -> non-zero exit, log byte-for-byte unchanged.
  - Extra positional args -> non-zero exit, log byte-for-byte unchanged.
  - Success stdout includes the story_id and the new state (Cardiff-friendly
    confirmation surface — Story 14 acceptance bullet 5).
  - Failure stderr surfaces something useful (the structured error from
    the state machine, per Story 14 acceptance bullet 2).
  - Failure invariant: log unchanged on every error path.
  - Cross-subcommand independence: transitioning one story doesn't move
    another; accept on a terminal story is rejected.
  - End-to-end lifecycles: full chain start->submit->accept and
    start->submit->reject via the CLI.

Tests must FAIL on first run — the four subcommands do not exist in
`sm._cli_main` yet. The Coder downstream wires the dispatch to satisfy
these tests.

The invocation contract is `python -m sm <command> <story_id>`, hermetically
isolated via the SM_LOG_PATH env var (the same hook used by the Story 6
`ingest` CLI tests and the Story 11 `sprint-cut` CLI tests).
"""

from __future__ import annotations

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
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_log(tmp_path):
    """Return a (log_path, env) tuple for hermetic CLI invocation.

    The log_path is a fresh per-test tmp file; the env dict points the CLI
    at it via SM_LOG_PATH. Mirrors the pattern from test_sprint_cut.py.
    """
    log_path = tmp_path / "cli_log.jsonl"
    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(log_path)
    return log_path, env


def _open_iteration_at(log_path: pathlib.Path,
                       iteration_id: str = "iter-1",
                       requirements=None) -> dict:
    """Append an `iteration_open` entry to the file at `log_path` by
    temporarily redirecting `sm.LOG_PATH`. Restores the original on exit so
    no cross-test bleed occurs.
    """
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
    """Append a `story_backlog` entry with N canonical stories to the file
    at `log_path`. Returns the list of minted story_ids in sequence order.
    """
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
    """Open iteration + seed backlog + cut the sprint, all targeting the
    file at `log_path`. Returns (story_ids, in_sprint_ids, deferred_ids).
    """
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


def _derive_state_at(log_path: pathlib.Path) -> dict:
    """Run `sm.derive_state()` against the file at `log_path` by temporarily
    redirecting `sm.LOG_PATH`. Pure read.
    """
    import sm
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        return sm.derive_state()
    finally:
        sm.LOG_PATH = orig_log


def _run_cli(env: dict, *args: str,
             timeout: int = 30) -> subprocess.CompletedProcess:
    """Invoke `python -m sm <args...>` with the supplied env, captured."""
    return subprocess.run(
        [sys.executable, "-m", "sm", *args],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _assert_recognized_failure(result: subprocess.CompletedProcess) -> None:
    """Assert the CLI run failed (non-zero), AND failed for a real reason —
    NOT because the subcommand was unrecognized. This guard ensures these
    tests actually pin Story 14 (subcommand wired + transition rejected)
    rather than accidentally passing against the pre-Story-14 CLI where
    every `start`/`submit`/`accept`/`reject` invocation falls through to
    'unknown command'.
    """
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


def _advance(log_path: pathlib.Path, story_id: str, *target_states: str
             ) -> None:
    """Drive a story through one or more transitions via direct
    `sm.transition_story` calls (not the CLI under test), so happy-path
    tests can stage a story into the state we want without depending on
    the CLI we're testing.

    Story 15 extension: if any target state is `accepted`, a satisfying
    `reviewer_approval` entry is recorded immediately beforehand so the
    accept gate is satisfied. Mirrors how the CLI flow expects callers to
    chain `record-review` before `accept`.
    """
    import sm
    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        for to_state in target_states:
            if to_state == "accepted":
                sm.record_review(story_id, True, "ok")
            sm.transition_story(story_id, to_state)
    finally:
        sm.LOG_PATH = orig_log


# ===========================================================================
# Smoke (4) — each subcommand is recognized
# ===========================================================================


def test_cli_start_command_known(cli_log):
    """`python -m sm start <id>` is NOT 'unknown command'."""
    log_path, env = cli_log
    result = _run_cli(env, "start", _uuid.uuid4().hex)
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'start' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_submit_command_known(cli_log):
    """`python -m sm submit <id>` is NOT 'unknown command'."""
    log_path, env = cli_log
    result = _run_cli(env, "submit", _uuid.uuid4().hex)
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'submit' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_accept_command_known(cli_log):
    """`python -m sm accept <id>` is NOT 'unknown command'."""
    log_path, env = cli_log
    result = _run_cli(env, "accept", _uuid.uuid4().hex)
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'accept' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_reject_command_known(cli_log):
    """`python -m sm reject <id>` is NOT 'unknown command'."""
    log_path, env = cli_log
    result = _run_cli(env, "reject", _uuid.uuid4().hex)
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'reject' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ===========================================================================
# Happy path — start  (4)
# ===========================================================================


def test_cli_start_exits_zero_on_planned(cli_log):
    """start on a planned in-sprint story exits 0."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    result = _run_cli(env, "start", target)
    assert result.returncode == 0, (
        f"start on planned story must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_start_moves_story_to_in_progress(cli_log):
    """After `start <id>`, derive_state shows the story in in_progress."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    result = _run_cli(env, "start", target)
    assert result.returncode == 0
    state = _derive_state_at(log_path)
    assert state["story_states"][target] == "in_progress", (
        f"story must be in_progress after `start`; "
        f"got {state['story_states'][target]!r}"
    )


def test_cli_start_writes_one_story_state_change(cli_log):
    """A successful `start` writes exactly one new story_state_change."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    before = log_path.read_bytes()

    result = _run_cli(env, "start", target)
    assert result.returncode == 0
    # Count new story_state_change entries.
    new_bytes = log_path.read_bytes()[len(before):]
    new_lines = [
        ln for ln in new_bytes.decode("utf-8").splitlines() if ln.strip()
    ]
    state_changes = [
        json.loads(ln) for ln in new_lines
        if json.loads(ln).get("type") == "story_state_change"
    ]
    assert len(state_changes) == 1, (
        f"start must write exactly one story_state_change; "
        f"got {len(state_changes)}"
    )


def test_cli_start_entry_has_correct_to_state(cli_log):
    """The new story_state_change entry has to_state=in_progress."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    result = _run_cli(env, "start", target)
    assert result.returncode == 0
    state_changes = [
        json.loads(ln) for ln in log_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if ln.strip() and json.loads(ln).get("type") == "story_state_change"
    ]
    assert state_changes[-1]["to_state"] == "in_progress"
    assert state_changes[-1]["story_id"] == target


# ===========================================================================
# Happy path — submit  (4)
# ===========================================================================


def test_cli_submit_exits_zero_on_in_progress(cli_log):
    """submit on an in_progress story exits 0."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress")

    result = _run_cli(env, "submit", target)
    assert result.returncode == 0, (
        f"submit on in_progress story must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_submit_moves_story_to_in_review(cli_log):
    """After `submit <id>`, derive_state shows the story in in_review."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress")

    result = _run_cli(env, "submit", target)
    assert result.returncode == 0
    state = _derive_state_at(log_path)
    assert state["story_states"][target] == "in_review"


def test_cli_submit_writes_one_story_state_change(cli_log):
    """A successful `submit` writes exactly one new story_state_change."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress")
    before = log_path.read_bytes()

    result = _run_cli(env, "submit", target)
    assert result.returncode == 0
    new_bytes = log_path.read_bytes()[len(before):]
    new_lines = [
        ln for ln in new_bytes.decode("utf-8").splitlines() if ln.strip()
    ]
    state_changes = [
        json.loads(ln) for ln in new_lines
        if json.loads(ln).get("type") == "story_state_change"
    ]
    assert len(state_changes) == 1


def test_cli_submit_entry_has_correct_to_state(cli_log):
    """The new entry has to_state=in_review."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress")

    result = _run_cli(env, "submit", target)
    assert result.returncode == 0
    state_changes = [
        json.loads(ln) for ln in log_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if ln.strip() and json.loads(ln).get("type") == "story_state_change"
    ]
    assert state_changes[-1]["to_state"] == "in_review"
    assert state_changes[-1]["story_id"] == target


# ===========================================================================
# Happy path — accept  (4)
# ===========================================================================


def test_cli_accept_exits_zero_on_in_review(cli_log):
    """accept on an in_review story exits 0."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review")
    assert _run_cli(env, "record-review", target,
                    "--approved", "true",
                    "--test-result", "ok").returncode == 0

    result = _run_cli(env, "accept", target)
    assert result.returncode == 0, (
        f"accept on in_review story must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_accept_moves_story_to_accepted(cli_log):
    """After `accept <id>`, derive_state shows the story in accepted."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review")
    assert _run_cli(env, "record-review", target,
                    "--approved", "true",
                    "--test-result", "ok").returncode == 0

    result = _run_cli(env, "accept", target)
    assert result.returncode == 0
    state = _derive_state_at(log_path)
    assert state["story_states"][target] == "accepted"


def test_cli_accept_writes_one_story_state_change(cli_log):
    """A successful `accept` writes exactly one new story_state_change."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review")
    assert _run_cli(env, "record-review", target,
                    "--approved", "true",
                    "--test-result", "ok").returncode == 0
    before = log_path.read_bytes()

    result = _run_cli(env, "accept", target)
    assert result.returncode == 0
    new_bytes = log_path.read_bytes()[len(before):]
    new_lines = [
        ln for ln in new_bytes.decode("utf-8").splitlines() if ln.strip()
    ]
    state_changes = [
        json.loads(ln) for ln in new_lines
        if json.loads(ln).get("type") == "story_state_change"
    ]
    assert len(state_changes) == 1


def test_cli_accept_entry_has_correct_to_state(cli_log):
    """The new entry has to_state=accepted."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review")
    assert _run_cli(env, "record-review", target,
                    "--approved", "true",
                    "--test-result", "ok").returncode == 0

    result = _run_cli(env, "accept", target)
    assert result.returncode == 0
    state_changes = [
        json.loads(ln) for ln in log_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if ln.strip() and json.loads(ln).get("type") == "story_state_change"
    ]
    assert state_changes[-1]["to_state"] == "accepted"
    assert state_changes[-1]["story_id"] == target


# ===========================================================================
# Happy path — reject  (4)
# ===========================================================================


def test_cli_reject_exits_zero_on_in_review(cli_log):
    """reject on an in_review story exits 0."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review")

    result = _run_cli(env, "reject", target)
    assert result.returncode == 0, (
        f"reject on in_review story must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_reject_moves_story_to_rejected(cli_log):
    """After `reject <id>`, derive_state shows the story in rejected."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review")

    result = _run_cli(env, "reject", target)
    assert result.returncode == 0
    state = _derive_state_at(log_path)
    assert state["story_states"][target] == "rejected"


def test_cli_reject_writes_one_story_state_change(cli_log):
    """A successful `reject` writes exactly one new story_state_change."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review")
    before = log_path.read_bytes()

    result = _run_cli(env, "reject", target)
    assert result.returncode == 0
    new_bytes = log_path.read_bytes()[len(before):]
    new_lines = [
        ln for ln in new_bytes.decode("utf-8").splitlines() if ln.strip()
    ]
    state_changes = [
        json.loads(ln) for ln in new_lines
        if json.loads(ln).get("type") == "story_state_change"
    ]
    assert len(state_changes) == 1


def test_cli_reject_entry_has_correct_to_state(cli_log):
    """The new entry has to_state=rejected."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review")

    result = _run_cli(env, "reject", target)
    assert result.returncode == 0
    state_changes = [
        json.loads(ln) for ln in log_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if ln.strip() and json.loads(ln).get("type") == "story_state_change"
    ]
    assert state_changes[-1]["to_state"] == "rejected"
    assert state_changes[-1]["story_id"] == target


# ===========================================================================
# Illegal transitions (8) — each exits non-zero
# ===========================================================================


def test_cli_submit_before_start_exits_nonzero(cli_log):
    """submit on a planned story (skip start) exits non-zero with a
    recognized-subcommand failure path."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    result = _run_cli(env, "submit", target)
    _assert_recognized_failure(result)


def test_cli_accept_before_submit_exits_nonzero(cli_log):
    """accept on a planned story (skip start+submit) exits non-zero."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    result = _run_cli(env, "accept", target)
    _assert_recognized_failure(result)


def test_cli_reject_before_submit_exits_nonzero(cli_log):
    """reject on a planned story exits non-zero."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    result = _run_cli(env, "reject", target)
    _assert_recognized_failure(result)


def test_cli_accept_on_in_progress_exits_nonzero(cli_log):
    """accept on in_progress (skip in_review) exits non-zero."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress")

    result = _run_cli(env, "accept", target)
    _assert_recognized_failure(result)


def test_cli_start_on_in_progress_exits_nonzero(cli_log):
    """start on an already in_progress story (self-loop) exits non-zero."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress")

    result = _run_cli(env, "start", target)
    _assert_recognized_failure(result)


def test_cli_start_on_accepted_exits_nonzero(cli_log):
    """start on an accepted (terminal) story exits non-zero."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review", "accepted")

    result = _run_cli(env, "start", target)
    _assert_recognized_failure(result)


def test_cli_submit_on_rejected_exits_nonzero(cli_log):
    """submit on a rejected (terminal) story exits non-zero."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review", "rejected")

    result = _run_cli(env, "submit", target)
    _assert_recognized_failure(result)


def test_cli_accept_on_accepted_exits_nonzero(cli_log):
    """accept on an already-accepted story exits non-zero (terminal)."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review", "accepted")

    result = _run_cli(env, "accept", target)
    _assert_recognized_failure(result)


# ===========================================================================
# Missing args (4) — each subcommand without story_id exits non-zero
# ===========================================================================


def test_cli_start_missing_story_id_exits_nonzero(cli_log):
    """`python -m sm start` (no story_id) exits non-zero, recognized
    subcommand failure path (not 'unknown command')."""
    log_path, env = cli_log
    result = _run_cli(env, "start")
    _assert_recognized_failure(result)


def test_cli_submit_missing_story_id_exits_nonzero(cli_log):
    """`python -m sm submit` (no story_id) exits non-zero."""
    log_path, env = cli_log
    result = _run_cli(env, "submit")
    _assert_recognized_failure(result)


def test_cli_accept_missing_story_id_exits_nonzero(cli_log):
    """`python -m sm accept` (no story_id) exits non-zero."""
    log_path, env = cli_log
    result = _run_cli(env, "accept")
    _assert_recognized_failure(result)


def test_cli_reject_missing_story_id_exits_nonzero(cli_log):
    """`python -m sm reject` (no story_id) exits non-zero."""
    log_path, env = cli_log
    result = _run_cli(env, "reject")
    _assert_recognized_failure(result)


# ===========================================================================
# Extra args (4) — each subcommand with too many args exits non-zero
# ===========================================================================


def test_cli_start_extra_arg_exits_nonzero(cli_log):
    """`python -m sm start <id> <extra>` exits non-zero, recognized failure."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    result = _run_cli(env, "start", in_sprint[0], "extra-arg")
    _assert_recognized_failure(result)


def test_cli_submit_extra_arg_exits_nonzero(cli_log):
    """`python -m sm submit <id> <extra>` exits non-zero."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    _advance(log_path, in_sprint[0], "in_progress")
    result = _run_cli(env, "submit", in_sprint[0], "extra-arg")
    _assert_recognized_failure(result)


def test_cli_accept_extra_arg_exits_nonzero(cli_log):
    """`python -m sm accept <id> <extra>` exits non-zero."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    _advance(log_path, in_sprint[0], "in_progress", "in_review")
    result = _run_cli(env, "accept", in_sprint[0], "extra-arg")
    _assert_recognized_failure(result)


def test_cli_reject_extra_arg_exits_nonzero(cli_log):
    """`python -m sm reject <id> <extra>` exits non-zero."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    _advance(log_path, in_sprint[0], "in_progress", "in_review")
    result = _run_cli(env, "reject", in_sprint[0], "extra-arg")
    _assert_recognized_failure(result)


# ===========================================================================
# Output content (5) — success names story_id + state, failure surfaces err
# ===========================================================================


def test_cli_start_success_stdout_mentions_story_id(cli_log):
    """Successful start prints something mentioning the story_id."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    result = _run_cli(env, "start", target)
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert target in combined, (
        f"success output should name story_id {target!r};\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_start_success_stdout_mentions_new_state(cli_log):
    """Successful start prints something mentioning the new state."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    result = _run_cli(env, "start", target)
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert "in_progress" in combined, (
        f"success output should name new state 'in_progress';\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_accept_success_stdout_mentions_new_state(cli_log):
    """Successful accept prints something mentioning 'accepted'."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review")
    assert _run_cli(env, "record-review", target,
                    "--approved", "true",
                    "--test-result", "ok").returncode == 0

    result = _run_cli(env, "accept", target)
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert "accepted" in combined, (
        f"success output should name new state 'accepted';\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_illegal_transition_stderr_nonempty(cli_log):
    """An illegal transition produces non-empty stderr (useful error
    surfaced — Story 14 acceptance bullet 2)."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    # submit on planned -> illegal
    result = _run_cli(env, "submit", target)
    _assert_recognized_failure(result)
    assert result.stderr.strip(), (
        f"illegal transition must surface a stderr message;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_unknown_story_stderr_nonempty(cli_log):
    """A transition on an unknown story_id surfaces a useful stderr."""
    log_path, env = cli_log
    _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    bogus = _uuid.uuid4().hex

    result = _run_cli(env, "start", bogus)
    _assert_recognized_failure(result)
    assert result.stderr.strip(), (
        f"unknown story_id must surface a stderr message;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ===========================================================================
# Failure invariants (6) — log unchanged on every failure path
# ===========================================================================


def test_cli_start_illegal_log_unchanged(cli_log):
    """start on an accepted (terminal) story leaves log unchanged."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress", "in_review", "accepted")
    bytes_before = log_path.read_bytes()

    result = _run_cli(env, "start", target)
    _assert_recognized_failure(result)
    assert log_path.read_bytes() == bytes_before, (
        "log must be byte-for-byte unchanged on a failed start"
    )


def test_cli_submit_illegal_log_unchanged(cli_log):
    """submit on planned (skip) leaves log unchanged."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    bytes_before = log_path.read_bytes()

    result = _run_cli(env, "submit", target)
    _assert_recognized_failure(result)
    assert log_path.read_bytes() == bytes_before


def test_cli_accept_illegal_log_unchanged(cli_log):
    """accept on in_progress (skip in_review) leaves log unchanged."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    _advance(log_path, target, "in_progress")
    bytes_before = log_path.read_bytes()

    result = _run_cli(env, "accept", target)
    _assert_recognized_failure(result)
    assert log_path.read_bytes() == bytes_before


def test_cli_reject_illegal_log_unchanged(cli_log):
    """reject on planned leaves log unchanged."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]
    bytes_before = log_path.read_bytes()

    result = _run_cli(env, "reject", target)
    _assert_recognized_failure(result)
    assert log_path.read_bytes() == bytes_before


def test_cli_missing_arg_log_unchanged(cli_log):
    """`python -m sm start` (no story_id) leaves log unchanged."""
    log_path, env = cli_log
    _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    bytes_before = log_path.read_bytes()

    result = _run_cli(env, "start")
    _assert_recognized_failure(result)
    assert log_path.read_bytes() == bytes_before


def test_cli_extra_arg_log_unchanged(cli_log):
    """`python -m sm start <id> <extra>` leaves log unchanged."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    bytes_before = log_path.read_bytes()

    result = _run_cli(env, "start", in_sprint[0], "extra-arg")
    _assert_recognized_failure(result)
    assert log_path.read_bytes() == bytes_before


# ===========================================================================
# Cross-subcommand independence (3)
# ===========================================================================


def test_cli_start_on_one_story_does_not_move_another(cli_log):
    """`start A` leaves story B in planned."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    a, b = in_sprint[0], in_sprint[1]

    result = _run_cli(env, "start", a)
    assert result.returncode == 0
    state = _derive_state_at(log_path)
    assert state["story_states"][a] == "in_progress"
    assert state["story_states"][b] == "planned", (
        f"other in-sprint story {b!r} must remain planned; "
        f"got {state['story_states'][b]!r}"
    )


def test_cli_accept_on_terminal_story_rejected_independently(cli_log):
    """accept on accepted A doesn't taint story B's pipeline."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    a, b = in_sprint[0], in_sprint[1]
    # Drive A to accepted via the CLI commands themselves (end-to-end).
    assert _run_cli(env, "start", a).returncode == 0
    assert _run_cli(env, "submit", a).returncode == 0
    assert _run_cli(env, "record-review", a,
                    "--approved", "true",
                    "--test-result", "ok").returncode == 0
    assert _run_cli(env, "accept", a).returncode == 0

    # accept on A again is rejected (terminal).
    result = _run_cli(env, "accept", a)
    assert result.returncode != 0

    # B is unaffected — still planned, can be started.
    result_b = _run_cli(env, "start", b)
    assert result_b.returncode == 0, (
        f"unrelated story B must be startable;\n"
        f"stdout={result_b.stdout!r}\nstderr={result_b.stderr!r}"
    )
    state = _derive_state_at(log_path)
    assert state["story_states"][a] == "accepted"
    assert state["story_states"][b] == "in_progress"


def test_cli_two_stories_diverge(cli_log):
    """One story accepted, another rejected — both reflected
    independently."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    a, b = in_sprint[0], in_sprint[1]

    # A: start -> submit -> record-review -> accept
    assert _run_cli(env, "start", a).returncode == 0
    assert _run_cli(env, "submit", a).returncode == 0
    assert _run_cli(env, "record-review", a,
                    "--approved", "true",
                    "--test-result", "ok").returncode == 0
    assert _run_cli(env, "accept", a).returncode == 0
    # B: start -> submit -> reject
    assert _run_cli(env, "start", b).returncode == 0
    assert _run_cli(env, "submit", b).returncode == 0
    assert _run_cli(env, "reject", b).returncode == 0

    state = _derive_state_at(log_path)
    assert state["story_states"][a] == "accepted"
    assert state["story_states"][b] == "rejected"


# ===========================================================================
# End-to-end lifecycles (2) — full chains via CLI
# ===========================================================================


def test_cli_full_chain_to_accepted(cli_log):
    """Full happy chain start -> submit -> accept via the CLI."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    r1 = _run_cli(env, "start", target)
    assert r1.returncode == 0, (
        f"start failed;\nstdout={r1.stdout!r}\nstderr={r1.stderr!r}"
    )
    assert (
        _derive_state_at(log_path)["story_states"][target] == "in_progress"
    )

    r2 = _run_cli(env, "submit", target)
    assert r2.returncode == 0, (
        f"submit failed;\nstdout={r2.stdout!r}\nstderr={r2.stderr!r}"
    )
    assert (
        _derive_state_at(log_path)["story_states"][target] == "in_review"
    )

    rrr = _run_cli(env, "record-review", target,
                   "--approved", "true",
                   "--test-result", "ok")
    assert rrr.returncode == 0, (
        f"record-review failed;\nstdout={rrr.stdout!r}"
        f"\nstderr={rrr.stderr!r}"
    )

    r3 = _run_cli(env, "accept", target)
    assert r3.returncode == 0, (
        f"accept failed;\nstdout={r3.stdout!r}\nstderr={r3.stderr!r}"
    )
    assert (
        _derive_state_at(log_path)["story_states"][target] == "accepted"
    )


def test_cli_full_chain_to_rejected(cli_log):
    """Full chain start -> submit -> reject via the CLI."""
    log_path, env = cli_log
    _, in_sprint, _ = _seed_sprint_at(log_path, n_stories=5, cut_at=3)
    target = in_sprint[0]

    r1 = _run_cli(env, "start", target)
    assert r1.returncode == 0
    r2 = _run_cli(env, "submit", target)
    assert r2.returncode == 0
    r3 = _run_cli(env, "reject", target)
    assert r3.returncode == 0, (
        f"reject failed;\nstdout={r3.stdout!r}\nstderr={r3.stderr!r}"
    )
    assert (
        _derive_state_at(log_path)["story_states"][target] == "rejected"
    )
