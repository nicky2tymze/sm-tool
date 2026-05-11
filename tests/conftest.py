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
import sys
import types
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROLES_DIR = PACKAGE_ROOT / "roles"


def _build_sentinel_anthropic_module() -> types.ModuleType:
    """Construct a sentinel `anthropic` module whose `Anthropic` class refuses
    to instantiate. Installed into sys.modules at session start so any
    unguarded test that triggers `_invoke_anthropic` fails LOUDLY instead of
    billing the API.

    Story 15 — Iter 2 suite-green gate. Interoperates with the Stories 6/7/8/9
    `_install_fake_anthropic` pattern: tests that monkeypatch
    `sys.modules["anthropic"]` override this sentinel for the test's duration,
    and `monkeypatch.setitem` restores the sentinel on teardown.
    """

    class _SentinelAnthropic:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "Refusing to construct a LIVE anthropic.Anthropic "
                "client during the test suite. Tests must install a "
                "fake anthropic module via "
                "monkeypatch.setitem(sys.modules, 'anthropic', "
                "<fake>) BEFORE invoking any real-agent default. "
                "See tests/test_decompose_real_spawn.py for the "
                "`_install_fake_anthropic` pattern."
            )

    module = types.ModuleType("anthropic")
    module.Anthropic = _SentinelAnthropic
    # `AsyncAnthropic` is not currently used by sm.py but is added defensively
    # so a future async refactor would still fail loudly.
    module.AsyncAnthropic = _SentinelAnthropic
    module.__sm_sentinel__ = True  # marker for tests to detect
    return module


@pytest.fixture(autouse=True, scope="session")
def _install_live_sdk_guard_session():
    """Session-scoped autouse fixture: installs the sentinel `anthropic`
    module into sys.modules BEFORE any test runs. Tests that need a
    real-shaped fake override this via
    `monkeypatch.setitem(sys.modules, 'anthropic', fake_module)`;
    `monkeypatch` restores the sentinel between tests automatically.
    """
    sys.modules["anthropic"] = _build_sentinel_anthropic_module()
    yield


@pytest.fixture(autouse=True)
def _install_live_sdk_guard():
    """Function-scoped autouse companion to the session fixture. Some
    tests deliberately purge `anthropic` from sys.modules (e.g.
    `test_invoke_anthropic.py::_purge_anthropic_from_sys_modules`,
    `test_resolve_api_key.py::purge_anthropic_imports`) to observe
    lazy-import behavior, and may leave `sys.modules` without an
    `anthropic` entry on teardown. This function-scoped fixture re-
    installs the sentinel BEFORE every test so the guard is never
    missing when a downstream test depends on it. Per-test
    `monkeypatch.setitem(sys.modules, 'anthropic', fake)` calls run
    AFTER this fixture's setup and supersede the sentinel for that
    test, then auto-restore the sentinel on teardown.
    """
    sys.modules["anthropic"] = _build_sentinel_anthropic_module()
    yield


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
