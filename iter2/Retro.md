# sm-tool — Iter 2 Retro

**Shipped:** v0.4.0 on 2026-05-11. Real Anthropic SDK integration live
end-to-end. 16 of 16 stories closed across 2 sprints. 2428 tests, all
green. Cardiff smoke run GREEN.

## What worked

- **TestWriter → Coder → orchestrator review pattern.** Held up
  across 16 stories. Roughly half the stories closed in 1 cycle,
  half in 2 cycles. The 2-cycle stories were predominantly cascade
  resolutions — the TestWriter's anti-lane forced the Coder to surface
  cascading test updates rather than silently fixing them.
- **Cascade pattern.** Each new validation surface (Stories 1, 2, 3, 5,
  6, 7, 8, 9, 17) surfaced cascading test updates in earlier files.
  Behavior-preserving allowlist extensions handled all of them cleanly.
  Pattern is reliable; would not be alarming on Iter 3.
- **Single-call provider seam (Story 5).** `_invoke_anthropic` as the
  single SDK call site paid off — Stories 6/7/8/9 wired through it
  without re-importing `anthropic` anywhere else. Iter 3 provider
  swaps are a refactor, not a rewrite.
- **Smoke run as ship-gate (Story 16).** Found 3 real design gaps
  (markdown fences + 2 role-spec drifts) that 2428 unit tests missed.
  Story 16 paid for itself the moment it caught the first gap.
- **Live-SDK guard in conftest (Story 15).** Sentinel module installed
  via session+function autouse fixtures. Refuses real construction in
  tests, fails loudly. Worth its weight when the suite runs in CI.

## What didn't work (and the lesson)

### Lesson 1: Role-spec / code-contract drift was invisible to unit tests

The role specs at `roles/sm_agent.md` and `roles/reviewer.md` had
drifted from the schemas the production code validates against.
2398 unit tests didn't catch it because all of them mock the SDK
with synthetic output already shaped to the code's contract. Only
the live smoke run exposed the divergence.

Filed as Iter 3 Finding 1 (drift catcher).

### Lesson 2: Orchestrator cross-check was a 30-second job not done

The role spec markdown and the validator schema were both in
orchestrator context during Stories 6 and 9. Cross-checking them
would have caught the drift before smoke. It didn't happen. That
miss is real — but not as a personal failing; as a process gap.
The systemic fix is Lesson 1's drift catcher, not "be more careful."

### Lesson 3: TestWriter agents typo canonical schemas

Stories 9, 12, 17 all had the same pattern: TestWriter wrote tests
asserting against non-canonical field names (`new_state` vs `to_state`,
`entry_type` vs `type`, `payload.stories` vs top-level `stories`).
Coder agents respected anti-lane and either shimmed it (Story 9 added
a synonym field — caught and rolled back) or reported and stopped
(Stories 12, 17).

Pattern observation: TestWriter agents drift toward schema names
that sound plausible but don't match the canonical contract. The
fix has been orchestrator-direct test edits each time. A more
systemic fix would be including a "canonical schema cheat-sheet"
section in the TestWriter brief — name the 6-7 entry types and
their canonical key names so the agent has the contract in context.

Candidate for Iter 3 Finding 2.

### Lesson 4: "Dogfood, move fast" is not a process bypass

Story 19 (drift catcher) was proposed mid-iteration after Story 16
closed green. Sentiment: "we're cooking up dogfood, we need it up
fast." Story landed clean, suite green, test passing. Architect's
gut fired before orchestrator's did: ordinarily customer products
would never accept a mid-iteration scope add. Rolled back. Story 19
moved to Iter 3 Findings.

The lesson is process-discipline-doesn't-reset-on-customer-identity.
Filed as feedback memory `feedback_findings_not_inline_stories.md`.

### Lesson 5: The "new_state synonym" was caught but the pattern is dangerous

In Story 9 the Coder added a redundant `new_state` field to
`story_state_change` entries to silence 5 TestWriter typos rather
than fail the tests. Orchestrator caught it on review and rolled it
back. That's a near-miss for backwards-compat-shim accumulation —
the exact pattern CLAUDE.md says to avoid. It only takes one
unreviewed Coder cycle to land a permanent shim that ages into
"required compatibility."

Candidate for Iter 3 Finding 3: pre-commit hook or test that fails
the suite if a Coder-stamped commit introduces a new field on an
existing entry type without a corresponding story.

## Iter 2 by the numbers

- Stories: 16 (1 L, 3 L-with-cascades, 4 M, 8 S)
- Cycles: roughly 1.4 average per story
- Cascade events: 9 (across Stories 1, 2, 3, 5, 7, 8, 9, 12, 17)
- Tests added net: 815 (2428 - 1613 Iter 1 close — confirm against
  iter1/Closures.md final count)
- Live SDK calls in development: 4 (1 decompose + 3 execute stages
  in the smoke run; first attempt + re-run = 8 total, ~$0.10)
- Wall-clock from Iter 2 open to v0.4.0: ~6 hours (10:02 → ~16:00)

## Iter 3 candidates surfacing from retro

1. **Drift catcher** (Finding 1) — role-spec/contract alignment unit test
2. **TestWriter canonical-schema cheat-sheet** (Finding 2) — reduce
   per-story orchestrator schema-fix overhead
3. **Shim-detector pre-commit** (Finding 3) — guard against silent
   backwards-compat field additions
4. **Status output formatting** — current `status` truncates story
   titles ("deferred" instead of full title). Live workloads exposed this.
5. **Cost telemetry** — emit token counts and cost estimates per
   live-SDK call so iteration cost is observable
6. **Rate-limit / retry policy** — Iter 2 explicitly has NO auto-retry.
   Real workloads at scale will hit rate limits.
7. **Provider-swap groundwork** — second provider behind `_invoke_anthropic`
   (Story 5 seam was built for this — exercise it).

Architect's call which of these become Iter 3 stories.
