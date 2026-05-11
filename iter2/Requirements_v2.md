# sm-tool — Iter 2 Requirements v2 (LOCKED)

## REQUIREMENTS (ranked by priority)

### 1. Real `spawn_agent` default in `decompose` (priority: MUST)

Customer's stated need: "`decompose` default `spawn_agent`: when called without an injected callable, spawns a real one-shot agent from the SM Agent role spec and the active iteration's requirements, returns structured story list JSON."

What this means: The current `NotImplementedError` default in `decompose` is replaced with a function that reads the SM Agent role spec from `roles/sm_agent.md` (per Iter 1 Story 8's `resolve_role_spec`), sends it plus the active iteration's requirements as a single user message to the Anthropic SDK, and returns a parsed story list. The agent is prompted to return JSON; the tool runs `json.loads` on the response, and a malformed response raises a typed error per requirement 9. The call defaults to Claude Haiku 4.5 (overridable per req 7) with `max_tokens=4096` (overridable via `SM_MAX_TOKENS` global or `SM_DECOMPOSE_MAX_TOKENS` per-spawn).

Acceptance: Running `python -m sm decompose` against a real active iteration with `ANTHROPIC_API_KEY` set and no injected `spawn_agent` produces a structured story list (parsed via ask-and-parse JSON) and persists it through the existing pipeline contract; a malformed agent response raises the typed parse error rather than crashing downstream.

### 2. Real `spawn_test_writer` default in `execute` (priority: MUST)

Customer's stated need: "`execute` default `spawn_test_writer`: spawns a real Test Writer agent given the role spec + a story dict, returns test code as string."

What this means: The current `NotImplementedError` default in `execute`'s test-writer surface is replaced with a real Anthropic SDK call that reads the Test Writer role spec from `roles/test_writer.md` (per Iter 1 Story 8's `resolve_role_spec`) and a story dict and returns test source code as a string. The call defaults to Claude Haiku 4.5 (overridable per req 7) with `max_tokens=4096` (overridable via `SM_MAX_TOKENS` global or `SM_TEST_WRITER_MAX_TOKENS` per-spawn).

Acceptance: Running `execute <story_id>` end-to-end with no injected `spawn_test_writer` and a valid `ANTHROPIC_API_KEY` produces a string of test code that is accepted by the downstream coder stage.

### 3. Real `spawn_coder` default in `execute` (priority: MUST)

Customer's stated need: "`execute` default `spawn_coder`: spawns a real Coder agent given the role spec + story + test code, returns implementation code as string."

What this means: The current `NotImplementedError` default in `execute`'s coder surface is replaced with a real Anthropic SDK call that reads the Coder role spec from `roles/coder.md` (per Iter 1 Story 8's `resolve_role_spec`), the story, and the test code, and returns implementation source as a string. The call defaults to Claude Haiku 4.5 (overridable per req 7) with `max_tokens=4096` (overridable via `SM_MAX_TOKENS` global or `SM_CODER_MAX_TOKENS` per-spawn).

Acceptance: Running `execute <story_id>` end-to-end with no injected `spawn_coder` and a valid `ANTHROPIC_API_KEY` produces a string of implementation code that is accepted by the downstream reviewer stage.

### 4. Real `spawn_reviewer` default in `execute` (priority: MUST)

Customer's stated need: "`execute` default `spawn_reviewer`: spawns a real Reviewer agent given the role spec + story + test code + impl code, returns `{approved: bool, test_result: str}`."

What this means: The current `NotImplementedError` default in `execute`'s reviewer surface is replaced with a real Anthropic SDK call that reads the Reviewer role spec from `roles/reviewer.md` (per Iter 1 Story 8's `resolve_role_spec`), story, test code, and impl code, and returns a dict with `approved` (bool) and `test_result` (str). The agent is prompted to return JSON; the tool runs `json.loads` on the response, and a malformed response raises a typed error per requirement 9. The call defaults to Claude Haiku 4.5 (overridable per req 7) with `max_tokens=4096` (overridable via `SM_MAX_TOKENS` global or `SM_REVIEWER_MAX_TOKENS` per-spawn).

Acceptance: Running `execute <story_id>` end-to-end with no injected `spawn_reviewer` and a valid `ANTHROPIC_API_KEY` produces a correctly-typed `{approved: bool, test_result: str}` dict (parsed via ask-and-parse JSON) that drives the existing accept/reject branch logic; a malformed agent response raises the typed parse error rather than corrupting branch logic.

### 5. Anthropic SDK runtime dependency (priority: MUST)

Customer's stated need: "Anthropic Python SDK is added as a runtime dependency. `requirements.txt` (or `pyproject.toml` dependencies section) gets one entry: `anthropic`."

What this means: The project declares `anthropic` as a runtime dependency in whichever dependency file the project already uses.

Acceptance: A fresh install of sm-tool pulls the `anthropic` package automatically without any extra operator step.

### 6. API key handling with actionable error (priority: MUST)

Customer's stated need: "Reading the API key from an environment variable (`ANTHROPIC_API_KEY`) is fine. No hardcoding, no committed secrets. If the env var is missing, the tool should give a clear actionable error rather than crashing deep in the SDK."

What this means: All four real-agent surfaces read the key from `ANTHROPIC_API_KEY`; if it is unset, the tool emits a clear, actionable error message (and a clean exit code) before any SDK call is attempted.

Acceptance: Running any real-agent-invoking command with `ANTHROPIC_API_KEY` unset produces a single human-readable error pointing at the missing env var, with no SDK stack trace bleeding through.

### 7. Default to cost-effective model with override (priority: MUST)

Customer's stated need: "Default to a cost-effective model (Claude Haiku 4.5 is the right default for sm-tool's per-spawn calls). The operator should be able to override with an env var or config value if a specific spawn needs a stronger model."

What this means: All four real-agent surfaces default to Claude Haiku 4.5. The operator may override the model via env vars in the following precedence order: per-spawn variable (`SM_DECOMPOSE_MODEL`, `SM_TEST_WRITER_MODEL`, `SM_CODER_MODEL`, `SM_REVIEWER_MODEL`) wins over the global `SM_MODEL` variable, which wins over the Haiku 4.5 default. If neither override is set, the default applies.

Acceptance: With no override set, every spawn uses Haiku 4.5; with only `SM_MODEL` set, every spawn uses the overridden model; with a per-spawn `SM_<ROLE>_MODEL` set, that spawn uses the per-spawn model and others fall back to `SM_MODEL` (or the default) — verifiable from request payload or log.

### 8. Agent-agnostic provider seam (priority: SHOULD)

Customer's stated need: "the architecture should remain agent-agnostic enough that a different provider could be swapped in via configuration."

What this means: The four spawn defaults are written behind a thin internal seam such that swapping in a different provider in Iter 3 is a refactor, not a rewrite — but no multi-provider abstraction layer is built in Iter 2.

Acceptance: A reviewer reading the four spawn defaults can identify a single point at which the Anthropic SDK is invoked, and the role-spec-reading / message-shaping logic is not entangled with SDK specifics.

### 9. Typed agent-failure propagation (priority: SHOULD)

Customer's stated need: "If a spawn fails, propagate the exception (or wrap as `DecomposeAgentError` / appropriate typed error). The operator decides whether to retry by re-invoking the command."

What this means: When a real agent call fails (network, auth, malformed response, JSON-parse failure on structured-output spawns), the failure is surfaced as a typed exception — using `DecomposeAgentError` in the decompose path (this also closes retro item 1) and an appropriate typed error in execute paths — and is not silently swallowed or auto-retried. Typed agent-failure errors exit with the dedicated exit code `EXIT_AGENT_ERROR = 12`.

Acceptance: A simulated SDK failure during `decompose` raises `DecomposeAgentError`; a simulated SDK failure during `execute` raises a typed error; a malformed-JSON response from `decompose` or `reviewer` raises the corresponding typed parse error; all three cases exit with code `12`.

### 10. Retro polish — dead-code cleanup (priority: SHOULD)

Customer's stated need (retro items 2, 8, 9, 11): unused `_TERMINAL_STATES` constant; redundant `or []` / `or {}` clauses in `aggregate_requirements`; unreachable `try/except Exception: pass` in `execute` reject path; `_LIFECYCLE_TARGETS` rebuilt on every CLI invocation.

What this means: Mechanical dead-code / cleanup pass — delete unused constants, remove belt-and-suspenders defaults, drop unreachable defensive blocks, and hoist module-level constants out of function bodies.

Acceptance: All four cleanup items are addressed in code, the full existing test suite stays green, and a static scan finds no remaining references to the deleted symbols.

### 11. Retro polish — log-replay consolidation and state enrichment (priority: SHOULD)

Customer's stated need (retro items 7, 10): two log replays per ingest call (`derive_state` walks once, dup-id loop walks again); `derive_state` could carry `iteration_goal` so `close_iteration` doesn't have to re-scan the log.

What this means: Consolidate the two ingest-time log walks into a single pass returning both `active_iteration` and the `seen_iteration_ids` set, and enrich `derive_state` output to carry `iteration_goal` so `close_iteration` does not re-scan.

Acceptance: Each `ingest` call walks the log exactly once; `close_iteration` reads `iteration_goal` from derived state instead of re-scanning the log; full test suite stays green.

### 12. Retro polish — env var and CLI surface clarity (priority: SHOULD)

Customer's stated need (retro items 3, 4): `SM_LOG_PATH` read by production code as a test-isolation lever; `_HELP_TEXT` only documents `ingest` and exit codes 0-6.

What this means: Rename `SM_LOG_PATH` → `SM_TEST_LOG_PATH` so production semantics are clear; production code no longer reads the old name. Refresh `_HELP_TEXT` to cover all 12 subcommands and exit codes 0-12 (inclusive of the new `EXIT_AGENT_ERROR = 12` from req 9).

Acceptance: An operator reading `--help` sees all 12 subcommands and exit codes 0-12; no production code path reads `SM_LOG_PATH` under its old name; tests that previously relied on `SM_LOG_PATH` set the renamed `SM_TEST_LOG_PATH` instead.

### 13. Retro polish — honesty in tests and docstrings (priority: SHOULD)

Customer's stated need (retro items 5, 6): `test_no_inline_entry_construction_in_sm_module` only checks that `def build_entry` exists; `build_entry` docstring claims "deep independence both directions" but implementation is shallow copy.

What this means: Tighten the structural test to actually grep for inline entry construction across modules. Keep `build_entry`'s existing shallow-copy implementation and rewrite the docstring to honestly describe shallow-copy behavior — no callers depend on deep-copy semantics (verified during Iter 1 review).

Acceptance: The tightened test fails if a future change reintroduces inline entry construction in any module; the `build_entry` docstring matches actual runtime semantics (shallow copy).

### 14. Full test suite stays 100% green (priority: MUST)

Customer's stated need: "Full test suite still 100% green (1680+ existing tests, plus whatever new tests Iter 2 stories add)."

What this means: All pre-existing tests continue to pass after Iter 2 changes, and any new tests added by Iter 2 stories also pass; production tests mock the SDK or use injected fake callables — they do not call real agents.

Acceptance: A single full-suite run with no `ANTHROPIC_API_KEY` set is 100% green.

### 15. End-to-end production-runnability validation (priority: MUST)

Customer's stated need: "An operator can run `python -m sm decompose` and `python -m sm execute <story_id>` end-to-end against the real Anthropic API (given `ANTHROPIC_API_KEY` set) without injecting any stubs."

What this means: A live smoke run against the real API exercises `decompose` and `execute` end-to-end without any stub injection.

Acceptance: With `ANTHROPIC_API_KEY` set, `python -m sm decompose` produces a story list and `python -m sm execute <story_id>` runs all three execute spawns through to a terminal state on at least one story.

## LOCKED_DECISIONS

| # | Decision | Affects requirement(s) |
|---|---|---|
| 1 | `SM_MODEL` global override + per-spawn `SM_DECOMPOSE_MODEL` / `SM_TEST_WRITER_MODEL` / `SM_CODER_MODEL` / `SM_REVIEWER_MODEL` overrides; per-spawn wins over global, global wins over the Claude Haiku 4.5 default | 7 (with knock-on into 1-4) |
| 2 | Ask-and-parse JSON for `decompose` story list and `reviewer` `{approved, test_result}`; malformed responses raise typed errors per req 9 | 1, 4, 9 |
| 3 | Role-spec files fixed at `roles/<role>.md` (`sm_agent.md`, `test_writer.md`, `coder.md`, `reviewer.md`) per Iter 1 Story 8's `resolve_role_spec`; no change in Iter 2 | 1, 2, 3, 4 |
| 4 | Rename `SM_LOG_PATH` → `SM_TEST_LOG_PATH`; production code no longer reads the old name | 12 |
| 5 | `build_entry` keeps shallow-copy implementation; rewrite docstring to honestly describe shallow behavior (no callers depend on deep-copy semantics) | 13 |
| 6 | `max_tokens=4096` cap per spawn; `SM_MAX_TOKENS` global override + `SM_DECOMPOSE_MAX_TOKENS` / `SM_TEST_WRITER_MAX_TOKENS` / `SM_CODER_MAX_TOKENS` / `SM_REVIEWER_MAX_TOKENS` per-spawn overrides (same precedence pattern as model overrides) | 1, 2, 3, 4 |
| 7 | New exit code `EXIT_AGENT_ERROR = 12` for typed agent-failure errors; `_HELP_TEXT` refresh in retro item 4 covers exit codes 0-12 | 9, 12 |

## ASSUMPTIONS MADE

1. The four real-agent surfaces preserve the existing injectable-callable signatures exactly — production simply replaces `raise NotImplementedError` with a real call; no signature changes ripple downstream.
2. "Claude Haiku 4.5" maps to the current Anthropic SDK model identifier for that release; the implementer will pin the exact string.
3. "Structured story list JSON" for `decompose` is parsed into the same shape the existing pipeline already consumes from stubs — no new schema work in Iter 2.
4. Role-spec files already exist on disk from Iter 1 (or are provided alongside Iter 2 work); reading them is a file-read, not an authoring task.
5. The "agent-agnostic enough to swap providers later" requirement is satisfied by a single internal function (or thin module) — no formal `Provider` interface or plugin registry is required in Iter 2.
6. Production tests will mock the `anthropic` client at the SDK boundary; no test in the suite makes a real network call.
7. The validation-gate smoke run in requirement 15 is operator-executed once before sign-off, not part of the automated test suite.
8. Retro item 1 (`DecomposeAgentError`) is consumed by requirement 9 (typed failure propagation) rather than left as a standalone cleanup, since wiring it into the agent-failure path is one of the two options the customer offered.
9. Grouping the 11 retro items into 4 requirements (10-13) plus folding item 1 into requirement 9 is the right granularity per the operator's guidance to group mechanically-related items.
