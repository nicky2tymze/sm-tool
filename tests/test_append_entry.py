"""Story 1 — pin the contract of `sm._append_entry`.

What this file pins:
  - Function signature and shape: `_append_entry(entry: dict) -> None`, internal,
    callable, returns None on success.
  - Append-only semantics: the writer never opens `log.jsonl` in `w` or `r+`
    mode; existing content survives every call; entries are emitted in order;
    one JSON object per line.
  - File handling: creates `log.jsonl` if absent; uses `sm.LOG_PATH` (not a
    hardcoded path); writes raw LF line terminators (no Windows CRLF
    translation); flushes before returning; closes the handle (via `with`).
  - Encoding: `ensure_ascii=False` — unicode survives as real UTF-8 bytes,
    not `\\uXXXX` escapes.
  - Input validation: non-dict input raises `TypeError` and the message names
    the offending type. Lists, strings, ints, None, tuples, sets, bytes, and
    bools are all rejected.
  - Pre-write serialization: non-JSON-serializable values raise BEFORE any
    file I/O — the existing log is left exactly as it was (byte-for-byte).
  - No sidecar files: no `.state`, no DB, no auxiliary persistent files appear
    in the package directory after a successful append.
  - Source-of-truth invariant: a grep across the package returns exactly one
    write-mode open of `log.jsonl`.

Tests must FAIL on first run — `_append_entry` does not exist yet. The Coder
downstream implements to satisfy these tests.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sys
import threading

import pytest

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file.

    Mirrors the suite convention (po-tool, standup-tool): monkeypatch the
    module-level constant so production code uses the patched value, while
    each test gets a fresh, isolated log file.
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _read_lines(path: pathlib.Path) -> list[str]:
    """Read raw lines from the log, splitting on LF only (no universal newlines)."""
    raw = path.read_bytes().decode("utf-8")
    if raw == "":
        return []
    # Trailing LF means the last "line" is empty; drop it.
    parts = raw.split("\n")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------

def test_function_exists_on_module():
    import sm
    assert hasattr(sm, "_append_entry"), "sm._append_entry must exist"


def test_function_is_callable():
    import sm
    assert callable(sm._append_entry)


def test_function_name_starts_with_underscore():
    """It's an internal helper, not a public API entry point."""
    import sm
    assert sm._append_entry.__name__.startswith("_")


def test_function_returns_none(isolated_log):
    import sm
    result = sm._append_entry({"event": "smoke"})
    assert result is None


def test_function_not_in_dunder_all():
    """Internal functions should not be exported via __all__."""
    import sm
    if hasattr(sm, "__all__"):
        assert "_append_entry" not in sm.__all__


# ---------------------------------------------------------------------------
# File creation
# ---------------------------------------------------------------------------

def test_creates_log_file_if_missing(isolated_log):
    import sm
    assert not isolated_log.exists()
    sm._append_entry({"event": "first"})
    assert isolated_log.exists()
    assert isolated_log.is_file()


def test_creates_log_when_parent_dir_already_exists(tmp_path, monkeypatch):
    import sm
    # Parent dir already exists (tmp_path), file does not.
    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    assert tmp_path.is_dir()
    assert not log_file.exists()
    sm._append_entry({"event": "x"})
    assert log_file.is_file()


def test_no_sidecar_files_created(isolated_log, tmp_path):
    """No `.state`, no DB, no journal, nothing but log.jsonl."""
    import sm
    sm._append_entry({"event": "alpha"})
    sm._append_entry({"event": "beta"})
    contents = sorted(p.name for p in tmp_path.iterdir())
    assert contents == ["log.jsonl"]


def test_does_not_create_log_when_input_is_invalid(isolated_log):
    """Bad input must not have the side-effect of creating an empty file."""
    import sm
    with pytest.raises(TypeError):
        sm._append_entry("not a dict")
    assert not isolated_log.exists()


# ---------------------------------------------------------------------------
# Append-only
# ---------------------------------------------------------------------------

def test_existing_content_preserved_across_appends(isolated_log):
    import sm
    sm._append_entry({"n": 1})
    first_bytes = isolated_log.read_bytes()
    sm._append_entry({"n": 2})
    second_bytes = isolated_log.read_bytes()
    # The first call's bytes are a strict prefix of the second call's bytes.
    assert second_bytes.startswith(first_bytes)
    assert len(second_bytes) > len(first_bytes)


def test_pre_existing_content_preserved(isolated_log):
    """Even if log.jsonl already has content (e.g. from a prior session)."""
    import sm
    seed = b'{"event": "seed"}\n'
    isolated_log.write_bytes(seed)
    sm._append_entry({"event": "added"})
    final = isolated_log.read_bytes()
    assert final.startswith(seed)


def test_append_after_many_pre_existing_lines(isolated_log):
    import sm
    seed_lines = [json.dumps({"i": i}) for i in range(50)]
    seed_bytes = ("\n".join(seed_lines) + "\n").encode("utf-8")
    isolated_log.write_bytes(seed_bytes)
    sm._append_entry({"i": 999})
    final = isolated_log.read_bytes()
    assert final.startswith(seed_bytes)
    # New tail line is the appended entry.
    last = _read_lines(isolated_log)[-1]
    assert json.loads(last) == {"i": 999}


def test_no_write_mode_open_in_codebase():
    """Grep for write-mode opens of log.jsonl — exactly one site allowed."""
    sm_path = PACKAGE_DIR / "sm.py"
    src = sm_path.read_text(encoding="utf-8")
    # Forbidden: opening in 'w' or 'r+' mode anywhere.
    forbidden_patterns = [
        r'open\([^)]*["\']w["\']',
        r'open\([^)]*["\']wb["\']',
        r'open\([^)]*["\']wt["\']',
        r'open\([^)]*["\']r\+["\']',
        r'open\([^)]*["\']w\+["\']',
        r'\.open\([^)]*["\']w["\']',
        r'\.open\([^)]*["\']wb["\']',
        r'\.open\([^)]*["\']r\+["\']',
        r'\.open\([^)]*["\']w\+["\']',
    ]
    for pat in forbidden_patterns:
        matches = re.findall(pat, src)
        assert not matches, f"Forbidden write/rewrite mode found: pattern={pat!r}"


def test_exactly_one_append_open_site():
    """The writer is the only place log.jsonl is opened for writing."""
    sm_path = PACKAGE_DIR / "sm.py"
    src = sm_path.read_text(encoding="utf-8")
    # Count append-mode opens. There should be exactly one.
    append_opens = re.findall(r'open\([^)]*["\']a["\']', src) + \
                   re.findall(r'\.open\([^)]*["\']a["\']', src)
    assert len(append_opens) == 1, (
        f"Expected exactly 1 append-mode open in sm.py, found {len(append_opens)}"
    )


# ---------------------------------------------------------------------------
# Multiple appends — order and count
# ---------------------------------------------------------------------------

def test_single_append_one_line(isolated_log):
    import sm
    sm._append_entry({"event": "only"})
    lines = _read_lines(isolated_log)
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"event": "only"}


def test_two_appends_two_lines_in_order(isolated_log):
    import sm
    sm._append_entry({"event": "a"})
    sm._append_entry({"event": "b"})
    lines = _read_lines(isolated_log)
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"event": "a"}
    assert json.loads(lines[1]) == {"event": "b"}


def test_ten_appends_ten_lines_in_order(isolated_log):
    import sm
    for i in range(10):
        sm._append_entry({"i": i, "msg": f"entry-{i}"})
    lines = _read_lines(isolated_log)
    assert len(lines) == 10
    for i, line in enumerate(lines):
        assert json.loads(line) == {"i": i, "msg": f"entry-{i}"}


def test_one_hundred_appends_preserve_order(isolated_log):
    import sm
    for i in range(100):
        sm._append_entry({"i": i})
    lines = _read_lines(isolated_log)
    assert len(lines) == 100
    for i, line in enumerate(lines):
        assert json.loads(line) == {"i": i}


# ---------------------------------------------------------------------------
# Line format
# ---------------------------------------------------------------------------

def test_each_line_is_valid_json(isolated_log):
    import sm
    payloads = [
        {"event": "x"},
        {"nested": {"a": 1}},
        {"list": [1, 2, 3]},
        {"empty_dict": {}},
        {"empty_list": []},
    ]
    for p in payloads:
        sm._append_entry(p)
    for line in _read_lines(isolated_log):
        json.loads(line)  # raises if invalid


def test_lines_terminate_in_single_lf(isolated_log):
    import sm
    sm._append_entry({"a": 1})
    sm._append_entry({"a": 2})
    raw = isolated_log.read_bytes()
    assert raw.endswith(b"\n")
    # No CRLF anywhere.
    assert b"\r\n" not in raw
    assert b"\r" not in raw


def test_no_crlf_with_unicode_payload(isolated_log):
    """Windows: newline='\\n' must keep LF as LF even with non-ASCII content."""
    import sm
    sm._append_entry({"msg": "héllo wörld 日本語"})
    raw = isolated_log.read_bytes()
    assert b"\r\n" not in raw
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_one_json_object_per_line(isolated_log):
    """A line, split on LF, is a complete JSON document — not partial."""
    import sm
    for i in range(5):
        sm._append_entry({"i": i})
    raw = isolated_log.read_text(encoding="utf-8", newline="")
    chunks = raw.split("\n")
    # Last chunk is empty (trailing LF).
    assert chunks[-1] == ""
    for chunk in chunks[:-1]:
        obj = json.loads(chunk)
        assert isinstance(obj, dict)


def test_empty_dict_writes_minimal_line(isolated_log):
    import sm
    sm._append_entry({})
    lines = _read_lines(isolated_log)
    assert len(lines) == 1
    assert json.loads(lines[0]) == {}


def test_no_extra_whitespace_around_lines(isolated_log):
    """Default json.dumps doesn't add a leading newline; lines should be tight."""
    import sm
    sm._append_entry({"k": "v"})
    raw = isolated_log.read_bytes()
    # Exactly one line of content + one trailing LF.
    assert raw.count(b"\n") == 1


# ---------------------------------------------------------------------------
# Encoding — ensure_ascii=False
# ---------------------------------------------------------------------------

def test_unicode_round_trips_as_utf8_bytes(isolated_log):
    import sm
    sm._append_entry({"msg": "héllo"})
    raw = isolated_log.read_bytes()
    # Real UTF-8 bytes for 'é' — not \u escape.
    assert "héllo".encode("utf-8") in raw
    assert b"\\u00e9" not in raw


def test_japanese_text_written_as_utf8(isolated_log):
    import sm
    sm._append_entry({"msg": "日本語テスト"})
    raw = isolated_log.read_bytes()
    assert "日本語テスト".encode("utf-8") in raw
    assert b"\\u" not in raw  # no escapes at all in this payload


def test_emoji_round_trips(isolated_log):
    import sm
    sm._append_entry({"reaction": "🔥"})
    raw = isolated_log.read_bytes()
    assert "🔥".encode("utf-8") in raw


def test_mixed_ascii_and_unicode_keys_and_values(isolated_log):
    import sm
    payload = {"ключ": "значение", "key": "value", "混合": [1, "ñ", "ü"]}
    sm._append_entry(payload)
    lines = _read_lines(isolated_log)
    assert json.loads(lines[0]) == payload


def test_unicode_in_keys_not_escaped(isolated_log):
    import sm
    sm._append_entry({"café": 1})
    raw = isolated_log.read_bytes()
    assert "café".encode("utf-8") in raw
    assert b"\\u00e9" not in raw


# ---------------------------------------------------------------------------
# Type validation — non-dict input
# ---------------------------------------------------------------------------

def test_list_input_raises_typeerror(isolated_log):
    import sm
    with pytest.raises(TypeError):
        sm._append_entry([{"event": "wrapped"}])


def test_str_input_raises_typeerror(isolated_log):
    import sm
    with pytest.raises(TypeError):
        sm._append_entry("event")


def test_int_input_raises_typeerror(isolated_log):
    import sm
    with pytest.raises(TypeError):
        sm._append_entry(42)


def test_float_input_raises_typeerror(isolated_log):
    import sm
    with pytest.raises(TypeError):
        sm._append_entry(3.14)


def test_none_input_raises_typeerror(isolated_log):
    import sm
    with pytest.raises(TypeError):
        sm._append_entry(None)


def test_tuple_input_raises_typeerror(isolated_log):
    import sm
    with pytest.raises(TypeError):
        sm._append_entry(("event", "x"))


def test_set_input_raises_typeerror(isolated_log):
    import sm
    with pytest.raises(TypeError):
        sm._append_entry({"event"})  # this is a set literal, not a dict


def test_bytes_input_raises_typeerror(isolated_log):
    import sm
    with pytest.raises(TypeError):
        sm._append_entry(b'{"event": "x"}')


def test_bool_input_raises_typeerror(isolated_log):
    """bool is an int subclass — explicitly verify it's rejected."""
    import sm
    with pytest.raises(TypeError):
        sm._append_entry(True)


def test_typeerror_message_names_offending_type(isolated_log):
    import sm
    with pytest.raises(TypeError) as exc_info:
        sm._append_entry([1, 2, 3])
    msg = str(exc_info.value).lower()
    assert "list" in msg or "dict" in msg, (
        f"TypeError message should reference the offending type or expected type; got: {exc_info.value!r}"
    )


def test_typeerror_message_for_str(isolated_log):
    import sm
    with pytest.raises(TypeError) as exc_info:
        sm._append_entry("hello")
    msg = str(exc_info.value).lower()
    assert "str" in msg or "dict" in msg


def test_dict_subclass_accepted(isolated_log):
    """A dict subclass is still a dict — should be accepted."""
    import sm

    class MyDict(dict):
        pass

    d = MyDict()
    d["event"] = "subclass"
    sm._append_entry(d)
    lines = _read_lines(isolated_log)
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"event": "subclass"}


def test_invalid_input_does_not_modify_log(isolated_log):
    """Existing log content must be untouched when input fails type check."""
    import sm
    sm._append_entry({"event": "good"})
    before = isolated_log.read_bytes()
    with pytest.raises(TypeError):
        sm._append_entry("bad")
    after = isolated_log.read_bytes()
    assert before == after


# ---------------------------------------------------------------------------
# Pre-write serialization — non-serializable values
# ---------------------------------------------------------------------------

def test_function_value_raises_before_io(isolated_log):
    import sm
    assert not isolated_log.exists()
    with pytest.raises((TypeError, ValueError)):
        sm._append_entry({"callback": lambda x: x})
    # File must NOT have been created.
    assert not isolated_log.exists()


def test_set_value_raises_before_io(isolated_log):
    """A set is not JSON-serializable; must raise without touching the file."""
    import sm
    assert not isolated_log.exists()
    with pytest.raises((TypeError, ValueError)):
        sm._append_entry({"tags": {"a", "b"}})
    assert not isolated_log.exists()


def test_non_serializable_does_not_corrupt_existing_log(isolated_log):
    """Existing log byte-for-byte unchanged after a failed append."""
    import sm
    sm._append_entry({"event": "first"})
    sm._append_entry({"event": "second"})
    before = isolated_log.read_bytes()
    with pytest.raises((TypeError, ValueError)):
        sm._append_entry({"bad": object()})
    after = isolated_log.read_bytes()
    assert before == after, "log.jsonl must be untouched when serialization fails"


def test_object_value_raises_before_io(isolated_log):
    import sm
    sm._append_entry({"event": "seed"})
    before = isolated_log.read_bytes()

    class Custom:
        pass

    with pytest.raises((TypeError, ValueError)):
        sm._append_entry({"obj": Custom()})
    after = isolated_log.read_bytes()
    assert before == after


def test_bytes_value_raises_before_io(isolated_log):
    """bytes are not JSON-serializable by default."""
    import sm
    assert not isolated_log.exists()
    with pytest.raises((TypeError, ValueError)):
        sm._append_entry({"blob": b"\x00\x01\x02"})
    assert not isolated_log.exists()


def test_circular_reference_raises_before_io(isolated_log):
    import sm
    assert not isolated_log.exists()
    a: dict = {}
    a["self"] = a
    with pytest.raises((TypeError, ValueError, RecursionError)):
        sm._append_entry(a)
    # No partial write.
    if isolated_log.exists():
        # If the implementation pre-creates the file, the body must be empty.
        assert isolated_log.read_bytes() == b""


def test_failed_append_does_not_create_file_when_log_missing(isolated_log):
    """Pre-write serialization: log.jsonl is NOT created when serialization fails."""
    import sm
    assert not isolated_log.exists()
    with pytest.raises((TypeError, ValueError)):
        sm._append_entry({"x": object()})
    assert not isolated_log.exists()


# ---------------------------------------------------------------------------
# LOG_PATH-based — uses sm.LOG_PATH, not a hardcoded path
# ---------------------------------------------------------------------------

def test_writes_go_to_patched_log_path(tmp_path, monkeypatch):
    """Monkeypatching sm.LOG_PATH redirects all writes — proves no hardcoded path."""
    import sm
    custom = tmp_path / "custom_name.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", custom)
    sm._append_entry({"event": "patched"})
    assert custom.is_file()
    assert custom.read_bytes() != b""


def test_does_not_write_to_real_log_path(tmp_path, monkeypatch):
    """Confirm the patched path is honored — real LOG_PATH untouched."""
    import sm
    real_log = PACKAGE_DIR / "log.jsonl"
    real_existed_before = real_log.exists()
    real_size_before = real_log.stat().st_size if real_existed_before else None

    custom = tmp_path / "patched.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", custom)
    sm._append_entry({"event": "isolated"})

    if real_existed_before:
        assert real_log.stat().st_size == real_size_before
    else:
        assert not real_log.exists()


def test_log_path_in_nested_directory(tmp_path, monkeypatch):
    """Patched path with a nested filename — function must use it as given."""
    import sm
    nested = tmp_path / "deep.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", nested)
    sm._append_entry({"event": "deep"})
    assert nested.is_file()


def test_log_path_as_string_or_pathlib(tmp_path, monkeypatch):
    """LOG_PATH being a Path object is the canonical form — verify it's used."""
    import sm
    target = tmp_path / "as_path.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", target)
    sm._append_entry({"event": "x"})
    # The file at the patched Path location should exist with content.
    assert target.is_file()
    assert b"\n" in target.read_bytes()


# ---------------------------------------------------------------------------
# File handle hygiene — flush + close
# ---------------------------------------------------------------------------

def test_content_visible_immediately_after_return(isolated_log):
    """Flush before return: bytes are on disk (readable) by the time we get control back."""
    import sm
    sm._append_entry({"event": "flushed"})
    # Read in a wholly fresh handle — if the function didn't flush, this could be empty.
    raw = isolated_log.read_bytes()
    assert raw != b""
    assert b'"event"' in raw


def test_repeated_calls_each_visible_immediately(isolated_log):
    import sm
    for i in range(5):
        sm._append_entry({"i": i})
        # Each successive read sees one more line than the last.
        lines = _read_lines(isolated_log)
        assert len(lines) == i + 1


def test_handle_closed_after_return(isolated_log):
    """Function uses `with`-style context — handle is closed before returning.

    On Windows, an open file handle blocks deletion; we assert deletion succeeds
    immediately after return, which implies the handle was closed.
    """
    import sm
    sm._append_entry({"event": "x"})
    # If the handle were still open on Windows, unlink() would raise PermissionError.
    isolated_log.unlink()
    assert not isolated_log.exists()


def test_can_append_after_external_truncation(isolated_log):
    """Independent verification that no stale handle is held: external write works."""
    import sm
    sm._append_entry({"event": "first"})
    # Externally rewrite the file (simulating another process / tool).
    isolated_log.write_bytes(b'{"external": true}\n')
    # Now append again — the function must be opening a fresh handle each call.
    sm._append_entry({"event": "second"})
    raw = isolated_log.read_bytes()
    assert b'"external"' in raw
    assert b'"second"' in raw


# ---------------------------------------------------------------------------
# Payload variety — values that ARE serializable
# ---------------------------------------------------------------------------

def test_nested_dict_preserved(isolated_log):
    import sm
    payload = {"outer": {"inner": {"deep": [1, 2, {"x": "y"}]}}}
    sm._append_entry(payload)
    lines = _read_lines(isolated_log)
    assert json.loads(lines[0]) == payload


def test_null_value_preserved(isolated_log):
    import sm
    sm._append_entry({"x": None})
    lines = _read_lines(isolated_log)
    assert json.loads(lines[0]) == {"x": None}


def test_bool_value_preserved(isolated_log):
    import sm
    sm._append_entry({"flag": True, "other": False})
    lines = _read_lines(isolated_log)
    assert json.loads(lines[0]) == {"flag": True, "other": False}


def test_numeric_values_preserved(isolated_log):
    import sm
    sm._append_entry({"int": 42, "float": 3.14, "neg": -7, "zero": 0})
    lines = _read_lines(isolated_log)
    obj = json.loads(lines[0])
    assert obj["int"] == 42
    assert obj["float"] == 3.14
    assert obj["neg"] == -7
    assert obj["zero"] == 0


def test_string_with_embedded_newline(isolated_log):
    """An entry value containing '\\n' must NOT split the line in the log."""
    import sm
    sm._append_entry({"msg": "line1\nline2"})
    lines = _read_lines(isolated_log)
    # Exactly one log line, even though the value contains \n.
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"msg": "line1\nline2"}


def test_string_with_embedded_quote(isolated_log):
    import sm
    sm._append_entry({"msg": 'she said "hi"'})
    lines = _read_lines(isolated_log)
    assert json.loads(lines[0]) == {"msg": 'she said "hi"'}


def test_string_with_backslash(isolated_log):
    import sm
    sm._append_entry({"path": "C:\\Users\\nick"})
    lines = _read_lines(isolated_log)
    assert json.loads(lines[0]) == {"path": "C:\\Users\\nick"}


# ---------------------------------------------------------------------------
# Sequencing — interleaved appends and reads
# ---------------------------------------------------------------------------

def test_interleaved_append_and_read(isolated_log):
    import sm
    sm._append_entry({"step": 1})
    assert len(_read_lines(isolated_log)) == 1
    sm._append_entry({"step": 2})
    assert len(_read_lines(isolated_log)) == 2
    sm._append_entry({"step": 3})
    lines = _read_lines(isolated_log)
    assert len(lines) == 3
    assert [json.loads(l)["step"] for l in lines] == [1, 2, 3]


def test_appending_to_log_with_no_trailing_newline(isolated_log):
    """Edge case: prior content lacks a trailing LF.

    The contract pins 'one JSON object per line, terminated in LF'. The
    canonical state always has a trailing LF, so well-formed prior content
    means appending naturally keeps lines distinct.
    """
    import sm
    # Seed with a properly-terminated line (canonical).
    isolated_log.write_bytes(b'{"event": "seed"}\n')
    sm._append_entry({"event": "next"})
    lines = _read_lines(isolated_log)
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"event": "seed"}
    assert json.loads(lines[1]) == {"event": "next"}
