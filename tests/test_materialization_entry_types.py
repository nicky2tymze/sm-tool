"""Iter 3 v2 Sprint 1 Story 5 — pin canonical shape of the two new log
entry types introduced for req-2 (file materialization).

This file is the FOUNDATION of req-2. Subsequent stories implement the
write dispatcher (Story 6), collision handling (Story 7), and pipeline
wiring (Story 8). Story 5 only PINS THE SHAPE CONTRACT — the entry types
themselves, exercised through `build_entry`.

Background on the architectural translation
-------------------------------------------
The SM Agent's acceptance criterion describes a `LogEntry` subclass
hierarchy ("LogEntry subclasses materialized_file(...) and
materialization_status(...) defined; both serialize/deserialize
correctly"). sm-tool does NOT have a `LogEntry` class hierarchy —
entries are dicts built via `build_entry(entry_type: str, payload:
dict)` which returns a dict with `id`, `type`, `timestamp` auto-stamped
plus the payload merged at the top level.

So Story 5 is implemented as two PUBLIC factory helpers that PIN THE
CANONICAL SHAPE of each new entry type and validate inputs at the call
site (rather than relying on a downstream consumer to discover bad
data on replay). The factories return the dict produced by
`build_entry`, with the new entry-type strings ("materialized_file" /
"materialization_status") and the payload fields merged at the top
level. Both round-trip through `_append_entry` / `read_entries`
byte-for-byte, same as every other entry.

What this file pins
-------------------
1. `make_materialized_file_entry(story_id, role, target_path,
    byte_count, sha256) -> dict` — PUBLIC, in `sm.__all__`.
    - Returns `build_entry("materialized_file", {...})` with the five
      fields merged at the top level.
    - Validates inputs and raises `ValueError` (naming the offending
      field) on bad data. Type errors raise `TypeError`.
2. `make_materialization_status_entry(story_id, status, reason) ->
    dict` — PUBLIC, in `sm.__all__`.
    - Returns `build_entry("materialization_status", {...})` with the
      three fields merged at the top level.
    - Validates inputs and raises `ValueError` on bad data.
3. Round-trip through `_append_entry` / `read_entries` is exact for
    both entry types.

Status allowlist for `materialization_status`
---------------------------------------------
Picked by the TestWriter, narrow-by-default: {"materialized",
"collision", "rejected"}. Rationale:
  - "materialized" — successful write.
  - "collision" — target path exists (req-2 default policy is
    .candidate sidecar + diff, but the status row still fires).
  - "rejected" — Reviewer rejection per req-2 spec line 58–60. The
    files are NOT rolled back, but a `materialization_status` row
    carries the rejection reason.
The spec line "On Reviewer rejection, materialized files are NOT
rolled back" rules out a "rolled_back" status by current design;
omitting it keeps the allowlist honest. If a future story adds rollback
semantics, this allowlist will extend — that's a deliberate change and
a separate test edit, not a silent expansion.

These tests must FAIL on first run — neither factory exists yet. The
Coder downstream implements `sm.make_materialized_file_entry` and
`sm.make_materialization_status_entry` to satisfy these tests.

Cascade tests flagged
---------------------
- `test_persistence_audit.py` enumerates `sm.__all__`. Adding the two
  new public names will cause that audit to surface them; it's an
  inventory test (not a hardcoded-set test), so it will continue to
  pass — no edit required. If the Coder discovers otherwise, that's a
  Story-5 finding to file, not a test-of-tests edit.
- No existing test exercises `materialized_file` or
  `materialization_status` as entry types yet, so no other suite
  should regress.
"""

from __future__ import annotations

import pathlib
import sys

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

    Mirrors the suite convention (test_append_entry.py,
    test_read_entries.py, test_build_entry.py).
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)
    return log_file


# A canonical valid sha256 (64 lowercase hex chars). Use this anywhere a
# valid digest is required so we never accidentally violate format in a
# negative-case test by passing a malformed-but-not-the-focus value.
VALID_SHA256 = "a" * 64
ANOTHER_VALID_SHA256 = "0123456789abcdef" * 4  # 64 lowercase hex chars


VALID_ROLES = ("test_writer", "coder", "reviewer")
VALID_STATUSES = ("materialized", "collision", "rejected")


# ---------------------------------------------------------------------------
# A. make_materialized_file_entry — smoke
# ---------------------------------------------------------------------------

def test_make_materialized_file_entry_exists():
    """`sm.make_materialized_file_entry` is a module attribute."""
    import sm

    assert hasattr(sm, "make_materialized_file_entry"), (
        "sm.make_materialized_file_entry must exist"
    )


def test_make_materialized_file_entry_is_callable():
    import sm

    assert callable(sm.make_materialized_file_entry)


def test_make_materialized_file_entry_in_dunder_all():
    """Factory is part of the public API."""
    import sm

    assert "make_materialized_file_entry" in sm.__all__, (
        "make_materialized_file_entry must be in sm.__all__"
    )


def test_make_materialized_file_entry_returns_dict():
    import sm

    result = sm.make_materialized_file_entry(
        story_id="s1",
        role="test_writer",
        target_path="tests/test_foo.py",
        byte_count=123,
        sha256=VALID_SHA256,
    )
    assert isinstance(result, dict)


def test_make_materialized_file_entry_type_is_materialized_file():
    """The entry's `type` field is the canonical string."""
    import sm

    entry = sm.make_materialized_file_entry(
        story_id="s1",
        role="coder",
        target_path="sm.py",
        byte_count=42,
        sha256=VALID_SHA256,
    )
    assert entry["type"] == "materialized_file"


def test_make_materialized_file_entry_all_five_fields_top_level():
    """All five payload fields appear at the TOP LEVEL of the entry
    (not nested under `payload` or `content`)."""
    import sm

    entry = sm.make_materialized_file_entry(
        story_id="s1",
        role="reviewer",
        target_path="docs/README.md",
        byte_count=7,
        sha256=VALID_SHA256,
    )
    assert entry["story_id"] == "s1"
    assert entry["role"] == "reviewer"
    assert entry["target_path"] == "docs/README.md"
    assert entry["byte_count"] == 7
    assert entry["sha256"] == VALID_SHA256


def test_make_materialized_file_entry_has_auto_stamped_fields():
    """build_entry stamps id and timestamp — the factory must inherit
    those, not strip them."""
    import sm

    entry = sm.make_materialized_file_entry(
        story_id="s1",
        role="coder",
        target_path="sm.py",
        byte_count=10,
        sha256=VALID_SHA256,
    )
    assert "id" in entry
    assert "timestamp" in entry
    # id is the same 32-char lowercase hex shape that build_entry emits.
    assert isinstance(entry["id"], str)
    assert len(entry["id"]) == 32


def test_make_materialized_file_entry_accepts_byte_count_zero():
    """Zero bytes is non-negative — empty files are legitimate."""
    import sm

    entry = sm.make_materialized_file_entry(
        story_id="s1",
        role="test_writer",
        target_path="tests/test_empty.py",
        byte_count=0,
        sha256=VALID_SHA256,
    )
    assert entry["byte_count"] == 0


# ---------------------------------------------------------------------------
# B. make_materialized_file_entry — validation
# ---------------------------------------------------------------------------

def test_make_materialized_file_entry_rejects_empty_story_id():
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialized_file_entry(
            story_id="",
            role="coder",
            target_path="sm.py",
            byte_count=1,
            sha256=VALID_SHA256,
        )
    assert "story_id" in str(exc_info.value), (
        f"Error must name the offending field 'story_id'; "
        f"got: {exc_info.value!r}"
    )


def test_make_materialized_file_entry_rejects_unknown_role():
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialized_file_entry(
            story_id="s1",
            role="architect",  # not in the allowlist
            target_path="sm.py",
            byte_count=1,
            sha256=VALID_SHA256,
        )
    assert "role" in str(exc_info.value), (
        f"Error must name the offending field 'role'; "
        f"got: {exc_info.value!r}"
    )


def test_make_materialized_file_entry_accepts_each_valid_role():
    """Every role in {test_writer, coder, reviewer} is accepted."""
    import sm

    for role in VALID_ROLES:
        entry = sm.make_materialized_file_entry(
            story_id="s1",
            role=role,
            target_path="x",
            byte_count=1,
            sha256=VALID_SHA256,
        )
        assert entry["role"] == role


def test_make_materialized_file_entry_rejects_empty_target_path():
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialized_file_entry(
            story_id="s1",
            role="coder",
            target_path="",
            byte_count=1,
            sha256=VALID_SHA256,
        )
    assert "target_path" in str(exc_info.value), (
        f"Error must name 'target_path'; got: {exc_info.value!r}"
    )


def test_make_materialized_file_entry_rejects_negative_byte_count():
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialized_file_entry(
            story_id="s1",
            role="coder",
            target_path="sm.py",
            byte_count=-1,
            sha256=VALID_SHA256,
        )
    assert "byte_count" in str(exc_info.value), (
        f"Error must name 'byte_count'; got: {exc_info.value!r}"
    )


def test_make_materialized_file_entry_rejects_sha256_wrong_length():
    """sha256 must be exactly 64 chars."""
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialized_file_entry(
            story_id="s1",
            role="coder",
            target_path="sm.py",
            byte_count=1,
            sha256="abc123",  # 6 chars, not 64
        )
    assert "sha256" in str(exc_info.value), (
        f"Error must name 'sha256'; got: {exc_info.value!r}"
    )


def test_make_materialized_file_entry_rejects_sha256_uppercase():
    """sha256 must be LOWERCASE hex."""
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialized_file_entry(
            story_id="s1",
            role="coder",
            target_path="sm.py",
            byte_count=1,
            sha256="A" * 64,  # 64 chars, but uppercase
        )
    assert "sha256" in str(exc_info.value), (
        f"Error must name 'sha256'; got: {exc_info.value!r}"
    )


def test_make_materialized_file_entry_rejects_sha256_non_hex():
    """sha256 must contain only [0-9a-f]."""
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialized_file_entry(
            story_id="s1",
            role="coder",
            target_path="sm.py",
            byte_count=1,
            sha256="z" * 64,  # 64 chars but 'z' is not hex
        )
    assert "sha256" in str(exc_info.value), (
        f"Error must name 'sha256'; got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# C. make_materialization_status_entry — smoke
# ---------------------------------------------------------------------------

def test_make_materialization_status_entry_exists():
    import sm

    assert hasattr(sm, "make_materialization_status_entry"), (
        "sm.make_materialization_status_entry must exist"
    )


def test_make_materialization_status_entry_in_dunder_all():
    import sm

    assert "make_materialization_status_entry" in sm.__all__, (
        "make_materialization_status_entry must be in sm.__all__"
    )


def test_make_materialization_status_entry_type_is_materialization_status():
    import sm

    entry = sm.make_materialization_status_entry(
        story_id="s1",
        status="materialized",
        reason="ok",
    )
    assert entry["type"] == "materialization_status"


def test_make_materialization_status_entry_all_three_fields_top_level():
    import sm

    entry = sm.make_materialization_status_entry(
        story_id="s1",
        status="rejected",
        reason="reviewer veto: missing tests",
    )
    assert entry["story_id"] == "s1"
    assert entry["status"] == "rejected"
    assert entry["reason"] == "reviewer veto: missing tests"


def test_make_materialization_status_entry_accepts_each_valid_status():
    """Each status in {materialized, collision, rejected} is accepted."""
    import sm

    for status in VALID_STATUSES:
        entry = sm.make_materialization_status_entry(
            story_id="s1",
            status=status,
            reason="r",
        )
        assert entry["status"] == status


# ---------------------------------------------------------------------------
# D. make_materialization_status_entry — validation
# ---------------------------------------------------------------------------

def test_make_materialization_status_entry_rejects_empty_story_id():
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialization_status_entry(
            story_id="",
            status="materialized",
            reason="r",
        )
    assert "story_id" in str(exc_info.value)


def test_make_materialization_status_entry_rejects_unknown_status():
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialization_status_entry(
            story_id="s1",
            status="approved",  # not in allowlist
            reason="r",
        )
    assert "status" in str(exc_info.value)


def test_make_materialization_status_entry_rejects_empty_status():
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialization_status_entry(
            story_id="s1",
            status="",
            reason="r",
        )
    assert "status" in str(exc_info.value)


def test_make_materialization_status_entry_rejects_empty_reason():
    """`reason` is the whole point of the entry — must be present and
    non-empty so replay observers can see WHY."""
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.make_materialization_status_entry(
            story_id="s1",
            status="rejected",
            reason="",
        )
    assert "reason" in str(exc_info.value)


# ---------------------------------------------------------------------------
# E. Round-trip through _append_entry + read_entries
# ---------------------------------------------------------------------------

def test_round_trip_materialized_file_entry(isolated_log):
    """Build via factory, append, read back — equal."""
    import sm

    built = sm.make_materialized_file_entry(
        story_id="story-42",
        role="coder",
        target_path="sm.py",
        byte_count=1234,
        sha256=ANOTHER_VALID_SHA256,
    )
    sm._append_entry(built)
    entries = list(sm.read_entries())
    assert entries == [built]


def test_round_trip_materialization_status_entry(isolated_log):
    """Build via factory, append, read back — equal."""
    import sm

    built = sm.make_materialization_status_entry(
        story_id="story-42",
        status="rejected",
        reason="reviewer: missing tests for module X",
    )
    sm._append_entry(built)
    entries = list(sm.read_entries())
    assert entries == [built]


def test_round_trip_mixed_materialization_entries(isolated_log):
    """Multiple entries of both types interleaved replay correctly."""
    import sm

    m1 = sm.make_materialized_file_entry(
        story_id="s1",
        role="test_writer",
        target_path="tests/test_x.py",
        byte_count=10,
        sha256=VALID_SHA256,
    )
    s1 = sm.make_materialization_status_entry(
        story_id="s1",
        status="materialized",
        reason="ok",
    )
    m2 = sm.make_materialized_file_entry(
        story_id="s1",
        role="coder",
        target_path="sm.py",
        byte_count=99,
        sha256=ANOTHER_VALID_SHA256,
    )
    s2 = sm.make_materialization_status_entry(
        story_id="s1",
        status="rejected",
        reason="reviewer pushback",
    )

    for built in (m1, s1, m2, s2):
        sm._append_entry(built)

    entries = list(sm.read_entries())
    assert entries == [m1, s1, m2, s2]
    # Spot-check: types are preserved across replay.
    assert [e["type"] for e in entries] == [
        "materialized_file",
        "materialization_status",
        "materialized_file",
        "materialization_status",
    ]
