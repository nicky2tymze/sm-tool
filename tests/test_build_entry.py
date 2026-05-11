"""Story 3 — pin the contract of `sm.build_entry`.

What this file pins:
  - Function signature and shape: `build_entry(type: str, content: dict) -> dict`,
    PUBLIC, callable, in `sm.__all__`, importable as `from sm import build_entry`,
    accepts (type, content) positionally.
  - Auto-stamped fields: every result has `id`, `type`, and `timestamp` keys.
      - `id` is a uuid4 hex (32 lowercase hex characters, no dashes), unique
        per call (no caching/reuse).
      - `timestamp` is ISO 8601 with explicit timezone offset (matches the
        suite convention — `.astimezone().isoformat()` — which produces local
        offset form, e.g. `+00:00` or `-07:00`). Parseable by
        `datetime.fromisoformat`. Monotonic across consecutive calls.
      - `type` is the value passed in (string, non-empty).
  - Reserved-key rejection: `content` containing top-level `id`, `type`, or
    `timestamp` raises `ValueError` naming the offending key. Case-sensitive
    (`ID`, `Type`, `TimeStamp` are NOT reserved). Nested keys are NOT flagged.
  - Type-param validation: empty string raises `ValueError`; whitespace-only
    string raises `ValueError`; non-string raises `TypeError` naming the
    offending type. (Strict `bool` rejection — `bool` is an `int` subclass.)
  - Content-param validation: non-dict raises `TypeError` naming the offending
    type. Dict subclass accepted.
  - Merge order: result dict order is `id`, `type`, `timestamp`, then content
    fields in their original insertion order (Python 3.7+ dict ordering).
  - Result independence: result is a SHALLOW copy — the top-level dict is
    a new object (adding/removing/overwriting top-level keys on the result
    does not affect the input, and vice versa), but nested values are
    shared references. Iter 2 Story 14 corrected this from an earlier
    overclaim of "deep independence" per LOCKED_DECISION 5.
  - Round-trip: an entry built by `build_entry`, written by `_append_entry`,
    read back by `read_entries`, equals (`==`) the original built dict.
  - Schema invariant: every emitted entry has `id`, `type`, `timestamp` plus
    its content; replaying the log via `read_entries` confirms this.
  - Empty content: `build_entry("foo", {})` is OK; result has only `id`,
    `type`, `timestamp`.
  - Many content fields and unicode keys/values survive.

Tests must FAIL on first run — `build_entry` does not exist yet. The Coder
downstream implements to satisfy these tests.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re
import sys
import time
from datetime import datetime, timezone

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

    Mirrors the suite convention (test_append_entry.py, test_read_entries.py).
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------

def test_function_exists_on_module():
    import sm
    assert hasattr(sm, "build_entry"), "sm.build_entry must exist"


def test_function_is_callable():
    import sm
    assert callable(sm.build_entry)


def test_function_name_is_public():
    """No leading underscore — public API."""
    import sm
    assert not sm.build_entry.__name__.startswith("_")
    assert sm.build_entry.__name__ == "build_entry"


def test_function_importable_directly():
    """`from sm import build_entry` succeeds — public-import form."""
    from sm import build_entry  # noqa: F401
    assert callable(build_entry)


def test_function_in_dunder_all():
    """Public function must be exported via __all__."""
    import sm
    assert hasattr(sm, "__all__"), "sm.__all__ must exist"
    assert "build_entry" in sm.__all__, (
        f"build_entry must be in __all__; got {sm.__all__!r}"
    )


def test_function_signature_accepts_two_positional_args():
    """`build_entry(type, content)` — two positional params."""
    import sm
    sig = inspect.signature(sm.build_entry)
    params = list(sig.parameters.values())
    # At least two positional-acceptable params (excluding self).
    assert len(params) >= 2, (
        f"build_entry must accept at least 2 args; got params={params!r}"
    )
    # First two must be positional-acceptable.
    for p in params[:2]:
        assert p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ), f"Param {p.name!r} must be positional-acceptable; got kind={p.kind!r}"


def test_function_callable_positionally():
    """The function works when called with positional args."""
    import sm
    result = sm.build_entry("smoke", {"event": "ok"})
    assert isinstance(result, dict)


def test_function_returns_dict():
    """Return type is a dict."""
    import sm
    result = sm.build_entry("smoke", {})
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Reserved keys in content — case-sensitive top-level rejection
# ---------------------------------------------------------------------------

def test_content_with_reserved_id_raises():
    import sm
    with pytest.raises(ValueError) as exc_info:
        sm.build_entry("smoke", {"id": "foo"})
    assert "id" in str(exc_info.value), (
        f"Error must name the offending key 'id'; got: {exc_info.value!r}"
    )


def test_content_with_reserved_type_raises():
    import sm
    with pytest.raises(ValueError) as exc_info:
        sm.build_entry("smoke", {"type": "other"})
    assert "type" in str(exc_info.value), (
        f"Error must name the offending key 'type'; got: {exc_info.value!r}"
    )


def test_content_with_reserved_timestamp_raises():
    import sm
    with pytest.raises(ValueError) as exc_info:
        sm.build_entry("smoke", {"timestamp": "2026-01-01T00:00:00+00:00"})
    assert "timestamp" in str(exc_info.value), (
        f"Error must name the offending key 'timestamp'; got: {exc_info.value!r}"
    )


def test_content_with_reserved_id_among_others_raises():
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("smoke", {"a": 1, "id": "x", "b": 2})


def test_content_with_reserved_type_among_others_raises():
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("smoke", {"a": 1, "type": "other", "b": 2})


def test_content_with_reserved_timestamp_among_others_raises():
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("smoke", {"a": 1, "timestamp": "x", "b": 2})


def test_content_with_multiple_reserved_keys_raises():
    """Even one offender is enough — but multiple definitely raise."""
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("smoke", {"id": "x", "type": "y", "timestamp": "z"})


def test_content_uppercase_id_allowed():
    """Reserved-key check is case-sensitive — `ID` is not reserved."""
    import sm
    result = sm.build_entry("smoke", {"ID": "user-123"})
    assert result["ID"] == "user-123"
    assert "ID" in result
    # The auto-stamped lowercase 'id' is also present and different.
    assert "id" in result
    assert result["id"] != "user-123"


def test_content_titlecase_type_allowed():
    """`Type` is not reserved (case-sensitive)."""
    import sm
    result = sm.build_entry("smoke", {"Type": "vehicle"})
    assert result["Type"] == "vehicle"
    assert result["type"] == "smoke"


def test_content_titlecase_timestamp_allowed():
    """`TimeStamp` is not reserved (case-sensitive)."""
    import sm
    result = sm.build_entry("smoke", {"TimeStamp": "yesterday"})
    assert result["TimeStamp"] == "yesterday"
    assert "timestamp" in result
    assert result["timestamp"] != "yesterday"


def test_content_uppercase_all_reserved_allowed():
    """`ID`, `TYPE`, `TIMESTAMP` — all distinct from lowercase reserved set."""
    import sm
    result = sm.build_entry(
        "smoke", {"ID": "a", "TYPE": "b", "TIMESTAMP": "c"}
    )
    assert result["ID"] == "a"
    assert result["TYPE"] == "b"
    assert result["TIMESTAMP"] == "c"


def test_content_with_nested_id_allowed():
    """Reserved-key check applies only at top level — nested `id` is fine."""
    import sm
    result = sm.build_entry("smoke", {"data": {"id": "nested-id"}})
    assert result["data"] == {"id": "nested-id"}
    # Top-level auto-stamped id is still present and is a hex32 string.
    assert HEX32_RE.match(result["id"])


def test_content_with_nested_type_allowed():
    import sm
    result = sm.build_entry("smoke", {"data": {"type": "nested-type"}})
    assert result["data"] == {"type": "nested-type"}
    assert result["type"] == "smoke"


def test_content_with_nested_timestamp_allowed():
    import sm
    result = sm.build_entry("smoke", {"data": {"timestamp": "2026-01-01"}})
    assert result["data"] == {"timestamp": "2026-01-01"}
    assert "timestamp" in result
    assert result["timestamp"] != "2026-01-01"


def test_content_with_empty_string_key_allowed():
    """An empty string key in content is a real key (not reserved). Allowed."""
    import sm
    result = sm.build_entry("smoke", {"": "blank-key"})
    assert result[""] == "blank-key"


def test_reserved_key_rejection_does_not_create_id():
    """Failed build_entry call doesn't have side effects (e.g. burning ids).
    This is a sanity check — the call raised, no result to inspect."""
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("smoke", {"id": "x"})
    # If we get here, no result was returned — test passes.


# ---------------------------------------------------------------------------
# `type` param — validation
# ---------------------------------------------------------------------------

def test_type_empty_string_raises_valueerror():
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("", {"event": "x"})


def test_type_whitespace_only_raises_valueerror():
    """A type that is only spaces/tabs is meaningless — reject."""
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("   ", {"event": "x"})


def test_type_tab_only_raises_valueerror():
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("\t", {"event": "x"})


def test_type_newline_only_raises_valueerror():
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("\n", {"event": "x"})


def test_type_none_raises_typeerror():
    import sm
    with pytest.raises(TypeError) as exc_info:
        sm.build_entry(None, {"event": "x"})
    msg = str(exc_info.value).lower()
    assert "none" in msg or "nonetype" in msg or "str" in msg, (
        f"TypeError should reference the offending type or expected type; got: {exc_info.value!r}"
    )


def test_type_int_raises_typeerror():
    import sm
    with pytest.raises(TypeError) as exc_info:
        sm.build_entry(42, {"event": "x"})
    msg = str(exc_info.value).lower()
    assert "int" in msg or "str" in msg


def test_type_float_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry(3.14, {"event": "x"})


def test_type_list_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry(["smoke"], {"event": "x"})


def test_type_dict_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry({"type": "smoke"}, {"event": "x"})


def test_type_bool_raises_typeerror():
    """`bool` is an int subclass — explicitly verify it's rejected too."""
    import sm
    with pytest.raises(TypeError):
        sm.build_entry(True, {"event": "x"})


def test_type_bytes_raises_typeerror():
    """bytes is not str."""
    import sm
    with pytest.raises(TypeError):
        sm.build_entry(b"smoke", {"event": "x"})


def test_type_tuple_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry(("smoke",), {"event": "x"})


def test_type_normal_string_accepted():
    """Sanity: a plain non-empty string is fine."""
    import sm
    result = sm.build_entry("smoke", {"event": "x"})
    assert result["type"] == "smoke"


def test_type_with_internal_spaces_accepted():
    """Spaces inside the type are fine; only purely-whitespace types reject."""
    import sm
    result = sm.build_entry("po review", {"event": "x"})
    assert result["type"] == "po review"


# ---------------------------------------------------------------------------
# `content` param — validation
# ---------------------------------------------------------------------------

def test_content_none_raises_typeerror():
    import sm
    with pytest.raises(TypeError) as exc_info:
        sm.build_entry("smoke", None)
    msg = str(exc_info.value).lower()
    assert "none" in msg or "nonetype" in msg or "dict" in msg, (
        f"TypeError should reference the offending or expected type; got: {exc_info.value!r}"
    )


def test_content_list_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry("smoke", [{"event": "x"}])


def test_content_str_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry("smoke", "event-x")


def test_content_int_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry("smoke", 42)


def test_content_tuple_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry("smoke", (("a", 1),))


def test_content_set_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry("smoke", {"a", "b"})


def test_content_bytes_raises_typeerror():
    import sm
    with pytest.raises(TypeError):
        sm.build_entry("smoke", b'{"event": "x"}')


def test_content_dict_subclass_accepted():
    """Dict subclass is still a dict — must be accepted."""
    import sm

    class MyDict(dict):
        pass

    d = MyDict()
    d["event"] = "subclass"
    result = sm.build_entry("smoke", d)
    assert result["event"] == "subclass"
    assert result["type"] == "smoke"


def test_content_empty_dict_accepted():
    """Empty content is valid — result has only auto-stamped fields."""
    import sm
    result = sm.build_entry("smoke", {})
    assert isinstance(result, dict)
    assert result["type"] == "smoke"


# ---------------------------------------------------------------------------
# `id` field — uuid4 hex (32 lowercase hex chars), unique per call
# ---------------------------------------------------------------------------

def test_id_is_string():
    import sm
    result = sm.build_entry("smoke", {})
    assert isinstance(result["id"], str)


def test_id_is_32_chars():
    import sm
    result = sm.build_entry("smoke", {})
    assert len(result["id"]) == 32, (
        f"id must be 32 chars; got {len(result['id'])}: {result['id']!r}"
    )


def test_id_is_lowercase_hex():
    import sm
    result = sm.build_entry("smoke", {})
    assert HEX32_RE.match(result["id"]), (
        f"id must be 32 lowercase hex chars; got {result['id']!r}"
    )


def test_id_no_dashes():
    """uuid4().hex format — no dashes, just hex."""
    import sm
    result = sm.build_entry("smoke", {})
    assert "-" not in result["id"], (
        f"id must have no dashes; got {result['id']!r}"
    )


def test_id_no_uppercase():
    import sm
    result = sm.build_entry("smoke", {})
    assert result["id"] == result["id"].lower(), (
        f"id must be all lowercase; got {result['id']!r}"
    )


def test_two_calls_have_different_ids():
    import sm
    a = sm.build_entry("smoke", {})
    b = sm.build_entry("smoke", {})
    assert a["id"] != b["id"], (
        f"Two consecutive calls must produce different ids; got {a['id']!r} == {b['id']!r}"
    )


def test_many_calls_all_unique_ids():
    """1000 calls, all ids unique — no caching/reuse."""
    import sm
    ids = {sm.build_entry("smoke", {})["id"] for _ in range(1000)}
    assert len(ids) == 1000, (
        f"All ids must be unique across 1000 calls; got {len(ids)} unique"
    )


def test_id_unique_across_different_types():
    """Even with different types, ids never repeat."""
    import sm
    a = sm.build_entry("alpha", {"k": "v"})
    b = sm.build_entry("beta", {"k": "v"})
    c = sm.build_entry("gamma", {"k": "v"})
    assert len({a["id"], b["id"], c["id"]}) == 3


def test_id_unique_across_same_content():
    """Identical content does NOT produce identical ids — id is fresh per call."""
    import sm
    a = sm.build_entry("smoke", {"x": 1})
    b = sm.build_entry("smoke", {"x": 1})
    assert a["id"] != b["id"]


def test_id_present_in_result():
    import sm
    result = sm.build_entry("smoke", {"foo": "bar"})
    assert "id" in result


def test_id_does_not_contain_invalid_chars():
    """No spaces, no underscores, no special chars in uuid4 hex."""
    import sm
    result = sm.build_entry("smoke", {})
    forbidden = set(" _-/\\:.+")
    assert not (set(result["id"]) & forbidden), (
        f"id contains forbidden chars: {result['id']!r}"
    )


# ---------------------------------------------------------------------------
# `timestamp` field — ISO 8601 with timezone offset
# ---------------------------------------------------------------------------

def test_timestamp_is_string():
    import sm
    result = sm.build_entry("smoke", {})
    assert isinstance(result["timestamp"], str)


def test_timestamp_is_present():
    import sm
    result = sm.build_entry("smoke", {})
    assert "timestamp" in result


def test_timestamp_contains_T_separator():
    """ISO 8601 separates date and time with `T`."""
    import sm
    result = sm.build_entry("smoke", {})
    assert "T" in result["timestamp"], (
        f"timestamp must contain 'T'; got {result['timestamp']!r}"
    )


def test_timestamp_has_timezone_offset():
    """Suite convention is `.astimezone().isoformat()` — has `+HH:MM` or `-HH:MM`."""
    import sm
    result = sm.build_entry("smoke", {})
    ts = result["timestamp"]
    # Match offset at end: `[+-]HH:MM`.
    assert re.search(r"[+-]\d{2}:\d{2}$", ts), (
        f"timestamp must end with a timezone offset like +HH:MM; got {ts!r}"
    )


def test_timestamp_parseable_by_fromisoformat():
    import sm
    result = sm.build_entry("smoke", {})
    # Raises if not parseable.
    parsed = datetime.fromisoformat(result["timestamp"])
    assert parsed.tzinfo is not None, (
        f"Parsed timestamp must be tz-aware; got {parsed!r}"
    )


def test_timestamp_close_to_system_clock():
    """The stamped timestamp is within a few seconds of `now`."""
    import sm
    before = datetime.now(timezone.utc)
    result = sm.build_entry("smoke", {})
    after = datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(result["timestamp"])
    # Compare in UTC.
    parsed_utc = parsed.astimezone(timezone.utc)
    # Allow generous slack (10 seconds) for slow CI.
    assert before <= parsed_utc + _SLACK, (
        f"timestamp {parsed_utc!r} too far before call ({before!r})"
    )
    assert parsed_utc <= after + _SLACK, (
        f"timestamp {parsed_utc!r} too far after call ({after!r})"
    )


def test_timestamps_monotonic_across_calls():
    """Successive calls produce same-or-later timestamps."""
    import sm
    timestamps = []
    for _ in range(10):
        t = sm.build_entry("smoke", {})["timestamp"]
        timestamps.append(datetime.fromisoformat(t))
        # Tiny pause to avoid same-microsecond ties; not strictly required
        # since `<=` is acceptable, but reduces flake.
    for i in range(1, len(timestamps)):
        assert timestamps[i] >= timestamps[i - 1], (
            f"timestamp regressed: {timestamps[i-1]!r} -> {timestamps[i]!r}"
        )


def test_timestamp_does_not_use_zulu_suffix():
    """The suite uses `.astimezone().isoformat()` — produces `+00:00`, not `Z`.

    A `Z` suffix would mean a different code path (e.g. manual formatting).
    Keep the suite consistent.
    """
    import sm
    result = sm.build_entry("smoke", {})
    assert not result["timestamp"].endswith("Z"), (
        f"timestamp must use offset form (+HH:MM), not Z; got {result['timestamp']!r}"
    )


def test_timestamp_distinct_per_call_or_monotonic():
    """Two consecutive calls produce same-or-later (never earlier) timestamps."""
    import sm
    a = sm.build_entry("smoke", {})["timestamp"]
    b = sm.build_entry("smoke", {})["timestamp"]
    a_dt = datetime.fromisoformat(a)
    b_dt = datetime.fromisoformat(b)
    assert b_dt >= a_dt


def test_timestamp_iso_format_standard_shape():
    """Shape: `YYYY-MM-DDTHH:MM:SS[.ffffff][+HH:MM | -HH:MM]`."""
    import sm
    result = sm.build_entry("smoke", {})
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$"
    assert re.match(pattern, result["timestamp"]), (
        f"timestamp shape unexpected; got {result['timestamp']!r}"
    )


# Slack for time comparisons (10s).
from datetime import timedelta as _td  # noqa: E402

_SLACK = _td(seconds=10)


# ---------------------------------------------------------------------------
# Merge & ordering
# ---------------------------------------------------------------------------

def test_result_has_id_first():
    """Auto-stamped fields come first; `id` is the very first key."""
    import sm
    result = sm.build_entry("smoke", {"a": 1, "b": 2})
    keys = list(result.keys())
    assert keys[0] == "id", f"First key must be 'id'; got {keys!r}"


def test_result_has_type_second():
    import sm
    result = sm.build_entry("smoke", {"a": 1, "b": 2})
    keys = list(result.keys())
    assert keys[1] == "type", f"Second key must be 'type'; got {keys!r}"


def test_result_has_timestamp_third():
    import sm
    result = sm.build_entry("smoke", {"a": 1, "b": 2})
    keys = list(result.keys())
    assert keys[2] == "timestamp", f"Third key must be 'timestamp'; got {keys!r}"


def test_content_keys_follow_auto_stamped():
    """After id/type/timestamp come content keys in original order."""
    import sm
    result = sm.build_entry("smoke", {"alpha": 1, "beta": 2, "gamma": 3})
    keys = list(result.keys())
    assert keys == ["id", "type", "timestamp", "alpha", "beta", "gamma"], (
        f"Key order unexpected: {keys!r}"
    )


def test_content_key_order_preserved():
    """Insertion order of content fields is preserved."""
    import sm
    # Build content with specific insertion order.
    content = {}
    content["zeta"] = 1
    content["alpha"] = 2
    content["mu"] = 3
    content["beta"] = 4
    result = sm.build_entry("smoke", content)
    keys_after_stamp = list(result.keys())[3:]
    assert keys_after_stamp == ["zeta", "alpha", "mu", "beta"]


def test_content_values_merged_at_top_level():
    """Content fields appear at top level of result (not nested under 'content')."""
    import sm
    result = sm.build_entry("smoke", {"foo": "bar", "n": 42})
    assert result["foo"] == "bar"
    assert result["n"] == 42
    assert "content" not in result, (
        "Content fields must merge at top level, not nest under 'content'"
    )


def test_result_only_contains_expected_keys():
    """Result has exactly auto-stamped keys + content keys, nothing else."""
    import sm
    result = sm.build_entry("smoke", {"a": 1, "b": 2})
    expected = {"id", "type", "timestamp", "a", "b"}
    assert set(result.keys()) == expected


def test_empty_content_result_has_only_auto_stamped_keys():
    import sm
    result = sm.build_entry("smoke", {})
    assert set(result.keys()) == {"id", "type", "timestamp"}
    assert list(result.keys()) == ["id", "type", "timestamp"]


def test_type_value_matches_input():
    """Whatever string was passed as `type` is the value of result['type']."""
    import sm
    result = sm.build_entry("po_review", {})
    assert result["type"] == "po_review"


def test_content_values_are_unchanged():
    """Values pass through verbatim — no transformation."""
    import sm
    payload = {"str": "hello", "int": 7, "list": [1, 2, 3], "dict": {"k": "v"}}
    result = sm.build_entry("smoke", payload)
    assert result["str"] == "hello"
    assert result["int"] == 7
    assert result["list"] == [1, 2, 3]
    assert result["dict"] == {"k": "v"}


# ---------------------------------------------------------------------------
# Result independence — mutating result doesn't affect content; vice versa
# ---------------------------------------------------------------------------

def test_mutating_result_does_not_affect_content():
    import sm
    content = {"a": 1, "b": 2}
    result = sm.build_entry("smoke", content)
    result["a"] = 999
    result["new_key"] = "new"
    assert content == {"a": 1, "b": 2}, (
        f"Mutating result must not affect input content; got {content!r}"
    )


def test_mutating_content_does_not_affect_result():
    import sm
    content = {"a": 1, "b": 2}
    result = sm.build_entry("smoke", content)
    content["a"] = 999
    content["new_key"] = "new"
    assert result["a"] == 1
    assert result["b"] == 2
    assert "new_key" not in result


def test_result_is_new_dict_object():
    """The returned dict is not the same object as the input content."""
    import sm
    content = {"a": 1}
    result = sm.build_entry("smoke", content)
    assert result is not content


def test_clearing_content_does_not_clear_result():
    import sm
    content = {"a": 1, "b": 2}
    result = sm.build_entry("smoke", content)
    content.clear()
    assert result["a"] == 1
    assert result["b"] == 2


def test_deleting_from_content_does_not_affect_result():
    import sm
    content = {"a": 1, "b": 2}
    result = sm.build_entry("smoke", content)
    del content["a"]
    assert result["a"] == 1
    assert "a" in result


def test_two_results_are_distinct_objects():
    """Two separate calls return two distinct dict objects."""
    import sm
    a = sm.build_entry("smoke", {"x": 1})
    b = sm.build_entry("smoke", {"x": 1})
    assert a is not b


def test_mutating_one_result_does_not_affect_another():
    import sm
    a = sm.build_entry("smoke", {"x": 1})
    b = sm.build_entry("smoke", {"x": 1})
    a["x"] = 999
    assert b["x"] == 1


# ---------------------------------------------------------------------------
# Round-trip — build → append → read
# ---------------------------------------------------------------------------

def test_round_trip_single_entry(isolated_log):
    """Build, append, read back — equal."""
    import sm
    built = sm.build_entry("note", {"text": "hello"})
    sm._append_entry(built)
    entries = list(sm.read_entries())
    assert entries == [built]


def test_round_trip_multiple_entries(isolated_log):
    import sm
    built = []
    for i in range(5):
        b = sm.build_entry("note", {"i": i, "text": f"entry-{i}"})
        sm._append_entry(b)
        built.append(b)
    entries = list(sm.read_entries())
    assert entries == built


def test_round_trip_preserves_id_type_timestamp(isolated_log):
    """The auto-stamped fields survive the round-trip exactly."""
    import sm
    built = sm.build_entry("note", {"text": "hi"})
    sm._append_entry(built)
    entries = list(sm.read_entries())
    assert len(entries) == 1
    assert entries[0]["id"] == built["id"]
    assert entries[0]["type"] == built["type"]
    assert entries[0]["timestamp"] == built["timestamp"]


def test_round_trip_preserves_unicode(isolated_log):
    """Unicode in content survives the build → append → read round-trip."""
    import sm
    built = sm.build_entry("note", {"msg": "héllo 日本語 🔥"})
    sm._append_entry(built)
    entries = list(sm.read_entries())
    assert entries == [built]


def test_round_trip_many_entries_each_has_unique_id(isolated_log):
    """Replaying the log: every entry has its own unique id."""
    import sm
    for i in range(20):
        sm._append_entry(sm.build_entry("note", {"i": i}))
    entries = list(sm.read_entries())
    ids = [e["id"] for e in entries]
    assert len(set(ids)) == 20


# ---------------------------------------------------------------------------
# Schema invariant — every result has id+type+timestamp keys with right shapes
# ---------------------------------------------------------------------------

def test_every_result_has_three_auto_stamped_keys():
    import sm
    for content in [{}, {"a": 1}, {"x": "y", "n": 42}, {"deep": {"nested": True}}]:
        result = sm.build_entry("smoke", content)
        assert "id" in result
        assert "type" in result
        assert "timestamp" in result


def test_every_result_id_is_hex32():
    import sm
    for _ in range(20):
        result = sm.build_entry("smoke", {})
        assert HEX32_RE.match(result["id"]), (
            f"Every id must be 32 lowercase hex chars; got {result['id']!r}"
        )


def test_every_result_type_is_nonempty_string():
    import sm
    for t in ["smoke", "po_review", "standup_close", "x", "long_type_name_here"]:
        result = sm.build_entry(t, {})
        assert isinstance(result["type"], str)
        assert len(result["type"]) > 0
        assert result["type"] == t


def test_every_result_timestamp_parseable():
    import sm
    for _ in range(10):
        result = sm.build_entry("smoke", {})
        # Will raise if not parseable.
        datetime.fromisoformat(result["timestamp"])


def test_replay_log_schema_invariant_holds(isolated_log):
    """Spec clause: 'replay the log and assert on every entry'."""
    import sm
    # Emit a variety of entries through the canonical path.
    sm._append_entry(sm.build_entry("alpha", {}))
    sm._append_entry(sm.build_entry("beta", {"x": 1}))
    sm._append_entry(sm.build_entry("gamma", {"y": "z", "n": 42}))
    sm._append_entry(sm.build_entry("delta", {"deep": {"k": "v"}}))

    entries = list(sm.read_entries())
    assert len(entries) == 4
    for e in entries:
        # Every entry has the schema invariant.
        assert "id" in e
        assert "type" in e
        assert "timestamp" in e
        assert HEX32_RE.match(e["id"])
        assert isinstance(e["type"], str) and len(e["type"]) > 0
        # Parseable timestamp.
        datetime.fromisoformat(e["timestamp"])


# ---------------------------------------------------------------------------
# Empty content
# ---------------------------------------------------------------------------

def test_empty_content_succeeds():
    import sm
    result = sm.build_entry("smoke", {})
    assert isinstance(result, dict)


def test_empty_content_result_has_three_keys():
    import sm
    result = sm.build_entry("smoke", {})
    assert len(result) == 3


def test_empty_content_keys_exactly():
    import sm
    result = sm.build_entry("smoke", {})
    assert set(result.keys()) == {"id", "type", "timestamp"}


# ---------------------------------------------------------------------------
# Many content fields
# ---------------------------------------------------------------------------

def test_50_content_fields_preserved():
    """A content dict with 50 fields — every one survives."""
    import sm
    content = {f"field_{i}": i for i in range(50)}
    result = sm.build_entry("smoke", content)
    for i in range(50):
        assert result[f"field_{i}"] == i


def test_100_content_fields_preserved():
    import sm
    content = {f"k{i}": f"v{i}" for i in range(100)}
    result = sm.build_entry("smoke", content)
    for i in range(100):
        assert result[f"k{i}"] == f"v{i}"


def test_many_content_fields_preserve_order():
    import sm
    keys_in = [f"k{i}" for i in range(30)]
    content = {k: i for i, k in enumerate(keys_in)}
    result = sm.build_entry("smoke", content)
    # First 3 keys are the auto-stamped, then the content keys in order.
    keys_out = list(result.keys())
    assert keys_out[:3] == ["id", "type", "timestamp"]
    assert keys_out[3:] == keys_in


def test_many_content_fields_count_matches():
    import sm
    content = {f"k{i}": i for i in range(30)}
    result = sm.build_entry("smoke", content)
    # 3 auto-stamped + 30 content = 33.
    assert len(result) == 33


# ---------------------------------------------------------------------------
# Unicode in type / content
# ---------------------------------------------------------------------------

def test_type_with_unicode_accepted():
    """Unicode in `type` is allowed (still a non-empty string)."""
    import sm
    result = sm.build_entry("événement", {"x": 1})
    assert result["type"] == "événement"


def test_content_with_unicode_keys():
    import sm
    payload = {"ключ": "значение", "混合": "value"}
    result = sm.build_entry("smoke", payload)
    assert result["ключ"] == "значение"
    assert result["混合"] == "value"


def test_content_with_unicode_values():
    import sm
    result = sm.build_entry("smoke", {"msg": "héllo 日本語 🔥"})
    assert result["msg"] == "héllo 日本語 🔥"


def test_content_with_emoji_value():
    import sm
    result = sm.build_entry("smoke", {"reaction": "🎉🔥💧"})
    assert result["reaction"] == "🎉🔥💧"


def test_content_with_unicode_and_ascii_mix():
    import sm
    payload = {"ascii_key": "ascii_value", "ключ": "значение", "混合": [1, "ñ"]}
    result = sm.build_entry("smoke", payload)
    for k, v in payload.items():
        assert result[k] == v


# ---------------------------------------------------------------------------
# __all__ pollution check — only public names exported
# ---------------------------------------------------------------------------

def test_dunder_all_does_not_export_private_helpers():
    """Private helpers (e.g. `_append_entry`, `_now_iso`, `_new_id`) must not
    be in `__all__`."""
    import sm
    if hasattr(sm, "__all__"):
        for name in sm.__all__:
            assert not name.startswith("_"), (
                f"__all__ must not export private name {name!r}; got {sm.__all__!r}"
            )


def test_dunder_all_includes_build_entry():
    """Defensive duplicate of smoke — confirm `build_entry` is in __all__."""
    import sm
    assert "build_entry" in sm.__all__


def test_dunder_all_does_not_include_append_entry():
    """`_append_entry` is private — never in __all__."""
    import sm
    assert "_append_entry" not in sm.__all__


# ---------------------------------------------------------------------------
# Type strip-check edge cases — leading/trailing whitespace in `type`
# ---------------------------------------------------------------------------

def test_type_with_leading_trailing_spaces_value_handling():
    """Pin: a type that strips to non-empty is accepted; the exact value passed
    through is what ends up in result['type']. (Implementation may strip or
    pass through — but any non-whitespace-only string must succeed.)"""
    import sm
    # Whitespace stripping is implementation-defined for non-empty types; what
    # IS pinned is that this call does NOT raise (strip yields a non-empty type).
    result = sm.build_entry("  smoke  ", {"x": 1})
    # Result type is either "  smoke  " (passthrough) or "smoke" (stripped).
    # Both are acceptable as long as the core letters are present.
    assert "smoke" in result["type"]


def test_type_only_whitespace_raises():
    """A type that strips to empty must raise (whitespace-only)."""
    import sm
    with pytest.raises(ValueError):
        sm.build_entry("   \t  ", {"x": 1})


# ---------------------------------------------------------------------------
# Auto-stamped values are not overwritten by content reserved-key collision
# (covered by reserved-key rejection — but pin the inverse direction too)
# ---------------------------------------------------------------------------

def test_id_value_not_user_controlled():
    """The id stamped by build_entry is NOT pulled from any content field."""
    import sm
    # Pass content that, if naively merged, might have shadowed id (already
    # rejected at top-level; but use unrelated keys here).
    result = sm.build_entry("smoke", {"my_custom_id": "fake-id-123"})
    assert result["id"] != "fake-id-123"
    assert HEX32_RE.match(result["id"])


def test_timestamp_value_not_user_controlled():
    """The timestamp stamped is NOT pulled from any content field."""
    import sm
    result = sm.build_entry("smoke", {"event_time": "1999-01-01T00:00:00+00:00"})
    assert result["timestamp"] != "1999-01-01T00:00:00+00:00"


def test_type_value_only_from_param():
    """The result['type'] is exactly the param, never from content."""
    import sm
    result = sm.build_entry("alpha", {"category": "beta"})
    assert result["type"] == "alpha"


# ---------------------------------------------------------------------------
# Source-of-truth invariant — every log write goes through build_entry
# (Story 3 acceptance: "no module constructs entry dicts inline")
#
# NOTE: The weak structural test that lived here previously
# (`test_no_inline_entry_construction_in_sm_module`, body =
# `assert "def build_entry" in src`) was removed in Iter 2 Story 14.
# It was replaced by a properly tightened AST-walking version in
# `tests/test_retro_build_entry_honesty.py` that fails if ANY inline
# dict literal with all three reserved keys is found outside
# `build_entry`'s body. See Story 14 retro item 5.
# ---------------------------------------------------------------------------


def test_build_entry_output_directly_acceptable_to_append_entry(isolated_log):
    """`build_entry` output is the canonical input to `_append_entry`."""
    import sm
    built = sm.build_entry("smoke", {"event": "test"})
    # No exception — built is a dict, no reserved keys violated by self.
    sm._append_entry(built)
    entries = list(sm.read_entries())
    assert entries == [built]


# ---------------------------------------------------------------------------
# JSON-serializability — build_entry result is always JSON-serializable
# ---------------------------------------------------------------------------

def test_result_is_json_serializable_empty_content():
    import sm
    result = sm.build_entry("smoke", {})
    json.dumps(result)  # raises if not serializable


def test_result_is_json_serializable_simple_content():
    import sm
    result = sm.build_entry("smoke", {"a": 1, "b": "hello", "c": [1, 2, 3]})
    s = json.dumps(result)
    parsed = json.loads(s)
    assert parsed == result


def test_result_is_json_serializable_unicode():
    import sm
    result = sm.build_entry("smoke", {"msg": "héllo 日本語"})
    s = json.dumps(result, ensure_ascii=False)
    parsed = json.loads(s)
    assert parsed == result


def test_result_round_trips_through_json():
    """Round-trip via json.dumps/loads is identity for the result."""
    import sm
    result = sm.build_entry("smoke", {"x": 1, "y": [1, 2], "z": {"deep": True}})
    s = json.dumps(result)
    loaded = json.loads(s)
    assert loaded == result


# ---------------------------------------------------------------------------
# Defensive: input content with non-serializable values
# ---------------------------------------------------------------------------
# Story 3 acceptance is silent on whether build_entry pre-validates content
# for JSON-serializability. _append_entry already raises on non-serializable
# values pre-IO. We do NOT pin behavior at build time — that's Story 1's lane.
# But we do pin that build_entry returns a dict in normal cases.

def test_content_with_serializable_nested_dict_works():
    import sm
    content = {"nested": {"deep": {"deeper": [1, 2, {"x": "y"}]}}}
    result = sm.build_entry("smoke", content)
    assert result["nested"] == content["nested"]


def test_content_with_none_value_works():
    import sm
    result = sm.build_entry("smoke", {"x": None})
    assert result["x"] is None


def test_content_with_bool_values_works():
    import sm
    result = sm.build_entry("smoke", {"flag": True, "other": False})
    assert result["flag"] is True
    assert result["other"] is False


def test_content_with_numeric_values_works():
    import sm
    result = sm.build_entry("smoke", {"int": 42, "float": 3.14, "neg": -7, "zero": 0})
    assert result["int"] == 42
    assert result["float"] == 3.14
    assert result["neg"] == -7
    assert result["zero"] == 0


def test_content_with_list_values_works():
    import sm
    result = sm.build_entry("smoke", {"items": [1, "two", 3.0, None, True]})
    assert result["items"] == [1, "two", 3.0, None, True]
