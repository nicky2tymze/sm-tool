"""sm-tool — scrum-master pipeline (skeleton).

Iteration 1 lives here. The skeleton defines the module shape and the
LOG_PATH constant; behavior lands in subsequent stories.

Stdlib only; Python 3.10+.
"""

from __future__ import annotations

import copy as _copy
import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Iterator

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

        elif etype == "story_decomposed":
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

    # --- Duplicate iteration_id check (Story 6).
    # Scan ALL prior `iteration_open` entries — including ones that have
    # since been closed or force-closed. This is independent of (and runs
    # before) the single-active check: a duplicate is a duplicate even
    # if nothing is currently open. Pure read of the log; no write.
    for prior in read_entries():
        if (prior.get("type") == "iteration_open"
                and prior.get("iteration_id") == iter_id):
            raise IngestDuplicateError(
                f"cannot ingest: iteration_id {iter_id!r} was already "
                f"used by a prior iteration_open entry"
            )

    # --- Single-active-iteration enforcement (via derive_state) ---
    state = derive_state()
    if state["active_iteration"] is not None:
        open_id = state["active_iteration"]["iteration_id"]
        raise IngestActiveError(
            f"cannot ingest: iteration {open_id!r} is already open"
        )

    # --- All validation passed; build + append ---
    entry = build_entry("iteration_open", handoff)
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
    import os
    import sys as _sys

    if len(argv) < 1:
        print(_HELP_TEXT, file=_sys.stderr)
        return EXIT_OTHER

    cmd = argv[0]

    if cmd in ("--help", "-h", "help"):
        print(_HELP_TEXT)
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
            global LOG_PATH
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
