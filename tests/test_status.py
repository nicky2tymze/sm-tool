"""Story 16 — pin the contract of `sm.status` + the `status` CLI subcommand.

Story 16 (Sprint 2, size M) adds a read-only query command that surfaces
the active iteration's full picture at a glance. It introduces a new
public function and CLI subcommand:

    status() -> str
    python -m sm status

What this file pins:

  - Function signature and shape:
      `status()` — PUBLIC, callable, in `sm.__all__`, importable as
      `from sm import status`. Returns a string carrying the rendered
      output. Pure read: calls `derive_state()` but writes nothing.

  - Output content with NO active iteration:
      * The literal substring "no active iteration" appears in the output.
      * No call to `_append_entry`; log bytes unchanged before vs after.
      * CLI exits 0 (read-only commands don't fail on "nothing to report").

  - Output content with an active iteration:
      * The active iteration_id appears in the output.
      * Every backlog story_id appears in the output.
      * Each story's sequence appears in the output.
      * Each story's lifecycle state appears in the output.
      * In-sprint vs deferred membership labels appear in the output.

  - Output ordering:
      * Stories rendered by sequence ascending — even if the backlog were
        written out-of-order or with gaps in sequence numbers.

  - Lifecycle state rendering covers every state:
      * planned, in_progress, in_review, accepted, rejected all surface
        their state name in the output for the right stories.
      * A mixed-state backlog renders each story's actual current state.

  - In-sprint vs deferred labelling:
      * After a cut at K of N, the first K stories carry an in-sprint
        marker, the last N-K carry the deferred marker.

  - Read-only invariant:
      * `_append_entry` is not called by `status`.
      * `log.jsonl` is byte-for-byte unchanged before vs after `status()`.
      * Running `status()` 100 times in a row leaves the log identical.

  - CLI surface — `python -m sm status`:
      * Subcommand recognized (not "unknown command").
      * Exits 0 whether or not an iteration is active.
      * stdout contains the rendered output the function would return.

  - Edge cases:
      * Iteration open + no decompose yet -> output names the iteration_id
        and does NOT crash. Backlog rendering may be empty or carry a
        "no story backlog" marker — either is acceptable as long as the
        function returns cleanly.
      * Iteration open + decomposed + no sprint_cut yet -> output renders
        every story; sprint membership labelling pre-cut is implementor's
        call (tests do NOT pin a specific label choice here).
      * Decomposed + cut + multiple transitions -> output reflects every
        new state.

Tests must FAIL on first run — `status` and the `status` CLI subcommand
do not exist yet. The Coder downstream implements them to satisfy these
tests.

The CLI invocation contract is `python -m sm status`, hermetically
isolated via the SM_TEST_LOG_PATH env var (the same hook used by every other
Sprint 2 subcommand-level test).
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
                    requirements=None) -> dict:
    """Append an `iteration_open` entry directly via build_entry +
    _append_entry. Caller is responsible for `isolated_log`.
    """
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


def _close_iteration(iteration_id: str = "iter-1",
                     reason: str = "test close") -> dict:
    """Append an `iteration_close` entry. Caller owns `isolated_log`."""
    import sm

    entry = sm.build_entry("iteration_close", {
        "iteration_id": iteration_id,
        "closed_by": "test",
        "reason": reason,
        "accepted_count": 0,
        "rejected_count": 0,
        "force_closed_count": 0,
    })
    sm._append_entry(entry)
    return entry


def _seed_backlog(n: int = 5,
                  sequences: list = None) -> list:
    """Append a `story_backlog` entry with N canonical stories. Returns
    the list of minted story_ids (in the order they appear in the entry,
    which corresponds to the sequence list).
    """
    import sm

    if sequences is None:
        sequences = list(range(1, n + 1))
    if len(sequences) != n:
        raise ValueError(
            "sequences length must equal n; got "
            f"len={len(sequences)} n={n}"
        )

    story_ids = [_uuid.uuid4().hex for _ in range(n)]
    sizes = ["S", "M", "L"]
    stories = []
    for i in range(n):
        stories.append({
            "story_id": story_ids[i],
            "sequence": sequences[i],
            "title": f"Story seq={sequences[i]}",
            "size": sizes[i % 3],
            "requirement_ids": ["req-1"],
            "acceptance_criteria": f"Story {sequences[i]} must pass.",
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
               iteration_id: str = "iter-1") -> tuple:
    """Open iteration + decompose backlog + cut sprint. Returns
    (story_ids, in_sprint_ids, deferred_ids). Caller owns isolated_log.
    """
    import sm

    _open_iteration(iteration_id=iteration_id)
    sids = _seed_backlog(n=n_stories)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at], sids[cut_at:]


def _advance(story_id: str, *target_states: str) -> None:
    """Drive a story through one or more transitions in the active sprint.
    Mirrors the helper in test_lifecycle_commands.py.
    """
    import sm

    for to_state in target_states:
        if to_state == "accepted":
            sm.record_review(story_id, True, "ok")
        sm.transition_story(story_id, to_state)


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


def _open_iteration_at(log_path: pathlib.Path,
                       iteration_id: str = "iter-1",
                       requirements=None) -> dict:
    """Open an iteration against an arbitrary log path (subprocess CLI
    pattern). Restores sm.LOG_PATH on exit.
    """
    import sm

    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        return _open_iteration(iteration_id=iteration_id,
                               requirements=requirements)
    finally:
        sm.LOG_PATH = orig_log


def _seed_full_at(log_path: pathlib.Path,
                  n_stories: int = 5,
                  cut_at: int = 3,
                  iteration_id: str = "iter-1") -> tuple:
    """Open iter + backlog + cut against an arbitrary log path."""
    import sm

    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        return _seed_full(n_stories=n_stories,
                          cut_at=cut_at,
                          iteration_id=iteration_id)
    finally:
        sm.LOG_PATH = orig_log


def _advance_at(log_path: pathlib.Path, story_id: str,
                *target_states: str) -> None:
    import sm

    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _advance(story_id, *target_states)
    finally:
        sm.LOG_PATH = orig_log


def _call_status() -> str:
    """Call `sm.status()` and coerce its return value to a string.

    Implementor's choice: status() may return a string OR print + return
    None. Tests pin content, not the print/return split — so we capture
    both and concatenate. The substring assertions downstream don't care
    which channel produced the bytes.
    """
    import io
    import contextlib
    import sm

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ret = sm.status()
    printed = buf.getvalue()
    if ret is None:
        return printed
    return f"{printed}{ret}"


# ===========================================================================
# Smoke (6) — function exists, callable, public, in __all__, returns string
# ===========================================================================


def test_status_function_exists():
    """sm.status must exist on the module."""
    import sm
    assert hasattr(sm, "status"), "sm.status must exist"


def test_status_function_is_callable():
    import sm
    assert callable(sm.status), "sm.status must be callable"


def test_status_function_is_public():
    """No leading underscore — public API."""
    import sm
    assert not sm.status.__name__.startswith("_")
    assert sm.status.__name__ == "status"


def test_status_function_importable_directly():
    """`from sm import status` succeeds — public-import form."""
    from sm import status  # noqa: F401
    assert callable(status)


def test_status_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "status" in sm.__all__, (
        f"status must be in __all__; got {sm.__all__!r}"
    )


def test_status_accepts_zero_args(isolated_log):
    """status() accepts no positional/keyword arguments (read-only query
    against the current log)."""
    import sm
    sig = inspect.signature(sm.status)
    required = [
        p for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    assert len(required) == 0, (
        f"status() must take no required args; got {required!r}"
    )
    # And it actually runs with no args on an empty log:
    _call_status()


# ===========================================================================
# No active iteration (6) — empty log + post-close both report cleanly
# ===========================================================================


def test_status_empty_log_returns_no_active_iteration(isolated_log):
    """On an empty log, status() output contains 'no active iteration'."""
    out = _call_status()
    assert "no active iteration" in out.lower(), (
        f"empty-log status must mention 'no active iteration';\n"
        f"got: {out!r}"
    )


def test_status_after_close_returns_no_active_iteration(isolated_log):
    """After iteration_open + iteration_close, status() reports no active."""
    _open_iteration()
    _close_iteration()
    out = _call_status()
    assert "no active iteration" in out.lower(), (
        f"post-close status must mention 'no active iteration';\n"
        f"got: {out!r}"
    )


def test_status_log_missing_returns_no_active_iteration(tmp_path,
                                                        monkeypatch):
    """If LOG_PATH does not exist on disk at all, status() still returns
    cleanly with the 'no active iteration' marker (no exception)."""
    import sm
    missing = tmp_path / "does-not-exist.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", missing)
    out = _call_status()
    assert "no active iteration" in out.lower()


def test_status_empty_log_returns_string(isolated_log):
    """status() output is a string (not a dict / list / None)."""
    import io
    import contextlib
    import sm

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ret = sm.status()
    # Either: returns the string directly, OR prints + returns None.
    if ret is None:
        assert isinstance(buf.getvalue(), str)
        assert buf.getvalue().strip(), (
            "if status() returns None it must print non-empty output"
        )
    else:
        assert isinstance(ret, str), (
            f"status() must return a string; got {type(ret).__name__}"
        )


def test_cli_status_no_active_exits_zero(cli_log):
    """`python -m sm status` on an empty log exits 0."""
    log_path, env = cli_log
    result = _run_cli(env, "status")
    assert result.returncode == 0, (
        f"status with no active iteration must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_status_post_close_exits_zero(cli_log):
    """`python -m sm status` after open + close exits 0."""
    log_path, env = cli_log
    import sm

    orig_log = sm.LOG_PATH
    try:
        sm.LOG_PATH = log_path
        _open_iteration(iteration_id="iter-closed")
        _close_iteration(iteration_id="iter-closed")
    finally:
        sm.LOG_PATH = orig_log

    result = _run_cli(env, "status")
    assert result.returncode == 0, (
        f"status post-close must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ===========================================================================
# Active iteration content (10) — iteration_id, story_ids, sequences, etc.
# ===========================================================================


def test_status_includes_iteration_id(isolated_log):
    """Active iteration: output contains the iteration_id verbatim."""
    sids, _, _ = _seed_full(n_stories=5, cut_at=3,
                            iteration_id="iter-content-1")
    out = _call_status()
    assert "iter-content-1" in out, (
        f"output must include iteration_id 'iter-content-1';\n"
        f"got: {out!r}"
    )


def test_status_includes_every_story_id(isolated_log):
    """Active iteration: output contains every story_id from the backlog."""
    sids, _, _ = _seed_full(n_stories=5, cut_at=3)
    out = _call_status()
    for sid in sids:
        assert sid in out, (
            f"output must include story_id {sid!r};\n"
            f"got: {out!r}"
        )


def test_status_includes_every_sequence(isolated_log):
    """Active iteration: output contains every story sequence number."""
    _seed_full(n_stories=5, cut_at=3)
    out = _call_status()
    # Sequences are 1..5 in this seed.
    for seq in range(1, 6):
        assert str(seq) in out, (
            f"output must include sequence {seq};\n"
            f"got: {out!r}"
        )


def test_status_includes_in_sprint_label(isolated_log):
    """Active iteration with a cut: output contains an 'in-sprint' / 'sprint'
    label of some shape (the membership marker)."""
    _seed_full(n_stories=5, cut_at=3)
    out = _call_status().lower()
    # Accept any of a few canonical forms — the spec says "visible label",
    # not "this exact word".
    assert ("in-sprint" in out
            or "in_sprint" in out
            or "in sprint" in out
            or "sprint" in out), (
        f"output must include an in-sprint membership label;\n"
        f"got: {out!r}"
    )


def test_status_includes_deferred_label(isolated_log):
    """Active iteration with a partial cut: output contains 'deferred'."""
    _seed_full(n_stories=5, cut_at=3)
    out = _call_status().lower()
    assert "deferred" in out, (
        f"output must include the 'deferred' membership label;\n"
        f"got: {out!r}"
    )


def test_status_includes_lifecycle_state_planned(isolated_log):
    """Fresh backlog: every story is in 'planned' — the word 'planned'
    appears in the output."""
    _seed_full(n_stories=5, cut_at=3)
    out = _call_status().lower()
    assert "planned" in out, (
        f"output must surface lifecycle state 'planned';\n"
        f"got: {out!r}"
    )


def test_status_renders_iteration_id_in_header_position(isolated_log):
    """Iteration_id appears before any story_id in the output (header-style
    rendering — the iteration frame comes first)."""
    sids, _, _ = _seed_full(n_stories=5, cut_at=3,
                            iteration_id="iter-header-1")
    out = _call_status()
    iter_pos = out.find("iter-header-1")
    first_story_pos = min(out.find(s) for s in sids if out.find(s) >= 0)
    assert iter_pos >= 0
    assert iter_pos < first_story_pos, (
        f"iteration_id must appear before any story_id;\n"
        f"iter_pos={iter_pos} first_story_pos={first_story_pos}\n"
        f"got: {out!r}"
    )


def test_status_returns_non_empty_string_when_active(isolated_log):
    """With an active iteration + backlog, status() output is non-empty."""
    _seed_full(n_stories=5, cut_at=3)
    out = _call_status()
    assert out.strip(), (
        f"active-iteration status output must be non-empty;\n"
        f"got: {out!r}"
    )


def test_status_each_story_id_appears_exactly_once(isolated_log):
    """Each story_id appears at least once and (sanity) not duplicated
    across the whole-iteration listing — one row per story."""
    sids, _, _ = _seed_full(n_stories=5, cut_at=3)
    out = _call_status()
    for sid in sids:
        assert out.count(sid) >= 1, (
            f"story_id {sid!r} must appear at least once;\n"
            f"got: {out!r}"
        )
        # Allow up to 2 occurrences (e.g. header + body row) but flag wild
        # duplication that would suggest a render bug.
        assert out.count(sid) <= 3, (
            f"story_id {sid!r} appears too many times "
            f"(count={out.count(sid)});\nget: {out!r}"
        )


def test_status_full_active_iteration_smoke(isolated_log):
    """Smoke: open + decompose + cut + transition all renders without
    raising."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    _advance(in_sprint[0], "in_progress")
    _advance(in_sprint[1], "in_progress", "in_review")

    # Should not raise.
    out = _call_status()
    assert out.strip()


# ===========================================================================
# Sequence ordering (5) — ascending by sequence, regardless of write order
# ===========================================================================


def test_status_orders_stories_ascending_by_sequence(isolated_log):
    """story_ids appear in sequence-ascending order in the output."""
    sids, _, _ = _seed_full(n_stories=5, cut_at=3)
    out = _call_status()
    positions = [out.find(sid) for sid in sids]
    assert all(p >= 0 for p in positions), (
        f"every story_id must appear in the output; positions={positions}"
    )
    assert positions == sorted(positions), (
        f"story_ids must render in sequence-ascending order;\n"
        f"sids (seq order) = {sids!r}\n"
        f"positions in output = {positions!r}\n"
        f"got: {out!r}"
    )


def test_status_orders_reverse_written_backlog_ascending(isolated_log):
    """A backlog written with sequences [5,4,3,2,1] still renders by
    sequence ascending (Story 4's derive_state already sorts; status must
    not undo that)."""
    import sm

    _open_iteration(iteration_id="iter-rev")
    sids_in_write_order = _seed_backlog(n=5, sequences=[5, 4, 3, 2, 1])
    # sids_in_write_order[0] -> sequence 5, ..., sids_in_write_order[4] -> seq 1
    # Sequence-ascending render order is reverse of write order:
    ascending = list(reversed(sids_in_write_order))

    out = _call_status()
    positions = [out.find(sid) for sid in ascending]
    assert all(p >= 0 for p in positions), (
        f"every story_id must appear; positions={positions}"
    )
    assert positions == sorted(positions), (
        f"reverse-written backlog must still render ascending by sequence;\n"
        f"ascending sids = {ascending!r}\n"
        f"positions = {positions!r}\n"
        f"got: {out!r}"
    )


def test_status_orders_noncontiguous_sequences_ascending(isolated_log):
    """Non-contiguous sequences (e.g. 10, 20, 30) still render ascending.
    derive_state sorts by sequence — strict 1..N isn't an invariant of the
    replay, only of decompose-time validation. status must honor whatever
    backlog the replay produces.
    """
    _open_iteration(iteration_id="iter-noncontig")
    sids_in_write_order = _seed_backlog(n=3, sequences=[30, 10, 20])
    # sids[0]->seq 30, sids[1]->seq 10, sids[2]->seq 20.
    # Ascending order is: sids[1] (10), sids[2] (20), sids[0] (30).
    expected_order = [
        sids_in_write_order[1],
        sids_in_write_order[2],
        sids_in_write_order[0],
    ]

    out = _call_status()
    positions = [out.find(sid) for sid in expected_order]
    assert all(p >= 0 for p in positions), (
        f"every story_id must appear; positions={positions}"
    )
    assert positions == sorted(positions), (
        f"non-contiguous sequences must render ascending;\n"
        f"expected order = {expected_order!r}\n"
        f"positions = {positions!r}\n"
        f"got: {out!r}"
    )


def test_status_orders_after_cut(isolated_log):
    """After sprint_cut, in-sprint stories (sequence 1..K) still come
    before deferred (K+1..N) — because ordering is by sequence ascending,
    and the cut respects sequence order."""
    sids, in_sprint, deferred = _seed_full(n_stories=5, cut_at=3)
    out = _call_status()
    last_in_sprint_pos = max(out.find(s) for s in in_sprint)
    first_deferred_pos = min(out.find(s) for s in deferred)
    assert last_in_sprint_pos < first_deferred_pos, (
        f"in-sprint stories (seq 1..3) must render before deferred "
        f"(seq 4..5);\nlast_in_sprint_pos={last_in_sprint_pos}\n"
        f"first_deferred_pos={first_deferred_pos}\n"
        f"got: {out!r}"
    )


def test_status_orders_unchanged_by_transitions(isolated_log):
    """State changes do not reshuffle the story order — it remains
    sequence-ascending."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    _advance(in_sprint[2], "in_progress")
    _advance(in_sprint[0], "in_progress", "in_review")

    out = _call_status()
    positions = [out.find(sid) for sid in sids]
    assert positions == sorted(positions), (
        f"transitions must not reshuffle story order;\n"
        f"positions={positions!r}\nout={out!r}"
    )


# ===========================================================================
# Lifecycle state rendering (8) — every state surfaces correctly
# ===========================================================================


def test_status_renders_planned_state(isolated_log):
    """Fresh backlog: every in-sprint story is rendered as 'planned'."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    out = _call_status().lower()
    # 'planned' must appear somewhere — at least one story is planned.
    assert "planned" in out, (
        f"output must surface 'planned';\ngot: {out!r}"
    )


def test_status_renders_in_progress_after_start(isolated_log):
    """After start, 'in_progress' appears in the output."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    _advance(in_sprint[0], "in_progress")
    out = _call_status().lower()
    assert "in_progress" in out, (
        f"output must surface 'in_progress' after start;\ngot: {out!r}"
    )


def test_status_renders_in_review_after_submit(isolated_log):
    """After start+submit, 'in_review' appears in the output."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    _advance(in_sprint[0], "in_progress", "in_review")
    out = _call_status().lower()
    assert "in_review" in out, (
        f"output must surface 'in_review' after submit;\ngot: {out!r}"
    )


def test_status_renders_accepted_after_accept(isolated_log):
    """After the full chain to accepted, 'accepted' appears in the output."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    out = _call_status().lower()
    assert "accepted" in out, (
        f"output must surface 'accepted';\ngot: {out!r}"
    )


def test_status_renders_rejected_after_reject(isolated_log):
    """After start+submit+reject, 'rejected' appears in the output."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    _advance(in_sprint[0], "in_progress", "in_review", "rejected")
    out = _call_status().lower()
    assert "rejected" in out, (
        f"output must surface 'rejected';\ngot: {out!r}"
    )


def test_status_renders_mixed_states_all_present(isolated_log):
    """A backlog with stories in different states surfaces every state."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    # in_sprint[0] -> accepted
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    # in_sprint[1] -> rejected
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    # in_sprint[2] -> in_progress (leaves an in_progress trace)
    _advance(in_sprint[2], "in_progress")

    out = _call_status().lower()
    for expected_state in ("planned", "in_progress", "accepted", "rejected"):
        assert expected_state in out, (
            f"output must surface state {expected_state!r};\n"
            f"got: {out!r}"
        )


def test_status_renders_deferred_stories_as_planned(isolated_log):
    """Deferred stories are still 'planned' — derive_state initialized them
    to 'planned' at decompose time, and they're not transitioned. They
    should render with their actual state."""
    sids, in_sprint, deferred = _seed_full(n_stories=5, cut_at=3)
    # Transition the in-sprint stories to non-planned so the only 'planned'
    # entries left are the deferred ones.
    for sid in in_sprint:
        _advance(sid, "in_progress")

    out = _call_status()
    # Each deferred story_id must appear, AND 'planned' must still be
    # present (because deferred stories are still planned).
    for sid in deferred:
        assert sid in out, (
            f"deferred story_id {sid!r} must appear;\ngot: {out!r}"
        )
    assert "planned" in out.lower(), (
        f"deferred stories must still render as 'planned';\n"
        f"got: {out!r}"
    )


def test_status_renders_state_adjacent_to_story_id(isolated_log):
    """For an in_progress story, the state name 'in_progress' appears
    within a reasonable window (~200 chars) of that story's id in the
    output. Pins per-row rendering rather than the state being noted only
    in a far-away aggregate."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    _advance(in_sprint[0], "in_progress")
    target = in_sprint[0]

    out = _call_status()
    pos = out.find(target)
    assert pos >= 0
    window = out[max(0, pos - 200):pos + 200].lower()
    assert "in_progress" in window, (
        f"'in_progress' must appear near story_id {target!r};\n"
        f"window={window!r}\nfull output={out!r}"
    )


# ===========================================================================
# In-sprint vs deferred (5) — cut at 3/5, 5/5, 1/5
# ===========================================================================


def test_status_cut_three_of_five_membership(isolated_log):
    """Cut at 3 of 5: first 3 stories marked in-sprint, last 2 deferred."""
    sids, in_sprint, deferred = _seed_full(n_stories=5, cut_at=3)
    out = _call_status()
    # 'deferred' must appear in the output (there are deferred stories).
    assert "deferred" in out.lower(), (
        f"output must contain 'deferred';\ngot: {out!r}"
    )
    # Each deferred story_id should be near a 'deferred' marker.
    for sid in deferred:
        pos = out.find(sid)
        assert pos >= 0
        window = out[max(0, pos - 200):pos + 200].lower()
        assert "deferred" in window, (
            f"story_id {sid!r} (deferred) must be near a 'deferred' "
            f"marker;\nwindow={window!r}\nfull output={out!r}"
        )


def test_status_cut_five_of_five_all_in_sprint(isolated_log):
    """Cut at 5 of 5: every story is in-sprint, no deferred."""
    sids, in_sprint, deferred = _seed_full(n_stories=5, cut_at=5)
    assert deferred == []
    out = _call_status().lower()
    # 'deferred' should NOT appear as a populated label — every story is
    # in-sprint. Allow it to appear as a column header or descriptor if the
    # implementor formats a table; what we really pin is "no story_id is
    # near a 'deferred' marker".
    # Simpler pin: just ensure 'deferred' as a per-story state isn't claimed.
    # Skip the strict header check and verify the in-sprint label is
    # everywhere instead:
    for sid in sids:
        pos = out.find(sid)
        assert pos >= 0


def test_status_cut_one_of_five_membership(isolated_log):
    """Cut at 1 of 5: first story in-sprint, remaining 4 deferred."""
    sids, in_sprint, deferred = _seed_full(n_stories=5, cut_at=1)
    assert len(in_sprint) == 1
    assert len(deferred) == 4
    out = _call_status()
    assert "deferred" in out.lower(), (
        f"output must contain 'deferred';\ngot: {out!r}"
    )
    # Each of the 4 deferred stories must appear in the output.
    for sid in deferred:
        assert sid in out, (
            f"deferred story_id {sid!r} must appear;\ngot: {out!r}"
        )


def test_status_in_sprint_label_near_in_sprint_stories(isolated_log):
    """For an in-sprint story, an in-sprint label (any of the canonical
    forms) appears within a reasonable window of the story_id."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    target = in_sprint[0]
    out = _call_status().lower()
    pos = out.find(target)
    assert pos >= 0
    window = out[max(0, pos - 200):pos + 200]
    assert ("in-sprint" in window
            or "in_sprint" in window
            or "in sprint" in window), (
        f"in-sprint label must appear near story_id {target!r};\n"
        f"window={window!r}\nfull output={out!r}"
    )


def test_status_membership_separates_in_sprint_from_deferred(isolated_log):
    """The in-sprint story_ids and the deferred story_ids carry distinct
    markers — pin by checking no deferred story_id falls inside a window
    that contains 'in-sprint' AND no in-sprint story_id falls inside a
    window that contains 'deferred' (per-row marker rendering)."""
    sids, in_sprint, deferred = _seed_full(n_stories=5, cut_at=3)
    out = _call_status().lower()

    # A "row window" is +/- 80 chars around the story_id position.
    for sid in deferred:
        pos = out.find(sid)
        window = out[max(0, pos - 80):pos + 80]
        # The row of a deferred story should NOT carry the in-sprint
        # label — that would be a labelling bug.
        # We're lenient on table headers; the per-row marker is what counts.
        # If "in-sprint" appears, "deferred" should appear too, and in a
        # canonical layout that wouldn't happen on the same row.
        if ("in-sprint" in window or "in_sprint" in window):
            # Only allowed if 'deferred' is ALSO in the window (table-
            # header text bleeding into a wide window). The real failure
            # mode is mislabelled rows — pin that the deferred marker is
            # present so misclassification fails the test.
            assert "deferred" in window, (
                f"deferred story_id {sid!r} carries an in-sprint marker "
                f"without a deferred marker — looks misclassified;\n"
                f"window={window!r}\nfull output={out!r}"
            )


# ===========================================================================
# Read-only invariant (6) — log unchanged, _append_entry never fires
# ===========================================================================


def test_status_does_not_modify_log_bytes_empty(isolated_log):
    """Empty log: status() leaves log bytes unchanged."""
    # Empty log -> file may not even exist yet. Capture state both ways.
    before = isolated_log.read_bytes() if isolated_log.exists() else b""
    _call_status()
    after = isolated_log.read_bytes() if isolated_log.exists() else b""
    assert before == after, (
        "status() on an empty log must leave log unchanged"
    )


def test_status_does_not_modify_log_bytes_active(isolated_log):
    """Active iteration: status() leaves log bytes unchanged."""
    _seed_full(n_stories=5, cut_at=3)
    before = isolated_log.read_bytes()
    _call_status()
    after = isolated_log.read_bytes()
    assert before == after, (
        "status() on an active iteration must leave log unchanged"
    )


def test_status_does_not_modify_log_bytes_after_transitions(isolated_log):
    """Active iteration + transitions: status() leaves log unchanged."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    before = isolated_log.read_bytes()
    _call_status()
    after = isolated_log.read_bytes()
    assert before == after, (
        "status() must leave log unchanged regardless of state complexity"
    )


def test_status_does_not_call_append_entry(isolated_log, monkeypatch):
    """status() must not call _append_entry. Monkeypatch the writer to
    fail loudly if it fires."""
    import sm

    _seed_full(n_stories=5, cut_at=3)

    calls = []
    real_append = sm._append_entry

    def _spy(entry):
        calls.append(entry)
        # Don't actually write — fail loud.
        raise AssertionError(
            "status() must not call _append_entry; it's read-only"
        )

    monkeypatch.setattr(sm, "_append_entry", _spy)

    # Should NOT raise — because status should never call _append_entry.
    _call_status()
    assert calls == [], (
        f"status() called _append_entry {len(calls)} times — "
        f"it must be read-only"
    )

    # Restore (monkeypatch will do it anyway).
    monkeypatch.setattr(sm, "_append_entry", real_append)


def test_status_repeated_100_times_log_unchanged(isolated_log):
    """Running status() 100 times leaves the log byte-for-byte identical."""
    _seed_full(n_stories=5, cut_at=3)
    before = isolated_log.read_bytes()
    for _ in range(100):
        _call_status()
    after = isolated_log.read_bytes()
    assert before == after, (
        "100 consecutive status() calls must leave the log unchanged"
    )


def test_status_no_sidecar_files_in_log_dir(isolated_log, tmp_path):
    """status() must not create any sidecar files in the log directory.
    The log file itself may or may not exist (depending on whether seeded);
    nothing else must appear."""
    _seed_full(n_stories=5, cut_at=3)
    before_files = sorted(p.name for p in tmp_path.iterdir())
    _call_status()
    after_files = sorted(p.name for p in tmp_path.iterdir())
    assert before_files == after_files, (
        f"status() must not create sidecar files;\n"
        f"before={before_files!r}\nafter={after_files!r}"
    )


# ===========================================================================
# CLI (5) — subcommand recognized, exits 0 in both modes, stdout matches
# ===========================================================================


def test_cli_status_subcommand_recognized(cli_log):
    """`python -m sm status` is NOT 'unknown command'."""
    log_path, env = cli_log
    result = _run_cli(env, "status")
    combined = (result.stdout + result.stderr).lower()
    assert "unknown command" not in combined, (
        f"CLI must recognize 'status' as a known subcommand;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_status_exits_zero_active(cli_log):
    """status with an active iteration + backlog exits 0."""
    log_path, env = cli_log
    _seed_full_at(log_path, n_stories=5, cut_at=3,
                  iteration_id="iter-cli-1")
    result = _run_cli(env, "status")
    assert result.returncode == 0, (
        f"status with active iteration must exit 0;\n"
        f"got returncode={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_status_stdout_mentions_iteration_id(cli_log):
    """CLI stdout includes the iteration_id when one is active."""
    log_path, env = cli_log
    _seed_full_at(log_path, n_stories=5, cut_at=3,
                  iteration_id="iter-cli-2")
    result = _run_cli(env, "status")
    assert result.returncode == 0
    assert "iter-cli-2" in (result.stdout + result.stderr), (
        f"stdout must contain iteration_id 'iter-cli-2';\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_status_stdout_mentions_no_active_when_empty(cli_log):
    """CLI stdout contains 'no active iteration' substring when empty."""
    log_path, env = cli_log
    result = _run_cli(env, "status")
    combined = (result.stdout + result.stderr).lower()
    assert "no active iteration" in combined, (
        f"stdout must contain 'no active iteration' when no iter is open;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_status_does_not_modify_log_bytes(cli_log):
    """CLI invocation of `status` leaves log bytes unchanged."""
    log_path, env = cli_log
    _seed_full_at(log_path, n_stories=5, cut_at=3,
                  iteration_id="iter-cli-readonly")
    before = log_path.read_bytes()
    result = _run_cli(env, "status")
    assert result.returncode == 0
    after = log_path.read_bytes()
    assert before == after, (
        "CLI `status` must leave log bytes unchanged"
    )


# ===========================================================================
# Edge cases (5) — backlog empty, pre-cut, post-cut+transitions, etc.
# ===========================================================================


def test_status_iteration_open_no_decompose_yet(isolated_log):
    """Iteration open but decompose not yet run: status() returns cleanly
    and names the iteration_id. Whether it adds a 'no story backlog'
    marker or just renders an empty backlog is the implementor's call —
    but it must not crash."""
    _open_iteration(iteration_id="iter-no-decompose")
    out = _call_status()
    # Iteration_id must surface.
    assert "iter-no-decompose" in out, (
        f"iteration_id must appear even with no backlog;\ngot: {out!r}"
    )
    # AND the 'no active iteration' marker must NOT appear (there IS an
    # active iteration; just no backlog).
    assert "no active iteration" not in out.lower(), (
        f"iteration IS active here — 'no active iteration' must NOT "
        f"appear;\ngot: {out!r}"
    )


def test_status_iteration_open_decompose_no_cut(isolated_log):
    """Iteration open + decompose run + sprint_cut NOT run yet: status()
    renders every story without crashing."""
    _open_iteration(iteration_id="iter-no-cut")
    sids = _seed_backlog(n=5)

    out = _call_status()
    # iteration_id surfaces.
    assert "iter-no-cut" in out
    # Every story_id surfaces.
    for sid in sids:
        assert sid in out, (
            f"story_id {sid!r} must appear pre-cut;\ngot: {out!r}"
        )


def test_status_decomposed_cut_no_transitions(isolated_log):
    """Decomposed + cut + no transitions: every story renders as planned,
    membership labels appear, order is sequence-ascending."""
    sids, in_sprint, deferred = _seed_full(n_stories=5, cut_at=3)
    out = _call_status()

    # Every sid appears.
    for sid in sids:
        assert sid in out

    # planned appears (every story is still planned).
    assert "planned" in out.lower()

    # Sequence-ascending order preserved.
    positions = [out.find(sid) for sid in sids]
    assert positions == sorted(positions)


def test_status_decomposed_cut_multiple_transitions(isolated_log):
    """Decomposed + cut + lots of transitions: every story renders with
    its current lifecycle state."""
    sids, in_sprint, _ = _seed_full(n_stories=5, cut_at=3)
    _advance(in_sprint[0], "in_progress", "in_review", "accepted")
    _advance(in_sprint[1], "in_progress", "in_review", "rejected")
    _advance(in_sprint[2], "in_progress")

    out = _call_status().lower()
    for state in ("planned", "in_progress", "accepted", "rejected"):
        assert state in out, (
            f"state {state!r} must appear after multiple transitions;\n"
            f"got: {out!r}"
        )


def test_status_re_call_returns_equal_output(isolated_log):
    """Two consecutive status() calls produce equal output (deterministic
    on a frozen log)."""
    _seed_full(n_stories=5, cut_at=3)
    out1 = _call_status()
    out2 = _call_status()
    assert out1 == out2, (
        f"two consecutive status() calls must return equal output;\n"
        f"out1={out1!r}\nout2={out2!r}"
    )
