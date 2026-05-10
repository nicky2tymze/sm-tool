# sm-tool — Requirements v2 (LOCKED)

## REQUIREMENTS (ranked by priority)

### 1. Iteration ingestion from PO Tool handoff (priority: MUST)

Customer's stated need: "Take a PO Tool handoff JSON path, parse and validate it, store the iteration's state in the SM Tool's own JSONL log."

What this means: The tool accepts a path to a PO-Tool-produced iteration-open handoff, validates its shape, and records the iteration as an open entry in `log.jsonl`. This is the entry point of every cycle. If an iteration is already open in the log, ingestion is a hard error — there is no mid-iteration revision path in Iter 1.

Acceptance: Given a valid PO Tool handoff JSON path with no iteration currently open, the tool records an iteration-open entry containing the parsed requirements in `log.jsonl`; ingestion of malformed handoffs, duplicate handoffs, or any handoff while an iteration is already open returns an error and writes no log entry.

### 2. Append-only JSONL log as single source of truth (priority: MUST)

Customer's stated need: "Append-only JSONL log — single source of truth, replay reconstructs all state."

What this means: All state changes (ingest, decomposition, sprint cut, story transitions, close) are append-only entries in `log.jsonl` at the package directory. Current state is always derivable by replaying the log; no separate state file.

Acceptance: Replaying `log.jsonl` from empty reconstructs the exact current state of the iteration, story backlog, sprint cut, and per-story status, with no entry ever mutated or deleted.

### 3. Content-oriented entry schema (priority: MUST)

Customer's stated need: "Content-oriented schema — entries carry content fields plus `id` / `type` / `timestamp`."

What this means: Every log entry conforms to the suite's content-oriented shape: stable `id`, `type` discriminator, `timestamp`, plus type-specific content fields. Same shape as `standup-tool` and `po-tool`.

Acceptance: Every entry written to `log.jsonl` has `id`, `type`, `timestamp`, and type-appropriate content fields, and a schema check across the log passes for every entry type the tool emits.

### 4. Single-active-iteration contract (priority: MUST)

Customer's stated need: "Multi-iteration concurrency. One iteration active at a time matches PO Tool's contract; mirror that."

What this means: At most one iteration is open at a time. Attempting to ingest any handoff (new or revised) while one is open is a hard error until the active iteration is closed (accepted or force-closed).

Acceptance: Ingesting a second iteration — including a revised handoff for the currently-open iteration — while one is open returns an error and writes no log entry; ingestion succeeds only when no iteration is open.

### 5. Story decomposition via SM Agent (priority: MUST)

Customer's stated need: "Drive an SM Agent (one-shot, spec-driven) to decompose the iteration's requirements into atomic, sequenced stories with technical-level acceptance criteria. Capture the output as a story backlog inside the iteration."

What this means: The tool spawns a one-shot SM Agent from a frozen role spec, feeds it the active iteration's requirements, and records the returned stories as a sequenced backlog in the log. Each story has technical-level acceptance criteria and a required `requirement_ids: list[str]` field naming the requirement(s) it rolls up to. The spawn is synchronous: the operator's terminal blocks until the agent returns; there is no resume entry point in Iter 1.

Acceptance: After decomposition runs synchronously, the log contains a sequenced story backlog tied to the active iteration, where each story has a stable id, ordinal sequence, technical-level acceptance criteria captured verbatim from the SM Agent output, and a non-empty `requirement_ids` list referencing requirement ids from the ingested handoff.

### 6. Sprint cut at a position (priority: MUST)

Customer's stated need: "Let me cut the story backlog at a position so stories 1..N are this sprint and N+1..end are deferred. The cut becomes a new entry in the log."

What this means: The operator specifies an integer cut position N. Stories 1..N become the active sprint; N+1..end are deferred. The cut is recorded as a single log entry referencing story ids on each side. The cut is re-runnable while every story is still in `planned`; once any story has transitioned to `in_progress`, the cut is locked. A successful re-cut writes a new sprint-cut entry that supersedes the prior — replay logic always uses the latest cut entry.

Acceptance: Given a cut position N within the backlog length and all in-sprint stories still in `planned`, a sprint-cut entry is appended naming the in-sprint and deferred story ids, supersedes any prior cut, and subsequent state queries treat only stories 1..N as the active sprint; once any story has left `planned`, a re-cut attempt returns an error and writes no entry.

### 7. Per-story lifecycle state tracking (priority: MUST)

Customer's stated need: "Every story has a lifecycle: planned → in_progress → in_review → accepted (or rejected). State transitions write append-only entries to the log."

What this means: Each story moves through a fixed state machine. Both `accepted` and `rejected` are terminal — a sprint can close with rejected stories, and SM Tool does not re-loop a rejected story within an iteration. Every transition is an append-only log entry. Illegal transitions (e.g., planned → accepted, or any transition out of a terminal state) are rejected.

Acceptance: For every in-sprint story, the tool enforces transitions only along planned → in_progress → in_review → accepted | rejected, treats `accepted` and `rejected` as terminal (no further transitions accepted), writes an entry per transition, and rejects any out-of-order or out-of-terminal transition without writing.

### 8. Status query reports current state of every story (priority: MUST)

Customer's stated need: "Status query reports current state of every story."

What this means: A read-only command derives current state from the log and reports, for the active iteration, every story's id, sequence, sprint membership (in-sprint vs deferred), and current lifecycle state.

Acceptance: Running the status command prints, for the active iteration, the current lifecycle state of every story and its sprint membership, derived purely by replaying `log.jsonl`.

### 9. Test-pass gate on story acceptance (priority: MUST)

Customer's stated need: "Tests are mandatory per story; no story is accepted without a Reviewer-approved test pass."

What this means: A story cannot reach the `accepted` state without a recorded Reviewer approval entry for that story. In Iter 1 the approval entry itself is sufficient evidence — it carries a non-empty free-text `test_result` field where the Reviewer cites which tests passed (verbatim or summary). No separate structured test-result artifact is required in Iter 1. Force-close is the only bypass and is logged distinctly.

Acceptance: Transitioning a story to `accepted` requires a logged Reviewer-approved entry for that story with a non-empty `test_result` field; absent that entry, only `rejected` or an explicit force-close path is permitted.

### 10. Iteration-close handoff for PO Tool (priority: MUST)

Customer's stated need: "When the sprint's stories are all accepted (or explicitly force-closed), produce an iteration-close handoff JSON that PO Tool can read to mark its requirements accepted/rejected upstream."

What this means: When every in-sprint story is in a terminal state (accepted, rejected, or force-closed), the tool emits a close handoff JSON that aggregates story outcomes back to PO Tool's requirement ids using each story's `requirement_ids`. A requirement is reported `accepted` only if all its stories are accepted; `rejected` if any of its stories are rejected (or force-closed as rejected); `partial` if its stories are mixed. The tool also records an iteration-close entry in the log. Mirrors PO Tool's close-and-flow shape.

Acceptance: When all in-sprint stories are terminal, the tool writes a close handoff JSON whose shape PO Tool can ingest, in which each ingested requirement is marked `accepted` / `rejected` / `partial` according to the per-story aggregation rule above, records an iteration-close entry in the log, and refuses to close while any in-sprint story is still non-terminal unless force-closed.

### 11. Force-close path for stuck iterations (priority: MUST)

Customer's stated need: "When the sprint's stories are all accepted (or explicitly force-closed)..."

What this means: The operator can explicitly force-close an iteration when stories cannot be cleanly accepted. Force-close requires a non-empty free-text `reason` field — the reason is the confirmation; there is no separate confirmation prompt. The reason is recorded in the force-close log entry and surfaced in the close handoff. Force-close is a distinct, logged action — not a silent acceptance — and still produces a valid close handoff.

Acceptance: A force-close command without a non-empty `reason` returns an error and writes nothing; with a valid reason, force-close terminates the active iteration with a distinct log entry containing the reason, marks remaining non-terminal stories as force-closed in the handoff, surfaces the reason in the close handoff JSON, and produces a close handoff PO Tool can ingest.

### 12. Role-spec-driven agent spawning (priority: MUST)

Customer's stated need: "Role-spec driven agents — every spawned agent runs from a frozen brief."

What this means: Every agent the tool spawns (SM Agent in Iter 1; TestWriter / Coder / Reviewer if execution lands) runs from a frozen role-spec file, not an inline prompt assembled at runtime. Same convention as the rest of the suite.

Acceptance: Every agent invocation references a frozen role-spec file by path, and the spec used is recorded in the log entry that captures the agent's output.

### 13. Close-and-flow cycle hygiene (priority: MUST)

Customer's stated need: "Close-and-flow — every cycle closes cleanly so the next can flow in."

What this means: After iteration-close, the tool returns to a clean, no-active-iteration state ready to ingest the next PO Tool handoff. No residual partial state blocks the next cycle.

Acceptance: After iteration-close, ingesting a new PO Tool handoff succeeds without any manual log cleanup, and status reports no active iteration between close and next ingest.

### 14. Per-story execution pipeline (TestWriter → Coder → Reviewer) (priority: NICE)

Customer's stated need: "Optionally drive the TestWriter → Coder → Reviewer pipeline for a story end-to-end without me having to spawn each agent by hand. If this slips to Iter 2 that's fine; the manual handoff still works."

What this means: A single command takes a story through TestWriter → Coder → Reviewer end-to-end, feeding each agent's output to the next, and records each handoff in the log. Manual lifecycle transitions remain a supported path either way.

Acceptance: When invoked on an in-sprint story, the execution command runs TestWriter, then Coder, then Reviewer in sequence, records each agent's output as log entries, and lands the story in `in_review` (or `accepted`/`rejected` per Reviewer outcome) — and if not implemented in Iter 1, the manual transition path still satisfies all other requirements.

### 15. Terminal-only operator interface (priority: SHOULD)

Customer's stated need: "A web UI. Terminal-only is fine."

What this means: All operator interaction is via terminal commands invoked from inside an active dev-suite session. No long-running service, no UI layer.

Acceptance: Every tool capability is reachable via a terminal command that runs to completion and exits, with no background process or UI dependency.

### 16. Single-user, single-machine, no auth (priority: SHOULD)

Customer's stated need: "Authentication / multi-user. Single-user, single-machine."

What this means: The tool assumes one operator on one machine. No auth, no multi-user coordination, no remote access.

Acceptance: The tool runs end-to-end with no authentication step and no networked coordination, against a local `log.jsonl` only.

### 17. JSONL-only persistence (no DB) (priority: SHOULD)

Customer's stated need: "Database backing. JSONL log only."

What this means: All persistence is the single `log.jsonl` file in the package directory. No database, no auxiliary state files.

Acceptance: The tool reads and writes only `log.jsonl` (plus the one emitted close-handoff JSON file at iteration close); no other persistent stores are created or required.

## LOCKED_DECISIONS

| # | Decision (from operator answers, condensed for the spec) |
|---|---|
| 1 | Mid-iteration handoff revision is a hard error: ingest while an iteration is open returns an error and writes nothing. (Affects req 1, 4.) |
| 2 | `rejected` is a terminal story state. A sprint can close with rejected stories; their outcome is carried up in the close handoff. SM Tool does not loop within an iteration. (Affects req 7, 10.) |
| 3 | Each story carries a required `requirement_ids: list[str]` populated by the SM Agent at decomposition. Close handoff aggregates per requirement: `accepted` only if all its stories accepted; `rejected` if any rejected; `partial` if mixed. (Affects req 5, 10.) |
| 4 | Force-close requires a non-empty free-text `reason` field; the reason is the confirmation (no separate prompt). The reason is recorded in the force-close log entry and surfaced in the close handoff. (Affects req 11.) |
| 5 | SM Agent spawn is synchronous — operator terminal blocks until the agent returns. No resume entry point in Iter 1. (Affects req 5.) |
| 6 | Sprint cut is re-runnable while every story is still in `planned`; once any story leaves `planned` (transitions to `in_progress`), the cut is locked. A successful re-cut writes a new sprint-cut entry that supersedes the prior; replay always uses the latest. (Affects req 6.) |
| 7 | Test-pass gate in Iter 1 is satisfied by the Reviewer's logged approval entry alone, which must include a non-empty free-text `test_result` field citing which tests passed. No structured test-result artifact required in Iter 1. (Affects req 9.) |

## ASSUMPTIONS (carried from v1, kept stable)

1. The package directory and `log.jsonl` location convention is identical to `po-tool` and `standup-tool`; no new path config is required.
2. Story ids are tool-assigned at decomposition time and stable for the iteration's lifetime; the SM Agent does not assign them.
3. The close handoff JSON is written to a path PO Tool already knows how to consume (mirrors PO Tool's own close handoff convention) — no new integration contract is required beyond schema agreement.
4. "Atomic, sequenced stories" means total ordering by integer sequence, not a DAG; sprint cut is a single integer position, which the customer's wording confirms.
5. Iter 1 ships without the per-story execution pipeline if it's tight; the manual lifecycle-transition commands are sufficient to call Iter 1 done.
6. Reviewer approval and test pass are recorded as a single combined artifact per story (the customer pairs them: "Reviewer-approved test pass"), not two independent gates.
7. The tool is invoked from inside an active dev-suite session, so environment, working directory, and agent-spawn machinery are already available — the tool does not bootstrap them.
