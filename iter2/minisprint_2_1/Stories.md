# sm-tool MiniSprint 2.1 — Stories

## Story MS2.1-1: Role-spec positive-example drift catcher (size: S, verification-only)

ROLLS UP TO: Iter 2 Findings.md Finding 1.

AS AN operator, I WANT a unit test that parses the `POSITIVE EXAMPLE`
blocks out of `roles/sm_agent.md` and `roles/reviewer.md` and runs
them through the production validators SO THAT a future role-spec
edit that drifts the example off the code's contract fails the suite
before any smoke run is needed.

ACCEPTANCE (technical):
  - New test file at `tests/test_role_spec_examples_validate.py`.
  - Helper `_extract_positive_examples(path)` parses every literal
    JSON block following a `POSITIVE EXAMPLE` header in the markdown.
  - `sm_agent.md` examples: parsed via `parse_agent_json(role="decompose")`,
    asserted to be a dict with top-level `stories` key, every story
    has exactly the 5 canonical keys (`sequence`, `title`, `size`,
    `requirement_ids`, `acceptance_criteria`), every story's
    per-field type matches the production validator's expectations.
  - `reviewer.md` examples: parsed via `parse_agent_json(role="reviewer")`,
    asserted to be a dict with exactly the 2 canonical keys
    (`approved`: strict bool, `test_result`: non-empty str), and at
    least one accept (approved=True) AND one reject (approved=False)
    example exists (the LLM needs to be taught both verdicts).
  - Test count: ~10 tests, all pass on the current tree.
  - Verification-only — no production code change.

DEPENDS ON: none (operates on existing artifacts).

## Note on execution

This is a VERIFICATION-ONLY story per the MiniSprint ceremony floor.
The role specs from Iter 2 Stories 18 + 18b already include valid
positive examples; this test merely codifies the contract between
those examples and the production validators. No TestWriter/Coder
cycle needed — orchestrator writes the test directly, runs it, and
verifies it green.
