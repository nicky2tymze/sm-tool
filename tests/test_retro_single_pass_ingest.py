"""Iter 2 Story 11 — Retro polish: single-pass ingest replay and state enrichment.

Story 11 (size M, behavior-preserving for end-users) closes 2 retro items from
Iter 1:

  - Retro item 7: ``ingest`` currently walks the log TWICE — once via
    ``derive_state()`` (the single-active check) and a second time via a
    separate ``read_entries()`` loop for the duplicate-iteration-id check
    (current sm.py line 1105). After Story 11, ingest walks the log ONCE.
    The consolidated walk yields both the derived state (in particular
    ``active_iteration``) AND the set of prior ``iteration_open`` ids
    (``seen_iteration_ids``). The Story spec wording:

        "Consolidates the two ingest-time log walks (`derive_state` walk +
         dup-id loop walk, retro item 7) into a single pass that returns
         both `active_iteration` and the `seen_iteration_ids` set; ingest
         consumes both from one call."

    The Coder picks the exact mechanism (helper that returns a tuple,
    a richer derive_state output, an optional kwarg, etc.). These tests
    pin the *observable* characteristic — ingest reads each log entry
    exactly once per ingest call — without prescribing the shape of the
    internal helper.

  - Retro item 10: ``derive_state``'s returned dict currently does NOT
    carry ``iteration_goal``. ``close_iteration`` (current sm.py line
    2313) re-scans the log just to recover the goal. After Story 11,
    ``derive_state`` carries ``iteration_goal`` on its output (populated
    from the active ``iteration_open`` entry, mirroring how
    ``active_iteration`` is populated), and ``close_iteration`` reads
    the goal from the state object rather than re-scanning. Story spec:

        "Enriches `derive_state` output to carry `iteration_goal` (retro
         item 10) populated from the `iteration_open` entry on replay."

        "`close_iteration` reads `iteration_goal` from the derived state
         object instead of re-scanning the log; a grep confirms
         `close_iteration` no longer iterates `read_entries()`."

What this file pins (target 30-50 tests):

  Category A — derive_state iteration_goal enrichment
    * iteration_goal key present on every derive_state() return
    * iteration_goal == the goal from the active iteration_open entry
    * iteration_goal is None when no active iteration (matches the
      ``active_iteration is None`` convention)
    * iteration_goal preserved across the decompose / sprint_cut /
      transition lifecycle while the iteration is open
    * iteration_goal returns to None after iteration_close
    * iteration_goal matches the log byte-for-byte across edge cases
      (empty string, unicode, multiline, very long)

  Category B — single-pass ingest
    * ingest() reads each log entry exactly once per call (counted by
      monkeypatching ``sm.read_entries`` with a call-counting wrapper)
    * the duplicate-iteration-id behavior is preserved: a closed-then-
      re-ingest with the same id still raises IngestDuplicateError
    * other ingest validations (shape, single-active) still fire
    * the appended iteration_open entry shape is unchanged
    * ingest's failure-invariant (log byte-for-byte unchanged on
      validation failure) is preserved

  Category C — close_iteration reads from state
    * close_iteration's function source contains zero ``read_entries``
      calls (the goal-recovery walk is gone). A static grep over
      ``inspect.getsource(sm.close_iteration)`` is the canonical pin.
    * close_iteration still writes a handoff JSON whose iteration_goal
      matches the original iteration_open entry's goal
    * close_iteration still handles None / empty / unicode / multiline
      goals correctly
    * close_iteration's end-to-end behavior (single iteration_close log
      entry appended, handoff file created at LOG_PATH.parent) is
      unchanged

  Category D — regression smoke
    * full ingest -> decompose -> sprint_cut -> accept -> close flow
      still works
    * multiple iterations sequenced (close one, open another) — the
      derived ``iteration_goal`` switches correctly
    * force_close path still produces a handoff with the correct goal
    * aggregate_requirements still works against the (now-enriched)
      state dict

Tests must FAIL on first run — Story 11 has not been implemented yet.
The Coder downstream implements the consolidation to satisfy these.

These tests are ADDITIVE — Story 11 is behavior-preserving for every
existing public contract. The full Iter 1+2 suite (2258/2258) must stay
green after Story 11 lands.

Invocation contract: ``import sm`` — no subprocess required for the core
pins. The single-pass refactor is purely internal.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re
import sys

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect ``sm.LOG_PATH`` to a per-test tmp file. Mirrors suite
    convention (test_derive_state.py, test_close_iteration.py)."""
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _canonical_requirement(rid: str = "req-1", title: str = "T1") -> dict:
    return {
        "requirement_id": rid,
        "title": title,
        "description": "D",
        "priority": "MUST",
        "acceptance_criteria": "AC",
    }


def _canonical_handoff(iteration_id: str = "iter-1",
                       iteration_goal: str = "Ship the thing.",
                       requirements=None) -> dict:
    if requirements is None:
        requirements = [_canonical_requirement("req-1", "T1")]
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


def _open_iteration_directly(iteration_id: str = "iter-1",
                             iteration_goal: str = "Ship the thing.",
                             requirements=None) -> dict:
    """Append an ``iteration_open`` entry via the canonical build_entry +
    _append_entry path. Bypasses ingest()'s validations."""
    import sm

    if requirements is None:
        requirements = [_canonical_requirement("req-1", "T1")]
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": iteration_goal,
        "requirements": list(requirements),
    })
    sm._append_entry(entry)
    return entry


def _append_close(iteration_id: str = "iter-1",
                  closed_by: str = "operator",
                  reason=None) -> dict:
    """Append a minimal ``iteration_close`` entry directly. Used to free
    the active slot when we don't need to exercise close_iteration's
    sidecar path."""
    import sm

    entry = sm.build_entry("iteration_close", {
        "iteration_id": iteration_id,
        "closed_by": closed_by,
        "reason": reason,
        "accepted_count": 0,
        "rejected_count": 0,
        "force_closed_count": 0,
    })
    sm._append_entry(entry)
    return entry


def _make_read_counter(monkeypatch):
    """Wrap ``sm.read_entries`` with a call-counting proxy. Returns a
    dict {"calls": int} that the test can inspect after the SUT runs.

    The wrapper preserves iterator semantics — it returns a fresh
    iterator on each call by delegating to the original
    ``sm.read_entries``. Each invocation of the wrapped function
    increments ``state["calls"]`` by 1.

    NOTE: this is the canonical pin for retro item 7. The contract is
    "ingest walks the log once" — operationalized as "ingest invokes
    read_entries exactly once". A direct helper that internally calls
    read_entries also counts.
    """
    import sm

    orig = sm.read_entries
    state = {"calls": 0}

    def _counting_read_entries(*args, **kwargs):
        state["calls"] += 1
        # Delegate to the real read_entries; yield to keep iterator
        # semantics. Using ``yield from`` preserves laziness.
        yield from orig(*args, **kwargs)

    monkeypatch.setattr(sm, "read_entries", _counting_read_entries)
    return state


def _close_iteration_source() -> str:
    """Return the source text of ``close_iteration``."""
    import sm
    return inspect.getsource(sm.close_iteration)


def _ingest_source() -> str:
    """Return the source text of ``ingest``."""
    import sm
    return inspect.getsource(sm.ingest)


# ===========================================================================
# Category A — derive_state iteration_goal enrichment (12 tests)
# ===========================================================================


def test_derive_state_returned_dict_has_iteration_goal_key(isolated_log):
    """Every derive_state() call returns a dict with an ``iteration_goal``
    key (even on an empty log). Mirrors how ``active_iteration`` is always
    present as a key with value None when no iteration is open."""
    import sm

    state = sm.derive_state()
    assert "iteration_goal" in state, (
        "Story 11 retro item 10: derive_state's returned dict must contain "
        "the key 'iteration_goal' on every call (including empty-log calls), "
        "mirroring the 'active_iteration' convention. Got keys: "
        f"{sorted(state.keys())!r}"
    )


def test_derive_state_iteration_goal_none_on_empty_log(isolated_log):
    """Empty log -> iteration_goal is None (matches active_iteration None
    convention)."""
    import sm

    state = sm.derive_state()
    assert state["iteration_goal"] is None, (
        "On an empty log, derive_state's iteration_goal must be None "
        "(mirrors active_iteration's None convention); got "
        f"{state['iteration_goal']!r}"
    )


def test_derive_state_iteration_goal_set_after_iteration_open(isolated_log):
    """After a single iteration_open, derive_state's iteration_goal
    matches the entry's iteration_goal value exactly."""
    import sm

    _open_iteration_directly(iteration_id="iter-1",
                             iteration_goal="Ship the alpha.")
    state = sm.derive_state()
    assert state["iteration_goal"] == "Ship the alpha.", (
        f"derive_state iteration_goal must equal the iteration_open entry's "
        f"iteration_goal; got {state['iteration_goal']!r}"
    )


def test_derive_state_iteration_goal_matches_active_iteration_id(isolated_log):
    """After iteration_open, iteration_goal AND active_iteration are both
    populated (consistent state — never one without the other)."""
    import sm

    _open_iteration_directly(iteration_id="iter-x",
                             iteration_goal="Goal X")
    state = sm.derive_state()
    assert state["active_iteration"] is not None
    assert state["iteration_goal"] == "Goal X"
    assert state["active_iteration"]["iteration_id"] == "iter-x"


def test_derive_state_iteration_goal_none_after_iteration_close(isolated_log):
    """After iteration_close, iteration_goal returns to None — same
    lifecycle as active_iteration."""
    import sm

    _open_iteration_directly(iteration_id="iter-1",
                             iteration_goal="Goal that will be closed.")
    _append_close(iteration_id="iter-1")
    state = sm.derive_state()
    assert state["active_iteration"] is None
    assert state["iteration_goal"] is None, (
        f"After iteration_close, derive_state's iteration_goal must reset "
        f"to None (mirrors active_iteration None); got "
        f"{state['iteration_goal']!r}"
    )


def test_derive_state_iteration_goal_switches_across_sequential_iterations(
    isolated_log,
):
    """Close one iteration, open another — iteration_goal switches to the
    new one's goal."""
    import sm

    _open_iteration_directly(iteration_id="iter-A", iteration_goal="A goal")
    _append_close(iteration_id="iter-A")
    _open_iteration_directly(iteration_id="iter-B", iteration_goal="B goal")
    state = sm.derive_state()
    assert state["iteration_goal"] == "B goal", (
        f"iteration_goal must switch to the newly-opened iteration's goal; "
        f"got {state['iteration_goal']!r}"
    )


def test_derive_state_iteration_goal_empty_string_preserved(isolated_log):
    """Empty-string goal is preserved as the empty string (not coerced to
    None). The handoff schema permits an empty goal."""
    import sm

    _open_iteration_directly(iteration_id="iter-1", iteration_goal="")
    state = sm.derive_state()
    assert state["iteration_goal"] == "", (
        f"empty-string iteration_goal must be preserved exactly (not "
        f"coerced to None); got {state['iteration_goal']!r}"
    )


def test_derive_state_iteration_goal_unicode_preserved(isolated_log):
    """Unicode (non-ASCII) goal round-trips byte-for-byte."""
    import sm

    goal = "Goal: 飞翔 → ✓ über"
    _open_iteration_directly(iteration_id="iter-uni", iteration_goal=goal)
    state = sm.derive_state()
    assert state["iteration_goal"] == goal, (
        f"unicode iteration_goal must round-trip exactly; got "
        f"{state['iteration_goal']!r}"
    )


def test_derive_state_iteration_goal_multiline_preserved(isolated_log):
    """Multiline goal (with embedded \\n) round-trips."""
    import sm

    goal = "Line one.\nLine two.\nLine three."
    _open_iteration_directly(iteration_id="iter-multi", iteration_goal=goal)
    state = sm.derive_state()
    assert state["iteration_goal"] == goal


def test_derive_state_iteration_goal_long_string_preserved(isolated_log):
    """A long (~10KB) goal round-trips. No truncation."""
    import sm

    goal = "X" * 10_000
    _open_iteration_directly(iteration_id="iter-long", iteration_goal=goal)
    state = sm.derive_state()
    assert state["iteration_goal"] == goal
    assert len(state["iteration_goal"]) == 10_000


def test_derive_state_iteration_goal_pure_replay(isolated_log):
    """derive_state remains a pure read — two consecutive calls return
    equal iteration_goal values."""
    import sm

    _open_iteration_directly(iteration_id="iter-1",
                             iteration_goal="Stable goal.")
    s1 = sm.derive_state()
    s2 = sm.derive_state()
    assert s1["iteration_goal"] == s2["iteration_goal"] == "Stable goal."


def test_derive_state_iteration_goal_mutation_independence(isolated_log):
    """Mutating a returned state dict's iteration_goal does not affect a
    later derive_state() call."""
    import sm

    _open_iteration_directly(iteration_id="iter-1",
                             iteration_goal="Original.")
    s1 = sm.derive_state()
    s1["iteration_goal"] = "MUTATED"
    s2 = sm.derive_state()
    assert s2["iteration_goal"] == "Original.", (
        f"mutating a returned state must not leak into the next "
        f"derive_state() call; got {s2['iteration_goal']!r}"
    )


# ===========================================================================
# Category B — single-pass ingest (10 tests)
# ===========================================================================


def test_ingest_calls_read_entries_exactly_once(isolated_log, tmp_path,
                                                monkeypatch):
    """Retro item 7 core pin: a successful ingest() invokes
    ``sm.read_entries`` EXACTLY ONCE.

    Pre-Story-11: ingest() calls read_entries twice — once via
    ``derive_state()`` (line 925) and again in the dup-id loop
    (line 1105). After Story 11, those two walks consolidate into one.
    """
    counter = _make_read_counter(monkeypatch)
    import sm

    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"))
    sm.ingest(p)

    assert counter["calls"] == 1, (
        f"Story 11 retro item 7: ingest() must walk the log exactly ONCE "
        f"per call (currently walks twice — derive_state walk + dup-id "
        f"loop walk). Got {counter['calls']} call(s) to read_entries."
    )


def test_ingest_calls_read_entries_once_with_prior_closed_iteration(
    isolated_log, tmp_path, monkeypatch,
):
    """Even when there is a prior (closed) iteration in the log — which
    is the exact scenario where the dup-id check has to scan something —
    ingest still walks the log only once."""
    # Seed: open + close a prior iteration, then start counting.
    _open_iteration_directly(iteration_id="iter-prior",
                             iteration_goal="Prior goal.")
    _append_close(iteration_id="iter-prior")

    counter = _make_read_counter(monkeypatch)
    import sm

    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-new"))
    sm.ingest(p)

    assert counter["calls"] == 1, (
        f"ingest() must walk the log exactly ONCE even when prior "
        f"iterations exist in the log; got {counter['calls']}"
    )


def test_ingest_calls_read_entries_once_on_dup_id_failure(
    isolated_log, tmp_path, monkeypatch,
):
    """Even on the failure path (duplicate-id detected), ingest still
    only walks the log once. Both checks share a single walk."""
    # Seed: open + close iter-1, so iter-1 is a closed prior iteration.
    _open_iteration_directly(iteration_id="iter-1",
                             iteration_goal="Original.")
    _append_close(iteration_id="iter-1")

    counter = _make_read_counter(monkeypatch)
    import sm

    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"))
    with pytest.raises(sm.IngestDuplicateError):
        sm.ingest(p)

    assert counter["calls"] == 1, (
        f"ingest() must walk the log exactly ONCE even on the dup-id "
        f"failure path (single walk feeds both single-active and dup-id "
        f"checks); got {counter['calls']}"
    )


def test_ingest_calls_read_entries_once_on_single_active_failure(
    isolated_log, tmp_path, monkeypatch,
):
    """Failure-path single-walk pin: when single-active fires (an
    iteration is already open), ingest still walks the log only once."""
    _open_iteration_directly(iteration_id="iter-open",
                             iteration_goal="Active.")

    counter = _make_read_counter(monkeypatch)
    import sm

    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-new"))
    with pytest.raises(sm.IngestActiveError):
        sm.ingest(p)

    assert counter["calls"] == 1, (
        f"ingest() must walk the log exactly ONCE on the single-active "
        f"failure path; got {counter['calls']}"
    )


def test_ingest_calls_read_entries_zero_times_on_validation_failure(
    isolated_log, tmp_path, monkeypatch,
):
    """When the handoff itself is malformed (shape failure), ingest
    raises BEFORE any log read — read_entries is invoked zero times.

    This pins that the consolidation didn't accidentally move the log
    read earlier in the validation cascade (shape validation must still
    short-circuit before any log scan).
    """
    counter = _make_read_counter(monkeypatch)
    import sm

    # Missing required field 'iteration_id' -> shape failure.
    bad_handoff = {"requirements": [_canonical_requirement("req-1")]}
    p = _write_handoff(tmp_path, bad_handoff, name="bad.json")
    with pytest.raises(sm.IngestShapeError):
        sm.ingest(p)

    assert counter["calls"] == 0, (
        f"ingest() must not walk the log when handoff shape validation "
        f"fails first; got {counter['calls']}"
    )


def test_ingest_still_detects_duplicate_iteration_ids(isolated_log, tmp_path):
    """Behavioral regression: dup-id detection still fires after the
    refactor. The two-walk consolidation must preserve the check."""
    import sm

    # First ingest succeeds.
    p1 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-dup"),
                        name="h1.json")
    sm.ingest(p1)

    # Close the iteration so single-active doesn't preempt.
    _append_close(iteration_id="iter-dup")

    # Re-ingest with the same id -> dup-id failure.
    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-dup"),
                        name="h2.json")
    with pytest.raises(sm.IngestDuplicateError):
        sm.ingest(p2)


def test_ingest_still_detects_single_active_violation(isolated_log, tmp_path):
    """Behavioral regression: single-active still fires after refactor."""
    import sm

    p1 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-A"),
                        name="h1.json")
    sm.ingest(p1)

    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-B"),
                        name="h2.json")
    with pytest.raises(sm.IngestActiveError):
        sm.ingest(p2)


def test_ingest_still_returns_appended_entry_unchanged(isolated_log, tmp_path):
    """Behavioral regression: the returned entry dict shape is unchanged.
    Story 11 must not alter ingest's public output."""
    import sm

    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-1",
                                          iteration_goal="G"))
    result = sm.ingest(p)
    assert isinstance(result, dict)
    assert result["type"] == "iteration_open"
    assert result["iteration_id"] == "iter-1"
    assert result["iteration_goal"] == "G"
    # canonical auto-stamped fields
    assert "id" in result
    assert "timestamp" in result


def test_ingest_failure_invariant_log_unchanged_on_dup_id(
    isolated_log, tmp_path,
):
    """Behavioral regression: on dup-id failure, log.jsonl is
    byte-for-byte unchanged. The refactor must preserve the
    failure-write invariant."""
    import sm

    # Seed: open + close iter-1.
    p1 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    _append_close(iteration_id="iter-1")

    log_bytes_before = isolated_log.read_bytes()

    p2 = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-1"),
                        name="h2.json")
    with pytest.raises(sm.IngestDuplicateError):
        sm.ingest(p2)

    log_bytes_after = isolated_log.read_bytes()
    assert log_bytes_after == log_bytes_before, (
        "log.jsonl must be byte-for-byte unchanged on dup-id failure "
        "even after the single-pass refactor"
    )


def test_ingest_source_no_longer_contains_two_separate_read_entries_calls(
    isolated_log,
):
    """Source-level pin (defensive): the ``ingest`` function source
    contains at most ONE call to ``read_entries(``.

    Pre-Story-11 the function has two call sites. Post-Story-11 it has
    at most one (possibly zero, if the Coder extracts a helper). This
    pin is a defense-in-depth check against accidental re-introduction
    of the two-walk pattern; the canonical functional pin is the
    monkeypatched read-counter test above.
    """
    src = _ingest_source()
    # Count occurrences of `read_entries(` (with open paren — exclude
    # bare-name references in comments / docstrings).
    pattern = re.compile(r"\bread_entries\s*\(")
    hits = pattern.findall(src)
    assert len(hits) <= 1, (
        f"ingest() function source contains {len(hits)} call(s) to "
        f"read_entries(); Story 11 must consolidate the two-walk pattern "
        f"to at most one call site in ingest's body. Source:\n{src}"
    )


# ===========================================================================
# Category C — close_iteration reads from state, not log (10 tests)
# ===========================================================================


def test_close_iteration_source_has_no_read_entries_call():
    """Story 11 core pin (retro item 10): the ``close_iteration``
    function source contains ZERO calls to ``read_entries()``.

    Pre-Story-11: close_iteration calls read_entries (line 2313) to
    recover the iteration_goal and the latest sprint_cut's
    ``in_sprint_story_ids``. Both pieces must come from derive_state
    after Story 11. iteration_goal is the explicit retro item 10 fix.

    NOTE: this pin uses the regex ``\\bread_entries\\s*\\(`` — calls only,
    not bare-name mentions in comments. The expected post-fix count
    is 0.
    """
    src = _close_iteration_source()
    pattern = re.compile(r"\bread_entries\s*\(")
    hits = pattern.findall(src)
    assert len(hits) == 0, (
        f"Story 11 retro item 10: close_iteration must NOT call "
        f"read_entries() — the iteration_goal (and any other previously-"
        f"scanned fields) must come from the derived state object. "
        f"Got {len(hits)} call(s) to read_entries() in "
        f"close_iteration's source. Source:\n{src}"
    )


def test_close_iteration_does_not_invoke_read_entries_at_runtime(
    isolated_log, tmp_path, monkeypatch,
):
    """Runtime pin: a successful close_iteration() does NOT increment the
    read_entries counter via close_iteration's own body.

    Setup: open + decompose + cut + accept all + close. We snapshot the
    counter immediately BEFORE the close_iteration call and again after,
    so that read_entries calls made by setup helpers don't pollute the
    count. The close_iteration call MAY trigger read_entries indirectly
    via derive_state() (one call). After Story 11 the only legitimate
    read_entries call from inside close_iteration is the single one
    inside derive_state. The pre-fix code does TWO walks (one in
    derive_state, one for the iteration_goal lookup). So:

        - Pre-fix expected delta: 2 (derive_state + goal-scan loop)
        - Post-fix expected delta: 1 (derive_state only)

    We pin <= 1 to allow Coder flexibility (could be 0 if Coder cached
    state, but at minimum the derive_state call survives).
    """
    import sm

    # Set up a closeable iteration via the canonical public surface.
    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-close",
                                          iteration_goal="Close goal."),
                       name="open.json")
    sm.ingest(p)
    # Seed a minimal story_backlog directly so we don't depend on
    # agent spawn.
    story_id = "story-1"
    backlog_entry = sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": story_id,
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<test-stub>",
        "role_spec_hash": "<test-stub>",
    })
    sm._append_entry(backlog_entry)
    sm.sprint_cut(1)
    sm.record_review(story_id, True, "ok")
    sm.transition_story(story_id, "in_progress")
    sm.transition_story(story_id, "in_review")
    sm.transition_story(story_id, "accepted")

    # NOW start counting.
    counter = _make_read_counter(monkeypatch)
    sm.close_iteration()

    assert counter["calls"] <= 1, (
        f"close_iteration must invoke read_entries AT MOST once (via the "
        f"derive_state call); the iteration_goal walk must be gone. "
        f"Got {counter['calls']} calls."
    )


def test_close_iteration_handoff_carries_iteration_goal_from_state(
    isolated_log, tmp_path,
):
    """Behavioral regression: the close handoff JSON file's
    iteration_goal still matches the iteration_open entry's goal.
    Reading from state must yield the same value as the old log-scan."""
    import sm

    goal = "Ship Iter 1 alpha."
    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-close",
                                          iteration_goal=goal),
                       name="open.json")
    sm.ingest(p)
    backlog_entry = sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<test-stub>",
        "role_spec_hash": "<test-stub>",
    })
    sm._append_entry(backlog_entry)
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")
    sm.close_iteration()

    handoff_path = isolated_log.parent / "close_handoff_iter-close.json"
    assert handoff_path.exists()
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert handoff["iteration_goal"] == goal


def test_close_iteration_handoff_iteration_goal_empty_string(
    isolated_log, tmp_path,
):
    """Edge case: empty-string goal still round-trips to the handoff."""
    import sm

    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-empty",
                                          iteration_goal=""),
                       name="open.json")
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")
    sm.close_iteration()

    handoff = json.loads(
        (isolated_log.parent / "close_handoff_iter-empty.json")
        .read_text(encoding="utf-8")
    )
    assert handoff["iteration_goal"] == ""


def test_close_iteration_handoff_iteration_goal_unicode(
    isolated_log, tmp_path,
):
    """Edge case: unicode goal round-trips byte-for-byte through close."""
    import sm

    goal = "Plan: 飞翔 → ✓"
    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-uni",
                                          iteration_goal=goal),
                       name="open.json")
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")
    sm.close_iteration()

    handoff = json.loads(
        (isolated_log.parent / "close_handoff_iter-uni.json")
        .read_text(encoding="utf-8")
    )
    assert handoff["iteration_goal"] == goal


def test_close_iteration_handoff_iteration_goal_multiline(
    isolated_log, tmp_path,
):
    """Edge case: multiline (embedded \\n) goal round-trips."""
    import sm

    goal = "Line one.\nLine two."
    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-ml",
                                          iteration_goal=goal),
                       name="open.json")
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")
    sm.close_iteration()

    handoff = json.loads(
        (isolated_log.parent / "close_handoff_iter-ml.json")
        .read_text(encoding="utf-8")
    )
    assert handoff["iteration_goal"] == goal


def test_close_iteration_still_appends_iteration_close_entry(
    isolated_log, tmp_path,
):
    """Behavioral regression: close_iteration still appends EXACTLY ONE
    iteration_close log entry."""
    import sm

    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-1"))
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")

    entries_before = list(sm.read_entries())
    sm.close_iteration()
    entries_after = list(sm.read_entries())

    assert len(entries_after) == len(entries_before) + 1
    appended = entries_after[-1]
    assert appended["type"] == "iteration_close"
    assert appended["iteration_id"] == "iter-1"


def test_close_iteration_still_writes_handoff_sidecar_file(
    isolated_log, tmp_path,
):
    """Behavioral regression: handoff sidecar JSON file is still
    written at LOG_PATH.parent / close_handoff_<id>.json."""
    import sm

    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-side"))
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")
    sm.close_iteration()

    expected = isolated_log.parent / "close_handoff_iter-side.json"
    assert expected.exists()


def test_close_iteration_still_returns_iteration_close_entry(
    isolated_log, tmp_path,
):
    """Behavioral regression: return value of close_iteration is still
    the appended iteration_close entry dict (shape preserved)."""
    import sm

    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-1"))
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")

    result = sm.close_iteration()

    assert isinstance(result, dict)
    assert result["type"] == "iteration_close"
    assert result["iteration_id"] == "iter-1"
    assert "id" in result
    assert "timestamp" in result
    assert "handoff_file_path" in result
    assert "per_requirement_status" in result
    assert "closed_by" in result
    assert "accepted_count" in result


def test_close_iteration_validation_cascade_preserved(
    isolated_log, tmp_path,
):
    """Behavioral regression: the four-step validation cascade still
    fires. Specifically, calling close_iteration with no active
    iteration still raises IterationCloseError. (Pins that the refactor
    didn't accidentally drop the active_iteration None check now that
    it relies on the state object.)"""
    import sm

    with pytest.raises(sm.IterationCloseError):
        sm.close_iteration()


# ===========================================================================
# Category D — regression smoke (8 tests)
# ===========================================================================


def test_full_lifecycle_open_through_close_still_works(isolated_log, tmp_path):
    """End-to-end smoke: ingest -> story_backlog -> sprint_cut ->
    accept -> close_iteration still produces a valid close handoff."""
    import sm

    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-full",
                                          iteration_goal="Full smoke goal."))
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")
    result = sm.close_iteration()

    assert result["type"] == "iteration_close"
    # Post-close state is empty.
    state = sm.derive_state()
    assert state["active_iteration"] is None
    assert state["iteration_goal"] is None


def test_two_sequential_iterations_iteration_goal_switches(
    isolated_log, tmp_path,
):
    """Sequence two iterations through full close. The derived
    iteration_goal switches across the boundary correctly."""
    import sm

    # Iteration A.
    pa = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-A",
                                           iteration_goal="A goal."),
                        name="A.json")
    sm.ingest(pa)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-A1",
            "sequence": 1,
            "title": "T",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-A1", True, "ok")
    sm.transition_story("story-A1", "in_progress")
    sm.transition_story("story-A1", "in_review")
    sm.transition_story("story-A1", "accepted")
    sm.close_iteration()

    # Iteration B.
    pb = _write_handoff(tmp_path,
                        _canonical_handoff(iteration_id="iter-B",
                                           iteration_goal="B goal."),
                        name="B.json")
    sm.ingest(pb)
    state = sm.derive_state()
    assert state["iteration_goal"] == "B goal.", (
        f"After closing A and opening B, derived iteration_goal must be "
        f"B's goal; got {state['iteration_goal']!r}"
    )


def test_force_close_still_produces_handoff_with_iteration_goal(
    isolated_log, tmp_path,
):
    """force_close path still produces a handoff carrying the correct
    iteration_goal (force_close delegates to close_iteration)."""
    import sm

    goal = "Force-close goal."
    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-force",
                                          iteration_goal=goal),
                       name="open.json")
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.force_close("operator chose to abandon")

    handoff = json.loads(
        (isolated_log.parent / "close_handoff_iter-force.json")
        .read_text(encoding="utf-8")
    )
    assert handoff["iteration_goal"] == goal


def test_aggregate_requirements_still_works_against_enriched_state(
    isolated_log, tmp_path,
):
    """aggregate_requirements still produces correct output against the
    (now-enriched) state dict. The extra iteration_goal key must not
    break aggregation."""
    import sm

    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-agg",
                                          iteration_goal="Agg goal."))
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")

    state = sm.derive_state()
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted"}


def test_status_command_still_works_with_enriched_state(isolated_log, tmp_path):
    """status() (which reads derive_state) still works against the
    enriched state dict — the new iteration_goal field must not break
    the status output path."""
    import sm

    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-status",
                                          iteration_goal="Status goal."))
    sm.ingest(p)
    out = sm.status()
    # status returns a string; we don't pin contents here (other tests
    # do), only that it doesn't crash on the enriched state.
    assert isinstance(out, str)
    assert len(out) > 0


def test_derive_state_shape_keys_superset_of_original_five(isolated_log):
    """The original five keys (active_iteration, story_backlog, sprint_cut,
    story_states, close_status) are all still present. Story 11 must
    add the iteration_goal key without removing any existing key."""
    import sm

    state = sm.derive_state()
    required_original = {
        "active_iteration",
        "story_backlog",
        "sprint_cut",
        "story_states",
        "close_status",
    }
    assert required_original.issubset(set(state.keys())), (
        f"Story 11 must not remove any of the original derive_state keys. "
        f"Missing: {required_original - set(state.keys())!r}"
    )
    # And the new key is present too.
    assert "iteration_goal" in state


def test_close_iteration_entry_still_has_handoff_file_path(
    isolated_log, tmp_path,
):
    """Behavioral regression: the iteration_close log entry still
    carries the absolute handoff_file_path. The path string content
    must still be the canonical close_handoff_<id>.json under
    LOG_PATH.parent."""
    import sm

    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-path"))
    sm.ingest(p)
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": "story-1",
            "sequence": 1,
            "title": "T1",
            "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>",
        "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)
    sm.record_review("story-1", True, "ok")
    sm.transition_story("story-1", "in_progress")
    sm.transition_story("story-1", "in_review")
    sm.transition_story("story-1", "accepted")
    entry = sm.close_iteration()

    expected = (isolated_log.parent / "close_handoff_iter-path.json").resolve()
    assert pathlib.Path(entry["handoff_file_path"]) == expected


def test_iteration_goal_state_value_matches_log_entry_byte_for_byte(
    isolated_log, tmp_path,
):
    """Cross-pin: the iteration_goal carried on derive_state is
    byte-for-byte equal to the iteration_open entry's iteration_goal
    in the raw log (not a re-encoded copy)."""
    import sm

    goal = "Specific: line1\nline2 — über"
    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-bb",
                                          iteration_goal=goal))
    sm.ingest(p)

    # Read raw log entry.
    raw = isolated_log.read_text(encoding="utf-8").splitlines()[0]
    entry = json.loads(raw)
    assert entry["type"] == "iteration_open"
    assert entry["iteration_goal"] == goal

    state = sm.derive_state()
    assert state["iteration_goal"] == entry["iteration_goal"]
