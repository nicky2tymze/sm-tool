"""Iter 2 Story 1 — `anthropic` declared as a runtime dependency.

Story 1 (Iter 2, size S) pins a single contract: `anthropic` is listed in
the project's existing dependency manifest (`pyproject.toml`, since
that's what sm-tool uses) so a clean `pip install` of the project
resolves and installs the SDK with no extra operator step.

What this file pins:

  - pyproject.toml is parseable as TOML and has a `[project]` table.
  - `anthropic` appears exactly once in `[project.dependencies]`.
  - No extras flags (`anthropic[bedrock]`, etc.) introduced.
  - No version pin tighter than the SDK's published compatibility band:
    bare `anthropic` OR a `>=` lower bound is allowed; `==` and `~=`
    pins are forbidden.
  - No competing dependency manifests added (no `requirements.txt`,
    `requirements-dev.txt`, or `setup.py`).
  - `pyproject.toml` hygiene preserved: trailing newline kept, no
    duplicated top-level sections introduced.

All tests are static — they read `pyproject.toml` as text and parse it
with the stdlib `tomllib` module (Python 3.11+, which this project
already requires).
"""

from __future__ import annotations

import pathlib
import re

import pytest

try:  # Python 3.11+ stdlib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover — requires-python = ">=3.10"
    tomllib = None  # type: ignore[assignment]


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
PYPROJECT_PATH = PACKAGE_DIR / "pyproject.toml"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _read_text() -> str:
    """Return pyproject.toml as text (utf-8)."""
    return PYPROJECT_PATH.read_text(encoding="utf-8")


def _parse() -> dict:
    """Parse pyproject.toml as TOML and return the top-level mapping."""
    assert tomllib is not None, (
        "tomllib is unavailable; this project requires Python >= 3.10 "
        "and the test suite expects 3.11+ for tomllib"
    )
    return tomllib.loads(_read_text())


def _project_table() -> dict:
    data = _parse()
    assert "project" in data, "[project] table missing from pyproject.toml"
    return data["project"]


def _dependencies() -> list:
    """Return the [project.dependencies] list, or [] if the key is absent."""
    proj = _project_table()
    deps = proj.get("dependencies", [])
    assert isinstance(deps, list), (
        f"[project.dependencies] must be a TOML array; got {type(deps).__name__}"
    )
    return deps


def _anthropic_entries() -> list[str]:
    """Return every entry in [project.dependencies] whose distribution
    name normalizes to `anthropic`.

    PEP 508 / PEP 503: a distribution requirement is the leading
    identifier before any version specifier, extras, marker, or
    whitespace. We split on the first character that is not a valid
    distribution-name char (`[A-Za-z0-9_.-]`).
    """
    matches: list[str] = []
    for raw in _dependencies():
        if not isinstance(raw, str):
            continue
        stripped = raw.strip()
        # Distribution name is the leading [A-Za-z0-9_.-]+ run.
        m = re.match(r"([A-Za-z0-9_.\-]+)", stripped)
        if not m:
            continue
        name = m.group(1)
        # PEP 503 normalization: lowercase, runs of `-_.` → single `-`.
        norm = re.sub(r"[-_.]+", "-", name).lower()
        if norm == "anthropic":
            matches.append(stripped)
    return matches


# ===========================================================================
# Category A — pyproject parse (3)
#
# pyproject.toml exists, parses as valid TOML, and has a [project] table.
# Without these, every assertion below is vacuous.
# ===========================================================================


def test_pyproject_toml_exists():
    """pyproject.toml is the manifest sm-tool uses — must be present."""
    assert PYPROJECT_PATH.is_file(), (
        f"pyproject.toml not found at {PYPROJECT_PATH}"
    )


def test_pyproject_toml_parses_as_valid_toml():
    """pyproject.toml is syntactically valid TOML — every other audit
    below depends on this."""
    try:
        data = _parse()
    except Exception as exc:  # noqa: BLE001 — surface the parse error
        pytest.fail(f"pyproject.toml is not valid TOML: {exc!r}")
    assert isinstance(data, dict)


def test_pyproject_has_project_table():
    """PEP 621 `[project]` table is where runtime dependencies live."""
    data = _parse()
    assert "project" in data, (
        "pyproject.toml must contain a [project] table (PEP 621)"
    )
    assert isinstance(data["project"], dict)


# ===========================================================================
# Category B — anthropic listed (4)
#
# `anthropic` is in [project.dependencies], appears exactly once, and
# carries no extras flags.
# ===========================================================================


def test_dependencies_key_present_in_project_table():
    """[project] must declare a `dependencies` key for Story 1 to be
    satisfied. An empty list is not sufficient — we want `anthropic`
    in it — but the key itself must exist."""
    proj = _project_table()
    assert "dependencies" in proj, (
        "[project.dependencies] missing from pyproject.toml; Story 1 "
        "requires `anthropic` to be declared as a runtime dependency"
    )
    assert isinstance(proj["dependencies"], list), (
        "[project.dependencies] must be a TOML array of strings"
    )


def test_anthropic_in_project_dependencies():
    """`anthropic` appears in [project.dependencies]."""
    entries = _anthropic_entries()
    assert entries, (
        "expected `anthropic` to appear in [project.dependencies] of "
        f"pyproject.toml; got entries: {_dependencies()!r}"
    )


def test_anthropic_listed_exactly_once():
    """Only one entry resolves to the `anthropic` distribution. A
    duplicate (e.g. once bare and once pinned) would be a hygiene break.
    """
    entries = _anthropic_entries()
    assert len(entries) == 1, (
        f"expected exactly one `anthropic` entry in "
        f"[project.dependencies]; got {len(entries)}: {entries!r}"
    )


def test_anthropic_entry_has_no_extras_flag():
    """No extras flag (`anthropic[bedrock]`, `anthropic[vertex]`, etc.).
    The acceptance criteria forbid extras."""
    entries = _anthropic_entries()
    assert entries, "no `anthropic` entry to audit for extras"
    entry = entries[0]
    assert "[" not in entry and "]" not in entry, (
        f"anthropic dependency must not declare extras flags; got "
        f"{entry!r}"
    )


# ===========================================================================
# Category C — No version pin tighter than the SDK compat band (3)
#
# Bare `anthropic` is preferred; `>=X.Y` is allowed. `==`, `~=`, and a
# `<` upper bound are too tight.
# ===========================================================================


def test_anthropic_entry_has_no_exact_version_pin():
    """`==` exact pin is too tight — forbidden by acceptance criteria."""
    entries = _anthropic_entries()
    assert entries, "no `anthropic` entry to audit for pin tightness"
    entry = entries[0]
    assert "==" not in entry, (
        f"anthropic dependency must not use `==` exact pin; got {entry!r}"
    )


def test_anthropic_entry_has_no_compatible_release_pin():
    """`~=` (compatible-release) is also too tight — forbidden."""
    entries = _anthropic_entries()
    assert entries, "no `anthropic` entry to audit for compatible-release pin"
    entry = entries[0]
    assert "~=" not in entry, (
        f"anthropic dependency must not use `~=` compatible-release "
        f"pin; got {entry!r}"
    )


def test_anthropic_entry_uses_bare_or_lower_bound_only():
    """Only bare `anthropic` or a `>=` lower-bound spec is permitted.
    The full entry text — after stripping the name — must contain at
    most a `>=` clause and whitespace/digits/dots/commas/etc."""
    entries = _anthropic_entries()
    assert entries, "no `anthropic` entry to audit for spec shape"
    entry = entries[0]
    # Strip leading name `anthropic` (case-insensitive, PEP 503-style
    # canonicalization is unnecessary here — the entry IS named `anthropic`).
    remainder = re.sub(r"^[A-Za-z0-9_.\-]+", "", entry).strip()
    # Acceptable shapes:
    #   ""                       (bare)
    #   ">=X.Y"  / ">=X.Y.Z"     (loose lower bound)
    # Forbidden:
    #   "<", "!=", "===", "~=", "=="
    forbidden_ops = ("<", "!=", "===", "~=", "==")
    for op in forbidden_ops:
        assert op not in remainder, (
            f"anthropic dependency must not contain `{op}`; got "
            f"specifier {remainder!r}"
        )


# ===========================================================================
# Category D — No new manifest files (3)
#
# The acceptance criteria require a single manifest. sm-tool uses
# pyproject.toml; no `requirements.txt`, `requirements-dev.txt`, or
# `setup.py` may appear.
# ===========================================================================


def test_no_requirements_txt_introduced():
    """`requirements.txt` would be a second manifest — forbidden."""
    p = PACKAGE_DIR / "requirements.txt"
    assert not p.exists(), (
        f"`requirements.txt` must not exist (Story 1: single manifest); "
        f"found at {p}"
    )


def test_no_requirements_dev_txt_introduced():
    """`requirements-dev.txt` would also be a second manifest."""
    p = PACKAGE_DIR / "requirements-dev.txt"
    assert not p.exists(), (
        f"`requirements-dev.txt` must not exist (Story 1: single "
        f"manifest); found at {p}"
    )


def test_no_setup_py_introduced():
    """`setup.py` would be a competing build/spec channel."""
    p = PACKAGE_DIR / "setup.py"
    assert not p.exists(), (
        f"`setup.py` must not exist (Story 1: single manifest in "
        f"pyproject.toml); found at {p}"
    )


# ===========================================================================
# Category E — pyproject hygiene (3)
#
# Trailing newline preserved; no duplicated top-level sections
# introduced; build-system table still intact.
# ===========================================================================


def test_pyproject_ends_with_trailing_newline():
    """Acceptance criteria require existing manifest hygiene preserved.
    The file must end in a newline."""
    text = _read_text()
    assert text.endswith("\n"), (
        "pyproject.toml must end with a trailing newline (hygiene)"
    )


def test_pyproject_has_no_duplicate_top_level_sections():
    """No `[project]`, `[build-system]`, `[tool.pytest.ini_options]`,
    or `[project.dependencies]` header appears more than once. A
    duplicated TOML header is a parse error in strict parsers and a
    hygiene break regardless."""
    text = _read_text()
    headers = re.findall(r"^\s*\[([^\]\n]+)\]\s*$", text, re.MULTILINE)
    seen: dict[str, int] = {}
    for h in headers:
        key = h.strip()
        seen[key] = seen.get(key, 0) + 1
    dupes = {k: v for k, v in seen.items() if v > 1}
    assert not dupes, (
        f"pyproject.toml has duplicated section headers: {dupes!r}"
    )


def test_pyproject_build_system_table_preserved():
    """The pre-existing `[build-system]` table must survive the Story 1
    edit unchanged in shape — same `requires` list semantics, same
    backend."""
    data = _parse()
    bs = data.get("build-system")
    assert isinstance(bs, dict), (
        "pyproject.toml must keep its [build-system] table"
    )
    assert "requires" in bs and isinstance(bs["requires"], list), (
        "[build-system].requires must remain a list"
    )
    assert bs.get("build-backend"), (
        "[build-system].build-backend must remain set"
    )
