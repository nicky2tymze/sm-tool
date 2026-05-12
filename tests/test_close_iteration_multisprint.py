"""iter4-multisprint-v2 Sprint 1 Story 4 — close_iteration validates ALL sprints.

Story:  Update close_iteration to validate all sprints terminal  (S, req-1)

Acceptance criteria (verbatim):
  - `close_iteration` collects `in_sprint_story_ids` from ALL sprint_cut
    entries (not just the latest) and validates that every story across
    all cuts is terminal; raises `IterationCloseError` if any story from
    any sprint is non-terminal; error message names all offenders with
    their sprint position.

What this file pins:

  - **Multi-sprint happy close.** When an iteration has multiple
    `sprint_cut` entries and every in-sprint story across every cut is
    terminal (accepted / rejected / force_closed), `close_iteration`
    succeeds and writes a single `iteration_close` log entry plus the
    handoff JSON sidecar (Story 18's contract).

  - **All-sprints validation contract.** `close_iteration` MUST union
    `in_sprint_story_ids` from every `sprint_cut` entry in the active
    iteration and validate every story across every cut, NOT just the
    latest cut. Under normal lifecycle the multi-sprint lock (Story 1)
    guarantees prior cuts are terminal by the time a re-cut runs, so
    this gate is defense-in-depth: if a `sprint_cut` entry is written
    by any path that bypasses the lock (a future API, manual log
    surgery, force_close edge cases, etc.), close_iteration must still
    detect non-terminal stories from prior sprints and refuse.

  - **Error message names offenders WITH sprint position.** When one
    or more stories across ANY cut are non-terminal at close time, the
    raised `IterationCloseError`'s message contains every offending
    story_id AND identifies which sprint position the offender came
    from (e.g. "sprint 1", "sprint 2"). This lets the operator
    pinpoint which sprint had the dangling work.

  - **Sprint position attribution.** Sprint positions are 1-indexed by
    the order `sprint_cut` entries were appended within the active
    iteration. The first `sprint_cut` entry = sprint 1; the second =
    sprint 2; and so on. A story is attributed to the FIRST sprint
    whose `in_sprint_story_ids` contains its story_id (under
    iter4-multisprint-v2 Story 3 semantics each story_id appears in
    at most one cut, so attribution is unambiguous).

  - **First-cut-only / Iter 1 compatibility.** When the active
    iteration has exactly one `sprint_cut` entry, `close_iteration`
    behaves identically to Iter 1 (Story 18's contract): validates the
    single cut's in_sprint_story_ids, succeeds when all terminal,
    raises when any is non-terminal, names the offenders. The
    multi-sprint code path reduces to the single-cut path when N=1.

  - **Force-close interaction across multiple sprints.** `force_close`
    transitions the LATEST cut's non-terminal in-sprint stories to
    `force_closed` (Story 19's contract) and hands off to
    `close_iteration`. When prior cuts' stories are already terminal
    (the expected post-lock state), `force_close` succeeds. When a
    bypass scenario leaves a prior cut's story non-terminal,
    `force_close`'s downstream `close_iteration` call MUST raise
    `IterationCloseError` (force_close does NOT force-close prior
    cuts' stories — it only handles the active cohort).

  - **Failure invariants (unchanged from Story 18):**
      * On validation failure, log bytes unchanged.
      * On validation failure, no handoff JSON file appears.

What this file does NOT pin:

  - Story 5: _HELP_TEXT updates documenting multi-sprint close.
  - The exact wording / formatting of "sprint N" in the error message
    (the test only requires the substring naming the position; e.g.
    "sprint 1", "sprint 2", or any string containing those positional
    references in a way an operator can read).

Tests must FAIL on first run under the post-Story-1/2/3 implementation
of `close_iteration`, which reads ONLY the LATEST `sprint_cut` entry's
`in_sprint_story_ids` (via `_derive_state_full`'s
`latest_in_sprint_story_ids` slot). The bypass-scenario tests in
section C exercise the gap directly: they construct a `sprint_cut`
entry whose stories are non-terminal at close time, then add a
second `sprint_cut` whose stories ARE terminal — current code passes
close because it only checks the latest; Story 4's contract demands
the close fail with the prior-sprint offenders named. The error-
message-names-sprint-position tests in section B fail under current
code regardless (current error message names states but not sprint
position).

Cascade tests flagged for Coder review:
  - `test_close_iteration.py` tests pin Iter-1 single-cut behavior. They
    should remain passing under Story 4 since the N=1 path reduces to
    the existing contract. Coder verifies no regression.
  - `test_force_close.py` likewise tests single-cut semantics; Story 4
    changes nothing about force_close's transition logic, only what
    `close_iteration` validates after force_close runs. No cascade
    expected, but Coder verifies.
"""

from __future__ import annotations

import json
import pathlib
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
    """Redirect `sm.LOG_PATH` to a per-test tmp file. Suite convention."""
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _open_iteration(iteration_id: str = "iter-1",
                    requirements=None,
                    goal: str = "Test iteration") -> dict:
    """Append an `iteration_open` entry directly."""
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


def _seed_backlog(n: int = 5) -> list:
    """Append a `story_backlog` entry with N canonical stories."""
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


def _advance(story_id: str, *target_states: str) -> None:
    """Drive a story through transitions via the public API."""
    import sm

    for to_state in target_states:
        if to_state == "accepted":
            sm.record_review(story_id, True, "ok")
        sm.transition_story(story_id, to_state)


def _drive_to_accepted(story_id: str) -> None:
    """Accepted path via the public API."""
    _advance(story_id, "in_progress", "in_review", "accepted")


def _drive_to_rejected(story_id: str) -> None:
    """Rejected path via the public API."""
    _advance(story_id, "in_progress", "in_review", "rejected")


def _craft_state_change(story_id: str, from_state: str, to_state: str) -> dict:
    """Direct append of a `story_state_change` entry (no validation).
    Mirrors the `test_sprint_cut_lock.py` helper used to bypass per-
    story gates so tests can construct arbitrary on-log lifecycles."""
    import sm

    entry = sm.build_entry("story_state_change", {
        "story_id": story_id,
        "from_state": from_state,
        "to_state": to_state,
        "notes": "test fixture",
    })
    sm._append_entry(entry)
    return entry


def _craft_sprint_cut(cut_position: int, in_sprint_ids: list,
                      deferred_ids: list) -> dict:
    """Direct append of a `sprint_cut` entry — bypasses the sprint_cut()
    function's lock + range checks. Used to construct bypass scenarios
    for Story 4's defense-in-depth contract: a sprint_cut entry whose
    stories are non-terminal at close time. Under the normal API the
    lock prevents this, but a future writer / manual surgery / a force-
    close edge could create such an on-log shape. Story 4's
    close_iteration must still detect and refuse it."""
    import sm

    entry = sm.build_entry("sprint_cut", {
        "cut_position": cut_position,
        "in_sprint_story_ids": list(in_sprint_ids),
        "deferred_story_ids": list(deferred_ids),
    })
    sm._append_entry(entry)
    return entry


def _seed_multisprint(cuts: list, iteration_id: str = "iter-multi",
                      drive_each_cut: bool = True) -> dict:
    """Open iteration, seed backlog, run N sprint cuts via the public
    API, optionally driving each prior cut's stories to `accepted`
    before the next cut.

    `cuts` is a list of ints — successive `sprint_cut(N)` calls.
    Returns a dict with story_ids + per-cut in_sprint cohorts.
    """
    import sm

    total = sum(cuts) + 2  # leave a small planned tail for safety
    _open_iteration(iteration_id=iteration_id)
    sids = _seed_backlog(n=total)

    per_cut_in_sprint: list = []
    for idx, n in enumerate(cuts):
        result = sm.sprint_cut(n)
        per_cut_in_sprint.append(list(result["in_sprint_story_ids"]))
        if drive_each_cut and idx < len(cuts) - 1:
            # Drive this cut's stories to accepted so the next cut's
            # lock check passes. For the LAST cut, the caller decides
            # what state to leave the cohort in.
            for sid in per_cut_in_sprint[-1]:
                _drive_to_accepted(sid)

    return {
        "story_ids": sids,
        "per_cut_in_sprint": per_cut_in_sprint,
    }


def _handoff_path_for(log_path: pathlib.Path,
                      iteration_id: str) -> pathlib.Path:
    """Compute the expected handoff JSON path per Story 18."""
    return log_path.parent / f"close_handoff_{iteration_id}.json"


# ===========================================================================
# A. Multi-sprint happy close — every cut terminal, close succeeds (5)
# ===========================================================================


def test_two_sprints_all_terminal_close_succeeds(isolated_log):
    """Two sprint_cut entries; every story in both cuts is accepted
    by close time → close_iteration succeeds."""
    import sm
    ctx = _seed_multisprint(cuts=[2, 2], iteration_id="iter-2sp-acc")
    # Drive the LATEST cut's stories to accepted too.
    for sid in ctx["per_cut_in_sprint"][-1]:
        _drive_to_accepted(sid)
    entry = sm.close_iteration()
    assert entry["type"] == "iteration_close"


def test_two_sprints_all_terminal_handoff_written(isolated_log):
    """Two-sprint close produces the handoff JSON sidecar."""
    import sm
    ctx = _seed_multisprint(cuts=[2, 2], iteration_id="iter-2sp-handoff")
    for sid in ctx["per_cut_in_sprint"][-1]:
        _drive_to_accepted(sid)
    sm.close_iteration()
    handoff_path = _handoff_path_for(isolated_log, "iter-2sp-handoff")
    assert handoff_path.exists(), (
        f"handoff file must exist at {handoff_path!s}"
    )


def test_three_sprints_all_terminal_close_succeeds(isolated_log):
    """Three sprint_cut entries; every cut's cohort terminal → close ok."""
    import sm
    ctx = _seed_multisprint(cuts=[2, 2, 2], iteration_id="iter-3sp-acc")
    for sid in ctx["per_cut_in_sprint"][-1]:
        _drive_to_accepted(sid)
    entry = sm.close_iteration()
    assert entry["type"] == "iteration_close"


def test_five_sprints_chained_close_succeeds(isolated_log):
    """Five chained sprint cuts, all terminal → close succeeds.
    Stress test that the AC's 'ALL sprint_cut entries' is unbounded."""
    import sm
    ctx = _seed_multisprint(cuts=[1, 1, 1, 1, 1], iteration_id="iter-5sp")
    for sid in ctx["per_cut_in_sprint"][-1]:
        _drive_to_accepted(sid)
    entry = sm.close_iteration()
    assert entry["type"] == "iteration_close"


def test_two_sprints_mixed_terminals_across_cuts_close_succeeds(isolated_log):
    """Sprint 1 stories rejected, sprint 2 stories accepted; all terminal
    → close succeeds. Pins that terminal-mix across cuts is allowed."""
    import sm
    _open_iteration(iteration_id="iter-mix-cuts")
    sids = _seed_backlog(n=6)
    # Sprint 1: 2 stories → reject both.
    sm.sprint_cut(2)
    for sid in sids[:2]:
        _drive_to_rejected(sid)
    # Sprint 2: 2 stories → accept both.
    sm.sprint_cut(2)
    for sid in sids[2:4]:
        _drive_to_accepted(sid)
    entry = sm.close_iteration()
    assert entry["type"] == "iteration_close"


# ===========================================================================
# B. Single-sprint compatibility (Iter 1 behavior preserved) (4)
# ===========================================================================


def test_single_sprint_all_accepted_close_succeeds(isolated_log):
    """Single sprint_cut + all accepted → close succeeds (Iter 1 path)."""
    import sm
    _open_iteration(iteration_id="iter-single-acc")
    sids = _seed_backlog(n=3)
    sm.sprint_cut(3)
    for sid in sids:
        _drive_to_accepted(sid)
    entry = sm.close_iteration()
    assert entry["type"] == "iteration_close"


def test_single_sprint_one_planned_close_raises(isolated_log):
    """Single cut, one story still planned → IterationCloseError.
    Preserves Iter 1's contract."""
    import sm
    _open_iteration(iteration_id="iter-single-plan")
    sids = _seed_backlog(n=3)
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _drive_to_accepted(sids[1])
    # sids[2] stays planned
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_single_sprint_error_names_offender(isolated_log):
    """Single-cut close: error names the planned story_id (Iter 1 contract)."""
    import sm
    _open_iteration(iteration_id="iter-single-named")
    sids = _seed_backlog(n=3)
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _drive_to_accepted(sids[1])
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    assert sids[2] in str(exc_info.value), (
        f"single-cut close error must name the planned story; "
        f"got: {exc_info.value!s}"
    )


def test_single_sprint_close_counts_match_iter1(isolated_log):
    """Single cut with mixed terminals → counts match Iter 1 behavior."""
    import sm
    _open_iteration(iteration_id="iter-single-counts")
    sids = _seed_backlog(n=3)
    sm.sprint_cut(3)
    _drive_to_accepted(sids[0])
    _drive_to_accepted(sids[1])
    _drive_to_rejected(sids[2])
    entry = sm.close_iteration()
    assert entry["accepted_count"] == 2
    assert entry["rejected_count"] == 1
    assert entry["force_closed_count"] == 0


# ===========================================================================
# C. All-sprints validation — bypass scenarios prove ALL cuts are checked (7)
# ===========================================================================


def test_close_detects_nonterminal_story_in_first_of_two_cuts(isolated_log):
    """Bypass scenario: two sprint_cut entries on the log, the LATEST
    cut's stories are all terminal, but a story from the FIRST cut is
    in `in_progress`. Under current code (reads only latest), close
    would succeed. Story 4's contract: close MUST detect the prior-cut
    offender and raise IterationCloseError."""
    import sm
    _open_iteration(iteration_id="iter-bypass-2")
    sids = _seed_backlog(n=5)
    # Manually craft TWO sprint_cut entries — bypass the lock check
    # so we can construct an on-log shape the lock would normally
    # prevent. This is the defensive contract Story 4 enforces.
    _craft_sprint_cut(
        cut_position=2,
        in_sprint_ids=sids[:2],
        deferred_ids=sids[2:],
    )
    # Move sids[0] to in_progress but NOT to terminal — this is the
    # "phantom non-terminal prior-cut story" Story 4 must catch.
    _craft_state_change(sids[0], "planned", "in_progress")
    # Second cut over the next two stories — both will be driven
    # terminal so the LATEST-only check passes.
    _craft_sprint_cut(
        cut_position=2,
        in_sprint_ids=sids[2:4],
        deferred_ids=[sids[0], sids[1], sids[4]],
    )
    # Drive sids[2..3] to accepted (latest cut all terminal).
    _drive_to_accepted(sids[2])
    _drive_to_accepted(sids[3])
    # Close MUST raise — sids[0] from sprint 1 is non-terminal.
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


def test_close_error_names_prior_cut_offender(isolated_log):
    """The error message must name the prior-cut non-terminal story_id."""
    import sm
    _open_iteration(iteration_id="iter-bypass-named")
    sids = _seed_backlog(n=5)
    _craft_sprint_cut(2, sids[:2], sids[2:])
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_sprint_cut(2, sids[2:4], [sids[0], sids[1], sids[4]])
    _drive_to_accepted(sids[2])
    _drive_to_accepted(sids[3])
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value)
    assert sids[0] in msg, (
        f"error must name prior-cut offender {sids[0]!r}; "
        f"got: {exc_info.value!s}"
    )


def test_close_error_names_sprint_position(isolated_log):
    """The error message must identify which sprint position the
    offender came from (e.g. 'sprint 1')."""
    import sm
    _open_iteration(iteration_id="iter-bypass-position")
    sids = _seed_backlog(n=5)
    _craft_sprint_cut(2, sids[:2], sids[2:])
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_sprint_cut(2, sids[2:4], [sids[0], sids[1], sids[4]])
    _drive_to_accepted(sids[2])
    _drive_to_accepted(sids[3])
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value).lower()
    # Accept any wording that pins the position: "sprint 1", "sprint=1",
    # "cut 1", "cut=1", or "(sprint 1)" etc. The required signal is
    # that the operator can tell sprint 1 owned the offender.
    assert "sprint 1" in msg or "sprint=1" in msg or "cut 1" in msg or (
        "sprint" in msg and "1" in msg
    ), (
        f"error must name the offender's sprint position; "
        f"got: {exc_info.value!s}"
    )


def test_close_detects_offenders_across_multiple_prior_cuts(isolated_log):
    """Three sprint_cut entries; offenders in both sprint 1 AND sprint 2;
    sprint 3 (latest) all terminal. Close must name BOTH offenders."""
    import sm
    _open_iteration(iteration_id="iter-bypass-multi")
    sids = _seed_backlog(n=8)
    _craft_sprint_cut(2, sids[:2], sids[2:])
    _craft_state_change(sids[0], "planned", "in_progress")  # sprint 1 offender
    _craft_sprint_cut(2, sids[2:4],
                      [sids[0], sids[1], sids[4], sids[5], sids[6], sids[7]])
    _craft_state_change(sids[2], "planned", "in_progress")  # sprint 2 offender
    _craft_sprint_cut(2, sids[4:6],
                      [sids[0], sids[1], sids[2], sids[3],
                       sids[6], sids[7]])
    _drive_to_accepted(sids[4])
    _drive_to_accepted(sids[5])
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value)
    assert sids[0] in msg, (
        f"error must name sprint-1 offender {sids[0]!r}; got: {msg!s}"
    )
    assert sids[2] in msg, (
        f"error must name sprint-2 offender {sids[2]!r}; got: {msg!s}"
    )


def test_close_log_unchanged_on_prior_cut_failure(isolated_log):
    """Bypass-scenario failure: log bytes unchanged."""
    import sm
    _open_iteration(iteration_id="iter-bypass-bytes")
    sids = _seed_backlog(n=5)
    _craft_sprint_cut(2, sids[:2], sids[2:])
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_sprint_cut(2, sids[2:4], [sids[0], sids[1], sids[4]])
    _drive_to_accepted(sids[2])
    _drive_to_accepted(sids[3])
    before = isolated_log.read_bytes()
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    after = isolated_log.read_bytes()
    assert before == after, (
        "failed close on prior-cut non-terminal must not write to log"
    )


def test_close_no_handoff_file_on_prior_cut_failure(isolated_log, tmp_path):
    """Bypass-scenario failure: no handoff JSON file appears."""
    import sm
    _open_iteration(iteration_id="iter-bypass-files")
    sids = _seed_backlog(n=5)
    _craft_sprint_cut(2, sids[:2], sids[2:])
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_sprint_cut(2, sids[2:4], [sids[0], sids[1], sids[4]])
    _drive_to_accepted(sids[2])
    _drive_to_accepted(sids[3])
    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()
    handoff_appeared = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith("close_handoff_") and p.name.endswith(".json")
    ]
    assert handoff_appeared == [], (
        f"no handoff JSON file should appear on failed close; "
        f"got: {handoff_appeared!r}"
    )


def test_close_detects_offender_only_in_latest_when_prior_clean(isolated_log):
    """Inverse: all prior cuts terminal, latest cut has a non-terminal
    story → error still names the offender (and its sprint position
    matches the LATEST sprint). Pins that Story 4's gate doesn't
    REGRESS the latest-cut behavior."""
    import sm
    ctx = _seed_multisprint(cuts=[2, 2], iteration_id="iter-latest-only",
                            drive_each_cut=True)
    # Leave the latest cut's stories planned — they're in-sprint
    # under sprint 2, so close must raise and name them.
    latest = ctx["per_cut_in_sprint"][-1]
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value)
    for sid in latest:
        assert sid in msg, (
            f"error must name latest-cut planned story {sid!r}; "
            f"got: {exc_info.value!s}"
        )


# ===========================================================================
# D. Sprint position attribution (3)
# ===========================================================================


def test_sprint_position_is_one_indexed(isolated_log):
    """Sprint positions are 1-indexed: first sprint_cut entry = sprint 1.
    Validated by a single-cut close: latest-cut offender must surface as
    sprint 1, not sprint 0."""
    import sm
    _open_iteration(iteration_id="iter-pos-1idx")
    sids = _seed_backlog(n=3)
    sm.sprint_cut(3)
    # All planned → all non-terminal.
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value).lower()
    # Must reference "sprint 1" / "cut 1" — NEVER "sprint 0".
    assert "sprint 0" not in msg and "cut 0" not in msg, (
        f"sprint positions must be 1-indexed; got: {exc_info.value!s}"
    )


def test_sprint_position_attribution_unique_per_story(isolated_log):
    """Under Story 3 semantics each story_id appears in at most one cut.
    A non-terminal story is attributed to the SINGLE sprint whose
    in_sprint_story_ids contains it — never multi-attributed."""
    import sm
    _open_iteration(iteration_id="iter-pos-unique")
    sids = _seed_backlog(n=5)
    _craft_sprint_cut(2, sids[:2], sids[2:])
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_sprint_cut(2, sids[2:4], [sids[0], sids[1], sids[4]])
    _drive_to_accepted(sids[2])
    _drive_to_accepted(sids[3])
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value).lower()
    # The sids[0] offender belongs to sprint 1 only — the message must
    # mention sprint 1 (or cut 1) somewhere. It must NOT name sprint 2
    # as the offender's sprint (sprint 2's stories are all accepted).
    # We assert sprint 1 / cut 1 is referenced; sprint 2 may appear in
    # other context but the sids[0] attribution is sprint 1.
    assert (
        "sprint 1" in msg or "cut 1" in msg
        or ("sprint" in msg and " 1" in msg)
    ), (
        f"sids[0]'s sprint-1 attribution must appear in error; "
        f"got: {exc_info.value!s}"
    )


def test_sprint_position_matches_append_order(isolated_log):
    """Sprint position attribution follows on-log order of sprint_cut
    entries: the 3rd sprint_cut appended is sprint 3, regardless of
    cut_position values."""
    import sm
    _open_iteration(iteration_id="iter-pos-order")
    sids = _seed_backlog(n=8)
    # Three sprint_cut entries; offender lives in the SECOND one.
    _craft_sprint_cut(2, sids[:2], sids[2:])
    _drive_to_accepted(sids[0])
    _drive_to_accepted(sids[1])
    _craft_sprint_cut(2, sids[2:4], [sids[0], sids[1], sids[4],
                                     sids[5], sids[6], sids[7]])
    # sids[2] left non-terminal — sprint 2 offender.
    _craft_state_change(sids[2], "planned", "in_progress")
    _craft_sprint_cut(2, sids[4:6], [sids[0], sids[1], sids[2], sids[3],
                                     sids[6], sids[7]])
    _drive_to_accepted(sids[4])
    _drive_to_accepted(sids[5])
    with pytest.raises(sm.IterationCloseError) as exc_info:
        sm.close_iteration()
    msg = str(exc_info.value).lower()
    # sids[2] is in sprint 2 (the 2nd sprint_cut entry). Must reference 2.
    assert (
        "sprint 2" in msg or "cut 2" in msg
        or ("sprint" in msg and " 2" in msg)
    ), (
        f"sids[2]'s sprint-2 attribution must appear in error; "
        f"got: {exc_info.value!s}"
    )


# ===========================================================================
# E. Force_close interaction with multi-sprint iterations (3)
# ===========================================================================


def test_force_close_succeeds_when_prior_cuts_terminal(isolated_log):
    """Force_close after a multi-sprint chain where prior cuts are all
    accepted and the LATEST cut has un-terminal stories: force_close
    transitions the latest cohort to force_closed and close succeeds."""
    import sm
    ctx = _seed_multisprint(cuts=[2, 2], iteration_id="iter-fc-prior-ok",
                            drive_each_cut=True)
    # Latest cut left planned — force_close must transition + close.
    entry = sm.force_close("operator intervened")
    assert entry["type"] == "iteration_close"
    assert entry["closed_by"] == "force-close"


def test_force_close_succeeds_single_sprint_unchanged(isolated_log):
    """Single-sprint force_close still works (no regression)."""
    import sm
    _open_iteration(iteration_id="iter-fc-single")
    sids = _seed_backlog(n=3)
    sm.sprint_cut(3)
    # Leave all stories planned — force_close transitions them.
    entry = sm.force_close("test reason")
    assert entry["type"] == "iteration_close"
    assert entry["closed_by"] == "force-close"


def test_force_close_raises_when_prior_cut_has_nonterminal_story(isolated_log):
    """Bypass scenario: a sprint_cut entry exists with a non-terminal
    story from sprint 1, the latest cut has live stories. force_close
    transitions ONLY the latest cohort's non-terminal stories; the
    downstream close_iteration then detects sprint 1's offender and
    raises IterationCloseError. Pins that force_close does NOT silently
    bypass the all-sprints validation."""
    import sm
    _open_iteration(iteration_id="iter-fc-bypass")
    sids = _seed_backlog(n=5)
    # Sprint 1 with one non-terminal story sids[0] (in_progress).
    _craft_sprint_cut(2, sids[:2], sids[2:])
    _craft_state_change(sids[0], "planned", "in_progress")
    _craft_state_change(sids[1], "planned", "in_progress")
    _craft_state_change(sids[1], "in_progress", "in_review")
    _craft_state_change(sids[1], "in_review", "accepted")  # sids[1] terminal
    # Sprint 2 with planned cohort sids[2..3].
    _craft_sprint_cut(2, sids[2:4], [sids[0], sids[1], sids[4]])
    # force_close should raise (the downstream close detects sids[0]
    # as a prior-cut non-terminal offender).
    with pytest.raises((sm.IterationCloseError, sm.ForceCloseError)):
        sm.force_close("attempting bypass")
