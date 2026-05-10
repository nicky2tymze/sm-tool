"""Pytest fixtures shared across the sm-tool test suite.

Story 9 — when a test monkeypatches `sm.LOG_PATH` to a `tmp_path`-based file,
the resolver's package anchor (`LOG_PATH.parent`) shifts to `tmp_path`. The
`test_decompose.py` `isolated_log` fixture redirects `LOG_PATH` but does not
stage a `roles/` dir at the new anchor. The tests in that file still expect
`sm.resolve_role_spec("sm_agent")` to succeed.

This autouse fixture mirrors the package's `roles/` dir under `tmp_path/roles/`
ONLY when the current test requested `isolated_log`. Tests that use
`temp_roles_dir` (in `test_resolve_role_spec.py`) — which intentionally
creates an empty `roles/` — are unaffected because we don't fire there.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROLES_DIR = PACKAGE_ROOT / "roles"


@pytest.fixture(autouse=True)
def _stage_roles_for_decompose(request, tmp_path):
    """If the current test lives in `test_decompose.py` and uses the
    `isolated_log` fixture, mirror the package's `roles/` dir into
    `tmp_path/roles/` so `resolve_role_spec` finds the canonical role-spec
    markdown files.

    Scoped to `test_decompose.py` to avoid touching:
      - `test_resolve_role_spec.py` (creates its own empty `roles/` via
        `temp_roles_dir`)
      - other test files that use `isolated_log` and assert no sidecars
        appear under `tmp_path` (e.g. `test_append_entry.py`,
        `test_read_entries.py`).
    """
    test_path = Path(str(request.node.fspath))
    if test_path.name != "test_decompose.py":
        yield
        return
    if "isolated_log" not in request.fixturenames:
        yield
        return

    dest = tmp_path / "roles"
    if not dest.exists() and SOURCE_ROLES_DIR.is_dir():
        shutil.copytree(SOURCE_ROLES_DIR, dest)
    yield
