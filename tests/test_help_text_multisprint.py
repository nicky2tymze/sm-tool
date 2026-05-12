"""iter4-multisprint-v2 Sprint 1 Story 5 — `_HELP_TEXT` multi-sprint refresh.

Story:  Update _HELP_TEXT to reflect multi-sprint semantics  (S, req-1)

Acceptance criteria (verbatim):
  - `_HELP_TEXT` `sprint-cut` description explains that multiple cuts are
    allowed once prior cuts reach terminal state; mentions that
    `cut_position N` applies to planned stories; references that close
    validates all sprints terminal.

What this file pins:

  - **Multi-cut phrasing.** `_HELP_TEXT` communicates that `sprint-cut`
    may be called more than once per iteration (re-cut / additional
    cut / multiple cuts) — i.e. that the one-cut-per-iteration lock
    from Iter 1 has been replaced by the iter4-multisprint-v2 Story 1
    sequential-lock semantics.

  - **Terminal-state precondition phrasing.** `_HELP_TEXT` communicates
    that a subsequent `sprint-cut` is only permitted once every story
    in the prior cut has reached a terminal state
    (`accepted` / `rejected` / `force_closed`). This pins the Story 1
    lock semantics into the help surface so an operator reading
    `--help` doesn't have to read source or experiment to discover the
    precondition.

  - **N-applies-to-currently-planned phrasing.** `_HELP_TEXT`
    communicates that `sprint-cut <N>` counts N against the
    currently-planned subset of the backlog (NOT the cumulative
    original-backlog position). This pins the Story 3 `cut_position`
    semantics — under multi-sprint, N is "first N of the still-planned
    stories", so an operator re-cutting after a 3-story first cut and
    wanting the next two writes `sprint-cut 2` (not `sprint-cut 5`).

  - **Close-validates-all-sprints phrasing.** `_HELP_TEXT` communicates
    that `close` validates EVERY sprint (across all cuts) is terminal,
    not just the latest cut. Pins Story 4's all-sprints validation
    contract into the help surface.

Anti-lane invariants preserved (Iter 2 Story 13 drift-catcher
contract continues to hold):

  - All 12 dispatched subcommands remain in `_HELP_TEXT`.
  - All 12 documented exit codes remain in `_HELP_TEXT`.
  - Exit code 10 remains ABSENT (Iter 2 Story 13's reserved-gap
    design — no `EXIT_*` constant with value 10, so the help row must
    not list one).
  - `python -m sm --help` continues to exit 0 with stdout matching
    `_HELP_TEXT`.

TestWriter design choices (phrase-matching strategy):

  Each of the four multi-sprint semantic facts is pinned by a
  REGEX OVER A SET of acceptable phrasings (case-insensitive). The
  Coder picks the wording; the test pins SEMANTIC CONTENT, not
  prose style. The accepted phrase sets are documented at the
  patterns below.

These tests pin Story 5. They MUST fail on first run (no Coder has
touched `_HELP_TEXT` yet) for the multi-sprint phrasing assertions,
and they MUST pass after the Coder lands Story 5's refresh.

Invocation contract:
  * Source-level tests read `sm._HELP_TEXT` after `import sm`.
  * Behavioral tests shell out to `python -m sm --help` via
    subprocess from PACKAGE_DIR (the standard suite pattern).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Inherited expectations from Iter 2 Story 13's drift catcher. These are
# duplicated here intentionally so this test file is self-contained: a
# regression in `test_retro_help_text_refresh.py` (e.g. deletion of the
# file or a future restructure) would not silently weaken Story 5's
# behavioral-preservation pins.
# ---------------------------------------------------------------------------
EXPECTED_SUBCOMMANDS: tuple[str, ...] = (
    "decompose",
    "sprint-cut",
    "ingest",
    "start",
    "submit",
    "accept",
    "reject",
    "record-review",
    "status",
    "close",
    "force-close",
    "execute",
)

# (constant_name, value) for the 12 documented exit codes. Code 10 is
# reserved (gap between EXIT_TRANSITION=9 and EXIT_CLOSE=11) and is NOT
# expected in the help text — pinned negatively below.
EXPECTED_EXIT_CODES: tuple[tuple[str, int], ...] = (
    ("EXIT_OK", 0),
    ("EXIT_OTHER", 1),
    ("EXIT_PATH", 2),
    ("EXIT_JSON", 3),
    ("EXIT_SHAPE", 4),
    ("EXIT_DUP_ID", 5),
    ("EXIT_SINGLE_ACTIVE", 6),
    ("EXIT_UNKNOWN_REQ", 7),
    ("EXIT_SPRINT_CUT", 8),
    ("EXIT_TRANSITION", 9),
    ("EXIT_CLOSE", 11),
    ("EXIT_AGENT_ERROR", 12),
)


# ---------------------------------------------------------------------------
# Phrase sets for the four multi-sprint semantic facts.
#
# Each set lists acceptable substrings (case-insensitive, plain match
# unless noted). The Coder picks any one (or more); the test passes if
# ANY member of the set is present in `_HELP_TEXT`.
# ---------------------------------------------------------------------------

# Fact 1: multiple cuts are allowed per iteration.
MULTI_CUT_PHRASES: tuple[str, ...] = (
    "multiple cuts",
    "multiple sprint",       # "multiple sprint cuts" / "multiple sprints"
    "multiple sprint-cut",
    "multiple sprint cuts",
    "multi-sprint",
    "multi sprint",
    "re-cut",
    "recut",
    "additional cut",
    "additional cuts",
    "additional sprint",
    "another cut",
    "another sprint",
    "more than once",
    "may be called multiple",
    "can be called multiple",
    "subsequent cut",
    "subsequent cuts",
    "subsequent sprint",
)

# Fact 2: subsequent cuts require prior cut's stories to reach terminal
# state.
#
# NOTE: the bare word "terminal" is intentionally EXCLUDED — Iter 2
# Story 13's grouping uses "Terminal commands:" as a section header, so
# `"terminal" in help.lower()` would be a false positive. We require
# phrases that pair "terminal" with a state/precondition word.
TERMINAL_PRECONDITION_PHRASES: tuple[str, ...] = (
    "terminal state",
    "terminal states",
    "reach terminal",
    "reaches terminal",
    "reached terminal",
    "are terminal",
    "is terminal",
    "in terminal",              # "in terminal state" / "in terminal states"
    "all terminal",             # "all terminal"
    "accepted, rejected",       # explicit listing of terminal states
    "accepted or rejected",
    "accepted/rejected",
    "accepted / rejected",
    "after prior cut",
    "after prior cuts",
    "once prior cut",
    "once prior cuts",
    "prior cut complete",
    "prior cuts complete",
    "prior cut completes",
    "prior cuts completes",
    "prior cut's stories",
    "prior sprint complete",
    "previous cut complete",
    "previous cuts complete",
    "previous sprint complete",
    "once the prior",
    "after the prior",
)

# Fact 3: N applies to currently-planned stories (not the cumulative
# backlog position).
N_APPLIES_PHRASES: tuple[str, ...] = (
    "currently-planned",
    "currently planned",
    "planned stor",            # "planned stories" / "planned story"
    "still-planned",
    "still planned",
    "remaining planned",
    "not yet in a sprint",
    "not yet cut",
    "not in a sprint",
    "not yet committed",
    "uncut stor",              # "uncut stories" / "uncut story"
    "not previously cut",
    "remaining backlog",
    "remaining stor",          # "remaining stories"
    "stories not yet",
    "applies to planned",
)

# Fact 4: close validates ALL sprints terminal.
CLOSE_VALIDATES_ALL_PHRASES: tuple[str, ...] = (
    "all sprint",                 # "all sprints" / "all sprint-cut"
    "all sprints",
    "every sprint",
    "every cut",
    "all cuts",
    "across all cut",             # "across all cuts"
    "across all sprint",          # "across all sprints"
    "across every cut",
    "across every sprint",
    "validates across all",
    "validates every",
    "validates all",
    "each sprint",
    "each cut",
    "stories from every cut",
    "stories across every",
    "stories across all",
    "in_sprint_story_ids from all",
    "every in-sprint stor",
    "every in_sprint stor",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _help_text() -> str:
    """Return `sm._HELP_TEXT` as-is."""
    import sm
    return sm._HELP_TEXT


def _has_any_phrase(haystack: str, phrases: tuple[str, ...]) -> tuple[bool, str | None]:
    """Case-insensitive substring search: True iff ANY phrase in
    `phrases` is a substring of `haystack`. Returns (found, matched_phrase).
    """
    low = haystack.lower()
    for p in phrases:
        if p.lower() in low:
            return True, p
    return False, None


def _run_cli(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Invoke `python -m sm <args...>` from PACKAGE_DIR, captured."""
    return subprocess.run(
        [sys.executable, "-m", "sm", *args],
        cwd=str(PACKAGE_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ===========================================================================
# Category A — Multi-cut phrasing in _HELP_TEXT (3 tests)
#
# Story 5's three new semantic facts about `sprint-cut`:
#   A1. Multiple cuts allowed.
#   A2. Prior cut must reach terminal state first.
#   A3. N counts against currently-planned stories.
# ===========================================================================


def test_help_text_indicates_multiple_cuts_allowed():
    """`_HELP_TEXT` contains at least one phrase from
    MULTI_CUT_PHRASES — communicating that `sprint-cut` may be called
    more than once per iteration.

    The Coder picks the exact wording; this test pins SEMANTIC CONTENT.
    """
    help_text = _help_text()
    found, _ = _has_any_phrase(help_text, MULTI_CUT_PHRASES)
    assert found, (
        "_HELP_TEXT must communicate that multiple sprint-cuts are "
        "allowed per iteration (Story 5). Acceptable phrasings include: "
        f"{MULTI_CUT_PHRASES!r}. None of them were found in:\n"
        f"{help_text}"
    )


def test_help_text_indicates_terminal_state_precondition():
    """`_HELP_TEXT` contains at least one phrase from
    TERMINAL_PRECONDITION_PHRASES — communicating that a subsequent
    `sprint-cut` is only permitted once the prior cut's stories reach
    terminal state (`accepted` / `rejected` / `force_closed`).
    """
    help_text = _help_text()
    found, _ = _has_any_phrase(help_text, TERMINAL_PRECONDITION_PHRASES)
    assert found, (
        "_HELP_TEXT must communicate the terminal-state precondition "
        "for subsequent sprint-cuts (Story 5). Acceptable phrasings "
        f"include: {TERMINAL_PRECONDITION_PHRASES!r}. None of them were "
        f"found in:\n{help_text}"
    )


def test_help_text_indicates_n_applies_to_currently_planned():
    """`_HELP_TEXT` contains at least one phrase from
    N_APPLIES_PHRASES — communicating that `sprint-cut <N>` counts N
    against the currently-planned subset of the backlog (not the
    cumulative original-backlog position).
    """
    help_text = _help_text()
    found, _ = _has_any_phrase(help_text, N_APPLIES_PHRASES)
    assert found, (
        "_HELP_TEXT must communicate that `sprint-cut <N>` applies to "
        "currently-planned stories (Story 5). Acceptable phrasings "
        f"include: {N_APPLIES_PHRASES!r}. None of them were found in:\n"
        f"{help_text}"
    )


def test_help_text_multi_sprint_phrasing_appears_near_sprint_cut_entry():
    """The multi-sprint phrasing lands near the `sprint-cut` entry (or
    in a notes / details section that follows the subcommand block) —
    not buried somewhere unrelated.

    Pinned loosely: at least one MULTI_CUT_PHRASES OR one
    N_APPLIES_PHRASES match falls within 25 lines of the line that
    introduces the `sprint-cut` subcommand. (25 lines comfortably spans
    the Iter 2 layout's per-subcommand row plus a typical notes /
    semantics paragraph or list.)

    This catches the failure mode of "Coder added the phrases somewhere
    in _HELP_TEXT but nowhere near sprint-cut" — e.g. accidentally in
    the close section only.
    """
    help_text = _help_text()
    lines = help_text.splitlines()

    sprint_cut_line_idx: int | None = None
    for i, line in enumerate(lines):
        # Match `sprint-cut` as a standalone subcommand name on the row
        # (the Iter 2 Story 13 listing form).
        if re.search(r"(?<![A-Za-z0-9_-])sprint-cut(?![A-Za-z0-9_-])", line):
            sprint_cut_line_idx = i
            break

    assert sprint_cut_line_idx is not None, (
        "_HELP_TEXT must list `sprint-cut` as a subcommand row "
        "(Iter 2 Story 13 contract). Got _HELP_TEXT:\n" + help_text
    )

    window_start = sprint_cut_line_idx
    window_end = min(len(lines), sprint_cut_line_idx + 25 + 1)
    window = "\n".join(lines[window_start:window_end])

    # Either a multi-cut phrase OR a currently-planned phrase must
    # appear in the window. (The terminal-precondition fact is more
    # likely to live in a notes section that could be further away —
    # we don't require it within the window. The window pin is about
    # ensuring the sprint-cut entry itself was actually touched.)
    found_multi, _ = _has_any_phrase(window, MULTI_CUT_PHRASES)
    found_n, _ = _has_any_phrase(window, N_APPLIES_PHRASES)
    assert found_multi or found_n, (
        f"_HELP_TEXT must include multi-sprint phrasing near the "
        f"`sprint-cut` entry (within {25} lines of line "
        f"{sprint_cut_line_idx}). Searched window:\n---\n{window}\n---"
    )


# ===========================================================================
# Category B — Close-validates-all-sprints phrasing (2 tests)
# ===========================================================================


def test_help_text_indicates_close_validates_all_sprints():
    """`_HELP_TEXT` contains at least one phrase from
    CLOSE_VALIDATES_ALL_PHRASES — communicating that `close` validates
    across all sprints (every cut), not just the latest.

    Pins Story 4's all-sprints contract into the help surface.
    """
    help_text = _help_text()
    found, _ = _has_any_phrase(help_text, CLOSE_VALIDATES_ALL_PHRASES)
    assert found, (
        "_HELP_TEXT must communicate that `close` validates across "
        "all sprints (Story 5 / Story 4). Acceptable phrasings include: "
        f"{CLOSE_VALIDATES_ALL_PHRASES!r}. None of them were found in:\n"
        f"{help_text}"
    )


def test_help_text_close_all_sprints_phrasing_in_close_context():
    """The "all sprints" phrasing for `close` either appears on the
    `close` subcommand row itself OR in a notes / details section that
    mentions `close` within the same paragraph.

    Pinned loosely: there exists a contiguous block of lines such that
      * the block contains the token `close` as a standalone subcommand
        word (matched outside the `force-close` compound), AND
      * the same block contains at least one CLOSE_VALIDATES_ALL_PHRASES
        match.

    "Block" = a sequence of consecutive non-blank lines (paragraph), OR
    a single subcommand row by itself. This catches the failure mode of
    "Coder added 'all sprints' phrasing somewhere disconnected from any
    mention of close".
    """
    help_text = _help_text()
    lines = help_text.splitlines()

    # Split into blocks separated by blank lines.
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    # A block "mentions close" if it contains the standalone word
    # `close` not preceded by `force-` (which would mean `force-close`).
    def block_mentions_close(block_text: str) -> bool:
        # Find occurrences of `close` outside hyphen-compound forms.
        for m in re.finditer(
            r"(?<![A-Za-z0-9_-])close(?![A-Za-z0-9_-])",
            block_text,
        ):
            return True
        return False

    matched_block: str | None = None
    for block in blocks:
        block_text = "\n".join(block)
        if not block_mentions_close(block_text):
            continue
        found, _ = _has_any_phrase(block_text, CLOSE_VALIDATES_ALL_PHRASES)
        if found:
            matched_block = block_text
            break

    assert matched_block is not None, (
        "_HELP_TEXT must include 'all sprints' / 'every cut' phrasing "
        "in a paragraph that also mentions `close` (Story 5 / Story 4). "
        "No block of _HELP_TEXT contains both a standalone `close` "
        f"reference and one of {CLOSE_VALIDATES_ALL_PHRASES!r}.\n"
        f"_HELP_TEXT was:\n{help_text}"
    )


# ===========================================================================
# Category C — Behavioral preservation (Iter 2 Story 13 drift-catcher
# invariants continue to hold) (3 tests)
# ===========================================================================


@pytest.mark.parametrize("subcommand", EXPECTED_SUBCOMMANDS)
def test_help_text_still_lists_every_registered_subcommand(subcommand: str):
    """ANTI-LANE pin. Every one of the 12 Iter 2 Story 13 subcommands
    still appears in `_HELP_TEXT` after Story 5's refresh.

    A common refresh failure: a Coder rewrites the sprint-cut paragraph
    and accidentally drops another subcommand's row. Parametrized — a
    failure pinpoints which subcommand went missing.
    """
    help_text = _help_text()
    pattern = (
        r"(?<![A-Za-z0-9_-])" + re.escape(subcommand) + r"(?![A-Za-z0-9_-])"
    )
    assert re.search(pattern, help_text), (
        f"_HELP_TEXT must still list subcommand {subcommand!r} after "
        f"Story 5's refresh (Iter 2 Story 13 invariant). Got "
        f"_HELP_TEXT:\n{help_text}"
    )


@pytest.mark.parametrize("constant_name,value", EXPECTED_EXIT_CODES)
def test_help_text_still_lists_every_documented_exit_code(
    constant_name: str, value: int
):
    """ANTI-LANE pin. Every one of the 12 Iter 2 Story 13 exit codes
    (0,1,2,3,4,5,6,7,8,9,11,12) still appears as a row in `_HELP_TEXT`
    after Story 5's refresh. Code 10 remains absent (pinned in the next
    test).
    """
    help_text = _help_text()
    pattern = r"(?m)^[^\S\n]*" + str(value) + r"\b"
    assert re.search(pattern, help_text), (
        f"_HELP_TEXT must still list exit code {value} "
        f"(constant {constant_name}) after Story 5's refresh "
        f"(Iter 2 Story 13 invariant). Got _HELP_TEXT:\n{help_text}"
    )


def test_help_text_still_omits_reserved_exit_code_10():
    """ANTI-LANE pin. Exit code 10 remains absent from the exit-codes
    section (Iter 2 Story 13 design: no `EXIT_*` constant has value 10,
    so the help row must not list one). Story 5 must not introduce a
    `10  ...` row while refreshing the help text.
    """
    help_text = _help_text()
    bad_row = re.compile(r"(?m)^[^\S\n]*10\b\s+[A-Za-z]")
    assert not bad_row.search(help_text), (
        "_HELP_TEXT must not document exit code 10 after Story 5's "
        "refresh (Iter 2 Story 13 invariant: 10 is reserved). "
        f"Got _HELP_TEXT:\n{help_text}"
    )


# ===========================================================================
# Category D — CLI behavior pin (2 tests)
# ===========================================================================


def test_cli_help_still_exits_zero_after_refresh():
    """`python -m sm --help` continues to exit 0 after Story 5's
    refresh. Catches the failure mode of "Coder broke the triple-quoted
    string or introduced a syntax error while editing _HELP_TEXT".
    """
    result = _run_cli("--help")
    assert result.returncode == 0, (
        f"`python -m sm --help` must exit 0 after Story 5; got "
        f"returncode={result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_cli_help_stdout_matches_help_text_constant():
    """`python -m sm --help` stdout equals the in-process
    `sm._HELP_TEXT` constant (subject to trailing-newline normalization,
    matching the Iter 2 Story 13 contract). Catches installed-vs-source
    drift after the refresh.
    """
    import sm

    result = _run_cli("--help")
    assert result.returncode == 0, (
        f"`python -m sm --help` must exit 0; got returncode="
        f"{result.returncode}"
    )
    stdout = result.stdout
    expected = sm._HELP_TEXT
    assert (
        stdout == expected
        or stdout == expected + "\n"
        or stdout.rstrip() == expected.rstrip()
    ), (
        "`python -m sm --help` stdout must equal _HELP_TEXT after "
        "Story 5's refresh.\n"
        f"--- stdout ---\n{stdout!r}\n"
        f"--- expected _HELP_TEXT ---\n{expected!r}"
    )
