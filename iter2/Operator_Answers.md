# sm-tool — Iter 2 Operator Answers

PO v1's 7 open questions, answered for the v2 lock. Rationale
included so the SM Agent and downstream pipeline have judgment
context, not just rulings.

---

**1. Override env var name — `SM_MODEL` global or per-spawn?**

**Answer:** Both. `SM_MODEL` is a single global override applied to
all four spawns. Per-spawn overrides — `SM_DECOMPOSE_MODEL`,
`SM_TEST_WRITER_MODEL`, `SM_CODER_MODEL`, `SM_REVIEWER_MODEL` — take
precedence over `SM_MODEL` when both are set. If neither is set, the
default is Claude Haiku 4.5.

**Rationale:** Simple by default (one variable), flexible when
needed (per-spawn). Most operators will only ever set `SM_MODEL`;
the per-spawn overrides exist for the case where Reviewer needs a
stronger model than the other spawns.

---

**2. Structured-output strategy — JSON mode / tool use vs ask-and-parse?**

**Answer:** Ask-and-parse for Iter 2. Each spawn that needs
structured output (decompose's story list, reviewer's `{approved,
test_result}`) prompts the agent to return JSON, the tool runs
`json.loads`, and a malformed response raises a typed error per
requirement 9 (DecomposeOutputParseError / equivalent).

**Rationale:** Simpler implementation, fewer SDK-specific dependencies,
and the typed-error path is already established from Iter 1. If
malformed-output rates become a real problem post-Iter-2, Iter 3 can
move to tool-use enforcement.

---

**3. Role-spec file location — fixed or configurable?**

**Answer:** Fixed at `roles/<role>.md` relative to the package
directory (already established by Iter 1 Story 8's `resolve_role_spec`
function). No change in Iter 2. The four files — `sm_agent.md`,
`test_writer.md`, `coder.md`, `reviewer.md` — already exist in the
public repo from Iter 1.

**Rationale:** Already locked. No need to rebuild a working
convention.

---

**4. Retro item 3 — `--log-path` CLI flag vs `SM_TEST_LOG_PATH` rename?**

**Answer:** Rename env var to `SM_TEST_LOG_PATH`. Keep current usage
pattern unchanged (env var read at CLI dispatch time); just rename
to make test-isolation semantics explicit.

**Rationale:** Minimum surface change. Tests update their fixtures
to set the renamed env var; production code paths that don't set it
behave identically. CLI flag would be a new feature; rename is
cleanup.

---

**5. Retro item 6 — `build_entry` shallow vs deep copy?**

**Answer:** Rewrite docstring to honestly describe shallow-copy
behavior. No callers depend on deep-copy semantics (verified during
Iter 1 review).

**Rationale:** Lower risk. Implementing deep copy would change behavior
that no test currently pins; the cleaner fix is documentation
honesty.

---

**6. `max_tokens` cap per spawn — explicit limit or SDK default?**

**Answer:** Explicit cap at `4096` for all four spawns. Configurable
via `SM_MAX_TOKENS` env var if an operator needs a higher cap for a
specific spawn (matching the `SM_MODEL` override pattern — global
default, per-spawn override if needed via `SM_<ROLE>_MAX_TOKENS`).

**Rationale:** Cost discipline matters. 4096 tokens of output is
comfortably enough for a story decomposition list or a code review
verdict. Operators with longer outputs can override; the default
caps unbounded API spend.

---

**7. Exit code for typed agent-failure errors — reuse existing or new?**

**Answer:** New exit code: `EXIT_AGENT_ERROR = 12`. The `_HELP_TEXT`
refresh in retro item 4 covers exit codes 0-12.

**Rationale:** Distinct exit code makes scripting/automation cleaner —
an operator script can differentiate between "tool worked, story was
rejected" (EXIT_TRANSITION = 9) and "tool couldn't reach the agent"
(EXIT_AGENT_ERROR = 12). Reusing 1 ("unexpected / other") would lose
that distinction.

---

## Summary of locked decisions for v2

| # | Decision |
|---|---|
| 1 | `SM_MODEL` global + per-spawn `SM_<ROLE>_MODEL` overrides; Haiku 4.5 default |
| 2 | Ask-and-parse JSON; typed errors on malformed |
| 3 | Role-spec at `roles/<role>.md` (already established) |
| 4 | Rename `SM_LOG_PATH` → `SM_TEST_LOG_PATH` |
| 5 | `build_entry` keeps shallow copy; rewrite docstring to match |
| 6 | `max_tokens=4096` cap with `SM_MAX_TOKENS` global + per-spawn overrides |
| 7 | New `EXIT_AGENT_ERROR = 12`; help text covers 0-12 |
