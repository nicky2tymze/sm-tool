# sm-tool — Iteration 2 Customer Ask

The customer-side description of what `sm-tool` Iteration 2 needs to
do. Same voice and framing as Iter 1's Customer_Ask.md.

---

## Context

Iteration 1 shipped sm-tool's full pipeline as a contract — every
command exists, every state transition works, every log entry shape
is pinned. But the tool is **stub-runnable, not production-runnable**:
four agent-spawn surfaces raise `NotImplementedError("real agent
integration ships in Iter 2 — pass spawn_* for testing/manual ops")`,
and an operator running `python -m sm decompose` against a real
iteration today gets that error.

The tool also accumulated 11 retro items during Iter 1 — small polish
issues that didn't block shipping but are worth cleaning up before
real users run the tool against real iterations.

## The Ask

As an operator running the dev-suite pipeline, I want sm-tool to be
production-runnable end-to-end against real agents — without me
having to inject test stubs. Specifically:

**Real agent integration.** The four `NotImplementedError` surfaces
need real implementations:

1. `decompose` default `spawn_agent`: when called without an injected
   callable, spawns a real one-shot agent from the SM Agent role spec
   and the active iteration's requirements, returns structured story
   list JSON.
2. `execute` default `spawn_test_writer`: spawns a real Test Writer
   agent given the role spec + a story dict, returns test code as
   string.
3. `execute` default `spawn_coder`: spawns a real Coder agent given
   the role spec + story + test code, returns implementation code as
   string.
4. `execute` default `spawn_reviewer`: spawns a real Reviewer agent
   given the role spec + story + test code + impl code, returns
   `{approved: bool, test_result: str}`.

These should call out to an LLM provider. The cleanest interface is
the Anthropic Python SDK directly — read the role-spec file content,
build a single user message with the role spec + structured input
context, request structured output, parse and return. But the
architecture should remain agent-agnostic enough that a different
provider could be swapped in via configuration.

**API key handling.** Reading the API key from an environment variable
(`ANTHROPIC_API_KEY`) is fine. No hardcoding, no committed secrets.
If the env var is missing, the tool should give a clear actionable
error rather than crashing deep in the SDK.

**Cost discipline.** Default to a cost-effective model (Claude
Haiku 4.5 is the right default for sm-tool's per-spawn calls).
The operator should be able to override with an env var or
config value if a specific spawn needs a stronger model.

**Retro polish items from Iter 1.** Address the 11 items captured in
`iter1/Iter_1_Retro.md`:

1. `DecomposeAgentError` — defined but unused. Either delete or wire
   it into the agent-failure path of `decompose`.
2. `_TERMINAL_STATES` — constant defined in `derive_state` but unused.
   Delete (the transition graph already encodes terminality).
3. `SM_LOG_PATH` env var — currently read by production code as a test
   isolation lever. Either gate behind a `--log-path` CLI flag or
   rename to `SM_TEST_LOG_PATH` so production semantics are clearer.
4. `_HELP_TEXT` block — only documents `ingest` and exit codes 0-6.
   Refresh to cover all 12 subcommands and all exit codes 0-11.
5. `test_no_inline_entry_construction_in_sm_module` — currently checks
   only that `def build_entry` exists. Tighten to actually grep for
   inline entry construction across modules.
6. Story 3 docstring on `build_entry` overpromises "deep independence
   both directions" — implementation is shallow copy. Either implement
   deep-copy semantics or rewrite docstring to honestly describe
   shallow behavior.
7. Two log replays per ingest call (`derive_state` walks once,
   dup-id loop walks again) — consolidate into a single walk
   returning both `active_iteration` and `seen_iteration_ids` set.
8. `aggregate_requirements` has redundant `or []` and `or {}` clauses
   after `state.get(key, default)` — defaults already handle missing
   keys; clean up the belt-and-suspenders.
9. `execute` reject path has paranoid `try/except Exception: pass`
   around `record_review` — the branch is unreachable since
   `test_result.strip()` is truthy when entered. Remove.
10. `derive_state` could carry `iteration_goal` so `close_iteration`
    doesn't have to re-scan the log for the `iteration_open` entry.
    State-shape enrichment opportunity.
11. `_LIFECYCLE_TARGETS` dict — defined inside `_cli_main` so it
    rebuilds on every CLI invocation. Hoist to module-level constant.

## What I'm NOT asking for in Iter 2

- Async agent spawning. The Iter 1 contract is synchronous-blocks-
  terminal, and Iter 2 keeps that. If async ever lands, it's a
  separate iteration.
- Streaming output from agents. Single-shot request/response is
  fine. Even if the SDK supports streaming, the tool consumes the
  complete response as a string.
- Retry / circuit-breaker logic for failed agent calls. If a spawn
  fails, propagate the exception (or wrap as `DecomposeAgentError` /
  appropriate typed error). The operator decides whether to retry by
  re-invoking the command.
- Cost tracking / budget enforcement. Out of scope for Iter 2;
  operator monitors API costs via the Anthropic console.
- Multi-provider abstraction layer. Build for Anthropic SDK first;
  if multi-provider becomes needed, refactor in Iter 3.
- Caching of agent outputs. Each spawn is fresh. No prompt-cache,
  no result-cache.

## Validation gate (mirrors Iter 1 pattern)

Iter 2 ships when:

- All four `NotImplementedError` surfaces have real implementations
  that spawn agents via the Anthropic SDK and return correctly-typed
  results
- An operator can run `python -m sm decompose` and `python -m sm
  execute <story_id>` end-to-end against the real Anthropic API
  (given `ANTHROPIC_API_KEY` set) without injecting any stubs
- All 11 retro items closed
- Full test suite still 100% green (1680+ existing tests, plus
  whatever new tests Iter 2 stories add)

## Context the operator should know

- Anthropic Python SDK is added as a runtime dependency. `requirements.txt`
  (or `pyproject.toml` dependencies section) gets one entry: `anthropic`.
- Production tests will need to mock the SDK or use a fake agent
  callable. The existing injectable callable pattern from Story 9 +
  Story 23 makes this easy — tests pass stubs, production uses default.
- The four real-agent functions live alongside the existing
  injectable-callable interfaces; production just makes the default
  case call a real agent instead of raising NotImplementedError.
