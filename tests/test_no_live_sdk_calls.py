"""Iter 2 Story 15 — Suite-green gate: no live SDK calls during tests.

This file pins the SUITE-GREEN GATE for Iter 2:

  1. Every existing test plus every Iter 2 test passes with no
     `ANTHROPIC_API_KEY` set — the suite is self-contained.
  2. Iter 2 tests for the four real-agent defaults mock the `anthropic`
     SDK at the Story 5 provider seam — zero tests make real network
     calls.
  3. The four real-agent default test files (Stories 6/7/8/9) verify all
     the failure paths: API-key-missing, model/max_tokens precedence,
     JSON parse errors typed correctly per role, SDK errors wrapped and
     propagated.
  4. A TEST-TIME LIVE-SDK GUARD refuses to run if real
     `anthropic.Anthropic` is instantiated during the suite — fails
     LOUDLY rather than silently billing the API.

Pinned clauses (verbatim from `iter2/Stories_v1.md`, Story 15):

  - All 1680+ pre-existing tests pass after Iter 2 changes — verified by
    a single full-suite run with `ANTHROPIC_API_KEY` unset.
  - New tests added by Stories 2-9 mock the `anthropic` SDK client at
    the Story 5 provider-seam boundary (per ASSUMPTION 6) — zero tests
    make a real network call.
  - Tests that exercise the four real-agent defaults verify:
    API-key-missing path raises `MissingAPIKeyError`, model/max_tokens
    precedence honored, JSON parse errors typed correctly, SDK errors
    wrapped and propagated.
  - A test-time guard (fixture or pytest plugin) refuses to run if the
    suite detects a real `anthropic.Anthropic` client instantiation —
    fails loudly rather than billing the API.

CONTRACT INTERPRETATION (locked by TestWriter):

  - The guard lives in `tests/conftest.py` as an autouse session-scoped
    fixture (or equivalent module-level setup) that installs a SENTINEL
    fake `anthropic` module into `sys.modules` BEFORE any test runs.
  - The sentinel's `Anthropic` class raises a loud `RuntimeError` (or
    SystemError) when instantiated. The error message names "live",
    "real", or "production" SDK call AND tells the developer to install
    a fake via the `_install_fake_anthropic` pattern.
  - Per-test fixtures from Stories 6/7/8/9 that monkey-patch
    `sys.modules["anthropic"]` with their own fake CONTINUE TO WORK —
    `monkeypatch.setitem` overrides the sentinel for the duration of
    that test, then auto-restores. The session-scoped sentinel snaps
    back into place between tests.
  - `_invoke_anthropic` in `sm.py` lazy-imports `anthropic` inside the
    function body (Story 5's contract — line 675). The lazy import
    finds whatever is in `sys.modules["anthropic"]` at call time:
       * with no per-test fake installed -> sentinel -> RuntimeError
       * with a per-test fake installed   -> fake -> success
  - Failure-path coverage is verified META-style: this file greps the
    four real-agent test files for references to each failure-path
    symbol (`MissingAPIKeyError`, the role-specific `*AgentError`,
    `ConfigError` where applicable, and parse errors for JSON roles)
    and asserts at least one mention per default. Story 15 is a GATE,
    not a re-implementation of the failure-path tests already pinned by
    6/7/8/9.

GUARD ARCHITECTURE — what `conftest.py` should add (for Coder):

    import sys
    import types

    import pytest


    _ANTHROPIC_SENTINEL_INSTALLED = False


    def _build_sentinel_anthropic_module() -> types.ModuleType:
        \"\"\"Construct a sentinel `anthropic` module whose `Anthropic`
        class refuses to instantiate. Installed into sys.modules at
        session start so any unguarded test that triggers
        `_invoke_anthropic` fails LOUDLY instead of billing the API.\"\"\"

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
        # `AsyncAnthropic` is not currently used by sm.py but is added
        # defensively so a future async refactor would still fail loudly.
        module.AsyncAnthropic = _SentinelAnthropic
        module.__sm_sentinel__ = True  # marker for tests to detect
        return module


    @pytest.fixture(autouse=True, scope="session")
    def _install_live_sdk_guard():
        \"\"\"Session-scoped autouse fixture: installs the sentinel
        `anthropic` module into sys.modules BEFORE any test runs. Tests
        that need a real-shaped fake override this via
        `monkeypatch.setitem(sys.modules, 'anthropic', fake_module)`;
        `monkeypatch` restores the sentinel between tests automatically.
        \"\"\"
        global _ANTHROPIC_SENTINEL_INSTALLED
        # Don't clobber a real anthropic install in a non-test environment;
        # only install the sentinel when running under pytest.
        sys.modules["anthropic"] = _build_sentinel_anthropic_module()
        _ANTHROPIC_SENTINEL_INSTALLED = True
        yield

INTEROP WITH `_install_fake_anthropic` PATTERN:

  Story 6/7/8/9 test files define a per-test helper
  `_install_fake_anthropic(monkeypatch, ...)` that calls
  `monkeypatch.setitem(sys.modules, "anthropic", fake_module)`.
  `monkeypatch.setitem` restores the previous value of
  `sys.modules["anthropic"]` (the sentinel) at test teardown, so the
  sentinel snaps back between tests. No changes to the existing fixture
  pattern are required.

ANTI-LANE:
  - This file does NOT modify sm.py.
  - This file does NOT modify any existing tests.
  - This file does NOT itself implement the guard — the Coder adds the
    guard fixture to `tests/conftest.py`. These tests will FAIL until
    the Coder lands the guard, which is the expected red-green-green
    cadence.

CASCADE NOTE:
  Once the Coder adds the sentinel fixture to `conftest.py`, ALL
  existing tests must continue to pass — the sentinel only fires when
  no per-test fake has been installed AND the production code attempts
  a real SDK call. The existing 2367 tests all either (a) don't touch
  the SDK at all, or (b) install a fake via `_install_fake_anthropic`
  before the call. So the sentinel is invisible to them.
"""

from __future__ import annotations

import importlib
import pathlib
import re
import subprocess
import sys
import types

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
TESTS_DIR = THIS_FILE.parent
SM_PATH = PACKAGE_DIR / "sm.py"
CONFTEST_PATH = TESTS_DIR / "conftest.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# Story 15 contract pins the four real-agent test files by name.
REAL_AGENT_TEST_FILES = (
    TESTS_DIR / "test_decompose_real_spawn.py",
    TESTS_DIR / "test_execute_real_test_writer.py",
    TESTS_DIR / "test_execute_real_coder.py",
    TESTS_DIR / "test_execute_real_reviewer.py",
)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Category A — Live-SDK guard exists and is active
# ---------------------------------------------------------------------------


def test_conftest_file_exists():
    """`tests/conftest.py` must exist — the guard lives there."""
    assert CONFTEST_PATH.is_file(), (
        f"conftest.py not found at {CONFTEST_PATH}; Story 15's guard "
        f"requires a session-level autouse fixture in conftest.py"
    )


def test_conftest_installs_anthropic_sentinel_into_sys_modules():
    """After the conftest's session fixture fires, `sys.modules["anthropic"]`
    points at the sentinel module — verifiable from any test that has
    not overridden it via `monkeypatch.setitem`."""
    assert "anthropic" in sys.modules, (
        "sys.modules['anthropic'] should be populated by the conftest "
        "session-scoped autouse fixture BEFORE any test runs."
    )
    mod = sys.modules["anthropic"]
    assert hasattr(mod, "Anthropic"), (
        "sys.modules['anthropic'] must expose an `Anthropic` attribute "
        "even when the sentinel is installed (so the lazy import in "
        "_invoke_anthropic resolves)."
    )


def test_sentinel_anthropic_class_raises_on_instantiation():
    """Instantiating `sys.modules['anthropic'].Anthropic(api_key=...)`
    directly must raise a loud error — the sentinel must NOT silently
    construct."""
    anthropic_mod = sys.modules["anthropic"]
    with pytest.raises(Exception) as exc_info:
        anthropic_mod.Anthropic(api_key="sk-test-not-real")
    # Loud error — RuntimeError, SystemError, or a custom subclass.
    # NOT a TypeError / AttributeError (those would indicate the
    # sentinel was overridden by a fake that doesn't accept api_key).
    assert not isinstance(exc_info.value, (TypeError, AttributeError)), (
        f"Sentinel raised {type(exc_info.value).__name__} — looks like "
        f"a real fake leaked in. Sentinel should raise RuntimeError or "
        f"similar loud error."
    )


def test_sentinel_error_mentions_live_or_real_call():
    """The sentinel's error message must call out the LIVE/REAL nature
    of the rejection so a developer reading a test failure traceback
    immediately understands why."""
    anthropic_mod = sys.modules["anthropic"]
    with pytest.raises(Exception) as exc_info:
        anthropic_mod.Anthropic(api_key="sk-test")
    msg = str(exc_info.value).lower()
    assert any(
        keyword in msg
        for keyword in ("live", "real", "production", "refus", "sentinel")
    ), (
        f"Sentinel error message {msg!r} should mention 'live', 'real', "
        f"'production', 'refus(e)', or 'sentinel' so the developer "
        f"recognizes it as the guard firing."
    )


def test_sentinel_error_mentions_remediation():
    """The sentinel's error message must tell the developer HOW to fix
    it — either by mentioning the `_install_fake_anthropic` helper, the
    `monkeypatch.setitem` pattern, or the `sys.modules` injection."""
    anthropic_mod = sys.modules["anthropic"]
    with pytest.raises(Exception) as exc_info:
        anthropic_mod.Anthropic(api_key="sk-test")
    msg = str(exc_info.value).lower()
    assert any(
        keyword in msg
        for keyword in (
            "_install_fake_anthropic",
            "monkeypatch",
            "sys.modules",
            "fake",
            "mock",
        )
    ), (
        f"Sentinel error {msg!r} should mention `_install_fake_anthropic`, "
        f"`monkeypatch`, `sys.modules`, `fake`, or `mock` so a developer "
        f"hitting it can fix the test without reading conftest source."
    )


def test_sentinel_does_not_interfere_with_legitimate_fake_install(monkeypatch):
    """When a test installs its own fake via `monkeypatch.setitem`, the
    sentinel is overridden for the test's lifetime. Constructing the
    fake's `Anthropic` must succeed."""
    class _LocalFakeAnthropic:
        def __init__(self, api_key=None, **kwargs):
            self.api_key = api_key

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _LocalFakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    # Now the sentinel is gone for this test.
    import anthropic  # noqa: WPS433 — intentional re-import
    client = anthropic.Anthropic(api_key="sk-test")
    assert client.api_key == "sk-test"


def test_sentinel_restored_after_fake_teardown(monkeypatch):
    """After a per-test fake's monkeypatch is torn down, the sentinel
    must snap back into place. We exercise this WITHIN one test by
    installing then explicitly undoing."""
    class _LocalFake:
        def __init__(self, *a, **kw):
            self.api_key = kw.get("api_key")

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _LocalFake
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.undo()

    # Sentinel should be back.
    anthropic_mod = sys.modules["anthropic"]
    with pytest.raises(Exception):
        anthropic_mod.Anthropic(api_key="sk-test")


def test_guard_fails_loudly_not_silently():
    """Triggering the sentinel must surface as a test FAILURE, not a
    silent return or a network call. We assert by capturing the
    exception traceback content and confirming it points at the
    sentinel construction site, not at requests/httpx/socket code."""
    anthropic_mod = sys.modules["anthropic"]
    try:
        anthropic_mod.Anthropic(api_key="sk-test")
    except Exception as exc:
        tb_text = repr(exc)
        # Loud error type.
        assert any(
            cls in type(exc).__name__
            for cls in ("RuntimeError", "SystemError", "SystemExit",
                        "Sentinel", "Error")
        ), (
            f"Sentinel raised {type(exc).__name__} — should be a loud "
            f"error type, not a quiet network/AttributeError."
        )
        # Should not look like a network failure.
        for forbidden in ("ConnectionError", "URLError", "socket.gaierror",
                          "ssl.SSLError", "httpx", "requests."):
            assert forbidden not in tb_text, (
                f"Sentinel raise looks like a real network failure "
                f"({forbidden} in {tb_text!r}); the guard should fire "
                f"BEFORE any network code."
            )
    else:
        pytest.fail(
            "Sentinel Anthropic construction did NOT raise — guard is "
            "missing or broken."
        )


# ---------------------------------------------------------------------------
# Category B — Suite cleanliness: no direct anthropic imports outside seam
# ---------------------------------------------------------------------------


def test_no_test_file_imports_anthropic_at_module_top_level():
    """No test file may have a TOP-LEVEL `import anthropic` or
    `from anthropic`. (Indented imports inside a function body — after
    a per-test fake has been installed — are legitimate and used by
    this very file's interop tests.) The only way to reference the SDK
    in tests at module scope is via `sys.modules["anthropic"]` injection.
    """
    bad_imports = []
    for test_path in sorted(TESTS_DIR.glob("test_*.py")):
        text = _read(test_path)
        # Only flag imports that start at column 0 — i.e. module top level.
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.startswith("import anthropic") or \
                    line.startswith("from anthropic"):
                bad_imports.append(f"{test_path.name}:{lineno}: {line}")
    assert not bad_imports, (
        "Test files must not import `anthropic` at module top level — "
        "they should inject a fake via `monkeypatch.setitem(sys.modules, "
        "'anthropic', fake)`. Offenders:\n  " + "\n  ".join(bad_imports)
    )


def test_only_one_real_anthropic_import_in_sm():
    """`sm.py` must have EXACTLY ONE `import anthropic` site — Story 5's
    seam inside `_invoke_anthropic`. Drift here means the SDK leaked
    out of the seam."""
    text = _read(SM_PATH)
    matches = re.findall(
        r"^[ \t]*(?:import\s+anthropic|from\s+anthropic\b)",
        text,
        flags=re.MULTILINE,
    )
    assert len(matches) == 1, (
        f"Expected exactly 1 `import anthropic` in sm.py (Story 5's "
        f"seam in `_invoke_anthropic`); found {len(matches)}: {matches}"
    )


def test_lone_sm_anthropic_import_is_inside_invoke_anthropic():
    """The single `import anthropic` in `sm.py` must be inside the
    `_invoke_anthropic` function body (Story 5's lazy import contract),
    not at module top level."""
    text = _read(SM_PATH)
    lines = text.splitlines()
    import_line_no = None
    for idx, line in enumerate(lines):
        if re.match(r"^[ \t]*import\s+anthropic", line):
            import_line_no = idx
            break
    assert import_line_no is not None, "no `import anthropic` found in sm.py"

    # The import must be indented (inside a function body).
    raw_line = lines[import_line_no]
    assert raw_line.startswith((" ", "\t")), (
        f"sm.py line {import_line_no + 1} `{raw_line}` is at module top "
        f"level — it must live inside `_invoke_anthropic` for the lazy-"
        f"import / test-injection contract to hold."
    )

    # Walk backwards looking for the enclosing `def _invoke_anthropic`.
    found_seam = False
    for prior in range(import_line_no - 1, -1, -1):
        prior_line = lines[prior]
        if prior_line.lstrip().startswith("def "):
            if "_invoke_anthropic" in prior_line:
                found_seam = True
            break
    assert found_seam, (
        f"sm.py line {import_line_no + 1}'s `import anthropic` is not "
        f"inside `_invoke_anthropic`. The seam contract requires the "
        f"import to live in that function body."
    )


def test_fake_anthropic_install_pattern_in_each_real_agent_test_file():
    """Each of the four real-agent test files (Stories 6/7/8/9) must
    use `monkeypatch.setitem(sys.modules, "anthropic", ...)` to install
    a fake. This is the pattern the guard is designed to interoperate
    with — if any file diverges, the guard's per-test override breaks."""
    missing = []
    for test_path in REAL_AGENT_TEST_FILES:
        text = _read(test_path)
        if "monkeypatch.setitem(sys.modules" not in text or \
                '"anthropic"' not in text:
            missing.append(test_path.name)
    assert not missing, (
        f"These real-agent test files do not use the "
        f"`monkeypatch.setitem(sys.modules, 'anthropic', ...)` install "
        f"pattern: {missing}"
    )


def test_install_fake_anthropic_helper_in_each_real_agent_test_file():
    """Each real-agent test file must define a `_install_fake_anthropic`
    helper (the canonical Story 6/7/8/9 fixture pattern). The guard
    assumes this helper to override the sentinel."""
    missing = []
    for test_path in REAL_AGENT_TEST_FILES:
        text = _read(test_path)
        if "def _install_fake_anthropic" not in text:
            missing.append(test_path.name)
    assert not missing, (
        f"These real-agent test files do not define `_install_fake_anthropic`: "
        f"{missing}"
    )


def test_no_real_anthropic_attribute_access_in_tests():
    """Test files must NOT access `anthropic.Anthropic(...)` directly via
    a top-level `import anthropic` — they only ever reach the SDK via
    `sys.modules['anthropic']` lookup. This pins the absence of bypass
    paths around the seam."""
    bad = []
    for test_path in sorted(TESTS_DIR.glob("test_*.py")):
        text = _read(test_path)
        # Strip docstrings (very rough — only catches triple-quoted
        # strings spanning multiple lines).
        stripped = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
        stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
        # We're looking for `anthropic.Anthropic` references AFTER a
        # bare `import anthropic` — but since the previous test pinned
        # zero top-level imports, this is a belt-and-braces check.
        if re.search(r"^[ \t]*import\s+anthropic\s*$",
                     stripped, flags=re.MULTILINE):
            bad.append(test_path.name)
    assert not bad, (
        f"Test files {bad} have a bare top-level `import anthropic` — "
        f"they would bypass the sentinel."
    )


# ---------------------------------------------------------------------------
# Category C — Suite-green meta-verification
# ---------------------------------------------------------------------------


def test_suite_has_at_least_baseline_test_count():
    """The suite has crossed the 2000-test mark; this is a floor pin.
    Drift below this (e.g. mass test deletion) trips this assertion."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         str(TESTS_DIR)],
        cwd=str(PACKAGE_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"pytest --collect-only failed: rc={proc.returncode}\n"
        f"stdout: {proc.stdout[-2000:]}\n"
        f"stderr: {proc.stderr[-500:]}"
    )
    # Parse the "N tests collected" line from output tail.
    match = re.search(r"(\d+)\s+tests?\s+collected", proc.stdout)
    assert match, (
        f"could not parse test count from pytest --collect-only output:\n"
        f"{proc.stdout[-2000:]}"
    )
    test_count = int(match.group(1))
    assert test_count >= 2000, (
        f"Suite has only {test_count} tests; Story 15 expects ≥2000 "
        f"(baseline at end of Iter 1 was 1680+; Iter 2 added several "
        f"hundred more)."
    )


def test_no_xfail_or_skip_in_real_agent_test_files():
    """Stories 6/7/8/9 test files must not contain `@pytest.mark.xfail`,
    `@pytest.mark.skip`, `pytest.skip(...)`, or `pytest.xfail(...)`.
    These would mask failures the gate is supposed to catch."""
    offenders = []
    forbidden_patterns = (
        re.compile(r"^\s*@pytest\.mark\.xfail", re.MULTILINE),
        re.compile(r"^\s*@pytest\.mark\.skip\b", re.MULTILINE),
        re.compile(r"\bpytest\.skip\s*\(", re.MULTILINE),
        re.compile(r"\bpytest\.xfail\s*\(", re.MULTILINE),
    )
    for test_path in REAL_AGENT_TEST_FILES:
        text = _read(test_path)
        for pat in forbidden_patterns:
            if pat.search(text):
                offenders.append(
                    f"{test_path.name} matches {pat.pattern!r}"
                )
    assert not offenders, (
        f"Real-agent test files have skip/xfail markers (these mask "
        f"failures the gate must catch):\n  " + "\n  ".join(offenders)
    )


def test_real_agent_tests_cover_all_four_defaults():
    """Exactly one Story-X test file exists per real-agent default —
    decompose / test_writer / coder / reviewer. The four files are the
    contract surface for Iter 2's real-agent work."""
    for path in REAL_AGENT_TEST_FILES:
        assert path.is_file(), (
            f"Missing real-agent test file: {path.name} — Stories "
            f"6/7/8/9 each contribute one of these files."
        )


def test_each_real_agent_file_has_more_than_one_test():
    """Each real-agent test file must collect more than 1 test — a file
    with 0-1 tests indicates a story's contract was barely pinned."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         *[str(p) for p in REAL_AGENT_TEST_FILES]],
        cwd=str(PACKAGE_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-500:]
    counts = {}
    for line in proc.stdout.splitlines():
        for path in REAL_AGENT_TEST_FILES:
            if line.startswith(f"tests/{path.name}::"):
                counts[path.name] = counts.get(path.name, 0) + 1
    for path in REAL_AGENT_TEST_FILES:
        assert counts.get(path.name, 0) > 1, (
            f"{path.name} collected {counts.get(path.name, 0)} tests; "
            f"each Story 6/7/8/9 file should pin >1 test."
        )


# ---------------------------------------------------------------------------
# Category D — Failure-path verification (meta — count refs)
# ---------------------------------------------------------------------------


def test_missing_api_key_path_covered_for_each_real_agent_default():
    """Each of the four real-agent test files must reference
    `MissingAPIKeyError` at least once — pinning that the missing-key
    path is covered for each default."""
    missing = []
    for test_path in REAL_AGENT_TEST_FILES:
        text = _read(test_path)
        if "MissingAPIKeyError" not in text:
            missing.append(test_path.name)
    assert not missing, (
        f"These real-agent test files do not reference "
        f"`MissingAPIKeyError`: {missing}. Story 15 requires the "
        f"missing-key path be covered for each default."
    )


def test_sdk_exception_wrap_covered_for_each_real_agent_default():
    """Each real-agent test file must reference its role-specific
    `*AgentError` class — pinning that the SDK-exception-wrap path is
    covered for each default."""
    expected_errors = {
        "test_decompose_real_spawn.py": "DecomposeAgentError",
        "test_execute_real_test_writer.py": "TestWriterAgentError",
        "test_execute_real_coder.py": "CoderAgentError",
        "test_execute_real_reviewer.py": "ReviewerAgentError",
    }
    failures = []
    for test_path in REAL_AGENT_TEST_FILES:
        text = _read(test_path)
        expected = expected_errors[test_path.name]
        if expected not in text:
            failures.append(f"{test_path.name} missing {expected}")
    assert not failures, (
        f"Real-agent test files missing role-specific *AgentError "
        f"references: {failures}"
    )


def test_sdk_exception_wrap_test_names_present_in_each_file():
    """Each real-agent test file must define at least one test whose
    name contains `sdk` or `wrap` or `propagat` — the SDK-exception
    wrapping path must be exercised by a NAMED test, not just imported
    symbols."""
    missing = []
    for test_path in REAL_AGENT_TEST_FILES:
        text = _read(test_path)
        if not re.search(
            r"def test_[a-zA-Z0-9_]*(sdk|wrap|propagat)[a-zA-Z0-9_]*\s*\(",
            text,
        ):
            missing.append(test_path.name)
    assert not missing, (
        f"Real-agent test files missing an SDK-wrap/propagat named "
        f"test: {missing}"
    )


def test_parse_error_path_covered_for_json_roles():
    """The two JSON-shaped roles (decompose, reviewer) must reference
    `parse_agent_json` or a `*ParseError` in their test files —
    pinning that the typed parse-error path is covered. (Test writer
    and coder return raw strings and have no parse path.)"""
    json_role_files = (
        TESTS_DIR / "test_decompose_real_spawn.py",
        TESTS_DIR / "test_execute_real_reviewer.py",
    )
    missing = []
    for test_path in json_role_files:
        text = _read(test_path)
        if "parse_agent_json" not in text and "ParseError" not in text \
                and "parse_error" not in text and "parse error" not in text:
            missing.append(test_path.name)
    assert not missing, (
        f"JSON-role test files (decompose / reviewer) do not reference "
        f"`parse_agent_json` or a `*ParseError`: {missing}"
    )


def test_precedence_env_vars_referenced_in_each_real_agent_file():
    """Each real-agent test file must reference its
    `SM_<ROLE>_MAX_TOKENS` or `SM_<ROLE>_MODEL` env var — pinning that
    the model/max_tokens precedence path is covered for each default."""
    expected_env_substrings = {
        "test_decompose_real_spawn.py": "SM_DECOMPOSE",
        "test_execute_real_test_writer.py": "SM_TEST_WRITER",
        "test_execute_real_coder.py": "SM_CODER",
        "test_execute_real_reviewer.py": "SM_REVIEWER",
    }
    missing = []
    for test_path in REAL_AGENT_TEST_FILES:
        text = _read(test_path)
        substring = expected_env_substrings[test_path.name]
        if substring not in text:
            missing.append(f"{test_path.name} missing {substring}*")
    assert not missing, (
        f"Real-agent test files missing per-role env-var references "
        f"(precedence not pinned): {missing}"
    )


def test_failure_path_test_count_floor_per_file():
    """Each real-agent test file must contain at least 10 tests — a
    floor that ensures the four failure paths (missing-key, precedence,
    parse, wrap) each have multiple pins per default, not just one."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         *[str(p) for p in REAL_AGENT_TEST_FILES]],
        cwd=str(PACKAGE_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout[-2000:]
    counts = {}
    for line in proc.stdout.splitlines():
        for path in REAL_AGENT_TEST_FILES:
            if line.startswith(f"tests/{path.name}::"):
                counts[path.name] = counts.get(path.name, 0) + 1
    weak = [
        (name, n) for name, n in counts.items() if n < 10
    ]
    for path in REAL_AGENT_TEST_FILES:
        # also fail if not present at all
        if path.name not in counts:
            weak.append((path.name, 0))
    assert not weak, (
        f"Real-agent test files with fewer than 10 tests: {weak}. "
        f"Story 15 expects each default to pin missing-key, precedence, "
        f"parse, AND wrap paths — at least 10 tests per file."
    )


# ---------------------------------------------------------------------------
# Category E — CI / live-call defense behavioral pin
# ---------------------------------------------------------------------------


def _run_pytest_subprocess(args, extra_env=None):
    """Run pytest as a subprocess with `ANTHROPIC_API_KEY` removed from
    env. Returns the CompletedProcess. The subprocess inherits the
    parent env minus ANTHROPIC_API_KEY (plus any extra_env)."""
    import os
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=str(PACKAGE_DIR),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def test_decompose_real_spawn_passes_with_no_api_key_in_env():
    """Running `tests/test_decompose_real_spawn.py` with no
    `ANTHROPIC_API_KEY` in env must succeed (rc 0) — confirming the
    suite is self-contained and tests do not depend on a live key."""
    proc = _run_pytest_subprocess([
        "tests/test_decompose_real_spawn.py", "-q", "--tb=short",
        "--no-header",
    ])
    assert proc.returncode == 0, (
        f"test_decompose_real_spawn.py failed under no-key env "
        f"(rc={proc.returncode}):\n"
        f"--- STDOUT (tail) ---\n{proc.stdout[-2500:]}\n"
        f"--- STDERR (tail) ---\n{proc.stderr[-1000:]}"
    )


def test_execute_real_test_writer_passes_with_no_api_key_in_env():
    """Same gate for the test_writer real-default file."""
    proc = _run_pytest_subprocess([
        "tests/test_execute_real_test_writer.py", "-q", "--tb=short",
        "--no-header",
    ])
    assert proc.returncode == 0, (
        f"test_execute_real_test_writer.py failed under no-key env "
        f"(rc={proc.returncode}):\n"
        f"--- STDOUT (tail) ---\n{proc.stdout[-2500:]}\n"
        f"--- STDERR (tail) ---\n{proc.stderr[-1000:]}"
    )


def test_invoke_anthropic_seam_passes_with_no_api_key_in_env():
    """Same gate for the provider-seam file (Story 5). The seam itself
    must not depend on a live key — only on the injected fake."""
    proc = _run_pytest_subprocess([
        "tests/test_invoke_anthropic.py", "-q", "--tb=short",
        "--no-header",
    ])
    assert proc.returncode == 0, (
        f"test_invoke_anthropic.py failed under no-key env "
        f"(rc={proc.returncode}):\n"
        f"--- STDOUT (tail) ---\n{proc.stdout[-2500:]}\n"
        f"--- STDERR (tail) ---\n{proc.stderr[-1000:]}"
    )


def test_sentinel_active_in_subprocess_pytest_run():
    """Run a tiny inline pytest invocation in a subprocess (no-API-key
    env) that asserts the sentinel is in `sys.modules["anthropic"]`.
    Confirms the guard fires for OTHER pytest invocations, not just
    this in-process one."""
    proc = _run_pytest_subprocess([
        "tests/test_no_live_sdk_calls.py::"
        "test_conftest_installs_anthropic_sentinel_into_sys_modules",
        "-q", "--tb=short", "--no-header",
    ])
    assert proc.returncode == 0, (
        f"sentinel-installed self-check failed in subprocess "
        f"(rc={proc.returncode}):\n{proc.stdout[-2000:]}\n"
        f"{proc.stderr[-500:]}"
    )


# ---------------------------------------------------------------------------
# Category F — Sanity / interop pins
# ---------------------------------------------------------------------------


def test_sm_module_imports_without_loading_anthropic():
    """`import sm` must NOT trigger an `import anthropic` — Story 5's
    lazy-import contract. We verify by removing `anthropic` from
    `sys.modules` momentarily, importing sm, and checking it's still
    absent."""
    # Drop the sentinel.
    prev = sys.modules.pop("anthropic", None)
    try:
        # Force a fresh import of sm.
        if "sm" in sys.modules:
            del sys.modules["sm"]
        import sm  # noqa: F401
        assert "anthropic" not in sys.modules, (
            "Importing sm loaded `anthropic` into sys.modules — "
            "violates Story 5's lazy-import contract."
        )
    finally:
        # Restore.
        if prev is not None:
            sys.modules["anthropic"] = prev


def test_invoke_anthropic_seam_triggers_sentinel_without_fake():
    """When NO per-test fake is installed and `_invoke_anthropic` is
    called, the sentinel must fire — confirming the guard interacts
    with the seam's lazy import as designed."""
    # Make sure the sentinel is in place (no per-test override).
    assert "anthropic" in sys.modules
    sentinel_mod = sys.modules["anthropic"]
    # Sanity check: this is the sentinel, not a real fake.
    with pytest.raises(Exception):
        sentinel_mod.Anthropic(api_key="sk-test")

    # Now call the seam through sm — it should hit the sentinel.
    import sm
    with pytest.raises(Exception) as exc_info:
        sm._invoke_anthropic(
            messages=[{"role": "user", "content": "ping"}],
            model="claude-haiku-4-5",
            max_tokens=100,
            api_key="sk-test-not-real",
        )
    # Same kind of loud error.
    assert not isinstance(exc_info.value, (TypeError, AttributeError)), (
        f"_invoke_anthropic raised {type(exc_info.value).__name__} when "
        f"the sentinel was in place — expected the sentinel's loud "
        f"RuntimeError. Maybe the sentinel was overridden by a leaked "
        f"fake from a previous test."
    )


def test_sentinel_marker_attribute_present():
    """The sentinel module should carry a marker attribute (e.g.
    `__sm_sentinel__`) so tests CAN distinguish it from a fake. The
    Coder's conftest implementation should expose such a marker."""
    mod = sys.modules.get("anthropic")
    assert mod is not None
    # At least one of these markers should be present — either an
    # explicit `__sm_sentinel__` flag, OR the class name should hint
    # at sentinel/guard role.
    has_marker_attr = getattr(mod, "__sm_sentinel__", False)
    cls = getattr(mod, "Anthropic", None)
    cls_name_hint = bool(cls) and any(
        keyword in cls.__name__.lower()
        for keyword in ("sentinel", "guard", "refuse")
    )
    # Either marker is acceptable.
    assert has_marker_attr or cls_name_hint, (
        f"sys.modules['anthropic'] should carry a marker attribute "
        f"(e.g. `__sm_sentinel__ = True`) or the Anthropic class name "
        f"should hint at sentinel/guard. Got module={mod!r} "
        f"Anthropic={cls!r}"
    )
