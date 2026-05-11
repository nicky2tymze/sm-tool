"""Iter 2 Story 13 — Retro polish: `_HELP_TEXT` refresh.

Story 13 (size S, behavior-preserving) closes the last retro item from
Iter 2's polish cluster: the `_HELP_TEXT` constant in sm.py is stale.

Current state (pinned by this test file BEFORE the Coder lands the
refresh):

  * `_HELP_TEXT` mentions only `ingest` — 11 of the 12 registered CLI
    subcommands are absent (decompose, sprint-cut, start, submit, accept,
    reject, record-review, status, close, force-close, execute).
  * `_HELP_TEXT` documents exit codes 0-6 only. The 6 additional codes
    introduced after Iter 1 Sprint 1 — EXIT_UNKNOWN_REQ=7,
    EXIT_SPRINT_CUT=8, EXIT_TRANSITION=9, EXIT_CLOSE=11,
    EXIT_AGENT_ERROR=12 — are not mentioned. (Code 10 is intentionally
    reserved/skipped.)
  * `--help` therefore is not a discoverable surface; a user has to read
    sm.py to find subcommands or exit codes.

Story 13's contract (pinned here):

  1. ALL 12 registered subcommands appear in `_HELP_TEXT` with a
     short one-line description each:
       decompose, sprint-cut, ingest, start, submit, accept, reject,
       record-review, status, close, force-close, execute
  2. ALL documented exit codes appear in `_HELP_TEXT` — the 12 codes
     currently defined (0,1,2,3,4,5,6,7,8,9,11,12). Code 10 is reserved
     and not documented. EXIT_AGENT_ERROR=12 is named explicitly with
     its semantic phrase ("agent" appears near code 12).
  3. Subcommands are grouped logically. We pin the convention via group
     headers / labels:
       - "Read-only" section contains: status
       - "Mutating" section contains: ingest, decompose, sprint-cut,
         start, submit, record-review, accept, reject, execute
       - "Terminal" section contains: close, force-close
     The TestWriter chose this grouping. The Coder may rename headers
     (e.g. "Query / Mutating / Terminal") but the BUCKET MEMBERSHIP is
     pinned: each subcommand appears in a section whose header line is
     above it and the next group's header line is below it.
  4. Drift-catcher: the test that introspects sm's CLI dispatcher /
     `_LIFECYCLE_TARGETS` derives the 12 subcommand names DYNAMICALLY
     and verifies each appears in `_HELP_TEXT`. Adding a 13th
     subcommand without updating `_HELP_TEXT` will fail this test.
  5. Behavioral pin: `python -m sm --help` and `python -m sm -h` both
     print `_HELP_TEXT` to stdout and exit 0.

ANTI-LANE invariants (Story 13 is behavior-preserving beyond the help
text content):

  * All 12 registered subcommands continue to dispatch.
  * Exit code CONSTANT values do not change.
  * `_LIFECYCLE_TARGETS` retains exactly its current 4 members.

These tests pin the refresh. They MUST fail on first run (no Coder has
touched `_HELP_TEXT` yet) and pass after the Coder lands Story 13.

Invocation contract:
  * Source-level tests use `import sm` and read `_HELP_TEXT` / inspect
    `_LIFECYCLE_TARGETS` and the dispatcher source.
  * Behavioral tests shell out to `python -m sm --help` / `-h` via
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
# The 12 registered subcommands (extracted from sm.py `_cli_main` body).
# ---------------------------------------------------------------------------
# This list is the static expectation. The drift-catching test uses
# `_dispatcher_subcommands()` below to re-derive the set dynamically.
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

# Logical bucket assignments. The Coder may rename the section headers
# but must keep these memberships. Order within a bucket is the natural
# order printed by `_HELP_TEXT` — we only pin SET membership and bucket
# ordering, not intra-bucket ordering.
READ_ONLY_COMMANDS: frozenset[str] = frozenset({"status"})
MUTATING_COMMANDS: frozenset[str] = frozenset({
    "ingest", "decompose", "sprint-cut",
    "start", "submit", "record-review",
    "accept", "reject", "execute",
})
TERMINAL_COMMANDS: frozenset[str] = frozenset({"close", "force-close"})

# The 12 documented exit codes. Code 10 is reserved (gap between
# EXIT_TRANSITION=9 and EXIT_CLOSE=11) and is NOT expected in the help
# text. Format: (constant_name, value).
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
# Helpers
# ---------------------------------------------------------------------------


def _help_text() -> str:
    """Return sm._HELP_TEXT as-is."""
    import sm
    return sm._HELP_TEXT


def _dispatcher_subcommands() -> set[str]:
    """Dynamically derive the set of CLI subcommands registered in
    `_cli_main` by combining:

      * The hard-coded `cmd == "..."` / `cmd in (...)` checks (extracted
        via regex over sm.py source).
      * The `_LIFECYCLE_TARGETS` keys (start / submit / accept / reject).

    Excludes the `--help` / `-h` / `help` meta dispatch since that is
    not a subcommand.

    This is what the drift-catcher uses: when the Coder adds a 13th
    subcommand to `_cli_main`, this set grows automatically and the
    membership pin breaks until `_HELP_TEXT` is updated.
    """
    import sm

    src = SM_PATH.read_text(encoding="utf-8")

    found: set[str] = set()

    # Match `if cmd == "name":` (the standard per-subcommand branch).
    for m in re.finditer(r'^\s*if\s+cmd\s*==\s*"([a-z][a-z0-9_-]*)"\s*:',
                         src, flags=re.MULTILINE):
        found.add(m.group(1))

    # Exclude the help/meta branch members.
    found -= {"--help", "-h", "help"}

    # The lifecycle branch dispatches `cmd in _LIFECYCLE_TARGETS`; add
    # those keys (4 names).
    found |= set(sm._LIFECYCLE_TARGETS.keys())

    return found


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
# Category A — Subcommand coverage in _HELP_TEXT (5 tests)
# ===========================================================================


@pytest.mark.parametrize("subcommand", EXPECTED_SUBCOMMANDS)
def test_help_text_mentions_subcommand(subcommand: str):
    """Each of the 12 registered subcommands appears verbatim in
    `_HELP_TEXT`. Parametrized — yields 12 test cases, one per
    subcommand, so a failure pinpoints exactly which one is missing.
    """
    help_text = _help_text()
    # Match the subcommand as a whole word so e.g. "close" doesn't
    # accidentally match inside "close_iteration" prose. Note hyphens
    # (sprint-cut, record-review, force-close) cannot be wrapped in
    # `\b...\b` alone since `-` is a word boundary — we use lookarounds
    # that exclude alphanumerics on either side.
    pattern = (
        r"(?<![A-Za-z0-9_-])" + re.escape(subcommand) + r"(?![A-Za-z0-9_-])"
    )
    assert re.search(pattern, help_text), (
        f"_HELP_TEXT must mention subcommand {subcommand!r} (Story 13). "
        f"Got _HELP_TEXT:\n{help_text}"
    )


def test_help_text_mentions_every_registered_subcommand():
    """DRIFT-CATCHER. Dynamically introspect sm's CLI dispatcher and
    `_LIFECYCLE_TARGETS`, derive the actual set of registered
    subcommand names, and verify every one is mentioned in
    `_HELP_TEXT`.

    When a future Iter adds a 13th subcommand to `_cli_main` and forgets
    to update `_HELP_TEXT`, this test fails and forces the help update.
    """
    registered = _dispatcher_subcommands()

    # Sanity: dispatcher introspection must find at least the 12 we know
    # exist today. (A regression in `_dispatcher_subcommands` itself
    # would otherwise silently pass.)
    assert registered >= set(EXPECTED_SUBCOMMANDS), (
        f"Dispatcher introspection failed to recover the known 12 "
        f"subcommands. Expected superset {set(EXPECTED_SUBCOMMANDS)!r}; "
        f"got {registered!r}. Fix `_dispatcher_subcommands()` first."
    )

    help_text = _help_text()
    missing: list[str] = []
    for cmd in sorted(registered):
        pattern = (
            r"(?<![A-Za-z0-9_-])" + re.escape(cmd) + r"(?![A-Za-z0-9_-])"
        )
        if not re.search(pattern, help_text):
            missing.append(cmd)
    assert not missing, (
        f"_HELP_TEXT is missing these registered subcommands: "
        f"{missing!r}. Story 13 requires every dispatcher-registered "
        f"subcommand to be listed in --help. Update _HELP_TEXT."
    )


def test_help_text_subcommand_count_at_least_12():
    """At least 12 distinct registered-subcommand names appear in
    `_HELP_TEXT`. Counts unique matches (so a subcommand mentioned twice
    only counts once).
    """
    help_text = _help_text()
    hits: set[str] = set()
    for cmd in EXPECTED_SUBCOMMANDS:
        pattern = (
            r"(?<![A-Za-z0-9_-])" + re.escape(cmd) + r"(?![A-Za-z0-9_-])"
        )
        if re.search(pattern, help_text):
            hits.add(cmd)
    assert len(hits) >= 12, (
        f"_HELP_TEXT lists only {len(hits)} of the 12 registered "
        f"subcommands. Found: {sorted(hits)!r}. Missing: "
        f"{sorted(set(EXPECTED_SUBCOMMANDS) - hits)!r}."
    )


def test_help_text_subcommands_each_have_oneline_description():
    """Each subcommand line carries some after-the-name description.
    Pinned loosely: on the line containing the subcommand name, there is
    at least one non-whitespace token AFTER the name. (Empty bullets
    like `  decompose\\n` violate the contract.)
    """
    help_text = _help_text()
    bare_listings: list[str] = []
    for cmd in EXPECTED_SUBCOMMANDS:
        # Find every line that contains the subcommand as a standalone
        # token. Require at least one such line where, after the
        # subcommand, there is at least one word character later on the
        # same line.
        pattern = re.compile(
            r"^[^\S\n]*" + re.escape(cmd)
            + r"(?![A-Za-z0-9_-])[^\n]*$",
            flags=re.MULTILINE,
        )
        described = False
        for line in pattern.findall(help_text):
            # Strip the command name itself; what's left should contain
            # at least one alphanumeric character (description /
            # placeholder argspec).
            remainder = line.split(cmd, 1)[1] if cmd in line else ""
            if re.search(r"[A-Za-z0-9]", remainder):
                described = True
                break
        if not described:
            bare_listings.append(cmd)
    assert not bare_listings, (
        f"_HELP_TEXT lists these subcommands without a one-line "
        f"description on the same line: {bare_listings!r}. Story 13: "
        f"each subcommand row needs a short description."
    )


def test_help_text_uses_correct_subcommand_hyphenation():
    """Multi-word subcommands use the dispatched hyphen form
    (`sprint-cut`, `record-review`, `force-close`), NOT
    underscored (`sprint_cut`) or spaced forms.
    """
    help_text = _help_text()
    hyphenated = ("sprint-cut", "record-review", "force-close")
    underscored = ("sprint_cut", "record_review", "force_close")
    for canonical in hyphenated:
        assert canonical in help_text, (
            f"_HELP_TEXT must spell {canonical!r} with a hyphen "
            f"(the dispatcher form). Got _HELP_TEXT:\n{help_text}"
        )
    for wrong in underscored:
        # Underscored forms must not appear as standalone tokens; allow
        # them inside python function names if any (unlikely).
        pattern = (
            r"(?<![A-Za-z0-9_-])" + re.escape(wrong) + r"(?![A-Za-z0-9_-])"
        )
        assert not re.search(pattern, help_text), (
            f"_HELP_TEXT must not use the underscored form {wrong!r}; "
            f"the CLI dispatches the hyphenated form."
        )


# ===========================================================================
# Category B — Exit code coverage in _HELP_TEXT (4 tests)
# ===========================================================================


@pytest.mark.parametrize("constant_name,value", EXPECTED_EXIT_CODES)
def test_help_text_mentions_exit_code(constant_name: str, value: int):
    """Each of the 12 documented exit codes is mentioned in
    `_HELP_TEXT`. The code's numeric value must appear in the dedicated
    exit-code section. Parametrized — yields 12 cases.

    We look for the digit as a standalone token at the start of a line
    (typical layout: `  0  success`, `  12  agent error...`).
    """
    help_text = _help_text()
    # Match `<value>` as a standalone token preceded by whitespace and
    # followed by whitespace — the canonical exit-code-row form.
    pattern = (
        r"(?m)^[^\S\n]*" + str(value) + r"\b"
    )
    assert re.search(pattern, help_text), (
        f"_HELP_TEXT must list exit code {value} "
        f"(constant {constant_name}). Story 13 requires every "
        f"documented exit code to appear in the exit-codes section. "
        f"Got _HELP_TEXT:\n{help_text}"
    )


def test_help_text_does_not_list_reserved_code_10_in_codes_section():
    """Exit code 10 is reserved (gap between EXIT_TRANSITION=9 and
    EXIT_CLOSE=11) — there is no `EXIT_*` constant with value 10.
    `_HELP_TEXT` therefore must not list `10` as an exit code row.

    We pin this via the line-start row form (` 10 description`); the
    digit 10 is allowed elsewhere (e.g. inside prose / version strings)
    but not as a documented code row.
    """
    help_text = _help_text()
    # Reject ` 10  ...` or `  10\t...` as a row, but allow `100`, `10`
    # inside prose / version-like contexts.
    bad_row = re.compile(r"(?m)^[^\S\n]*10\b\s+[A-Za-z]")
    assert not bad_row.search(help_text), (
        "_HELP_TEXT must not document exit code 10 — it is reserved "
        "(no EXIT_* constant with value 10). Codes are 0,1,...,9,11,12. "
        f"Got _HELP_TEXT:\n{help_text}"
    )


def test_help_text_mentions_exit_agent_error_explicitly():
    """EXIT_AGENT_ERROR=12 must be named with its semantic phrase, not
    just the bare digit `12`. We accept any of:
      * the literal constant name `EXIT_AGENT_ERROR`
      * the phrase "agent error" (case-insensitive)
      * "agent" appearing on the same physical line as the digit 12
        (which catches phrasings like "12  agent / API key error").
    """
    help_text = _help_text()
    haystack_lower = help_text.lower()
    direct = (
        "exit_agent_error" in haystack_lower
        or "agent error" in haystack_lower
    )
    same_line = False
    for line in help_text.splitlines():
        # Pin: line contains digit 12 as a standalone token AND the
        # word "agent" (case-insensitive).
        if re.search(r"\b12\b", line) and "agent" in line.lower():
            same_line = True
            break
    assert direct or same_line, (
        "_HELP_TEXT must name EXIT_AGENT_ERROR=12 with semantic text "
        "('agent error' / 'EXIT_AGENT_ERROR' / 'agent' on the row). "
        f"Got _HELP_TEXT:\n{help_text}"
    )


def test_help_text_exit_codes_section_header_present():
    """`_HELP_TEXT` has a dedicated section header for exit codes (so
    the codes don't get lost in subcommand prose). We accept any header
    line containing both `exit` and `code` (case-insensitive).
    """
    help_text = _help_text()
    has_header = False
    for line in help_text.splitlines():
        low = line.lower()
        if "exit" in low and "code" in low:
            has_header = True
            break
    assert has_header, (
        "_HELP_TEXT must include a section header containing 'exit' "
        "and 'code' (e.g. 'Exit codes:'). Got _HELP_TEXT:\n"
        f"{help_text}"
    )


# ===========================================================================
# Category C — Logical grouping (3 tests)
# ===========================================================================


def _help_text_lines_with_index() -> list[tuple[int, str]]:
    return list(enumerate(_help_text().splitlines()))


def _first_line_index_for(token: str) -> int:
    """Return the line index of the first `_HELP_TEXT` line that mentions
    the token as a standalone subcommand name, else -1.
    """
    pattern = re.compile(
        r"(?<![A-Za-z0-9_-])" + re.escape(token) + r"(?![A-Za-z0-9_-])"
    )
    for idx, line in _help_text_lines_with_index():
        if pattern.search(line):
            return idx
    return -1


def test_help_text_has_logical_group_headers():
    """`_HELP_TEXT` contains group-header lines that label the three
    buckets. Lenient match — any line whose lowercase text includes one
    of the bucket keywords counts:
      * read-only / query / status section
      * mutating / action / sprint / lifecycle / story / write section
      * terminal / close / finish section
    We accept that the Coder names the headers however they like as long
    as each of the three bucket-meanings appears as a header line.
    """
    lines = [line.lower() for line in _help_text().splitlines()]

    def has_any(*keywords: str) -> bool:
        return any(any(kw in line for kw in keywords) for line in lines)

    read_only_header = has_any(
        "read-only", "read only", "query", "queries", "inspect",
    )
    mutating_header = has_any(
        "mutating", "mutation", "action", "lifecycle", "write",
        "story", "iteration", "actions",
    )
    terminal_header = has_any(
        "terminal", "close", "closing", "finish",
    )
    missing: list[str] = []
    if not read_only_header:
        missing.append("read-only / query")
    if not mutating_header:
        missing.append("mutating / action / lifecycle")
    if not terminal_header:
        missing.append("terminal / close")
    assert not missing, (
        "_HELP_TEXT must include section headers for these buckets: "
        f"{missing!r}. Story 13 requires logical grouping in --help."
    )


def test_help_text_groups_are_in_canonical_order():
    """The buckets appear in this top-to-bottom order:
      1. Read-only (status)
      2. Mutating (ingest, decompose, sprint-cut, start, submit,
                   record-review, accept, reject, execute)
      3. Terminal (close, force-close)

    Pinned by comparing the line indices of the FIRST subcommand
    mentioned per bucket. read-only first-line < mutating first-line <
    terminal first-line.
    """
    read_first = min(
        (i for i in (_first_line_index_for(c) for c in READ_ONLY_COMMANDS)
         if i >= 0),
        default=-1,
    )
    mutating_first = min(
        (i for i in (_first_line_index_for(c) for c in MUTATING_COMMANDS)
         if i >= 0),
        default=-1,
    )
    terminal_first = min(
        (i for i in (_first_line_index_for(c) for c in TERMINAL_COMMANDS)
         if i >= 0),
        default=-1,
    )

    assert read_first >= 0, (
        "Read-only bucket missing entirely — `status` not in _HELP_TEXT."
    )
    assert mutating_first >= 0, (
        "Mutating bucket missing entirely — no mutating subcommand "
        "found in _HELP_TEXT."
    )
    assert terminal_first >= 0, (
        "Terminal bucket missing entirely — neither `close` nor "
        "`force-close` found in _HELP_TEXT."
    )

    assert read_first < mutating_first < terminal_first, (
        "Bucket order wrong. Story 13 requires:\n"
        "  Read-only (line {r}) < Mutating (line {m}) < "
        "Terminal (line {t}).".format(
            r=read_first, m=mutating_first, t=terminal_first,
        )
    )


def test_help_text_close_appears_after_mutating_commands():
    """`close` and `force-close` (terminal bucket) appear AFTER every
    mutating subcommand. Pinpoints the common refresh error of putting
    `close` in the middle of the mutating list.
    """
    terminal_first = min(
        (i for i in (_first_line_index_for(c) for c in TERMINAL_COMMANDS)
         if i >= 0),
        default=-1,
    )
    assert terminal_first >= 0, (
        "Terminal bucket missing — neither `close` nor `force-close` "
        "appears in _HELP_TEXT."
    )

    mutating_last = max(
        (_first_line_index_for(c) for c in MUTATING_COMMANDS),
        default=-1,
    )
    assert mutating_last >= 0, (
        "No mutating subcommands found in _HELP_TEXT."
    )

    assert mutating_last < terminal_first, (
        f"Terminal bucket (first line {terminal_first}) must come AFTER "
        f"the last mutating subcommand (line {mutating_last}). Story "
        f"13 grouping invariant."
    )


# ===========================================================================
# Category D — Drift-catching meta-tests (3 tests)
# ===========================================================================


def test_dispatcher_subcommand_set_matches_expected_12():
    """ANTI-LANE pin. Dynamically discovered subcommand set must equal
    exactly the 12 we know about today. If a Coder adds or removes a
    subcommand without updating Story 13's expectations, this test
    fails with a clear diff.

    Includes the 4 `_LIFECYCLE_TARGETS` keys + 8 single-name branches
    (decompose, sprint-cut, ingest, record-review, status, close,
    force-close, execute) = 12.
    """
    found = _dispatcher_subcommands()
    expected = set(EXPECTED_SUBCOMMANDS)

    extra = found - expected
    missing = expected - found
    assert not extra and not missing, (
        f"Dispatcher subcommand set drift. "
        f"Extra (in code but not in test expectations): {sorted(extra)!r}. "
        f"Missing (in test expectations but not in code): "
        f"{sorted(missing)!r}. Update EXPECTED_SUBCOMMANDS or revert "
        f"the dispatcher change."
    )


def test_lifecycle_targets_unchanged():
    """ANTI-LANE pin. `_LIFECYCLE_TARGETS` has exactly the 4 expected
    members. Story 13 is a docs-only refresh and must not perturb the
    dispatcher mapping.
    """
    import sm
    assert set(sm._LIFECYCLE_TARGETS.keys()) == {
        "start", "submit", "accept", "reject",
    }, (
        f"_LIFECYCLE_TARGETS keys drifted: "
        f"{sorted(sm._LIFECYCLE_TARGETS.keys())!r}. Story 13 must not "
        f"touch the lifecycle dispatch."
    )


def test_exit_code_constants_unchanged():
    """ANTI-LANE pin. The 12 EXIT_* constants have their expected
    numeric values. Story 13 is docs-only — Coders must not renumber
    codes while refreshing the help text.
    """
    import sm
    mismatches: list[str] = []
    for name, value in EXPECTED_EXIT_CODES:
        actual = getattr(sm, name, None)
        if actual != value:
            mismatches.append(f"{name}: expected {value}, got {actual!r}")
    assert not mismatches, (
        "EXIT_* constant values drifted: " + "; ".join(mismatches)
        + ". Story 13 is docs-only — do not renumber codes."
    )


# ===========================================================================
# Category E — CLI behavioral pin (3 tests)
# ===========================================================================


def test_cli_help_long_flag_prints_help_text():
    """`python -m sm --help` exits 0 and stdout equals `_HELP_TEXT`
    (subject to trailing newline normalization).
    """
    import sm

    result = _run_cli("--help")
    assert result.returncode == 0, (
        f"`python -m sm --help` must exit 0; got returncode="
        f"{result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    # _HELP_TEXT triple-quoted string includes a trailing newline. The
    # CLI uses `print(_HELP_TEXT)` which appends another. Accept either
    # exact match or text-stripped equality.
    stdout = result.stdout
    expected = sm._HELP_TEXT
    assert stdout == expected or stdout == expected + "\n" \
        or stdout.rstrip() == expected.rstrip(), (
            "`python -m sm --help` stdout must equal _HELP_TEXT.\n"
            f"--- stdout ---\n{stdout!r}\n--- expected _HELP_TEXT ---\n"
            f"{expected!r}"
        )


def test_cli_help_short_flag_prints_help_text():
    """`python -m sm -h` is equivalent to `--help`: exits 0 and prints
    `_HELP_TEXT` to stdout.
    """
    import sm

    result = _run_cli("-h")
    assert result.returncode == 0, (
        f"`python -m sm -h` must exit 0; got returncode="
        f"{result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    stdout = result.stdout
    expected = sm._HELP_TEXT
    assert stdout == expected or stdout == expected + "\n" \
        or stdout.rstrip() == expected.rstrip(), (
            "`python -m sm -h` stdout must equal _HELP_TEXT.\n"
            f"--- stdout ---\n{stdout!r}\n--- expected _HELP_TEXT ---\n"
            f"{expected!r}"
        )


def test_cli_help_mentions_every_subcommand_via_subprocess():
    """End-to-end pin: every one of the 12 registered subcommands is
    visible in the `python -m sm --help` output. This is the test that
    catches a stale build / installed-vs-source mismatch — the source
    `_HELP_TEXT` could be updated while the user gets the old text.
    """
    result = _run_cli("--help")
    assert result.returncode == 0, (
        f"`python -m sm --help` must exit 0; got returncode="
        f"{result.returncode}"
    )
    out = result.stdout
    missing: list[str] = []
    for cmd in EXPECTED_SUBCOMMANDS:
        pattern = (
            r"(?<![A-Za-z0-9_-])" + re.escape(cmd) + r"(?![A-Za-z0-9_-])"
        )
        if not re.search(pattern, out):
            missing.append(cmd)
    assert not missing, (
        f"`python -m sm --help` output is missing these subcommands: "
        f"{missing!r}. Story 13: refreshed help must list all 12."
    )
