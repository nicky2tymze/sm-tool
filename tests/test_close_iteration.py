"""Story 18 — pin the contract of `sm.close_iteration` + the `close` CLI.

Story 18 (Sprint 2, size L) is the iteration-close handoff producer. It
introduces a new public function, a new typed error, a new exit code, a
new CLI subcommand, AND a new sidecar file (the close handoff JSON):

    close_iteration() -> dict
    class IterationCloseError(ValueError)
    EXIT_CLOSE = 11
    python -m sm close

What this file pins:

  - Function signature and shape:
      `close_iteration()` — PUBLIC, callable, in `sm.__all__`, importable
      as `from sm import close_iteration`. Takes no positional/keyword
      args. Returns the appended `iteration_close` log entry dict.

  - Typed error class:
      `sm.IterationCloseError` exists, subclasses `ValueError`, is in
      `sm.__all__`. Subclassing ValueError keeps existing `except
      ValueError` callers compatible.

  - EXIT_CLOSE constant:
      `sm.EXIT_CLOSE == 11`, distinct from every other declared exit code.

  - Validation cascade — each failure raises `IterationCloseError`, no
    log write, no handoff file:
      * no active iteration -> "no active iteration"
      * no story_backlog -> can't close what wasn't decomposed
      * no sprint_cut -> can't close before cutting
      * in-sprint stories not all terminal -> error names every
        non-terminal story_id with its current state

  - Happy path: every in-sprint story in a terminal state -> close
    succeeds, appends a single `iteration_close` entry, writes a single
    handoff JSON file at `LOG_PATH.parent / "close_handoff_<id>.json"`,
    returns the entry.

  - `iteration_close` log entry shape (matches Story 4's reader):
      {
        "id", "type": "iteration_close", "timestamp" (auto-stamped),
        "iteration_id": "<id>",
        "handoff_file_path": "<absolute string>",
        "per_requirement_status": {"req-1": "accepted", ...},
        "closed_by": "operator",
        "reason": None,
        "accepted_count": <int>,
        "rejected_count": <int>,
        "force_closed_count": <int>,
      }

  - Handoff JSON file shape:
      {
        "iteration_id": "<id>",
        "iteration_goal": "<copied from iteration_open>",
        "per_requirement_status": {"req-1": "accepted", ...},
        "stories": [
          {"story_id": "<id>", "sequence": 1, "title": "...",
           "requirement_ids": ["req-1"], "outcome": "accepted"},
          ...
        ],
        "closed_at": "<ISO 8601 timestamp>"
      }

  - Handoff file path convention:
      `LOG_PATH.parent / "close_handoff_<iteration_id>.json"`. Pinned by
      tests checking the path exists and that the entry's
      `handoff_file_path` field matches that path verbatim.

  - derive_state integration: after a successful close,
    `active_iteration` is None and `close_status` is populated from the
    count fields.

  - Failure invariants: on every failure mode, log bytes unchanged AND
    no handoff JSON file exists in `LOG_PATH.parent`.

  - Sole-other-file invariant: after a successful close, the only files
    that appear in `LOG_PATH.parent` are `log.jsonl` and the handoff
    file (no sidecars, no temp files).

  - CLI surface — `python -m sm close`:
      * Subcommand recognized (not "unknown command").
      * Exits 0 on success.
      * Exits EXIT_CLOSE on `IterationCloseError`.
      * `--help` / `-h` exits 0.
      * Extra positional args -> non-zero exit.

Tests must FAIL on first run — `close_iteration`, `IterationCloseError`,
and `EXIT_CLOSE` do not exist yet, and the CLI doesn't recognize the
`close` subcommand. The Coder downstream implements them to satisfy
these tests.

The CLI invocation contract is `python -m sm close`, hermetically
isolated via the SM_TEST_LOG_PATH env var (the same hook used by every other
Sprint 2 subcommand-level test).
"""

from __future__ import annotations

import datetime as _dt
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

    `requirement_ids_per_story` is an optional list of per-story
    requirement_ids; defaults to ["req-1"] for every story.
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


def _drive_all_in_sprint_to(in_sprint_ids, outcome: str) -> None:
    """Push every in-sprint story all the way to `outcome` (accepted/rejected)."""
    for sid in in_sprint_ids:
        if outcome == "accepted":
            _advance(sid, "in_progress", "in_review", "accepted")
        elif outcome == "rejected":
            _advance(sid, "in_progress", "in_review", "rejected")
        else:
            raise ValueError(f"unsupported outcome {outcome!r}")


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


def _handoff_path_for(log_path: pathlib.Path, iteration_id: str) -> pathlib.Path:
    """Compute the expected handoff JSON path per the Story 18 convention."""
    return log_path.parent / f"close_handoff_{iteration_id}.json"


def _list_dir(p: pathlib.Path) -> list:
    """Sorted list of names directly under `p` (1 level deep)."""
    return sorted([child.name for child in p.iterdir()])


# ===========================================================================
# Smoke (6) — close_iteration exists, callable, public, in __all__, signature
# ===========================================================================


def test_close_iteration_function_exists():
    """sm.close_iteration must exist on the module."""
    import sm
    assert hasattr(sm, "close_iteration"), (
        "sm.close_iteration must exist"
    )


def test_close_iteration_function_is_callable():
    """sm.close_iteration must be callable."""
    import sm
    assert callable(sm.close_iteration), (
        "sm.close_iteration must be callable"
    )


def test_close_iteration_function_is_public():
    """No leading underscore — public API."""
    import sm
    name = sm.close_iteration.__name__
    assert not name.startswith("_"), (
        f"close_iteration must be public; got name {name!r}"
    )
    assert name == "close_iteration"


def test_close_iteration_function_importable_directly():
    """`from sm import close_iteration` succeeds."""
    from sm import close_iteration  # noqa: F401
    assert callable(close_iteration)


def test_close_iteration_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__")
    assert "close_iteration" in sm.__all__, (
        f"close_iteration must be in __all__; got {sm.__all__!r}"
    )


def test_close_iteration_signature_no_required_args():
    """close_iteration takes no required positional args."""
    import sm
    sig = inspect.signature(sm.close_iteration)
    required = [
        p for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    assert len(required) == 0, (
        f"close_iteration must take no required args; got {required!r}"
    )


# ===========================================================================
# IterationCloseError typed (5) — exists, in __all__, ValueError subclass
# ===========================================================================


def test_iteration_close_error_exists():
    """sm.IterationCloseError must exist."""
    import sm
    assert hasattr(sm, "IterationCloseError"), (
        "sm.IterationCloseError must exist"
    )


def test_iteration_close_error_is_value_error_subclass():
    """IterationCloseError narrows ValueError so existing `except ValueError`
    callers keep working."""
    import sm
    assert issubclass(sm.IterationCloseError, ValueError), (
        f"IterationCloseError must subclass ValueError; got bases "
        f"{sm.IterationCloseError.__mro__!r}"
    )


def test_iteration_close_error_in_dunder_all():
    """IterationCloseError is exported via __all__."""
    import sm
    assert "IterationCloseError" in sm.__all__, (
        f"IterationCloseError must be in __all__; got {sm.__all__!r}"
    )


def test_iteration_close_error_is_a_class():
    """IterationCloseError is an exception class (not a function / instance)."""
    import sm
    assert isinstance(sm.IterationCloseError, type), (
        f"IterationCloseError must be a class; got "
        f"{sm.IterationCloseError!r}"
    )
    assert issubclass(sm.IterationCloseError, Exception)


def test_iteration_close_error_constructible():
    """IterationCloseError can be raised + caught with a message."""
    import sm
    with pytest.raises(sm.IterationCloseError) as exc_info:
        raise sm.IterationCloseError("test message")
    assert "test message" in str(exc_info.value)


# ===========================================================================
# EXIT_CLOSE constant (3) — exists, == 11, distinct
# ===========================================================================


def test_exit_close_constant_exists():
    """sm.EXIT_CLOSE must exist as a module-level constant."""
    import sm
    assert hasattr(sm, "EXIT_CLOSE"), (
        "sm.EXIT_CLOSE must exist (Story 18 reserves an exit code)"
    )


def test_exit_close_value_is_11():
    """Story 18 reserves exit code 11 for iteration-close failures."""
    import sm
    assert sm.EXIT_CLOSE == 11, (
        f"EXIT_CLOSE must be 11; got {sm.EXIT_CLOSE!r}"
    )


def test_exit_close_distinct_from_other_codes():
    """EXIT_CLOSE must be distinct from every other declared exit code."""
    import sm
    others = (
        sm.EXIT_OK, sm.EXIT_OTHER, sm.EXIT_PATH, sm.EXIT_JSON,
        sm.EXIT_SHAPE, sm.EXIT_DUP_ID, sm.EXIT_SINGLE_ACTIVE,
        sm.EXIT_UNKNOWN_REQ, sm.EXIT_SPRINT_CUT, sm.EXIT_TRANSITION,
    )
    assert sm.EXIT_CLOSE not in others, (
        f"EXIT_CLOSE must be distinct; got {sm.EXIT_CLOSE!r} "
        f"in {others!r}"
    )


# ===========================================================================
# No active iteration (5) — empty log + post-close + IterationCloseError
# ===========================================================================


def test_close_no_active_iteration_empty_log_raises(isolated_log):
    """Empty log → close_iteration raises IterationCloseError."""
    import sm
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_close_no_active_iteration_empty_log_message(isolated_log):
    """The error message names the missing-iteration condition."""
    import sm
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value).lower()
    assert "iteration" in msg, (
        f"error must mention 'iteration'; got: {exc_info.value!s}"
    )


def test_close_no_active_iteration_empty_log_unchanged(isolated_log):
    """Empty log + failed close → log bytes unchanged."""
    import sm
    # Log may not exist yet for empty case
    before = isolated_log.read_bytes() if isolated_log.exists() else b""
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after = isolated_log.read_bytes() if isolated_log.exists() else b""
    assert before == after, "failed close on empty log must not write"


def test_close_no_active_iteration_post_close_raises(isolated_log):
    """After a prior open+close, log has no active iteration → fails."""
    import sm
    # Seed a closed iteration directly via build_entry.
    open_e = sm.build_entry("iteration_open", {
        "iteration_id": "iter-prior",
        "iteration_goal": "prior",
        "requirements": [{"requirement_id": "req-1"}],
    })
    sm._append_entry(open_e)
    close_e = sm.build_entry("iteration_close", {
        "iteration_id": "iter-prior",
        "handoff_file_path": "<test-stub>",
        "per_requirement_status": {"req-1": "accepted"},
        "closed_by": "operator",
        "reason": None,
        "accepted_count": 1,
        "rejected_count": 0,
        "force_closed_count": 0,
    })
    sm._append_entry(close_e)

    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_close_no_active_iteration_no_handoff_file(isolated_log, tmp_path):
    """Failed close on empty log → no handoff JSON file appears."""
    import sm
    before_files = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after_files = sorted(p.name for p in tmp_path.iterdir())
    # Filter: no handoff file should appear.
    handoff_appeared = [
        f for f in after_files
        if f.startswith("close_handoff_") and f.endswith(".json")
    ]
    assert handoff_appeared == [], (
        f"no handoff file should appear on failed close; "
        f"got: {handoff_appeared!r}"
    )


# ===========================================================================
# No backlog (4) — iter open + no decompose -> error, no handoff
# ===========================================================================


def test_close_no_backlog_raises(isolated_log):
    """Iteration open + decompose not run → IterationCloseError."""
    import sm
    _open_iteration(iteration_id="iter-no-backlog")
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_close_no_backlog_log_unchanged(isolated_log):
    """No backlog + failed close → log bytes unchanged."""
    import sm
    _open_iteration(iteration_id="iter-no-backlog")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after = isolated_log.read_bytes()
    assert before == after, "failed close (no backlog) must not write"


def test_close_no_backlog_no_handoff_file(isolated_log, tmp_path):
    """No backlog + failed close → no handoff JSON file appears."""
    import sm
    _open_iteration(iteration_id="iter-no-backlog")
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    handoff_appeared = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith("close_handoff_") and p.name.endswith(".json")
    ]
    assert handoff_appeared == [], (
        f"no handoff file should appear; got: {handoff_appeared!r}"
    )


def test_close_no_backlog_message_meaningful(isolated_log):
    """The error mentions backlog / decompose / stories."""
    import sm
    _open_iteration(iteration_id="iter-no-backlog")
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value).lower()
    assert ("backlog" in msg or "decompose" in msg or "stories" in msg
            or "sprint" in msg), (
        f"error must mention backlog/decompose/stories/sprint; "
        f"got: {exc_info.value!s}"
    )


# ===========================================================================
# No sprint_cut (4) — iter + decompose + no cut → error, no handoff
# ===========================================================================


def test_close_no_sprint_cut_raises(isolated_log):
    """Open + decompose + no cut → IterationCloseError."""
    import sm
    _open_iteration(iteration_id="iter-no-cut")
    _seed_backlog(n=5)
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_close_no_sprint_cut_log_unchanged(isolated_log):
    """No cut + failed close → log bytes unchanged."""
    import sm
    _open_iteration(iteration_id="iter-no-cut")
    _seed_backlog(n=5)
    before = isolated_log.read_bytes()
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after = isolated_log.read_bytes()
    assert before == after, "failed close (no cut) must not write"


def test_close_no_sprint_cut_no_handoff_file(isolated_log, tmp_path):
    """No cut + failed close → no handoff JSON file appears."""
    import sm
    _open_iteration(iteration_id="iter-no-cut")
    _seed_backlog(n=5)
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    handoff_appeared = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith("close_handoff_") and p.name.endswith(".json")
    ]
    assert handoff_appeared == [], (
        f"no handoff file should appear; got: {handoff_appeared!r}"
    )


def test_close_no_sprint_cut_message_meaningful(isolated_log):
    """The error mentions sprint / cut / before."""
    import sm
    _open_iteration(iteration_id="iter-no-cut")
    _seed_backlog(n=5)
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value).lower()
    assert ("sprint" in msg or "cut" in msg or "before" in msg
            or "active" in msg), (
        f"error must mention sprint/cut/before; got: {exc_info.value!s}"
    )


# ===========================================================================
# Non-terminal in-sprint stories (10) — error names the offenders
# ===========================================================================


def test_close_with_one_planned_story_raises(isolated_log):
    """All in-sprint planned → IterationCloseError."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-planned")
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_close_with_one_planned_story_names_it(isolated_log):
    """Error message names the planned (non-terminal) story_id."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-name-planned")
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value)
    for sid in in_sprint:
        assert sid in msg, (
            f"error must name in-sprint planned story_id {sid!r}; "
            f"got: {exc_info.value!s}"
        )


def test_close_with_in_progress_story_raises(isolated_log):
    """An in-sprint story is in_progress → IterationCloseError."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-inprog")
    _advance(in_sprint[0], "in_progress")
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_close_with_in_progress_story_names_it(isolated_log):
    """Error names the in_progress story."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-inprog-named")
    _advance(in_sprint[0], "in_progress")
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    assert in_sprint[0] in str(exc_info.value), (
        f"error must name in_progress story {in_sprint[0]!r}; "
        f"got: {exc_info.value!s}"
    )


def test_close_with_in_review_story_raises(isolated_log):
    """An in-sprint story is in_review → IterationCloseError."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-inreview")
    _advance(in_sprint[0], "in_progress", "in_review")
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_close_with_in_review_story_names_it(isolated_log):
    """Error names the in_review story."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-inreview-named")
    _advance(in_sprint[0], "in_progress", "in_review")
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    assert in_sprint[0] in str(exc_info.value), (
        f"error must name in_review story {in_sprint[0]!r}; "
        f"got: {exc_info.value!s}"
    )


def test_close_mixed_terminals_plus_one_nonterminal_raises(isolated_log):
    """Two stories accepted, one still planned → IterationCloseError."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-mixed-fail")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    # in_sprint[2] stays planned
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_close_mixed_terminals_plus_one_nonterminal_names_offender(isolated_log):
    """The one non-terminal story is named in the error."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-mixed-named")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    # in_sprint[2] stays planned
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value)
    assert in_sprint[2] in msg, (
        f"error must name the remaining planned story {in_sprint[2]!r}; "
        f"got: {exc_info.value!s}"
    )


def test_close_multiple_nonterminals_names_all(isolated_log):
    """Multiple non-terminal in-sprint stories → all are named."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=4, cut_at=4,
                                    iteration_id="iter-multi-nonterm")
    # Three stories planned, one accepted
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    # in_sprint[1], in_sprint[2], in_sprint[3] stay planned
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value)
    for offender in in_sprint[1:]:
        assert offender in msg, (
            f"error must name every non-terminal story; "
            f"missing {offender!r};\n"
            f"got: {exc_info.value!s}"
        )


def test_close_nonterminal_deferred_does_not_block(isolated_log):
    """Deferred stories are NOT in-sprint, so their planned state must
    NOT block close. Only in-sprint stories are gated."""
    import sm
    sids, in_sprint, deferred = _seed_full(n_stories=5, cut_at=3,
                                           iteration_id="iter-deferred-ok")
    # Accept every in-sprint; leave the 2 deferred stories planned.
    _drive_all_in_sprint_to(in_sprint, "accepted")
    # Should succeed despite deferred stories being planned.
    entry = sm.close_iteration()
    assert entry["type"] == "iteration_close"


def test_close_nonterminal_log_unchanged(isolated_log):
    """On non-terminal-story failure → log bytes unchanged, no handoff."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-nonterm-bytes")
    _advance(in_sprint[0], "in_progress")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after = isolated_log.read_bytes()
    assert before == after


def test_close_nonterminal_no_handoff_file(isolated_log, tmp_path):
    """On non-terminal-story failure → no handoff JSON file."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-nonterm-files")
    _advance(in_sprint[0], "in_progress")
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    handoff_path = _handoff_path_for(isolated_log, "iter-nonterm-files")
    assert not handoff_path.exists(), (
        f"no handoff file may exist after failed close; "
        f"got: {handoff_path!s}"
    )


# ===========================================================================
# Happy path — all accepted (10)
# ===========================================================================


def test_close_all_accepted_succeeds(isolated_log):
    """All in-sprint accepted → close returns cleanly."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert isinstance(entry, dict)


def test_close_all_accepted_entry_type(isolated_log):
    """Returned entry has type='iteration_close'."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc-type")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert entry["type"] == "iteration_close", (
        f"entry type must be 'iteration_close'; got {entry['type']!r}"
    )


def test_close_all_accepted_per_requirement_status(isolated_log):
    """All accepted → per_requirement_status is {req-1: 'accepted'}."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc-status")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert entry["per_requirement_status"] == {"req-1": "accepted"}, (
        f"all accepted must yield req-1='accepted'; "
        f"got {entry['per_requirement_status']!r}"
    )


def test_close_all_accepted_counts(isolated_log):
    """All in-sprint accepted → accepted_count=N, others=0."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc-counts")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert entry["accepted_count"] == 3
    assert entry["rejected_count"] == 0
    assert entry["force_closed_count"] == 0


def test_close_all_accepted_handoff_file_exists(isolated_log):
    """All accepted → handoff JSON file is written."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc-file")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoff_path = _handoff_path_for(isolated_log, "iter-allacc-file")
    assert handoff_path.exists(), (
        f"handoff file must exist at {handoff_path!s}"
    )


def test_close_all_accepted_log_grew_by_one(isolated_log):
    """Log gained exactly one entry on a successful close."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc-grow")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    before_lines = isolated_log.read_text().splitlines()
    sm.close_iteration()
    after_lines = isolated_log.read_text().splitlines()
    assert len(after_lines) == len(before_lines) + 1, (
        f"log must gain exactly one entry on close; "
        f"before={len(before_lines)} after={len(after_lines)}"
    )


def test_close_all_accepted_iteration_id_in_entry(isolated_log):
    """The entry carries iteration_id matching the open."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc-id")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert entry["iteration_id"] == "iter-allacc-id", (
        f"entry iteration_id mismatch; got {entry['iteration_id']!r}"
    )


def test_close_all_accepted_closed_by_operator(isolated_log):
    """Normal close path: closed_by='operator'."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc-closer")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert entry["closed_by"] == "operator", (
        f"normal close: closed_by must be 'operator'; "
        f"got {entry['closed_by']!r}"
    )


def test_close_all_accepted_reason_is_none(isolated_log):
    """Normal close path: reason=None (force-close populates this)."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc-reason")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert entry["reason"] is None, (
        f"normal close: reason must be None; got {entry['reason']!r}"
    )


def test_close_all_accepted_returned_entry_is_last_in_log(isolated_log):
    """The returned entry equals the last entry in the log."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allacc-tail")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    last_line = isolated_log.read_text().splitlines()[-1]
    last_entry = json.loads(last_line)
    assert last_entry == entry, (
        "returned entry must equal the last appended log entry"
    )


# ===========================================================================
# Happy path — all rejected (5)
# ===========================================================================


def test_close_all_rejected_succeeds(isolated_log):
    """All in-sprint rejected → close succeeds."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allrej")
    _drive_all_in_sprint_to(in_sprint, "rejected")
    entry = sm.close_iteration()
    assert entry["type"] == "iteration_close"


def test_close_all_rejected_per_requirement_status(isolated_log):
    """All rejected → per_requirement_status is {req-1: 'rejected'}."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allrej-status")
    _drive_all_in_sprint_to(in_sprint, "rejected")
    entry = sm.close_iteration()
    assert entry["per_requirement_status"] == {"req-1": "rejected"}, (
        f"all rejected must yield req-1='rejected'; "
        f"got {entry['per_requirement_status']!r}"
    )


def test_close_all_rejected_counts(isolated_log):
    """All rejected → rejected_count=N, others=0."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allrej-counts")
    _drive_all_in_sprint_to(in_sprint, "rejected")
    entry = sm.close_iteration()
    assert entry["accepted_count"] == 0
    assert entry["rejected_count"] == 3
    assert entry["force_closed_count"] == 0


def test_close_all_rejected_handoff_file_exists(isolated_log):
    """All rejected → handoff JSON file is written."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allrej-file")
    _drive_all_in_sprint_to(in_sprint, "rejected")
    sm.close_iteration()
    handoff_path = _handoff_path_for(isolated_log, "iter-allrej-file")
    assert handoff_path.exists(), (
        f"handoff file must exist at {handoff_path!s}"
    )


def test_close_all_rejected_handoff_stories_outcome(isolated_log):
    """Handoff JSON's stories list carries outcome='rejected' for every story."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-allrej-outcome")
    _drive_all_in_sprint_to(in_sprint, "rejected")
    sm.close_iteration()
    handoff_path = _handoff_path_for(isolated_log, "iter-allrej-outcome")
    handoff = json.loads(handoff_path.read_text())
    in_sprint_outcomes = [
        s["outcome"] for s in handoff["stories"] if s["story_id"] in in_sprint
    ]
    assert all(o == "rejected" for o in in_sprint_outcomes), (
        f"every in-sprint story must show outcome=rejected; "
        f"got {in_sprint_outcomes!r}"
    )


# ===========================================================================
# Happy path — mixed terminals (8)
# ===========================================================================


def test_close_mixed_terminals_succeeds(isolated_log):
    """Mix of accepted + rejected → close succeeds."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-mixed")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "accepted")
    _advance(in_sprint[2], "in_progress", "in_review", "rejected")
    entry = sm.close_iteration()
    assert entry["type"] == "iteration_close"


def test_close_mixed_terminals_counts(isolated_log):
    """Mixed: 2 accepted + 1 rejected → counts reflect the split."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-mixed-counts")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "accepted")
    _advance(in_sprint[2], "in_progress", "in_review", "rejected")
    entry = sm.close_iteration()
    assert entry["accepted_count"] == 2
    assert entry["rejected_count"] == 1
    assert entry["force_closed_count"] == 0


def test_close_mixed_per_requirement_single_req_partial_or_rejected(isolated_log):
    """All stories carry req-1; with rejected present, req-1 must be
    'rejected' (rejected rule short-circuits)."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-mixed-req")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "accepted")
    _advance(in_sprint[2], "in_progress", "in_review", "rejected")
    entry = sm.close_iteration()
    assert entry["per_requirement_status"] == {"req-1": "rejected"}, (
        f"with a rejected in the set, req-1 must be 'rejected'; "
        f"got {entry['per_requirement_status']!r}"
    )


def test_close_mixed_terminals_handoff_file_exists(isolated_log):
    """Mixed terminals → handoff JSON file written."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-mixed-file")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    _advance(in_sprint[2], "in_progress", "in_review", "accepted")
    sm.close_iteration()
    handoff_path = _handoff_path_for(isolated_log, "iter-mixed-file")
    assert handoff_path.exists()


def test_close_mixed_two_reqs_split_status(isolated_log):
    """Two requirements; first all accepted, second has a rejected →
    per_requirement_status carries 'accepted' and 'rejected' respectively."""
    import sm
    reqs = [
        {"requirement_id": "req-A", "title": "A", "description": "d",
         "priority": "MUST", "acceptance_criteria": "ac"},
        {"requirement_id": "req-B", "title": "B", "description": "d",
         "priority": "MUST", "acceptance_criteria": "ac"},
    ]
    # 4 stories: 1+2 carry req-A; 3+4 carry req-B. 1-3 accepted; 4 rejected.
    _open_iteration(iteration_id="iter-two-reqs", requirements=reqs)
    sids = _seed_backlog(
        n=4,
        requirement_ids_per_story=[
            ["req-A"], ["req-A"], ["req-B"], ["req-B"],
        ],
    )
    import sm
    sm.sprint_cut(4)
    _advance(sids[0], "in_progress", "in_review", "accepted")
    _advance(sids[1], "in_progress", "in_review", "accepted")
    _advance(sids[2], "in_progress", "in_review", "accepted")
    _advance(sids[3], "in_progress", "in_review", "rejected")

    entry = sm.close_iteration()
    per_req = entry["per_requirement_status"]
    assert per_req["req-A"] == "accepted", (
        f"req-A: all stories accepted → 'accepted'; got {per_req!r}"
    )
    assert per_req["req-B"] == "rejected", (
        f"req-B: one rejected → 'rejected'; got {per_req!r}"
    )


def test_close_mixed_terminals_log_grew_by_one(isolated_log):
    """Mixed terminals: log gains exactly one entry."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-mixed-grow")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    _advance(in_sprint[2], "in_progress", "in_review", "accepted")
    before = len(isolated_log.read_text().splitlines())
    sm.close_iteration()
    after = len(isolated_log.read_text().splitlines())
    assert after == before + 1


def test_close_mixed_handoff_stories_outcome_mix(isolated_log):
    """Handoff JSON's stories list carries per-story outcomes faithfully."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-mixed-outmix")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    _advance(in_sprint[2], "in_progress", "in_review", "accepted")
    sm.close_iteration()
    handoff_path = _handoff_path_for(isolated_log, "iter-mixed-outmix")
    handoff = json.loads(handoff_path.read_text())
    # Build a story_id -> outcome map.
    outcome_map = {s["story_id"]: s["outcome"] for s in handoff["stories"]}
    assert outcome_map[in_sprint[0]] == "accepted"
    assert outcome_map[in_sprint[1]] == "rejected"
    assert outcome_map[in_sprint[2]] == "accepted"


def test_close_mixed_returned_entry_is_last_in_log(isolated_log):
    """Returned entry equals the last log entry (mixed terminals path)."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-mixed-tail")
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    _advance(in_sprint[2], "in_progress", "in_review", "accepted")
    entry = sm.close_iteration()
    last_line = isolated_log.read_text().splitlines()[-1]
    last_entry = json.loads(last_line)
    assert last_entry == entry


# ===========================================================================
# Handoff file shape (10) — iteration_id, goal, per_req_status, stories, etc.
# ===========================================================================


def test_handoff_file_at_expected_path(isolated_log):
    """Handoff file path is LOG_PATH.parent/'close_handoff_<id>.json'."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-path-1")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    expected = isolated_log.parent / "close_handoff_iter-path-1.json"
    assert expected.exists(), (
        f"handoff file must exist at expected path {expected!s}"
    )


def test_handoff_file_path_matches_entry_field(isolated_log):
    """The iteration_close entry's handoff_file_path == actual path string."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-path-match")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    expected = isolated_log.parent / "close_handoff_iter-path-match.json"
    # Compare as Path objects for OS-independence.
    assert pathlib.Path(entry["handoff_file_path"]) == expected, (
        f"entry handoff_file_path must match the on-disk path;\n"
        f"entry: {entry['handoff_file_path']!r}\n"
        f"expected: {expected!s}"
    )


def test_handoff_file_is_valid_json(isolated_log):
    """The handoff file's contents parse as JSON."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-json")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoff_path = _handoff_path_for(isolated_log, "iter-json")
    parsed = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)


def test_handoff_file_contains_iteration_id(isolated_log):
    """Handoff JSON carries iteration_id."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-hid")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-hid").read_text()
    )
    assert handoff["iteration_id"] == "iter-hid"


def test_handoff_file_contains_iteration_goal(isolated_log):
    """Handoff JSON carries iteration_goal copied from iteration_open."""
    import sm
    _open_iteration(iteration_id="iter-goal", goal="A specific test goal")
    _seed_backlog(n=3, iteration_id="iter-goal")
    sm.sprint_cut(3)
    state = sm.derive_state()
    in_sprint = [s["story_id"] for s in state["story_backlog"][:3]]
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-goal").read_text()
    )
    assert handoff["iteration_goal"] == "A specific test goal", (
        f"iteration_goal must be copied verbatim; got "
        f"{handoff.get('iteration_goal')!r}"
    )


def test_handoff_file_contains_per_requirement_status(isolated_log):
    """Handoff JSON carries per_requirement_status mapping."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-pers")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-pers").read_text()
    )
    assert "per_requirement_status" in handoff
    assert isinstance(handoff["per_requirement_status"], dict)
    assert handoff["per_requirement_status"] == {"req-1": "accepted"}


def test_handoff_file_contains_stories_list(isolated_log):
    """Handoff JSON carries a 'stories' list."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-stl")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-stl").read_text()
    )
    assert "stories" in handoff
    assert isinstance(handoff["stories"], list)


def test_handoff_file_stories_have_required_fields(isolated_log):
    """Each handoff story carries story_id, sequence, title,
    requirement_ids, outcome."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-stfields")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-stfields").read_text()
    )
    required = ("story_id", "sequence", "title", "requirement_ids", "outcome")
    for s in handoff["stories"]:
        for field in required:
            assert field in s, (
                f"handoff story missing required field {field!r};\n"
                f"story: {s!r}"
            )


def test_handoff_file_closed_at_iso8601(isolated_log):
    """Handoff JSON's closed_at parses as an ISO-8601 timestamp."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-iso")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-iso").read_text()
    )
    assert "closed_at" in handoff
    # Parses as ISO 8601 — datetime.fromisoformat accepts the format.
    parsed = _dt.datetime.fromisoformat(handoff["closed_at"])
    assert parsed is not None


def test_handoff_file_stories_contain_every_in_sprint_id(isolated_log):
    """Handoff stories list contains every in-sprint story_id."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-stids")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoff = json.loads(
        _handoff_path_for(isolated_log, "iter-stids").read_text()
    )
    handoff_ids = {s["story_id"] for s in handoff["stories"]}
    for sid in in_sprint:
        assert sid in handoff_ids, (
            f"handoff stories must include in-sprint story_id {sid!r};\n"
            f"got: {handoff_ids!r}"
        )


# ===========================================================================
# iteration_close entry shape (10) — content matches Story 4 reader
# ===========================================================================


def test_entry_has_id_type_timestamp(isolated_log):
    """Auto-stamped fields: id, type, timestamp."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-stamp")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    for k in ("id", "type", "timestamp"):
        assert k in entry, f"entry missing auto-stamped field {k!r}"
    assert entry["type"] == "iteration_close"


def test_entry_has_iteration_id(isolated_log):
    """Entry carries iteration_id."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-eid")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert entry["iteration_id"] == "iter-eid"


def test_entry_has_handoff_file_path_string(isolated_log):
    """Entry carries handoff_file_path as a string."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ehp")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert "handoff_file_path" in entry
    assert isinstance(entry["handoff_file_path"], str), (
        f"handoff_file_path must be a string; "
        f"got {entry['handoff_file_path']!r}"
    )


def test_entry_handoff_file_path_is_absolute(isolated_log):
    """Entry's handoff_file_path is an absolute path string."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-eabs")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert pathlib.Path(entry["handoff_file_path"]).is_absolute(), (
        f"handoff_file_path must be absolute; "
        f"got {entry['handoff_file_path']!r}"
    )


def test_entry_has_per_requirement_status(isolated_log):
    """Entry carries per_requirement_status dict."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-eprs")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert "per_requirement_status" in entry
    assert isinstance(entry["per_requirement_status"], dict)


def test_entry_has_closed_by_operator(isolated_log):
    """Normal close: closed_by='operator'."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ecb")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert entry["closed_by"] == "operator"


def test_entry_has_reason_none(isolated_log):
    """Normal close: reason is None."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-er")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert entry["reason"] is None


def test_entry_has_accepted_count_int(isolated_log):
    """Entry carries accepted_count as int."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-eac")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert "accepted_count" in entry
    assert isinstance(entry["accepted_count"], int)
    assert not isinstance(entry["accepted_count"], bool)


def test_entry_has_rejected_count_int(isolated_log):
    """Entry carries rejected_count as int."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-erc")
    _drive_all_in_sprint_to(in_sprint, "rejected")
    entry = sm.close_iteration()
    assert "rejected_count" in entry
    assert isinstance(entry["rejected_count"], int)
    assert not isinstance(entry["rejected_count"], bool)


def test_entry_has_force_closed_count_int(isolated_log):
    """Entry carries force_closed_count as int (zero for normal close)."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-efcc")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    entry = sm.close_iteration()
    assert "force_closed_count" in entry
    assert isinstance(entry["force_closed_count"], int)
    assert entry["force_closed_count"] == 0  # normal close path


# ===========================================================================
# derive_state integration (6) — close_status + active_iteration
# ===========================================================================


def test_derive_state_active_iteration_cleared_after_close(isolated_log):
    """After a successful close, active_iteration is None."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ds-act")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    state = sm.derive_state()
    assert state["active_iteration"] is None, (
        f"active_iteration must be None after close; "
        f"got {state['active_iteration']!r}"
    )


def test_derive_state_close_status_populated_after_close(isolated_log):
    """After close, close_status is not None."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ds-status")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    state = sm.derive_state()
    assert state["close_status"] is not None, (
        "close_status must be populated after close"
    )


def test_derive_state_close_status_carries_closed_by(isolated_log):
    """close_status carries closed_by='operator'."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ds-cb")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    state = sm.derive_state()
    assert state["close_status"]["closed_by"] == "operator"


def test_derive_state_close_status_carries_counts(isolated_log):
    """close_status carries the count fields populated from the entry."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ds-counts")
    # Mix: 2 accepted + 1 rejected
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "accepted")
    _advance(in_sprint[2], "in_progress", "in_review", "rejected")
    sm.close_iteration()
    state = sm.derive_state()
    cs = state["close_status"]
    assert cs["accepted_count"] == 2
    assert cs["rejected_count"] == 1
    assert cs["force_closed_count"] == 0


def test_derive_state_close_status_carries_reason_none(isolated_log):
    """close_status reason=None for normal close path."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ds-reason")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    state = sm.derive_state()
    assert state["close_status"]["reason"] is None


def test_derive_state_can_open_new_iteration_after_close(isolated_log):
    """After close, a new ingest of a different iteration_id should succeed
    (active_iteration cleared)."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-ds-reopen-1")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    # Confirm by directly opening a 2nd iteration via build_entry; should
    # not raise. derive_state should then show the new iteration as active.
    _open_iteration(iteration_id="iter-ds-reopen-2")
    state = sm.derive_state()
    assert state["active_iteration"] is not None
    assert state["active_iteration"]["iteration_id"] == "iter-ds-reopen-2"


# ===========================================================================
# Failure invariants (8) — log unchanged + no handoff on every error
# ===========================================================================


def test_failure_invariant_empty_log_log_bytes_unchanged(isolated_log):
    """Empty log + close → log bytes unchanged (already may not exist)."""
    import sm
    before = isolated_log.read_bytes() if isolated_log.exists() else b""
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after = isolated_log.read_bytes() if isolated_log.exists() else b""
    assert before == after


def test_failure_invariant_empty_log_no_handoff_anywhere(isolated_log, tmp_path):
    """Empty log + close → NO close_handoff_*.json files in dir."""
    import sm
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    handoffs = [p for p in tmp_path.iterdir()
                if p.name.startswith("close_handoff_")]
    assert handoffs == [], (
        f"no handoff file may exist; got: {handoffs!r}"
    )


def test_failure_invariant_no_backlog_log_unchanged(isolated_log):
    """Open but no decompose → log bytes unchanged after failed close."""
    import sm
    _open_iteration(iteration_id="iter-fail-nb")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after = isolated_log.read_bytes()
    assert before == after


def test_failure_invariant_no_backlog_no_handoff(isolated_log, tmp_path):
    """Open but no decompose → no handoff file appears."""
    import sm
    _open_iteration(iteration_id="iter-fail-nb")
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    handoffs = [p for p in tmp_path.iterdir()
                if p.name.startswith("close_handoff_")]
    assert handoffs == []


def test_failure_invariant_no_cut_log_unchanged(isolated_log):
    """Open + decompose + no cut → log unchanged after failed close."""
    import sm
    _open_iteration(iteration_id="iter-fail-nc")
    _seed_backlog(n=5)
    before = isolated_log.read_bytes()
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after = isolated_log.read_bytes()
    assert before == after


def test_failure_invariant_no_cut_no_handoff(isolated_log, tmp_path):
    """Open + decompose + no cut → no handoff file."""
    import sm
    _open_iteration(iteration_id="iter-fail-nc")
    _seed_backlog(n=5)
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    handoffs = [p for p in tmp_path.iterdir()
                if p.name.startswith("close_handoff_")]
    assert handoffs == []


def test_failure_invariant_nonterminal_log_unchanged(isolated_log):
    """Non-terminal in-sprint → log bytes unchanged after failed close."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-fail-nt")
    _advance(in_sprint[0], "in_progress")
    before = isolated_log.read_bytes()
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after = isolated_log.read_bytes()
    assert before == after


def test_failure_invariant_nonterminal_no_handoff(isolated_log, tmp_path):
    """Non-terminal in-sprint → no handoff file."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-fail-nt2")
    _advance(in_sprint[0], "in_progress", "in_review")
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    handoffs = [p for p in tmp_path.iterdir()
                if p.name.startswith("close_handoff_")]
    assert handoffs == []


# ===========================================================================
# Sole-other-file invariant (3) — after success: log.jsonl + handoff_<id>.json
# ===========================================================================


def test_sole_other_file_after_close(isolated_log, tmp_path):
    """After a success, the only files in LOG_PATH.parent are log.jsonl
    and close_handoff_<id>.json. No sidecars allowed."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-sole")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()

    files = _list_dir(tmp_path)
    expected = {"log.jsonl", "close_handoff_iter-sole.json"}
    assert set(files) == expected, (
        f"only log.jsonl and the handoff file may exist after a close;\n"
        f"expected: {sorted(expected)!r}\n"
        f"got: {files!r}"
    )


def test_sole_other_file_no_temp_files(isolated_log, tmp_path):
    """No .tmp/.bak/.swp files left behind after a successful close."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-tmp")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    suspicious = [
        p.name for p in tmp_path.iterdir()
        if p.name.endswith((".tmp", ".bak", ".swp", "~"))
    ]
    assert suspicious == [], (
        f"no temp/backup files may persist; got: {suspicious!r}"
    )


def test_sole_other_file_handoff_named_with_iteration_id(isolated_log, tmp_path):
    """The handoff file's name encodes the iteration_id verbatim."""
    import sm
    sids, in_sprint, _ = _seed_full(n_stories=3, cut_at=3,
                                    iteration_id="iter-name-encoded")
    _drive_all_in_sprint_to(in_sprint, "accepted")
    sm.close_iteration()
    handoffs = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith("close_handoff_") and p.name.endswith(".json")
    ]
    assert handoffs == ["close_handoff_iter-name-encoded.json"], (
        f"handoff filename must encode iteration_id; got: {handoffs!r}"
    )


# ===========================================================================
# CLI (6) — subcommand recognized, exits 0 on success, EXIT_CLOSE on failure
# ===========================================================================


def test_cli_close_subcommand_recognized(cli_log):
    """`python -m sm close` is NOT 'unknown command'."""
    log_path, env = cli_log
    result = _run_cli(env, "close")
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'close' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_close_exits_zero_on_success(cli_log):
    """`python -m sm close` exits 0 on successful close."""
    log_path, env = cli_log
    # Seed a closeable state via in-process helpers redirected at log_path.
    sids_in_sprint = _run_at(
        log_path, lambda: _seed_full(n_stories=3, cut_at=3,
                                     iteration_id="iter-cli-ok")[1],
    )
    _run_at(log_path, _drive_all_in_sprint_to, sids_in_sprint, "accepted")
    result = _run_cli(env, "close")
    assert result.returncode == 0, (
        f"close on a clean state must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_close_exits_exit_close_on_failure(cli_log):
    """`python -m sm close` exits EXIT_CLOSE (11) on IterationCloseError."""
    import sm
    log_path, env = cli_log
    # Empty log → no active iteration → IterationCloseError
    result = _run_cli(env, "close")
    assert result.returncode == sm.EXIT_CLOSE, (
        f"close on empty log must exit EXIT_CLOSE ({sm.EXIT_CLOSE});\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_close_help_exits_zero(cli_log):
    """`python -m sm close --help` exits 0."""
    log_path, env = cli_log
    result = _run_cli(env, "close", "--help")
    assert result.returncode == 0, (
        f"close --help must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_close_extra_args_nonzero(cli_log):
    """`python -m sm close extra-arg` exits non-zero (close takes no args).

    Must also be recognized — pre-Story-18 the CLI returns 'unknown command'
    for any `close ...` invocation. This test pins both: subcommand
    recognized AND extra-arg rejected.
    """
    log_path, env = cli_log
    result = _run_cli(env, "close", "unexpected-positional")
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'close' even when extra args are present;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert result.returncode != 0, (
        f"close with extra arg must exit non-zero;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_close_failure_log_unchanged(cli_log):
    """CLI close failure leaves log bytes unchanged."""
    import sm
    log_path, env = cli_log
    # Seed a non-terminal in-sprint state → close should fail.
    sids_in_sprint = _run_at(
        log_path, lambda: _seed_full(n_stories=3, cut_at=3,
                                     iteration_id="iter-cli-fail")[1],
    )
    _run_at(log_path, _advance, sids_in_sprint[0], "in_progress")

    before = log_path.read_bytes()
    result = _run_cli(env, "close")
    after = log_path.read_bytes()
    assert result.returncode == sm.EXIT_CLOSE, (
        f"CLI close on non-terminal must exit EXIT_CLOSE;\n"
        f"got returncode={result.returncode}"
    )
    assert before == after, "CLI close failure must leave log unchanged"


# ===========================================================================
# Bonus — read-only safety on validation paths
# ===========================================================================


def test_close_does_not_call_append_entry_on_failure(isolated_log, monkeypatch):
    """Validation failures must NOT call _append_entry — the writer is the
    last step on the success path only."""
    import sm
    _seed_full(n_stories=3, cut_at=3, iteration_id="iter-no-append")
    # Leave stories planned -> non-terminal -> failure.

    calls = []
    real_append = sm._append_entry

    def _spy(entry):
        calls.append(entry)
        raise AssertionError(
            "close_iteration must not call _append_entry on failure"
        )

    monkeypatch.setattr(sm, "_append_entry", _spy)
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    assert calls == [], (
        f"failed close must not call _append_entry; got {len(calls)} calls"
    )
    monkeypatch.setattr(sm, "_append_entry", real_append)
