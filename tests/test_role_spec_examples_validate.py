"""MiniSprint 2.1 — role-spec / code-contract drift catcher.

Closes Iter 2 Findings.md Finding 1: the role specs at
`roles/sm_agent.md` and `roles/reviewer.md` had drifted from the
schemas the production code validates against. The unit tests didn't
catch it because they all mock the SDK with synthetic output already
matching the code's contract. Only the live Cardiff smoke run
surfaced the gap.

The role specs (Iter 2 Stories 18 + 18b) now contain literal JSON
POSITIVE EXAMPLE blocks. This file extracts each example and runs it
through the exact same validator the production code uses. If a
future role-spec edit drifts the example off the code's contract,
this test fails the suite before any smoke run is needed.

Cheap, zero new production code, catches exactly this class of drift.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

ROLES_DIR = PACKAGE_DIR / "roles"


def _extract_positive_examples(role_spec_path: pathlib.Path) -> list[str]:
    """Pull every literal JSON block following a `POSITIVE EXAMPLE` header.

    Convention: each positive example block starts with a line beginning
    `POSITIVE EXAMPLE` followed by a blank-line separator and one JSON
    object/array starting with `{` or `[` on its own line.
    """
    text = role_spec_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"POSITIVE EXAMPLE[^\n]*\n(?:[^\n]*\n)*?\n(\{[^\n]*\}|\[[^\n]*\])",
        re.MULTILINE,
    )
    return [m.group(1) for m in pattern.finditer(text)]


# ---------------------------------------------------------------------------
# sm_agent.md — decompose role
# ---------------------------------------------------------------------------


def test_sm_agent_spec_has_at_least_one_positive_example():
    spec = ROLES_DIR / "sm_agent.md"
    examples = _extract_positive_examples(spec)
    assert len(examples) >= 1, (
        "roles/sm_agent.md should contain at least one POSITIVE EXAMPLE "
        f"block with a literal JSON object; found {len(examples)}"
    )


def test_sm_agent_examples_parse_as_json():
    import sm
    spec = ROLES_DIR / "sm_agent.md"
    for i, raw in enumerate(_extract_positive_examples(spec)):
        parsed = sm.parse_agent_json(raw, role="decompose")
        assert isinstance(parsed, dict), (
            f"sm_agent.md positive example #{i + 1} should parse as a "
            f"JSON object; got {type(parsed).__name__}"
        )


def test_sm_agent_examples_have_stories_top_level_key():
    import sm
    spec = ROLES_DIR / "sm_agent.md"
    for i, raw in enumerate(_extract_positive_examples(spec)):
        parsed = sm.parse_agent_json(raw, role="decompose")
        assert "stories" in parsed, (
            f"sm_agent.md positive example #{i + 1} must have top-level "
            f"'stories' key (the canonical contract); got keys "
            f"{sorted(parsed.keys())}"
        )


def test_sm_agent_examples_each_story_has_canonical_keys():
    """Every story in every positive example must have exactly the
    five canonical keys: sequence, title, size, requirement_ids,
    acceptance_criteria. Catches role-spec drift toward `summary`,
    `acceptance`, `story_id`, `depends_on`, etc.
    """
    import sm
    canonical_keys = {
        "sequence", "title", "size",
        "requirement_ids", "acceptance_criteria",
    }
    spec = ROLES_DIR / "sm_agent.md"
    for i, raw in enumerate(_extract_positive_examples(spec)):
        parsed = sm.parse_agent_json(raw, role="decompose")
        for j, story in enumerate(parsed["stories"]):
            assert isinstance(story, dict), (
                f"sm_agent.md example #{i + 1} story #{j + 1} must "
                f"be a dict; got {type(story).__name__}"
            )
            got_keys = set(story.keys())
            assert got_keys == canonical_keys, (
                f"sm_agent.md example #{i + 1} story #{j + 1} must "
                f"have exactly {sorted(canonical_keys)}; got "
                f"{sorted(got_keys)}. If the role spec is teaching the "
                f"LLM the wrong schema, the live SDK will return the "
                f"wrong shape and decompose will fail at runtime."
            )


def test_sm_agent_examples_each_story_has_correct_types():
    """Per-story types: sequence=int, title=non-empty str, size in
    {S,M,L}, requirement_ids=non-empty list[str], acceptance_criteria=
    non-empty str. Matches what decompose's downstream validation
    accepts.
    """
    import sm
    spec = ROLES_DIR / "sm_agent.md"
    for i, raw in enumerate(_extract_positive_examples(spec)):
        parsed = sm.parse_agent_json(raw, role="decompose")
        for j, story in enumerate(parsed["stories"]):
            assert isinstance(story["sequence"], int), (
                f"example #{i + 1} story #{j + 1}: sequence must be int"
            )
            assert isinstance(story["title"], str) and story["title"], (
                f"example #{i + 1} story #{j + 1}: title must be "
                f"non-empty str"
            )
            assert story["size"] in {"S", "M", "L"}, (
                f"example #{i + 1} story #{j + 1}: size must be in "
                f"{{S, M, L}}; got {story['size']!r}"
            )
            assert (
                isinstance(story["requirement_ids"], list)
                and story["requirement_ids"]
                and all(isinstance(r, str) for r in story["requirement_ids"])
            ), (
                f"example #{i + 1} story #{j + 1}: requirement_ids "
                f"must be non-empty list[str]"
            )
            assert (
                isinstance(story["acceptance_criteria"], str)
                and story["acceptance_criteria"]
            ), (
                f"example #{i + 1} story #{j + 1}: acceptance_criteria "
                f"must be non-empty str"
            )


# ---------------------------------------------------------------------------
# reviewer.md — reviewer role
# ---------------------------------------------------------------------------


def test_reviewer_spec_has_at_least_one_positive_example():
    spec = ROLES_DIR / "reviewer.md"
    examples = _extract_positive_examples(spec)
    assert len(examples) >= 1, (
        "roles/reviewer.md should contain at least one POSITIVE EXAMPLE "
        f"block with a literal JSON object; found {len(examples)}"
    )


def test_reviewer_examples_parse_as_json():
    import sm
    spec = ROLES_DIR / "reviewer.md"
    for i, raw in enumerate(_extract_positive_examples(spec)):
        parsed = sm.parse_agent_json(raw, role="reviewer")
        assert isinstance(parsed, dict), (
            f"reviewer.md positive example #{i + 1} should parse as a "
            f"JSON object; got {type(parsed).__name__}"
        )


def test_reviewer_examples_have_exactly_canonical_keys():
    """The reviewer's shape validator (Story 9
    _default_execute_reviewer_spawn) requires EXACTLY {approved,
    test_result}. Extra/missing/wrong-typed keys raise
    ReviewerAgentError. This pins that the role spec's examples obey
    that contract.
    """
    import sm
    canonical_keys = {"approved", "test_result"}
    spec = ROLES_DIR / "reviewer.md"
    for i, raw in enumerate(_extract_positive_examples(spec)):
        parsed = sm.parse_agent_json(raw, role="reviewer")
        got_keys = set(parsed.keys())
        assert got_keys == canonical_keys, (
            f"reviewer.md example #{i + 1} must have exactly "
            f"{sorted(canonical_keys)}; got {sorted(got_keys)}. If "
            f"the role spec teaches the LLM extra keys like "
            f"`verdict`/`clauses_met`/`notes`, Story 9's shape "
            f"validator will reject the review at runtime."
        )


def test_reviewer_examples_have_correct_types():
    """approved must be a strict bool (type is bool, not int 0/1),
    test_result must be a non-empty str."""
    import sm
    spec = ROLES_DIR / "reviewer.md"
    for i, raw in enumerate(_extract_positive_examples(spec)):
        parsed = sm.parse_agent_json(raw, role="reviewer")
        assert type(parsed["approved"]) is bool, (
            f"reviewer.md example #{i + 1}: approved must be a strict "
            f"bool (type bool, not int); got "
            f"{type(parsed['approved']).__name__}"
        )
        assert (
            isinstance(parsed["test_result"], str)
            and parsed["test_result"]
        ), (
            f"reviewer.md example #{i + 1}: test_result must be a "
            f"non-empty str; got "
            f"{type(parsed['test_result']).__name__}"
        )


def test_reviewer_examples_cover_both_verdicts():
    """At least one accept (approved=True) AND at least one reject
    (approved=False) example. Without both, the role spec under-teaches
    the model on one branch of the verdict.
    """
    import sm
    spec = ROLES_DIR / "reviewer.md"
    examples = _extract_positive_examples(spec)
    verdicts = set()
    for raw in examples:
        parsed = sm.parse_agent_json(raw, role="reviewer")
        verdicts.add(parsed["approved"])
    assert verdicts == {True, False}, (
        f"reviewer.md should have at least one accept (approved=True) "
        f"AND one reject (approved=False) positive example so the LLM "
        f"is taught both verdict paths; observed verdicts: "
        f"{sorted(verdicts, key=str)}"
    )
