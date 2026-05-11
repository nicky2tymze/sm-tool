# sm-tool MiniSprint 2.1 — Customer Ask

**Opened:** 2026-05-11 (immediately after Iter 2 close at v0.4.0)
**Previous sprint:** Iter 2 Sprint 2 (closed v0.4.0)
**Target version:** v0.4.1

## Relation to prior sprint (rule 3 of MiniSprint pattern)

This MiniSprint addresses **Iter 2 Findings.md Finding 1**
(role-spec / code-contract drift catcher), filed during the Iter 2
Sprint 2 close. The finding documents that the role specs at
`roles/sm_agent.md` and `roles/reviewer.md` had drifted from the
schemas the production code validates against — invisible to 2398
unit tests because all of them mock the SDK with synthetic output
shaped to the code's contract. Only the live Cardiff smoke run
(Story 16, first attempt) exposed the divergence.

## The ask

Add a small unit-test layer that parses the `POSITIVE EXAMPLE` blocks
out of `roles/sm_agent.md` and `roles/reviewer.md`, runs them through
`parse_agent_json` and the corresponding production shape validators,
and asserts the examples are accepted. If a future role-spec edit
drifts the example off the production contract, this test fails the
suite before any smoke run is needed.

The role specs from Stories 18 and 18b already include the positive
example blocks (one in `sm_agent.md`, two in `reviewer.md` covering
both accept and reject paths). The work is the test file — no
production code change.

## Scope

Single S story. Test file only. Closes Finding 1.

## Non-goals (rule 1 — single story)

- No expansion to other role specs beyond sm_agent and reviewer
  (test_writer and coder return raw code, not JSON — nothing to
  validate against parse_agent_json)
- No changes to the role spec content itself (Stories 18 + 18b
  already locked it)
- No changes to parse_agent_json or the shape validators (the test
  exercises them, doesn't modify them)
