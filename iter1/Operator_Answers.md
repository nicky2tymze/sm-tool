# sm-tool — Operator Answers to PO v1 Open Questions

The 7 open questions PO v1 surfaced, answered for the v2 lock.
Rationale included so the SM Agent and downstream pipeline have
reasoning, not just rulings.

---

**1. PO Tool revising requirements mid-iteration.**

**Answer:** Hard error. If a handoff is ingested for an iteration that's already open in SM Tool's log, the ingest call returns an error and writes nothing. The rule "PO Tool won't do this while one is open" is the operator-level discipline; SM Tool enforces it as a precondition.

**Rationale:** Mirrors PO Tool's "one active iteration" contract. Silent no-op + warning hides the conflict; the operator should see the failure and resolve it.

---

**2. Is `rejected` a terminal state per story?**

**Answer:** Yes — terminal. A sprint can close with rejected stories. Iteration-close handoff carries the rejected story's outcome up to PO Tool, which then decides what happens to the upstream requirement (re-rank, defer, drop). SM Tool does not loop within an iteration.

**Rationale:** Suite convention is single-pass per cycle (close-and-flow). Forcing re-work-until-accepted within one iteration violates that. PO Tool is the home of "what do we do about this rejection."

---

**3. Story → requirement mapping in close handoff.**

**Answer:** Each story carries a `requirement_ids: list[str]` field — the requirement(s) that story rolls up to. Close handoff aggregates story outcomes per requirement: a requirement is `accepted` only if all its stories accepted; `rejected` if any rejected; `partial` if mixed. The SM Agent must populate `requirement_ids` per story during decomposition (this is required, not optional).

**Rationale:** Stories often span requirements (a foundation story serves several). 1:1 mapping is too narrow. Aggregation rule keeps PO Tool's accept/reject ergonomics simple at the requirement level.

---

**4. Force-close confirmation/reason.**

**Answer:** Required reason field, free-text string, non-empty. No separate confirmation prompt — the reason field is the confirmation. The reason is recorded in the force-close log entry and surfaces in the close handoff so PO Tool sees why.

**Rationale:** Suite convention from PO Tool: force-close is a distinct, logged action, not silent. The reason is the artifact that makes "force" auditable.

---

**5. SM Agent spawn synchronicity.**

**Answer:** Synchronous — the operator's terminal blocks until the agent returns. SM Tool does not need a "resume after agent returns" entry point in Iter 1.

**Rationale:** Matches PO Tool's call pattern. Async/resume adds complexity that earns nothing in single-user single-machine. Add only if the cost of waiting becomes a real bottleneck.

---

**6. Sprint-cut re-runnability.**

**Answer:** Re-runnable until the first story leaves `planned`. Once any story has transitioned to `in_progress`, the cut is locked and re-cut returns an error. Re-cut writes a new sprint-cut entry that supersedes the prior one (no rewrite of history; replay logic always uses the latest cut).

**Rationale:** Operator changes their mind during planning is normal; once execution starts, changing scope mid-flight breaks the close-and-flow contract.

---

**7. Test-pass gate — structured artifact or just Reviewer approval?**

**Answer:** Reviewer's logged approval entry by itself is sufficient evidence in Iter 1. The approval entry includes a free-text `test_result` field where the Reviewer cites which tests passed (verbatim or summary). Iter 2 may add a structured test-result artifact format if needed.

**Rationale:** Iter 1 simplicity wins. Reviewer is human-or-agent-with-judgment; their approval IS the trust boundary. Adding structured artifacts before knowing the failure modes earns nothing.

---

## Summary of locked decisions for v2

| # | Decision |
|---|---|
| 1 | Mid-iteration handoff revision: hard error |
| 2 | `rejected` is terminal; sprint can close with rejected stories |
| 3 | Stories carry `requirement_ids: list[str]`; close handoff aggregates per-requirement (accepted/rejected/partial) |
| 4 | Force-close requires non-empty reason; logged in entry + close handoff |
| 5 | SM Agent spawn synchronous; no resume entry point in Iter 1 |
| 6 | Sprint-cut re-runnable until first story leaves `planned`; locked thereafter |
| 7 | Reviewer approval entry with `test_result` text field is sufficient gate; no structured artifact in Iter 1 |
