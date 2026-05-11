"""Iter 3 v2 Sprint 1 Story 7 — pin the .candidate-sidecar contract.

Story 6 implemented `write_agent_output` in GREENFIELD-only mode: any
collision with an existing target file raises `FileExistsError`. Story 7
REPLACES that policy with a `.candidate` + `.candidate.diff` sidecar
behavior so write_agent_output works on existing targets without ever
mutating the operator's file. Story 8 (next) wires the call site +
logs the `materialization_status(collision)` entry; Story 9 verifies
the whole assembly.

What Story 7 pins
-----------------

1. **Existing-target behavior**. When the resolved target file ALREADY
   EXISTS, `write_agent_output` no longer raises. Instead:
     a. The post-normalization content is written to
        ``<target>.candidate`` (atomic tempfile + os.replace, UTF-8, LF).
     b. The unified diff between the EXISTING target's content and the
        NEW candidate's content is written to
        ``<target>.candidate.diff`` (also atomic, UTF-8).
     c. The function returns ``(<absolute candidate path>, byte_count,
        sha256_hex)`` — i.e. the SAME 3-tuple shape Story 6 already uses,
        but the path now points to the sidecar candidate rather than the
        original target. byte_count and sha256_hex describe the
        ``.candidate`` file, NOT the original.
     d. The original target's bytes are unchanged.

2. **The 3-tuple shape stays the same.** TestWriter judgment: callers
   (Story 8) infer "collision happened" by inspecting whether the
   returned path ends in ``.candidate``. We deliberately do NOT add a
   4th boolean to the tuple — the suffix already encodes it, and a 4th
   element would silently break any Story-6 caller that unpacks three
   names. Keeping the shape lets Story 8 layer collision-status
   logging on top without retrofitting Story 6's contract.

3. **Diff format**. The diff is produced by Python's
   ``difflib.unified_diff`` over the original-target text and the new
   candidate text, both split into lines. Standard ``--- old / +++ new
   / @@ -... +... @@`` headers are preserved.
     - The ``--- old`` / ``+++ new`` labels use the project-root-RELATIVE
       paths (``sm.py`` and ``sm.py.candidate``, not absolutes), so the
       diff is portable across machines and copies cleanly into a PR
       description.
     - When the original and the new candidate are byte-identical, the
       diff file is created but is EMPTY (zero bytes). TestWriter pick:
       empty file is a clearer "no changes" signal than a synthetic
       "no-op diff" string, and is what `difflib.unified_diff` produces
       naturally for equal inputs (it yields no lines).

4. **`.candidate` already exists (collision-on-collision)**. If
   ``<target>.candidate`` is ITSELF present from a prior run, the new
   write OVERWRITES it (and its `.diff` sidecar). TestWriter rationale:
   the previous candidate is by definition stale (Reviewer hasn't acted
   on it yet, or it would have been merged or deleted), and the
   operator wants the FRESHEST candidate visible. Atomic-replace
   semantics protect against partial-write states.

5. **Atomicity preserved**. Both the ``.candidate`` and
   ``.candidate.diff`` writes use the same tempfile + ``os.replace``
   pattern as the Story 6 greenfield path. A mid-write failure on
   either sidecar:
     - Leaves the ORIGINAL target file UNTOUCHED (this is the load-
       bearing safety property — the operator's work is never at risk).
     - Leaves no `.tmp` / `.part` litter in the parent directory.

6. **Greenfield regression (Story 6 behavior still works)**. When the
   target does NOT exist, write_agent_output behaves EXACTLY as in
   Story 6:
     - Writes to the TARGET (not `<target>.candidate`).
     - Returns the target's absolute path (not the candidate path).
     - Creates no `.candidate` / `.candidate.diff` files anywhere.
   The whole .candidate codepath is collision-gated.

7. **No-rollback foundation**. Story 7 doesn't itself implement
   no-rollback — that's a Reviewer-side / pipeline-side contract. But
   Story 7's behavior PINS the foundation: `write_agent_output` never
   touches the original target on collision, and `.candidate` /
   `.candidate.diff` are materialized files that the caller is
   responsible for cleaning up (or accepting). They are NOT auto-
   reverted by write_agent_output itself.

TestWriter design decisions locked here
---------------------------------------

  - **Path-hint + collision interaction**. If the path hint resolves
    to an EXISTING file, collision behavior applies to the hinted
    path (i.e. `<hinted_target>.candidate` + `<hinted_target>.candidate.
    diff`). Hint routing happens first, collision detection second.

  - **Returned path ends with literal `.candidate`** (not
    `.candidate.py` or `.py.candidate` swapped — we APPEND the suffix
    after whatever extension the target has). So `sm.py` collides into
    `sm.py.candidate`; `tests/test_x.py` collides into
    `tests/test_x.py.candidate`. The diff lives at
    `<that>.diff` → `sm.py.candidate.diff`. This pattern keeps `git
    status` showing both sidecars adjacent to the original.

  - **Diff is unified-diff format only.** We do NOT use ndiff,
    context_diff, or a custom format. `difflib.unified_diff` is the
    industry-standard `--- / +++ / @@` shape every reviewer + every
    tool understands. fromfile / tofile labels become the relative
    paths described above; n=3 (default context lines) is fine.

  - **byte_count + sha256 describe the `.candidate` file.** The
    returned tuple's `byte_count` and `sha256_hex` MUST match the
    `.candidate` file on disk, not the original target. This is the
    point of returning a path-shifted tuple: callers logging a
    `materialized_file` entry log the CANDIDATE's provenance, since
    that's what was actually written.

  - **`.candidate` itself never collides recursively.** We do NOT
    walk to `<target>.candidate.candidate` if `.candidate` exists —
    we overwrite. Endless candidate chains would balloon the file
    system and obscure which version Reviewer is supposed to look at.

  - **Identical-content diff is an EMPTY file** (not absent). The
    `.candidate.diff` file is ALWAYS created when a collision fires,
    even when its content is empty. The presence of the file is the
    "collision happened" signal for downstream observers / Story 8.

Cascade tests flagged
---------------------

  - ``tests/test_write_agent_output.py`` Section E — the four
    Story-6 collision tests assert ``FileExistsError`` on existing
    targets. Story 7's Coder MUST cascade-update those tests to
    reflect the new policy:
      * ``test_collision_existing_file_raises`` — becomes
        ``test_collision_writes_candidate_sidecar`` (or equivalent).
      * ``test_collision_existing_file_left_unmodified`` — stays in
        spirit but now asserts via the NEW policy.
      * ``test_collision_existing_test_file_via_hint_raises`` — also
        flips polarity.
    The Coder must update or delete those four tests as part of the
    Story 7 Coder cycle. THIS test file is the new pin for the
    collision contract; the old file's E section is obsolete.

  - ``tests/test_persistence_audit.py`` counts write-mode opens /
    write_text calls. Story 7 may add 1-2 new write sites (the diff
    write). The Coder must update the count constants there, exactly
    as Story 6 did.

  - ``test_no_live_sdk_calls.py`` and other audit tests are
    unaffected — `.candidate` writes are pure local I/O.

These tests must FAIL on first run — Story 7's behavior is not yet
implemented in sm.py.
"""

from __future__ import annotations

import difflib
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


SHORT_ID = "abcd1234"


def _seed_existing(target: pathlib.Path, body: str) -> None:
    """Create an existing target file with `body`. Pre-creates parent
    dirs so test_writer-default tests don't trip on a missing
    `tests/` dir."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


# ===========================================================================
# A. Collision basic behavior — existing target triggers sidecar
# ===========================================================================

def test_collision_does_not_raise_file_exists_error(tmp_path):
    """Story 7 supersedes the Story-6 FileExistsError on collision.
    The same call that previously raised must now SUCCEED."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original = 1\n")

    # This is the Story-6 trigger that used to raise.
    sm.write_agent_output(
        role="coder",
        output="x = 2\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    # If we got here, the new policy held.


def test_collision_returns_candidate_path(tmp_path):
    """Returned path ends with `.candidate` (not the original path)."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output="updated\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert target_path.endswith(".candidate"), (
        f"expected path ending in .candidate, got {target_path!r}"
    )
    assert pathlib.Path(target_path).name == "sm.py.candidate"


def test_collision_candidate_file_created(tmp_path):
    """`.candidate` file exists on disk after a collision."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    sm.write_agent_output(
        role="coder",
        output="updated\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert (tmp_path / "sm.py.candidate").is_file()


def test_collision_diff_file_created(tmp_path):
    """`.candidate.diff` file exists on disk after a collision."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    sm.write_agent_output(
        role="coder",
        output="updated\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert (tmp_path / "sm.py.candidate.diff").is_file()


def test_collision_original_target_unchanged(tmp_path):
    """Original target's bytes are UNCHANGED after collision."""
    import sm

    target = tmp_path / "sm.py"
    original = "DO NOT TOUCH — original content\n"
    _seed_existing(target, original)
    sm.write_agent_output(
        role="coder",
        output="new candidate content\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert target.read_text(encoding="utf-8") == original


def test_collision_candidate_contains_new_content(tmp_path):
    """`.candidate` file holds the agent's post-normalization output."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    new_content = "this is the new content\nwith two lines\n"
    sm.write_agent_output(
        role="coder",
        output=new_content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    candidate = tmp_path / "sm.py.candidate"
    assert candidate.read_text(encoding="utf-8") == new_content


def test_collision_candidate_post_normalization(tmp_path):
    """`.candidate` content is post-newline-normalization (LF only),
    matching the Story 6 normalization rule applied to greenfield
    writes."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    crlf_input = "alpha\r\nbeta\r\n"
    sm.write_agent_output(
        role="coder",
        output=crlf_input,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    candidate_bytes = (tmp_path / "sm.py.candidate").read_bytes()
    assert b"\r\n" not in candidate_bytes
    assert candidate_bytes == b"alpha\nbeta\n"


# ===========================================================================
# B. Returned tuple shape
# ===========================================================================

def test_collision_returns_three_tuple(tmp_path):
    """Return value is still a 3-tuple — shape unchanged from Story 6.
    TestWriter pick: do NOT add a 4th 'collision' element; caller
    infers from path suffix."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    result = sm.write_agent_output(
        role="coder",
        output="updated\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert isinstance(result, tuple)
    assert len(result) == 3, (
        f"expected 3-tuple (path, byte_count, sha256), got {len(result)}"
    )


def test_collision_returned_byte_count_matches_candidate_size(tmp_path):
    """`byte_count` describes the `.candidate` file, NOT the original."""
    import sm

    target = tmp_path / "sm.py"
    # Original is intentionally a different size from the new content.
    _seed_existing(target, "x\n")  # 2 bytes
    new_content = "this is a much longer string with many bytes\n"
    _, byte_count, _ = sm.write_agent_output(
        role="coder",
        output=new_content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    candidate = tmp_path / "sm.py.candidate"
    assert byte_count == candidate.stat().st_size
    assert byte_count == len(new_content.encode("utf-8"))
    # And explicitly NOT the size of the original.
    assert byte_count != target.stat().st_size


def test_collision_returned_sha256_matches_candidate_hash(tmp_path):
    """`sha256_hex` is the hash of the `.candidate` file's bytes."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    new_content = "fresh content\n"
    _, _, sha256_hex = sm.write_agent_output(
        role="coder",
        output=new_content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    candidate_bytes = (tmp_path / "sm.py.candidate").read_bytes()
    assert sha256_hex == _sha256_hex(candidate_bytes)
    # Original's hash MUST differ — proves we returned the candidate's.
    original_bytes = target.read_bytes()
    assert sha256_hex != _sha256_hex(original_bytes)


def test_collision_returned_path_is_absolute(tmp_path):
    """Candidate path is returned in ABSOLUTE form, matching Story 6's
    pin for the greenfield case."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output="updated\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert pathlib.Path(target_path).is_absolute()


def test_collision_sha256_is_canonical_hex(tmp_path):
    """sha256 is 64 lowercase hex chars (matches Story 6 shape)."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    _, _, sha256_hex = sm.write_agent_output(
        role="coder",
        output="new\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert len(sha256_hex) == 64
    assert sha256_hex == sha256_hex.lower()
    assert all(c in "0123456789abcdef" for c in sha256_hex)


# ===========================================================================
# C. Diff content — unified-diff format, labels, content correctness
# ===========================================================================

def test_diff_has_unified_diff_headers(tmp_path):
    """`.candidate.diff` contains `---`, `+++`, and `@@ ... @@`
    headers — i.e. it's a unified diff, not raw text or ndiff."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "old line one\n")
    sm.write_agent_output(
        role="coder",
        output="new line one\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    diff_text = (tmp_path / "sm.py.candidate.diff").read_text(
        encoding="utf-8"
    )
    assert "---" in diff_text
    assert "+++" in diff_text
    # Unified diff @@ hunk header.
    assert "@@" in diff_text


def test_diff_shows_minus_and_plus_lines(tmp_path):
    """A one-line change produces one `-` line and one `+` line."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "the only line\n")
    sm.write_agent_output(
        role="coder",
        output="THE ONLY LINE\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    diff_text = (tmp_path / "sm.py.candidate.diff").read_text(
        encoding="utf-8"
    )
    # Strip header lines (`---`, `+++`) before counting + / - hunk
    # lines so we don't conflate them with the change markers.
    body_lines = [
        ln for ln in diff_text.splitlines()
        if not ln.startswith("---") and not ln.startswith("+++")
    ]
    minus_lines = [ln for ln in body_lines if ln.startswith("-")]
    plus_lines = [ln for ln in body_lines if ln.startswith("+")]
    assert len(minus_lines) == 1, (
        f"expected exactly 1 minus line, got {minus_lines!r}"
    )
    assert len(plus_lines) == 1, (
        f"expected exactly 1 plus line, got {plus_lines!r}"
    )
    assert "the only line" in minus_lines[0]
    assert "THE ONLY LINE" in plus_lines[0]


def test_diff_for_identical_content_is_empty(tmp_path):
    """When original == new (post-normalization), the diff file is
    EMPTY (zero bytes). difflib.unified_diff yields no lines for
    equal inputs."""
    import sm

    target = tmp_path / "sm.py"
    same = "same content\n"
    _seed_existing(target, same)
    sm.write_agent_output(
        role="coder",
        output=same,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    diff_file = tmp_path / "sm.py.candidate.diff"
    assert diff_file.is_file()
    assert diff_file.stat().st_size == 0


def test_diff_labels_use_relative_paths(tmp_path):
    """`--- old` / `+++ new` labels are project-root-RELATIVE so the
    diff is portable and PR-pasteable, not absolute paths bound to
    one machine."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "a\n")
    sm.write_agent_output(
        role="coder",
        output="b\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    diff_text = (tmp_path / "sm.py.candidate.diff").read_text(
        encoding="utf-8"
    )
    # No absolute paths — the tmp_path absolute prefix must NOT appear.
    abs_root = str(tmp_path)
    assert abs_root not in diff_text, (
        f"diff leaks absolute path {abs_root!r}: {diff_text!r}"
    )
    # The relative form should appear.
    assert "sm.py" in diff_text
    assert "sm.py.candidate" in diff_text


def test_diff_label_minus_points_to_original(tmp_path):
    """The `---` label refers to the ORIGINAL file (`sm.py`)."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "a\n")
    sm.write_agent_output(
        role="coder",
        output="b\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    diff_text = (tmp_path / "sm.py.candidate.diff").read_text(
        encoding="utf-8"
    )
    # Extract the `---` header line.
    minus_header = next(
        (ln for ln in diff_text.splitlines() if ln.startswith("---")),
        None,
    )
    assert minus_header is not None
    # The minus header points to the ORIGINAL, not the candidate.
    # i.e. "sm.py" appears AND ".candidate" does NOT (since the
    # original target has no .candidate suffix).
    assert "sm.py" in minus_header
    assert ".candidate" not in minus_header


def test_diff_label_plus_points_to_candidate(tmp_path):
    """The `+++` label refers to the `.candidate` file."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "a\n")
    sm.write_agent_output(
        role="coder",
        output="b\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    diff_text = (tmp_path / "sm.py.candidate.diff").read_text(
        encoding="utf-8"
    )
    plus_header = next(
        (ln for ln in diff_text.splitlines() if ln.startswith("+++")),
        None,
    )
    assert plus_header is not None
    assert "sm.py.candidate" in plus_header


def test_diff_content_matches_difflib_output(tmp_path):
    """The on-disk diff matches what difflib.unified_diff would
    produce for the same inputs (modulo trailing newline). Pins the
    EXACT format choice: standard unified_diff, n=3 (default
    context)."""
    import sm

    target = tmp_path / "sm.py"
    original_text = "alpha\nbeta\ngamma\n"
    _seed_existing(target, original_text)
    new_text = "alpha\nBETA\ngamma\n"
    sm.write_agent_output(
        role="coder",
        output=new_text,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    on_disk = (tmp_path / "sm.py.candidate.diff").read_text(
        encoding="utf-8"
    )
    # The diff must contain at least the two changed lines in the
    # canonical +/- form. We don't pin EXACT byte-for-byte equality
    # (difflib's label/timestamp internals can vary) but we DO pin
    # that the same difflib output is consistent shape.
    assert "-beta" in on_disk
    assert "+BETA" in on_disk
    # And canonically alpha/gamma are context, not change.
    assert ("-alpha" not in on_disk) and ("+alpha" not in on_disk)
    assert ("-gamma" not in on_disk) and ("+gamma" not in on_disk)


# ===========================================================================
# D. .candidate already exists (collision-on-collision)
# ===========================================================================

def test_candidate_collision_overwrites_existing_candidate(tmp_path):
    """If `.candidate` already exists from a prior run, the new write
    OVERWRITES it. TestWriter rationale: stale candidate is by
    definition stale; fresh one wins."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    stale_candidate = tmp_path / "sm.py.candidate"
    stale_candidate.write_text("STALE CANDIDATE\n", encoding="utf-8")

    sm.write_agent_output(
        role="coder",
        output="fresh candidate\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert stale_candidate.read_text(encoding="utf-8") == "fresh candidate\n"


def test_candidate_collision_overwrites_existing_diff(tmp_path):
    """`.candidate.diff` from a prior run is also overwritten with
    the new diff."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    stale_diff = tmp_path / "sm.py.candidate.diff"
    stale_diff.write_text("STALE DIFF\n", encoding="utf-8")

    sm.write_agent_output(
        role="coder",
        output="fresh\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    fresh_diff = stale_diff.read_text(encoding="utf-8")
    assert "STALE DIFF" not in fresh_diff
    # And the fresh diff contains the actual change markers.
    assert ("-original" in fresh_diff) or ("+fresh" in fresh_diff)


def test_candidate_collision_does_not_touch_original(tmp_path):
    """Even when BOTH .candidate and .candidate.diff already exist,
    the ORIGINAL target file is still untouched."""
    import sm

    target = tmp_path / "sm.py"
    original = "original target — must survive\n"
    _seed_existing(target, original)
    (tmp_path / "sm.py.candidate").write_text(
        "stale candidate\n", encoding="utf-8"
    )
    (tmp_path / "sm.py.candidate.diff").write_text(
        "stale diff\n", encoding="utf-8"
    )

    sm.write_agent_output(
        role="coder",
        output="brand new\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert target.read_text(encoding="utf-8") == original


def test_no_recursive_candidate_chain(tmp_path):
    """We do NOT walk to `<target>.candidate.candidate` when
    `.candidate` exists — the policy is OVERWRITE the existing
    .candidate, not chain to a deeper sidecar."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    (tmp_path / "sm.py.candidate").write_text(
        "stale\n", encoding="utf-8"
    )

    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output="new\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    # The returned path is `.candidate`, not `.candidate.candidate`.
    assert pathlib.Path(target_path).name == "sm.py.candidate"
    # And no `.candidate.candidate` file was created.
    assert not (tmp_path / "sm.py.candidate.candidate").exists()


# ===========================================================================
# E. Atomicity — no partial files, original safety on failure
# ===========================================================================

def test_no_tempfile_litter_after_successful_collision(tmp_path):
    """After a successful collision write, no `.tmp` / `.part` /
    `.sm_write_` litter remains in the parent dir. Only the original
    + .candidate + .candidate.diff are present."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    sm.write_agent_output(
        role="coder",
        output="updated\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    children = {p.name for p in tmp_path.iterdir()}
    # We expect exactly these three names; absence of any others
    # means no tempfile leaked.
    assert children == {"sm.py", "sm.py.candidate", "sm.py.candidate.diff"}, (
        f"unexpected files in parent dir: {children!r}"
    )


def test_rename_failure_leaves_original_untouched(tmp_path, monkeypatch):
    """If `os.replace` fails during the collision write, the ORIGINAL
    target's bytes must STILL be intact. This is the load-bearing
    safety property: the operator's file is never at risk."""
    import sm

    target = tmp_path / "sm.py"
    original = "PRECIOUS ORIGINAL — MUST NOT BE LOST\n"
    _seed_existing(target, original)

    real_replace = os.replace

    def boom(*args, **kwargs):
        raise OSError("simulated rename failure during candidate write")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError) as exc_info:
        sm.write_agent_output(
            role="coder",
            output="never-makes-it\n",
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )
    monkeypatch.setattr(os, "replace", real_replace)

    # Pin: the failure was the SIMULATED rename failure, not the
    # Story-6 FileExistsError (which would mean the implementation
    # never entered the new collision codepath at all). This guards
    # against passing-for-wrong-reason: FileExistsError is an OSError
    # subclass, so a naive `pytest.raises(OSError)` would catch it.
    assert not isinstance(exc_info.value, FileExistsError), (
        "Story 7 still raising FileExistsError on collision — new "
        "policy not implemented"
    )
    assert "simulated rename failure" in str(exc_info.value)
    # Original is byte-for-byte intact.
    assert target.read_text(encoding="utf-8") == original


def test_rename_failure_leaves_no_tempfile_litter(tmp_path, monkeypatch):
    """If `os.replace` fails, the tempfile must be cleaned up — no
    `.tmp` / `.part` / `.sm_write_` litter survives."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")

    real_replace = os.replace

    def boom(*args, **kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError) as exc_info:
        sm.write_agent_output(
            role="coder",
            output="content\n",
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )
    monkeypatch.setattr(os, "replace", real_replace)

    # Pin we hit the simulated rename failure, not Story 6's
    # FileExistsError (which would mean we never reached the new
    # codepath).
    assert not isinstance(exc_info.value, FileExistsError)
    assert "simulated rename failure" in str(exc_info.value)

    # No `.part` / `.sm_write_` / `.tmp` files in parent dir.
    leftovers = [
        p.name for p in tmp_path.iterdir()
        if (".part" in p.name or ".tmp" in p.name
            or p.name.startswith(".sm_write_"))
    ]
    assert leftovers == [], (
        f"tempfile leaked after rename failure: {leftovers!r}"
    )


def test_original_never_touched_under_any_failure(tmp_path, monkeypatch):
    """Regardless of WHERE the failure fires (replace), the original
    target's mtime / contents must be unchanged. Pin: the original
    file is never opened in write mode during the collision codepath."""
    import sm

    target = tmp_path / "sm.py"
    original = "untouchable\n"
    _seed_existing(target, original)
    pre_mtime = target.stat().st_mtime_ns
    pre_size = target.stat().st_size

    real_replace = os.replace

    def boom(*args, **kwargs):
        raise OSError("rename boom")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError) as exc_info:
        sm.write_agent_output(
            role="coder",
            output="new\n",
            story_short_id=SHORT_ID,
            project_root=str(tmp_path),
        )
    monkeypatch.setattr(os, "replace", real_replace)

    # Pin we hit the simulated rename failure, not Story 6's
    # FileExistsError (FileExistsError IS an OSError, so the naive
    # raises(OSError) would silently match the wrong codepath).
    assert not isinstance(exc_info.value, FileExistsError)
    assert "rename boom" in str(exc_info.value)

    # Bytes AND size AND mtime are all stable.
    assert target.read_text(encoding="utf-8") == original
    assert target.stat().st_size == pre_size
    assert target.stat().st_mtime_ns == pre_mtime


# ===========================================================================
# F. Path-hint + collision interaction
# ===========================================================================

def test_hinted_path_collision_writes_candidate_at_hinted_path(tmp_path):
    """Hint resolves to an existing file → `.candidate` sidecar
    appears alongside the HINTED target, not the role-default path."""
    import sm

    hinted = tmp_path / "tests" / "test_foo.py"
    _seed_existing(hinted, "# pre-existing test\n")
    sm.write_agent_output(
        role="test_writer",
        output="# path: tests/test_foo.py\n# new test body\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert (tmp_path / "tests" / "test_foo.py.candidate").is_file()
    assert (tmp_path / "tests" / "test_foo.py.candidate.diff").is_file()
    # The default test_writer path was NOT touched.
    assert not (tmp_path / "tests" / f"test_{SHORT_ID}.py").exists()


def test_hinted_path_collision_returns_hinted_candidate_path(tmp_path):
    """Returned path reflects the hinted target + `.candidate`."""
    import sm

    hinted = tmp_path / "tests" / "test_foo.py"
    _seed_existing(hinted, "# pre-existing\n")
    target_path, _, _ = sm.write_agent_output(
        role="test_writer",
        output="# path: tests/test_foo.py\nbody\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert pathlib.Path(target_path).name == "test_foo.py.candidate"


def test_hinted_path_collision_original_unchanged(tmp_path):
    """Hinted-path collision leaves the hinted ORIGINAL unchanged."""
    import sm

    hinted = tmp_path / "tests" / "test_foo.py"
    original = "# precious existing test\n"
    _seed_existing(hinted, original)
    sm.write_agent_output(
        role="test_writer",
        output="# path: tests/test_foo.py\nnew body\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert hinted.read_text(encoding="utf-8") == original


def test_test_writer_default_path_collision(tmp_path):
    """test_writer's role-default path (`tests/test_<id>.py`) also
    triggers collision behavior when the file pre-exists."""
    import sm

    default_target = tmp_path / "tests" / f"test_{SHORT_ID}.py"
    _seed_existing(default_target, "# pre-existing test\n")
    target_path, _, _ = sm.write_agent_output(
        role="test_writer",
        output="# new\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    expected_candidate = default_target.parent / (
        default_target.name + ".candidate"
    )
    assert expected_candidate.is_file()
    assert pathlib.Path(target_path) == expected_candidate.resolve() or (
        pathlib.Path(target_path).resolve() == expected_candidate.resolve()
    )


def test_coder_default_path_collision(tmp_path):
    """coder's role-default `sm.py` also triggers collision behavior
    when it pre-exists."""
    import sm

    default_target = tmp_path / "sm.py"
    _seed_existing(default_target, "x = 1\n")
    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output="x = 2\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert (tmp_path / "sm.py.candidate").is_file()
    assert pathlib.Path(target_path).name == "sm.py.candidate"


# ===========================================================================
# G. Greenfield regression — Story 6 behavior preserved when target absent
# ===========================================================================

def test_greenfield_still_writes_to_target_not_candidate(tmp_path):
    """When target is ABSENT, write goes to TARGET, not `.candidate`."""
    import sm

    target = tmp_path / "sm.py"
    assert not target.exists()
    target_path, _, _ = sm.write_agent_output(
        role="coder",
        output="x = 1\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    # Returned path is the TARGET, not a candidate.
    assert pathlib.Path(target_path).name == "sm.py"
    assert not pathlib.Path(target_path).name.endswith(".candidate")
    # And the target file itself exists.
    assert target.is_file()


def test_greenfield_creates_no_candidate_files(tmp_path):
    """Greenfield write must NOT create `.candidate` or
    `.candidate.diff` sidecars — those are collision-only artifacts."""
    import sm

    sm.write_agent_output(
        role="coder",
        output="x = 1\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    assert not (tmp_path / "sm.py.candidate").exists()
    assert not (tmp_path / "sm.py.candidate.diff").exists()


def test_greenfield_test_writer_creates_no_candidate(tmp_path):
    """test_writer greenfield: no `.candidate` sidecars either."""
    import sm

    sm.write_agent_output(
        role="test_writer",
        output="# body\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    target = tmp_path / "tests" / f"test_{SHORT_ID}.py"
    assert target.is_file()
    assert not target.with_suffix(target.suffix + ".candidate").exists()
    assert not (target.parent / (target.name + ".candidate")).exists()
    assert not (target.parent / (target.name + ".candidate.diff")).exists()


def test_greenfield_tuple_describes_target(tmp_path):
    """Greenfield byte_count + sha256 describe the TARGET file
    (regression of Story 6's pin — Story 7 must NOT shift the tuple
    to point at a phantom candidate in the greenfield case)."""
    import sm

    content = "deterministic\n"
    _, byte_count, sha256_hex = sm.write_agent_output(
        role="coder",
        output=content,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    target = tmp_path / "sm.py"
    assert byte_count == target.stat().st_size
    assert sha256_hex == _sha256_hex(target.read_bytes())


# ===========================================================================
# H. Misc — public API surface unchanged, both sidecars are UTF-8
# ===========================================================================

def test_signature_unchanged_no_new_required_params(tmp_path):
    """write_agent_output's signature does NOT gain a new required
    parameter in Story 7. The Story-6 call form still works on
    BOTH greenfield and collision paths. (Adding a required param
    would silently break every Story-6 caller.)"""
    import sm
    import inspect

    sig = inspect.signature(sm.write_agent_output)
    # The Story-6 params: role, output, story_short_id, project_root.
    expected_params = {"role", "output", "story_short_id", "project_root"}
    actual = set(sig.parameters.keys())
    # Allow Story 7 to ADD optional params, but NOT remove or add
    # required ones.
    missing = expected_params - actual
    assert not missing, (
        f"Story 6 params disappeared from signature: {missing!r}"
    )
    # Any NEW required params would break callers — flag them.
    new_required = [
        name
        for name, p in sig.parameters.items()
        if name not in expected_params
        and p.default is inspect.Parameter.empty
        and p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    assert not new_required, (
        f"Story 7 added new REQUIRED params (forbidden — would break "
        f"Story-6 callers): {new_required!r}"
    )


def test_candidate_file_is_utf8(tmp_path):
    """`.candidate` file is UTF-8 encoded (handles multi-byte content)."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "ascii original\n")
    multi_byte = "japanese: 日本語\nchinese: 中文\n"
    sm.write_agent_output(
        role="coder",
        output=multi_byte,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    candidate_text = (tmp_path / "sm.py.candidate").read_text(
        encoding="utf-8"
    )
    assert candidate_text == multi_byte


def test_diff_file_is_utf8(tmp_path):
    """`.candidate.diff` file is UTF-8 encoded — multi-byte chars in
    either the original or the new content survive round-trip."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "old: 古い\n")
    sm.write_agent_output(
        role="coder",
        output="new: 新しい\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    diff_text = (tmp_path / "sm.py.candidate.diff").read_text(
        encoding="utf-8"
    )
    # Both the original and the new multi-byte strings appear.
    assert "古い" in diff_text
    assert "新しい" in diff_text


def test_diff_file_exists_even_for_empty_diff(tmp_path):
    """The `.candidate.diff` file is ALWAYS created on collision —
    even when identical-content makes it empty. Its presence is the
    'collision happened' signal for downstream Story-8 logging."""
    import sm

    target = tmp_path / "sm.py"
    same = "identical line\n"
    _seed_existing(target, same)
    sm.write_agent_output(
        role="coder",
        output=same,
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    diff_file = tmp_path / "sm.py.candidate.diff"
    assert diff_file.exists(), (
        "diff file must be present even when content is identical"
    )


def test_collision_does_not_create_extra_files(tmp_path):
    """Collision creates EXACTLY two new files: `.candidate` and
    `.candidate.diff`. No third sidecar (e.g. `.candidate.meta`,
    `.candidate.json`) is permitted by the contract."""
    import sm

    target = tmp_path / "sm.py"
    _seed_existing(target, "original\n")
    pre_files = {p.name for p in tmp_path.iterdir()}

    sm.write_agent_output(
        role="coder",
        output="new\n",
        story_short_id=SHORT_ID,
        project_root=str(tmp_path),
    )
    post_files = {p.name for p in tmp_path.iterdir()}
    added = post_files - pre_files
    assert added == {"sm.py.candidate", "sm.py.candidate.diff"}, (
        f"collision created unexpected files: {added!r}"
    )
