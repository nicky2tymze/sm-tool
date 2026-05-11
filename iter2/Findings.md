# sm-tool — Iter 2 Findings (for Iter 3 sprint planning)

Findings surfaced during Iter 2 that DO NOT block Iter 2 close.
Each enters Iter 3 sprint planning where it competes on merit
against other candidates.

## Finding 1: Role-spec / code-contract drift catcher

**Source:** Iter 2 Story 16 first-attempt Cardiff smoke run (2026-05-11).

**What happened:** The role specs at `roles/sm_agent.md` and
`roles/reviewer.md` had drifted from the schemas the production code
validates against. `sm_agent.md` taught the LLM to return
`summary`/`acceptance`/`story_id` while `decompose`'s validator
accepts `title`/`acceptance_criteria` (and assigns `story_id`
itself). `reviewer.md` taught the LLM to return `verdict` +
`clauses_met` + `clauses_unmet` + `notes` + `suite_result` while
Story 9's shape validator accepts exactly `{approved, test_result}`.

The unit-test layer (2398 tests, all green) did not catch this —
every test mocks the SDK and injects synthetic agent output that
already matches the code's contract. Only the live smoke run
exposed the divergence.

Closed in Iter 2 via Stories 17 (fence strip in `parse_agent_json`)
+ 18 (`sm_agent.md` rewrite) + a parallel `reviewer.md` rewrite.

**Proposed Iter 3 story:** A small test (`tests/test_role_spec_examples_validate.py`,
~190 lines, 10 tests) that parses the `POSITIVE EXAMPLE` blocks out
of each role spec, runs them through `parse_agent_json` and the
corresponding shape validator, and asserts they accept. Catches the
exact class of drift before any smoke run is needed.

**Size:** S. No production code change. Zero new dependencies.

**Risk of NOT doing it:** Next role-spec edit drifts silently; the
next smoke run pays in tokens to discover what a unit test could
catch for free.

**Risk of doing it:** Minimal. Smoke remains the gate of record;
this is additive belt-and-suspenders. Role specs change rarely;
maintenance cost is near zero.

**Note:** A draft of this test was written during Iter 2 close
(10/10 green) but rolled back per Architect process correction:
mid-iteration discoveries are filed as findings for next-sprint
planning, not slipped in as inline stories — even for dogfood. The
draft is recoverable from git history if Iter 3 planning accepts it.

---

(Add additional Iter 2 findings here as they're surfaced during the
Iter 2 retro.)
