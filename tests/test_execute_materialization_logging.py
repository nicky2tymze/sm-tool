"""Iter 3 v2 Sprint 1 Story 8 — pin file-materialization logging in execute().

Story 8 wires the Story 6/7 `write_agent_output` dispatcher into the
existing `execute()` pipeline (TestWriter -> Coder -> Reviewer) so each
agent stage that produces a file also emits a `materialized_file` log
entry (and a `materialization_status` entry when a collision or write
failure fires). Story 5 minted the two factory helpers
(`make_materialized_file_entry`, `make_materialization_status_entry`)
that produce the canonical entry shapes; Story 6 implemented the
greenfield write dispatcher; Story 7 layered `.candidate` /
`.candidate.diff` collision handling onto it. Story 8 wires the call
site.

What Story 8 pins
-----------------

1. After `spawn_test_writer` returns successfully and the
   `testwriter_output` entry has been appended, `execute()` calls
   `write_agent_output(role="test_writer", output=<tw_output>,
   story_short_id=<story_id[:8]>)` and then appends a
   `materialized_file` entry built via `make_materialized_file_entry`.
2. The same pattern fires for `spawn_coder` (role="coder").
3. The `target_path` field of the `materialized_file` entry is
   PROJECT-ROOT-RELATIVE (`tests/test_<short_id>.py`, `sm.py`), NOT
   the absolute path that `write_agent_output` returns. `execute()`
   converts the absolute return value to relative before logging.
4. The `materialized_file` entry's other four fields come straight
   from the call site: `story_id` (the executed story), `role`
   ("test_writer" or "coder"), `byte_count` and `sha256` (the write
   triple's 2nd and 3rd values).
5. On a write-side collision (target file already exists), the
   returned absolute path ends in `.candidate`. `execute()` detects
   this and ALSO appends a `materialization_status` entry with
   status="collision" and a `reason` string naming the colliding path.
6. The `materialized_file` entry for a collision case still fires
   (using the `.candidate` path's relative form + that file's byte
   count + sha256) — collisions don't suppress the provenance entry,
   they augment it.
7. The Reviewer stage does NOT produce a `materialized_file` entry
   (Reviewer returns a JSON verdict, not a code file).
8. On a `write_agent_output` failure (raises ValueError, e.g. for a
   `# path:` hint that escapes project_root), `execute()` appends a
   `materialization_status` entry with status="rejected" and a
   `reason` capturing the failure cause, then propagates the error.
   The story transitions to rejected (no Coder spawn).
9. On Reviewer REJECTION (after both writes succeeded), the prior
   `materialized_file` entries STAY in the log. Per req-2 spec
   "materialized files are NOT rolled back" — the entries remain a
   truthful audit trail and the files remain on disk.

What Story 8 does NOT do
------------------------

- Does NOT change the Reviewer entry shape, the lifecycle transitions,
  or the validation cascade (those stay exactly as Iter 1 Story 23 +
  Iter 2 Stories 7/8/9 left them).
- Does NOT roll back materialized files on rejection. Reviewer
  rejection is logged via existing `reviewer_approval` +
  `story_state_change(rejected)` entries; no additional
  `materialization_status` entry is appended for rejection-by-Reviewer
  (rejection-by-write-failure DOES log one — see point 8 above).
- Does NOT touch sm.py's Iter-2 default real-spawn callables. The
  injected-stub call shape Iter 1 pinned is preserved end-to-end.

TestWriter design decisions locked here
---------------------------------------

  - **Project-root anchor for write_agent_output.** `execute()` MUST
    pass `project_root=<LOG_PATH.parent>` (or the equivalent active
    anchor) when invoking `write_agent_output`. Tests pin this by
    monkeypatching `sm.LOG_PATH` to a per-test tmp directory and
    asserting the file ends up there. If `execute()` defaults to
    `Path.cwd()` instead of the LOG_PATH anchor, the materialized
    files would land in the package source tree during testing — a
    cascade hazard. So the test asserts the file lives under the
    LOG_PATH anchor and the entry's relative `target_path` resolves
    relative to that anchor.

  - **Short id derivation.** `execute()` derives `story_short_id`
    from `story_id[:8]`. This matches the test_writer default path
    `tests/test_<short_id>.py` so the round-trip is verifiable
    without inspecting an undocumented prefix length.

  - **`target_path` shape on collision.** Story 7 returns the
    absolute path of the `.candidate` sidecar. `execute()` converts
    it to a project-root-relative string for the `materialized_file`
    entry. So the entry's `target_path` for a TestWriter collision
    looks like `tests/test_<short_id>.py.candidate`. The test pins
    this exact suffix, NOT just "ends in .candidate" in absolute form.

  - **Order in the log.** Per stage, the order is
    `<stage>_output` -> `materialized_file` -> (optional
    `materialization_status`). The materialization rows fire AFTER
    the agent-output entry so a partial pipeline (TestWriter wrote
    file, Coder crashed BEFORE writing) still records the TestWriter's
    `materialized_file` even if no Coder entry ever appears.

  - **Reason string content.** For collision: the reason names the
    colliding TARGET path (the original, not the candidate) so an
    operator reading the log can locate the file in conflict. For
    rejection-by-write-failure: the reason carries the underlying
    ValueError message (so e.g. a `..` escape attempt is visible).
    Tests assert a substring match, not byte-for-byte equality, so
    the Coder can phrase the message naturally.

Cascade tests flagged
---------------------

  - `tests/test_execute.py` (Iter 1 Story 23 happy-path tests) inspect
    the log entries written by `execute()`. They use
    `_entries_of_type(entries, "<type>")` and `_last_of_type(...)`
    helpers, so the introduction of two NEW entry types
    (`materialized_file`, `materialization_status`) does not corrupt
    any existing assertion — the existing tests just don't see them.
    HOWEVER, several tests count the TOTAL number of log entries
    via `len(entries)` and assert a precise number (e.g. "5 entries
    after one execute run"). Those tests WILL break when Story 8
    adds two more entries per run (one per stage). The Coder for
    Story 8 must audit `test_execute.py` for `len(entries) ==`
    assertions and update the counts. We did NOT inventory them
    exhaustively here — that's the Coder's cascade work. THIS file
    is the new pin for the materialization logging contract.

  - `tests/test_execute_real_*.py` — these use mocked SDK shapes,
    so the same `len(entries) ==` audit applies. Same Coder cascade.

  - `tests/test_persistence_audit.py` enumerates `sm.__all__`. No
    edit needed — Story 8 doesn't add public names.

  - `tests/test_no_live_sdk_calls.py` — Story 8 is pure local I/O;
    no impact.

  - `tests/test_force_close.py`, `tests/test_close_iteration.py` —
    those interact with the log but not with execute's pipeline;
    no impact (they only assert lifecycle invariants, not entry
    counts in execute's local block).

These tests must FAIL on first run — the materialization-logging
wiring in `execute()` does not exist yet.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import uuid as _uuid

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


SOURCE_ROLES_DIR = PACKAGE_DIR / "roles"


# ---------------------------------------------------------------------------
# Fixtures + helpers (mirror test_execute.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Redirect `sm.LOG_PATH` to a per-test tmp file and stage roles/ +
    cwd at tmp_path so `resolve_role_spec` and `write_agent_output`
    both resolve at the redirected anchor.
    """
    import sm

    log_file = tmp_path / "log.jsonl"
    monkeypatch.setattr(sm, "LOG_PATH", log_file)

    # Stage roles/ at the new anchor so resolve_role_spec(...) succeeds
    # during the execute pipeline.
    dest = tmp_path / "roles"
    if not dest.exists() and SOURCE_ROLES_DIR.is_dir():
        shutil.copytree(SOURCE_ROLES_DIR, dest)

    # Run with cwd at tmp_path so write_agent_output's default
    # project_root (Path.cwd()) is also under the anchor, and so
    # files materialize next to log.jsonl, never in the source tree.
    monkeypatch.chdir(tmp_path)

    return log_file


def _open_iteration(iteration_id: str = "iter-1") -> dict:
    """Append an `iteration_open` entry directly."""
    import sm
    entry = sm.build_entry("iteration_open", {
        "iteration_id": iteration_id,
        "iteration_goal": "Test iteration",
        "requirements": [
            {"requirement_id": "req-1", "title": "T1",
             "description": "D1", "priority": "MUST",
             "acceptance_criteria": "AC1"},
        ],
    })
    sm._append_entry(entry)
    return entry


def _seed_backlog(n: int = 3) -> list:
    """Append a `story_backlog` entry with N canonical stories. Returns
    the list of story_ids in sequence order."""
    import sm
    story_ids = [_uuid.uuid4().hex for _ in range(n)]
    sizes = ["S", "M", "L"]
    stories = []
    for i in range(1, n + 1):
        stories.append({
            "story_id": story_ids[i - 1],
            "sequence": i,
            "title": f"Story {i}",
            "size": sizes[(i - 1) % 3],
            "requirement_ids": ["req-1"],
            "acceptance_criteria": f"Story {i} must pass its tests.",
        })
    entry = sm.build_entry("story_backlog", {
        "stories": stories,
        "role_spec_path": "<test-stub>",
        "role_spec_hash": "<test-stub>",
    })
    sm._append_entry(entry)
    return story_ids


def _seed_sprint(n_stories: int = 3, cut_at: int = 2,
                 iteration_id: str = "iter-1") -> tuple:
    """Open iteration + seed backlog + cut sprint. Returns
    (story_ids, in_sprint_ids, deferred_ids)."""
    import sm
    _open_iteration(iteration_id=iteration_id)
    sids = _seed_backlog(n=n_stories)
    sm.sprint_cut(cut_at)
    return sids, sids[:cut_at], sids[cut_at:]


def _make_test_writer(test_code: str = "def test_x():\n    assert True\n"):
    """spawn_test_writer stub returning the given code."""
    def _spawn(role_spec_path, story):
        return test_code
    return _spawn


def _make_coder(impl_code: str = "def foo():\n    return 1\n"):
    """spawn_coder stub returning the given code."""
    def _spawn(role_spec_path, story, test_code):
        return impl_code
    return _spawn


def _make_reviewer(approved: bool = True,
                   test_result: str = "all 12 tests passed"):
    """spawn_reviewer stub returning approved/test_result."""
    def _spawn(role_spec_path, story, test_code, impl_code):
        return {"approved": approved, "test_result": test_result}
    return _spawn


def _entries_of_type(entries: list, etype: str) -> list:
    return [e for e in entries if e.get("type") == etype]


def _last_of_type(entries: list, etype: str):
    matches = _entries_of_type(entries, etype)
    return matches[-1] if matches else None


def _first_of_type(entries: list, etype: str):
    matches = _entries_of_type(entries, etype)
    return matches[0] if matches else None


def _index_of_type(entries: list, etype: str, occurrence: int = 0):
    """Return the index in `entries` of the `occurrence`-th entry of
    `etype` (0-based). Returns -1 if not found."""
    seen = 0
    for i, e in enumerate(entries):
        if e.get("type") == etype:
            if seen == occurrence:
                return i
            seen += 1
    return -1


# ===========================================================================
# A. Greenfield happy path — both stages materialize successfully.
# ===========================================================================


def test_happy_writes_test_writer_materialized_file_entry(isolated_log):
    """A successful approve run appends a `materialized_file` entry for
    the TestWriter stage."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    mat_entries = [e for e in _entries_of_type(entries, "materialized_file")
                   if e.get("role") == "test_writer"]
    assert len(mat_entries) == 1, (
        f"expected exactly one TestWriter materialized_file entry; got "
        f"{len(mat_entries)} (types in log: "
        f"{[e['type'] for e in entries]!r})"
    )


def test_happy_writes_coder_materialized_file_entry(isolated_log):
    """A successful approve run appends a `materialized_file` entry for
    the Coder stage."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    mat_entries = [e for e in _entries_of_type(entries, "materialized_file")
                   if e.get("role") == "coder"]
    assert len(mat_entries) == 1, (
        f"expected exactly one Coder materialized_file entry; got "
        f"{len(mat_entries)} (types in log: "
        f"{[e['type'] for e in entries]!r})"
    )


def test_happy_two_materialized_file_entries_total(isolated_log):
    """Exactly two `materialized_file` entries per successful run:
    one TW + one Coder. Reviewer does not materialize a file."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    mat_entries = _entries_of_type(entries, "materialized_file")
    assert len(mat_entries) == 2, (
        f"expected exactly 2 materialized_file entries (TW + Coder); "
        f"got {len(mat_entries)}: roles={[e.get('role') for e in mat_entries]!r}"
    )


def test_happy_no_reviewer_materialized_file_entry(isolated_log):
    """No `materialized_file` entry with role='reviewer' — Reviewer
    doesn't produce files."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    reviewer_mat = [e for e in _entries_of_type(entries, "materialized_file")
                    if e.get("role") == "reviewer"]
    assert reviewer_mat == [], (
        f"Reviewer must not produce a materialized_file entry; "
        f"got {reviewer_mat!r}"
    )


def test_happy_test_writer_entry_has_story_id(isolated_log):
    """The TestWriter `materialized_file` entry's `story_id` matches the
    executed story."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw_mat = [e for e in _entries_of_type(entries, "materialized_file")
              if e.get("role") == "test_writer"][0]
    assert tw_mat["story_id"] == sid


def test_happy_coder_entry_has_story_id(isolated_log):
    """The Coder `materialized_file` entry's `story_id` matches the
    executed story."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    coder_mat = [e for e in _entries_of_type(entries, "materialized_file")
                 if e.get("role") == "coder"][0]
    assert coder_mat["story_id"] == sid


def test_happy_test_writer_entry_has_byte_count_and_sha256(isolated_log):
    """The TestWriter `materialized_file` entry carries a non-negative
    byte_count and a 64-hex-char sha256."""
    import sm
    import re
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw_mat = [e for e in _entries_of_type(entries, "materialized_file")
              if e.get("role") == "test_writer"][0]
    assert isinstance(tw_mat.get("byte_count"), int)
    assert tw_mat["byte_count"] >= 0
    assert isinstance(tw_mat.get("sha256"), str)
    assert re.match(r"^[0-9a-f]{64}$", tw_mat["sha256"]), (
        f"sha256 must be 64-char lowercase hex; got {tw_mat['sha256']!r}"
    )


# ===========================================================================
# B. Ordering — materialization rows fire AFTER the agent-output entry.
# ===========================================================================


def test_test_writer_materialized_after_testwriter_output(isolated_log):
    """The TestWriter `materialized_file` entry appears in the log AFTER
    the `testwriter_output` entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw_out_idx = _index_of_type(entries, "testwriter_output")
    # First materialized_file entry is the TestWriter's (Coder's fires
    # later in the pipeline).
    tw_mat_idx = _index_of_type(entries, "materialized_file", occurrence=0)
    assert tw_out_idx >= 0 and tw_mat_idx >= 0
    assert tw_out_idx < tw_mat_idx, (
        f"testwriter_output (idx {tw_out_idx}) must precede the TestWriter "
        f"materialized_file (idx {tw_mat_idx})"
    )


def test_coder_materialized_after_coder_output(isolated_log):
    """The Coder `materialized_file` entry appears in the log AFTER the
    `coder_output` entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    coder_out_idx = _index_of_type(entries, "coder_output")
    # Second materialized_file entry is the Coder's.
    coder_mat_idx = _index_of_type(entries, "materialized_file", occurrence=1)
    assert coder_out_idx >= 0 and coder_mat_idx >= 0
    assert coder_out_idx < coder_mat_idx, (
        f"coder_output (idx {coder_out_idx}) must precede the Coder "
        f"materialized_file (idx {coder_mat_idx})"
    )


def test_test_writer_materialized_before_coder_output(isolated_log):
    """The TestWriter `materialized_file` fires BEFORE the Coder stage
    starts — partial pipeline = truthful audit trail."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw_mat_idx = _index_of_type(entries, "materialized_file", occurrence=0)
    coder_out_idx = _index_of_type(entries, "coder_output")
    assert tw_mat_idx >= 0 and coder_out_idx >= 0
    assert tw_mat_idx < coder_out_idx, (
        f"TestWriter materialized_file (idx {tw_mat_idx}) must precede "
        f"coder_output (idx {coder_out_idx})"
    )


# ===========================================================================
# C. Files actually exist on disk + relative target_path.
# ===========================================================================


def test_happy_test_writer_file_exists_on_disk(isolated_log, tmp_path):
    """The TestWriter's output is actually written to disk under the
    LOG_PATH anchor."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    # Default test_writer path: tests/test_<short_id>.py under cwd.
    short_id = sid[:8]
    expected = tmp_path / "tests" / f"test_{short_id}.py"
    assert expected.exists(), (
        f"expected TestWriter file at {expected!s}; not found. "
        f"Contents of tmp_path: "
        f"{sorted(p.name for p in tmp_path.iterdir())!r}"
    )


def test_happy_coder_file_exists_on_disk(isolated_log, tmp_path):
    """The Coder's output is actually written to disk as sm.py under
    the LOG_PATH anchor."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    expected = tmp_path / "sm.py"
    assert expected.exists(), (
        f"expected Coder file at {expected!s}; not found. "
        f"Contents of tmp_path: "
        f"{sorted(p.name for p in tmp_path.iterdir())!r}"
    )


def test_happy_test_writer_target_path_is_relative(isolated_log):
    """The TestWriter `materialized_file` entry's `target_path` is
    relative (not absolute) — no leading `/`, no `:` drive prefix."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw_mat = [e for e in _entries_of_type(entries, "materialized_file")
              if e.get("role") == "test_writer"][0]
    tp = tw_mat["target_path"]
    assert isinstance(tp, str)
    # Not absolute on POSIX (no leading `/`) and not absolute on Windows
    # (no `<letter>:` prefix).
    assert not tp.startswith("/"), (
        f"target_path must be project-root-relative, not POSIX-absolute; "
        f"got {tp!r}"
    )
    assert not (len(tp) >= 2 and tp[1] == ":"), (
        f"target_path must be project-root-relative, not Windows-absolute; "
        f"got {tp!r}"
    )


def test_happy_coder_target_path_is_relative(isolated_log):
    """The Coder `materialized_file` entry's `target_path` is relative
    (not absolute)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    coder_mat = [e for e in _entries_of_type(entries, "materialized_file")
                 if e.get("role") == "coder"][0]
    tp = coder_mat["target_path"]
    assert isinstance(tp, str)
    assert not tp.startswith("/"), (
        f"target_path must be relative; got {tp!r}"
    )
    assert not (len(tp) >= 2 and tp[1] == ":"), (
        f"target_path must be relative; got {tp!r}"
    )


def test_happy_test_writer_target_path_matches_default_route(isolated_log):
    """TestWriter default route: `tests/test_<short_id>.py` where
    short_id = story_id[:8]."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw_mat = [e for e in _entries_of_type(entries, "materialized_file")
              if e.get("role") == "test_writer"][0]
    short_id = sid[:8]
    # Accept either POSIX or Windows separator in the stored path.
    norm = tw_mat["target_path"].replace("\\", "/")
    assert norm == f"tests/test_{short_id}.py", (
        f"expected target_path 'tests/test_{short_id}.py'; got "
        f"{tw_mat['target_path']!r}"
    )


def test_happy_coder_target_path_matches_default_route(isolated_log):
    """Coder default route: `sm.py`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    coder_mat = [e for e in _entries_of_type(entries, "materialized_file")
                 if e.get("role") == "coder"][0]
    norm = coder_mat["target_path"].replace("\\", "/")
    assert norm == "sm.py", (
        f"expected target_path 'sm.py'; got {coder_mat['target_path']!r}"
    )


# ===========================================================================
# D. Byte count / sha256 match what got written to disk.
# ===========================================================================


def test_happy_test_writer_byte_count_matches_file(isolated_log, tmp_path):
    """The entry's `byte_count` equals the on-disk file size."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw_mat = [e for e in _entries_of_type(entries, "materialized_file")
              if e.get("role") == "test_writer"][0]
    f = tmp_path / "tests" / f"test_{sid[:8]}.py"
    assert f.exists()
    assert tw_mat["byte_count"] == f.stat().st_size


def test_happy_coder_sha256_matches_file(isolated_log, tmp_path):
    """The entry's `sha256` equals the sha256 of the on-disk file bytes."""
    import sm
    import hashlib
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    coder_mat = [e for e in _entries_of_type(entries, "materialized_file")
                 if e.get("role") == "coder"][0]
    f = tmp_path / "sm.py"
    assert f.exists()
    expected = hashlib.sha256(f.read_bytes()).hexdigest()
    assert coder_mat["sha256"] == expected


# ===========================================================================
# E. Collision path — pre-existing target -> .candidate + status entry.
# ===========================================================================


def test_collision_test_writer_writes_candidate_status(isolated_log,
                                                       tmp_path):
    """Pre-create the TestWriter target -> the write goes to a
    `.candidate` sidecar AND a `materialization_status(collision)`
    entry is appended."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    # Pre-create the target so the TestWriter write hits a collision.
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    target = tests_dir / f"test_{sid[:8]}.py"
    target.write_text("# pre-existing\n", encoding="utf-8")

    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())

    entries = list(sm.read_entries())
    status_entries = [
        e for e in _entries_of_type(entries, "materialization_status")
        if e.get("status") == "collision"
    ]
    assert len(status_entries) >= 1, (
        f"expected at least one materialization_status(collision) entry; "
        f"got types {[e['type'] for e in entries]!r}"
    )


def test_collision_test_writer_target_path_ends_in_candidate(
        isolated_log, tmp_path):
    """On TestWriter collision, the corresponding `materialized_file`
    entry's `target_path` ends in `.candidate`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / f"test_{sid[:8]}.py").write_text(
        "# pre-existing\n", encoding="utf-8",
    )

    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())

    entries = list(sm.read_entries())
    tw_mat = [e for e in _entries_of_type(entries, "materialized_file")
              if e.get("role") == "test_writer"][0]
    assert tw_mat["target_path"].endswith(".candidate"), (
        f"on collision, TestWriter target_path must end in '.candidate'; "
        f"got {tw_mat['target_path']!r}"
    )


def test_collision_coder_writes_candidate_status(isolated_log, tmp_path):
    """Pre-create sm.py -> Coder write goes to `sm.py.candidate` AND a
    `materialization_status(collision)` entry fires."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    # Pre-create sm.py so the Coder write hits a collision.
    (tmp_path / "sm.py").write_text("# pre-existing\n", encoding="utf-8")

    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())

    entries = list(sm.read_entries())
    # At LEAST one collision status entry; precisely one is the Coder's.
    status_entries = [
        e for e in _entries_of_type(entries, "materialization_status")
        if e.get("status") == "collision"
    ]
    assert len(status_entries) >= 1


def test_collision_coder_target_path_ends_in_candidate(
        isolated_log, tmp_path):
    """On Coder collision, the entry's `target_path` ends in
    `.candidate`."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    (tmp_path / "sm.py").write_text("# pre-existing\n", encoding="utf-8")

    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())

    entries = list(sm.read_entries())
    coder_mat = [e for e in _entries_of_type(entries, "materialized_file")
                 if e.get("role") == "coder"][0]
    assert coder_mat["target_path"].endswith(".candidate"), (
        f"on collision, Coder target_path must end in '.candidate'; "
        f"got {coder_mat['target_path']!r}"
    )


def test_collision_status_reason_mentions_target(isolated_log, tmp_path):
    """The collision `materialization_status` entry's `reason` names
    the colliding target path (so an operator can locate the conflict)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / f"test_{sid[:8]}.py").write_text(
        "# pre-existing\n", encoding="utf-8",
    )

    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())

    entries = list(sm.read_entries())
    status_entries = [
        e for e in _entries_of_type(entries, "materialization_status")
        if e.get("status") == "collision"
    ]
    assert len(status_entries) >= 1
    # At least one collision status entry's reason names the TW target
    # filename (normalized to forward slashes for cross-platform match).
    matching = [
        e for e in status_entries
        if f"test_{sid[:8]}.py" in e.get("reason", "").replace("\\", "/")
    ]
    assert matching, (
        f"expected at least one collision status whose reason names the "
        f"TestWriter target 'test_{sid[:8]}.py'; got reasons: "
        f"{[e.get('reason') for e in status_entries]!r}"
    )


def test_collision_original_file_unchanged(isolated_log, tmp_path):
    """A collision does NOT overwrite the original file — its bytes
    stay exactly as pre-staged."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    pre_existing = "# pre-existing content\nx = 1\n"
    target = tests_dir / f"test_{sid[:8]}.py"
    target.write_text(pre_existing, encoding="utf-8")

    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())

    # Original is byte-for-byte unchanged.
    assert target.read_text(encoding="utf-8") == pre_existing


# ===========================================================================
# F. Path hints from agent output (`# path: <relpath>`)
# ===========================================================================


def test_path_hint_test_writer_routes_to_hinted_path(isolated_log, tmp_path):
    """A TestWriter `# path:` hint overrides the default route — the
    `materialized_file` entry's `target_path` reflects the hint."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    hinted_code = (
        "# path: tests/custom_route.py\n"
        "def test_y():\n"
        "    assert True\n"
    )
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(test_code=hinted_code),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    tw_mat = [e for e in _entries_of_type(entries, "materialized_file")
              if e.get("role") == "test_writer"][0]
    norm = tw_mat["target_path"].replace("\\", "/")
    assert norm == "tests/custom_route.py", (
        f"hint must override default route; got target_path "
        f"{tw_mat['target_path']!r}"
    )
    # And the file actually exists at the hinted location.
    assert (tmp_path / "tests" / "custom_route.py").exists()


def test_path_hint_coder_routes_to_hinted_path(isolated_log, tmp_path):
    """A Coder `# path:` hint overrides `sm.py` — the entry reflects
    the hinted path."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    hinted_code = "# path: lib/helpers.py\ndef helper():\n    return 2\n"
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(impl_code=hinted_code),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    coder_mat = [e for e in _entries_of_type(entries, "materialized_file")
                 if e.get("role") == "coder"][0]
    norm = coder_mat["target_path"].replace("\\", "/")
    assert norm == "lib/helpers.py"
    assert (tmp_path / "lib" / "helpers.py").exists()


def test_path_hint_collision_combined(isolated_log, tmp_path):
    """A path hint that resolves to an EXISTING file produces a
    collision: target_path ends in `.candidate` AND a collision-status
    entry fires."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    # Pre-create the hinted target.
    (tmp_path / "lib").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lib" / "helpers.py").write_text(
        "# pre-existing\n", encoding="utf-8",
    )
    hinted = "# path: lib/helpers.py\ndef helper():\n    return 2\n"
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(impl_code=hinted),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    coder_mat = [e for e in _entries_of_type(entries, "materialized_file")
                 if e.get("role") == "coder"][0]
    norm = coder_mat["target_path"].replace("\\", "/")
    assert norm == "lib/helpers.py.candidate", (
        f"hinted collision must produce '.candidate' target_path; "
        f"got {coder_mat['target_path']!r}"
    )
    status_entries = [
        e for e in _entries_of_type(entries, "materialization_status")
        if e.get("status") == "collision"
    ]
    assert len(status_entries) >= 1


# ===========================================================================
# G. Write failure path — write_agent_output raises -> rejected status.
# ===========================================================================


def test_write_failure_test_writer_appends_rejected_status(
        isolated_log):
    """A TestWriter output with a `..` path-hint (escapes project_root)
    triggers `write_agent_output` ValueError. `execute()` appends a
    `materialization_status(rejected)` entry capturing the failure."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad_code = "# path: ../escape.py\ndef test_z(): assert True\n"
    # The error propagates (TestWriter picks: re-raise the underlying
    # ValueError, no error swallowing).
    with pytest.raises(ValueError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(test_code=bad_code),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    rejected_entries = [
        e for e in _entries_of_type(entries, "materialization_status")
        if e.get("status") == "rejected"
    ]
    assert len(rejected_entries) >= 1, (
        f"expected at least one materialization_status(rejected) entry; "
        f"got types {[e['type'] for e in entries]!r}"
    )


def test_write_failure_no_coder_spawn(isolated_log):
    """A TestWriter write-failure stops the pipeline — Coder is NOT
    spawned, no `coder_output` entry written."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad_code = "# path: ../escape.py\ndef test_z(): assert True\n"
    coder_calls = {"count": 0}

    def _coder(role_spec_path, story, test_code):
        coder_calls["count"] += 1
        return "def foo(): return 1\n"

    with pytest.raises(ValueError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(test_code=bad_code),
                   spawn_coder=_coder,
                   spawn_reviewer=_make_reviewer())
    assert coder_calls["count"] == 0, (
        f"Coder must not be spawned after a TestWriter write-failure; "
        f"got {coder_calls['count']} call(s)"
    )
    entries = list(sm.read_entries())
    assert _entries_of_type(entries, "coder_output") == []


def test_write_failure_no_coder_materialized_file(isolated_log):
    """A TestWriter write-failure means no Coder-role
    `materialized_file` entry is appended."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    bad_code = "# path: ../escape.py\ndef test_z(): assert True\n"
    with pytest.raises(ValueError):
        sm.execute(in_sprint[0],
                   spawn_test_writer=_make_test_writer(test_code=bad_code),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    coder_mat = [e for e in _entries_of_type(entries, "materialized_file")
                 if e.get("role") == "coder"]
    assert coder_mat == [], (
        f"no Coder materialized_file should fire after a TestWriter "
        f"write-failure; got {coder_mat!r}"
    )


def test_write_failure_rejected_status_has_story_id(isolated_log):
    """The `materialization_status(rejected)` entry's `story_id` matches
    the executed story."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    bad_code = "# path: ../escape.py\ndef test_z(): assert True\n"
    with pytest.raises(ValueError):
        sm.execute(sid,
                   spawn_test_writer=_make_test_writer(test_code=bad_code),
                   spawn_coder=_make_coder(),
                   spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    rejected_entries = [
        e for e in _entries_of_type(entries, "materialization_status")
        if e.get("status") == "rejected"
    ]
    assert rejected_entries
    assert rejected_entries[-1]["story_id"] == sid


# ===========================================================================
# H. Reviewer rejection — NO rollback of materialized files / entries.
# ===========================================================================


def test_reviewer_rejection_keeps_test_writer_materialized_entry(
        isolated_log):
    """Reviewer rejection (after both writes succeeded) does NOT remove
    the TestWriter `materialized_file` entry from the log."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(
                   approved=False, test_result="0 of 5 passed"))
    entries = list(sm.read_entries())
    tw_mat = [e for e in _entries_of_type(entries, "materialized_file")
              if e.get("role") == "test_writer"]
    assert len(tw_mat) == 1, (
        f"TestWriter materialized_file entry must survive Reviewer "
        f"rejection; got {len(tw_mat)}"
    )


def test_reviewer_rejection_keeps_coder_materialized_entry(isolated_log):
    """Reviewer rejection does NOT remove the Coder `materialized_file`
    entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(
                   approved=False, test_result="failed"))
    entries = list(sm.read_entries())
    coder_mat = [e for e in _entries_of_type(entries, "materialized_file")
                 if e.get("role") == "coder"]
    assert len(coder_mat) == 1


def test_reviewer_rejection_keeps_files_on_disk(isolated_log, tmp_path):
    """Reviewer rejection does NOT delete the materialized files."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(
                   approved=False, test_result="rejected by review"))
    tw_file = tmp_path / "tests" / f"test_{sid[:8]}.py"
    coder_file = tmp_path / "sm.py"
    assert tw_file.exists(), (
        f"TestWriter file must survive Reviewer rejection; "
        f"missing {tw_file!s}"
    )
    assert coder_file.exists(), (
        f"Coder file must survive Reviewer rejection; "
        f"missing {coder_file!s}"
    )


def test_reviewer_rejection_story_goes_rejected(isolated_log):
    """Standard Iter 1 lifecycle: rejection transitions the story to
    'rejected' (sanity — the new materialization wiring doesn't change
    the existing lifecycle)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer(
                   approved=False, test_result="nope"))
    state = sm.derive_state()
    assert state["story_states"][sid] == "rejected"


# ===========================================================================
# I. Entry shape — built via the canonical factories.
# ===========================================================================


def test_materialized_file_entry_has_required_fields(isolated_log):
    """The `materialized_file` entry carries all five required fields
    (the factory enforces this; this test pins that `execute()` actually
    routes through the factory, not a hand-rolled dict)."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    for e in _entries_of_type(entries, "materialized_file"):
        for key in ("story_id", "role", "target_path",
                    "byte_count", "sha256"):
            assert key in e, (
                f"materialized_file entry missing required field "
                f"{key!r}; got keys {sorted(e.keys())!r}"
            )
        # Auto-stamped fields from build_entry.
        assert "id" in e and "timestamp" in e


def test_materialization_status_collision_entry_has_required_fields(
        isolated_log, tmp_path):
    """The `materialization_status` entry carries `story_id`, `status`,
    `reason`, plus auto-stamped id/timestamp/type from build_entry."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sid = in_sprint[0]
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / f"test_{sid[:8]}.py").write_text(
        "# pre-existing\n", encoding="utf-8",
    )
    sm.execute(sid,
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    status_entries = _entries_of_type(entries, "materialization_status")
    assert status_entries
    for e in status_entries:
        for key in ("story_id", "status", "reason"):
            assert key in e
        assert "id" in e and "timestamp" in e


def test_materialized_file_role_is_valid_string(isolated_log):
    """The entry's `role` is one of {'test_writer', 'coder'} — Story 5's
    factory rejects anything else with ValueError, so seeing a valid
    role round-tripped through the log confirms execute() routes through
    the factory."""
    import sm
    _, in_sprint, _ = _seed_sprint()
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    for e in _entries_of_type(entries, "materialized_file"):
        assert e["role"] in ("test_writer", "coder"), (
            f"materialized_file.role must be test_writer or coder "
            f"(no reviewer); got {e['role']!r}"
        )


# ===========================================================================
# J. Multiple-story isolation — each execute() emits its own pair.
# ===========================================================================


def test_two_executes_produce_four_materialized_file_entries(isolated_log):
    """Running execute() twice (different stories) appends 2 + 2 = 4
    `materialized_file` entries (TW + Coder per run)."""
    import sm
    _, in_sprint, _ = _seed_sprint(n_stories=3, cut_at=2)
    sm.execute(in_sprint[0],
               spawn_test_writer=_make_test_writer(),
               spawn_coder=_make_coder(),
               spawn_reviewer=_make_reviewer())
    # Second story's TestWriter writes to tests/test_<other_short>.py
    # (a different default route — no collision).
    sm.execute(in_sprint[1],
               spawn_test_writer=_make_test_writer(
                   test_code="# path: tests/second_run.py\ndef test_q():"
                             " assert True\n"),
               spawn_coder=_make_coder(
                   impl_code="# path: lib/second_run.py\ndef q():"
                             " return 1\n"),
               spawn_reviewer=_make_reviewer())
    entries = list(sm.read_entries())
    mat = _entries_of_type(entries, "materialized_file")
    assert len(mat) == 4, (
        f"expected 4 materialized_file entries across 2 runs; got "
        f"{len(mat)}: {[(e.get('role'), e.get('target_path')) for e in mat]!r}"
    )
