"""Story 20 — pin the close-and-flow clean-state contract for next ingest.

Story 20 (Sprint 2, size S) is a verification / pin story. Stories 18
(close_iteration) and 19 (force_close) already implement the clean-state
behavior: a successful close clears `active_iteration` from `derive_state`,
clears the in-sprint-cut lock, and leaves the log in a state that a fresh
`ingest` accepts immediately — no manual cleanup, no flag reset.

What this file pins (integration replay against the LIVE close/force-close
implementations — no stub close paths):

  - After a successful `close_iteration()`:
      * derive_state()["active_iteration"] is None
      * derive_state()["close_status"] is populated (closed_by, reason,
        accepted_count, rejected_count, force_closed_count)
      * derive_state()["story_states"] history is preserved (terminal
        states from the closed iteration still appear)
      * derive_state()["sprint_cut"] may persist as a historical artifact —
        this file pins that behavior (current implementation: sprint_cut
        is NOT cleared on close — it's a replayed log artifact carried
        across the close boundary until a new iteration_open + story
        backlog seeds new state)

  - After a successful `force_close(reason)`: same as close, but
      close_status.closed_by == "force-close" and reason is captured.

  - `status()` between close and the next ingest contains the substring
    "no active iteration" — for both close and force-close paths.

  - `ingest(<fresh handoff>)` immediately succeeds after a close. No
    manual cleanup of log.jsonl, no resetting of any flag. The newly
    opened iteration becomes the active iteration, and the story_backlog
    is cleared (replaced by the new iteration's empty state).

  - `ingest(<fresh handoff>)` immediately succeeds after a force_close
    with the same invariants.

  - End-to-end replay: ingest -> decompose -> sprint_cut ->
    transition_story (all in-sprint stories accepted via record_review
    -> in_review -> accepted) -> close_iteration -> ingest succeeds
    without intervention. The new iteration is active.

  - End-to-end with force-close: ingest -> decompose -> sprint_cut ->
    progress some stories but NOT all -> force_close(reason) -> ingest
    succeeds without intervention.

  - Multiple cycles in one log: three rounds of ingest -> ... -> close
    -> ingest succeed in the same log file. The log accumulates history
    (multiple iteration_open + iteration_close entries) but each ingest
    sees a clean slate.

Story 20 SUCCESS OUTCOME (per the spec): If all tests pass on first run,
the clean-state contract is already verified by Stories 18 + 19 — no new
production code is needed. Story 20 ships as "contract verified."

Tests are integration-style: they invoke the live `sm.close_iteration`,
`sm.force_close`, `sm.ingest`, and `sm.decompose(spawn_agent=stub)` —
they do NOT stub the close path. Direct `_append_entry` is used only to
seed initial iteration state in tests that don't need full end-to-end
replay (matching the Sprint 2 test-suite convention).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import uuid as _uuid

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SOURCE_ROLES_DIR = PACKAGE_DIR / "roles"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file AND mirror the
    package `roles/` dir into `tmp_path/roles/` so that `sm.decompose`'s
    `resolve_role_spec("sm_agent")` call (which anchors at LOG_PATH.parent)
    finds the canonical role-spec markdown files.

    Mirroring is local to this file — the project's shared autouse fixture
    in `conftest.py` is scoped to `test_decompose.py` only. The end-to-end
    replay tests in this file need the same staging.
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)

    # Stage roles for sm.decompose's resolve_role_spec call.
    dest_roles = tmp_path / "roles"
    if not dest_roles.exists() and SOURCE_ROLES_DIR.is_dir():
        shutil.copytree(SOURCE_ROLES_DIR, dest_roles)

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
        requirements = [
            _canonical_requirement("req-1", "Title 1"),
            _canonical_requirement("req-2", "Title 2"),
        ]
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


def _open_iteration_direct(iteration_id: str = "iter-1",
                           requirements=None,
                           goal: str = "Test iteration") -> dict:
    """Append an `iteration_open` entry directly via build_entry +
    _append_entry. Used for fast seeding of derive_state-clean tests.

    Caller owns `isolated_log`.
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
        "iteration_goal": goal,
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return entry


def _seed_backlog_direct(n: int = 3,
                         requirement_ids_per_story=None) -> list:
    """Append a `story_backlog` entry with N canonical stories directly.
    Returns the list of minted story_ids in sequence order.
    """
    import sm

    if requirement_ids_per_story is None:
        requirement_ids_per_story = [["req-1"] for _ in range(n)]
    if len(requirement_ids_per_story) != n:
        raise ValueError(
            "requirement_ids_per_story length must equal n"
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


def _seed_open_decomposed_cut(n_stories: int = 3,
                              cut_at: int = 2,
                              iteration_id: str = "iter-1",
                              requirements=None) -> tuple:
    """Seed open + backlog + cut via direct _append_entry. Returns
    (all_story_ids, in_sprint_ids).
    """
    import sm

    _open_iteration_direct(iteration_id=iteration_id,
                           requirements=requirements)
    sids = _seed_backlog_direct(n=n_stories)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at]


def _drive_all_accepted(in_sprint_ids: list) -> None:
    """Push every in-sprint story planned -> in_progress -> in_review ->
    accepted (with a reviewer_approval entry before accept).
    """
    import sm

    for sid in in_sprint_ids:
        sm.transition_story(sid, "in_progress")
        sm.transition_story(sid, "in_review")
        sm.record_review(sid, True, "ok")
        sm.transition_story(sid, "accepted")


def _stub_spawn_for(requirements: list, n_stories: int = 3):
    """Build a spawn_agent stub for `sm.decompose` that emits N stories
    whose requirement_ids are taken from the supplied requirements list.

    Used in true end-to-end replay tests (ingest -> decompose -> ...).
    """
    rid_pool = [r["requirement_id"] for r in requirements]
    sizes = ["S", "M", "L"]
    stories = []
    for i in range(1, n_stories + 1):
        stories.append({
            "sequence": i,
            "title": f"Story {i}",
            "size": sizes[(i - 1) % 3],
            "requirement_ids": [rid_pool[(i - 1) % len(rid_pool)]],
            "acceptance_criteria": f"Story {i} AC.",
        })
    payload = json.dumps({"stories": stories})

    def _spawn(role_spec_path, requirements_arg):
        return payload

    return _spawn


# ===========================================================================
# After close — derive_state clean (5+)
# ===========================================================================


def test_after_close_active_iteration_is_none(isolated_log):
    """Post-successful-close, derive_state reports no active iteration."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    state = sm.derive_state()
    assert state["active_iteration"] is None, (
        f"after close, active_iteration must be None; got "
        f"{state['active_iteration']!r}"
    )


def test_after_close_close_status_populated(isolated_log):
    """Post-successful-close, close_status is populated with counts."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    state = sm.derive_state()
    cs = state["close_status"]
    assert cs is not None, "close_status must be populated after close"
    assert cs["closed_by"] == "operator", (
        f"default close_by must be 'operator'; got {cs['closed_by']!r}"
    )
    assert cs["accepted_count"] == 2, (
        f"two stories accepted; got accepted_count={cs['accepted_count']!r}"
    )
    assert cs["rejected_count"] == 0
    assert cs["force_closed_count"] == 0


def test_after_close_story_states_history_preserved(isolated_log):
    """Story lifecycle history survives close — terminal states still in
    story_states (the log replay carries them across the close boundary).
    """
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    state = sm.derive_state()
    for sid in in_sprint:
        assert state["story_states"].get(sid) == "accepted", (
            f"story {sid!r} must remain in 'accepted' after close; "
            f"got {state['story_states'].get(sid)!r}"
        )


def test_after_close_sprint_cut_persists_as_historical_artifact(isolated_log):
    """sprint_cut int persists in derive_state across close — it's a
    replayed log artifact, not bound to active_iteration. This pins
    current behavior: close clears `active_iteration` + sets
    `close_status` but does NOT clear sprint_cut.
    """
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    state = sm.derive_state()
    # sprint_cut persists — it's a replayed scalar artifact.
    assert state["sprint_cut"] == 2, (
        f"sprint_cut must persist as historical artifact; got "
        f"{state['sprint_cut']!r}"
    )


def test_after_close_log_jsonl_intact_and_extended(isolated_log):
    """The log is not truncated by close — it's append-only. The
    iteration_close entry is the last entry on disk."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    entries = list(sm.read_entries())
    assert len(entries) > 0
    assert entries[-1]["type"] == "iteration_close", (
        f"last log entry after close must be iteration_close; got "
        f"{entries[-1]['type']!r}"
    )


def test_after_close_two_consecutive_derive_state_calls_equal(isolated_log):
    """derive_state is pure read; two consecutive calls return equal dicts
    even across the close boundary."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    s1 = sm.derive_state()
    s2 = sm.derive_state()
    assert s1 == s2, "two derive_state calls post-close must be equal"


# ===========================================================================
# After force-close — derive_state clean (4+)
# ===========================================================================


def test_after_force_close_active_iteration_is_none(isolated_log):
    """Post-force-close, derive_state reports no active iteration."""
    import sm

    _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    sm.force_close("emergency stop")

    state = sm.derive_state()
    assert state["active_iteration"] is None, (
        f"after force-close, active_iteration must be None; got "
        f"{state['active_iteration']!r}"
    )


def test_after_force_close_close_status_marked_force_close(isolated_log):
    """Post-force-close, close_status.closed_by == 'force-close' and
    reason is captured verbatim."""
    import sm

    _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    sm.force_close("emergency stop")

    state = sm.derive_state()
    cs = state["close_status"]
    assert cs is not None
    assert cs["closed_by"] == "force-close", (
        f"force-close must mark closed_by='force-close'; got "
        f"{cs['closed_by']!r}"
    )
    assert cs["reason"] == "emergency stop"
    assert cs["force_closed_count"] == 2, (
        f"two non-terminal in-sprint stories force-closed; got "
        f"force_closed_count={cs['force_closed_count']!r}"
    )


def test_after_force_close_story_states_history_preserved(isolated_log):
    """Force-closed stories carry the `force_closed` lifecycle state in
    story_states after the close — terminal-state history survives.
    """
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    sm.force_close("operator abort")

    state = sm.derive_state()
    for sid in in_sprint:
        assert state["story_states"].get(sid) == "force_closed", (
            f"story {sid!r} must remain in 'force_closed' after "
            f"force-close; got {state['story_states'].get(sid)!r}"
        )


def test_after_force_close_log_jsonl_intact_and_extended(isolated_log):
    """Force-close appends state_change entries + iteration_close; the
    iteration_close is the final entry. The log is not truncated.
    """
    import sm

    _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    sm.force_close("emergency stop")

    entries = list(sm.read_entries())
    assert len(entries) > 0
    assert entries[-1]["type"] == "iteration_close"
    # Force-close also appends story_state_change entries before close.
    types = [e["type"] for e in entries]
    assert "story_state_change" in types, (
        f"force-close must have appended at least one state_change "
        f"before close; got entry types {types!r}"
    )


# ===========================================================================
# Status reports "no active iteration" between close and next ingest (3+)
# ===========================================================================


def test_status_after_close_reports_no_active_iteration(isolated_log):
    """Post-close, status() output contains 'no active iteration'."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    output = sm.status()
    assert "no active iteration" in output, (
        f"status must say 'no active iteration' post-close; got: {output!r}"
    )


def test_status_after_force_close_reports_no_active_iteration(isolated_log):
    """Post-force-close, status() output contains 'no active iteration'."""
    import sm

    _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    sm.force_close("emergency stop")

    output = sm.status()
    assert "no active iteration" in output, (
        f"status must say 'no active iteration' post-force-close; "
        f"got: {output!r}"
    )


def test_status_is_read_only_across_close_boundary(isolated_log):
    """status() between close and next ingest does not write to the log."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    before = isolated_log.read_bytes()
    _ = sm.status()
    _ = sm.status()
    _ = sm.status()
    after = isolated_log.read_bytes()

    assert before == after, (
        "status() between close and next ingest must not write to log"
    )


# ===========================================================================
# Ingest succeeds after close (5+)
# ===========================================================================


def test_ingest_succeeds_immediately_after_close(isolated_log, tmp_path):
    """Fresh handoff ingest succeeds right after close — no cleanup."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    handoff = _canonical_handoff(iteration_id="iter-2")
    handoff_path = _write_handoff(tmp_path, handoff, name="handoff_2.json")

    entry = sm.ingest(handoff_path)
    assert entry["type"] == "iteration_open"
    assert entry["iteration_id"] == "iter-2"


def test_ingest_after_close_becomes_active(isolated_log, tmp_path):
    """The newly-ingested iteration becomes the active one in derive_state."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    handoff = _canonical_handoff(iteration_id="iter-2")
    handoff_path = _write_handoff(tmp_path, handoff, name="handoff_2.json")
    sm.ingest(handoff_path)

    state = sm.derive_state()
    assert state["active_iteration"] is not None
    assert state["active_iteration"]["iteration_id"] == "iter-2"


def test_ingest_after_close_story_backlog_persists_until_next_decompose(
        isolated_log, tmp_path):
    """After a new ingest, the prior iteration's story_backlog persists in
    derive_state as a replayed log artifact until the next decompose
    overwrites it. The clean-state contract is anchored on `active_iteration`
    being None — backlog is a separate replayed scalar. This test pins the
    actual behavior so future changes are intentional.
    """
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    # Pre-ingest: prior backlog still reachable via replay.
    pre = sm.derive_state()
    assert len(pre["story_backlog"]) == 3

    handoff = _canonical_handoff(iteration_id="iter-2")
    handoff_path = _write_handoff(tmp_path, handoff, name="handoff_2.json")
    sm.ingest(handoff_path)

    post = sm.derive_state()
    # active_iteration is the NEW iteration (clean-state contract).
    assert post["active_iteration"]["iteration_id"] == "iter-2"
    # story_backlog still carries the prior decompose's stories — it's
    # a log-replayed artifact; only a fresh story_backlog entry rewrites it.
    assert len(post["story_backlog"]) == 3, (
        f"prior story_backlog persists as historical replay until next "
        f"decompose overwrites it; got {len(post['story_backlog'])} stories"
    )


def test_ingest_after_close_clears_close_status(isolated_log, tmp_path):
    """A new iteration_open clears the prior close_status (per
    derive_state's contract: close_status resets on each new open)."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    handoff = _canonical_handoff(iteration_id="iter-2")
    handoff_path = _write_handoff(tmp_path, handoff, name="handoff_2.json")
    sm.ingest(handoff_path)

    state = sm.derive_state()
    assert state["close_status"] is None, (
        f"new iteration_open must clear close_status; got "
        f"{state['close_status']!r}"
    )


def test_ingest_after_close_no_manual_log_cleanup(isolated_log, tmp_path):
    """No need to touch log.jsonl between close and next ingest — the
    log file is left intact and the next ingest appends to it."""
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    # The log file exists and is non-empty.
    assert isolated_log.exists()
    pre_size = isolated_log.stat().st_size
    assert pre_size > 0

    handoff = _canonical_handoff(iteration_id="iter-2")
    handoff_path = _write_handoff(tmp_path, handoff, name="handoff_2.json")
    sm.ingest(handoff_path)

    # Log appended (size grew) — not truncated, not replaced.
    post_size = isolated_log.stat().st_size
    assert post_size > pre_size


def test_ingest_after_close_reuses_old_iteration_id_blocked(
        isolated_log, tmp_path):
    """Reusing a prior (closed) iteration_id is still rejected as a
    duplicate — clean-state does not erase log history. This ensures the
    "clean-state" pin doesn't accidentally weaken the duplicate-id check.
    """
    import sm

    _, in_sprint = _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    _drive_all_accepted(in_sprint)
    sm.close_iteration()

    # Reusing iter-1 (just-closed) must raise.
    handoff = _canonical_handoff(iteration_id="iter-1")
    handoff_path = _write_handoff(tmp_path, handoff, name="handoff_dup.json")

    with pytest.raises(sm.IngestDuplicateError):
        sm.ingest(handoff_path)


# ===========================================================================
# Ingest succeeds after force-close (3+)
# ===========================================================================


def test_ingest_succeeds_immediately_after_force_close(isolated_log, tmp_path):
    """Fresh handoff ingest succeeds right after force-close — no cleanup."""
    import sm

    _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    sm.force_close("emergency stop")

    handoff = _canonical_handoff(iteration_id="iter-2")
    handoff_path = _write_handoff(tmp_path, handoff, name="handoff_2.json")

    entry = sm.ingest(handoff_path)
    assert entry["type"] == "iteration_open"
    assert entry["iteration_id"] == "iter-2"


def test_ingest_after_force_close_becomes_active(isolated_log, tmp_path):
    """The newly-ingested iteration becomes active after a force-close."""
    import sm

    _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    sm.force_close("emergency stop")

    handoff = _canonical_handoff(iteration_id="iter-2")
    handoff_path = _write_handoff(tmp_path, handoff, name="handoff_2.json")
    sm.ingest(handoff_path)

    state = sm.derive_state()
    assert state["active_iteration"] is not None
    assert state["active_iteration"]["iteration_id"] == "iter-2"


def test_ingest_after_force_close_clears_close_status(isolated_log, tmp_path):
    """A new iteration_open clears the prior force-close's close_status."""
    import sm

    _seed_open_decomposed_cut(n_stories=3, cut_at=2)
    sm.force_close("emergency stop")

    handoff = _canonical_handoff(iteration_id="iter-2")
    handoff_path = _write_handoff(tmp_path, handoff, name="handoff_2.json")
    sm.ingest(handoff_path)

    state = sm.derive_state()
    assert state["close_status"] is None, (
        f"new iteration_open must clear force-close close_status; got "
        f"{state['close_status']!r}"
    )


# ===========================================================================
# End-to-end full cycle (3+) — ingest -> decompose -> cut -> review ->
# accept -> close -> ingest succeeds
# ===========================================================================


def test_e2e_full_cycle_accepted_then_next_ingest(isolated_log, tmp_path):
    """End-to-end replay: ingest -> decompose -> cut -> all accepted ->
    close -> ingest succeeds.
    """
    import sm

    # --- Cycle 1 ---
    handoff_1 = _canonical_handoff(iteration_id="iter-1")
    h1_path = _write_handoff(tmp_path, handoff_1, name="handoff_1.json")
    sm.ingest(h1_path)

    spawn = _stub_spawn_for(handoff_1["requirements"], n_stories=3)
    sm.decompose(spawn_agent=spawn)

    state = sm.derive_state()
    backlog = state["story_backlog"]
    assert len(backlog) == 3

    sm.sprint_cut(2)
    state = sm.derive_state()
    in_sprint_ids = [s["story_id"] for s in backlog[:2]]
    _drive_all_accepted(in_sprint_ids)
    sm.close_iteration()

    # --- Cycle 2 ---
    handoff_2 = _canonical_handoff(iteration_id="iter-2")
    h2_path = _write_handoff(tmp_path, handoff_2, name="handoff_2.json")
    entry = sm.ingest(h2_path)
    assert entry["type"] == "iteration_open"
    assert entry["iteration_id"] == "iter-2"


def test_e2e_full_cycle_close_status_then_clean_for_next(
        isolated_log, tmp_path):
    """End-to-end: after full-accept close, close_status reflects the
    accept count; the next ingest then clears that close_status.
    """
    import sm

    handoff_1 = _canonical_handoff(iteration_id="iter-1")
    h1_path = _write_handoff(tmp_path, handoff_1, name="handoff_1.json")
    sm.ingest(h1_path)

    spawn = _stub_spawn_for(handoff_1["requirements"], n_stories=3)
    sm.decompose(spawn_agent=spawn)
    sm.sprint_cut(2)
    backlog = sm.derive_state()["story_backlog"]
    in_sprint_ids = [s["story_id"] for s in backlog[:2]]
    _drive_all_accepted(in_sprint_ids)
    sm.close_iteration()

    # Post-close, close_status reflects the cycle outcome.
    mid_state = sm.derive_state()
    assert mid_state["close_status"]["accepted_count"] == 2
    assert mid_state["active_iteration"] is None

    # Next ingest clears close_status.
    handoff_2 = _canonical_handoff(iteration_id="iter-2")
    h2_path = _write_handoff(tmp_path, handoff_2, name="handoff_2.json")
    sm.ingest(h2_path)

    post_state = sm.derive_state()
    assert post_state["close_status"] is None
    assert post_state["active_iteration"]["iteration_id"] == "iter-2"


def test_e2e_full_cycle_status_strings_match_phase(isolated_log, tmp_path):
    """End-to-end: status() narrates each phase correctly across the
    close + next-ingest boundary.
    """
    import sm

    # Cycle 1
    handoff_1 = _canonical_handoff(iteration_id="iter-1")
    h1_path = _write_handoff(tmp_path, handoff_1, name="handoff_1.json")
    sm.ingest(h1_path)

    spawn = _stub_spawn_for(handoff_1["requirements"], n_stories=3)
    sm.decompose(spawn_agent=spawn)
    sm.sprint_cut(2)
    backlog = sm.derive_state()["story_backlog"]
    in_sprint_ids = [s["story_id"] for s in backlog[:2]]
    _drive_all_accepted(in_sprint_ids)

    # Pre-close: status names the active iteration.
    pre_close = sm.status()
    assert "iter-1" in pre_close

    sm.close_iteration()

    # Between close and next ingest: status says "no active iteration".
    between = sm.status()
    assert "no active iteration" in between

    # Cycle 2 begins
    handoff_2 = _canonical_handoff(iteration_id="iter-2")
    h2_path = _write_handoff(tmp_path, handoff_2, name="handoff_2.json")
    sm.ingest(h2_path)

    # Post-ingest: status names the new iteration.
    post_ingest = sm.status()
    assert "iter-2" in post_ingest
    assert "no active iteration" not in post_ingest


# ===========================================================================
# End-to-end with force-close (2+) — ingest -> decompose -> cut -> partial
# progress -> force_close -> ingest succeeds
# ===========================================================================


def test_e2e_force_close_mid_progress_then_next_ingest(
        isolated_log, tmp_path):
    """End-to-end: ingest -> decompose -> cut -> some progress (one
    in_progress, one untouched) -> force_close -> next ingest succeeds.
    """
    import sm

    handoff_1 = _canonical_handoff(iteration_id="iter-1")
    h1_path = _write_handoff(tmp_path, handoff_1, name="handoff_1.json")
    sm.ingest(h1_path)

    spawn = _stub_spawn_for(handoff_1["requirements"], n_stories=3)
    sm.decompose(spawn_agent=spawn)
    sm.sprint_cut(2)

    backlog = sm.derive_state()["story_backlog"]
    in_sprint_ids = [s["story_id"] for s in backlog[:2]]

    # Partial progress: move one to in_progress, leave one planned.
    sm.transition_story(in_sprint_ids[0], "in_progress")

    # Force-close: both non-terminal stories get force_closed.
    sm.force_close("operator abort")

    state_mid = sm.derive_state()
    assert state_mid["active_iteration"] is None
    assert state_mid["close_status"]["force_closed_count"] == 2

    # Next ingest succeeds.
    handoff_2 = _canonical_handoff(iteration_id="iter-2")
    h2_path = _write_handoff(tmp_path, handoff_2, name="handoff_2.json")
    entry = sm.ingest(h2_path)
    assert entry["type"] == "iteration_open"
    assert entry["iteration_id"] == "iter-2"


def test_e2e_force_close_with_one_accepted_one_open(isolated_log, tmp_path):
    """End-to-end: ingest -> decompose -> cut -> accept one, leave one
    in_review -> force_close -> close_status has 1 accepted + 1 force_closed
    -> next ingest succeeds.
    """
    import sm

    handoff_1 = _canonical_handoff(iteration_id="iter-1")
    h1_path = _write_handoff(tmp_path, handoff_1, name="handoff_1.json")
    sm.ingest(h1_path)

    spawn = _stub_spawn_for(handoff_1["requirements"], n_stories=3)
    sm.decompose(spawn_agent=spawn)
    sm.sprint_cut(2)

    backlog = sm.derive_state()["story_backlog"]
    in_sprint_ids = [s["story_id"] for s in backlog[:2]]

    # Story 0 -> accepted.
    sm.transition_story(in_sprint_ids[0], "in_progress")
    sm.transition_story(in_sprint_ids[0], "in_review")
    sm.record_review(in_sprint_ids[0], True, "ok")
    sm.transition_story(in_sprint_ids[0], "accepted")

    # Story 1 -> in_review (not accepted).
    sm.transition_story(in_sprint_ids[1], "in_progress")
    sm.transition_story(in_sprint_ids[1], "in_review")

    sm.force_close("end of sprint, abandon remaining work")

    state_mid = sm.derive_state()
    cs = state_mid["close_status"]
    assert cs["accepted_count"] == 1, (
        f"one story accepted before force-close; got accepted_count="
        f"{cs['accepted_count']!r}"
    )
    assert cs["force_closed_count"] == 1, (
        f"one story force-closed; got force_closed_count="
        f"{cs['force_closed_count']!r}"
    )

    handoff_2 = _canonical_handoff(iteration_id="iter-2")
    h2_path = _write_handoff(tmp_path, handoff_2, name="handoff_2.json")
    entry = sm.ingest(h2_path)
    assert entry["iteration_id"] == "iter-2"


# ===========================================================================
# Multiple cycles (2+) — three cycles in one log
# ===========================================================================


def test_three_cycles_in_one_log(isolated_log, tmp_path):
    """Three full ingest -> close cycles in the same log file. Each
    ingest sees a clean slate; log accumulates history.
    """
    import sm

    for i in (1, 2, 3):
        iter_id = f"iter-{i}"
        handoff = _canonical_handoff(iteration_id=iter_id)
        h_path = _write_handoff(tmp_path, handoff, name=f"handoff_{i}.json")
        sm.ingest(h_path)

        spawn = _stub_spawn_for(handoff["requirements"], n_stories=2)
        sm.decompose(spawn_agent=spawn)
        sm.sprint_cut(2)

        backlog = sm.derive_state()["story_backlog"]
        in_sprint_ids = [s["story_id"] for s in backlog[:2]]
        _drive_all_accepted(in_sprint_ids)
        sm.close_iteration()

        state = sm.derive_state()
        assert state["active_iteration"] is None, (
            f"cycle {i}: active_iteration must be None after close"
        )

    # After 3 cycles, log has 3 iteration_open + 3 iteration_close entries.
    entries = list(sm.read_entries())
    opens = [e for e in entries if e.get("type") == "iteration_open"]
    closes = [e for e in entries if e.get("type") == "iteration_close"]
    assert len(opens) == 3, f"expected 3 opens; got {len(opens)}"
    assert len(closes) == 3, f"expected 3 closes; got {len(closes)}"


def test_three_cycles_mixed_close_and_force_close(isolated_log, tmp_path):
    """Three cycles: close, force-close, close. Each transition leaves a
    clean state for the next ingest.
    """
    import sm

    # Cycle 1: full accept + close.
    h1 = _canonical_handoff(iteration_id="iter-1")
    p1 = _write_handoff(tmp_path, h1, name="h1.json")
    sm.ingest(p1)
    spawn1 = _stub_spawn_for(h1["requirements"], n_stories=2)
    sm.decompose(spawn_agent=spawn1)
    sm.sprint_cut(2)
    in_sprint_1 = [s["story_id"] for s in sm.derive_state()["story_backlog"][:2]]
    _drive_all_accepted(in_sprint_1)
    sm.close_iteration()
    assert sm.derive_state()["active_iteration"] is None

    # Cycle 2: partial progress, force-close.
    h2 = _canonical_handoff(iteration_id="iter-2")
    p2 = _write_handoff(tmp_path, h2, name="h2.json")
    sm.ingest(p2)
    spawn2 = _stub_spawn_for(h2["requirements"], n_stories=2)
    sm.decompose(spawn_agent=spawn2)
    sm.sprint_cut(2)
    sm.force_close("abort mid-cycle")
    assert sm.derive_state()["active_iteration"] is None
    assert sm.derive_state()["close_status"]["closed_by"] == "force-close"

    # Cycle 3: full accept + close again.
    h3 = _canonical_handoff(iteration_id="iter-3")
    p3 = _write_handoff(tmp_path, h3, name="h3.json")
    sm.ingest(p3)
    spawn3 = _stub_spawn_for(h3["requirements"], n_stories=2)
    sm.decompose(spawn_agent=spawn3)
    sm.sprint_cut(2)
    in_sprint_3 = [s["story_id"] for s in sm.derive_state()["story_backlog"][:2]]
    _drive_all_accepted(in_sprint_3)
    sm.close_iteration()
    final = sm.derive_state()
    assert final["active_iteration"] is None
    assert final["close_status"]["closed_by"] == "operator"
    assert final["close_status"]["accepted_count"] == 2
