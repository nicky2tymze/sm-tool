"""sm-tool — scrum-master pipeline (skeleton).

Iteration 1 lives here. The skeleton defines the module shape and the
LOG_PATH constant; behavior lands in subsequent stories.

Stdlib only; Python 3.10+.
"""

from __future__ import annotations

import copy as _copy
import datetime as _dt
import hashlib as _hashlib
import json
import uuid
from pathlib import Path
from typing import Callable, Iterator, Optional

_CANONICAL_ROLES = ("sm_agent", "test_writer", "coder", "reviewer")

LOG_PATH: Path = Path(__file__).resolve().parent / "log.jsonl"

_RESERVED_KEYS = ("id", "type", "timestamp")

_TERMINAL_STATES = frozenset({"accepted", "rejected", "force_closed"})
_VALID_TRANSITIONS: dict = {
    "planned": frozenset({"in_progress", "force_closed"}),
    "in_progress": frozenset({"in_review", "force_closed"}),
    "in_review": frozenset({"accepted", "rejected", "force_closed"}),
    "accepted": frozenset(),
    "rejected": frozenset(),
    "force_closed": frozenset(),
}

__all__ = [
    "LOG_PATH",
    "build_entry",
    "read_entries",
    "derive_state",
    "ingest",
    "IngestJSONError",
    "IngestShapeError",
    "IngestDuplicateError",
    "IngestActiveError",
    "resolve_role_spec",
    "RoleSpecNotFoundError",
    "decompose",
    "DecomposeOutputParseError",
    "DecomposeOutputShapeError",
    "DecomposeUnknownRequirementError",
]


# ---------------------------------------------------------------------------
# Story 6 — typed ingest errors. Each subclass narrows ValueError so the
# existing `pytest.raises(ValueError)` callers keep working, while the CLI
# can map the class to a distinct exit code (see `_cli_main`).
# ---------------------------------------------------------------------------

class IngestJSONError(ValueError):
    """Handoff file is not valid JSON (malformed / empty)."""


class IngestShapeError(ValueError):
    """Handoff JSON is well-formed but does not match the required shape
    (missing/wrong-typed top-level fields, bad/duplicate requirements)."""


class IngestDuplicateError(ValueError):
    """The handoff's iteration_id matches a prior `iteration_open` entry —
    open OR closed. Distinct from `IngestActiveError`, which fires only
    while another iteration is currently open."""


class IngestActiveError(ValueError):
    """An iteration is currently open; cannot ingest a new handoff until
    it is closed."""


class RoleSpecNotFoundError(FileNotFoundError):
    """Raised when a canonical role-spec file does not exist on disk at the
    resolved path. Subclasses FileNotFoundError so existing
    `except FileNotFoundError` callers keep working."""


# ---------------------------------------------------------------------------
# Story 9 — typed decompose errors. Both narrow ValueError so existing
# `except ValueError` callers keep working, while distinguishing the parse
# failure mode (agent output isn't valid JSON) from the shape failure mode
# (JSON parsed but doesn't match the required schema).
# ---------------------------------------------------------------------------

class DecomposeAgentError(RuntimeError):
    """Raised when spawn_agent itself errors and the failure should be
    surfaced as a typed decompose-domain exception. Currently unused by the
    happy-path implementation (which lets the agent's exception propagate
    verbatim); reserved for future wrapping behavior."""


class DecomposeOutputParseError(ValueError):
    """The agent returned output that is not valid JSON."""


class DecomposeOutputShapeError(ValueError):
    """The agent's JSON parsed cleanly, but does not match the required
    story-backlog schema (missing keys, wrong types, bad sizes, non-1..N
    sequences, etc.)."""


class DecomposeUnknownRequirementError(ValueError):
    """A story's `requirement_ids` references an id that does not appear
    in the active iteration's handoff requirements list. Distinct from
    `DecomposeOutputShapeError` — both subclass ValueError so existing
    `except ValueError` callers keep working, but callers can branch on
    the exact class for cross-reference vs shape failures."""


def resolve_role_spec(role: str) -> Path:
    """Resolve the absolute path to a canonical role-spec markdown file.

    Returns an absolute pathlib.Path to `<package_dir>/roles/<role>.md`,
    where `<package_dir>` is `LOG_PATH.parent` (the same anchor used for
    log lookup, so monkeypatching LOG_PATH redirects role-spec lookup
    consistently with the rest of the suite).

    Validation:
      - `role` must be a `str`. Non-string raises TypeError naming the
        class of the bad value.
      - `role` must be non-empty and not whitespace-only. Empty/blank
        raises ValueError.
      - `role` must be one of the four canonical names. Anything else
        raises ValueError naming the offending string.

    If the resolved path does not exist on disk, raises
    `RoleSpecNotFoundError` (a FileNotFoundError subclass) naming the role.
    """
    if not isinstance(role, str):
        raise TypeError(
            f"role must be a string, got {role.__class__.__name__}"
        )
    if not role or not role.strip():
        raise ValueError("role must be a non-empty, non-whitespace string")
    if role not in _CANONICAL_ROLES:
        raise ValueError(
            f"unknown role {role!r}; valid roles are {_CANONICAL_ROLES!r}"
        )

    # Anchor at LOG_PATH.parent so monkeypatching LOG_PATH redirects
    # role-spec lookup the same way it redirects log lookup. Resolve to
    # absolute so the returned Path is always absolute, even when LOG_PATH
    # is set to a relative path.
    package_dir = Path(LOG_PATH).resolve().parent
    spec_path = (package_dir / "roles" / f"{role}.md").resolve()

    if not spec_path.is_file():
        raise RoleSpecNotFoundError(
            f"role-spec file for role {role!r} not found at {spec_path!s}"
        )

    return spec_path


def _role_spec_hash(role: str) -> str:
    """Return the SHA-256 hex digest of the role-spec file's bytes.

    Validation flows through `resolve_role_spec` — unknown / empty /
    non-string roles raise the same errors, and a missing file raises
    `RoleSpecNotFoundError`. Same role + same bytes -> same digest.
    """
    spec_path = resolve_role_spec(role)
    return _hashlib.sha256(spec_path.read_bytes()).hexdigest()


def _append_entry(entry: dict) -> None:
    """Append one JSON object as a single LF-terminated line to LOG_PATH."""
    if not isinstance(entry, dict):
        raise TypeError(
            f"_append_entry requires a dict, got {type(entry).__name__}"
        )
    json_line = json.dumps(entry, ensure_ascii=False)
    with open(LOG_PATH, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json_line + "\n")
        fh.flush()


def read_entries() -> Iterator[dict]:
    """Yield each line of `LOG_PATH` parsed as a dict, in file order.

    Returns an empty iterator if the log is missing or zero-byte. Raises
    `ValueError` naming the 1-based line number if any line is malformed
    (invalid JSON, blank/whitespace-only, or a top-level non-dict value).
    CRLF line endings are tolerated; the trailing `\\r` is stripped before
    parsing.
    """
    # Resolve LOG_PATH at call-time so monkeypatching `sm.LOG_PATH` works.
    log_path = LOG_PATH

    if not log_path.exists():
        return
    if log_path.stat().st_size == 0:
        return

    with open(log_path, "r", encoding="utf-8", newline="") as fh:
        for line_no, raw in enumerate(fh, start=1):
            # Strip a single trailing newline (LF or CRLF). Do NOT strip
            # other whitespace — blank/whitespace-only lines must raise.
            if raw.endswith("\r\n"):
                line = raw[:-2]
            elif raw.endswith("\n"):
                line = raw[:-1]
            else:
                # Final line with no trailing LF.
                line = raw
            # Tolerate a stray trailing \r (e.g. mixed/odd line endings).
            if line.endswith("\r"):
                line = line[:-1]

            if not line or not line.strip():
                raise ValueError(
                    f"Malformed log entry on line {line_no}: blank or "
                    f"whitespace-only line"
                )

            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Malformed log entry on line {line_no}: {e.msg}"
                ) from e

            if not isinstance(obj, dict):
                raise ValueError(
                    f"Malformed log entry on line {line_no}: top-level "
                    f"value must be a JSON object, got "
                    f"{type(obj).__name__}"
                )

            yield obj


def build_entry(type: str, content: dict) -> dict:
    """Build a canonical log entry dict from a `type` and a `content` payload.

    Returns a new dict whose first three keys are auto-stamped — `id` (a fresh
    32-char lowercase-hex uuid4), `type` (the param verbatim), `timestamp`
    (ISO 8601 with local timezone offset, via
    `datetime.now().astimezone().isoformat()`) — followed by the content
    fields in their original insertion order, merged at the top level.

    Validation:
      - `type` must be a non-empty, non-whitespace-only `str`. Non-string
        raises `TypeError`; empty/whitespace-only raises `ValueError`.
      - `content` must be a `dict` (or dict subclass). Other types raise
        `TypeError`. Empty dict is accepted.
      - `content` must not contain top-level keys `'id'`, `'type'`, or
        `'timestamp'` (case-sensitive — `'ID'`, `'Type'` are allowed; nested
        keys are not flagged). Violation raises `ValueError` naming the
        offending key.

    The returned dict is a fresh object — mutating it does not affect the
    input, and mutating the input after the call does not affect the result.
    """
    # NOTE: the `type` parameter shadows the builtin inside this function.
    # Use `.__class__.__name__` to format type names in error messages —
    # never call `type(x)`.

    # --- Validate `type` parameter ---
    # Reject bool explicitly: bool is not a str subclass, so isinstance check
    # below already covers it. Strict str-only.
    if not isinstance(type, str):
        raise TypeError(
            f"type must be a string, got {type.__class__.__name__}"
        )
    if not type or not type.strip():
        raise ValueError("type must be a non-empty, non-whitespace string")

    # --- Validate `content` parameter ---
    if not isinstance(content, dict):
        raise TypeError(
            f"content must be a dict, got {content.__class__.__name__}"
        )

    # --- Reserved-key check (case-sensitive, top-level only) ---
    for k in _RESERVED_KEYS:
        if k in content:
            raise ValueError(
                f"content must not contain reserved key {k!r}"
            )

    # --- Build the result dict (auto-stamped fields first, then content) ---
    result: dict = {
        "id": uuid.uuid4().hex,
        "type": type,
        "timestamp": _dt.datetime.now().astimezone().isoformat(),
    }
    for k, v in content.items():
        result[k] = v
    return result


def derive_state() -> dict:
    """Replay the full event log and return the derived current state.

    Pure read: log bytes are not modified, no sidecar files written. Two
    consecutive calls produce equal (and independent) results.

    Returns a dict with five top-level keys:
      - active_iteration: dict {iteration_id, requirements: [...]} or None
      - story_backlog:    list[dict] of story records, ordered by `sequence`
      - sprint_cut:       int or None (latest sprint_cut entry wins)
      - story_states:     dict {story_id: state} where state is one of
                          {planned, in_progress, in_review, accepted,
                           rejected, force_closed}
      - close_status:     dict {closed_by, reason, accepted_count,
                          rejected_count, force_closed_count} or None
                          (cleared to None on a new iteration_open)

    Raises ValueError naming the offending entry id when:
      - a state_change targets an unknown story_id
      - a state_change is an illegal lifecycle transition
      - a second iteration_open lands with no intervening iteration_close

    Unknown entry types are no-ops (forward-compatibility).
    """
    state: dict = {
        "active_iteration": None,
        "story_backlog": [],
        "sprint_cut": None,
        "story_states": {},
        "close_status": None,
    }

    for entry in read_entries():
        etype = entry.get("type")
        eid = entry.get("id")

        if etype == "iteration_open":
            if state["active_iteration"] is not None:
                raise ValueError(
                    f"iteration_open while another iteration is already "
                    f"open (entry id {eid!r})"
                )
            state["active_iteration"] = {
                "iteration_id": entry.get("iteration_id"),
                "requirements": list(entry.get("requirements", [])),
            }
            state["close_status"] = None  # clear on new open

        elif etype == "iteration_close":
            state["active_iteration"] = None
            state["close_status"] = {
                "closed_by": entry.get("closed_by"),
                "reason": entry.get("reason"),
                "accepted_count": entry.get("accepted_count", 0),
                "rejected_count": entry.get("rejected_count", 0),
                "force_closed_count": entry.get("force_closed_count", 0),
            }

        elif etype == "story_decomposed" or etype == "story_backlog":
            stories = entry.get("stories", [])
            new_backlog = sorted(
                (_copy.deepcopy(s) for s in stories),
                key=lambda s: s["sequence"],
            )
            state["story_backlog"] = new_backlog
            state["story_states"] = {
                s["story_id"]: "planned" for s in new_backlog
            }

        elif etype == "sprint_cut":
            state["sprint_cut"] = entry.get("cut_position")

        elif etype == "story_state_change":
            sid = entry.get("story_id")
            to_state = entry.get("to_state")
            if sid not in state["story_states"]:
                raise ValueError(
                    f"story_state_change for unknown story_id {sid!r} "
                    f"(entry id {eid!r})"
                )
            current = state["story_states"][sid]
            allowed = _VALID_TRANSITIONS.get(current, frozenset())
            if to_state not in allowed:
                raise ValueError(
                    f"illegal story state transition from {current!r} to "
                    f"{to_state!r} for story {sid!r} (entry id {eid!r})"
                )
            state["story_states"][sid] = to_state

        # Unknown entry types: no-op (forward-compat).

    return state


def ingest(path) -> dict:
    """Ingest a PO Tool iteration-open handoff JSON file at `path`.

    Reads + validates the handoff, then writes a single `iteration_open`
    log entry via the canonical `build_entry` + `_append_entry` path.
    Returns the appended entry dict.

    Validation failures raise `ValueError` (with no log write). Filesystem
    errors are stdlib-canonical: missing path → `FileNotFoundError`,
    directory path → `IsADirectoryError`.

    Accepts either `str` or `pathlib.Path`. Failure invariant: log.jsonl
    is byte-for-byte unchanged on any validation/parse/IO failure.
    """
    p = Path(path)

    # --- Filesystem checks (stdlib-canonical errors) ---
    if not p.exists():
        raise FileNotFoundError(f"handoff file not found: {p!s}")
    if p.is_dir():
        raise IsADirectoryError(f"handoff path is a directory: {p!s}")

    # --- Read + parse JSON ---
    raw = p.read_text(encoding="utf-8")
    try:
        handoff = json.loads(raw)
    except json.JSONDecodeError as e:
        raise IngestJSONError(
            f"handoff file is not valid JSON: {e.msg}"
        ) from e

    # --- Top-level shape ---
    if not isinstance(handoff, dict):
        raise IngestShapeError(
            f"handoff top-level must be a JSON object, got "
            f"{handoff.__class__.__name__}"
        )

    # iteration_id
    if "iteration_id" not in handoff:
        raise IngestShapeError(
            "handoff missing required field 'iteration_id'"
        )
    iter_id = handoff["iteration_id"]
    if not isinstance(iter_id, str) or not iter_id.strip():
        raise IngestShapeError(
            "handoff 'iteration_id' must be a non-empty string"
        )

    # requirements
    if "requirements" not in handoff:
        raise IngestShapeError(
            "handoff missing required field 'requirements'"
        )
    reqs = handoff["requirements"]
    if not isinstance(reqs, list):
        raise IngestShapeError(
            f"handoff 'requirements' must be a list, got "
            f"{reqs.__class__.__name__}"
        )
    if len(reqs) == 0:
        raise IngestShapeError(
            "handoff 'requirements' must not be empty"
        )

    # Per-requirement validation + duplicate-id check
    seen_ids: dict = {}
    for i, req in enumerate(reqs):
        if not isinstance(req, dict):
            raise IngestShapeError(
                f"handoff 'requirements'[{i}] must be a dict, got "
                f"{req.__class__.__name__}"
            )
        if "requirement_id" not in req:
            raise IngestShapeError(
                f"handoff 'requirements'[{i}] missing required field "
                f"'requirement_id'"
            )
        rid = req["requirement_id"]
        if not isinstance(rid, str) or not rid.strip():
            raise IngestShapeError(
                f"handoff 'requirements'[{i}] 'requirement_id' must be a "
                f"non-empty string"
            )
        if rid in seen_ids:
            raise IngestShapeError(
                f"handoff 'requirements' contains duplicate "
                f"requirement_id {rid!r}"
            )
        seen_ids[rid] = i

    # --- Single-active-iteration enforcement (via derive_state).
    # Story 7 precedence: this check fires BEFORE the dup-id check. When
    # both would fire (i.e., the new handoff's iteration_id matches the
    # currently-open iteration), the operator gets the actionable
    # "close it first" message rather than the cosmetic dup-id one.
    state = derive_state()
    if state["active_iteration"] is not None:
        open_id = state["active_iteration"]["iteration_id"]
        raise IngestActiveError(
            f"cannot ingest: iteration {open_id!r} is already open; "
            f"close before re-ingesting"
        )

    # --- Duplicate iteration_id check (Story 6).
    # Scan ALL prior `iteration_open` entries — including ones that have
    # since been closed or force-closed. With Story 7's precedence flip,
    # this only fires when nothing is currently open AND the new id was
    # used by a prior (now-closed) iteration. Pure read of the log; no write.
    for prior in read_entries():
        if (prior.get("type") == "iteration_open"
                and prior.get("iteration_id") == iter_id):
            raise IngestDuplicateError(
                f"cannot ingest: iteration_id {iter_id!r} was already "
                f"used by a prior iteration_open entry"
            )

    # --- All validation passed; build + append ---
    entry = build_entry("iteration_open", handoff)
    _append_entry(entry)
    return entry


def decompose(spawn_agent: Optional[Callable] = None) -> dict:
    """Spawn an SM Agent (or an injected stub) to decompose the active
    iteration's requirements into a sequence of stories, then write a single
    `story_backlog` log entry on success.

    Story 9 contract:

      - `spawn_agent` defaults to `None`; passing `None` (explicit or
        implicit) raises `NotImplementedError` mentioning Iter 2.
        Operators / tests inject a callable to drive the function in Iter 1.

      - Reads the active iteration via `derive_state()`. No active
        iteration → `ValueError("no active iteration; ingest a handoff
        first")`. No log write.

      - Resolves the SM Agent role-spec via `resolve_role_spec("sm_agent")`
        and computes the role-spec hash via `_role_spec_hash("sm_agent")`.

      - Calls `spawn_agent(role_spec_path: str, requirements: list[dict])`
        synchronously (blocks until the agent returns).

      - Parses the agent's JSON output. Parse failure raises
        `DecomposeOutputParseError`. Shape failure raises
        `DecomposeOutputShapeError`. Either way: NO log write. If the
        spawn_agent callable itself raises, that exception propagates
        verbatim and the log is unchanged.

      - On success: assigns each story a fresh uuid4-hex `story_id` (the
        operator's job, not the agent's — any agent-supplied `story_id` is
        overridden), then writes a single `story_backlog` log entry via
        `build_entry` + `_append_entry`. Returns the entry dict.
    """
    if spawn_agent is None:
        raise NotImplementedError(
            "real agent integration ships in Iter 2 — pass spawn_agent= "
            "for testing/manual ops"
        )

    state = derive_state()
    if state["active_iteration"] is None:
        raise ValueError("no active iteration; ingest a handoff first")

    iteration = state["active_iteration"]
    requirements = iteration["requirements"]

    role_spec_path = resolve_role_spec("sm_agent")
    role_spec_hash = _role_spec_hash("sm_agent")

    # Synchronous call — blocks until the agent returns. Any exception the
    # callable raises propagates verbatim (no log write).
    output_str = spawn_agent(str(role_spec_path), requirements)

    # --- Parse JSON ---
    try:
        output = json.loads(output_str)
    except (json.JSONDecodeError, TypeError) as e:
        raise DecomposeOutputParseError(
            f"agent output is not valid JSON: {e}"
        ) from e

    # --- Validate top-level shape ---
    if not isinstance(output, dict):
        raise DecomposeOutputShapeError(
            f"agent output must be a JSON object, got "
            f"{output.__class__.__name__}"
        )
    if "stories" not in output:
        raise DecomposeOutputShapeError(
            "agent output missing required 'stories' key"
        )
    stories = output["stories"]
    if not isinstance(stories, list):
        raise DecomposeOutputShapeError(
            f"'stories' must be a list, got "
            f"{stories.__class__.__name__}"
        )
    if len(stories) == 0:
        raise DecomposeOutputShapeError("'stories' must be non-empty")

    REQUIRED_FIELDS = (
        "sequence",
        "title",
        "size",
        "requirement_ids",
        "acceptance_criteria",
    )
    VALID_SIZES = {"S", "M", "L"}

    # --- Per-story shape validation ---
    for idx, s in enumerate(stories):
        if not isinstance(s, dict):
            raise DecomposeOutputShapeError(
                f"story at index {idx} must be a dict, got "
                f"{s.__class__.__name__}"
            )
        for field in REQUIRED_FIELDS:
            if field not in s:
                raise DecomposeOutputShapeError(
                    f"story at index {idx} missing required field "
                    f"{field!r}"
                )
        # size validation
        if s["size"] not in VALID_SIZES:
            raise DecomposeOutputShapeError(
                f"story at index {idx} has invalid size {s['size']!r}; "
                f"must be one of {sorted(VALID_SIZES)!r}"
            )
        # requirement_ids validation
        rids = s["requirement_ids"]
        if not isinstance(rids, list):
            raise DecomposeOutputShapeError(
                f"story at index {idx} requirement_ids must be a list, "
                f"got {rids.__class__.__name__}"
            )
        if len(rids) == 0:
            raise DecomposeOutputShapeError(
                f"story at index {idx} requirement_ids must be non-empty"
            )
        if not all(isinstance(r, str) for r in rids):
            raise DecomposeOutputShapeError(
                f"story at index {idx} requirement_ids must be a list of "
                f"strings"
            )
        # acceptance_criteria non-empty after strip (Story 10 tightening).
        # Story 9 already requires the field be present; Story 10 adds the
        # rule that whitespace-only AC (incl. empty string, tabs, newlines)
        # is a shape error — the field must carry substance.
        ac_val = s["acceptance_criteria"]
        if not isinstance(ac_val, str) or not ac_val.strip():
            seq_marker = s.get("sequence", idx + 1)
            title_marker = s.get("title", f"index {idx}")
            raise DecomposeOutputShapeError(
                f"story {seq_marker!r} ({title_marker!r}) field "
                f"'acceptance_criteria' must be a non-empty, "
                f"non-whitespace-only string"
            )

    # --- Sequence validation: must be exactly 1..N strictly increasing ---
    sequences = [s["sequence"] for s in stories]
    expected = list(range(1, len(stories) + 1))
    if sequences != expected:
        raise DecomposeOutputShapeError(
            f"sequences must be strictly 1..N, got {sequences!r} "
            f"(expected {expected!r})"
        )

    # --- Cross-reference check (Story 10): every requirement_id in every
    # story must appear in the active iteration's handoff requirements
    # list. Unknown ids → DecomposeUnknownRequirementError (distinct from
    # shape errors). Runs AFTER shape validation (so we know stories have
    # the right shape) and BEFORE story_id minting + log write (so any
    # failure leaves the log byte-for-byte unchanged).
    valid_ids = {
        r["requirement_id"]
        for r in requirements
        if isinstance(r, dict) and "requirement_id" in r
    }
    for s in stories:
        for rid in s["requirement_ids"]:
            if rid not in valid_ids:
                sequence = s.get("sequence")
                title = s.get("title")
                raise DecomposeUnknownRequirementError(
                    f"story {sequence!r} ({title!r}) references unknown "
                    f"requirement_id {rid!r}; valid ids are "
                    f"{sorted(valid_ids)!r}"
                )

    # --- Mint story_ids (override any agent-supplied id) ---
    enriched_stories = []
    for s in stories:
        new_s = dict(s)
        new_s["story_id"] = uuid.uuid4().hex
        enriched_stories.append(new_s)

    # --- Build + append the entry ---
    entry = build_entry(
        "story_backlog",
        {
            "stories": enriched_stories,
            "role_spec_path": str(role_spec_path),
            "role_spec_hash": role_spec_hash,
        },
    )
    _append_entry(entry)
    return entry


# ---------------------------------------------------------------------------
# CLI surface — `python -m sm <command> <args...>`
# ---------------------------------------------------------------------------

# Story 6 — documented CLI exit codes. Exposed so callers and docs can
# reference them by name. Every error class maps to exactly one code,
# distinct from every other class and from success (0).
EXIT_OK = 0
EXIT_OTHER = 1
EXIT_PATH = 2
EXIT_JSON = 3
EXIT_SHAPE = 4
EXIT_DUP_ID = 5
EXIT_SINGLE_ACTIVE = 6
EXIT_UNKNOWN_REQ = 7


_HELP_TEXT = """\
usage: python -m sm <command> [args...]

Commands:
  ingest <path>    Ingest a PO Tool iteration_open handoff JSON file.

Exit codes (return codes) for `ingest`:
  0  success
  1  unexpected / other error
  2  path error           (file not found, path is a directory)
  3  JSON parse error     (malformed or empty handoff JSON)
  4  shape error          (handoff missing/wrong-typed fields, bad reqs)
  5  duplicate iteration_id (id was used by a prior iteration_open,
                             open or closed)
  6  single-active-iteration violation (another iteration is open)
"""


def _cli_main(argv: list) -> int:
    """Dispatch CLI subcommands. Returns the exit code.

    Story 6 — distinct exit codes per error class:
        0 success, 1 other, 2 path, 3 json, 4 shape, 5 dup-id,
        6 single-active.
    """
    global LOG_PATH

    import os
    import sys as _sys

    if len(argv) < 1:
        print(_HELP_TEXT, file=_sys.stderr)
        return EXIT_OTHER

    cmd = argv[0]

    if cmd in ("--help", "-h", "help"):
        print(_HELP_TEXT)
        return EXIT_OK

    if cmd == "decompose":
        # Honor SM_LOG_PATH for hermetic subprocess testing.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        try:
            entry = decompose()
        except NotImplementedError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except DecomposeOutputParseError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_JSON
        except DecomposeOutputShapeError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_SHAPE
        except DecomposeUnknownRequirementError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_UNKNOWN_REQ
        except ValueError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        print(entry["id"])
        return EXIT_OK

    if cmd == "ingest":
        if len(argv) >= 2 and argv[1] in ("--help", "-h"):
            print(_HELP_TEXT)
            return EXIT_OK
        if len(argv) != 2:
            print("usage: python -m sm ingest <path>", file=_sys.stderr)
            print(_HELP_TEXT, file=_sys.stderr)
            return EXIT_OTHER

        # Honor SM_LOG_PATH env var if set, so subprocess CLI tests stay
        # hermetic and the package's real log isn't touched.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        try:
            entry = ingest(argv[1])
        except IngestDuplicateError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_DUP_ID
        except IngestActiveError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_SINGLE_ACTIVE
        except IngestShapeError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_SHAPE
        except IngestJSONError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_JSON
        except (FileNotFoundError, IsADirectoryError) as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_PATH
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        print(entry["iteration_id"])
        return EXIT_OK

    print(f"unknown command: {cmd!r}", file=_sys.stderr)
    print(_HELP_TEXT, file=_sys.stderr)
    return EXIT_OTHER


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli_main(_sys.argv[1:]))
