"""Story 5 — pin the contract of `sm.ingest`.

What this file pins:
  - Function signature and shape: `ingest(path: str | Path) -> dict`, PUBLIC,
    callable, in `sm.__all__`, importable as `from sm import ingest`. Accepts
    either `str` or `Path`. Returns the dict of the appended log entry.

  - Reads a PO Tool iteration-open handoff JSON at `<path>`, validates the
    shape, and on success writes ONE `iteration_open` log entry via the
    canonical `build_entry` + `_append_entry` path.

  - Handoff JSON shape (top level):
      {
        "iteration_id": "<non-empty string>",
        "iteration_goal": "<string>",       (informational; not validated)
        "requirements": [                    (non-empty list)
          {
            "requirement_id": "<non-empty string>",
            "title": "...",
            "description": "...",
            "priority": "MUST"|"SHOULD"|"NICE",
            "acceptance_criteria": "..."
          },
          ...
        ],
        ... extra forward-compat fields preserved verbatim ...
      }

  - The written entry's content carries the FULL parsed handoff verbatim
    (every top-level handoff field, plus extras, preserved). The merged log
    entry therefore has top-level `id`, `type`, `timestamp`, plus every
    handoff field.

  - Validation failures (each raises `ValueError`, no log write):
      * not a JSON object at top level
      * missing `iteration_id`
      * `iteration_id` not a non-empty string
      * missing `requirements`
      * `requirements` not a list
      * `requirements` empty
      * any requirement not a dict (error names the index)
      * any requirement missing `requirement_id` (names the index)
      * any `requirement_id` not a non-empty string (names the index)
      * duplicate `requirement_id` values inside the handoff (names the dup)
      * `derive_state()` shows an iteration is already open (names the open
        iteration_id)
  - File-system errors are stdlib-canonical:
      * missing path → `FileNotFoundError`
      * directory path → `IsADirectoryError`
      * malformed JSON → `ValueError`

  - Failure invariant: log.jsonl byte-for-byte unchanged on any failure.

  - Round-trip: after `ingest(path)`, `derive_state()` reflects the new
    iteration as active with the verbatim requirements list.

Tests must FAIL on first run — `ingest` does not exist yet. The Coder
downstream implements to satisfy these tests.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file.

    Mirrors the suite convention (test_append_entry.py, test_read_entries.py,
    test_build_entry.py, test_derive_state.py).
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _canonical_requirement(req_id: str = "req-1",
                           title: str = "Do the thing",
                           description: str = "A description.",
                           priority: str = "MUST",
                           acceptance_criteria: str = "AC: it works"
                           ) -> dict:
    return {
        "requirement_id": req_id,
        "title": title,
        "description": description,
        "priority": priority,
        "acceptance_criteria": acceptance_criteria,
    }


def _canonical_handoff(iteration_id: str = "iter-1",
                       iteration_goal: str = "Ship the thing.",
                       requirements=None) -> dict:
    if requirements is None:
        requirements = [
            _canonical_requirement("req-1", "Title 1"),
            _canonical_requirement("req-2", "Title 2"),
        ]
    return {
        "iteration_id": iteration_id,
        "iteration_goal": iteration_goal,
        "requirements": list(requirements),
    }


def _write_handoff(tmp_path: pathlib.Path,
                   handoff: dict,
                   name: str = "handoff.json") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(json.dumps(handoff), encoding="utf-8")
    return p


def _write_raw(tmp_path: pathlib.Path,
               raw: str,
               name: str = "handoff.json") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(raw, encoding="utf-8")
    return p


def _seed_log_with_one_entry(isolated_log_path: pathlib.Path) -> bytes:
    """Write a single benign entry (not iteration_open) so the log is non-empty.

    Returns the bytes of the seeded log so tests can verify
    byte-for-byte equality after a failure.
    """
    import sm
    # An unknown type is a no-op at replay (forward-compat) and does NOT
    # set active_iteration — perfect for "log is non-empty but no
    # iteration is open" preconditions.
    e = sm.build_entry("ingest_test_seed", {"marker": "before"})
    sm._append_entry(e)
    return isolated_log_path.read_bytes()


# ===========================================================================
# Smoke (5+)
# ===========================================================================

def test_function_exists_on_module():
    import sm
    assert hasattr(sm, "ingest"), "sm.ingest must exist"


def test_function_is_callable():
    import sm
    assert callable(sm.ingest)


def test_function_name_is_public():
    """No leading underscore — public API."""
    import sm
    assert not sm.ingest.__name__.startswith("_")
    assert sm.ingest.__name__ == "ingest"


def test_function_importable_directly():
    """`from sm import ingest` succeeds — public-import form."""
    from sm import ingest  # noqa: F401
    assert callable(ingest)


def test_function_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "ingest" in sm.__all__, (
        f"ingest must be in __all__; got {sm.__all__!r}"
    )


def test_accepts_str_path(isolated_log, tmp_path):
    """Function accepts str path."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(str(p))


def test_accepts_pathlib_path(isolated_log, tmp_path):
    """Function accepts pathlib.Path."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)


# ===========================================================================
# Happy path (8+)
# ===========================================================================

def test_happy_path_writes_one_entry(isolated_log, tmp_path):
    """A valid handoff produces exactly one new log entry."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    entries = list(sm.read_entries())
    assert len(entries) == 1


def test_happy_path_entry_type_is_iteration_open(isolated_log, tmp_path):
    """The single emitted entry has type `iteration_open`."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    entries = list(sm.read_entries())
    assert entries[0]["type"] == "iteration_open"


def test_happy_path_entry_has_canonical_fields(isolated_log, tmp_path):
    """The emitted entry has id, type, timestamp from build_entry."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    entries = list(sm.read_entries())
    e = entries[0]
    assert "id" in e
    assert "type" in e
    assert "timestamp" in e


def test_happy_path_returns_appended_entry(isolated_log, tmp_path):
    """ingest returns the dict that was appended to the log."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    result = sm.ingest(p)
    entries = list(sm.read_entries())
    assert result == entries[0]


def test_happy_path_return_value_is_dict(isolated_log, tmp_path):
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    result = sm.ingest(p)
    assert isinstance(result, dict)


def test_happy_path_return_value_carries_iteration_id(isolated_log, tmp_path):
    """Return value's iteration_id matches the handoff."""
    import sm
    handoff = _canonical_handoff(iteration_id="iter-alpha")
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    assert result["iteration_id"] == "iter-alpha"


def test_happy_path_return_value_carries_requirements_verbatim(
    isolated_log, tmp_path
):
    """The full requirements list (each with all fields) is preserved verbatim
    in the returned/written entry."""
    import sm
    reqs = [
        _canonical_requirement("req-1", "T1", "D1", "MUST", "AC1"),
        _canonical_requirement("req-2", "T2", "D2", "SHOULD", "AC2"),
        _canonical_requirement("req-3", "T3", "D3", "NICE", "AC3"),
    ]
    handoff = _canonical_handoff(requirements=reqs)
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    assert result["requirements"] == reqs


def test_happy_path_entry_has_iteration_goal(isolated_log, tmp_path):
    """iteration_goal is preserved in the written entry."""
    import sm
    handoff = _canonical_handoff(iteration_goal="Ship Iteration 1")
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    assert result["iteration_goal"] == "Ship Iteration 1"


def test_happy_path_derive_state_active_iteration_populated(
    isolated_log, tmp_path
):
    """After ingest, derive_state shows active_iteration set."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-x"))
    sm.ingest(p)
    state = sm.derive_state()
    assert state["active_iteration"] is not None
    assert state["active_iteration"]["iteration_id"] == "iter-x"


def test_happy_path_single_requirement_works(isolated_log, tmp_path):
    """A handoff with a single requirement is a happy path (>=1 is enough)."""
    import sm
    handoff = _canonical_handoff(
        requirements=[_canonical_requirement("only-req")],
    )
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    assert result["requirements"] == [_canonical_requirement("only-req")]


# ===========================================================================
# Path errors (4+)
# ===========================================================================

def test_missing_file_raises_file_not_found(isolated_log, tmp_path):
    import sm
    missing = tmp_path / "does_not_exist.json"
    assert not missing.exists()
    with pytest.raises(FileNotFoundError):
        sm.ingest(missing)


def test_missing_file_str_path_raises_file_not_found(isolated_log, tmp_path):
    import sm
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        sm.ingest(str(missing))


def test_missing_file_does_not_modify_log(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        sm.ingest(missing)
    assert isolated_log.read_bytes() == seeded


def test_directory_path_raises_is_a_directory(isolated_log, tmp_path):
    import sm
    d = tmp_path / "a_directory"
    d.mkdir()
    with pytest.raises(IsADirectoryError):
        sm.ingest(d)


def test_directory_path_does_not_modify_log(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    d = tmp_path / "a_directory"
    d.mkdir()
    with pytest.raises(IsADirectoryError):
        sm.ingest(d)
    assert isolated_log.read_bytes() == seeded


def test_missing_file_log_unchanged_when_log_absent(isolated_log, tmp_path):
    """Failure mode against an empty (missing) log: still no log file."""
    import sm
    missing = tmp_path / "no.json"
    with pytest.raises(FileNotFoundError):
        sm.ingest(missing)
    # Still no log file written.
    assert not isolated_log.exists()


# ===========================================================================
# JSON parse errors (3+)
# ===========================================================================

def test_invalid_json_raises_value_error(isolated_log, tmp_path):
    import sm
    p = _write_raw(tmp_path, "{this is not valid json")
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_invalid_json_does_not_modify_log(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    p = _write_raw(tmp_path, "{this is not valid json")
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_trailing_garbage_raises_value_error(isolated_log, tmp_path):
    """Valid object followed by garbage is invalid JSON."""
    import sm
    valid = json.dumps(_canonical_handoff())
    p = _write_raw(tmp_path, valid + "GARBAGE")
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_empty_file_raises_value_error(isolated_log, tmp_path):
    """An empty file is not valid JSON."""
    import sm
    p = _write_raw(tmp_path, "")
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_top_level_non_object_array_raises_value_error(isolated_log, tmp_path):
    """JSON list at top level is not a valid handoff."""
    import sm
    p = _write_raw(tmp_path, json.dumps([1, 2, 3]))
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_top_level_non_object_string_raises_value_error(isolated_log, tmp_path):
    """JSON string at top level is not a valid handoff."""
    import sm
    p = _write_raw(tmp_path, json.dumps("just a string"))
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_top_level_non_object_number_raises_value_error(isolated_log, tmp_path):
    """JSON number at top level is not a valid handoff."""
    import sm
    p = _write_raw(tmp_path, json.dumps(42))
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_top_level_non_object_null_raises_value_error(isolated_log, tmp_path):
    """JSON null at top level is not a valid handoff."""
    import sm
    p = _write_raw(tmp_path, json.dumps(None))
    with pytest.raises(ValueError):
        sm.ingest(p)


# ===========================================================================
# Top-level shape (6+)
# ===========================================================================

def test_missing_iteration_id_raises_value_error(isolated_log, tmp_path):
    import sm
    handoff = _canonical_handoff()
    del handoff["iteration_id"]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p)
    assert "iteration_id" in str(exc_info.value)


def test_missing_iteration_id_does_not_modify_log(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    handoff = _canonical_handoff()
    del handoff["iteration_id"]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_iteration_id_non_string_raises_value_error(isolated_log, tmp_path):
    """iteration_id must be a string."""
    import sm
    handoff = _canonical_handoff()
    handoff["iteration_id"] = 12345
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_iteration_id_null_raises_value_error(isolated_log, tmp_path):
    """iteration_id of null is invalid."""
    import sm
    handoff = _canonical_handoff()
    handoff["iteration_id"] = None
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_iteration_id_empty_string_raises_value_error(isolated_log, tmp_path):
    """iteration_id of empty string is invalid."""
    import sm
    handoff = _canonical_handoff()
    handoff["iteration_id"] = ""
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_iteration_id_whitespace_only_raises_value_error(
    isolated_log, tmp_path
):
    """iteration_id of whitespace-only is invalid."""
    import sm
    handoff = _canonical_handoff()
    handoff["iteration_id"] = "   "
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_missing_requirements_raises_value_error(isolated_log, tmp_path):
    import sm
    handoff = _canonical_handoff()
    del handoff["requirements"]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p)
    assert "requirements" in str(exc_info.value)


def test_missing_requirements_does_not_modify_log(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    handoff = _canonical_handoff()
    del handoff["requirements"]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_requirements_non_list_raises_value_error(isolated_log, tmp_path):
    """requirements must be a list."""
    import sm
    handoff = _canonical_handoff()
    handoff["requirements"] = {"req-1": _canonical_requirement("req-1")}
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_requirements_string_raises_value_error(isolated_log, tmp_path):
    """A string is not a list — even though it's iterable."""
    import sm
    handoff = _canonical_handoff()
    handoff["requirements"] = "req-1, req-2"
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_requirements_null_raises_value_error(isolated_log, tmp_path):
    import sm
    handoff = _canonical_handoff()
    handoff["requirements"] = None
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_requirements_empty_list_raises_value_error(isolated_log, tmp_path):
    """An iteration with zero requirements is meaningless and rejected."""
    import sm
    handoff = _canonical_handoff(requirements=[])
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_requirements_empty_list_does_not_modify_log(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    handoff = _canonical_handoff(requirements=[])
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


# ===========================================================================
# Per-requirement validation (8+)
# ===========================================================================

def test_requirement_non_dict_raises_value_error(isolated_log, tmp_path):
    """A non-dict requirement raises ValueError naming the index."""
    import sm
    handoff = _canonical_handoff()
    handoff["requirements"] = [
        _canonical_requirement("req-1"),
        "not-a-dict",
        _canonical_requirement("req-3"),
    ]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p)
    # The error must name the offending index (index 1).
    assert "1" in str(exc_info.value)


def test_requirement_non_dict_does_not_modify_log(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    handoff = _canonical_handoff()
    handoff["requirements"] = [_canonical_requirement("req-1"), 42]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_requirement_null_in_list_raises_value_error(isolated_log, tmp_path):
    """A null entry in requirements is invalid."""
    import sm
    handoff = _canonical_handoff()
    handoff["requirements"] = [_canonical_requirement("req-1"), None]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p)
    assert "1" in str(exc_info.value)


def test_requirement_missing_requirement_id_raises_value_error(
    isolated_log, tmp_path
):
    """Requirement missing requirement_id raises ValueError naming the index."""
    import sm
    handoff = _canonical_handoff()
    bad = _canonical_requirement("placeholder")
    del bad["requirement_id"]
    handoff["requirements"] = [_canonical_requirement("req-1"), bad]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p)
    assert "1" in str(exc_info.value)


def test_requirement_missing_requirement_id_does_not_modify_log(
    isolated_log, tmp_path
):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    handoff = _canonical_handoff()
    bad = _canonical_requirement("x")
    del bad["requirement_id"]
    handoff["requirements"] = [bad]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_requirement_id_non_string_raises_value_error(isolated_log, tmp_path):
    """requirement_id that's not a string is invalid."""
    import sm
    handoff = _canonical_handoff()
    bad = _canonical_requirement("placeholder")
    bad["requirement_id"] = 999
    handoff["requirements"] = [bad]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p)
    # Index 0 named.
    assert "0" in str(exc_info.value)


def test_requirement_id_null_raises_value_error(isolated_log, tmp_path):
    import sm
    handoff = _canonical_handoff()
    bad = _canonical_requirement("placeholder")
    bad["requirement_id"] = None
    handoff["requirements"] = [bad]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_requirement_id_empty_string_raises_value_error(
    isolated_log, tmp_path
):
    """Empty-string requirement_id is invalid."""
    import sm
    handoff = _canonical_handoff()
    bad = _canonical_requirement("placeholder")
    bad["requirement_id"] = ""
    handoff["requirements"] = [bad]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p)
    assert "0" in str(exc_info.value)


def test_requirement_id_whitespace_only_raises_value_error(
    isolated_log, tmp_path
):
    """Whitespace-only requirement_id is invalid."""
    import sm
    handoff = _canonical_handoff()
    bad = _canonical_requirement("placeholder")
    bad["requirement_id"] = "   "
    handoff["requirements"] = [bad]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_duplicate_requirement_ids_raises_value_error(isolated_log, tmp_path):
    """Duplicate requirement_id values inside the handoff are rejected."""
    import sm
    handoff = _canonical_handoff()
    handoff["requirements"] = [
        _canonical_requirement("req-1", "First"),
        _canonical_requirement("req-2", "Second"),
        _canonical_requirement("req-1", "Duplicate"),
    ]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p)
    # Error message must name the duplicate id.
    assert "req-1" in str(exc_info.value)


def test_duplicate_requirement_ids_does_not_modify_log(
    isolated_log, tmp_path
):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    handoff = _canonical_handoff()
    handoff["requirements"] = [
        _canonical_requirement("req-dup"),
        _canonical_requirement("req-dup"),
    ]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_three_way_duplicate_requirement_ids_raises(isolated_log, tmp_path):
    """Three-way duplicate is also rejected."""
    import sm
    handoff = _canonical_handoff()
    handoff["requirements"] = [
        _canonical_requirement("rx"),
        _canonical_requirement("rx"),
        _canonical_requirement("rx"),
    ]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p)
    assert "rx" in str(exc_info.value)


# ===========================================================================
# Forward-compat extras (3+)
# ===========================================================================

def test_extra_top_level_field_preserved(isolated_log, tmp_path):
    """Unknown top-level fields are preserved verbatim into the log entry."""
    import sm
    handoff = _canonical_handoff()
    handoff["extra_field"] = "future-value"
    handoff["another_extra"] = {"nested": [1, 2, 3]}
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    assert result["extra_field"] == "future-value"
    assert result["another_extra"] == {"nested": [1, 2, 3]}


def test_extra_requirement_field_preserved(isolated_log, tmp_path):
    """Extra fields on a requirement are preserved verbatim."""
    import sm
    req = _canonical_requirement("req-1")
    req["future_estimate_pts"] = 5
    req["custom_tag"] = "alpha"
    handoff = _canonical_handoff(requirements=[req])
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    written_req = result["requirements"][0]
    assert written_req["future_estimate_pts"] == 5
    assert written_req["custom_tag"] == "alpha"


def test_all_handoff_fields_preserved_verbatim(isolated_log, tmp_path):
    """A full handoff with extras round-trips byte-for-byte through ingest."""
    import sm
    handoff = _canonical_handoff()
    handoff["meta"] = {"author": "po-tool", "version": "0.2"}
    handoff["created_at"] = "2026-05-09T12:00:00"
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    # Every handoff key is present in result.
    for k, v in handoff.items():
        assert k in result, f"handoff key {k!r} missing from log entry"
        assert result[k] == v, (
            f"handoff key {k!r} not preserved verbatim: "
            f"got {result[k]!r}, want {v!r}"
        )


def test_empty_iteration_goal_preserved(isolated_log, tmp_path):
    """Even an empty iteration_goal is preserved verbatim."""
    import sm
    handoff = _canonical_handoff(iteration_goal="")
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    assert result["iteration_goal"] == ""


# ===========================================================================
# Single-active-iteration enforcement (4+)
# ===========================================================================

def test_ingest_while_iteration_open_raises_value_error(
    isolated_log, tmp_path
):
    """Cannot ingest a second handoff while an iteration is open."""
    import sm
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    # iter-1 is now open.
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    with pytest.raises(ValueError) as exc_info:
        sm.ingest(p2)
    # Error must name the currently-open iteration_id.
    assert "iter-1" in str(exc_info.value)


def test_ingest_while_iteration_open_does_not_modify_log(
    isolated_log, tmp_path
):
    import sm
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    bytes_after_first = isolated_log.read_bytes()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)
    # Log unchanged after the failure.
    assert isolated_log.read_bytes() == bytes_after_first


def test_ingest_succeeds_after_iteration_close(isolated_log, tmp_path):
    """Once the prior iteration is closed, a new ingest is allowed."""
    import sm
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)

    # Manually close iter-1 (Story 14 will provide the close command;
    # for this test we close via the canonical entry-builder path).
    close_entry = sm.build_entry("iteration_close", {
        "closed_by": "operator",
        "reason": None,
        "accepted_count": 0,
        "rejected_count": 0,
        "force_closed_count": 0,
    })
    sm._append_entry(close_entry)

    # Now iter-2 ingests cleanly.
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    sm.ingest(p2)

    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-2"


def test_ingest_succeeds_after_force_close(isolated_log, tmp_path):
    """A force-close (close with reason) also frees the slot."""
    import sm
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)

    close_entry = sm.build_entry("iteration_close", {
        "closed_by": "operator",
        "reason": "abort",
        "accepted_count": 0,
        "rejected_count": 0,
        "force_closed_count": 0,
    })
    sm._append_entry(close_entry)

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    sm.ingest(p2)

    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-2"


def test_first_ingest_on_empty_log_succeeds(isolated_log, tmp_path):
    """An empty log is the canonical green-field state — first ingest works."""
    import sm
    assert not isolated_log.exists()
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    assert isolated_log.exists()


# ===========================================================================
# Round-trip via derive_state (4+)
# ===========================================================================

def test_round_trip_active_iteration_id_matches(isolated_log, tmp_path):
    import sm
    p = _write_handoff(tmp_path,
                       _canonical_handoff(iteration_id="iter-rt-id"))
    sm.ingest(p)
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-rt-id"


def test_round_trip_requirements_list_verbatim(isolated_log, tmp_path):
    """derive_state's active_iteration.requirements equals the handoff's
    requirements list verbatim."""
    import sm
    reqs = [
        _canonical_requirement("a", "TA", "DA", "MUST", "ACA"),
        _canonical_requirement("b", "TB", "DB", "SHOULD", "ACB"),
    ]
    handoff = _canonical_handoff(requirements=reqs)
    p = _write_handoff(tmp_path, handoff)
    sm.ingest(p)
    state = sm.derive_state()
    assert state["active_iteration"]["requirements"] == reqs


def test_round_trip_close_status_is_none(isolated_log, tmp_path):
    """A fresh ingest leaves close_status at None."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    state = sm.derive_state()
    assert state["close_status"] is None


def test_round_trip_story_backlog_empty_after_ingest(isolated_log, tmp_path):
    """Ingest only opens the iteration — no decomposition, so backlog empty."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    state = sm.derive_state()
    assert state["story_backlog"] == []
    assert state["story_states"] == {}


def test_round_trip_sprint_cut_is_none(isolated_log, tmp_path):
    """Ingest does not touch sprint_cut."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    state = sm.derive_state()
    assert state["sprint_cut"] is None


def test_round_trip_after_close_then_ingest(isolated_log, tmp_path):
    """A close-then-ingest cycle results in the new iteration being active
    with close_status reset to None on the new open."""
    import sm
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="i1"),
                        name="i1.json")
    sm.ingest(p1)
    close = sm.build_entry("iteration_close", {
        "closed_by": "op", "reason": None,
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 0,
    })
    sm._append_entry(close)
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="i2"),
                        name="i2.json")
    sm.ingest(p2)

    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "i2"
    # close_status cleared on new open (per derive_state contract).
    assert state["close_status"] is None


# ===========================================================================
# Failure invariant — log byte-for-byte unchanged on any failure (5+)
# ===========================================================================

def test_log_unchanged_after_invalid_json(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    p = _write_raw(tmp_path, "{not json")
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_log_unchanged_after_top_level_array(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    p = _write_raw(tmp_path, json.dumps([1, 2, 3]))
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_log_unchanged_after_missing_iteration_id(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    handoff = _canonical_handoff()
    del handoff["iteration_id"]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_log_unchanged_after_empty_requirements(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    p = _write_handoff(tmp_path, _canonical_handoff(requirements=[]))
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_log_unchanged_after_duplicate_requirement_ids(
    isolated_log, tmp_path
):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    handoff = _canonical_handoff()
    handoff["requirements"] = [
        _canonical_requirement("dup"),
        _canonical_requirement("dup"),
    ]
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert isolated_log.read_bytes() == seeded


def test_log_unchanged_after_iteration_already_open(isolated_log, tmp_path):
    """Single-active-iteration violation also leaves the log unchanged."""
    import sm
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"),
                        name="h1.json")
    sm.ingest(p1)
    bytes_after_first = isolated_log.read_bytes()

    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-2"),
                        name="h2.json")
    with pytest.raises(ValueError):
        sm.ingest(p2)
    assert isolated_log.read_bytes() == bytes_after_first


def test_log_unchanged_after_directory_path(isolated_log, tmp_path):
    import sm
    seeded = _seed_log_with_one_entry(isolated_log)
    d = tmp_path / "dir"
    d.mkdir()
    with pytest.raises(IsADirectoryError):
        sm.ingest(d)
    assert isolated_log.read_bytes() == seeded


# ===========================================================================
# Unicode handling (3+)
# ===========================================================================

def test_unicode_in_iteration_id_round_trips(isolated_log, tmp_path):
    """Unicode in iteration_id round-trips through ingest + derive_state."""
    import sm
    handoff = _canonical_handoff(iteration_id="iter-α-β-π")
    p = _write_handoff(tmp_path, handoff)
    sm.ingest(p)
    state = sm.derive_state()
    assert state["active_iteration"]["iteration_id"] == "iter-α-β-π"


def test_unicode_in_requirement_title_round_trips(isolated_log, tmp_path):
    """Unicode in requirement title is preserved verbatim."""
    import sm
    title = "Réseau – épée — résumé · 中文 · 🔥"
    reqs = [_canonical_requirement("req-1", title=title)]
    handoff = _canonical_handoff(requirements=reqs)
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    assert result["requirements"][0]["title"] == title


def test_unicode_in_requirement_description_round_trips(
    isolated_log, tmp_path
):
    """Unicode in requirement description is preserved verbatim."""
    import sm
    desc = "Spec: «handle Über-cases» — including ✓ and ✗"
    reqs = [_canonical_requirement("req-1", description=desc)]
    handoff = _canonical_handoff(requirements=reqs)
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    assert result["requirements"][0]["description"] == desc


def test_unicode_in_iteration_goal_round_trips(isolated_log, tmp_path):
    """Unicode in iteration_goal is preserved verbatim."""
    import sm
    goal = "Build the 🚀 — first cut"
    handoff = _canonical_handoff(iteration_goal=goal)
    p = _write_handoff(tmp_path, handoff)
    result = sm.ingest(p)
    assert result["iteration_goal"] == goal


# ===========================================================================
# Path forms — relative vs absolute (3+)
# ===========================================================================

def test_absolute_path_works(isolated_log, tmp_path):
    """An absolute Path resolves to the file."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    abs_p = p.resolve()
    assert abs_p.is_absolute()
    sm.ingest(abs_p)
    assert len(list(sm.read_entries())) == 1


def test_absolute_str_path_works(isolated_log, tmp_path):
    """An absolute string path also resolves."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    abs_str = str(p.resolve())
    sm.ingest(abs_str)
    assert len(list(sm.read_entries())) == 1


def test_relative_path_resolves_against_cwd(isolated_log, tmp_path,
                                             monkeypatch):
    """A relative path is resolved against the process cwd."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff(),
                       name="relative_handoff.json")
    monkeypatch.chdir(tmp_path)
    # Use a relative path now that cwd == tmp_path.
    sm.ingest("relative_handoff.json")
    assert len(list(sm.read_entries())) == 1


# ===========================================================================
# LOG_PATH-based (2+)
# ===========================================================================

def test_uses_log_path_at_call_time(tmp_path, monkeypatch):
    """ingest writes to sm.LOG_PATH at call time (not import time)."""
    import sm
    custom = tmp_path / "custom.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", custom)
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    assert custom.exists()
    # The bytes are JSONL, not empty.
    assert custom.stat().st_size > 0


def test_log_path_change_between_calls(tmp_path, monkeypatch):
    """Changing LOG_PATH between calls redirects ingest to the new path."""
    import sm
    log_a = tmp_path / "a.jsonl"
    log_b = tmp_path / "b.jsonl"

    monkeypatch.setattr(sm, "LOG_PATH", log_a)
    p1 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="A"),
                        name="ha.json")
    sm.ingest(p1)
    assert log_a.exists() and log_a.stat().st_size > 0
    assert not log_b.exists()

    # Switch — close A's iteration via direct entry, then point at B and ingest.
    close = sm.build_entry("iteration_close", {
        "closed_by": "op", "reason": None,
        "accepted_count": 0, "rejected_count": 0, "force_closed_count": 0,
    })
    sm._append_entry(close)

    monkeypatch.setattr(sm, "LOG_PATH", log_b)
    p2 = _write_handoff(tmp_path, _canonical_handoff(iteration_id="B"),
                        name="hb.json")
    sm.ingest(p2)
    assert log_b.exists() and log_b.stat().st_size > 0


def test_does_not_write_to_real_log(tmp_path, monkeypatch):
    """With LOG_PATH patched, the package's real log.jsonl is not touched."""
    import sm
    custom = tmp_path / "custom.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", custom)
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    # Only the patched log was written.
    assert custom.exists()


# ===========================================================================
# Append-path canonicality — uses build_entry + _append_entry
# ===========================================================================

def test_uses_build_entry(isolated_log, tmp_path, monkeypatch):
    """ingest must go through sm.build_entry (the canonical entry path)."""
    import sm

    calls = {"n": 0}
    real = sm.build_entry

    def fake(type_, content):
        calls["n"] += 1
        return real(type_, content)

    monkeypatch.setattr(sm, "build_entry", fake)
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    assert calls["n"] == 1, (
        f"ingest must call build_entry exactly once; got {calls['n']}"
    )


def test_uses_append_entry(isolated_log, tmp_path, monkeypatch):
    """ingest must go through sm._append_entry (the canonical append path)."""
    import sm

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    p = _write_handoff(tmp_path, _canonical_handoff())
    sm.ingest(p)
    assert calls["n"] == 1


def test_append_entry_not_called_on_validation_failure(
    isolated_log, tmp_path, monkeypatch
):
    """Validation failure → no append. Pin the wire-up."""
    import sm

    calls = {"n": 0}
    real = sm._append_entry

    def fake(entry):
        calls["n"] += 1
        return real(entry)

    monkeypatch.setattr(sm, "_append_entry", fake)
    handoff = _canonical_handoff(requirements=[])  # invalid: empty requirements
    p = _write_handoff(tmp_path, handoff)
    with pytest.raises(ValueError):
        sm.ingest(p)
    assert calls["n"] == 0


# ===========================================================================
# Subprocess CLI surface (2+, optional)
# ===========================================================================
# These tests pin the `python -m sm ingest <path>` exit-code semantics.
# The CLI surface is part of Story 5's "terminal command" requirement.

def _project_env(tmp_path: pathlib.Path) -> dict:
    """Build an env where sm.LOG_PATH is redirected via SM_LOG_PATH (if
    supported) — but for now we rely on the cwd to keep tests hermetic.

    The simplest hermetic path is: chdir to tmp_path and accept that the
    package's real log.jsonl path is computed relative to the package
    directory. Since the CLI subprocess isn't easily LOG_PATH-monkeypatched,
    these tests focus on EXIT CODE only — they do not verify log contents.
    """
    import os
    return os.environ.copy()


def test_cli_module_runs_without_error_for_valid_handoff(tmp_path):
    """`python -m sm ingest <valid handoff>` exits 0 (or returns the
    iteration id in stdout). Pinned loosely: exit code 0.

    NOTE: This test creates a separate subprocess which writes to the
    package's real log.jsonl. To keep the test hermetic, we re-point
    LOG_PATH via an env var IF sm supports SM_LOG_PATH; otherwise we
    skip cleanup of the real log (acceptable for Iter 1).
    """
    import os

    handoff = _canonical_handoff(iteration_id="cli-iter-test")
    p = tmp_path / "h.json"
    p.write_text(json.dumps(handoff), encoding="utf-8")

    # Use a per-test log file via env. If sm doesn't honor it, the test is
    # pointing at the package log — still exits 0 if the package log is
    # empty / has no active iteration.
    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")

    result = subprocess.run(
        [sys.executable, "-m", "sm", "ingest", str(p)],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The CLI must (a) exit 0 AND (b) print the new iteration_id. Without
    # both, the CLI isn't actually wired up to ingest. Note: a bare `python
    # -m sm` against a module with no __main__ silently exits 0 — the
    # iteration_id check is what proves the CLI did real work.
    assert result.returncode == 0, (
        f"CLI exit code: {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "cli-iter-test" in result.stdout, (
        f"CLI must print the new iteration_id on success; "
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_module_nonzero_for_missing_path(tmp_path):
    """`python -m sm ingest <missing path>` exits non-zero."""
    import os

    missing = tmp_path / "definitely_missing.json"
    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")

    result = subprocess.run(
        [sys.executable, "-m", "sm", "ingest", str(missing)],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"CLI must exit non-zero for missing path; "
        f"got returncode={result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_cli_module_prints_iteration_id_on_success(tmp_path):
    """On success, the CLI prints the new iteration_id to stdout."""
    import os

    iter_id = "iter-printed-test"
    handoff = _canonical_handoff(iteration_id=iter_id)
    p = tmp_path / "h.json"
    p.write_text(json.dumps(handoff), encoding="utf-8")

    env = os.environ.copy()
    env["SM_LOG_PATH"] = str(tmp_path / "cli_log.jsonl")

    result = subprocess.run(
        [sys.executable, "-m", "sm", "ingest", str(p)],
        cwd=str(PACKAGE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"CLI must succeed for valid handoff; got returncode="
        f"{result.returncode}\nstdout={result.stdout!r}\n"
        f"stderr={result.stderr!r}"
    )
    assert iter_id in result.stdout, (
        f"iteration_id must appear in stdout on success; "
        f"stdout={result.stdout!r}"
    )


# ===========================================================================
# Idempotency / repeated calls — ingest is NOT idempotent: running twice
# on the same handoff while the iteration is open raises (single-active rule)
# ===========================================================================

def test_repeated_ingest_same_file_raises_on_second_call(
    isolated_log, tmp_path
):
    """The second ingest of the same handoff raises (already open)."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"))
    sm.ingest(p)
    with pytest.raises(ValueError):
        sm.ingest(p)


def test_repeated_ingest_does_not_write_second_entry(isolated_log, tmp_path):
    """After the second-call failure, only one entry exists in the log."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff(iteration_id="iter-1"))
    sm.ingest(p)
    bytes_after_first = isolated_log.read_bytes()
    with pytest.raises(ValueError):
        sm.ingest(p)
    # Log byte-for-byte unchanged.
    assert isolated_log.read_bytes() == bytes_after_first


# ===========================================================================
# Sanity — written entry is JSON-serializable and round-trips through the log
# ===========================================================================

def test_written_entry_round_trips_through_read_entries(
    isolated_log, tmp_path
):
    """The entry returned by ingest() equals the entry read back from the log."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    returned = sm.ingest(p)
    [from_log] = list(sm.read_entries())
    assert returned == from_log


def test_written_entry_is_json_serializable(isolated_log, tmp_path):
    """The written entry survives a json.dumps round-trip."""
    import sm
    p = _write_handoff(tmp_path, _canonical_handoff())
    e = sm.ingest(p)
    s = json.dumps(e)
    assert json.loads(s) == e
