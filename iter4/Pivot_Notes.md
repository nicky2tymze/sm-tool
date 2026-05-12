# sm-tool iter4-multisprint — Pivot Decision

**Decided:** 2026-05-11, ~10 minutes after iter4-multisprint dogfood
opened. Story 1 attempted via execute(); reviewer crashed on
JSON truncation; TestWriter and Coder outputs materialized but
fundamentally unusable (markdown-wrapped, conversational prose,
wrong path).

**Decision:** Pivot iter4-multisprint from "dogfood the multi-sprint
fix" to "orchestrator-driven implementation of the multi-sprint
fix; file dogfood findings for future dogfood-viability iteration."

## Empirical findings from the dogfood attempt

Story 1 of iter4-multisprint was attempted via `python -m sm execute`
against real Anthropic SDK. Pipeline ran 58 seconds, ~$0.20 in
spend. Findings:

**Finding A — TestWriter output is markdown-wrapped** (CRITICAL).
The materialized test file starts with `# Test Writer Output\n\n```python\n`
— markdown header + fenced code block, not raw Python. The inner
content (after fence-strip) is structurally reasonable (real
fixtures, real imports, real test patterns) but the file as
materialized isn't runnable.

**Finding B — Coder output is prose, not a file** (CRITICAL).
The materialized sm.py.candidate is 5.6KB of conversational
analysis ("Looking at this test file, I need to understand what
changes are required...") followed by code blocks containing
SNIPPETS, not the full file. Materializing this as `sm.py`
replaces 200KB of real code with 5KB of prose. The Coder isn't
producing a file at all — it's producing a *response* to "what
should I change?"

**Finding C — `project_root` resolution is wrong for tool
self-modification** (HIGH). Defaults to `LOG_PATH.parent`, which
for `iter4/iter4_log.jsonl` is `iter4/`. Files materialized at
`iter4/tests/test_<id>.py` and `iter4/sm.py` — wrong location.
The actual project root is `C:\Users\nickt\Desktop\sm-tool`.

**Finding D — Reviewer max_tokens=4096 too low** (HIGH —
already-known, req-3 was deferred from iter3-autonomy). Truncated
at column 884 mid-JSON-string, crashed with parse error.

**Finding E — No recovery path when Reviewer crashes**
(MEDIUM). execute() left story 1 in `in_review` with no clean
way to retry beyond force-close.

## What this means

The iter3-autonomy work (req-1 codebase context-passing + req-2
file materialization) shipped but is **structurally insufficient
for real-codebase dogfood**. The Cardiff smoke worked because the
work was greenfield + tiny output; the moment we try to modify
an existing 200KB sm.py, the gaps surface.

The full surface needed to make dogfood viable:
- Code-fence stripping for TestWriter + Coder outputs (parallel
  to Iter 2 Story 17's fence-stripper for JSON, but at the
  raw-text materialization layer)
- Coder output shape — full file vs diff vs snippet (architectural
  decision; affects role spec + materialization)
- `project_root` resolution (smarter default; explicit operator
  override)
- max_tokens budget (req-3 deferred — needs to land before
  dogfood is viable)
- Reviewer recovery semantics (retry on transient parse failure?
  fall back to operator? force-close?)

Per the just-codified R&D Sprint pattern: this dogfood attempt
was an R&D Sprint by nature (asked "is dogfood viable?" — answer
"no, here are the gaps") embedded inside a regular iteration.
Going forward, dogfood-viability exploration gets opened
EXPLICITLY as an R&D Sprint per
`FluxPlatform/Docs/Culture/RDSprint.txt`.

## What changes in iter4-multisprint

**iter4-multisprint v1 (closing now):** Force-close stories 1-6;
close iteration with pivot citation. Story 1's force-close
preserves the materialized files in `iter4/tests/` and `iter4/`
as evidence — the next iteration (whichever addresses dogfood
viability) can read them.

**iter4-multisprint v2 (next):** Same scope as v1 — relax
sprint-cut lock to allow multi-sprint per iteration. Run
**orchestrator-driven** (my Agent tool spawns TestWriter and
Coder subagents that have full codebase access; sm-tool's
execute() is NOT called). Use sm-tool's CLI lifecycle
(start/submit/record-review/accept) for tracking. Re-decompose
costs ~$0.02 — small enough to be worth the audit trail
consistency.

**Findings 5-9 (dogfood viability) flow into a future R&D Sprint
or regular iteration** (operator decision at iter4-multisprint v2
close).

## Versioning

No version bump on iter4-multisprint v1 close (no shippable code
produced). v0.5.0 still targets the full iter3-autonomy scope
plus dogfood viability — currently a ways off.
