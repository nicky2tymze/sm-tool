# sm-tool — Iter 2 Story Closures

Per-story sign-off log. Mirrors Iter 1's `iter1/Closures.md` pattern.

| # | Title | Size | Verdict | Push-backs | Notes |
|---|---|---|---|---|---|
| 1 | Anthropic SDK runtime dependency declared | S | APPROVED (orchestrator) | 1 cascade (Iter 1 stdlib-only posture invariant) | First-pass clean impl (16/16). Same pattern as Iter 1 Story 10/15 cascades — new validation (anthropic dep) exposed stale Iter 1 posture test. Behavior-preserving allowlist update: `test_pyproject_declares_no_runtime_dependencies` → `test_pyproject_declares_only_allowed_runtime_dependencies` with `_ALLOWED_RUNTIME_DEPS = {"anthropic"}`. Future deps require deliberate posture review (allowlist update). Full suite: 1696/1696. |
| 2 | API key resolution with actionable error | S | APPROVED (orchestrator) | 1 cascade (Iter 1 env-var posture) | First-pass clean impl (29/29). Public resolve_api_key() + MissingAPIKeyError(ValueError) + EXIT_AGENT_ERROR=12. Same cascade pattern as Story 1: introduced ANTHROPIC_API_KEY as second permitted env var, posture audit allowlist expanded to `_ALLOWED_ENV_VAR_READS = {"SM_LOG_PATH", "ANTHROPIC_API_KEY"}`. SDK not imported on missing-key failure path. Full suite: 1725/1725. |
