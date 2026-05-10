# sm-tool — Iter 1 Story Closures

Per-story sign-off log. Each row is a closed story with the Reviewer's
verdict and any push-back fires that landed during the build.

| # | Title | Size | Verdict | Push-backs | Notes |
|---|---|---|---|---|---|
| 1 | Append-only JSONL log writer | M | APPROVED | 1 (TW: Python 3.13+ API in test, orchestrator-fixed) | Cosmetic: `import json` vs `import json as _json` — flagged for retro, not blocking |
| 2 | JSONL log reader and replay scanner | M | APPROVED | 0 | First-pass clean; spec-mandated stricter contract than po/standup `_read_entries` (raises on malformed vs warns) — design intent, not drift |
| 3 | Content-oriented entry builder | **L** | APPROVED | 0 | First-pass clean L story. Two non-blocking retro notes: `test_no_inline_entry_construction` is weaker than name (will tighten when more writers exist), docstring "deep independence" overpromises (impl is shallow). |
| 4 | State derivation by log replay | **L** | APPROVED | 0 | Second L first-pass clean. Pattern confirmed (not luck): TestWriter pins firmly, Coder maps 1:1, no rework. One micro-retro note: `_TERMINAL_STATES` constant unused (transition table encodes terminality via empty frozensets). |
| 5 | Iteration ingestion command — happy path | **L** | APPROVED | 0 | Third L first-pass clean. Three Ls in 5 stories, all clean — same pattern PO Tool ran. CLI surface via `python -m sm ingest`. Sprint 2 retro item: SM_LOG_PATH env var is contract-driven (test isolation) but not gated behind `--log-path` flag — long-term, gate or rename. |
| 6 | Ingestion validation — malformed and duplicate handoffs | M | APPROVED | 0 | First-pass clean. 4 typed exception classes (all ValueError subclasses for back-compat), distinct CLI exit codes 0-6 mapped from exception type, dup-id check fires for ANY prior iteration_open and runs before single-active check. README + --help both document the exit-code table. |
| 7 | Single-active-iteration enforcement on ingest | S | APPROVED | 0 | First-pass clean. Inverted Story 6's check ordering: single-active now fires BEFORE dup-id when both would match (more actionable error: "close before re-ingesting" beats "id already used"). IngestActiveError message contains both id and "close" substring. Both regimes coexist correctly. |
| 8 | Frozen role-spec resolver + role-spec files | M | APPROVED | 0 | First-pass clean. Created roles/{sm_agent,test_writer,coder,reviewer}.md (41-43 lines each, public-facing, ROLE/LANE/ANTI-LANE/OUTPUT FORMAT markers, no culture leakage). Public resolve_role_spec, private SHA-256 _role_spec_hash, RoleSpecNotFoundError as FileNotFoundError subclass. Grep invariant holds: no inline prompt assembly in sm.py. |
