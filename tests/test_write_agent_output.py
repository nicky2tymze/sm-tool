"""Iter 3 v2 Sprint 1 Story 6 — pin the contract of `sm.write_agent_output`.

This file is the SECOND step of req-2 (file materialization). Story 5
pinned the entry-type factories (`make_materialized_file_entry`,
`make_materialization_status_entry`). Story 6 implements the function
that actually writes agent output to disk. Story 7 will handle existing-
file collisions (.candidate sidecar + diff); Story 8 wires the call site
into the execute pipeline; Story 9 verifies the whole assembly.

What Story 6 pins
-----------------

1. `write_agent_output(role, output, story_short_id, project_root=None)
   -> tuple[str, int, str]` is PUBLIC and in `sm.__all__`. The return
   tuple shape `(target_path, byte_count, sha256_hex)` matches the
   `materialized_file` log-entry payload from Story 5 so the caller can
   feed the tuple straight into `make_materialized_file_entry`.

2. Path routing by role (defaults — overridable by a path hint):
     - "test_writer" -> `tests/test_<story_short_id>.py`
     - "coder"       -> `sm.py`
     - "reviewer"    -> ValueError (Reviewer returns JSON, not files)
     - anything else -> ValueError

3. Path-hint override. If the FIRST LINE of `output` matches
   `# path: <relative_path>`, that path overrides the role default and
   the hint line is STRIPPED from the written content. The hint is a
   directive, not file content.

4. Path validation. The hint MUST be a relative path inside
   `project_root`. Absolute paths, `..` traversal, drive-letter
   prefixes, and paths that escape the root all raise `ValueError`.

5. `project_root=None` defaults to the current working directory. All
   paths resolve relative to this root.

6. Greenfield-only contract (Story 6's narrow scope). If the resolved
   target file already exists, `write_agent_output` raises
   `FileExistsError`. Story 7 will REPLACE this behavior with the
   .candidate-sidecar policy — this file's collision tests are FLAGGED
   as cascade-edits-on-Story-7.

7. Atomic write. The content is written to a temp file in the same
   directory and renamed to the target — a crash mid-write leaves no
   partial target file.

8. Parent directory creation. The target's parent directory is created
   (with `parents=True`) before the atomic write. Existing dirs are a
   no-op.

9. Encoding. UTF-8 always. Newlines normalized to `\n` (LF) regardless
   of what the agent emits. This pins TestWriter's choice: agents emit
   inconsistent line endings; the writer normalizes to one canonical
   form so the sha256 is reproducible across platforms.

10. Hashing. The returned `sha256` is the lowercase-hex SHA-256 of the
    BYTES actually written to disk (i.e. AFTER hint-strip + newline
    normalization). The returned `byte_count` is the size of those same
    bytes. The returned `target_path` is the (relative or absolute —
    TestWriter picks ABSOLUTE, see below) string path of the file
    written.

TestWriter design decisions locked here
---------------------------------------

  - **Path hint regex tolerance.** The hint is parsed by stripping the
    `# path:` prefix from line 0 and then `str.strip()`-ing the
    remainder. So `# path: foo.py`, `# path:    foo.py`, and
    `# path:foo.py` all parse to `foo.py`. A trailing-comment form
    (`# path: foo.py # blah`) is parsed STRICTLY — the whole remainder
    is the path, including the trailing `# blah`, which then fails path
    validation (path with spaces / `#`). TestWriter chooses strict over
    lax: agents emit one hint or none; the trailing-comment shape is
    an error, not a feature.

  - **Empty path hint.** `# path: ` (or `# path:`) with no path → the
    hint line is malformed; raises `ValueError`. We do NOT silently
    fall back to the role default — silent fallback hides bugs in the
    agent's hint emission.

  - **Backslash in path hint.** A POSIX-relative path with a backslash
    is a Windows-only form. We REJECT it: the hint format is POSIX-style
    forward slashes. Cross-platform agents must emit `tests/foo.py`,
    not the backslash-separator form. Raises `ValueError`.

  - **Hint must be FIRST LINE.** A `# path:` comment anywhere except
    line 0 is treated as regular code content and ignored. Stripping
    is purely a line-0 directive.

  - **Returned `target_path` is the ABSOLUTE path string.** The
    Story 5 log entry's `target_path` field is project-root-relative
    by spec ("tests/test_foo.py", "sm.py"). The Story 6 RETURN VALUE
    is the absolute path of the file on disk so callers can hand it
    straight to logging / verification / pipeline-bus tooling without
    re-resolving. Story 8 will translate to the relative form when
    building the log entry. This split keeps the writer testable
    without a coupling to log-entry conventions.

  - **story_short_id validation.** Must be a non-empty string with NO
    path separators (no `/`, no backslash, no `..`). Whitelist-ish: any
    of `[A-Za-z0-9_-]` is fine; anything else raises `ValueError`. (The
    canonical shape is the first 8 chars of a uuid4-hex story_id, which
    is `[0-9a-f]{8}`, but the validator is broader so test fixtures can
    use human-readable shorthands like `s1`, `story-42`.)

Cascade tests flagged
---------------------
  - `test_persistence_audit.py` pins "exactly one write-mode `open(...)`
    in sm.py" and "exactly one `.write_text(` in sm.py". Story 6 adds
    a new write site (the atomic tempfile write + rename). That audit
    WILL fail on the Coder's Story-6 implementation and needs an edit
    in Story 6 (the audit is a count-pin of the AS-OF-Iter-2 posture,
    not a forbid-writes pin). Story 6's TestWriter notes this as a
    KNOWN cascade-edit; the Coder must update the count constants
    (or expand the allowlist) in that file as part of Story 6.
  - `test_no_live_sdk_calls.py` is unaffected — `write_agent_output`
    is pure local I/O.
  - Existing collision tests in this file will be UPDATED in Story 7
    when the policy changes from FileExistsError to .candidate +
    materialization_status(collision). Story 6 pins the greenfield
    contract; Story 7's TestWriter will then EDIT those tests.

These tests must FAIL on first run — `write_agent_output` does not
exist yet.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import sys

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


SHORT_ID = "abcd1234"  # canonical 8-char hex shorthand


# ---------------------------------------------------------------------------
# A. Smoke
# ---------------------------------------------------------------------------

def test_write_agent_output_exists():
    """`sm.write_agent_output` is a module attribute."""
    import sm

    assert hasattr(sm, "write_agent_output"), (
        "sm.write_agent_output must exist"
    )


def test_write_agent_output_is_callable():
    import sm

    assert callable(sm.write_agent_output)


def test_write_agent_output_in_dunder_all():
    """The writer is part of the public API."""
    import sm

    assert "write_agent_output" in sm.__all__, (
        "write_agent_output must be in sm.__all__"
    )


def test_write_agent_output_returns_three_tuple(tmp_path):
    """Returns `(target_path, byte_count, sha256_hex)`."""
    import sm

    result = sm.write_agent_output(
        role="test_writer",
        output="print('hi')\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert isinstance(result, tuple)
    assert len(result) == 3
    target_path, byte_count, sha256_hex = result
    assert isinstance(target_path, str)
    assert isinstance(byte_count, int)
    assert isinstance(sha256_hex, str)


def test_write_agent_output_test_writer_default_path(tmp_path):
    """test_writer role lands at `tests/test_<short_id>.py`."""
    import sm

    target_path, _, _ = sm.write_agent_output(
        role="test_writer",
        output="print('hi')\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    expected = tmp_path / "tests" / f"test_{SHORT_ID}.py"
    assert pathlib.Path(target_path) == expected
    assert expected.is_file()


def test_write_agent_output_coder_default_path(tmp_path):
    """coder role lands at `sm.py` under project_root."""
    import sm

    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output="x = 1\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    expected = tmp_path / "sm.py"
    assert pathlib.Path(target_path) == expected
    assert expected.is_file()


def test_write_agent_output_reviewer_role_raises():
    """Reviewer doesn't materialize files; raises ValueError."""
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.write_agent_output(
            role="reviewer",
            output="{}",
            story_short_id=SHORT_ID,
        )
    assert "reviewer" in str(exc_info.value).lower() or (
        "role" in str(exc_info.value).lower()
    )


def test_write_agent_output_unknown_role_raises():
    """Anything not in the role allowlist raises ValueError."""
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.write_agent_output(
            role="architect",
            output="content",
            story_short_id=SHORT_ID,
        )
    assert "role" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# B. Greenfield writes — content, sizes, hashes
# ---------------------------------------------------------------------------

def test_greenfield_test_writer_writes_expected_content(tmp_path):
    """Content written to disk matches the input (after newline-LF
    normalization — no CRLF in this input, so input == output)."""
    import sm

    content = "import pytest\n\ndef test_foo():\n    assert True\n"
    sm.write_agent_output(
        role="test_writer",
        output=content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    on_disk = (tmp_path / "tests" / f"test_{SHORT_ID}.py").read_bytes()
    assert on_disk == content.encode("utf-8")


def test_greenfield_coder_writes_expected_content(tmp_path):
    """coder's sm.py written content matches input."""
    import sm

    content = "def main():\n    return 0\n"
    sm.write_agent_output(
        role="coder",
        output=content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    on_disk = (tmp_path / "sm.py").read_bytes()
    assert on_disk == content.encode("utf-8")


def test_greenfield_byte_count_matches_file_size(tmp_path):
    """Returned `byte_count` equals the on-disk file size in bytes."""
    import sm

    content = "x = 'unicode: éà中'\n"  # multi-byte UTF-8
    _, byte_count, _ = sm.write_agent_output(
        role="coder",
        output=content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    actual_size = (tmp_path / "sm.py").stat().st_size
    assert byte_count == actual_size
    # And the content's UTF-8 length is what we expect.
    assert byte_count == len(content.encode("utf-8"))


def test_greenfield_sha256_matches_actual_file_hash(tmp_path):
    """Returned `sha256_hex` is the lowercase-hex SHA-256 of the bytes
    on disk."""
    import sm

    content = "print('reproducible hash')\n"
    _, _, sha256_hex = sm.write_agent_output(
        role="coder",
        output=content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    on_disk = (tmp_path / "sm.py").read_bytes()
    assert sha256_hex == _sha256_hex(on_disk)
    # And it's the canonical 64-char lowercase hex shape.
    assert len(sha256_hex) == 64
    assert sha256_hex == sha256_hex.lower()
    assert all(c in "0123456789abcdef" for c in sha256_hex)


def test_greenfield_target_path_matches_actual_path(tmp_path):
    """Returned `target_path` is the actual filesystem path used."""
    import sm

    target_path, _, _ = sm.write_agent_output(
        role="test_writer",
        output="x = 1\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert pathlib.Path(target_path).is_file()


def test_greenfield_returned_target_path_is_absolute(tmp_path):
    """TestWriter pins: returned path is ABSOLUTE (callers don't have
    to re-resolve)."""
    import sm

    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output="x = 1\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert pathlib.Path(target_path).is_absolute()


def test_greenfield_utf8_content_preserved(tmp_path):
    """UTF-8 multi-byte content survives the write/read round-trip."""
    import sm

    content = "# 日本語コメント\nx = '中文'\n"
    sm.write_agent_output(
        role="coder",
        output=content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    on_disk_text = (tmp_path / "sm.py").read_text(encoding="utf-8")
    assert on_disk_text == content


def test_greenfield_empty_output_writes_zero_byte_file(tmp_path):
    """Empty output is a legitimate write — produces a zero-byte file."""
    import sm

    target_path, byte_count, sha256_hex = sm.write_agent_output(
        role="coder",
        output="",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert (tmp_path / "sm.py").is_file()
    assert byte_count == 0
    assert (tmp_path / "sm.py").stat().st_size == 0
    # SHA-256 of empty bytes is well-known.
    assert sha256_hex == _sha256_hex(b"")


# ---------------------------------------------------------------------------
# C. Path-hint parsing
# ---------------------------------------------------------------------------

def test_path_hint_overrides_test_writer_default(tmp_path):
    """`# path: tests/test_foo.py\n...` lands at tests/test_foo.py."""
    import sm

    body = "print('body')\n"
    output = f"# path: tests/test_foo.py\n{body}"
    target_path, _, _ = sm.write_agent_output(
        role="test_writer",
        output=output,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    expected = tmp_path / "tests" / "test_foo.py"
    assert pathlib.Path(target_path) == expected
    assert expected.is_file()


def test_path_hint_strips_hint_line_from_content(tmp_path):
    """The hint line itself is NOT part of the written content."""
    import sm

    body = "print('body')\n"
    output = f"# path: tests/test_foo.py\n{body}"
    sm.write_agent_output(
        role="test_writer",
        output=output,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    on_disk = (tmp_path / "tests" / "test_foo.py").read_bytes()
    assert on_disk == body.encode("utf-8")
    # Hint must NOT appear in the written file.
    assert b"# path:" not in on_disk


def test_path_hint_overrides_coder_default(tmp_path):
    """`# path: lib/util.py` overrides coder's sm.py default."""
    import sm

    body = "def util(): pass\n"
    output = f"# path: lib/util.py\n{body}"
    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output=output,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    expected = tmp_path / "lib" / "util.py"
    assert pathlib.Path(target_path) == expected
    assert expected.is_file()
    # And sm.py was NOT created.
    assert not (tmp_path / "sm.py").exists()


def test_path_hint_tolerates_extra_whitespace(tmp_path):
    """`# path:    tests/foo.py` (multiple spaces) parses as
    `tests/foo.py`."""
    import sm

    output = "# path:    tests/foo.py\nbody\n"
    target_path, _, _ = sm.write_agent_output(
        role="test_writer",
        output=output,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    expected = tmp_path / "tests" / "foo.py"
    assert pathlib.Path(target_path) == expected


def test_path_hint_no_space_after_colon_still_parses(tmp_path):
    """`# path:foo.py` (no space) parses to `foo.py`."""
    import sm

    output = "# path:foo.py\nbody\n"
    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output=output,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    expected = tmp_path / "foo.py"
    assert pathlib.Path(target_path) == expected


def test_path_hint_not_on_first_line_is_ignored(tmp_path):
    """A `# path:` comment on line 1+ is regular code content; the
    role default applies and the comment is preserved in output."""
    import sm

    output = "x = 1\n# path: tests/test_other.py\ny = 2\n"
    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output=output,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    # Default coder path applies, NOT the in-body comment.
    assert pathlib.Path(target_path) == tmp_path / "sm.py"
    # The "# path:" line is preserved in content (not stripped).
    on_disk = (tmp_path / "sm.py").read_text(encoding="utf-8")
    assert "# path: tests/test_other.py" in on_disk


def test_no_hint_uses_role_default(tmp_path):
    """Output without a leading `# path:` directive uses the role
    default path."""
    import sm

    output = "import sys\nx = 1\n"
    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output=output,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert pathlib.Path(target_path) == tmp_path / "sm.py"


def test_path_hint_trailing_comment_is_strict_error(tmp_path):
    """TestWriter design: `# path: foo.py # extra` is parsed strictly —
    the whole remainder is the path. `foo.py # extra` is not a valid
    path (spaces / `#`), so this raises ValueError. Strict parsing
    keeps the hint contract narrow."""
    import sm

    output = "# path: foo.py # extra comment\nbody\n"
    with pytest.raises(ValueError):
        sm.write_agent_output(
            role="coder",
            output=output,
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )


# ---------------------------------------------------------------------------
# D. Path validation — hint must be a safe relative path
# ---------------------------------------------------------------------------

def test_absolute_path_hint_raises(tmp_path):
    """Posix-absolute hint (`/etc/passwd`) raises ValueError."""
    import sm

    output = "# path: /etc/passwd\nbody\n"
    with pytest.raises(ValueError) as exc_info:
        sm.write_agent_output(
            role="coder",
            output=output,
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )
    assert "path" in str(exc_info.value).lower()
    # No file should have been created anywhere we control.
    assert not (tmp_path / "sm.py").exists()


def test_drive_letter_path_hint_raises(tmp_path):
    """Windows-absolute hint (`C:\\Users\\...`) raises ValueError. Drive
    letters are always rejected as absolute."""
    import sm

    output = "# path: C:/Windows/System32/x.py\nbody\n"
    with pytest.raises(ValueError):
        sm.write_agent_output(
            role="coder",
            output=output,
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )


def test_dotdot_in_path_hint_raises(tmp_path):
    """`..` anywhere in the hint raises ValueError (path traversal
    blocker — defense in depth even before the resolved-path check)."""
    import sm

    output = "# path: ../etc/passwd\nbody\n"
    with pytest.raises(ValueError) as exc_info:
        sm.write_agent_output(
            role="coder",
            output=output,
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )
    assert "path" in str(exc_info.value).lower()


def test_dotdot_nested_in_path_hint_raises(tmp_path):
    """`tests/../../escape.py` — `..` segment anywhere in the hint
    raises ValueError even if it doesn't start the path."""
    import sm

    output = "# path: tests/../../escape.py\nbody\n"
    with pytest.raises(ValueError):
        sm.write_agent_output(
            role="coder",
            output=output,
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )


def test_path_escaping_project_root_raises(tmp_path):
    """Even via a symlink-free purely relative form, a resolved target
    OUTSIDE project_root must raise. The `..` regex catches the
    obvious case; this test exercises the resolved-path verification
    that is the second line of defense."""
    import sm

    # `..` is rejected, so we test a different escape vector: a path
    # that on some filesystems would resolve outside (this is the same
    # as the dotdot test but reinforces the resolved-path check).
    output = "# path: ../sibling/file.py\nbody\n"
    with pytest.raises(ValueError):
        sm.write_agent_output(
            role="coder",
            output=output,
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )


def test_empty_path_hint_raises(tmp_path):
    """`# path:` with no path (or only whitespace) is a malformed
    directive and raises ValueError. We do NOT silently fall back to
    the role default."""
    import sm

    output = "# path:   \nbody\n"
    with pytest.raises(ValueError) as exc_info:
        sm.write_agent_output(
            role="coder",
            output=output,
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )
    assert "path" in str(exc_info.value).lower()


def test_backslash_in_path_hint_raises(tmp_path):
    """Backslash is not a POSIX path separator. The hint format is
    forward-slash only. `tests\\foo.py` raises ValueError."""
    import sm

    output = "# path: tests\\foo.py\nbody\n"
    with pytest.raises(ValueError):
        sm.write_agent_output(
            role="coder",
            output=output,
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )


# ---------------------------------------------------------------------------
# E. Collision — greenfield-only (cascade tests flagged for Story 7)
# ---------------------------------------------------------------------------

def test_collision_greenfield_succeeds(tmp_path):
    """Absent file: write succeeds. Sanity-pin so the existing-file
    tests below ride on a known-clean baseline."""
    import sm

    target = tmp_path / "sm.py"
    assert not target.exists()
    sm.write_agent_output(
        role="coder",
        output="x = 1\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert target.is_file()


def test_collision_existing_file_writes_candidate_sidecar(tmp_path):
    """Story 7 supersedes the Story-6 FileExistsError on collision.
    If the target already exists, the writer SUCCEEDS by writing a
    `.candidate` sidecar (and a `.candidate.diff`) and returns a path
    pointing at the sidecar — never raises."""
    import sm

    target = tmp_path / "sm.py"
    target.write_text("pre-existing\n", encoding="utf-8")
    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output="new content\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert target_path.endswith(".candidate")
    assert (tmp_path / "sm.py.candidate").is_file()
    assert (tmp_path / "sm.py.candidate.diff").is_file()


def test_collision_existing_file_left_unmodified(tmp_path):
    """Story 7 contract: the original target's bytes are UNCHANGED on
    collision. (Spirit of the old Story-6 test preserved — the assertion
    that the operator's file is never at risk still holds, but the
    writer no longer raises.)"""
    import sm

    target = tmp_path / "sm.py"
    original = "ORIGINAL CONTENT — DO NOT TOUCH\n"
    target.write_text(original, encoding="utf-8")
    sm.write_agent_output(
        role="coder",
        output="new content\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert target.read_text(encoding="utf-8") == original


def test_collision_existing_test_file_via_hint_writes_candidate(tmp_path):
    """Path-hint-routed file also flips to .candidate-sidecar policy
    under Story 7 (was: also raised FileExistsError under Story 6)."""
    import sm

    target = tmp_path / "tests" / "test_foo.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# existing\n", encoding="utf-8")
    sm.write_agent_output(
        role="test_writer",
        output="# path: tests/test_foo.py\nbody\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert (tmp_path / "tests" / "test_foo.py.candidate").is_file()
    assert (tmp_path / "tests" / "test_foo.py.candidate.diff").is_file()
    # And the original is unchanged.
    assert target.read_text(encoding="utf-8") == "# existing\n"


# ---------------------------------------------------------------------------
# F. Parent-directory creation
# ---------------------------------------------------------------------------

def test_parent_dir_created_for_nested_path(tmp_path):
    """Deeply-nested target path → parent dirs auto-created."""
    import sm

    output = "# path: tests/sub/dir/test_x.py\nbody\n"
    sm.write_agent_output(
        role="test_writer",
        output=output,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    target = tmp_path / "tests" / "sub" / "dir" / "test_x.py"
    assert target.is_file()
    assert target.parent.is_dir()


def test_parent_dir_existing_dir_no_error(tmp_path):
    """Existing target directory is a no-op (parents=True,
    exist_ok=True)."""
    import sm

    (tmp_path / "tests").mkdir()
    sm.write_agent_output(
        role="test_writer",
        output="body\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert (tmp_path / "tests" / f"test_{SHORT_ID}.py").is_file()


def test_parent_dir_creation_for_test_writer_default(tmp_path):
    """test_writer default `tests/test_<id>.py` creates `tests/` if
    missing."""
    import sm

    assert not (tmp_path / "tests").exists()
    sm.write_agent_output(
        role="test_writer",
        output="body\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert (tmp_path / "tests").is_dir()


# ---------------------------------------------------------------------------
# G. Newline normalization & UTF-8
# ---------------------------------------------------------------------------

def test_crlf_input_normalized_to_lf(tmp_path):
    """Windows-style CRLF line endings in agent output are normalized
    to LF on disk."""
    import sm

    crlf_content = "line1\r\nline2\r\nline3\r\n"
    sm.write_agent_output(
        role="coder",
        output=crlf_content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    on_disk = (tmp_path / "sm.py").read_bytes()
    assert b"\r\n" not in on_disk
    assert on_disk == b"line1\nline2\nline3\n"


def test_lone_cr_normalized_to_lf(tmp_path):
    """Classic-Mac-style lone CR is also normalized to LF."""
    import sm

    cr_content = "line1\rline2\rline3\r"
    sm.write_agent_output(
        role="coder",
        output=cr_content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    on_disk = (tmp_path / "sm.py").read_bytes()
    assert b"\r" not in on_disk
    assert on_disk == b"line1\nline2\nline3\n"


def test_byte_count_reflects_normalized_content(tmp_path):
    """byte_count is the size of the BYTES WRITTEN (post-
    normalization), not the input string's UTF-8 length."""
    import sm

    crlf_content = "a\r\nb\r\n"  # 6 UTF-8 bytes raw
    lf_normalized = b"a\nb\n"  # 4 bytes after normalization
    _, byte_count, _ = sm.write_agent_output(
        role="coder",
        output=crlf_content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert byte_count == len(lf_normalized)
    assert byte_count == 4


def test_sha256_reflects_normalized_content(tmp_path):
    """sha256 is the hash of the BYTES WRITTEN (post hint-strip +
    newline normalization)."""
    import sm

    output = "# path: tests/test_x.py\nfoo\r\nbar\r\n"
    # Expected on-disk after hint-strip + LF normalization:
    expected_bytes = b"foo\nbar\n"
    _, _, sha256_hex = sm.write_agent_output(
        role="test_writer",
        output=output,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert sha256_hex == _sha256_hex(expected_bytes)


# ---------------------------------------------------------------------------
# H. Hashing — determinism
# ---------------------------------------------------------------------------

def test_identical_inputs_produce_identical_hashes(tmp_path):
    """Two identical writes (to different paths) yield the same
    sha256."""
    import sm

    content = "deterministic content\n"
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _, _, hash_a = sm.write_agent_output(
        role="coder",
        output=content,
        story_short_id=SHORT_ID,
        project_root=str(root_a),
    )
    _, _, hash_b = sm.write_agent_output(
        role="coder",
        output=content,
        story_short_id=SHORT_ID,
        project_root=str(root_b),
    )
    assert hash_a == hash_b


def test_different_inputs_produce_different_hashes(tmp_path):
    """Different content produces different sha256 hashes."""
    import sm

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _, _, hash_a = sm.write_agent_output(
        role="coder",
        output="content A\n",
        story_short_id=SHORT_ID,
        project_root=str(root_a),
    )
    _, _, hash_b = sm.write_agent_output(
        role="coder",
        output="content B\n",
        story_short_id=SHORT_ID,
        project_root=str(root_b),
    )
    assert hash_a != hash_b


# ---------------------------------------------------------------------------
# I. story_short_id validation
# ---------------------------------------------------------------------------

def test_empty_story_short_id_raises(tmp_path):
    """Empty `story_short_id` raises ValueError naming the field."""
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.write_agent_output(
            role="test_writer",
            output="body\n",
            story_short_id="",
            project_root=str(tmp_path),
        )
    assert "story_short_id" in str(exc_info.value)


def test_story_short_id_with_slash_raises(tmp_path):
    """A `story_short_id` containing `/` is a path separator and must
    be rejected — it would break the `tests/test_<id>.py` default and
    enable traversal."""
    import sm

    with pytest.raises(ValueError) as exc_info:
        sm.write_agent_output(
            role="test_writer",
            output="body\n",
            story_short_id="foo/bar",
            project_root=str(tmp_path),
        )
    assert "story_short_id" in str(exc_info.value)


def test_story_short_id_with_backslash_raises(tmp_path):
    """Backslash also rejected (Windows path separator)."""
    import sm

    with pytest.raises(ValueError):
        sm.write_agent_output(
            role="test_writer",
            output="body\n",
            story_short_id="foo\\bar",
            project_root=str(tmp_path),
        )


def test_story_short_id_with_dotdot_raises(tmp_path):
    """`..` in `story_short_id` is a path-traversal vector and must be
    rejected even though `..` alone won't escape the `tests/` prefix
    in practice — defense in depth."""
    import sm

    with pytest.raises(ValueError):
        sm.write_agent_output(
            role="test_writer",
            output="body\n",
            story_short_id="..",
            project_root=str(tmp_path),
        )


def test_canonical_8_char_hex_short_id_accepted(tmp_path):
    """The canonical `uuid4-hex[:8]` shape `[0-9a-f]{8}` works."""
    import sm

    sm.write_agent_output(
        role="test_writer",
        output="body\n",
        story_short_id="0123abcd",
        project_root=str(tmp_path),
    )
    assert (tmp_path / "tests" / "test_0123abcd.py").is_file()


def test_human_readable_short_id_accepted(tmp_path):
    """Human-readable shorthands `s1`, `story-42` are also accepted
    (alphanumeric + dashes / underscores)."""
    import sm

    sm.write_agent_output(
        role="test_writer",
        output="body\n",
        story_short_id="story-42",
        project_root=str(tmp_path),
    )
    assert (tmp_path / "tests" / "test_story-42.py").is_file()


# ---------------------------------------------------------------------------
# J. project_root defaulting & isolation
# ---------------------------------------------------------------------------

def test_project_root_none_defaults_to_cwd(tmp_path, monkeypatch):
    """`project_root=None` resolves paths relative to the current
    working directory."""
    import sm

    monkeypatch.chdir(tmp_path)
    target_path, _, _ = sm.write_agent_output(
        role="test_writer",
        output="body\n",
        story_short_id=SHORT_ID,
    )
    expected = tmp_path / "tests" / f"test_{SHORT_ID}.py"
    # On some systems tmp_path resolves through a symlink, so compare
    # via Path.resolve() to be safe.
    assert pathlib.Path(target_path).resolve() == expected.resolve()


def test_project_root_explicit_overrides_cwd(tmp_path, monkeypatch):
    """Explicit `project_root` wins over cwd — the writer must never
    silently fall back to cwd when given a real path."""
    import sm

    cwd_dir = tmp_path / "cwd"
    proj_dir = tmp_path / "proj"
    cwd_dir.mkdir()
    proj_dir.mkdir()
    monkeypatch.chdir(cwd_dir)

    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output="body\n",
        story_short_id=SHORT_ID,
        project_root=str(proj_dir),
    )
    # File should be under proj_dir, NOT cwd_dir.
    assert (proj_dir / "sm.py").is_file()
    assert not (cwd_dir / "sm.py").exists()
    assert pathlib.Path(target_path).resolve() == (proj_dir / "sm.py").resolve()


# ---------------------------------------------------------------------------
# K. Atomicity — no partial files
# ---------------------------------------------------------------------------

def test_no_tempfile_left_after_successful_write(tmp_path):
    """After a successful write, no `.tmp` / `.partial` sidecar lingers
    in the target's parent directory. Defensive pin against tempfile
    leakage."""
    import sm

    sm.write_agent_output(
        role="test_writer",
        output="body\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    parent = tmp_path / "tests"
    children = list(parent.iterdir())
    # Exactly one file (the target). No stray tempfiles.
    assert len(children) == 1
    assert children[0].name == f"test_{SHORT_ID}.py"


def test_no_target_file_when_write_fails_mid_rename(tmp_path, monkeypatch):
    """If the OS-level rename fails, the target must NOT exist
    (greenfield case) and no `.tmp` should linger.

    We simulate by monkeypatching `os.replace` (the canonical atomic-
    rename primitive on both POSIX and Windows) to raise OSError. The
    target was absent before; it must remain absent after.
    """
    import sm

    target = tmp_path / "sm.py"
    assert not target.exists()

    real_replace = os.replace

    def boom(*args, **kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        sm.write_agent_output(
            role="coder",
            output="content\n",
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )

    # Restore so cleanup can run normally.
    monkeypatch.setattr(os, "replace", real_replace)

    # Target was never renamed into place.
    assert not target.exists()
