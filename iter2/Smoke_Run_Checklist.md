# Iter 2 Story 16 — Cardiff Live Smoke Run

**Status:** PENDING — operator-executed per ASSUMPTION 7. Run once before Iter 2 sign-off.

## Pre-flight

- [ ] `ANTHROPIC_API_KEY` set in current shell (operator's key — billed to operator's account)
- [ ] Working directory: `C:\Users\nickt\Desktop\sm-tool`
- [ ] Full suite green: `python -m pytest -q` returns rc 0 with 2398/2398 passing (with the live-SDK guard active, this stays mocked)
- [ ] Clean isolated log via `SM_TEST_LOG_PATH` — DO NOT run against the package's real `log.jsonl`

## Cost estimate

- 1 × decompose SDK call (Haiku 4.5, ~1k input tokens, ~2k output tokens)
- 1 × execute pipeline = 3 SDK calls (test_writer + coder + reviewer, each ~1k in / ~2k out)
- Total: ~4 SDK calls, well under $0.10 at Haiku 4.5 pricing

## Smoke run script

```powershell
# 1. Set up isolated log + iteration handoff
$env:SM_TEST_LOG_PATH = "C:\Users\nickt\Desktop\sm-tool\iter2\smoke_log.jsonl"
Remove-Item $env:SM_TEST_LOG_PATH -ErrorAction SilentlyContinue

# 2. Stage a minimal PO Tool handoff
@'
{
  "iteration_id": "cardiff-smoke-001",
  "iteration_goal": "Smoke-prove sm-tool runs end-to-end against real Anthropic API.",
  "requirements": [
    {
      "requirement_id": "req-1",
      "title": "Add greet function",
      "description": "Add a function greet(name) that returns 'Hello, <name>!' to a new utils.py module.",
      "priority": "MUST",
      "acceptance_criteria": "greet('Cardiff') returns the string 'Hello, Cardiff!'"
    }
  ]
}
'@ | Set-Content -Encoding utf8 C:\Users\nickt\Desktop\sm-tool\iter2\smoke_handoff.json

# 3. Ingest
python -m sm ingest C:\Users\nickt\Desktop\sm-tool\iter2\smoke_handoff.json

# 4. Decompose (REAL Anthropic SDK call — burns tokens)
python -m sm decompose

# 5. Check status — capture the story_id from the backlog
python -m sm status

# 6. Cut sprint at 1 (include the one story)
python -m sm sprint-cut 1

# 7. Start the story
python -m sm start <STORY_ID>

# 8. Execute (REAL test_writer + coder + reviewer pipeline — burns 3 SDK calls)
python -m sm execute <STORY_ID>

# 9. Submit + record-review + accept-or-reject based on reviewer output
# (See status to determine terminal state)
python -m sm status
```

## Capture (fill in during run)

- **Iteration id used:** `cardiff-smoke-001`
- **Story id assigned by decompose:** `_____` (uuid4-hex, from `status` output)
- **decompose entry written?** Yes / No
- **execute pipeline reached terminal state?** Yes / No
- **Terminal state observed:** `accepted` | `rejected` | other
- **reviewer approved?** True / False
- **Any deviations from expected behavior:** `_____`

## Expected log entries (in order)

1. `iteration_open` (from ingest)
2. `story_backlog` (from decompose — story_id uuid4-hex assigned)
3. `sprint_cut` (from sprint-cut 1)
4. `story_state_change` planned→in_progress (from start)
5. `testwriter_output` (from execute, test_writer stage)
6. `coder_output` (from execute, coder stage)
7. `reviewer_approval` (from execute, reviewer stage — carries approved + test_result)
8. `story_state_change` in_progress→in_review (from execute auto-transition) — or operator runs `submit` separately
9. Either `story_state_change` in_review→accepted (if approved) or in_review→rejected (if not)

## Sign-off

- [ ] All expected entries present in `smoke_log.jsonl`
- [ ] Terminal state reached without operator intervention beyond CLI invocations
- [ ] No SDK exceptions surfaced unwrapped (all wrapped as `*AgentError`)
- [ ] Total wall-clock under 2 minutes for the full pipeline

Once signed off → tag `v0.4.0` and Iter 2 ships.
