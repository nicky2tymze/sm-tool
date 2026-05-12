# sm-tool iter4-multisprint-v2 — Retro

**Shipped:** 2026-05-11. iter4-multisprint-v2 closed with 5 of 5
stories accepted. Multi-sprint capability is live.

## Pivot context

iter4-multisprint v1 attempted to ship the multi-sprint fix via
dogfood (real-SDK execute pipeline). That attempt surfaced 5
architectural autonomy gaps (markdown-wrapped TestWriter outputs,
prose-as-file Coder outputs, wrong project_root, max_tokens
truncation, no Reviewer crash recovery) — documented in
`iter4/Pivot_Notes.md`. v1 was force-closed; v2 opened with the
same scope, orchestrator-driven.

## What shipped (req-1 in one focused iteration)

**Story 1 (M) — Lock relaxation:**
sprint_cut now accepts re-cut when all prior in-sprint stories are
terminal (accepted/rejected/force_closed); rejects when any are
non-terminal (planned/in_progress/in_review). Lock checks only the
LATEST sprint_cut's roster. Error names offenders with their states.
Cross-iteration scope fixed (a latent bug where prior closed
iterations' cuts would have blocked re-cut under the relaxed
semantics).

**Story 2 (S) — derive_state verification:**
Verification-only. derive_state already supports multi-sprint_cut
replay (latest wins); Story 2 pinned the contract explicitly.

**Story 3 (S) — cut_position semantics:**
N now applies to currently-planned stories only (terminal-resolved
stories from prior cuts don't count). in_sprint_story_ids carries
only the new cut's roster. deferred_story_ids includes prior-cut
terminal stories. cut_position field = N (count for this cut, not
cumulative).

**Story 4 (S) — close_iteration multi-sprint validation:**
close_iteration now validates every story across EVERY sprint_cut
entry in the active iteration, attributing offenders to their
sprint position (1-indexed by append order). Implementation:
_derive_state_full extended to 4-tuple (new
`active_iteration_sprint_cohorts` slot). Forced by Iter 2's
single-pass-read contract — option (a) over option (b) because
option (b) would have broken the retro single-pass test.

**Story 5 (S) — _HELP_TEXT refresh:**
sprint-cut and close descriptions updated to reflect multi-sprint
semantics. New notes block between Mutating and Terminal command
sections explains: multiple cuts allowed once prior cuts reach
terminal state; N applies to currently-planned stories; close
validates all sprints. Drift catcher (Iter 2 Story 13) preserved
intact.

## By the numbers

- Stories: 5 (1 M + 4 S)
- Tests added net: 98 (2784 → 2882) ← from start of iter4-v2
- Cascade events absorbed: 5 large (Story 3's 39-test cascade
  across 3 files; Stories 1, 4 had smaller ones)
- Verification-only stories recognized: 1 (Story 2 — SM Agent's
  over-decomposition pattern visible again, consistent with
  iter3 Findings.md Finding 2)
- TestWriter typos fixed inline: 1 (Story 4's
  `planned→in_review` illegal transition)
- Wall-clock from v1 pivot to v2 close: ~1.5 hours

## Process notes from this iteration

1. **The dogfood R&D Sprint pattern worked.** Per the
   freshly-codified R&D Sprint discipline, the iter4-multisprint v1
   dogfood was an R&D Sprint by nature; its findings flowed
   directly into v2's scope decision (orchestrator-driven) and
   into a future dogfood-viability iteration. No process violation.

2. **TestWriter over-decomposition reproduced.** Story 2 was
   verification-only (same pattern as iter3-autonomy Stories 4 + 9).
   The SM Agent appears to default to splitting "implement" from
   "test" stories even when the implementation IS test-driven.
   Filed earlier under iter3/Findings.md Finding 2; this iteration
   adds another data point.

3. **Coder caught a TestWriter typo and refused to wing it.**
   Story 4's TestWriter wrote
   `_craft_state_change(planned, in_review)` which violates
   `_VALID_TRANSITIONS`. Coder reported the typo and stopped rather
   than globally relax the transition graph. This is the
   Never-Wing-It + Solution-Has-Gravity discipline working at the
   agent layer.

4. **One implementation choice was forced by a prior-iteration
   contract.** Story 4's option (b) — localized walk inside
   close_iteration — would have broken Iter 2's single-pass-read
   contract. Option (a) — extending _derive_state_full's tuple
   return — was the only viable path. Real evidence that
   well-codified contracts narrow the design space toward correct
   solutions (per Solution-Has-Gravity).

## Findings flowing into next iteration planning

(Updated `iter4/Findings.md` with the new finding below; iter3's
findings still active and unaddressed.)

**Iter 4 Finding 1: SM Agent over-decomposition (third data point).**
Story 2 of iter4-multisprint-v2 was verification-only, third
occurrence after iter3-autonomy Stories 4 + 9. Confirmed pattern.
The fix (tune `roles/sm_agent.md`) is in iter3 Findings as Finding
2; raise priority based on the third occurrence.

**Iter 4 Finding 2: Pending collapse — work-unit primitive.**
Architect's hypothesis cascade 2026-05-11 afternoon proposed that
Story / MiniSprint / R&D Sprint may all collapse into one primitive
with attributes {shape, size, placement_constraint, ceremony_floor}.
Tracked in `feedback_rule_addition_then_collapse.md`. Watch for
the collapse over next 2-3 iterations.

## Versioning

Target: **v0.4.2**. Real capability ship (multi-sprint enabled).
v0.5.0 still holds for the full iter3-autonomy scope (req-3
max_tokens tuning + req-4 autonomy regression smoke) plus
dogfood-viability work.

## Next iteration

Architect's call between:
- **iter5**: dogfood-viability fixes (the 5 architectural gaps from
  iter4-multisprint v1) — opens dogfood for real
- **iter5**: req-3 + req-4 deferred from iter3-autonomy — closes
  iter3 v2's original scope
- **iter5**: the 6 polish items deferred from Iter 3 v1 (Iter 3
  Findings Finding 1) — original Iter 3 ask
- Or another scope per Architect priority
