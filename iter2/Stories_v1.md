# sm-tool — Iter 2 Stories v1 (SM Agent decomposition)

Source: iter2/Requirements_v2.md (LOCKED)

## STORY BACKLOG (sequenced, foundation-first)

### Story 1: Anthropic SDK runtime dependency declared (size: S)

ROLLS UP TO: requirement #s [5]

AS AN operator, I WANT `anthropic` declared as a runtime dependency SO THAT a fresh install pulls it automatically with no extra step.

ACCEPTANCE (technical):
  - Adds a single `anthropic` entry to whichever dependency manifest the project already uses (`requirements.txt` or `pyproject.toml` dependencies section) — not both, no new manifest.
  - A clean `pip install` (or equivalent) of the project resolves and installs the `anthropic` package without manual operator intervention.
  - No version pin tighter than the SDK's published compatibility band; no extras flags introduced.
  - Existing dependency manifest hygiene preserved (sort order, comment style, trailing newline).

DEPENDS ON: none

### Story 2: API key resolution with actionable error (size: S)

ROLLS UP TO: requirement #s [6, 9]

AS AN operator, I WANT a single helper that reads `ANTHROPIC_API_KEY` and fails with a clear message SO THAT a missing key never bleeds an SDK stack trace.

ACCEPTANCE (technical):
  - Exposes an internal `resolve_api_key() -> str` that reads `os.environ["ANTHROPIC_API_KEY"]` and returns it on success.
  - On missing or empty-string env var, raises a typed `MissingAPIKeyError` (or equivalent) carrying a single human-readable message naming the env var and the remediation step; the SDK is not imported on this failure path.
  - The CLI dispatcher catches `MissingAPIKeyError` at the top level and exits with the dedicated agent-error exit code (`EXIT_AGENT_ERROR = 12` per LOCKED_DECISION 7), printing the message verbatim to stderr — no traceback.
  - Every real-agent spawn path (decompose, test_writer, coder, reviewer) routes API-key reads through this single helper; a grep for direct `os.environ` reads of `ANTHROPIC_API_KEY` outside this helper returns zero hits.

DEPENDS ON: 1

### Story 3: Model and max_tokens resolution with precedence (size: M)

ROLLS UP TO: requirement #s [7]

AS AN operator, I WANT a single resolver for per-spawn model and max_tokens with documented precedence SO THAT I can override any spawn's model or cap from env vars without code changes.

ACCEPTANCE (technical):
  - Exposes `resolve_model(role: str) -> str` returning the model id with precedence: per-spawn env var (`SM_DECOMPOSE_MODEL` / `SM_TEST_WRITER_MODEL` / `SM_CODER_MODEL` / `SM_REVIEWER_MODEL`) > `SM_MODEL` global > Claude Haiku 4.5 default.
  - Exposes `resolve_max_tokens(role: str) -> int` returning the cap with the same precedence pattern: per-spawn env var (`SM_DECOMPOSE_MAX_TOKENS` / `SM_TEST_WRITER_MAX_TOKENS` / `SM_CODER_MAX_TOKENS` / `SM_REVIEWER_MAX_TOKENS`) > `SM_MAX_TOKENS` global > `4096` default.
  - The Haiku 4.5 default is a single module-level constant pinning the exact SDK model identifier per ASSUMPTION 2.
  - Invalid integer values for any `*_MAX_TOKENS` env var raise a typed configuration error before any SDK call.
  - A unit-level test fixture can vary env vars per call and observe the resolved values; no spawn site reads model or max_tokens env vars directly.

DEPENDS ON: 1

### Story 4: JSON ask-and-parse helper with typed parse errors (size: M)

ROLLS UP TO: requirement #s [1, 4, 9]

AS AN operator, I WANT a shared helper that runs `json.loads` on agent output and raises a typed error on failure SO THAT both structured-output spawns (decompose, reviewer) share one parse path.

ACCEPTANCE (technical):
  - Exposes `parse_agent_json(raw: str, role: str) -> dict | list` that runs `json.loads` on the agent response text and returns the parsed object.
  - On `json.JSONDecodeError`, raises a typed parse error specific to the calling role: `DecomposeAgentError` for decompose (also closes retro item 1 per ASSUMPTION 8) and an appropriate typed error (e.g., `ReviewerAgentError` or shared parse-error class) for reviewer.
  - Typed parse errors carry the raw response snippet (truncated) and the role for operator debugging.
  - The CLI dispatcher maps every typed agent error to exit code `12` (`EXIT_AGENT_ERROR`).
  - A grep across the four spawn defaults finds exactly one call site invoking `json.loads` directly — this helper — and zero ad-hoc parse-and-raise blocks at spawn sites.

DEPENDS ON: 2

### Story 5: Provider seam — single Anthropic SDK invocation point (size: M)

ROLLS UP TO: requirement #s [8]

AS AN operator, I WANT the Anthropic SDK invoked from exactly one internal function SO THAT swapping providers in Iter 3 is a refactor, not a rewrite.

ACCEPTANCE (technical):
  - Exposes a single internal function (e.g., `_invoke_anthropic(messages: list, model: str, max_tokens: int, api_key: str) -> str`) that is the only call site in the codebase importing or invoking `anthropic.Anthropic` (or equivalent SDK client).
  - All four spawn defaults (decompose, test_writer, coder, reviewer) call through this function — they shape messages and parse responses, but never import `anthropic` directly.
  - Role-spec reading (from Iter 1 Story 8's `resolve_role_spec`) and message shaping live outside this function; the function is SDK-shaped, not role-shaped.
  - A grep for `import anthropic` or `from anthropic` in the codebase returns exactly one site.
  - Function is unit-testable by mocking the SDK client at this single boundary (per ASSUMPTION 6).

DEPENDS ON: 2, 3

### Story 6: Real `spawn_agent` default in `decompose` (size: L)

ROLLS UP TO: requirement #s [1, 9]

AS AN operator, I WANT `decompose`'s default `spawn_agent` to call a real one-shot SM Agent SO THAT running `python -m sm decompose` with no injected callable produces a real story list.

ACCEPTANCE (technical):
  - Replaces the `NotImplementedError` default in `decompose`'s `spawn_agent` parameter with a real implementation matching the existing injectable-callable signature exactly (per ASSUMPTION 1) — no signature drift, no downstream ripple.
  - Default reads `roles/sm_agent.md` via Iter 1 Story 8's `resolve_role_spec`, packages it plus the active iteration's requirement list into a single user message, calls the provider seam (Story 5) with `resolve_model("decompose")` and `resolve_max_tokens("decompose")`, then routes the response through `parse_agent_json(..., role="decompose")` (Story 4).
  - On `parse_agent_json` failure, raises `DecomposeAgentError` (caught by CLI → exit 12).
  - On SDK-level failure (network, auth, rate-limit), the exception is wrapped as `DecomposeAgentError` and propagated; no silent swallow, no auto-retry.
  - End-to-end (with mocked SDK): `python -m sm decompose` against a real active iteration with no injected callable returns a structured story list shaped per ASSUMPTION 3 and persists it through the existing pipeline contract unchanged.

DEPENDS ON: 4, 5

### Story 7: Real `spawn_test_writer` default in `execute` (size: M)

ROLLS UP TO: requirement #s [2, 9]

AS AN operator, I WANT `execute`'s default `spawn_test_writer` to call a real Test Writer agent SO THAT `execute <story_id>` produces test code with no injected stub.

ACCEPTANCE (technical):
  - Replaces the `NotImplementedError` default in `execute`'s `spawn_test_writer` parameter with a real implementation matching the existing injectable-callable signature exactly.
  - Default reads `roles/test_writer.md` via `resolve_role_spec`, packages it plus the story dict into a single user message, calls the provider seam with `resolve_model("test_writer")` and `resolve_max_tokens("test_writer")`, and returns the agent's response text as a string (no JSON parse — test_writer returns code, not structured output).
  - On SDK-level failure, the exception is wrapped as a typed agent error and propagated; no silent swallow.
  - The returned string is accepted unchanged by the downstream coder stage (no extra unwrapping at the call site).

DEPENDS ON: 5

### Story 8: Real `spawn_coder` default in `execute` (size: M)

ROLLS UP TO: requirement #s [3, 9]

AS AN operator, I WANT `execute`'s default `spawn_coder` to call a real Coder agent SO THAT `execute <story_id>` produces implementation code with no injected stub.

ACCEPTANCE (technical):
  - Replaces the `NotImplementedError` default in `execute`'s `spawn_coder` parameter with a real implementation matching the existing injectable-callable signature exactly.
  - Default reads `roles/coder.md` via `resolve_role_spec`, packages it plus the story and the test code from Story 7 into a single user message, calls the provider seam with `resolve_model("coder")` and `resolve_max_tokens("coder")`, and returns the agent's response text as a string.
  - On SDK-level failure, the exception is wrapped as a typed agent error and propagated; no silent swallow.
  - The returned string is accepted unchanged by the downstream reviewer stage.

DEPENDS ON: 5

### Story 9: Real `spawn_reviewer` default in `execute` (size: L)

ROLLS UP TO: requirement #s [4, 9]

AS AN operator, I WANT `execute`'s default `spawn_reviewer` to call a real Reviewer agent and return a typed dict SO THAT the existing accept/reject branch logic runs unchanged.

ACCEPTANCE (technical):
  - Replaces the `NotImplementedError` default in `execute`'s `spawn_reviewer` parameter with a real implementation matching the existing injectable-callable signature exactly.
  - Default reads `roles/reviewer.md` via `resolve_role_spec`, packages it plus the story, test code, and impl code into a single user message, calls the provider seam with `resolve_model("reviewer")` and `resolve_max_tokens("reviewer")`, and routes the response through `parse_agent_json(..., role="reviewer")` (Story 4).
  - Validates that the parsed object contains exactly the keys `approved` (bool) and `test_result` (str); shape violations raise the reviewer typed parse error (caught by CLI → exit 12).
  - On SDK-level failure or parse failure, the exception is wrapped as the reviewer typed error and propagated; the existing branch logic never receives a malformed dict.
  - End-to-end (with mocked SDK): `execute <story_id>` with no injected callables drives the accept/reject branch correctly from the real spawn's return.

DEPENDS ON: 4, 5

### Story 10: Retro polish — dead-code cleanup (size: S)

ROLLS UP TO: requirement #s [10]

AS AN operator, I WANT unused constants, redundant defaults, unreachable blocks, and per-call constant rebuilds removed SO THAT the codebase has no dead-code drag from Iter 1.

ACCEPTANCE (technical):
  - Deletes the unused `_TERMINAL_STATES` constant (retro item 2) and all import references; a grep returns zero hits.
  - Removes redundant `or []` / `or {}` clauses in `aggregate_requirements` (retro item 8) where inputs are already shape-guaranteed by upstream validation.
  - Deletes the unreachable `try/except Exception: pass` in `execute`'s reject path (retro item 9) and verifies via reachability analysis that no path reaches the removed block.
  - Hoists `_LIFECYCLE_TARGETS` to module scope (retro item 11) so it is constructed once at import, not rebuilt per CLI invocation.
  - Full existing test suite stays 100% green after the cleanup.

DEPENDS ON: none

### Story 11: Retro polish — single-pass ingest replay and state enrichment (size: M)

ROLLS UP TO: requirement #s [11]

AS AN operator, I WANT ingest to walk the log once and `derive_state` to carry `iteration_goal` SO THAT duplicate scans and close-time re-walks are gone.

ACCEPTANCE (technical):
  - Consolidates the two ingest-time log walks (`derive_state` walk + dup-id loop walk, retro item 7) into a single pass that returns both `active_iteration` and the `seen_iteration_ids` set; ingest consumes both from one call.
  - Enriches `derive_state` output to carry `iteration_goal` (retro item 10) populated from the `iteration_open` entry on replay.
  - `close_iteration` reads `iteration_goal` from the derived state object instead of re-scanning the log; a grep confirms `close_iteration` no longer iterates `read_entries()`.
  - Full existing test suite stays 100% green; performance characteristic (one walk per ingest) is verifiable by an entry-read counter in a test fixture.

DEPENDS ON: none

### Story 12: Retro polish — rename `SM_LOG_PATH` to `SM_TEST_LOG_PATH` (size: S)

ROLLS UP TO: requirement #s [12]

AS AN operator, I WANT the test-isolation env var renamed SO THAT production semantics are unambiguous and the lever's purpose is clear from its name.

ACCEPTANCE (technical):
  - Renames every read of `SM_LOG_PATH` → `SM_TEST_LOG_PATH` per LOCKED_DECISION 4 (retro item 3).
  - No production code path reads `SM_LOG_PATH` under the old name; a grep returns zero hits for the old name across the production codebase.
  - Every test that previously set `SM_LOG_PATH` now sets `SM_TEST_LOG_PATH`; suite stays 100% green.
  - The rename is mechanical — no behavioral change beyond the name.

DEPENDS ON: none

### Story 13: Retro polish — `_HELP_TEXT` refresh for all 12 subcommands and exit codes 0-12 (size: S)

ROLLS UP TO: requirement #s [12, 9]

AS AN operator, I WANT `--help` to cover every subcommand and every exit code SO THAT the surface is discoverable without reading source.

ACCEPTANCE (technical):
  - Updates `_HELP_TEXT` (retro item 4) to list all 12 subcommands with one-line descriptions each.
  - Documents exit codes 0 through 12 inclusive, with the new `EXIT_AGENT_ERROR = 12` covered explicitly (per LOCKED_DECISION 7).
  - Output is sorted/grouped logically (read-only commands, mutating commands, terminal commands) so an operator can scan it.
  - A test asserts that every subcommand registered with the CLI dispatcher is mentioned in `_HELP_TEXT` (catches future drift).

DEPENDS ON: 9

### Story 14: Retro polish — tightened structural test and honest `build_entry` docstring (size: S)

ROLLS UP TO: requirement #s [13]

AS AN operator, I WANT the inline-entry-construction test to actually scan modules and `build_entry`'s docstring to match its shallow-copy behavior SO THAT honesty in tests and docs is restored.

ACCEPTANCE (technical):
  - Replaces the existing `test_no_inline_entry_construction_in_sm_module` (retro item 5) with one that greps every module in the package for inline entry-dict construction patterns (e.g., literal dict assignment containing the reserved keys `id` / `type` / `timestamp` outside `build_entry`) and fails if any are found.
  - Test fails on a deliberately introduced inline construction (verified by a temporary canary in a single test run, then reverted) and passes on the current tree.
  - Rewrites `build_entry`'s docstring (retro item 6) to honestly describe shallow-copy semantics per LOCKED_DECISION 5; removes the "deep independence both directions" claim.
  - No implementation change to `build_entry` itself — shallow copy remains (no caller depends on deep-copy, verified during Iter 1 review).

DEPENDS ON: none

### Story 15: Test suite stays 100% green with mocked SDK (size: M)

ROLLS UP TO: requirement #s [14]

AS AN operator, I WANT every existing test plus new Iter 2 tests passing with no `ANTHROPIC_API_KEY` set SO THAT the suite is self-contained and CI never depends on a live API.

ACCEPTANCE (technical):
  - All 1680+ pre-existing tests pass after Iter 2 changes — verified by a single full-suite run with `ANTHROPIC_API_KEY` unset.
  - New tests added by Stories 2-9 mock the `anthropic` SDK client at the Story 5 provider-seam boundary (per ASSUMPTION 6) — zero tests make a real network call.
  - Tests that exercise the four real-agent defaults verify: API-key-missing path raises `MissingAPIKeyError`, model/max_tokens precedence honored, JSON parse errors typed correctly, SDK errors wrapped and propagated.
  - A test-time guard (fixture or pytest plugin) refuses to run if the suite detects a real `anthropic.Anthropic` client instantiation — fails loudly rather than billing the API.

DEPENDS ON: 6, 7, 8, 9

### Story 16: Cardiff end-to-end smoke run against real Anthropic API (size: M)

ROLLS UP TO: requirement #s [15]

AS AN operator, I WANT to run `decompose` and `execute` against the real API with no injection SO THAT Iter 2 ships only after live production-runnability is confirmed.

ACCEPTANCE (technical):
  - With `ANTHROPIC_API_KEY` set and no injected callables anywhere, `python -m sm decompose` against a real active iteration returns and persists a structured story list — operator-verified per ASSUMPTION 7.
  - With the same setup, `python -m sm execute <story_id>` runs Test Writer → Coder → Reviewer end-to-end on at least one story and drives it to a terminal state (`accepted` or `rejected`).
  - Smoke run is documented as a checklist in `iter2/` (or equivalent) capturing the iteration id used, the story id executed, the terminal state reached, and any deviation observed.
  - Smoke run is operator-executed once before sign-off, not part of the automated test suite (per ASSUMPTION 7).

DEPENDS ON: 6, 7, 8, 9, 15

## SPRINT-FIT NOTES

**Foundation cluster (Sprint 1 candidate, must not split):**
Stories 1, 2, 3, 4, 5 — the SDK dependency, API-key resolver, model/max_tokens resolver, JSON parse helper, and provider seam. Every real-agent story (6-9) depends on Story 5; Story 5 in turn depends on Stories 2 and 3. Splitting this cluster across sprints leaves no surface for the four spawn-default stories to build on. Story 5 is the second pinch-point of Iter 2 — the single SDK invocation point per LOCKED_DECISION (agent-agnostic seam).

**Real-agent cluster (Sprint 1-2 boundary, internal ordering flexible):**
Stories 6, 7, 8, 9 — one story per spawn default. Story 6 (decompose) and Story 9 (reviewer) are L because they additionally route through `parse_agent_json` and have shape validation; Stories 7 and 8 are M (string-return only). All four can ship in parallel work units once the foundation cluster is done. Story 6 is the first linchpin of Iter 2 — without it, `decompose` cannot run live. Do not split this cluster across more than two sprints; the four defaults are mechanically symmetric and reviewing them together catches drift.

**Retro polish cluster (any sprint, parallelizable):**
Stories 10, 11, 12, 14 have no dependencies on the real-agent work and can land in Sprint 1 alongside the foundation. Story 13 (`_HELP_TEXT` refresh) depends on Story 9's reviewer typed error being wired so the exit-code-12 documentation is accurate, so it should sit in Sprint 2 after Story 9. These five stories are all S except Story 11 (M for the single-pass refactor) and are intentionally small — they are mechanical retro debt, not new capability.

**Validation cluster (Sprint 2-3, must not split):**
Stories 15 and 16. Story 15 (suite 100% green with mocked SDK) gates Story 16 (live Cardiff smoke run). Story 16 is the Iter 2 release gate — no sign-off without it. Both depend on all four real-agent defaults (6-9). Story 16 is operator-executed, not automated — schedule it for the last work unit of Iter 2.

**Linchpin summary (the L stories):**
Story 6 (decompose real spawn), Story 9 (reviewer real spawn). Two L linchpins, both in the real-agent cluster. Iter 2 has fewer L stories than Iter 1 (six) because the scope is narrower — the foundation work is mostly mechanical wiring (S/M), not new architecture.

**Sequencing rationale:**
Foundation (1→5) → real-agent (6-9 in parallel) → validation (15→16). Retro polish (10, 11, 12, 14) can interleave anywhere in Sprint 1; Story 13 sits in Sprint 2 after Story 9. Sixteen stories from fifteen requirements is reasonable given the scope narrowing — each of the four spawn defaults is its own story, the seven foundation + retro items are atomic, and the two validation stories close the gate.

## OPEN_QUESTIONS_FOR_PO

none — v2 is locked, the seven LOCKED_DECISIONS and nine ASSUMPTIONS resolved every decomposition ambiguity encountered.
