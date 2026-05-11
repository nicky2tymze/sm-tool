# sm-tool — Iter 2 Story Closures

Per-story sign-off log. Mirrors Iter 1's `iter1/Closures.md` pattern.

| # | Title | Size | Verdict | Push-backs | Notes |
|---|---|---|---|---|---|
| 1 | Anthropic SDK runtime dependency declared | S | APPROVED (orchestrator) | 1 cascade (Iter 1 stdlib-only posture invariant) | First-pass clean impl (16/16). Same pattern as Iter 1 Story 10/15 cascades — new validation (anthropic dep) exposed stale Iter 1 posture test. Behavior-preserving allowlist update: `test_pyproject_declares_no_runtime_dependencies` → `test_pyproject_declares_only_allowed_runtime_dependencies` with `_ALLOWED_RUNTIME_DEPS = {"anthropic"}`. Future deps require deliberate posture review (allowlist update). Full suite: 1696/1696. |
