# sm-tool — Customer Ask

The customer-side description of what `sm-tool` Iteration 1 needs to
do. This is the input that requirements decomposition consumes.
Voice and framing match the customer ask that drove `po-tool` v0.2.

---

## The Ask

As an operator running the dev-suite pipeline, I want a software tool
that takes over the scrum-master work I currently do manually between
iteration-open and per-story execution.

Today, when `po-tool` produces an iteration-open handoff JSON, I have
to:

1. Read the handoff JSON by hand.
2. Spawn a one-shot SM agent with the role spec, paste in the
   requirements, parse the output stories.
3. Decide a sprint cut (which stories go in this sprint, which slip).
4. For each story in the sprint, spawn a TestWriter, then a Coder,
   then a Reviewer — manually feeding outputs to the next.
5. Track which stories are open, in progress, accepted, or rejected.
6. At the end, write an iteration-close handoff that `po-tool` can
   consume so requirements get marked accepted/rejected upstream.

I want a tool that owns this whole middle. Specifically:

**Iteration ingestion.** Take a PO Tool handoff JSON path, parse and
validate it, store the iteration's state in the SM Tool's own JSONL
log.

**Story decomposition.** Drive an SM Agent (one-shot, spec-driven)
to decompose the iteration's requirements into atomic, sequenced
stories with technical-level acceptance criteria. Capture the output
as a story backlog inside the iteration.

**Sprint cut.** Let me cut the story backlog at a position so stories
1..N are this sprint and N+1..end are deferred. The cut becomes a
new entry in the log.

**Per-story state tracking.** Every story has a lifecycle: planned →
in_progress → in_review → accepted (or rejected). State transitions
write append-only entries to the log. Status query reports current
state of every story.

**Per-story execution (Iter 1 nice-to-have, not must).** Optionally
drive the TestWriter → Coder → Reviewer pipeline for a story end-to-end
without me having to spawn each agent by hand. If this slips to Iter 2
that's fine; the manual handoff still works.

**Iteration close.** When the sprint's stories are all accepted (or
explicitly force-closed), produce an iteration-close handoff JSON that
PO Tool can read to mark its requirements accepted/rejected upstream.
Mirrors the close-and-flow shape that PO Tool established.

## Same shape as the suite

This tool follows the same patterns that produced `standup-tool` and
`po-tool`:

- **Append-only JSONL log** — single source of truth, replay
  reconstructs all state.
- **Content-oriented schema** — entries carry content fields
  (story title, requirement id, status, etc.) plus the always-present
  `id` / `type` / `timestamp` shape.
- **Close-and-flow** — every cycle (story, sprint, iteration) closes
  cleanly so the next can flow in. No half-states.
- **Role-spec driven agents** — every spawned agent runs from a frozen
  `LANE / ANTI-LANE / VOICE / OUTPUT FORMAT / TERMINATION` brief.

## What I'm NOT asking for in Iter 1

- A web UI. Terminal-only is fine.
- Multi-iteration concurrency. One iteration active at a time matches
  PO Tool's contract; mirror that.
- Database backing. JSONL log only.
- Automatic conflict resolution if PO Tool changes requirements
  mid-iteration. We'll handle that case manually if it comes up.
- Authentication / multi-user. Single-user, single-machine.

## Context the operator should know

- The tool's log lives at `log.jsonl` in the package directory,
  same convention as the other suite tools.
- The operator runs it from inside an active dev-suite session; it's
  not a long-running service.
- Tests are mandatory per story; no story is accepted without a
  Reviewer-approved test pass.
