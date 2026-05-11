# sm-tool — Iter 2 Sprint 2 Plan

Triggered by Cardiff smoke run (Story 16) surfacing 2 design gaps:
1. LLM wraps JSON output in markdown ` ```json ... ``` ` fences — `parse_agent_json` runs raw `json.loads` and chokes
2. SM Agent role spec doesn't tightly pin the output schema — model used `backlog`/`summary`/`acceptance` instead of `stories`/`title`/`acceptance_criteria`, and assigned `story_id` (operator's job)

## STORY BACKLOG (Sprint 2)

### Story 17: parse_agent_json strips markdown code fences (size: S)

ROLLS UP TO: requirement #s [N/A — gap surfaced by Story 16 smoke run]

AS AN operator, I WANT `parse_agent_json` to tolerate markdown code fences around JSON SO THAT typical LLM outputs (the model frequently wraps JSON in ` ```json ... ``` `) parse successfully instead of failing with `DecomposeOutputParseError` / `ReviewerAgentError`.

ACCEPTANCE (technical):
  - `parse_agent_json(raw, role)` strips leading ` ```json ` / ` ``` ` / ` ```JSON ` prefixes AND trailing ` ``` ` from the raw string BEFORE invoking `json.loads`
  - Strips also leading/trailing whitespace and newlines outside the fences
  - Preserves the existing typed-error contract: still raises the role-specific parse error on genuine JSON syntax errors AFTER the fence strip
  - Behaviorally tested: input ` ```json\n{"k": 1}\n``` ` returns `{"k": 1}`; input `{"k": 1}` still returns `{"k": 1}` (no regression); input `not json` still raises the typed parse error
  - Used by both Story 6 decompose and Story 9 reviewer paths automatically (no caller-side changes)

DEPENDS ON: none (single-function hardening)

### Story 18: SM Agent role spec tightening — exact schema + no fences (size: S)

ROLLS UP TO: requirement #s [N/A — gap surfaced by Story 16 smoke run]

AS AN operator, I WANT `roles/sm_agent.md` to pin the exact output schema with examples and explicitly forbid markdown fences SO THAT the live SDK call returns parseable JSON matching the operator-expected shape.

ACCEPTANCE (technical):
  - `roles/sm_agent.md` OUTPUT FORMAT section pins exactly: top-level `stories` (array), per-story keys `sequence` (int), `title` (str), `size` ("S"/"M"/"L"), `requirement_ids` (array of str), `acceptance_criteria` (str)
  - Explicitly forbids: top-level `backlog` key, per-story `summary`/`acceptance` keys, per-story `story_id` (operator assigns uuid4-hex)
  - Includes a positive example block showing the exact correct JSON
  - Includes a negative example block showing what NOT to return (markdown fences, wrong field names)
  - "NO MARKDOWN CODE FENCES" appears verbatim in the OUTPUT FORMAT section
  - No code change in sm.py — this is a markdown-only fix to the role spec

DEPENDS ON: none

### Story 16 (re-run): Cardiff live smoke run, post-Sprint-2 (size: M)

After Stories 17 + 18 close, re-run `iter2/run_smoke.ps1` end-to-end. Expected: decompose succeeds, status shows the story backlog with operator-assigned uuid4 story_ids matching the role spec shape, execute drives to a terminal state.

## SEQUENCING

17 → 18 → re-run 16. Both 17 and 18 are S; total work ~30 min.

## SUCCESS CRITERIA FOR ITER 2 SHIP

- All 2398 + new tests green with mocked SDK
- Cardiff smoke run (Story 16) closes green with REAL Anthropic API
- Tag v0.4.0
