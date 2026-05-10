# sm-tool — Stories v1 (SM Agent decomposition)

Source: iter1/Requirements_v2.md (LOCKED)

## STORY BACKLOG (sequenced, foundation-first)

### Story 1: Append-only JSONL log writer (size: M)

ROLLS UP TO: requirement #s [2, 17]

AS AN operator, I WANT a single append-only writer for `log.jsonl` SO THAT every state change lands as an immutable entry in one source-of-truth file.

ACCEPTANCE (technical):
  - Exposes a single internal `append(entry: dict) -> None` function that opens `log.jsonl` in append mode at the package directory, writes one JSON object per line, and flushes before return.
  - Refuses to mutate or rewrite any existing line; never opens the file in `w` or `r+` mode anywhere in the codebase.
  - Creates `log.jsonl` if it does not exist; never creates any auxiliary persistent files (no `.state`, no DB, no sidecars).
  - Writer is the only module in the package that opens `log.jsonl` for writing; a grep for write-mode opens of `log.jsonl` returns exactly one site.

DEPENDS ON: none

### Story 2: JSONL log reader and replay scanner (size: M)

ROLLS UP TO: requirement #s [2, 17]

AS AN operator, I WANT a reader that streams `log.jsonl` in order SO THAT every other component can derive state by replay without ever caching to a side file.

ACCEPTANCE (technical):
  - Exposes `read_entries() -> Iterable[dict]` that yields each line of `log.jsonl` parsed as a dict, in file order.
  - Returns an empty iterable when `log.jsonl` does not exist or is empty (no error).
  - Skips no entries; does not filter, sort, or rewrite — pure ordered read.
  - Malformed JSON on any line raises a structured error naming the line number; partial reads do not corrupt the iterator.

DEPENDS ON: 1

### Story 3: Content-oriented entry builder (size: L)

ROLLS UP TO: requirement #s [3]

AS AN operator, I WANT a single builder that stamps every entry with `id`, `type`, `timestamp` and accepts type-specific content fields SO THAT every entry conforms to the suite's content-oriented shape with no drift.

ACCEPTANCE (technical):
  - Exposes `build_entry(type: str, content: dict) -> dict` returning a new dict with auto-generated `id` (stable, unique, ULID-or-equivalent), ISO-8601 UTC `timestamp`, the given `type`, and all fields of `content` merged at the top level.
  - Rejects calls where `type` is empty or where `content` contains any of the reserved keys `id`, `type`, `timestamp` — raises a structured error.
  - Every code path that writes to `log.jsonl` routes through this builder; no module constructs entry dicts inline.
  - Entry-shape schema check (presence of `id`, `type`, `timestamp` plus type-appropriate content) passes for every entry the tool emits, verifiable by replaying the log and asserting on every entry.

DEPENDS ON: 1

### Story 4: State derivation by log replay (size: L)

ROLLS UP TO: requirement #s [2, 8]

AS AN operator, I WANT a replay function that reduces the log to current state SO THAT any command can derive the active iteration, story backlog, sprint cut, and per-story status without consulting a side store.

ACCEPTANCE (technical):
  - Exposes `derive_state() -> State` that consumes `read_entries()` once and returns a structured object containing: active iteration (or None), story backlog with sequence and `requirement_ids`, current sprint cut (or None), per-story lifecycle state, and close status.
  - Replay is pure: same log produces same state with no I/O side effects beyond reading `log.jsonl`.
  - Re-cut entries supersede prior cut entries — the latest sprint-cut entry wins.
  - Replaying an empty log yields `State` with no active iteration and empty backlog.
  - Replay rejects logs containing entries that violate the state machine (e.g., transition from a terminal state) by raising a structured error naming the offending entry id.

DEPENDS ON: 2, 3

### Story 5: Iteration ingestion command — happy path (size: L)

ROLLS UP TO: requirement #s [1, 3]

AS AN operator, I WANT to ingest a PO Tool handoff JSON by path SO THAT the iteration's requirements are recorded as an open iteration in the log.

ACCEPTANCE (technical):
  - Terminal command `ingest <path>` reads the file at `<path>`, parses it as JSON, validates against PO Tool's iteration-open handoff shape (top-level fields: iteration id, requirement list with stable ids), and on success writes a single `iteration_open` entry to the log via the entry builder.
  - The `iteration_open` entry's content carries the full parsed requirement list verbatim (each with its requirement id) so downstream decomposition has zero re-parsing.
  - Command exits 0 on success and prints the new iteration id; exits non-zero on any validation failure.
  - On any validation failure the command writes nothing to `log.jsonl`.

DEPENDS ON: 1, 3, 4

### Story 6: Ingestion validation — malformed and duplicate handoffs (size: M)

ROLLS UP TO: requirement #s [1]

AS AN operator, I WANT ingestion to reject malformed or duplicate handoffs SO THAT the log never holds a half-recorded iteration.

ACCEPTANCE (technical):
  - Ingestion of a non-existent path, a non-JSON file, or a JSON file missing required handoff fields returns a structured error naming the failing field and writes no log entry.
  - Ingestion of a handoff whose iteration id matches any prior `iteration_open` entry in the log returns a duplicate-handoff error and writes no log entry.
  - Each error class exits with a distinct, documented non-zero exit code.

DEPENDS ON: 5

### Story 7: Single-active-iteration enforcement on ingest (size: S)

ROLLS UP TO: requirement #s [4]

AS AN operator, I WANT ingestion to refuse any new handoff while an iteration is open SO THAT mid-iteration revisions cannot silently overwrite work.

ACCEPTANCE (technical):
  - When `derive_state()` reports an active iteration, `ingest <path>` returns a structured "iteration already open" error and writes nothing — regardless of whether the new handoff is a fresh iteration or a revised handoff for the active one.
  - The error message names the currently-open iteration id and instructs the operator to close before re-ingesting.
  - Ingestion succeeds only when `derive_state()` reports no active iteration.

DEPENDS ON: 4, 5

### Story 8: Frozen role-spec resolver and recording (size: M)

ROLLS UP TO: requirement #s [12]

AS AN operator, I WANT every spawned agent to load its brief from a frozen spec file SO THAT no inline runtime prompt assembly creeps in and every spawn is auditable from the log.

ACCEPTANCE (technical):
  - Exposes `resolve_role_spec(role: str) -> Path` returning an absolute path to a checked-in role-spec file (e.g., `roles/sm_agent.md`); raises a structured error if the file is missing.
  - Every agent-spawn code path calls `resolve_role_spec` and passes the file by path to the agent runner — no module concatenates a prompt string inline before spawn.
  - The log entry that captures any agent's output records the absolute path of the role-spec used and a content hash of the spec at spawn time.
  - A grep for inline prompt assembly (string concatenation of role text) at agent-spawn sites returns zero hits.

DEPENDS ON: 3

### Story 9: SM Agent spawn — synchronous decomposition driver (size: L)

ROLLS UP TO: requirement #s [5, 12]

AS AN operator, I WANT a command that spawns the SM Agent synchronously against the active iteration's requirements SO THAT decomposition output lands in the log as a sequenced story backlog.

ACCEPTANCE (technical):
  - Terminal command `decompose` reads the active iteration from `derive_state()`, fails fast if no iteration is open, and otherwise spawns a one-shot SM Agent using the resolved role-spec, passing the requirement list as input.
  - The spawn blocks the operator's terminal until the agent process returns; no background tasks, no resume token.
  - On agent return, the tool parses the agent's structured output (story list with sequence, technical acceptance criteria, `requirement_ids: list[str]`).
  - Tool assigns each story a stable `story_id` (not the agent's job per Assumption 2) and writes a single `story_backlog` entry to the log containing the ordered story list with assigned ids and the role-spec path/hash from Story 8.
  - On agent failure or output-parse failure, the command exits non-zero and writes no `story_backlog` entry.

DEPENDS ON: 4, 8

### Story 10: SM Agent output validation — requirement_ids and acceptance (size: M)

ROLLS UP TO: requirement #s [5]

AS AN operator, I WANT decomposition output to fail fast if any story is missing its `requirement_ids` or technical acceptance SO THAT the close handoff aggregation can never silently lose a requirement.

ACCEPTANCE (technical):
  - After parsing SM Agent output and before writing the `story_backlog` entry, the tool validates: every story has a non-empty `requirement_ids` list whose every element matches a requirement id from the active iteration; every story has a non-empty technical acceptance criteria field captured verbatim from the agent.
  - Validation failure returns a structured error naming the offending story title (or sequence) and the missing field; writes no log entry.
  - Stories whose `requirement_ids` reference an unknown requirement id are rejected with a distinct error class.

DEPENDS ON: 9

### Story 11: Sprint cut command at position N (size: M)

ROLLS UP TO: requirement #s [6]

AS AN operator, I WANT to cut the backlog at integer position N SO THAT stories 1..N are the active sprint and N+1..end are deferred.

ACCEPTANCE (technical):
  - Terminal command `sprint-cut <N>` reads the active iteration's story backlog from `derive_state()`, validates 1 <= N <= backlog length, and on success writes a `sprint_cut` entry naming the in-sprint story ids (1..N) and deferred story ids (N+1..end).
  - Replay logic always treats the latest `sprint_cut` entry as authoritative — earlier cut entries are superseded.
  - With no active iteration or no story backlog yet, the command exits non-zero and writes nothing.
  - N out of range (zero, negative, or greater than backlog length) returns a structured error and writes nothing.

DEPENDS ON: 4, 10

### Story 12: Sprint-cut re-run lock once any story leaves planned (size: S)

ROLLS UP TO: requirement #s [6]

AS AN operator, I WANT re-cut to be allowed only while every story is still in `planned` SO THAT in-flight work cannot be silently re-scoped out of a sprint.

ACCEPTANCE (technical):
  - `sprint-cut <N>` checks per-story lifecycle state from `derive_state()`; if any in-sprint story has transitioned out of `planned` (i.e., to `in_progress`, `in_review`, `accepted`, `rejected`, or force-closed), the command returns a "sprint cut locked" error and writes nothing.
  - With every story still `planned`, a successful re-cut writes a new `sprint_cut` entry that supersedes the prior on replay.
  - The lock check uses the same replay-derived state — no separate flag is persisted.

DEPENDS ON: 11, 13

### Story 13: Per-story lifecycle state machine and transition writer (size: L)

ROLLS UP TO: requirement #s [7]

AS AN operator, I WANT each story to advance through planned → in_progress → in_review → accepted | rejected with strict enforcement SO THAT illegal transitions can never be recorded.

ACCEPTANCE (technical):
  - Exposes a transition function that, given a story id and target state, validates the transition against the allowed graph (planned→in_progress, in_progress→in_review, in_review→accepted, in_review→rejected) and writes a single `story_transition` entry on success.
  - `accepted` and `rejected` are terminal: any transition out of either is rejected and writes nothing.
  - Skipping states (e.g., planned→accepted, planned→in_review) is rejected with a structured error naming the current state and the requested target.
  - Transition attempts on a story id outside the active sprint or outside the active iteration are rejected.
  - The transition writer is the only code path that emits `story_transition` entries.

DEPENDS ON: 4, 11

### Story 14: Terminal commands for manual lifecycle transitions (size: M)

ROLLS UP TO: requirement #s [7, 15]

AS AN operator, I WANT terminal commands to start, submit-for-review, accept, and reject a story SO THAT I can drive the state machine end-to-end without touching the log directly.

ACCEPTANCE (technical):
  - Commands `start <story_id>`, `submit <story_id>`, `accept <story_id>`, `reject <story_id>` each route through the Story 13 transition function with the appropriate target state.
  - Each command exits 0 on a successful transition and non-zero on an illegal transition, surfacing the structured error from the state machine.
  - Each command runs to completion and exits — no background process, no UI, no persistent service.

DEPENDS ON: 13

### Story 15: Reviewer approval entry with non-empty test_result (size: M)

ROLLS UP TO: requirement #s [9]

AS AN operator, I WANT story acceptance to require a logged Reviewer approval entry carrying a non-empty `test_result` SO THAT the test-pass gate is visible in the log itself.

ACCEPTANCE (technical):
  - Exposes a `record-review <story_id>` command (or accept-time argument) that writes a `reviewer_approval` entry containing `story_id`, `approved: bool`, and a free-text `test_result` field.
  - `accept <story_id>` from Story 14 fails if no `reviewer_approval` entry exists for that story id with `approved: true` and a non-empty `test_result` — error names the missing prerequisite, writes nothing.
  - An approval entry with empty or whitespace-only `test_result` is rejected at write time and not appended.
  - Replay correctly associates the latest reviewer-approval entry per story id.

DEPENDS ON: 13, 14

### Story 16: Status query command (size: M)

ROLLS UP TO: requirement #s [8, 15]

AS AN operator, I WANT a `status` command that prints the active iteration's full state SO THAT I can see every story's sequence, sprint membership, and lifecycle state at a glance.

ACCEPTANCE (technical):
  - Terminal command `status` calls `derive_state()` and prints, for the active iteration: iteration id, every story's id, sequence, in-sprint vs deferred membership, and current lifecycle state.
  - With no active iteration, prints "no active iteration" and exits 0.
  - Output is read-only — `status` writes nothing to `log.jsonl`.
  - Output ordering is by story sequence ascending.

DEPENDS ON: 4

### Story 17: Per-requirement aggregation rule (size: M)

ROLLS UP TO: requirement #s [10]

AS AN operator, I WANT a deterministic aggregation function that maps story outcomes back to requirement outcomes SO THAT the close handoff faithfully reports each PO Tool requirement as accepted, rejected, or partial.

ACCEPTANCE (technical):
  - Exposes `aggregate_requirements(state: State) -> dict[requirement_id, status]` where status is one of `accepted` | `rejected` | `partial`.
  - For each requirement id appearing in any story's `requirement_ids`: status is `accepted` only if every story rolling up to it is `accepted`; `rejected` if any story rolling up to it is `rejected` or force-closed-as-rejected; `partial` if its stories are mixed across terminal states without triggering the rejected rule.
  - Requirements with zero stories rolling up to them (should not occur given Story 10 validation) raise a structured error.
  - Function is pure — no I/O, no log writes; called by close handoff producer only.

DEPENDS ON: 4, 10

### Story 18: Iteration-close handoff producer (size: L)

ROLLS UP TO: requirement #s [10, 13, 17]

AS AN operator, I WANT a `close` command that emits the iteration-close handoff JSON SO THAT PO Tool can ingest it and mark its requirements upstream.

ACCEPTANCE (technical):
  - Terminal command `close` runs only when every in-sprint story is in a terminal state (accepted, rejected, or force-closed); otherwise returns a structured error naming the non-terminal stories and writes nothing.
  - On success, calls `aggregate_requirements()` and writes a close handoff JSON file to the path PO Tool already consumes (per Assumption 3) containing iteration id, per-requirement status, and per-story outcome list.
  - Writes a single `iteration_close` entry to `log.jsonl` referencing the handoff file path and the per-requirement aggregation result.
  - The close handoff JSON file is the only persistent file the tool writes besides `log.jsonl`.

DEPENDS ON: 4, 13, 15, 17

### Story 19: Force-close command with required reason (size: M)

ROLLS UP TO: requirement #s [11]

AS AN operator, I WANT a `force-close --reason <text>` command SO THAT a stuck iteration can terminate with a logged reason and still produce a valid close handoff.

ACCEPTANCE (technical):
  - Terminal command `force-close --reason <text>` requires a non-empty (after-strip) `reason`; missing or empty reason returns a structured error and writes nothing.
  - On success, writes a single `force_close` log entry containing the reason verbatim, then proceeds through the close-handoff producer (Story 18) — non-terminal stories are marked as force-closed for handoff purposes.
  - The close handoff JSON surfaces the force-close reason at the iteration level and marks force-closed stories distinctly from accepted/rejected.
  - Force-close runs without requiring all stories to be terminal — that is its purpose.

DEPENDS ON: 18

### Story 20: Close-and-flow clean state for next ingest (size: S)

ROLLS UP TO: requirement #s [13]

AS AN operator, I WANT post-close state to be clean SO THAT the next PO Tool handoff ingests with no manual log surgery.

ACCEPTANCE (technical):
  - After a successful `close` or `force-close`, `derive_state()` reports no active iteration.
  - `ingest <path>` immediately succeeds against a fresh, valid handoff after close — no manual cleanup, no flag reset.
  - `status` between close and next ingest prints "no active iteration".
  - The clean-state property is verified by an end-to-end replay: ingest → decompose → cut → transitions → close → ingest succeeds without intervention.

DEPENDS ON: 5, 18, 19

### Story 21: Single-user / no-auth / no-network posture (size: S)

ROLLS UP TO: requirement #s [16]

AS AN operator, I WANT the tool to assume one operator on one machine SO THAT no auth or networked coordination ever blocks a local cycle.

ACCEPTANCE (technical):
  - No code path opens a network socket, no module imports an auth library, no command requires credentials.
  - Every command runs end-to-end against a local `log.jsonl` only — verifiable by static dependency audit and by running with the network disabled.
  - No module reads any environment variable for auth/identity beyond the standard suite-session conventions (Assumption 7).

DEPENDS ON: none

### Story 22: JSONL-only persistence audit (size: S)

ROLLS UP TO: requirement #s [17]

AS AN operator, I WANT a guarantee that the tool persists nothing besides `log.jsonl` and the close handoff JSON SO THAT the single-source-of-truth contract holds.

ACCEPTANCE (technical):
  - End-to-end run from ingest through close produces exactly two file artifacts in the package directory: `log.jsonl` (appended throughout) and the one close handoff JSON written at close.
  - No SQLite, no `.state`, no `.cache`, no sidecar JSON appears at any point — verifiable by directory listing before/after each command.
  - Static audit confirms only the writer module (Story 1) and the close handoff producer (Story 18) write to disk.

DEPENDS ON: 1, 18

### Story 23: TestWriter → Coder → Reviewer execution pipeline (NICE) (size: L)

ROLLS UP TO: requirement #s [14, 12, 7]

AS AN operator, I WANT a single `execute <story_id>` command that runs TestWriter → Coder → Reviewer and lands the story in `in_review` (or terminal per Reviewer outcome) SO THAT I don't spawn each agent by hand.

ACCEPTANCE (technical):
  - Terminal command `execute <story_id>` validates that the story is in-sprint and currently `planned` or `in_progress`, then spawns TestWriter from its frozen role-spec, then Coder from its frozen role-spec, then Reviewer from its frozen role-spec — feeding each agent's output to the next.
  - Each agent's output is captured as its own log entry (`testwriter_output`, `coder_output`, `reviewer_approval`) with role-spec path/hash recorded.
  - On Reviewer approval with non-empty `test_result`, the command transitions the story to `in_review` and (per spec) on to `accepted`; on Reviewer rejection, transitions to `rejected`.
  - If this story slips to Iter 2, Stories 14 and 15 still satisfy the manual-transition path — no other story depends on Story 23.

DEPENDS ON: 8, 13, 14, 15

## SPRINT-FIT NOTES

**Foundation cluster (Sprint 1 candidate, must not split):**
Stories 1, 2, 3, 4 — the log writer, reader, entry builder, and replay-state derivation. Every other story depends on at least one of these. Splitting this cluster across sprints leaves the next sprint with no surface to build on. Story 3 is intentionally L — the entry builder is the single pinch-point through which every later story routes.

**Ingestion cluster (Sprint 1–2 boundary):**
Stories 5, 6, 7 form one capability slice. Story 5 (L) is the linchpin. Story 7 depends on Story 4's state derivation, so the foundation cluster must be done first. Do not split 5 from 6 — a half-validating ingest is worse than no ingest.

**Decomposition cluster (Sprint 2):**
Stories 8, 9, 10. Story 8 (role-spec resolver) is shared infrastructure for any future agent spawn — pull it in alongside Story 9 even though Story 23 also depends on it. Story 9 (L) is the second linchpin. Stories 9 and 10 should not split: shipping decomposition without `requirement_ids` validation breaks the close-handoff aggregation contract downstream.

**Sprint-cut + lifecycle cluster (Sprint 2–3):**
Stories 11, 12, 13, 14, 15. Story 13 (L) is the state machine — pulls in alongside the manual-transition commands (14) and the reviewer-approval gate (15). Story 12 (re-cut lock) depends on Story 13's state graph being live. Avoid shipping 13 without 14 — a state machine no one can drive is dead weight.

**Status (Sprint 2 or 3):**
Story 16 is small, depends only on Story 4, and is high-leverage for operator confidence. Pull it in as soon as Story 4 lands.

**Close cluster (Sprint 3, must not split):**
Stories 17, 18, 19, 20. Story 18 (L) is the third linchpin. Story 17 (aggregation) is pure logic and feeds 18. Story 19 (force-close) reuses 18's handoff producer. Story 20 (clean state) closes the cycle and is the integration test for everything before it. Do not split 17 from 18 — aggregation without a producer has no consumer.

**Posture audit (any sprint):**
Stories 21 and 22 are small, near-foundational, and act as standing assertions. Story 21 has no dependencies and can be done in Sprint 1. Story 22 is verified at close, so it sits in Sprint 3 alongside Story 18.

**NICE / deferrable (Iter 2 candidate):**
Story 23 is the NICE-priority requirement (#14). Per Assumption 5, Iter 1 ships without it if tight. Manual lifecycle (Story 14) plus reviewer approval (Story 15) keep the cycle whole. Do not pull Story 23 into Iter 1 unless every MUST story above is solidly fitting.

**Linchpin summary (the L stories):**
Story 3 (entry builder), Story 4 (replay state), Story 5 (ingest), Story 9 (decomposition spawn), Story 13 (lifecycle state machine), Story 18 (close handoff), Story 23 (execution pipeline, NICE). Six MUST linchpins map cleanly to three sprints at two L stories per sprint.

## OPEN_QUESTIONS_FOR_PO

none — v2 is locked and the seven LOCKED_DECISIONS plus seven ASSUMPTIONS resolved every decomposition ambiguity encountered.
