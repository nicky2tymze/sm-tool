"""Skeleton acceptance tests. Story 1 — module importable, LOG_PATH exists."""

from __future__ import annotations

import pathlib
import sys

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


def test_module_file_exists():
    assert (PACKAGE_DIR / "sm.py").is_file()


def test_module_importable():
    import sm
    assert sm is not None


def test_log_path_is_pathlib_path():
    from sm import LOG_PATH
    assert isinstance(LOG_PATH, pathlib.PurePath)


def test_log_path_filename():
    from sm import LOG_PATH
    assert LOG_PATH.name == "log.jsonl"


def test_log_path_resolves_under_package_dir():
    from sm import LOG_PATH
    assert pathlib.Path(LOG_PATH).resolve().parent == PACKAGE_DIR.resolve()
