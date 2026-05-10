# sm-tool

A scrum-master pipeline tool: reads a product-owner iteration handoff,
drives requirement-to-story decomposition, manages the per-story
test-writer → coder → reviewer pipeline. Single JSONL log, append-only.

**Status: in active build.** This repo is filling in live during
Iteration 1 of `sm-tool` v0.3.

Part of a small development suite shaped on the same patterns:

- [`standup-tool`](https://github.com/nicky2tymze/standup-tool) — daily-standup loop (shipped v0.1)
- [`po-tool`](https://github.com/nicky2tymze/po-tool) — product-owner pipeline (shipped v0.2)
- `sm-tool` — scrum-master pipeline (this repo, in build)

Each tool ships on the same shape: append-only JSONL log,
content-oriented schema, close-and-flow lifecycle. SM Tool closes the
loop — once it ships, the suite covers customer-interview through
per-story execution end-to-end with no per-story manual handoff.

## What it does (Iter 1 design)

Inputs:

- A `handoff_iter_<id>.json` produced by `po-tool` at iteration open.
  Contains the iteration goal and the selected requirement set.

Behavior:

- Decomposes each requirement into atomic, sequenced stories with
  technical-level acceptance criteria, S/M/L sizes, and explicit
  dependencies.
- Records every state transition as an append-only entry in
  `log.jsonl` — story creation, sprint open, story state changes,
  acceptance, rejection, sprint close.
- Optionally drives the per-story execution pipeline: spawns
  test-writer, coder, reviewer agents in sequence; pushes back on the
  caller when a story can't be closed clean.

Outputs:

- A story backlog readable via `list_stories()` / `status()`.
- A handoff artifact at sprint close (mirroring the PO Tool pattern)
  that downstream tooling can consume.

## Public API (planned for Iter 1)

```python
from sm import (
    LOG_PATH,
    # Story decomposition
    open_sprint, decompose_requirement,
    add_story, rerank_story,
    # Story lifecycle
    advance_story_phase, mark_story_complete,
    # Sprint lifecycle
    list_stories, status, close_sprint,
)
```

The exact surface is the live design target of the build. Stories that
land in `tests/` define the contract as they pass.

## How it gets built

Same shape as the suite: test-writer → coder → reviewer per story,
each story closing cleanly before the next opens (close-and-flow).
The build is being recorded; the recording will be edited into a 2-3
minute time-lapse showing the patterns landing in motion.

## Installation

Stdlib only — Python 3.10+.

```bash
git clone https://github.com/nicky2tymze/sm-tool
cd sm-tool
python -m pytest tests/
```

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 Nick Trolian
