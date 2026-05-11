"""Story 19 — pin the contract of `sm.force_close` + the `force-close` CLI.

Story 19 (Sprint 2, size M) is the force-close command. It introduces a
new public function, a new typed error, and a new CLI subcommand that
reuses Story 18's close-handoff producer. The whole point of force-close
is that it does NOT require every in-sprint story to be in a terminal
state — instead, non-terminal in-sprint stories are transitioned to
`force_closed` first, then the standard close-handoff path runs.

    force_close(reason: str) -> dict
    class ForceCloseError(ValueError)
    python -m sm force-close --reason <text>

What this file pins:

  - Function signature and shape:
      `force_close(reason)` — PUBLIC, callable, in `sm.__all__`,
      importable as `from sm import force_close`. Takes one positional
      arg `reason: str`. Returns the appended `iteration_close` log
      entry dict (the function reuses Story 18's close path).

  - Typed error class:
      `sm.ForceCloseError` exists, subclasses `ValueError`, is in
      `sm.__all__`. Subclassing ValueError keeps existing `except
      ValueError` callers compatible.

  - Reason validation:
      * non-string `reason` -> TypeError, no log write
      * empty string -> ForceCloseError, no log write
      * whitespace-only (spaces, tab, newline) -> ForceCloseError,
        no log write

  - Pre-conditions (each raises ForceCloseError, no log write,
    no handoff file):
      * no active iteration
      * no story backlog (decompose not run)
      * no sprint_cut yet

  - Force-close transitions: every in-sprint story whose current state
    is NOT in {accepted, rejected, force_closed} gets a
    `story_state_change` log entry transitioning it to `force_closed`
    before the close-handoff producer runs. Already-terminal stories
    are NOT transitioned (no duplicate state_change entry).

  - The close-handoff entry written by force-close carries
    `closed_by="force-close"` and `reason=<verbatim text>`. The
    handoff JSON sidecar surfaces force-closed stories with
    `outcome="force_closed"`.

  - Failure invariants: log byte-for-byte unchanged on every
    validation failure (TypeError or ForceCloseError, before any
    state_change entries are written), and no handoff JSON file
    appears.

  - CLI surface — `python -m sm force-close --reason "<text>"`:
      * Subcommand recognized.
      * Exits 0 on success.
      * Missing reason -> non-zero exit.
      * Empty reason -> non-zero exit.
      * `--help` / `-h` exits 0.

Tests must FAIL on first run — `force_close`, `ForceCloseError`, and
the `force-close` CLI subcommand do not exist yet. The Coder downstream
implements them to satisfy these tests.

The CLI invocation contract is `python -m sm force-close --reason
"<text>"`, hermetically isolated via the SM_TEST_LOG_PATH env var (the same
hook used by every other Sprint 2 subcommand-level test).
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
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file. Mirrors suite convention."""
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


@pytest.fixture
def cli_log(tmp_path):
    """Return (log_path, env) for hermetic CLI invocation via SM_TEST_LOG_PATH."""
    log_path = tmp_path / "cli_log.jsonl"
    env = os.environ.copy()
    env["SM_TEST_LOG_PATH"] = str(log_path)
    return log_path, env


def _open_iteration(iteration_id: str = "iter-1",
                    requirements=None,
                    goal: str = "Test iteration") -> dict:
    """Append an `iteration_open` entry directly. Caller owns `isolated_log`."""
    import sm

    if requirements is None:
        requirements = [
            {"requirement_id": "req-1", "title": "T1",
             "description": "D1", "priority": "MUST",
             "acceptance_criteria": "AC1"},
        ]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": goal,
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return entry


def _seed_backlog(n: int = 5,
                  requirement_ids_per_story=None,
                  iteration_id: str = "iter-1") -> list:
    """Append a `story_backlog` entry with N canonical stories. Returns
    the list of minted story_ids in sequence order.
    """
    import sm

    if requirement_ids_per_story is None:
        requirement_ids_per_story = [["req-1"] for _ in range(n)]
    if len(requirement_ids_per_story) != n:
        raise ValueError(
            "requirement_ids_per_story length must equal n; got "
            f"len={len(requirement_ids_per_story)} n={n}"
        )

    story_ids = [_uuid.uuid4().hex for _ in range(n)]
    sizes = ["S", "M", "L"]
    stories = []
    for i in range(1, n + 1):
        stories.append({
            "story_id": story_ids[i - 1],
            "sequence": i,
            "title": f"Story {i}",
            "size": sizes[(i - 1) % 3],
            "requirement_ids": list(requirement_ids_per_story[i - 1]),
            "acceptance_criteria": f"Story {i} must pass.",
        })
    entry = sm.build_entry("story_backlog", {
        "stories": stories,
        "role_spec_path": "<test-stub>",
        "role_spec_hash": "<test-stub>",
    })
    sm._append_entry(entry)
    return story_ids


def _seed_full(n_stories: int = 5,
               cut_at: int = 3,
               iteration_id: str = "iter-1",
               requirements=None,
               goal: str = "Test iteration") -> tuple:
    """Open iteration + decompose backlog + cut sprint. Returns
    (story_ids, in_sprint_ids, deferred_ids).
    """
    import sm

    _open_iteration(iteration_id=iteration_id,
                    requirements=requirements, goal=goal)
    sids = _seed_backlog(n=n_stories, iteration_id=iteration_id)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at], sids[cut_at:]


def _advance(story_id: str, *target_states: str) -> None:
    """Drive a story through transitions; records review before accept."""
    import sm

    for to_state in target_states:
        if to_state == "accepted":
            sm.record_review(story_id, True, "ok")
        sm.transition_story(story_id, to_state)


def _handoff_path_for(log_path: pathlib.Path,
                      iteration_id: str) -> pathlib.Path:
    """Compute the expected handoff JSON path per the Story 18 convention."""
    return log_path.parent / f"close_handoff_{iteration_id}.json"


def _run_cli(env: dict, *args: str,
             timeout: int = 30) -> subprocess.CompletedProcess:
    """Invoke `python -m sm <args...>` with the supplied env."""
    return subprocess.run(
        [sys.executable, "-m", "sm", *args],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_at(log_path: pathlib.Path, fn, *args, **kwargs):
    """Run a python-side helper against an arbitrary log path by
    temporarily redirecting sm.LOG_PATH. Restores on exit.
    """
    import sm

    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        return fn(*args, **kwargs)
    finally:
        sm.LOG_PATH = orig_log


def _state_change_entries_for(log_path: pathlib.Path,
                              story_id: str) -> list:
    """Return all story_state_change entries in `log_path` for the
    given story_id, in file order."""
    out = []
    if not log_path.exists():
        return out
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if (e.get("type") == "story_state_change"
                and e.get("story_id") == story_id):
            out.append(e)
    return out


# ===========================================================================
# Smoke (6) — force_close exists, callable, public, in __all__, signature
# ===========================================================================


def test_force_close_function_exists():
    """sm.force_close must exist on the module."""
    import sm
    assert hasattr(sm, "force_close"), (
        "sm.force_close must exist"
    )


def test_force_close_function_is_callable():
    """sm.force_close must be callable."""
    import sm
    assert callable(sm.force_close), (
        "sm.force_close must be callable"
    )


def test_force_close_function_is_public():
    """No leading underscore — public API."""
    import sm
    name = sm.force_close.__name__
    assert not name.startswith("_"), (
        f"force_close must be public; got name {name!r}"
    )
    assert name == "force_close"


def test_force_close_function_importable_directly():
    """`from sm import force_close` succeeds."""
    from sm import force_close  # noqa: F401
    assert callable(force_close)


def test_force_close_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__")
    assert "force_close" in sm.__all__, (
        f"force_close must be in __all__; got {sm.__all__!r}"
    )


def test_force_close_signature_accepts_reason():
    """force_close accepts a `reason` parameter (positional or keyword)."""
    import sm
    sig = inspect.signature(sm.force_close)
    params = sig.parameters
    assert "reason" in params, (
        f"force_close must accept a 'reason' parameter; "
        f"got {list(params)!r}"
    )


# ===========================================================================
# ForceCloseError typed (5) — exists, in __all__, ValueError subclass
# ===========================================================================


def test_force_close_error_exists():
    """sm.ForceCloseError must exist."""
    import sm
    assert hasattr(sm, "ForceCloseError"), (
        "sm.ForceCloseError must exist"
    )


def test_force_close_error_is_value_error_subclass():
    """ForceCloseError narrows ValueError so existing `except ValueError`
    callers keep working."""
    import sm
    assert issubclass(sm.ForceCloseError, ValueError), (
        f"ForceCloseError must subclass ValueError; got bases "
        f"{sm.ForceCloseError.__mro__!r}"
    )


def test_force_close_error_in_dunder_all():
    """ForceCloseError is exported via __all__."""
    import sm
    assert "ForceCloseError" in sm.__all__, (
        f"ForceCloseError must be in __all__; got {sm.__all__!r}"
    )


def test_force_close_error_is_a_class():
    """ForceCloseError is an exception class (not a function / instance)."""
    import sm
    assert isinstance(sm.ForceCloseError, type), (
        f"ForceCloseError must be a class; got {sm.ForceCloseError!r}"
    )
    assert issubclass(sm.ForceCloseError, Exception)


def test_force_close_error_constructible():
    """ForceCloseError can be raised + caught with a message."""
    import sm
    with pytest.raises(sm.ForceCloseError) as exc_info:
        raise sm.ForceCloseError("test message")
    assert "test message" in str(exc_info.value)


# ===========================================================================
# Reason validation (10) — type errors, empty/whitespace -> ForceCloseError
# ===========================================================================


def test_force_close_none_reason_raises_type_error(isolated_log):
    """reason=None → TypeError, no log write."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-rv-none")
    before = isolated_log.read_bytes() if isolated_log.exists() else b""
    with pytest.raises(TypeError):
        sm.force_close(None)
    after = isolated_log.read_bytes() if isolated_log.exists() else b""
    assert before == after, "TypeError must not write to log"


def test_force_close_int_reason_raises_type_error(isolated_log):
    """reason=42 → TypeError, no log write."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-rv-int")
    before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.force_close(42)
    after = isolated_log.read_bytes()
    assert before == after


def test_force_close_list_reason_raises_type_error(isolated_log):
    """reason=[] → TypeError, no log write."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-rv-list")
    before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.force_close(["a"])
    after = isolated_log.read_bytes()
    assert before == after


def test_force_close_dict_reason_raises_type_error(isolated_log):
    """reason={} → TypeError, no log write."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-rv-dict")
    before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.force_close({"reason": "x"})
    after = isolated_log.read_bytes()
    assert before == after


def test_force_close_empty_reason_raises_force_close_error(isolated_log):
    """reason='' → ForceCloseError, no log write."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-rv-empty")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("")
    after = isolated_log.read_bytes()
    assert before == after, "empty reason must not write to log"


def test_force_close_spaces_only_reason_raises_force_close_error(isolated_log):
    """reason='   ' → ForceCloseError (whitespace-only), no log write."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-rv-ws")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("   ")
    after = isolated_log.read_bytes()
    assert before == after


def test_force_close_tab_only_reason_raises_force_close_error(isolated_log):
    """reason='\\t' → ForceCloseError, no log write."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-rv-tab")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("\t")
    after = isolated_log.read_bytes()
    assert before == after


def test_force_close_newline_only_reason_raises_force_close_error(isolated_log):
    """reason='\\n' → ForceCloseError, no log write."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-rv-nl")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("\n")
    after = isolated_log.read_bytes()
    assert before == after


def test_force_close_mixed_whitespace_reason_raises_force_close_error(
        isolated_log):
    """reason=' \\t\\n ' → ForceCloseError, no log write."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-rv-mixed")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.ForceCloseError):
        sm.force_close(" \t\n ")
    after = isolated_log.read_bytes()
    assert before == after


def test_force_close_valid_reason_succeeds(isolated_log):
    """A non-empty, non-whitespace reason succeeds (with terminal stories)."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-rv-ok")
    # All stories accepted so force-close has no work-to-do on transitions
    # but the writer still produces an iteration_close.
    for sid in in_sprint:
        _advance(sid, "in_progress", "in_review", "accepted")
    entry = sm.force_close("budget cut")
    assert entry["type"] == "iteration_close"


# ===========================================================================
# Pre-conditions (6) — no active iter / no backlog / no cut -> ForceCloseError
# ===========================================================================


def test_force_close_no_active_iteration_raises(isolated_log):
    """Empty log → ForceCloseError, no log write."""
    import sm
    before = isolated_log.read_bytes() if isolated_log.exists() else b""
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("reason text")
    after = isolated_log.read_bytes() if isolated_log.exists() else b""
    assert before == after, "no-active-iteration failure must not write"


def test_force_close_no_active_iteration_no_handoff_file(isolated_log,
                                                         tmp_path):
    """No active iter + failed force-close → no handoff JSON in dir."""
    import sm
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("reason text")
    handoffs = [p.name for p in tmp_path.iterdir()
                if p.name.startswith("close_handoff_")
                and p.name.endswith(".json")]
    assert handoffs == [], (
        f"no handoff file should appear; got: {handoffs!r}"
    )


def test_force_close_no_backlog_raises(isolated_log):
    """Iteration open + no decompose → ForceCloseError, no log write."""
    import sm
    _open_iteration(iteration_id="iter-pre-nb")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("can't even decompose")
    after = isolated_log.read_bytes()
    assert before == after, "no-backlog failure must not write"


def test_force_close_no_backlog_no_handoff_file(isolated_log, tmp_path):
    """No backlog + failed force-close → no handoff JSON in dir."""
    import sm
    _open_iteration(iteration_id="iter-pre-nb2")
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("blocker")
    handoffs = [p.name for p in tmp_path.iterdir()
                if p.name.startswith("close_handoff_")
                and p.name.endswith(".json")]
    assert handoffs == [], (
        f"no handoff file should appear; got: {handoffs!r}"
    )


def test_force_close_no_sprint_cut_raises(isolated_log):
    """Open + decompose + no cut → ForceCloseError, no log write.
    Force-close still needs a sprint to act on — it transitions in-sprint
    stories, and pre-cut there is no in-sprint set."""
    import sm
    _open_iteration(iteration_id="iter-pre-nc")
    _seed_backlog(n=3, iteration_id="iter-pre-nc")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("nothing cut yet")
    after = isolated_log.read_bytes()
    assert before == after, "no-cut failure must not write"


def test_force_close_no_sprint_cut_no_handoff_file(isolated_log, tmp_path):
    """No cut + failed force-close → no handoff JSON in dir."""
    import sm
    _open_iteration(iteration_id="iter-pre-nc2")
    _seed_backlog(n=3, iteration_id="iter-pre-nc2")
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("nope")
    handoffs = [p.name for p in tmp_path.iterdir()
                if p.name.startswith("close_handoff_")
                and p.name.endswith(".json")]
    assert handoffs == [], (
        f"no handoff file should appear; got: {handoffs!r}"
    )


# ===========================================================================
# Force-close transitions non-terminal stories (10) — every non-terminal
# in-sprint story gets a story_state_change entry to force_closed
# ===========================================================================


def test_force_close_all_planned_transitions_all(isolated_log):
    """All in-sprint planned → every one gets a state_change entry to
    force_closed before close."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-fct-allplan")
    sm.force_close("planned blocked")
    # Each in-sprint story should have exactly one state_change ending
    # in force_closed.
    for sid in in_sprint:
        entries = _state_change_entries_for(isolated_log, sid)
        assert len(entries) >= 1, (
            f"story {sid!r} must have at least one state_change entry"
        )
        assert entries[-1]["to_state"] == "force_closed", (
            f"story {sid!r}'s last state_change must target force_closed; "
            f"got {entries[-1]['to_state']!r}"
        )


def test_force_close_in_progress_story_transitioned(isolated_log):
    """An in_progress story gets a state_change → force_closed."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-fct-inprog")
    _advance(in_sprint[0], "in_progress")
    sm.force_close("hardware fire")
    entries = _state_change_entries_for(isolated_log, in_sprint[0])
    # Should have at least the prior in_progress transition + the new
    # force_closed transition.
    last = entries[-1]
    assert last["to_state"] == "force_closed", (
        f"in_progress story must be transitioned to force_closed; "
        f"got entries: {entries!r}"
    )


def test_force_close_in_progress_from_state_recorded(isolated_log):
    """The force-close state_change records from_state='in_progress' for
    a story currently in_progress."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-fct-fromstate")
    _advance(in_sprint[0], "in_progress")
    sm.force_close("see you next sprint")
    entries = _state_change_entries_for(isolated_log, in_sprint[0])
    last = entries[-1]
    assert last["from_state"] == "in_progress", (
        f"force-close state_change must capture actual prior state; "
        f"got from_state={last.get('from_state')!r}"
    )
    assert last["to_state"] == "force_closed"


def test_force_close_in_review_story_transitioned(isolated_log):
    """An in_review story gets a state_change → force_closed."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-fct-inrev")
    _advance(in_sprint[0], "in_progress", "in_review")
    sm.force_close("reviewer left the company")
    entries = _state_change_entries_for(isolated_log, in_sprint[0])
    last = entries[-1]
    assert last["to_state"] == "force_closed", (
        f"in_review story must be transitioned to force_closed; "
        f"got entries: {entries!r}"
    )
    assert last["from_state"] == "in_review", (
        f"from_state must be 'in_review'; got {last.get('from_state')!r}"
    )


def test_force_close_planned_from_state_recorded(isolated_log):
    """The force-close state_change records from_state='planned' for a
    story still in planned."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-fct-planfrom")
    sm.force_close("planning collapse")
    for sid in in_sprint:
        entries = _state_change_entries_for(isolated_log, sid)
        last = entries[-1]
        assert last["from_state"] == "planned", (
            f"planned story's force-close must carry from_state='planned'; "
            f"got {last!r}"
        )


def test_force_close_mixed_terminal_and_nonterminal_transitions_only_nonterminal(
        isolated_log):
    """One accepted + one planned in-sprint: only the planned story gets
    a NEW state_change to force_closed. The accepted one does not."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-fct-mixed")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    # in_sprint[1] stays planned
    # Snapshot the existing state_change entries before force_close
    accepted_before = _state_change_entries_for(isolated_log, in_sprint[0])
    accepted_before_count = len(accepted_before)

    sm.force_close("mixed run")

    accepted_after = _state_change_entries_for(isolated_log, in_sprint[0])
    planned_after = _state_change_entries_for(isolated_log, in_sprint[1])

    # The accepted story must not have gained a NEW state_change.
    assert len(accepted_after) == accepted_before_count, (
        f"accepted story must not be re-transitioned by force-close;\n"
        f"before: {accepted_before_count}\n"
        f"after: {len(accepted_after)}"
    )
    # The planned story must have gained exactly one state_change to
    # force_closed.
    assert len(planned_after) >= 1, (
        f"planned story must be transitioned to force_closed"
    )
    assert planned_after[-1]["to_state"] == "force_closed"


def test_force_close_multiple_nonterminals_all_transitioned(isolated_log):
    """Three in-sprint stories in different non-terminal states are ALL
    transitioned to force_closed."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-fct-multinon")
    _advance(in_sprint[0], "in_progress")
    _advance(in_sprint[1], "in_progress", "in_review")
    # in_sprint[2] stays planned

    sm.force_close("bulk force-close")

    for sid in in_sprint:
        entries = _state_change_entries_for(isolated_log, sid)
        assert entries, (
            f"story {sid!r} must have state_change entries"
        )
        assert entries[-1]["to_state"] == "force_closed", (
            f"story {sid!r}'s last state_change must be force_closed; "
            f"got {entries[-1]!r}"
        )


def test_force_close_deferred_stories_not_transitioned(isolated_log):
    """Deferred (non-in-sprint) stories are NOT transitioned by force-close."""
    import sm
    sids, in_sprint, deferred = _seed_full(n_stories=5, cut_at=3,
                                           iteration_id="iter-fct-defer")
    # No deferred story has any state_change at start.
    sm.force_close("close everything in sprint")
    for sid in deferred:
        entries = _state_change_entries_for(isolated_log, sid)
        assert entries == [], (
            f"deferred story {sid!r} must NOT receive a force-close "
            f"state_change; got: {entries!r}"
        )


def test_force_close_state_change_entries_have_canonical_shape(isolated_log):
    """Each force-close state_change entry has id/type/timestamp +
    story_id, from_state, to_state ('force_closed')."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-fct-shape")
    sm.force_close("shape check")
    for sid in in_sprint:
        entries = _state_change_entries_for(isolated_log, sid)
        e = entries[-1]
        for k in ("id", "type", "timestamp",
                  "story_id", "from_state", "to_state"):
            assert k in e, (
                f"force-close state_change entry missing {k!r}; got {e!r}"
            )
        assert e["type"] == "story_state_change"
        assert e["story_id"] == sid
        assert e["to_state"] == "force_closed"


def test_force_close_state_change_entries_precede_iteration_close(
        isolated_log):
    """All force-close state_change entries appear BEFORE the
    iteration_close entry in the log."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-fct-order")
    sm.force_close("ordered close")
    lines = isolated_log.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    # Find the iteration_close index.
    close_idx = next(
        i for i, e in enumerate(entries) if e.get("type") == "iteration_close"
    )
    # Every state_change entry for in-sprint stories must come earlier.
    for i, e in enumerate(entries):
        if (e.get("type") == "story_state_change"
                and e.get("story_id") in in_sprint
                and e.get("to_state") == "force_closed"):
            assert i < close_idx, (
                f"force_closed state_change at idx {i} must precede "
                f"iteration_close at idx {close_idx}"
            )


# ===========================================================================
# Skip already-terminal stories (5)
# ===========================================================================


def test_force_close_accepted_story_not_retransitioned(isolated_log):
    """An accepted in-sprint story must NOT receive a force-close
    state_change."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-skip-acc")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    # Snapshot entries for the accepted story (it has 3 prior state_change
    # entries from _advance).
    before = _state_change_entries_for(isolated_log, in_sprint[0])
    before_count = len(before)

    sm.force_close("skip terminal")

    after = _state_change_entries_for(isolated_log, in_sprint[0])
    assert len(after) == before_count, (
        f"accepted story must not receive a new state_change on "
        f"force-close;\nbefore={before_count} after={len(after)}"
    )


def test_force_close_rejected_story_not_retransitioned(isolated_log):
    """A rejected in-sprint story must NOT receive a force-close
    state_change."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-skip-rej")
    _advance(in_sprint[0], "in_progress", "in_review", "rejected")
    before = _state_change_entries_for(isolated_log, in_sprint[0])
    before_count = len(before)

    sm.force_close("skip rejected")

    after = _state_change_entries_for(isolated_log, in_sprint[0])
    assert len(after) == before_count, (
        f"rejected story must not receive a new state_change on "
        f"force-close;\nbefore={before_count} after={len(after)}"
    )


def test_force_close_one_accepted_one_planned_only_planned_gets_change(
        isolated_log):
    """Two-story sprint: one accepted, one planned. Only the planned
    story gets a force-close state_change entry."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-skip-ap")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    # in_sprint[1] is planned
    accepted_before = len(_state_change_entries_for(
        isolated_log, in_sprint[0]))
    planned_before = len(_state_change_entries_for(
        isolated_log, in_sprint[1]))

    sm.force_close("split")

    accepted_after = len(_state_change_entries_for(
        isolated_log, in_sprint[0]))
    planned_after = len(_state_change_entries_for(
        isolated_log, in_sprint[1]))

    assert accepted_after == accepted_before, (
        "accepted story: state_change count unchanged"
    )
    assert planned_after == planned_before + 1, (
        f"planned story: exactly one new state_change; "
        f"before={planned_before} after={planned_after}"
    )


def test_force_close_all_terminal_writes_no_state_changes(isolated_log):
    """If every in-sprint story is already terminal, force-close writes
    NO new state_change entries — only the iteration_close entry."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-skip-allterm")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    # Snapshot the per-story state_change counts.
    pre = {sid: len(_state_change_entries_for(isolated_log, sid))
           for sid in in_sprint}

    sm.force_close("nothing to force")

    for sid in in_sprint:
        post_count = len(_state_change_entries_for(isolated_log, sid))
        assert post_count == pre[sid], (
            f"story {sid!r}: terminal, must not get new state_change;\n"
            f"before={pre[sid]} after={post_count}"
        )


def test_force_close_state_change_does_not_duplicate_per_call(isolated_log):
    """For a single planned story, force-close writes exactly ONE new
    state_change entry (not multiple)."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=1, cut_at=1,
                                    iteration_id="iter-skip-once")
    before = len(_state_change_entries_for(isolated_log, in_sprint[0]))
    sm.force_close("once")
    after = len(_state_change_entries_for(isolated_log, in_sprint[0]))
    assert after == before + 1, (
        f"force-close must write exactly one new state_change per "
        f"non-terminal story; before={before} after={after}"
    )


# ===========================================================================
# iteration_close entry shape (8) — closed_by="force-close", reason
# verbatim, counts include force_closed_count
# ===========================================================================


def test_force_close_returns_iteration_close_entry(isolated_log):
    """force_close returns an iteration_close entry dict."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-ret-ic")
    entry = sm.force_close("returns ic")
    assert isinstance(entry, dict)
    assert entry["type"] == "iteration_close", (
        f"entry type must be 'iteration_close'; got {entry['type']!r}"
    )


def test_force_close_closed_by_is_force_close(isolated_log):
    """The iteration_close entry's closed_by is the literal 'force-close'."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-ret-cb")
    entry = sm.force_close("any reason")
    assert entry["closed_by"] == "force-close", (
        f"force-close path: closed_by must be 'force-close'; "
        f"got {entry['closed_by']!r}"
    )


def test_force_close_reason_recorded_verbatim(isolated_log):
    """The reason text appears verbatim in the iteration_close entry."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-ret-rv")
    verbatim = "AWS region us-east-1 outage; resume next iteration"
    entry = sm.force_close(verbatim)
    assert entry["reason"] == verbatim, (
        f"reason must be recorded verbatim;\n"
        f"expected: {verbatim!r}\n"
        f"got: {entry['reason']!r}"
    )


def test_force_close_force_closed_count_reflects_transitions(isolated_log):
    """force_closed_count in the entry equals the number of stories
    transitioned to force_closed (planned + in_progress + in_review)."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ret-fcc")
    # All three start planned, all three will be force-closed.
    entry = sm.force_close("count check")
    assert entry["force_closed_count"] == 3, (
        f"all three planned → force_closed_count must be 3; "
        f"got {entry['force_closed_count']!r}"
    )


def test_force_close_mixed_terminal_counts(isolated_log):
    """One accepted + one rejected + one planned: counts split correctly."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ret-mix")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    # in_sprint[2] stays planned -> will be force-closed
    entry = sm.force_close("mixed counts")
    assert entry["accepted_count"] == 1, (
        f"accepted_count must be 1; got {entry['accepted_count']!r}"
    )
    assert entry["rejected_count"] == 1, (
        f"rejected_count must be 1; got {entry['rejected_count']!r}"
    )
    assert entry["force_closed_count"] == 1, (
        f"force_closed_count must be 1; got {entry['force_closed_count']!r}"
    )


def test_force_close_per_requirement_status_force_closed_as_rejected(
        isolated_log):
    """Per Story 17's aggregation rule, force_closed stories count as
    rejected at the requirement level. With all stories force-closed,
    every requirement should be 'rejected'."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-ret-prs")
    entry = sm.force_close("agg as rejected")
    assert entry["per_requirement_status"] == {"req-1": "rejected"}, (
        f"all force_closed → req-1 status 'rejected'; "
        f"got {entry['per_requirement_status']!r}"
    )


def test_force_close_entry_has_canonical_fields(isolated_log):
    """The iteration_close entry produced by force-close carries every
    documented top-level field."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-ret-fields")
    entry = sm.force_close("field check")
    required = (
        "id", "type", "timestamp",
        "iteration_id", "handoff_file_path", "per_requirement_status",
        "closed_by", "reason",
        "accepted_count", "rejected_count", "force_closed_count",
    )
    for k in required:
        assert k in entry, (
            f"iteration_close entry missing required field {k!r}; "
            f"got keys: {sorted(entry.keys())!r}"
        )


def test_force_close_returned_entry_is_last_in_log(isolated_log):
    """The returned entry equals the LAST entry in the log."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-ret-tail")
    entry = sm.force_close("tail check")
    last_line = isolated_log.read_text(encoding="utf-8").splitlines()[-1]
    last_entry = json.loads(last_line)
    assert last_entry == entry, (
        "returned entry must equal the last appended log entry"
    )


# ===========================================================================
# Handoff JSON shape (6) — force-closed stories marked, closed_by/reason
# ===========================================================================


def test_force_close_handoff_file_written(isolated_log):
    """force-close writes a handoff JSON file at the conventional path."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-hof-written")
    sm.force_close("written")
    expected = _handoff_path_for(isolated_log, "iter-hof-written")
    assert expected.exists(), (
        f"handoff JSON must exist at {expected!s}"
    )


def test_force_close_handoff_stories_have_force_closed_outcome(isolated_log):
    """In-sprint stories transitioned by force-close appear in handoff
    JSON with outcome='force_closed'."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-hof-outcome")
    sm.force_close("outcome check")
    handoff_path = _handoff_path_for(isolated_log, "iter-hof-outcome")
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    outcome_map = {s["story_id"]: s["outcome"] for s in handoff["stories"]}
    for sid in in_sprint:
        assert outcome_map[sid] == "force_closed", (
            f"story {sid!r}: outcome must be 'force_closed'; "
            f"got {outcome_map[sid]!r}"
        )


def test_force_close_handoff_per_requirement_status_rejected(isolated_log):
    """The handoff JSON's per_requirement_status marks force-closed reqs
    as 'rejected' (Story 17 rule)."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-hof-prs")
    sm.force_close("prs check")
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-hof-prs").read_text(
            encoding="utf-8")
    )
    assert handoff["per_requirement_status"] == {"req-1": "rejected"}, (
        f"force-closed req must be 'rejected' in handoff; "
        f"got {handoff['per_requirement_status']!r}"
    )


def test_force_close_handoff_mixed_outcomes(isolated_log):
    """Handoff JSON's stories list reflects accepted/rejected/force_closed
    distinctly when the sprint mixes terminals + non-terminals."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-hof-mix")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    # in_sprint[2] stays planned -> force-closed
    sm.force_close("mixed outcomes")
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-hof-mix").read_text(
            encoding="utf-8")
    )
    outcomes = {s["story_id"]: s["outcome"] for s in handoff["stories"]}
    assert outcomes[in_sprint[0]] == "accepted"
    assert outcomes[in_sprint[1]] == "rejected"
    assert outcomes[in_sprint[2]] == "force_closed"


def test_force_close_handoff_no_extra_fields_break_known_shape(isolated_log):
    """The handoff JSON file carries iteration_id, iteration_goal,
    per_requirement_status, stories, closed_at — the Story 18 shape."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-hof-shape")
    sm.force_close("shape")
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-hof-shape").read_text(
            encoding="utf-8")
    )
    for k in ("iteration_id", "iteration_goal",
              "per_requirement_status", "stories", "closed_at"):
        assert k in handoff, (
            f"handoff JSON missing required field {k!r}; "
            f"got keys: {sorted(handoff.keys())!r}"
        )


def test_force_close_handoff_includes_every_in_sprint_id(isolated_log):
    """Handoff JSON stories list contains every in-sprint story_id."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-hof-allids")
    sm.force_close("all ids")
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-hof-allids").read_text(
            encoding="utf-8")
    )
    ids = {s["story_id"] for s in handoff["stories"]}
    for sid in in_sprint:
        assert sid in ids, (
            f"handoff must contain in-sprint story_id {sid!r}; "
            f"got {ids!r}"
        )


# ===========================================================================
# CLI surface (7) — force-close subcommand, --reason flag, exit codes
# ===========================================================================


def test_cli_force_close_subcommand_recognized(cli_log):
    """`python -m sm force-close --reason "x"` is NOT 'unknown command'."""
    log_path, env = cli_log
    result = _run_cli(env, "force-close", "--reason", "x")
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'force-close' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_force_close_help_exits_zero(cli_log):
    """`python -m sm force-close --help` exits 0."""
    log_path, env = cli_log
    result = _run_cli(env, "force-close", "--help")
    assert result.returncode == 0, (
        f"force-close --help must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_force_close_exits_zero_on_success(cli_log):
    """`python -m sm force-close --reason "x"` exits 0 on success."""
    log_path, env = cli_log
    # Seed a force-closeable state via in-process helpers redirected at
    # log_path. All stories planned → force-close transitions them all.
    _run_at(
        log_path, lambda: _seed_full(n_stories=3, cut_at=3,
                                     iteration_id="iter-cli-ok")[1],
    )
    result = _run_cli(env, "force-close", "--reason", "operator pulled the plug")
    assert result.returncode == 0, (
        f"force-close on a clean state must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_force_close_missing_reason_nonzero(cli_log):
    """`python -m sm force-close` (no flag/value) exits non-zero."""
    log_path, env = cli_log
    # Seed a force-closeable state so the only failure is missing arg.
    _run_at(
        log_path, lambda: _seed_full(n_stories=2, cut_at=2,
                                     iteration_id="iter-cli-noreason")[1],
    )
    result = _run_cli(env, "force-close")
    assert result.returncode != 0, (
        f"force-close without --reason must exit non-zero;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_force_close_empty_reason_nonzero(cli_log):
    """`python -m sm force-close --reason ""` exits non-zero
    (empty reason fails validation)."""
    log_path, env = cli_log
    _run_at(
        log_path, lambda: _seed_full(n_stories=2, cut_at=2,
                                     iteration_id="iter-cli-empty")[1],
    )
    result = _run_cli(env, "force-close", "--reason", "")
    assert result.returncode != 0, (
        f"force-close with empty reason must exit non-zero;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_force_close_whitespace_reason_nonzero(cli_log):
    """`python -m sm force-close --reason "   "` exits non-zero."""
    log_path, env = cli_log
    _run_at(
        log_path, lambda: _seed_full(n_stories=2, cut_at=2,
                                     iteration_id="iter-cli-ws")[1],
    )
    result = _run_cli(env, "force-close", "--reason", "   ")
    assert result.returncode != 0, (
        f"force-close with whitespace-only reason must exit non-zero;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_force_close_no_active_iteration_nonzero(cli_log):
    """Empty log + force-close → non-zero exit (no active iteration)."""
    log_path, env = cli_log
    result = _run_cli(env, "force-close", "--reason", "abandon all hope")
    assert result.returncode != 0, (
        f"force-close on empty log must exit non-zero;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ===========================================================================
# Failure invariants (5) — log unchanged + no handoff on every error
# ===========================================================================


def test_failure_invariant_type_error_log_unchanged(isolated_log):
    """TypeError on bad reason type → log bytes unchanged."""
    import sm
    _seed_full(n_stories=2, cut_at=2, iteration_id="iter-fi-typ")
    before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm.force_close(123)
    after = isolated_log.read_bytes()
    assert before == after


def test_failure_invariant_empty_reason_log_unchanged(isolated_log):
    """ForceCloseError on empty reason → log bytes unchanged."""
    import sm
    _seed_full(n_stories=2, cut_at=2, iteration_id="iter-fi-empty")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("")
    after = isolated_log.read_bytes()
    assert before == after


def test_failure_invariant_no_iter_no_handoff(isolated_log, tmp_path):
    """No active iter + failed force-close → no handoff file appears."""
    import sm
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("ok reason")
    handoffs = [p.name for p in tmp_path.iterdir()
                if p.name.startswith("close_handoff_")
                and p.name.endswith(".json")]
    assert handoffs == [], (
        f"no handoff file should appear; got: {handoffs!r}"
    )


def test_failure_invariant_no_cut_no_state_changes_written(isolated_log):
    """Open + decompose + no cut → ForceCloseError, and NO state_change
    entries written for any backlog story (defense in depth — failure
    invariant must hold strictly)."""
    import sm
    _open_iteration(iteration_id="iter-fi-nocut")
    sids = _seed_backlog(n=3, iteration_id="iter-fi-nocut")
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("not yet cut")
    for sid in sids:
        entries = _state_change_entries_for(isolated_log, sid)
        assert entries == [], (
            f"failed force-close (no cut) must not write any state_change "
            f"entries; story {sid!r} has: {entries!r}"
        )


def test_failure_invariant_does_not_call_append_entry_on_validation_fail(
        isolated_log, monkeypatch):
    """Validation failures must NOT call _append_entry."""
    import sm
    _seed_full(n_stories=2, cut_at=2, iteration_id="iter-fi-noappend")

    calls = []

    def _spy(entry):
        calls.append(entry)
        raise AssertionError(
            "force_close must not call _append_entry on validation failure"
        )

    monkeypatch.setattr(sm, "_append_entry", _spy)
    # Empty reason → ForceCloseError before any writes.
    with pytest.raises(sm.ForceCloseError):
        sm.force_close("")
    assert calls == [], (
        f"failed force-close (validation) must not call _append_entry; "
        f"got {len(calls)} calls"
    )


# ===========================================================================
# derive_state integration (5) — post force-close, state reflects close
# ===========================================================================


def test_derive_state_active_iteration_none_after_force_close(isolated_log):
    """After force-close, active_iteration is None."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-ds-fc-act")
    sm.force_close("derive_state check")
    state = sm.derive_state()
    assert state["active_iteration"] is None, (
        f"active_iteration must be None after force-close; "
        f"got {state['active_iteration']!r}"
    )


def test_derive_state_close_status_populated_after_force_close(isolated_log):
    """After force-close, close_status is populated."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-ds-fc-status")
    sm.force_close("populated")
    state = sm.derive_state()
    assert state["close_status"] is not None, (
        "close_status must be populated after force-close"
    )


def test_derive_state_close_status_reason_after_force_close(isolated_log):
    """close_status carries the supplied reason verbatim."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=2, cut_at=2,
                                    iteration_id="iter-ds-fc-reason")
    sm.force_close("budget exhausted")
    state = sm.derive_state()
    assert state["close_status"]["reason"] == "budget exhausted", (
        f"close_status reason must reflect supplied reason; "
        f"got {state['close_status']['reason']!r}"
    )


def test_derive_state_close_status_force_closed_count_after_force_close(
        isolated_log):
    """close_status carries force_closed_count derived from the entry."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ds-fc-fcc")
    # All planned → all force-closed
    sm.force_close("count check")
    state = sm.derive_state()
    cs = state["close_status"]
    assert cs["force_closed_count"] == 3, (
        f"force_closed_count must be 3; got {cs['force_closed_count']!r}"
    )


def test_derive_state_story_states_force_closed_after_force_close(
        isolated_log):
    """After force-close, story_states maps every transitioned in-sprint
    story to 'force_closed'."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ds-fc-states")
    _advance(in_sprint[0], "in_progress")
    _advance(in_sprint[1], "in_progress", "in_review", "accepted")
    # in_sprint[2] planned
    sm.force_close("derive states")
    state = sm.derive_state()
    ss = state["story_states"]
    # Story 0 was in_progress → force_closed
    assert ss[in_sprint[0]] == "force_closed", (
        f"story {in_sprint[0]!r} (was in_progress) must be force_closed; "
        f"got {ss[in_sprint[0]]!r}"
    )
    # Story 1 was accepted → stays accepted (terminal, skipped)
    assert ss[in_sprint[1]] == "accepted", (
        f"story {in_sprint[1]!r} (was accepted) must stay accepted; "
        f"got {ss[in_sprint[1]]!r}"
    )
    # Story 2 was planned → force_closed
    assert ss[in_sprint[2]] == "force_closed", (
        f"story {in_sprint[2]!r} (was planned) must be force_closed; "
        f"got {ss[in_sprint[2]]!r}"
    )
