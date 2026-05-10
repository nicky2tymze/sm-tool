"""Story 7 — pin the DELTAS over Stories 5/6 for single-active-iteration
enforcement on `ingest`.

Story 5 already pinned the basic single-active rule (a fresh handoff
while another iteration is open raises ValueError, log unchanged).
Story 6 already pinned the typed exception (`IngestActiveError`) and the
distinct exit code (6).

Story 7 ADDS the following deltas, and this file pins ONLY those:

  1. REVISED-HANDOFF case: a handoff whose iteration_id MATCHES the
     currently-open iteration's id (i.e., a "revision" of the open
     iteration) must ALSO raise `IngestActiveError`, NOT
     `IngestDuplicateError`. The single-active-iteration check must
     fire FIRST when the iteration in question is currently open;
     duplicate-id only fires when no iteration is open. Pin the
     precedence.

  2. ERROR MESSAGE CONTENT: the message must
        (a) name the currently-open iteration_id, AND
        (b) instruct the operator to close before re-ingesting
            (the substring "close" appears in the message).

  3. DERIVE_STATE-DRIVEN CHECK (not flag-driven): the single-active
     check must use replay-derived state (no separate persisted "active
     flag"). We pin this by hand-crafting log entries via the canonical
     `build_entry` + `_append_entry` path and confirming the check
     still fires — i.e., the check observes the same world `derive_state`
     observes, not a sidecar marker.

ANTI-DUPLICATION: this file does NOT re-pin the broad ingest contract
already covered by `test_ingest.py` (happy path, shape errors, path
errors, JSON parse errors, byte-for-byte log invariants for unrelated
failure modes), nor the broad exit-code-table coverage already pinned
by `test_ingest_validation.py`. Read those files first; this one is
the Story 7 delta only.

These tests must FAIL on first run — Story 7's deltas are not yet
implemented (current code raises IngestDuplicateError when a same-id
revision lands while the iteration is still open, and the current
error message does not mention "close"). Once Coder implements them,
they pass.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers (mirror test_ingest.py / test_ingest_validation.py)
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


def _append_close(reason=None) -> None:
    """Append an iteration_close entry directly via the canonical
    builder. Story 14 will provide the real close command; for these
    tests we only need to free the active slot."""
    import sm
    close = sm.build_entry("iteration_close", {
        "closed_by": "test-harness",
        "reason": reason,
        "accepted_count": 0,
        "rejected_count": 0,
        "force_closed_count": 0,
    })
    sm._append_entry(close)


def _hand_craft_open(iteration_id: str, requirements=None) -> None:
    """Write an iteration_open entry directly via the canonical
    builder + append path — no `ingest()` call. Used to pin that the
    single-active check is derive_state-driven (sees ANY iteration_open
    in the log), not a sidecar-flag check.

    NOTE: bypassing ingest() means we skip ingest's validations — that
    is the POINT of these tests: confirm the check still fires when the
    log was written by *some* path other than ingest.
    """
    import sm
    if requirements is None:
        requirements = [_canonical_requirement("req-craft")]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": "hand-crafted",
        "requirements": list(requirements),
    })
    sm._append_entry(entry)


# ===========================================================================
# Smoke (2)
# ===========================================================================

def test_module_imports():
    """sm imports cleanly and exposes IngestActiveError + IngestDuplicateError —
    pins the test file is wired to the right module."""
    import sm
    assert hasattr(sm, "ingest")
    assert hasattr(sm, "IngestActiveError")
    assert hasattr(sm, "IngestDuplicateError")


def test_no_fixture_collision_with_other_files():
    """This file does not collide with test_ingest.py / test_ingest_validation.py.
    pytest treats each test_*.py as its own module — verify all three exist
    side-by-side and remain independent."""
    here = pathlib.Path(__file__).parent
    assert (here / "test_ingest.py").exists()
    assert (here / "test_ingest_validation.py").exists()
    assert (here / "test_single_active.py").exists()


# ===========================================================================
# Active-iteration blocks fresh handoff (different iteration_id) (5)
# Pins type, exit code surface, log-unchanged invariant, and message content.
# ===========================================================================

def test_fresh_id_while_open_raises_ingest_active_error(isolated_log, tmp_path):
    """A fresh (different) iteration_id while another iteration is open
    raises `IngestActiveError` specifically — not the generic ValueError,
    not IngestDuplicateError."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-open"),
                        name="h1.json")
    sm.ingest(p1)
    # iter-open is now active.

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-fresh"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError):
        sm.ingest(p2)


def test_fresh_id_while_open_exit_code_is_six(isolated_log, tmp_path,
                                               monkeypatch):
    """The CLI exit code for the single-active violation (fresh id) is
    EXIT_SINGLE_ACTIVE == 6 — Story 6's documented mapping holds for
    Story 7's same-class failure."""
    import sm

    # Seed an open iteration.
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-open"),
                        name="h1.json")
    sm.ingest(p1)

    # Drive _cli_main directly to honor the patched LOG_PATH (subprocess
    # would not see the monkeypatch; in-process is sufficient to pin the
    # exit-code mapping).
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-fresh"),
                        name="h2.json")
    rc = sm._cli_main(["ingest", str(p2)])
    assert rc == sm.EXIT_SINGLE_ACTIVE, (
        f"single-active exit code must be {sm.EXIT_SINGLE_ACTIVE} (6); "
        f"got {rc}"
    )


def test_fresh_id_while_open_log_byte_for_byte_unchanged(isolated_log,
                                                          tmp_path):
    """Failure invariant — fresh-id-while-open does not append anything."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-open"),
                        name="h1.json")
    sm.ingest(p1)
    bytes_before = isolated_log.read_bytes()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-fresh"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError):
        sm.ingest(p2)
    assert isolated_log.read_bytes() == bytes_before, (
        "single-active failure must leave the log byte-for-byte unchanged"
    )


def test_fresh_id_error_names_open_iteration_id(isolated_log, tmp_path):
    """The error message must NAME the currently-open iteration_id (so the
    operator knows what to close)."""
    import sm

    p1 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-named-open"),
                        name="h1.json")
    sm.ingest(p1)

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-fresh"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p2)
    assert "iter-named-open" in str(exc_info.value), (
        f"error must name the open iteration id 'iter-named-open'; "
        f"got {exc_info.value!r}"
    )


def test_fresh_id_error_instructs_close_before_reingesting(isolated_log,
                                                            tmp_path):
    """The error message must instruct the operator to close the open
    iteration before re-ingesting — pinned by the substring 'close'."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-open"),
                        name="h1.json")
    sm.ingest(p1)

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-fresh"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p2)
    msg = str(exc_info.value).lower()
    assert "close" in msg, (
        f"error must instruct operator to 'close' before re-ingesting; "
        f"got {exc_info.value!r}"
    )


# ===========================================================================
# Active-iteration blocks REVISED handoff (same iteration_id) (5)
# This is the core Story 7 delta: when a handoff whose iteration_id
# MATCHES the currently-open iteration is submitted, single-active fires
# FIRST — IngestActiveError, NOT IngestDuplicateError.
# ===========================================================================

def test_same_id_while_open_raises_ingest_active_not_duplicate(
    isolated_log, tmp_path
):
    """A revised handoff (iteration_id == currently-open id) must raise
    `IngestActiveError`, NOT `IngestDuplicateError`. The single-active
    check fires FIRST when the matching iteration is currently open.

    This is the key Story 7 precedence pin: dup-id is "this id was ever
    used"; single-active is "*something* is open right now". When both
    would fire, single-active wins so the operator gets the actionable
    message ("close it first") rather than the cosmetic one ("you've
    used this id before")."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-open"),
                        name="h1.json")
    sm.ingest(p1)

    # Same iteration_id as the open one — a "revision" attempt.
    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-open",
                                           iteration_goal="REVISED goal"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError):
        sm.ingest(p2)


def test_same_id_while_open_does_not_raise_ingest_duplicate(
    isolated_log, tmp_path
):
    """Strict precedence pin: ensure the raised exception is NOT
    `IngestDuplicateError`. Both classes inherit from ValueError, so a
    naive `pytest.raises(ValueError)` would pass on either. We must
    explicitly disqualify the dup-id class to pin the precedence.

    Note: IngestActiveError and IngestDuplicateError are siblings (both
    inherit from ValueError) — neither inherits from the other — so
    this assertion is meaningful."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-open"),
                        name="h1.json")
    sm.ingest(p1)

    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-open"),
                        name="h2.json")
    raised: Exception | None = None
    try:
        sm.ingest(p2)
    except Exception as e:  # noqa: BLE001 — this is the test
        raised = e
    assert raised is not None, "ingest must raise on same-id-while-open"
    assert isinstance(raised, sm.IngestActiveError), (
        f"expected IngestActiveError, got {type(raised).__name__}: {raised!r}"
    )
    assert not isinstance(raised, sm.IngestDuplicateError), (
        f"expected NOT IngestDuplicateError; got {type(raised).__name__}: "
        f"{raised!r}. Story 7 precedence: single-active fires before dup-id "
        f"when the matching iteration is currently open."
    )


def test_same_id_while_open_exit_code_is_six_not_five(isolated_log, tmp_path):
    """Same-id-while-open must surface as exit code 6 (single-active),
    NOT 5 (duplicate-id). The CLI mapping reflects the precedence."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-open"),
                        name="h1.json")
    sm.ingest(p1)

    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-open"),
                        name="h2.json")
    rc = sm._cli_main(["ingest", str(p2)])
    assert rc == sm.EXIT_SINGLE_ACTIVE, (
        f"same-id-while-open must exit {sm.EXIT_SINGLE_ACTIVE} "
        f"(single-active), not {sm.EXIT_DUP_ID} (dup-id); got {rc}"
    )


def test_same_id_while_open_error_names_open_id(isolated_log, tmp_path):
    """The error message for a revised same-id handoff must still name
    the currently-open iteration_id."""
    import sm

    p1 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-revising"),
                        name="h1.json")
    sm.ingest(p1)

    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-revising"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p2)
    assert "iter-revising" in str(exc_info.value), (
        f"error must name the open iteration id; got {exc_info.value!r}"
    )


def test_same_id_while_open_log_unchanged(isolated_log, tmp_path):
    """Failure invariant — a revised same-id handoff while the iteration
    is open does not append anything to the log."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-open"),
                        name="h1.json")
    sm.ingest(p1)
    bytes_before = isolated_log.read_bytes()

    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-open",
                                           iteration_goal="revised"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError):
        sm.ingest(p2)
    assert isolated_log.read_bytes() == bytes_before


# ===========================================================================
# After clean close, ingest succeeds (3)
# ===========================================================================

def test_after_clean_close_fresh_id_succeeds(isolated_log, tmp_path):
    """After a clean iteration_close, ingesting a NEW iteration_id
    succeeds — the single-active gate clears."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    _append_close()  # clean close, no reason

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    sm.ingest(p2)

    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-2"


def test_after_clean_close_derive_state_active_set(isolated_log, tmp_path):
    """After close-then-ingest, derive_state shows the new iteration as
    active with no leftover from the prior one."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    _append_close()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    sm.ingest(p2)

    state = sm.derive_state()
    assert state["active_iteration"] is not None
    assert state["active_iteration"]["iteration_id"] == "iter-2"
    # close_status cleared on new open per derive_state contract.
    assert state["close_status"] is None


def test_after_clean_close_log_has_three_entries(isolated_log, tmp_path):
    """The full open → close → open sequence produces exactly three log
    entries (open-1, close, open-2)."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    _append_close()
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    sm.ingest(p2)

    entries = list(sm.read_entries())
    assert len(entries) == 3, (
        f"expected 3 entries (open/close/open); got {len(entries)}"
    )
    assert entries[0]["type"] == "iteration_open"
    assert entries[1]["type"] == "iteration_close"
    assert entries[2]["type"] == "iteration_open"


# ===========================================================================
# After force-close (close with reason), ingest succeeds (2)
# ===========================================================================

def test_after_force_close_fresh_id_succeeds(isolated_log, tmp_path):
    """A force-close (iteration_close with a non-null reason) also frees
    the slot — subsequent ingest of a NEW id succeeds."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    _append_close(reason="force_closed_by_test")

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    sm.ingest(p2)

    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-2"


def test_after_force_close_derive_state_no_active_then_new_active(
    isolated_log, tmp_path
):
    """Mid-sequence pin: between the force-close and the next ingest,
    derive_state.active_iteration is None — proving the close cleared
    it before the next ingest re-set it."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    _append_close(reason="force")

    # Between close and next ingest: no active iteration.
    state_mid = sm.derive_state()
    assert state_mid["active_iteration"] is None

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    sm.ingest(p2)
    state_after = sm.derive_state()
    assert state_after["active_iteration"]["iteration_id"] == "iter-2"


# ===========================================================================
# derive_state-driven check (NOT flag-driven) (3)
# Hand-craft log entries via build_entry + _append_entry (bypassing ingest)
# to confirm the check observes replay-derived state — same world
# `derive_state` observes — not a sidecar marker.
# ===========================================================================

def test_check_fires_on_hand_crafted_open_entry(isolated_log, tmp_path):
    """Pin that the check is replay-driven: a hand-crafted iteration_open
    entry (written via build_entry + _append_entry, NOT through ingest())
    still triggers the single-active check on the next ingest call."""
    import sm

    # Hand-craft an iteration_open without going through ingest. The
    # single-active check must observe this entry just like derive_state
    # does — they read the same log.
    _hand_craft_open(iteration_id="iter-handcrafted")
    assert sm.derive_state()["active_iteration"]["iteration_id"] \
        == "iter-handcrafted"

    # Now attempt to ingest a different iteration_id. The check should
    # fire even though no `ingest()` ever produced the open entry.
    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-new"),
                       name="h.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p)
    assert "iter-handcrafted" in str(exc_info.value), (
        f"check must report the hand-crafted open id; got {exc_info.value!r}"
    )


def test_check_clears_on_hand_crafted_close_then_new_open(isolated_log,
                                                           tmp_path):
    """Pin replay-driven semantics on the close side: hand-craft an
    open, then a close, then a SECOND open — the check fires for the
    SECOND (currently-active) one, not the first (closed) one. This
    proves the check follows the replay state machine, not a "first
    iteration_open ever seen" heuristic."""
    import sm

    _hand_craft_open(iteration_id="iter-first-open")
    _append_close()  # closes iter-first-open
    _hand_craft_open(iteration_id="iter-second-open")
    assert sm.derive_state()["active_iteration"]["iteration_id"] \
        == "iter-second-open"

    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-new"),
                       name="h.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p)
    # The error must name the currently-open one (second), not the closed
    # one (first). This pins replay-state-driven over flag-on-first-open.
    assert "iter-second-open" in str(exc_info.value), (
        f"check must name the currently-open id (iter-second-open); "
        f"got {exc_info.value!r}"
    )


def test_check_does_not_fire_after_close_only_log(isolated_log, tmp_path):
    """Pin replay-driven semantics on the close side, the inverse:
    a log of [open, close] (nothing currently open) lets ingest succeed.
    Proves the check is NOT a "log non-empty → block" heuristic."""
    import sm

    _hand_craft_open(iteration_id="iter-was-open")
    _append_close()
    # State: nothing active, log non-empty.
    assert sm.derive_state()["active_iteration"] is None

    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-new"),
                       name="h.json")
    sm.ingest(p)  # must succeed; no exception expected

    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-new"


# ===========================================================================
# Message content invariants (5)
# Pin the regex/substring shape of the error message so the operator gets
# (a) the open id, (b) a "close" instruction.
# ===========================================================================

def test_message_contains_substring_close(isolated_log, tmp_path):
    """The error message contains the substring 'close' (case-insensitive)
    — operator-actionable instruction."""
    import sm

    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-msg"),
                        name="h1.json")
    sm.ingest(p1)
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-msg-2"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p2)
    assert re.search(r"close", str(exc_info.value), re.IGNORECASE), (
        f"message must mention 'close'; got {exc_info.value!r}"
    )


def test_message_contains_open_iteration_id_substring(isolated_log, tmp_path):
    """The error message contains the open iteration_id as a substring —
    pinned independently from the prior 'names the id' test, this time
    with a deliberately distinctive id to rule out coincidental matches."""
    import sm

    open_id = "iter-zzz-distinct-789"
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id=open_id),
                        name="h1.json")
    sm.ingest(p1)
    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-fresh"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p2)
    assert open_id in str(exc_info.value), (
        f"message must contain the open id substring {open_id!r}; "
        f"got {exc_info.value!r}"
    )


def test_message_close_and_id_both_present(isolated_log, tmp_path):
    """Joint pin: BOTH 'close' AND the open id appear in the same message
    — proves they aren't surfacing through different paths."""
    import sm

    open_id = "iter-joint-pin"
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id=open_id),
                        name="h1.json")
    sm.ingest(p1)
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-x"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p2)
    msg = str(exc_info.value)
    assert open_id in msg, f"missing open id; msg={msg!r}"
    assert re.search(r"close", msg, re.IGNORECASE), (
        f"missing 'close'; msg={msg!r}"
    )


def test_message_present_for_revised_handoff_too(isolated_log, tmp_path):
    """The message-content invariants hold even when the failure is a
    revised handoff (same iteration_id as the open one) — not just the
    fresh-id case. Story 7's message contract applies to the unified
    'iteration already open' path."""
    import sm

    open_id = "iter-rev-msg"
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id=open_id),
                        name="h1.json")
    sm.ingest(p1)
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id=open_id),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p2)
    msg = str(exc_info.value)
    assert open_id in msg, (
        f"revised-handoff message must name the open id; got {msg!r}"
    )
    assert re.search(r"close", msg, re.IGNORECASE), (
        f"revised-handoff message must mention 'close'; got {msg!r}"
    )


def test_message_present_when_open_was_hand_crafted(isolated_log, tmp_path):
    """Message-content invariants hold when the open entry was hand-crafted
    (via build_entry + _append_entry) — pins that the message wiring reads
    the open id from derive_state, not from a stashed cache from ingest()."""
    import sm

    _hand_craft_open(iteration_id="iter-hand-msg")
    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-new"),
                       name="h.json")
    with pytest.raises(sm.IngestActiveError) as exc_info:
        sm.ingest(p)
    msg = str(exc_info.value)
    assert "iter-hand-msg" in msg, (
        f"message must name the hand-crafted open id; got {msg!r}"
    )
    assert re.search(r"close", msg, re.IGNORECASE), (
        f"message must mention 'close'; got {msg!r}"
    )
