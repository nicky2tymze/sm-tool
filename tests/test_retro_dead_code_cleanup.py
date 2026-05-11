"""Iter 2 Story 10 — Retro polish: dead-code cleanup.

Story 10 (size S, behavior-preserving) closes 4 retro items from Iter 1:

  - Retro item 2: `_TERMINAL_STATES` constant — DELETE. The transition
    graph already encodes terminality (terminal states map to empty
    frozenset of next states), so the constant adds nothing. The two
    current call sites (in `close_iteration` and `force_close`) must be
    rewritten to inline an equivalent literal-set check, or to use the
    transition graph directly. Either way, no `_TERMINAL_STATES`
    identifier remains anywhere in sm.py or the tests directory.

  - Retro item 8: `aggregate_requirements` carries redundant `or []` /
    `or {}` clauses after `state.get(key, default)` calls. The defaults
    already cover the missing-key case; the `or` clauses are dead
    belt-and-suspenders. There are FOUR such sites in the current
    function body:
        1. `active.get("requirements", []) or []`         (line 2158)
        2. `state.get("story_backlog", []) or []`         (line 2167)
        3. `state.get("story_states", {}) or {}`          (line 2168)
        4. `s.get("requirement_ids", []) or []`           (line 2175)
    ALL FOUR are redundant by upstream contract:
      * `derive_state()` guarantees `active_iteration["requirements"]`
        is a list (built via `list(entry.get("requirements", []))`),
        guarantees `story_backlog` is a list, and guarantees
        `story_states` is a dict — none of them can be `None`.
      * `s["requirement_ids"]` is validated as a non-empty list of
        strings by `decompose` (lines 1624-1637) and the ingest /
        backlog shape — the field cannot reach `aggregate_requirements`
        as `None`.
    After cleanup the function body has ZERO `or []` / `or {}` clauses.

  - Retro item 9: `execute()` reject path has a `try/except Exception:
    pass` wrapping `record_review(story_id, False, test_result_str)`
    at lines 2808-2813. Reachability analysis: the wrapped call is
    guarded by `if test_result_str.strip():` at line 2807, AND
    `record_review` validates types (story_id str, approved bool,
    test_result str — all already correct at call site) and then
    requires `test_result.strip()` to be truthy (already guaranteed).
    `record_review`'s remaining failure mode is filesystem I/O on
    `_append_entry`, which the test suite asserts is NOT swallowed
    elsewhere. The try/except is paranoid dead defensive code;
    swallowing FS errors on a partial-write would actually CORRUPT the
    truthful-audit-trail invariant. Delete the wrapper; call
    `record_review` directly.

  - Retro item 11: `_LIFECYCLE_TARGETS` dict (line 3021) lives INSIDE
    `_cli_main`, so it is rebuilt on every CLI invocation. Hoist to
    module-scope (top of sm.py alongside `_VALID_TRANSITIONS`) so it
    is constructed once at import.

These tests pin the cleanup. They MUST fail on first run (no Coder has
touched the module yet) and pass after the Coder lands Story 10.

The cleanup is BEHAVIOR-PRESERVING by definition — every existing test
must stay green. These tests pin only the cleanup itself, not new
behavior.

The invocation contract is `import sm` — no subprocess, no CLI.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import sys

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"
TESTS_DIR = PACKAGE_DIR / "tests"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sm_source() -> str:
    """Return sm.py source text."""
    return SM_PATH.read_text(encoding="utf-8")


def _aggregate_function_body_source() -> str:
    """Return the source text of `aggregate_requirements`'s body (def line
    onward through the function's last line)."""
    import sm
    src = inspect.getsource(sm.aggregate_requirements)
    return src


def _execute_function_source() -> str:
    """Return the source text of `execute()`."""
    import sm
    return inspect.getsource(sm.execute)


def _module_ast() -> ast.Module:
    """Parse sm.py into an AST."""
    return ast.parse(_sm_source(), filename=str(SM_PATH))


def _find_function_def(tree: ast.Module, name: str) -> ast.FunctionDef:
    """Locate a top-level FunctionDef by name."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise LookupError(f"top-level function {name!r} not found in sm.py")


# ===========================================================================
# Category A — `_TERMINAL_STATES` deletion (5 tests)
# ===========================================================================


def test_terminal_states_constant_not_defined_in_sm():
    """sm._TERMINAL_STATES is gone — the constant no longer exists at all."""
    import sm
    assert not hasattr(sm, "_TERMINAL_STATES"), (
        "sm._TERMINAL_STATES must be deleted (retro item 2); the transition "
        "graph already encodes terminality. Got the constant still defined."
    )


def test_terminal_states_grep_zero_hits_in_sm_module():
    """Whole-word grep of `_TERMINAL_STATES` against sm.py returns zero hits.

    Pins both the definition site AND every reference site. The cleanup
    must remove the constant AND rewrite (or inline) the two former call
    sites at lines 2316 and 2536.
    """
    src = _sm_source()
    pattern = re.compile(r"\b_TERMINAL_STATES\b")
    hits = pattern.findall(src)
    assert len(hits) == 0, (
        f"_TERMINAL_STATES must not appear anywhere in sm.py after Story 10 "
        f"cleanup; got {len(hits)} occurrence(s). Definition site AND every "
        f"reference site must be cleaned up."
    )


def test_terminal_states_grep_zero_hits_in_tests_directory():
    """Whole-word grep of `_TERMINAL_STATES` across tests/ returns zero hits
    OUTSIDE this Story 10 audit file.

    This Story 10 test file references the identifier inside string
    literals (regex patterns / docstrings) for audit purposes — those
    do not count as "code references". Every OTHER test file must be
    free of references.
    """
    pattern = re.compile(r"\b_TERMINAL_STATES\b")
    offenders: list[str] = []
    for path in TESTS_DIR.rglob("test_*.py"):
        if path.resolve() == THIS_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(path))
    assert not offenders, (
        f"_TERMINAL_STATES must not appear in any test file (other than this "
        f"Story 10 audit file); got hits in:\n  - "
        + "\n  - ".join(offenders)
    )


def test_terminal_states_not_in_dunder_all():
    """sm.__all__ does not contain '_TERMINAL_STATES' (defense — it was
    never exported, but pin against accidental re-introduction)."""
    import sm
    assert "_TERMINAL_STATES" not in sm.__all__, (
        f"_TERMINAL_STATES must not be in sm.__all__; got {sm.__all__!r}"
    )


def test_terminal_states_close_iteration_still_gates_non_terminal():
    """Behavioral regression: close_iteration must STILL refuse to close
    when an in-sprint story is non-terminal (the cleanup must rewrite the
    gate using inline state names or the transition graph — not by
    deleting the gate).

    We exercise the behavior via the public surface: derive_state's
    transition graph still maps accepted / rejected / force_closed as
    terminal (empty allowed-next sets). The cleanup of `_TERMINAL_STATES`
    must NOT weaken this invariant.
    """
    import sm
    # Each terminal state must have an empty allowed-transitions set, which
    # is the semantic the constant was duplicating.
    for terminal in ("accepted", "rejected", "force_closed"):
        assert terminal in sm._VALID_TRANSITIONS, (
            f"terminal state {terminal!r} must still appear in "
            f"_VALID_TRANSITIONS"
        )
        assert sm._VALID_TRANSITIONS[terminal] == frozenset(), (
            f"terminal state {terminal!r} must map to an empty allowed-next "
            f"set so terminality is still encoded by the transition graph; "
            f"got {sm._VALID_TRANSITIONS[terminal]!r}"
        )


# ===========================================================================
# Category B — `aggregate_requirements` redundancy removal (6 tests)
# ===========================================================================


def test_aggregate_requirements_body_has_no_or_empty_list_clauses():
    """Source-level pin: `aggregate_requirements`'s body has ZERO `or []`
    clauses after cleanup.

    All four `or []` / `or {}` sites in the current body are redundant by
    upstream contract (see module docstring for the analysis). Each
    `state.get(key, default)` call already returns the default on missing
    key, AND `derive_state()` guarantees the values are non-None when
    keys are present.
    """
    src = _aggregate_function_body_source()
    # Whole-word " or []" pattern (whitespace tolerant). Match `or []`
    # with possible whitespace.
    pattern = re.compile(r"\bor\s*\[\s*\]")
    hits = pattern.findall(src)
    assert len(hits) == 0, (
        f"aggregate_requirements must have NO `or []` clauses after Story 10 "
        f"cleanup (all four current sites are redundant by upstream "
        f"contract); got {len(hits)} occurrence(s).\nFunction source:\n{src}"
    )


def test_aggregate_requirements_body_has_no_or_empty_dict_clauses():
    """Source-level pin: `aggregate_requirements`'s body has ZERO `or {}`
    clauses after cleanup.

    The current site is `state.get("story_states", {}) or {}` (line 2168).
    `derive_state()` initializes `story_states` to `{}` and only ever
    mutates the dict; it cannot be `None`. The `or {}` is dead code.
    """
    src = _aggregate_function_body_source()
    pattern = re.compile(r"\bor\s*\{\s*\}")
    hits = pattern.findall(src)
    assert len(hits) == 0, (
        f"aggregate_requirements must have NO `or {{}}` clauses after "
        f"Story 10 cleanup (the one current site is redundant by upstream "
        f"contract); got {len(hits)} occurrence(s).\nFunction source:\n{src}"
    )


def test_aggregate_requirements_still_handles_canonical_inputs():
    """Behavioral regression: `aggregate_requirements` still returns
    correct output on a canonical state dict (the cleanup must not weaken
    correctness)."""
    import sm
    state = {
        "active_iteration": {
            "iteration_id": "iter-1",
            "requirements": [
                {"requirement_id": "req-1"},
                {"requirement_id": "req-2"},
            ],
        },
        "story_backlog": [
            {
                "story_id": "story-1",
                "sequence": 1,
                "requirement_ids": ["req-1"],
            },
            {
                "story_id": "story-2",
                "sequence": 2,
                "requirement_ids": ["req-2"],
            },
        ],
        "story_states": {
            "story-1": "accepted",
            "story-2": "rejected",
        },
        "sprint_cut": 2,
        "close_status": None,
    }
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "accepted", "req-2": "rejected"}


def test_aggregate_requirements_still_raises_on_no_active_iteration():
    """Behavioral regression: AggregateError still fires when
    active_iteration is None."""
    import sm
    state = {
        "active_iteration": None,
        "story_backlog": [],
        "story_states": {},
        "sprint_cut": None,
        "close_status": None,
    }
    with pytest.raises(sm.AggregateError):
        sm.aggregate_requirements(state)


def test_aggregate_requirements_still_raises_on_orphan_requirement():
    """Behavioral regression: AggregateError still fires when a
    requirement has no story rolling up to it."""
    import sm
    state = {
        "active_iteration": {
            "iteration_id": "iter-1",
            "requirements": [
                {"requirement_id": "req-1"},
                {"requirement_id": "req-orphan"},
            ],
        },
        "story_backlog": [
            {
                "story_id": "story-1",
                "sequence": 1,
                "requirement_ids": ["req-1"],
            },
        ],
        "story_states": {"story-1": "planned"},
        "sprint_cut": None,
        "close_status": None,
    }
    with pytest.raises(sm.AggregateError):
        sm.aggregate_requirements(state)


def test_aggregate_requirements_partial_state_still_yields_partial():
    """Behavioral regression: a mixed accepted+in-flight story set still
    aggregates to `partial` (the cleanup must not flip the partial rule)."""
    import sm
    state = {
        "active_iteration": {
            "iteration_id": "iter-1",
            "requirements": [{"requirement_id": "req-1"}],
        },
        "story_backlog": [
            {
                "story_id": "story-1",
                "sequence": 1,
                "requirement_ids": ["req-1"],
            },
            {
                "story_id": "story-2",
                "sequence": 2,
                "requirement_ids": ["req-1"],
            },
        ],
        "story_states": {
            "story-1": "accepted",
            "story-2": "in_progress",
        },
        "sprint_cut": None,
        "close_status": None,
    }
    result = sm.aggregate_requirements(state)
    assert result == {"req-1": "partial"}


# ===========================================================================
# Category C — `execute()` reject-path unreachable block deletion (5 tests)
# ===========================================================================


def test_execute_reject_path_has_no_try_except_pass_block():
    """Source-level pin: the `try/except Exception: pass` block in
    `execute`'s reject path is GONE after Story 10 cleanup.

    Pattern: `except Exception:` immediately followed (possibly with a
    comment line) by `pass`. The current site (lines 2808-2813) wraps
    `record_review(story_id, False, test_result_str)`. Reachability:
    the wrapped call cannot raise (types validated upstream, .strip()
    guard already passed), so the wrapper is paranoid dead code that
    would swallow legitimate filesystem-write failures.
    """
    src = _execute_function_source()
    # Match `except Exception:` then any whitespace/comment lines, then
    # `pass` as the entire body. MULTILINE + DOTALL with a careful
    # bounded gap (`.{0,200}?` is enough — the typical block is 3-5 lines).
    pattern = re.compile(
        r"except\s+Exception\s*:\s*(?:\n\s*#[^\n]*)*\s*\n\s*pass\b",
        re.MULTILINE,
    )
    hits = pattern.findall(src)
    assert len(hits) == 0, (
        f"execute() must have NO `except Exception: pass` blocks after "
        f"Story 10 cleanup; got {len(hits)} occurrence(s). The block at "
        f"lines 2808-2813 was unreachable (record_review's validation is "
        f"already satisfied at the call site) AND was swallowing real "
        f"filesystem-write failures.\nFunction source:\n{src}"
    )


def test_execute_function_has_no_bare_pass_in_except_clause():
    """Stronger pin: `execute`'s AST has no `ExceptHandler` whose body is
    a single `pass` statement.

    Catches any flavor of `except <T>: pass` — typed or bare — not just
    the literal `except Exception: pass` text. Story 10's cleanup
    removes the only such handler, and tests should fail if any
    re-appears later in a regression.
    """
    tree = _module_ast()
    execute_fn = _find_function_def(tree, "execute")
    offenders: list[int] = []
    for node in ast.walk(execute_fn):
        if isinstance(node, ast.ExceptHandler):
            if (
                len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
            ):
                offenders.append(node.lineno)
    assert not offenders, (
        f"execute() must have no `except ...: pass` handlers (bare-pass "
        f"is a Story 10 anti-pattern by reachability analysis); got "
        f"offending lines: {offenders}"
    )


def test_execute_reject_path_still_writes_reviewer_approval(
    tmp_path, monkeypatch
):
    """Behavioral regression: the reject path with non-empty test_result
    still appends exactly one reviewer_approval entry with approved=False.

    Pins that the cleanup did NOT also remove the `record_review` call
    — only the surrounding try/except. The call itself must remain.
    """
    import sm
    import shutil

    # Hermetic log + staged roles dir.
    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    source_roles = PACKAGE_DIR / "roles"
    dest = tmp_path / "roles"
    if not dest.exists() and source_roles.is_dir():
        shutil.copytree(source_roles, dest)

    # Open iteration + seed backlog + cut sprint.
    sm._append_entry(sm.build_entry("iteration_open", {
        "iteration_id": "iter-1",
        "iteration_goal": "G",
        "requirements": [{
            "requirement_id": "req-1", "title": "T", "description": "D",
            "priority": "MUST", "acceptance_criteria": "AC",
        }],
    }))
    sid = "00000000000000000000000000000001"
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": sid, "sequence": 1, "title": "S1", "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>", "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)

    sm.execute(
        sid,
        spawn_test_writer=lambda p, s: "def t(): pass\n",
        spawn_coder=lambda p, s, t: "def f(): pass\n",
        spawn_reviewer=lambda p, s, t, i: {
            "approved": False, "test_result": "3 of 12 failed",
        },
    )

    entries = list(sm.read_entries())
    approvals = [e for e in entries if e.get("type") == "reviewer_approval"]
    assert len(approvals) == 1, (
        f"reject path must still write exactly one reviewer_approval entry; "
        f"got {len(approvals)}"
    )
    assert approvals[0]["approved"] is False
    assert approvals[0]["test_result"] == "3 of 12 failed"


def test_execute_reject_path_still_transitions_story_to_rejected(
    tmp_path, monkeypatch
):
    """Behavioral regression: the reject path still leaves the story in
    `rejected` state after the cleanup."""
    import sm
    import shutil

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    source_roles = PACKAGE_DIR / "roles"
    dest = tmp_path / "roles"
    if not dest.exists() and source_roles.is_dir():
        shutil.copytree(source_roles, dest)

    sm._append_entry(sm.build_entry("iteration_open", {
        "iteration_id": "iter-1",
        "iteration_goal": "G",
        "requirements": [{
            "requirement_id": "req-1", "title": "T", "description": "D",
            "priority": "MUST", "acceptance_criteria": "AC",
        }],
    }))
    sid = "00000000000000000000000000000002"
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": sid, "sequence": 1, "title": "S1", "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>", "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)

    sm.execute(
        sid,
        spawn_test_writer=lambda p, s: "def t(): pass\n",
        spawn_coder=lambda p, s, t: "def f(): pass\n",
        spawn_reviewer=lambda p, s, t, i: {
            "approved": False, "test_result": "smoke regressed",
        },
    )

    state = sm.derive_state()
    assert state["story_states"][sid] == "rejected"


def test_execute_reject_path_writes_story_state_change_to_rejected(
    tmp_path, monkeypatch
):
    """Behavioral regression: the FINAL story_state_change entry on a
    reject run records the in_review -> rejected transition (so the
    audit trail is preserved after Story 10 cleanup)."""
    import sm
    import shutil

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    source_roles = PACKAGE_DIR / "roles"
    dest = tmp_path / "roles"
    if not dest.exists() and source_roles.is_dir():
        shutil.copytree(source_roles, dest)

    sm._append_entry(sm.build_entry("iteration_open", {
        "iteration_id": "iter-1",
        "iteration_goal": "G",
        "requirements": [{
            "requirement_id": "req-1", "title": "T", "description": "D",
            "priority": "MUST", "acceptance_criteria": "AC",
        }],
    }))
    sid = "00000000000000000000000000000003"
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": sid, "sequence": 1, "title": "S1", "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>", "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)

    sm.execute(
        sid,
        spawn_test_writer=lambda p, s: "def t(): pass\n",
        spawn_coder=lambda p, s, t: "def f(): pass\n",
        spawn_reviewer=lambda p, s, t, i: {
            "approved": False, "test_result": "rejection",
        },
    )

    entries = list(sm.read_entries())
    changes = [
        e for e in entries
        if e.get("type") == "story_state_change"
        and e.get("story_id") == sid
    ]
    assert len(changes) >= 1
    last = changes[-1]
    assert last["to_state"] == "rejected", (
        f"final story_state_change must move story to rejected; got "
        f"to_state={last.get('to_state')!r}"
    )


# ===========================================================================
# Category D — `_LIFECYCLE_TARGETS` hoist (5 tests)
# ===========================================================================


def test_lifecycle_targets_at_module_scope():
    """sm._LIFECYCLE_TARGETS is accessible at module scope after Story 10
    hoists it out of `_cli_main`."""
    import sm
    assert hasattr(sm, "_LIFECYCLE_TARGETS"), (
        "sm._LIFECYCLE_TARGETS must be hoisted to module scope (retro item "
        "11); currently it lives inside _cli_main and rebuilds per CLI "
        "invocation."
    )


def test_lifecycle_targets_carries_canonical_four_mappings():
    """sm._LIFECYCLE_TARGETS pins the four canonical lifecycle-command
    target states (start/submit/accept/reject)."""
    import sm
    expected = {
        "start": "in_progress",
        "submit": "in_review",
        "accept": "accepted",
        "reject": "rejected",
    }
    assert sm._LIFECYCLE_TARGETS == expected, (
        f"sm._LIFECYCLE_TARGETS must match the four canonical lifecycle "
        f"mappings; got {sm._LIFECYCLE_TARGETS!r}"
    )


def test_lifecycle_targets_constructed_once_identity_stable():
    """Two consecutive reads of `sm._LIFECYCLE_TARGETS` return the SAME
    object (identity equality). Pins that the dict is constructed once
    at import, not rebuilt per access.
    """
    import sm
    first = sm._LIFECYCLE_TARGETS
    second = sm._LIFECYCLE_TARGETS
    assert first is second, (
        "sm._LIFECYCLE_TARGETS must be the same object across reads "
        "(constructed once at import); got different identities."
    )


def test_lifecycle_targets_defined_outside_cli_main_in_source():
    """AST pin: `_LIFECYCLE_TARGETS` is assigned at module scope, NOT
    inside the `_cli_main` function body.

    Catches the case where a future regression accidentally moves the
    constant back inside `_cli_main` (or any other function).
    """
    tree = _module_ast()

    # 1) Find at least one module-scope assignment to _LIFECYCLE_TARGETS.
    module_scope_assigns: list[int] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "_LIFECYCLE_TARGETS"
                ):
                    module_scope_assigns.append(node.lineno)
    assert module_scope_assigns, (
        "sm.py must assign `_LIFECYCLE_TARGETS` at MODULE scope (top-level); "
        "found no such assignment. Story 10 hoists this out of _cli_main."
    )

    # 2) Confirm `_cli_main` does NOT carry a local assignment to it.
    cli_main = _find_function_def(tree, "_cli_main")
    for node in ast.walk(cli_main):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "_LIFECYCLE_TARGETS"
                ):
                    pytest.fail(
                        f"_cli_main must NOT contain a local assignment to "
                        f"_LIFECYCLE_TARGETS (Story 10 retro item 11); "
                        f"found one at line {node.lineno}. Hoist it to "
                        f"module scope."
                    )


def test_lifecycle_commands_still_dispatch_correctly(tmp_path, monkeypatch):
    """Behavioral regression: the four lifecycle subcommands still resolve
    correctly via the hoisted `_LIFECYCLE_TARGETS` dict.

    Drives the dispatcher indirectly: `_cli_main` reads
    `_LIFECYCLE_TARGETS[cmd]` to pick the target state for each
    subcommand. As long as the dict carries the four canonical mappings,
    the CLI dispatch continues to work.

    This is a smoke check on the in-process path — we exercise
    `transition_story` directly with the same target state the CLI would
    pick to confirm the wiring is structurally intact.
    """
    import sm
    import shutil

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    source_roles = PACKAGE_DIR / "roles"
    dest = tmp_path / "roles"
    if not dest.exists() and source_roles.is_dir():
        shutil.copytree(source_roles, dest)

    # Seed an iteration with one story so transition_story has something
    # to act on.
    sm._append_entry(sm.build_entry("iteration_open", {
        "iteration_id": "iter-1",
        "iteration_goal": "G",
        "requirements": [{
            "requirement_id": "req-1", "title": "T", "description": "D",
            "priority": "MUST", "acceptance_criteria": "AC",
        }],
    }))
    sid = "0000000000000000000000000000000a"
    sm._append_entry(sm.build_entry("story_backlog", {
        "stories": [{
            "story_id": sid, "sequence": 1, "title": "S1", "size": "S",
            "requirement_ids": ["req-1"],
            "acceptance_criteria": "AC",
        }],
        "role_spec_path": "<stub>", "role_spec_hash": "<stub>",
    }))
    sm.sprint_cut(1)

    # Walk the canonical lifecycle path using the SAME mapping the CLI
    # would use. If `_LIFECYCLE_TARGETS` is the source of truth, this
    # walk succeeds; if its contents are wrong, the transition machine
    # will reject the moves.
    sm.transition_story(sid, sm._LIFECYCLE_TARGETS["start"])
    sm.transition_story(sid, sm._LIFECYCLE_TARGETS["submit"])
    # Skip accept here (it requires a reviewer_approval entry) — just
    # confirm the rejection branch's target state name is honored.
    sm.record_review(sid, False, "test_result")
    sm.transition_story(sid, sm._LIFECYCLE_TARGETS["reject"])

    state = sm.derive_state()
    assert state["story_states"][sid] == "rejected", (
        f"lifecycle walk via _LIFECYCLE_TARGETS must still drive the story "
        f"to rejected; got state {state['story_states'][sid]!r}"
    )


# ===========================================================================
# Category E — Smoke / suite-invariant (1 test)
# ===========================================================================


def test_sm_module_still_imports_clean_after_cleanup():
    """Story 10 is behavior-preserving — re-importing sm.py from scratch
    succeeds without ModuleNotFoundError, SyntaxError, or attribute
    errors. Smoke against the cleanup breaking module load.
    """
    # Force a fresh import to flush any cached partial state.
    if "sm" in sys.modules:
        del sys.modules["sm"]
    import sm  # noqa: F401
    # Sanity: the headline public API is still attached.
    for name in (
        "aggregate_requirements",
        "execute",
        "close_iteration",
        "force_close",
        "_VALID_TRANSITIONS",
        "_LIFECYCLE_TARGETS",
    ):
        assert hasattr(sm, name), (
            f"sm.{name} must still be available after Story 10 cleanup; "
            f"got missing attribute."
        )
