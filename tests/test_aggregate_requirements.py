"""Story 17 — pin the contract of `sm.aggregate_requirements`.

Story 17 (Sprint 2, size M) adds the per-requirement aggregation rule
that maps story outcomes back to requirement outcomes. It introduces a
new public pure function and a typed error class:

    aggregate_requirements(state: dict) -> dict[str, str]
    class AggregateError(ValueError)

What this file pins:

  - Function signature and shape:
      `aggregate_requirements(state)` — PUBLIC, callable, in `sm.__all__`,
      importable as `from sm import aggregate_requirements`. Takes one
      positional argument: a state dict (the shape produced by
      `derive_state()`). Returns a dict mapping requirement_id (str) to
      status (one of `"accepted"`, `"rejected"`, `"partial"`).

  - Pure function:
      * Never calls `_append_entry`.
      * Never calls `read_entries`.
      * Works on the state dict argument ONLY — no log I/O whatsoever.
      * Two calls produce equal results on the same input.
      * Mutating the returned dict does not affect a subsequent call.

  - Rule — `accepted`:
      A requirement is `accepted` only if every story that carries that
      requirement_id in its `requirement_ids` is in lifecycle state
      `accepted`.

  - Rule — `rejected`:
      A requirement is `rejected` if any story rolling up to it is in
      `rejected` OR `force_closed` (force-closed-as-rejected — the spec's
      "force-closed-as-rejected" phrase). The rejected rule short-circuits
      the accepted/partial rules.

  - Rule — `partial`:
      A requirement is `partial` if its stories are mixed across states
      WITHOUT triggering the rejected rule — i.e. some accepted + some
      still in flight (planned / in_progress / in_review), or all in
      flight. Pre-close this is the common case.

  - Multi-requirement stories:
      A story whose `requirement_ids` carries multiple ids counts toward
      every one of those requirements simultaneously. One accepted story
      rolling up to two requirements contributes to both being `accepted`
      (if it is the only story for each).

  - Orphan requirements — typed error:
      Requirements in `state["active_iteration"]["requirements"]` that no
      story rolls up to → raise `AggregateError` naming the orphan(s).
      Story 10 validation should prevent this on the live path; the error
      exists for defense in depth.

  - No active iteration:
      `state["active_iteration"] is None` → raise `AggregateError` with
      a message mentioning "no active iteration" (operator can't aggregate
      against nothing).

  - Typed error class:
      `sm.AggregateError` exists, subclasses `ValueError`, is in
      `sm.__all__`. Subclassing ValueError keeps existing `except
      ValueError` callers compatible.

Tests must FAIL on first run — `aggregate_requirements` and
`AggregateError` do not exist yet. The Coder downstream implements them
to satisfy these tests.

The function is pure logic: no LOG_PATH, no subprocess, no CLI. Every
test builds an in-memory state dict and calls the function directly.
"""

from __future__ import annotations

import inspect
import pathlib
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Helpers — build in-memory state dicts shaped like derive_state's output.
# ---------------------------------------------------------------------------


def _make_state(
    iteration_id: str = "iter-1",
    requirement_ids: list = None,
    stories: list = None,
    story_states: dict = None,
    sprint_cut_position: int = None,
    close_status: dict = None,
    active: bool = True,
) -> dict:
    """Build a state dict matching `derive_state()`'s output shape.

    Args:
      iteration_id: id for active iteration (ignored when active=False).
      requirement_ids: list of strings; ground-truth requirements for the
        iteration. Auto-defaults to ["req-1"] when not provided.
      stories: list of (story_id, requirement_ids) tuples OR list of full
        story dicts. Tuples are expanded to dicts with sequence assigned
        in order. Defaults to one story rolling up to every requirement.
      story_states: dict mapping story_id → lifecycle state. Defaults to
        every story in "planned".
      sprint_cut_position: optional int. Defaults to None.
      close_status: optional close status dict. Defaults to None.
      active: if False, active_iteration is None and stories/states stay
        provided as written (caller can simulate post-close inputs).

    Returns:
      A state dict.
    """
    if requirement_ids is None:
        requirement_ids = ["req-1"]

    # Build the requirements list as the ingest contract emits them —
    # each entry a dict with at least "requirement_id".
    requirements = [{"requirement_id": rid} for rid in requirement_ids]

    if stories is None:
        # Default: one story carrying every requirement.
        stories = [("story-default", list(requirement_ids))]

    # Expand tuple shorthand into full story dicts.
    expanded_stories = []
    for idx, s in enumerate(stories, start=1):
        if isinstance(s, tuple):
            sid, rids = s
            expanded_stories.append({
                "story_id": sid,
                "sequence": idx,
                "title": f"Story {idx}",
                "size": "M",
                "requirement_ids": list(rids),
                "acceptance_criteria": "ok",
            })
        else:
            # Caller supplied a full dict — use as-is, but make a copy.
            expanded_stories.append(dict(s))

    if story_states is None:
        story_states = {
            s["story_id"]: "planned" for s in expanded_stories
        }

    state: dict = {
        "active_iteration": (
            {
                "iteration_id": iteration_id,
                "requirements": requirements,
            }
            if active else None
        ),
        "story_backlog": expanded_stories,
        "sprint_cut": sprint_cut_position,
        "story_states": dict(story_states),
        "close_status": close_status,
    }
    return state


# ===========================================================================
# Smoke (6) — function exists, callable, public, in __all__, signature
# ===========================================================================


def test_aggregate_function_exists():
    """sm.aggregate_requirements must exist on the module."""
    import sm
    assert hasattr(sm, "aggregate_requirements"), (
        "sm.aggregate_requirements must exist"
    )


def test_aggregate_function_is_callable():
    """sm.aggregate_requirements must be callable."""
    import sm
    assert callable(sm.aggregate_requirements), (
        "sm.aggregate_requirements must be callable"
    )


def test_aggregate_function_is_public():
    """No leading underscore — public API."""
    import sm
    name = sm.aggregate_requirements.__name__
    assert not name.startswith("_"), (
        f"aggregate_requirements must be public; got name {name!r}"
    )
    assert name == "aggregate_requirements"


def test_aggregate_function_importable_directly():
    """`from sm import aggregate_requirements` succeeds."""
    from sm import aggregate_requirements  # noqa: F401
    assert callable(aggregate_requirements)


def test_aggregate_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__")
    assert "aggregate_requirements" in sm.__all__, (
        f"aggregate_requirements must be in __all__; got {sm.__all__!r}"
    )


def test_aggregate_signature_accepts_state_dict():
    """aggregate_requirements takes exactly one required positional arg
    (the state dict)."""
    import sm
    sig = inspect.signature(sm.aggregate_requirements)
    required = [
        p for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    assert len(required) == 1, (
        f"aggregate_requirements must take exactly one required arg; "
        f"got {required!r}"
    )


# ===========================================================================
# AggregateError typed (4) — exists, in __all__, ValueError subclass
# ===========================================================================


def test_aggregate_error_exists():
    """sm.AggregateError must exist."""
    import sm
    assert hasattr(sm, "AggregateError"), (
        "sm.AggregateError must exist"
    )


def test_aggregate_error_is_value_error_subclass():
    """AggregateError narrows ValueError so existing `except ValueError`
    callers keep working."""
    import sm
    assert issubclass(sm.AggregateError, ValueError), (
        f"AggregateError must subclass ValueError; got bases "
        f"{sm.AggregateError.__mro__!r}"
    )


def test_aggregate_error_in_dunder_all():
    """AggregateError is exported via __all__."""
    import sm
    assert "AggregateError" in sm.__all__, (
        f"AggregateError must be in __all__; got {sm.__all__!r}"
    )


def test_aggregate_error_is_a_class():
    """AggregateError is an exception class (not a function / instance)."""
    import sm
    assert isinstance(sm.AggregateError, type), (
        f"AggregateError must be a class; got {sm.AggregateError!r}"
    )
    assert issubclass(sm.AggregateError, Exception)


# ===========================================================================
# All accepted (6) — every story accepted -> requirement accepted
# ===========================================================================


def test_aggregate_single_req_single_accepted_story():
    """One requirement with one accepted story -> 'accepted'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted"}, (
        f"single accepted story must yield 'accepted'; got {result!r}"
    )


def test_aggregate_single_req_two_accepted_stories():
    """One requirement with two accepted stories -> 'accepted'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[("s1", ["req-1"]), ("s2", ["req-1"])],
        story_states={"s1": "accepted", "s2": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted"}


def test_aggregate_single_req_three_accepted_stories():
    """One requirement with three accepted stories -> 'accepted'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[("s1", ["req-1"]), ("s2", ["req-1"]),
                 ("s3", ["req-1"])],
        story_states={"s1": "accepted", "s2": "accepted",
                      "s3": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted"}


def test_aggregate_multiple_reqs_all_accepted():
    """Multiple requirements, each with all accepted stories -> all
    'accepted'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2", "req-3"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-2"]),
            ("s3", ["req-3"]),
        ],
        story_states={"s1": "accepted", "s2": "accepted",
                      "s3": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {
        "req-1": "accepted",
        "req-2": "accepted",
        "req-3": "accepted",
    }


def test_aggregate_returns_dict_with_str_keys_and_values():
    """Return value is dict with str keys and str values."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert isinstance(result, dict)
    for k, v in result.items():
        assert isinstance(k, str), (
            f"requirement_id key must be str; got {type(k).__name__}"
        )
        assert isinstance(v, str), (
            f"status value must be str; got {type(v).__name__}"
        )


def test_aggregate_status_only_returns_canonical_values():
    """Every value in the result is one of {'accepted','rejected','partial'}."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-2"]),
        ],
        story_states={"s1": "accepted", "s2": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    valid_statuses = {"accepted", "rejected", "partial"}
    for k, v in result.items():
        assert v in valid_statuses, (
            f"status {v!r} for {k!r} is not in {valid_statuses!r}"
        )


# ===========================================================================
# Any rejected (6) — one rejected story -> requirement rejected
# ===========================================================================


def test_aggregate_single_rejected_story():
    """One requirement with one rejected story -> 'rejected'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "rejected"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected"}


def test_aggregate_rejected_among_accepteds():
    """One rejected mixed in with accepteds -> 'rejected' for that req."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
            ("s3", ["req-1"]),
        ],
        story_states={"s1": "accepted", "s2": "rejected",
                      "s3": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected"}, (
        f"any rejected story must trigger 'rejected'; got {result!r}"
    )


def test_aggregate_multiple_rejecteds():
    """Multiple rejecteds in a req -> still 'rejected' (idempotent)."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
            ("s3", ["req-1"]),
        ],
        story_states={"s1": "rejected", "s2": "rejected",
                      "s3": "rejected"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected"}


def test_aggregate_rejected_isolated_to_its_req():
    """A rejected story in req-1 must not contaminate req-2's status."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-2"]),
        ],
        story_states={"s1": "rejected", "s2": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected", "req-2": "accepted"}, (
        f"rejection must not leak across requirements; got {result!r}"
    )


def test_aggregate_rejected_with_in_flight_other_stories():
    """rejected + in_progress in the same req -> still 'rejected'
    (rejected rule wins over partial)."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
        ],
        story_states={"s1": "rejected", "s2": "in_progress"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected"}


def test_aggregate_rejected_takes_precedence_over_accepted():
    """Spec: rejected if ANY story is rejected, regardless of other states.
    'accepted' rule requires EVERY story accepted, which fails when one is
    rejected — so the rule is unambiguous: rejected wins."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
            ("s3", ["req-1"]),
            ("s4", ["req-1"]),
        ],
        story_states={
            "s1": "accepted",
            "s2": "accepted",
            "s3": "accepted",
            "s4": "rejected",
        },
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected"}


# ===========================================================================
# Any force_closed (5) — force_closed treated as rejected for aggregation
# ===========================================================================


def test_aggregate_single_force_closed_story():
    """One requirement with one force_closed story -> 'rejected'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "force_closed"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected"}, (
        f"force_closed must roll up as 'rejected'; got {result!r}"
    )


def test_aggregate_force_closed_with_accepteds():
    """force_closed mixed with accepteds -> 'rejected'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
            ("s3", ["req-1"]),
        ],
        story_states={
            "s1": "accepted",
            "s2": "force_closed",
            "s3": "accepted",
        },
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected"}


def test_aggregate_force_closed_with_rejected():
    """force_closed + rejected in same req -> 'rejected'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
        ],
        story_states={"s1": "force_closed", "s2": "rejected"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected"}


def test_aggregate_force_closed_with_in_flight():
    """force_closed + in_progress -> 'rejected' (force_closed wins over
    partial, just like rejected does)."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
        ],
        story_states={"s1": "force_closed", "s2": "in_progress"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected"}


def test_aggregate_force_closed_isolated_to_its_req():
    """force_closed in req-1 must not contaminate req-2."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-2"]),
        ],
        story_states={"s1": "force_closed", "s2": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected", "req-2": "accepted"}


# ===========================================================================
# Partial (6) — mixed without triggering rejected -> 'partial'
# ===========================================================================


def test_aggregate_partial_accepted_plus_in_progress():
    """One accepted + one in_progress -> 'partial'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
        ],
        story_states={"s1": "accepted", "s2": "in_progress"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "partial"}, (
        f"accepted + in_progress without rejected must be 'partial'; "
        f"got {result!r}"
    )


def test_aggregate_partial_accepted_plus_in_review():
    """One accepted + one in_review -> 'partial'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
        ],
        story_states={"s1": "accepted", "s2": "in_review"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "partial"}


def test_aggregate_partial_accepted_plus_planned():
    """One accepted + one planned -> 'partial' (planned is in-flight)."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
        ],
        story_states={"s1": "accepted", "s2": "planned"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "partial"}


def test_aggregate_partial_all_in_progress():
    """All stories in_progress (none accepted yet) -> 'partial'.
    (Not 'accepted' because no story is accepted; not 'rejected' because
    no story is rejected or force_closed.)"""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
        ],
        story_states={"s1": "in_progress", "s2": "in_progress"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "partial"}


def test_aggregate_partial_mixed_non_terminal():
    """Mix of planned + in_progress + in_review -> 'partial'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
            ("s3", ["req-1"]),
        ],
        story_states={
            "s1": "planned",
            "s2": "in_progress",
            "s3": "in_review",
        },
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "partial"}


def test_aggregate_partial_some_accepted_some_in_flight_across_reqs():
    """req-1 is fully accepted; req-2 is mixed -> {'accepted','partial'}."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-2"]),
            ("s3", ["req-2"]),
        ],
        story_states={
            "s1": "accepted",
            "s2": "accepted",
            "s3": "in_progress",
        },
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted", "req-2": "partial"}


# ===========================================================================
# Zero-story requirements (4) — orphan -> AggregateError
# ===========================================================================


def test_aggregate_orphan_requirement_raises():
    """A requirement_id in the iteration with no story rolling up to it
    raises AggregateError."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-orphan"],
        stories=[("s1", ["req-1"])],   # nothing rolls up to req-orphan
        story_states={"s1": "accepted"},
    )
    with pytest.raises(sm.AggregateError):
        sm.aggregate_requirements(state)


def test_aggregate_orphan_error_names_orphan_id():
    """The AggregateError message names the orphan requirement_id so the
    operator can find it without grepping the log."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-orphan-xyz"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "accepted"},
    )
    with pytest.raises(sm.AggregateError) as excinfo:
        sm.aggregate_requirements(state)
    assert "req-orphan-xyz" in str(excinfo.value), (
        f"AggregateError must name orphan id 'req-orphan-xyz'; "
        f"got: {excinfo.value!s}"
    )


def test_aggregate_multiple_orphans_all_named():
    """Multiple orphans -> error message names every orphan id."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-orphan-a", "req-orphan-b"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "accepted"},
    )
    with pytest.raises(sm.AggregateError) as excinfo:
        sm.aggregate_requirements(state)
    msg = str(excinfo.value)
    assert "req-orphan-a" in msg, (
        f"AggregateError must name 'req-orphan-a'; got: {msg!r}"
    )
    assert "req-orphan-b" in msg, (
        f"AggregateError must name 'req-orphan-b'; got: {msg!r}"
    )


def test_aggregate_orphan_error_caught_as_value_error():
    """AggregateError subclasses ValueError — `except ValueError` catches
    the orphan failure."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-orphan"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "accepted"},
    )
    with pytest.raises(ValueError):
        sm.aggregate_requirements(state)


# ===========================================================================
# No active iteration (3) — empty / post-close -> AggregateError
# ===========================================================================


def test_aggregate_no_active_iteration_raises():
    """state['active_iteration'] is None -> AggregateError."""
    import sm
    state = {
        "active_iteration": None,
        "story_backlog": [],
        "sprint_cut": None,
        "story_states": {},
        "close_status": None,
    }
    with pytest.raises(sm.AggregateError):
        sm.aggregate_requirements(state)


def test_aggregate_no_active_iteration_message_mentions_it():
    """AggregateError on no-active-iteration mentions the condition so the
    operator knows why the call failed."""
    import sm
    state = {
        "active_iteration": None,
        "story_backlog": [],
        "sprint_cut": None,
        "story_states": {},
        "close_status": None,
    }
    with pytest.raises(sm.AggregateError) as excinfo:
        sm.aggregate_requirements(state)
    assert "no active iteration" in str(excinfo.value).lower(), (
        f"error message must mention 'no active iteration'; "
        f"got: {excinfo.value!s}"
    )


def test_aggregate_post_close_state_raises():
    """A state representing a closed iteration (active_iteration None +
    close_status populated) -> AggregateError."""
    import sm
    state = {
        "active_iteration": None,
        "story_backlog": [],
        "sprint_cut": None,
        "story_states": {},
        "close_status": {
            "closed_by": "test",
            "reason": "done",
            "accepted_count": 1,
            "rejected_count": 0,
            "force_closed_count": 0,
        },
    }
    with pytest.raises(sm.AggregateError):
        sm.aggregate_requirements(state)


# ===========================================================================
# Multi-requirement stories (5) — stories carrying multiple requirement_ids
# ===========================================================================


def test_aggregate_one_story_rolls_up_to_two_requirements():
    """One accepted story rolling up to two requirements -> both
    'accepted'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2"],
        stories=[("s1", ["req-1", "req-2"])],
        story_states={"s1": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted", "req-2": "accepted"}


def test_aggregate_one_story_to_many_requirements():
    """One accepted story rolling up to many requirements -> all
    'accepted'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2", "req-3", "req-4"],
        stories=[("s1", ["req-1", "req-2", "req-3", "req-4"])],
        story_states={"s1": "accepted"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {
        "req-1": "accepted",
        "req-2": "accepted",
        "req-3": "accepted",
        "req-4": "accepted",
    }


def test_aggregate_multi_req_story_rejected_contaminates_both():
    """One rejected story rolling up to two requirements -> both
    'rejected'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2"],
        stories=[("s1", ["req-1", "req-2"])],
        story_states={"s1": "rejected"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "rejected", "req-2": "rejected"}


def test_aggregate_many_stories_to_one_requirement():
    """Many stories all rolling up to the same single requirement, all
    accepted -> req 'accepted'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-1"]),
            ("s3", ["req-1"]),
            ("s4", ["req-1"]),
            ("s5", ["req-1"]),
        ],
        story_states={
            "s1": "accepted", "s2": "accepted", "s3": "accepted",
            "s4": "accepted", "s5": "accepted",
        },
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted"}


def test_aggregate_multi_req_story_partial_per_other_stories():
    """A story rolling up to req-1 and req-2 is accepted, but req-2 has
    another story still in_progress -> req-1 'accepted', req-2 'partial'."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2"],
        stories=[
            ("s_both", ["req-1", "req-2"]),
            ("s_extra", ["req-2"]),
        ],
        story_states={"s_both": "accepted", "s_extra": "in_progress"},
    )
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted", "req-2": "partial"}, (
        f"req-1 (only the shared story) must be accepted; req-2 "
        f"(shared + in-flight) must be partial; got {result!r}"
    )


# ===========================================================================
# Pure invariant (6) — no log I/O, idempotent, immutable to mutation
# ===========================================================================


def test_aggregate_does_not_call_append_entry(monkeypatch):
    """aggregate_requirements must not call _append_entry. Monkeypatch the
    writer to fail loudly if it fires."""
    import sm

    def _spy(entry):
        raise AssertionError(
            "aggregate_requirements must not call _append_entry — "
            "it is pure"
        )

    monkeypatch.setattr(sm, "_append_entry", _spy)
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "accepted"},
    )
    # Should NOT raise — function should never call _append_entry.
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted"}


def test_aggregate_does_not_call_read_entries(monkeypatch):
    """aggregate_requirements must not call read_entries — it works on the
    passed-in state dict only."""
    import sm

    def _spy():
        raise AssertionError(
            "aggregate_requirements must not call read_entries — it "
            "operates on the state argument, not the log"
        )

    monkeypatch.setattr(sm, "read_entries", _spy)
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "accepted"},
    )
    # Should NOT raise.
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted"}


def test_aggregate_two_calls_produce_equal_results():
    """Two consecutive calls with the same state produce equal results."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2", "req-3"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-2"]),
            ("s3", ["req-3"]),
        ],
        story_states={
            "s1": "accepted",
            "s2": "rejected",
            "s3": "in_progress",
        },
    )
    out1 = sm.aggregate_requirements(state)
    out2 = sm.aggregate_requirements(state)
    assert out1 == out2, (
        f"two calls must produce equal results;\n"
        f"out1={out1!r}\nout2={out2!r}"
    )


def test_aggregate_mutating_returned_dict_does_not_affect_next_call():
    """Mutating the returned dict must not affect a subsequent call's
    result — function returns fresh data each invocation."""
    import sm
    state = _make_state(
        requirement_ids=["req-1"],
        stories=[("s1", ["req-1"])],
        story_states={"s1": "accepted"},
    )
    out1 = sm.aggregate_requirements(state)
    # Trash the first return value.
    out1["req-1"] = "MUTATED"
    out1["bogus"] = "ignore_me"

    out2 = sm.aggregate_requirements(state)
    assert out2 == {"req-1": "accepted"}, (
        f"second call must return a fresh, correct dict despite mutation "
        f"of the first return value; got {out2!r}"
    )


def test_aggregate_does_not_mutate_input_state():
    """Calling aggregate_requirements must not mutate the input state
    dict's top-level keys or values."""
    import sm
    state = _make_state(
        requirement_ids=["req-1", "req-2"],
        stories=[
            ("s1", ["req-1"]),
            ("s2", ["req-2"]),
        ],
        story_states={"s1": "accepted", "s2": "in_progress"},
    )
    # Snapshot the active_iteration requirements list and story_states
    # before the call.
    req_ids_before = [
        r["requirement_id"]
        for r in state["active_iteration"]["requirements"]
    ]
    states_before = dict(state["story_states"])
    backlog_ids_before = [s["story_id"] for s in state["story_backlog"]]

    sm.aggregate_requirements(state)

    req_ids_after = [
        r["requirement_id"]
        for r in state["active_iteration"]["requirements"]
    ]
    states_after = dict(state["story_states"])
    backlog_ids_after = [s["story_id"] for s in state["story_backlog"]]

    assert req_ids_before == req_ids_after, (
        "aggregate_requirements must not mutate requirements list"
    )
    assert states_before == states_after, (
        "aggregate_requirements must not mutate story_states"
    )
    assert backlog_ids_before == backlog_ids_after, (
        "aggregate_requirements must not mutate story_backlog"
    )


def test_aggregate_pure_under_combined_spy_no_io():
    """Combined pure check — neither _append_entry nor read_entries fires
    on a complex state with all three result categories."""
    import sm

    def _append_spy(entry):
        raise AssertionError("must not write log")

    def _read_spy():
        raise AssertionError("must not read log")

    state = _make_state(
        requirement_ids=["req-acc", "req-rej", "req-par"],
        stories=[
            ("s1", ["req-acc"]),
            ("s2", ["req-rej"]),
            ("s3", ["req-par"]),
            ("s4", ["req-par"]),
        ],
        story_states={
            "s1": "accepted",
            "s2": "rejected",
            "s3": "accepted",
            "s4": "in_progress",
        },
    )

    # Patch via monkeypatch-style direct assignment so we can control both
    # in one test without depending on pytest's monkeypatch fixture.
    orig_append = sm._append_entry
    orig_read = sm.read_entries
    try:
        sm._append_entry = _append_spy
        sm.read_entries = _read_spy
        result = sm.aggregate_requirements(state)
    finally:
        sm._append_entry = orig_append
        sm.read_entries = orig_read

    assert result == {
        "req-acc": "accepted",
        "req-rej": "rejected",
        "req-par": "partial",
    }, (
        f"complex aggregation must work without any log I/O; got {result!r}"
    )
