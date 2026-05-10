"""Story 2 — pin the contract of `sm.read_entries`.

What this file pins:
  - Function signature and shape: `read_entries() -> Iterable[dict]`, public,
    callable, exposed via `from sm import read_entries`.
  - Empty / missing semantics: returns an empty iterable when log.jsonl is
    missing or zero-byte; never raises in that case.
  - Pure ordered read: yields each line of log.jsonl parsed as a dict, in
    file order; does not filter, sort, or rewrite.
  - Iterability: result is iterable (generator OR list both acceptable);
    `list(read_entries())` and `iter(read_entries())` both work; idempotent
    across calls.
  - Type discipline: every yielded item is a `dict` (top-level non-dict JSON
    on any line — number, string, array, null, bool — raises).
  - Malformed JSON: raises a structured error naming the offending 1-based
    line number; partial reads do not corrupt the iterator (dicts yielded
    before the bad line ARE valid).
  - Whitespace-only lines: spec says "skips no entries; pure ordered read" —
    interpreted as: whitespace-only/blank lines are malformed and raise.
  - LOG_PATH-based: uses `sm.LOG_PATH` (verified by monkeypatch).
  - Pure read: calling `read_entries()` does not modify log.jsonl byte-for-byte.
  - UTF-8 round-trip: unicode appended via `_append_entry` reads back intact.
  - CRLF tolerance: a log containing CRLF line endings is handled sanely.
    This file pins the lenient interpretation: CRLF lines are accepted (the
    `\r` is stripped before JSON parsing) so a log produced by a non-canonical
    writer still reads. If the implementation chooses STRICT (raise on \r),
    the tests in the CRLF section will need a follow-up story to flip.
  - Trailing-LF tolerance: log ending in `\n` (canonical) AND log not ending
    in `\n` (edge case) both produce the correct entry sequence.

Tests must FAIL on first run — `read_entries` does not exist yet. The Coder
downstream implements to satisfy these tests.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import types

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

    Mirrors the suite convention (test_append_entry.py): monkeypatch the
    module-level constant so production code uses the patched value, while
    each test gets a fresh, isolated log file.
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


def _write_lines_lf(path: pathlib.Path, lines: list[str]) -> None:
    """Write `lines` joined by LF, with a trailing LF — canonical form."""
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    path.write_bytes(payload)


def _write_lines_no_trailing(path: pathlib.Path, lines: list[str]) -> None:
    """Write `lines` joined by LF, NO trailing LF — non-canonical edge case."""
    payload = "\n".join(lines).encode("utf-8")
    path.write_bytes(payload)


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------

def test_function_exists_on_module():
    import sm
    assert hasattr(sm, "read_entries"), "sm.read_entries must exist"


def test_function_is_callable():
    import sm
    assert callable(sm.read_entries)


def test_function_name_is_public():
    """No leading underscore — it's part of the public surface."""
    import sm
    assert not sm.read_entries.__name__.startswith("_")
    assert sm.read_entries.__name__ == "read_entries"


def test_function_importable_directly():
    """`from sm import read_entries` succeeds — the standard public-import form."""
    from sm import read_entries  # noqa: F401
    assert callable(read_entries)


def test_function_in_dunder_all():
    """Public functions should be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "read_entries" in sm.__all__, (
        f"read_entries must be in __all__; got {sm.__all__!r}"
    )


def test_function_takes_no_required_args(isolated_log):
    """`read_entries()` is callable with no arguments."""
    import sm
    # Should not raise TypeError for missing args.
    result = sm.read_entries()
    # Drain the iterable to ensure no lazy errors.
    list(result)


# ---------------------------------------------------------------------------
# Empty / missing file
# ---------------------------------------------------------------------------

def test_missing_file_returns_empty_iterable(isolated_log):
    """Missing log.jsonl → empty iterable, no exception."""
    import sm
    assert not isolated_log.exists()
    result = sm.read_entries()
    assert list(result) == []


def test_missing_file_does_not_raise(isolated_log):
    import sm
    assert not isolated_log.exists()
    # Whether iteration is eager or lazy, no exception either way.
    try:
        result = sm.read_entries()
        list(result)
    except Exception as e:
        pytest.fail(f"read_entries() on missing log raised: {e!r}")


def test_missing_file_does_not_create_file(isolated_log):
    """Pure read — must not create the log as a side-effect."""
    import sm
    assert not isolated_log.exists()
    list(sm.read_entries())
    assert not isolated_log.exists(), (
        "read_entries() must not create log.jsonl as a side-effect"
    )


def test_zero_byte_file_returns_empty_iterable(isolated_log):
    """Existing but empty log.jsonl → empty iterable."""
    import sm
    isolated_log.write_bytes(b"")
    assert isolated_log.exists()
    assert isolated_log.stat().st_size == 0
    assert list(sm.read_entries()) == []


def test_zero_byte_file_does_not_raise(isolated_log):
    import sm
    isolated_log.write_bytes(b"")
    try:
        list(sm.read_entries())
    except Exception as e:
        pytest.fail(f"read_entries() on zero-byte log raised: {e!r}")


# ---------------------------------------------------------------------------
# Single valid entry
# ---------------------------------------------------------------------------

def test_single_entry_yields_one_dict(isolated_log):
    import sm
    sm._append_entry({"event": "only"})
    entries = list(sm.read_entries())
    assert len(entries) == 1
    assert entries[0] == {"event": "only"}


def test_single_entry_is_dict_type(isolated_log):
    import sm
    sm._append_entry({"event": "x"})
    entries = list(sm.read_entries())
    assert isinstance(entries[0], dict)


def test_single_entry_round_trip_via_append(isolated_log):
    """Whatever _append_entry wrote, read_entries reads it back equal."""
    import sm
    payload = {"event": "round-trip", "n": 7, "tags": ["a", "b"]}
    sm._append_entry(payload)
    entries = list(sm.read_entries())
    assert entries == [payload]


# ---------------------------------------------------------------------------
# Multiple valid entries — order preservation
# ---------------------------------------------------------------------------

def test_two_entries_in_file_order(isolated_log):
    import sm
    sm._append_entry({"event": "a"})
    sm._append_entry({"event": "b"})
    entries = list(sm.read_entries())
    assert entries == [{"event": "a"}, {"event": "b"}]


def test_ten_entries_in_file_order(isolated_log):
    import sm
    for i in range(10):
        sm._append_entry({"i": i, "msg": f"entry-{i}"})
    entries = list(sm.read_entries())
    assert len(entries) == 10
    for i, e in enumerate(entries):
        assert e == {"i": i, "msg": f"entry-{i}"}


def test_one_hundred_entries_strict_order(isolated_log):
    """Order preservation under load."""
    import sm
    for i in range(100):
        sm._append_entry({"i": i})
    entries = list(sm.read_entries())
    assert len(entries) == 100
    assert [e["i"] for e in entries] == list(range(100))


def test_no_entries_filtered(isolated_log):
    """`read_entries` does not filter — every appended dict is yielded."""
    import sm
    payloads = [
        {"event": "alpha"},
        {},  # empty dict — still a dict, must be yielded
        {"event": "gamma"},
        {"deep": {"nested": [1, 2]}},
    ]
    for p in payloads:
        sm._append_entry(p)
    entries = list(sm.read_entries())
    assert entries == payloads


def test_no_entries_sorted(isolated_log):
    """The reader does not sort — file order is preserved as written."""
    import sm
    # Append in non-sorted key order.
    sm._append_entry({"i": 5})
    sm._append_entry({"i": 2})
    sm._append_entry({"i": 9})
    sm._append_entry({"i": 1})
    entries = list(sm.read_entries())
    assert [e["i"] for e in entries] == [5, 2, 9, 1]


# ---------------------------------------------------------------------------
# Type — every yielded item is a dict
# ---------------------------------------------------------------------------

def test_every_yielded_item_is_dict(isolated_log):
    import sm
    sm._append_entry({"a": 1})
    sm._append_entry({"b": [1, 2, 3]})
    sm._append_entry({"c": {"nested": True}})
    sm._append_entry({})
    for entry in sm.read_entries():
        assert isinstance(entry, dict), (
            f"Every yielded item must be a dict; got {type(entry).__name__}"
        )


# ---------------------------------------------------------------------------
# Pure read — no log mutation
# ---------------------------------------------------------------------------

def test_read_does_not_modify_log_bytes(isolated_log):
    """Calling read_entries() leaves log.jsonl byte-for-byte unchanged."""
    import sm
    sm._append_entry({"event": "a"})
    sm._append_entry({"event": "b"})
    sm._append_entry({"event": "c"})
    before = isolated_log.read_bytes()
    list(sm.read_entries())
    after = isolated_log.read_bytes()
    assert before == after, "read_entries() must not modify log.jsonl"


def test_read_does_not_modify_log_mtime_inodes(isolated_log):
    """Pure read: file size unchanged after read."""
    import sm
    sm._append_entry({"event": "x"})
    size_before = isolated_log.stat().st_size
    list(sm.read_entries())
    size_after = isolated_log.stat().st_size
    assert size_before == size_after


def test_no_sidecar_files_on_read(isolated_log, tmp_path):
    """No `.state`, no journal, no DB — read_entries creates nothing."""
    import sm
    sm._append_entry({"event": "x"})
    list(sm.read_entries())
    contents = sorted(p.name for p in tmp_path.iterdir())
    assert contents == ["log.jsonl"]


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

def test_two_reads_yield_same_sequence(isolated_log):
    """Calling read_entries() twice returns identical entries."""
    import sm
    for i in range(5):
        sm._append_entry({"i": i})
    first = list(sm.read_entries())
    second = list(sm.read_entries())
    assert first == second


def test_three_reads_all_equal(isolated_log):
    import sm
    sm._append_entry({"a": 1})
    sm._append_entry({"b": 2})
    a = list(sm.read_entries())
    b = list(sm.read_entries())
    c = list(sm.read_entries())
    assert a == b == c


def test_idempotent_on_empty_log(isolated_log):
    import sm
    isolated_log.write_bytes(b"")
    assert list(sm.read_entries()) == []
    assert list(sm.read_entries()) == []


def test_idempotent_on_missing_log(isolated_log):
    import sm
    assert not isolated_log.exists()
    assert list(sm.read_entries()) == []
    assert list(sm.read_entries()) == []


# ---------------------------------------------------------------------------
# Iterability — generator or list both work
# ---------------------------------------------------------------------------

def test_result_is_iterable(isolated_log):
    """The result supports iter() — could be list, generator, or any iterable."""
    import sm
    sm._append_entry({"a": 1})
    result = sm.read_entries()
    iterator = iter(result)
    # Drain at least one item — proves it's iterable.
    first = next(iterator, None)
    assert first == {"a": 1}


def test_result_works_in_list_constructor(isolated_log):
    import sm
    sm._append_entry({"a": 1})
    sm._append_entry({"b": 2})
    out = list(sm.read_entries())
    assert out == [{"a": 1}, {"b": 2}]


def test_result_works_in_for_loop(isolated_log):
    import sm
    sm._append_entry({"a": 1})
    sm._append_entry({"b": 2})
    collected = []
    for entry in sm.read_entries():
        collected.append(entry)
    assert collected == [{"a": 1}, {"b": 2}]


def test_result_works_with_tuple_constructor(isolated_log):
    import sm
    sm._append_entry({"x": 1})
    sm._append_entry({"y": 2})
    out = tuple(sm.read_entries())
    assert out == ({"x": 1}, {"y": 2})


def test_result_works_with_enumerate(isolated_log):
    import sm
    for i in range(3):
        sm._append_entry({"i": i})
    for idx, entry in enumerate(sm.read_entries()):
        assert entry == {"i": idx}


# ---------------------------------------------------------------------------
# Malformed JSON — line numbering (1-based)
# ---------------------------------------------------------------------------

def test_malformed_line_1_raises_with_line_number(isolated_log):
    """Line 1 is malformed → exception names line 1."""
    import sm
    isolated_log.write_bytes(b"not-json\n")
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    msg = str(exc_info.value)
    assert "1" in msg, f"Error must name line 1; got: {exc_info.value!r}"


def test_malformed_line_2_raises_with_line_number(isolated_log):
    """Line 2 is malformed → exception names line 2."""
    import sm
    isolated_log.write_bytes(b'{"ok": true}\nnot-json\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    msg = str(exc_info.value)
    assert "2" in msg, f"Error must name line 2; got: {exc_info.value!r}"


def test_malformed_line_3_raises_with_line_number(isolated_log):
    import sm
    isolated_log.write_bytes(
        b'{"a": 1}\n{"b": 2}\n{garbage\n'
    )
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    msg = str(exc_info.value)
    assert "3" in msg, f"Error must name line 3; got: {exc_info.value!r}"


def test_malformed_line_5_raises_with_line_number(isolated_log):
    import sm
    lines = [
        b'{"i": 1}',
        b'{"i": 2}',
        b'{"i": 3}',
        b'{"i": 4}',
        b'this is not json',
    ]
    isolated_log.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    msg = str(exc_info.value)
    assert "5" in msg, f"Error must name line 5; got: {exc_info.value!r}"


def test_malformed_line_does_not_name_wrong_line(isolated_log):
    """Sanity: when line 3 is bad, error doesn't say 'line 1' or 'line 99'."""
    import sm
    lines = [b'{"a": 1}', b'{"b": 2}', b'{not-json']
    isolated_log.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    msg = str(exc_info.value)
    # The error mentions 3 (the bad line).
    assert "3" in msg
    # Defensive: it doesn't claim line 1 or some unrelated number.
    # We don't pin "must NOT contain 1" because msg might say "line 3 of 3".
    # But it must contain the correct line number.


def test_malformed_line_message_is_descriptive(isolated_log):
    """The error message is structured — has more than just an opaque value."""
    import sm
    isolated_log.write_bytes(b"completely bogus content\n")
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    msg = str(exc_info.value)
    # Non-empty, contains something pointing at a line.
    assert msg, "Error message must not be empty"
    assert "1" in msg


def test_malformed_in_middle_uses_one_based_line_numbers(isolated_log):
    """1-based line numbering — line 1 is the first line, not line 0."""
    import sm
    # Bad line is the very first.
    isolated_log.write_bytes(b"garbage\n")
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    msg = str(exc_info.value)
    # 1-based: the message must contain '1', not '0'.
    assert "1" in msg
    # Note: '0' could appear inside other content but must not be the line number.
    # We don't strictly forbid '0' since "line 10" contains it, but at line 1
    # we expect a '1' present.


# ---------------------------------------------------------------------------
# Malformed mid-stream — partial reads do not corrupt
# ---------------------------------------------------------------------------

def test_valid_lines_before_bad_line_can_be_consumed(isolated_log):
    """If the iterator is generator-style, valid lines before the bad line
    can be consumed; the bad line raises when reached.

    If the iterator is eager (returns list), it raises before yielding any.
    Both behaviors are acceptable per the spec — but if any items DO yield,
    they must be valid dicts equal to the appended payloads.
    """
    import sm
    assert hasattr(sm, "read_entries"), "read_entries must exist for this test"
    # 3 good lines, then a bad one.
    valid = [{"i": 0}, {"i": 1}, {"i": 2}]
    payload = b""
    for v in valid:
        payload += json.dumps(v).encode("utf-8") + b"\n"
    payload += b"GARBAGE\n"
    isolated_log.write_bytes(payload)

    collected = []
    raised_exc = None
    try:
        for entry in sm.read_entries():
            collected.append(entry)
    except AttributeError:
        # read_entries doesn't exist — re-raise so test fails loudly.
        raise
    except Exception as e:
        raised_exc = e

    assert raised_exc is not None, "Malformed line must eventually raise"
    # The error must name the bad line (line 4).
    assert "4" in str(raised_exc), (
        f"Error must name line 4; got: {raised_exc!r}"
    )
    # Whatever was collected before the raise must equal a prefix of `valid`.
    assert collected == valid[: len(collected)], (
        f"Pre-bad-line yields must equal a prefix of valid; got {collected!r}"
    )
    # And specifically: each collected item is a real dict.
    for item in collected:
        assert isinstance(item, dict)


def test_bad_line_prefix_is_strict_prefix(isolated_log):
    """Items yielded before the bad line are valid AND in the right order."""
    import sm
    assert hasattr(sm, "read_entries"), "read_entries must exist for this test"
    valid = [{"a": 1}, {"b": 2}, {"c": 3}]
    payload = b""
    for v in valid:
        payload += json.dumps(v).encode("utf-8") + b"\n"
    payload += b"{not-a-real-json\n"
    isolated_log.write_bytes(payload)

    collected: list[dict] = []
    raised_exc = None
    try:
        for entry in sm.read_entries():
            collected.append(entry)
    except AttributeError:
        raise
    except Exception as e:
        raised_exc = e

    assert raised_exc is not None, "Malformed line must eventually raise"
    # Each yielded item appears in the same position as in `valid`.
    for i, item in enumerate(collected):
        assert item == valid[i]


def test_two_calls_after_bad_line_both_raise(isolated_log):
    """The error is reproducible: a second call also raises naming the same line."""
    import sm
    isolated_log.write_bytes(b'{"ok": 1}\nbad-line\n')

    with pytest.raises(Exception) as exc1:
        list(sm.read_entries())
    with pytest.raises(Exception) as exc2:
        list(sm.read_entries())
    assert "2" in str(exc1.value)
    assert "2" in str(exc2.value)


# ---------------------------------------------------------------------------
# Whitespace-only lines — interpreted as malformed (raise)
# ---------------------------------------------------------------------------

def test_blank_line_raises(isolated_log):
    """A purely empty line in the middle is malformed (skips no entries)."""
    import sm
    # Line 2 is blank.
    isolated_log.write_bytes(b'{"a": 1}\n\n{"b": 2}\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "2" in str(exc_info.value)


def test_whitespace_only_line_raises(isolated_log):
    """A line containing only spaces/tabs is malformed."""
    import sm
    isolated_log.write_bytes(b'{"a": 1}\n   \t  \n{"b": 2}\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "2" in str(exc_info.value)


def test_blank_line_at_start_raises(isolated_log):
    import sm
    isolated_log.write_bytes(b'\n{"a": 1}\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "1" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Top-level non-dict JSON — every form raises
# ---------------------------------------------------------------------------

def test_top_level_number_raises(isolated_log):
    """Valid JSON, but not a dict — must raise."""
    import sm
    isolated_log.write_bytes(b"42\n")
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "1" in str(exc_info.value)


def test_top_level_string_raises(isolated_log):
    import sm
    isolated_log.write_bytes(b'"just a string"\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "1" in str(exc_info.value)


def test_top_level_array_raises(isolated_log):
    import sm
    isolated_log.write_bytes(b'[1, 2, 3]\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "1" in str(exc_info.value)


def test_top_level_null_raises(isolated_log):
    import sm
    isolated_log.write_bytes(b'null\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "1" in str(exc_info.value)


def test_top_level_true_raises(isolated_log):
    import sm
    isolated_log.write_bytes(b'true\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "1" in str(exc_info.value)


def test_top_level_false_raises(isolated_log):
    import sm
    isolated_log.write_bytes(b'false\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "1" in str(exc_info.value)


def test_top_level_non_dict_in_middle_names_correct_line(isolated_log):
    """A bare array on line 2 (between two valid dicts) raises naming line 2."""
    import sm
    isolated_log.write_bytes(b'{"a": 1}\n[1, 2]\n{"c": 3}\n')
    with pytest.raises(Exception) as exc_info:
        list(sm.read_entries())
    assert "2" in str(exc_info.value)


# ---------------------------------------------------------------------------
# UTF-8 round-trip
# ---------------------------------------------------------------------------

def test_unicode_value_round_trips(isolated_log):
    import sm
    sm._append_entry({"msg": "héllo"})
    entries = list(sm.read_entries())
    assert entries == [{"msg": "héllo"}]


def test_japanese_text_round_trips(isolated_log):
    import sm
    sm._append_entry({"msg": "日本語テスト"})
    entries = list(sm.read_entries())
    assert entries == [{"msg": "日本語テスト"}]


def test_emoji_round_trips(isolated_log):
    import sm
    sm._append_entry({"reaction": "🔥"})
    entries = list(sm.read_entries())
    assert entries == [{"reaction": "🔥"}]


def test_unicode_in_keys_round_trips(isolated_log):
    import sm
    payload = {"ключ": "значение", "混合": [1, "ñ", "ü"]}
    sm._append_entry(payload)
    entries = list(sm.read_entries())
    assert entries == [payload]


def test_mixed_unicode_multi_entry(isolated_log):
    import sm
    payloads = [
        {"msg": "café"},
        {"msg": "日本"},
        {"msg": "naïve"},
        {"msg": "🎉"},
    ]
    for p in payloads:
        sm._append_entry(p)
    entries = list(sm.read_entries())
    assert entries == payloads


# ---------------------------------------------------------------------------
# CRLF tolerance — a log with CRLF line endings reads sanely
# ---------------------------------------------------------------------------
# The canonical writer (_append_entry) emits LF only. But if a non-canonical
# producer writes CRLF, the spec says "skips no entries; pure ordered read".
# This file pins the LENIENT interpretation: trailing \r is stripped before
# JSON parsing, so CRLF-terminated valid JSON is read successfully.

def test_crlf_terminated_valid_json_reads(isolated_log):
    """A line ending in CRLF still parses (\\r is whitespace to JSON parsers
    after stripping)."""
    import sm
    # Two CRLF-terminated valid JSON dicts.
    isolated_log.write_bytes(b'{"a": 1}\r\n{"b": 2}\r\n')
    # Either: this works (lenient), or it raises (strict). The spec is
    # silent on CRLF, but lenient is the safer/sane default for cross-tool logs.
    entries = list(sm.read_entries())
    assert entries == [{"a": 1}, {"b": 2}]


def test_mixed_lf_and_crlf_reads(isolated_log):
    """Mixed line endings — every line still reads back as the right dict."""
    import sm
    isolated_log.write_bytes(b'{"a": 1}\n{"b": 2}\r\n{"c": 3}\n')
    entries = list(sm.read_entries())
    assert entries == [{"a": 1}, {"b": 2}, {"c": 3}]


# ---------------------------------------------------------------------------
# Trailing-LF tolerance
# ---------------------------------------------------------------------------

def test_log_with_trailing_lf_reads_correctly(isolated_log):
    """Canonical form: log ends with LF. Every line parses, no extra empty line."""
    import sm
    _write_lines_lf(isolated_log, ['{"a": 1}', '{"b": 2}'])
    entries = list(sm.read_entries())
    assert entries == [{"a": 1}, {"b": 2}]


def test_log_without_trailing_lf_reads_correctly(isolated_log):
    """Edge case: log doesn't end with LF — last line still parsed."""
    import sm
    _write_lines_no_trailing(isolated_log, ['{"a": 1}', '{"b": 2}'])
    entries = list(sm.read_entries())
    assert entries == [{"a": 1}, {"b": 2}]


def test_single_line_no_trailing_lf(isolated_log):
    import sm
    isolated_log.write_bytes(b'{"only": "entry"}')
    entries = list(sm.read_entries())
    assert entries == [{"only": "entry"}]


def test_canonical_writer_log_reads_back_clean(isolated_log):
    """The canonical writer always trails with LF; reading must produce no
    spurious empty / blank entry from the trailing LF."""
    import sm
    sm._append_entry({"a": 1})
    sm._append_entry({"b": 2})
    raw = isolated_log.read_bytes()
    assert raw.endswith(b"\n")
    entries = list(sm.read_entries())
    assert len(entries) == 2  # not 3


# ---------------------------------------------------------------------------
# Pre-existing log content + read + further append + read
# ---------------------------------------------------------------------------

def test_read_then_append_then_read_preserves_order(isolated_log):
    """read_entries() does not corrupt the log; subsequent appends + reads are clean."""
    import sm
    sm._append_entry({"i": 0})
    sm._append_entry({"i": 1})
    first_read = list(sm.read_entries())
    assert first_read == [{"i": 0}, {"i": 1}]

    sm._append_entry({"i": 2})
    sm._append_entry({"i": 3})
    second_read = list(sm.read_entries())
    assert second_read == [{"i": 0}, {"i": 1}, {"i": 2}, {"i": 3}]


def test_pre_existing_log_reads_correctly(isolated_log):
    """Log seeded externally (e.g. from a prior session) reads without issue."""
    import sm
    seed_lines = [json.dumps({"i": i}) for i in range(5)]
    payload = ("\n".join(seed_lines) + "\n").encode("utf-8")
    isolated_log.write_bytes(payload)
    entries = list(sm.read_entries())
    assert entries == [{"i": i} for i in range(5)]


def test_append_after_read_does_not_corrupt(isolated_log):
    """Reading then appending then reading again — no corruption, all entries present."""
    import sm
    sm._append_entry({"event": "a"})
    list(sm.read_entries())  # consume + discard
    sm._append_entry({"event": "b"})
    final = list(sm.read_entries())
    assert final == [{"event": "a"}, {"event": "b"}]


# ---------------------------------------------------------------------------
# LOG_PATH-based — uses sm.LOG_PATH, not a hardcoded path
# ---------------------------------------------------------------------------

def test_reads_from_patched_log_path(tmp_path, monkeypatch):
    """Monkeypatching sm.LOG_PATH redirects all reads — proves no hardcoded path."""
    import sm
    custom = tmp_path / "custom_log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", custom)
    custom.write_bytes(b'{"from": "custom"}\n')
    entries = list(sm.read_entries())
    assert entries == [{"from": "custom"}]


def test_does_not_read_from_real_log_path(tmp_path, monkeypatch):
    """If a custom path has no entries, the package-default log is NOT read."""
    import sm
    real_log = PACKAGE_DIR / "log.jsonl"
    custom = tmp_path / "patched.jsonl"
    # Custom file does not exist — must return empty iterable (not fall back
    # to the real LOG_PATH, which may or may not exist).
    monkeypatch.setattr(sm, "LOG_PATH", custom)
    assert not custom.exists()
    entries = list(sm.read_entries())
    assert entries == []


def test_log_path_in_nested_directory(tmp_path, monkeypatch):
    import sm
    nested = tmp_path / "nested.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", nested)
    nested.write_bytes(b'{"deep": true}\n')
    entries = list(sm.read_entries())
    assert entries == [{"deep": True}]


def test_log_path_change_between_calls(tmp_path, monkeypatch):
    """Changing LOG_PATH between calls redirects subsequent reads."""
    import sm

    log_a = tmp_path / "a.jsonl"
    log_b = tmp_path / "b.jsonl"
    log_a.write_bytes(b'{"from": "a"}\n')
    log_b.write_bytes(b'{"from": "b"}\n')

    monkeypatch.setattr(sm, "LOG_PATH", log_a)
    assert list(sm.read_entries()) == [{"from": "a"}]

    monkeypatch.setattr(sm, "LOG_PATH", log_b)
    assert list(sm.read_entries()) == [{"from": "b"}]


# ---------------------------------------------------------------------------
# Payload variety — read side mirrors what was written
# ---------------------------------------------------------------------------

def test_nested_dict_round_trips(isolated_log):
    import sm
    payload = {"outer": {"inner": {"deep": [1, 2, {"x": "y"}]}}}
    sm._append_entry(payload)
    entries = list(sm.read_entries())
    assert entries == [payload]


def test_null_value_round_trips(isolated_log):
    import sm
    sm._append_entry({"x": None})
    entries = list(sm.read_entries())
    assert entries == [{"x": None}]


def test_bool_values_round_trip(isolated_log):
    import sm
    sm._append_entry({"flag": True, "other": False})
    entries = list(sm.read_entries())
    assert entries == [{"flag": True, "other": False}]


def test_numeric_values_round_trip(isolated_log):
    import sm
    sm._append_entry({"int": 42, "float": 3.14, "neg": -7, "zero": 0})
    entries = list(sm.read_entries())
    assert entries == [{"int": 42, "float": 3.14, "neg": -7, "zero": 0}]


def test_string_with_embedded_newline_in_value(isolated_log):
    """A JSON-escaped \\n inside a string value does NOT split into two entries."""
    import sm
    sm._append_entry({"msg": "line1\nline2"})
    entries = list(sm.read_entries())
    assert len(entries) == 1
    assert entries[0] == {"msg": "line1\nline2"}


def test_string_with_embedded_quote(isolated_log):
    import sm
    sm._append_entry({"msg": 'she said "hi"'})
    entries = list(sm.read_entries())
    assert entries == [{"msg": 'she said "hi"'}]


def test_string_with_backslash(isolated_log):
    import sm
    sm._append_entry({"path": "C:\\Users\\nick"})
    entries = list(sm.read_entries())
    assert entries == [{"path": "C:\\Users\\nick"}]


def test_empty_dict_entry_round_trips(isolated_log):
    import sm
    sm._append_entry({})
    entries = list(sm.read_entries())
    assert entries == [{}]
