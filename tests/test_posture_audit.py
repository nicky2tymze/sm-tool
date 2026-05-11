"""Story 21 — Single-user / no-auth / no-network posture audit.

Static verification of sm.py source: no network sockets, no auth libraries,
no third-party imports, and no environment variable reads beyond the
established suite-session convention (`SM_LOG_PATH`, Assumption 7).

These tests are read-only: they ingest `sm.py` as text and apply regex /
substring checks. They run in milliseconds and require no fixtures.
"""

from __future__ import annotations

import pathlib
import re
import sys

THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Source loader
# ---------------------------------------------------------------------------

def _source() -> str:
    """Read sm.py once per call. Small file; no need to cache."""
    return SM_PATH.read_text(encoding="utf-8")


def _import_lines(src: str) -> list[str]:
    """Return only the lines that start (after leading whitespace) with
    `import ` or `from `. Comments and string literals are excluded.
    """
    out: list[str] = []
    for raw in src.splitlines():
        stripped = raw.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("import ") or stripped.startswith("from "):
            out.append(stripped)
    return out


def _has_import(src: str, module: str) -> bool:
    """True iff sm.py imports `module` at top-level or via `from module import ...`.

    Matches:
        import <module>
        import <module> as ...
        import <module>.<sub> ...
        from <module> import ...
        from <module>.<sub> import ...
    """
    pattern = re.compile(
        rf"^\s*(?:import\s+{re.escape(module)}(?:\.|\s|,|$)"
        rf"|from\s+{re.escape(module)}(?:\.|\s)+import\s)",
        re.MULTILINE,
    )
    return bool(pattern.search(src))


# ---------------------------------------------------------------------------
# Sanity: source is readable and non-trivial
# ---------------------------------------------------------------------------

def test_sm_py_exists():
    assert SM_PATH.is_file(), f"sm.py not found at {SM_PATH}"


def test_sm_py_is_not_empty():
    assert len(_source()) > 100


def test_import_extractor_finds_imports():
    """Guard: if _import_lines returns nothing, every audit below is vacuous."""
    lines = _import_lines(_source())
    assert len(lines) >= 3, f"expected at least 3 imports, found {len(lines)}"


# ---------------------------------------------------------------------------
# Forbidden network imports (8)
# ---------------------------------------------------------------------------

def test_no_socket_import():
    assert not _has_import(_source(), "socket"), "sm.py must not import socket"


def test_no_urllib_import():
    assert not _has_import(_source(), "urllib"), "sm.py must not import urllib"


def test_no_urllib3_import():
    assert not _has_import(_source(), "urllib3"), "sm.py must not import urllib3"


def test_no_http_import():
    assert not _has_import(_source(), "http"), "sm.py must not import http"


def test_no_https_import():
    # `https` isn't stdlib but check anyway in case of a third-party shim.
    assert not _has_import(_source(), "https"), "sm.py must not import https"


def test_no_ssl_import():
    assert not _has_import(_source(), "ssl"), "sm.py must not import ssl"


def test_no_smtplib_import():
    assert not _has_import(_source(), "smtplib"), "sm.py must not import smtplib"


def test_no_ftplib_import():
    assert not _has_import(_source(), "ftplib"), "sm.py must not import ftplib"


def test_no_imaplib_import():
    assert not _has_import(_source(), "imaplib"), "sm.py must not import imaplib"


def test_no_telnetlib_import():
    assert not _has_import(_source(), "telnetlib"), "sm.py must not import telnetlib"


def test_no_poplib_import():
    assert not _has_import(_source(), "poplib"), "sm.py must not import poplib"


def test_no_xmlrpc_import():
    assert not _has_import(_source(), "xmlrpc"), "sm.py must not import xmlrpc"


def test_no_asyncio_import():
    # asyncio.streams / asyncio.open_connection enable network IO.
    assert not _has_import(_source(), "asyncio"), "sm.py must not import asyncio"


def test_no_select_module_import():
    # `select` is the low-level IO multiplexer (sockets/pipes).
    assert not _has_import(_source(), "select"), "sm.py must not import the select module"


# ---------------------------------------------------------------------------
# Forbidden third-party network/HTTP libs (3)
# ---------------------------------------------------------------------------

def test_no_requests_import():
    assert not _has_import(_source(), "requests"), "sm.py must not import requests"


def test_no_httpx_import():
    assert not _has_import(_source(), "httpx"), "sm.py must not import httpx"


def test_no_aiohttp_import():
    assert not _has_import(_source(), "aiohttp"), "sm.py must not import aiohttp"


# ---------------------------------------------------------------------------
# Forbidden auth libraries (5)
# ---------------------------------------------------------------------------
# Note: `hashlib` IS allowed — Story 15 / role_spec_hash uses SHA-256
# over role-spec content. That is not an auth credential.

def test_no_jwt_import():
    assert not _has_import(_source(), "jwt"), "sm.py must not import jwt"


def test_no_passlib_import():
    assert not _has_import(_source(), "passlib"), "sm.py must not import passlib"


def test_no_bcrypt_import():
    assert not _has_import(_source(), "bcrypt"), "sm.py must not import bcrypt"


def test_no_oauth_imports():
    src = _source()
    for mod in ("oauth", "oauthlib", "requests_oauthlib", "authlib"):
        assert not _has_import(src, mod), f"sm.py must not import {mod}"


def test_no_keyring_import():
    assert not _has_import(_source(), "keyring"), "sm.py must not import keyring"


def test_no_secrets_module_used_for_credentials():
    # `secrets` is fine for generating tokens — but sm.py shouldn't need it.
    # If it appears, that signals identity/credential work creeping in.
    assert not _has_import(_source(), "secrets"), (
        "sm.py must not import secrets (no credentials issued by this tool)"
    )


def test_hashlib_is_allowed_and_present():
    """Positive control — confirms hashlib is the SHA-256 lane (role_spec_hash),
    not blocked by the audit.
    """
    assert _has_import(_source(), "hashlib"), (
        "hashlib should still be imported (used for role_spec_hash per Story 15)"
    )


# ---------------------------------------------------------------------------
# Env var read audit (4)
# ---------------------------------------------------------------------------

_ENV_READ_RE = re.compile(
    r"""os\.environ(?:
        \.get\(\s*["']([^"']+)["']
        |\[\s*["']([^"']+)["']\s*\]
    )""",
    re.VERBOSE,
)


def _env_keys_read(src: str) -> set[str]:
    keys: set[str] = set()
    for m in _ENV_READ_RE.finditer(src):
        keys.add(m.group(1) or m.group(2))
    # Also catch os.getenv("KEY")
    for m in re.finditer(r"""os\.getenv\(\s*["']([^"']+)["']""", src):
        keys.add(m.group(1))
    return keys


_ALLOWED_ENV_VAR_READS = {
    # Story 9 — hermetic subprocess test log path. Established Iter 1.
    "SM_LOG_PATH",
    # Iter 2 Story 2 — single-source-of-truth resolver for the Anthropic
    # API key. Posture evolved from "no env reads beyond SM_LOG_PATH" to
    # "the API key joins via the explicit `resolve_api_key()` helper".
    # Same cascade pattern Story 1 used to update the runtime-deps audit.
    "ANTHROPIC_API_KEY",
}


def test_only_sm_log_path_env_var_read():
    """Every os.environ.get / os.environ[...] / os.getenv must target one
    of the explicitly allowed env vars.

    Iter 1 pinned `{SM_LOG_PATH}` only; Iter 2 Story 2 expands the
    allowlist to include `ANTHROPIC_API_KEY` (read once, inside
    `resolve_api_key`). Any further additions require a deliberate
    posture review — keep the allowlist tight.
    """
    keys = _env_keys_read(_source())
    assert keys == _ALLOWED_ENV_VAR_READS, (
        f"sm.py reads unexpected env vars: "
        f"{sorted(keys - _ALLOWED_ENV_VAR_READS)}"
    )


def test_sm_log_path_is_actually_read():
    """Positive control — Story 9 established SM_LOG_PATH for hermetic
    subprocess testing. Confirm it's still wired up.
    """
    assert "SM_LOG_PATH" in _env_keys_read(_source())


def test_no_user_or_home_env_reads():
    """No USER / USERNAME / HOME / HOMEPATH style identity reads."""
    src = _source()
    for key in ("USER", "USERNAME", "LOGNAME", "HOME", "HOMEPATH", "USERPROFILE"):
        assert key not in _env_keys_read(src), (
            f"sm.py reads {key} from environment — not allowed under "
            f"no-auth posture"
        )


def test_no_credential_env_reads():
    """Common auth/credential env-var names that must never be read."""
    src = _source()
    forbidden = (
        "TOKEN", "API_KEY", "API_TOKEN", "AUTH_TOKEN", "SECRET",
        "PASSWORD", "PASSWD", "ACCESS_KEY", "SECRET_KEY",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN", "GH_TOKEN",
    )
    keys = _env_keys_read(src)
    for key in forbidden:
        assert key not in keys, (
            f"sm.py reads {key} from environment — credentials not permitted"
        )


def test_no_raw_environ_iteration():
    """No `for k in os.environ:` or `os.environ.items()` — that would let
    every env var influence behavior.
    """
    src = _source()
    assert "os.environ.items" not in src
    assert "os.environ.keys" not in src
    assert "os.environ.values" not in src
    assert not re.search(r"for\s+\w+\s+in\s+os\.environ\b", src)


# ---------------------------------------------------------------------------
# Subprocess / network process audit (3)
# ---------------------------------------------------------------------------

def test_no_subprocess_import():
    """sm.py must not shell out — that would route around the no-network
    posture (curl, ssh, etc.). Test infrastructure uses subprocess but that
    lives outside sm.py.
    """
    assert not _has_import(_source(), "subprocess"), (
        "sm.py must not import subprocess (no shell-out under no-network posture)"
    )


def test_no_os_system_call():
    src = _source()
    assert "os.system(" not in src, "sm.py must not call os.system"


def test_no_os_popen_call():
    src = _source()
    assert "os.popen(" not in src, "sm.py must not call os.popen"


def test_no_os_exec_calls():
    """os.execv / os.execve / os.spawn* — any process-launch primitive."""
    src = _source()
    for fn in ("os.execv", "os.execve", "os.execvp", "os.spawnv", "os.spawnl"):
        assert fn not in src, f"sm.py must not call {fn}"


# ---------------------------------------------------------------------------
# Stdlib-only audit (3)
# ---------------------------------------------------------------------------

# All modules sm.py is permitted to import. Stdlib only. No PyPI deps.
_ALLOWED_TOP_LEVEL_MODULES = {
    # __future__ machinery
    "__future__",
    # Core stdlib used by sm.py today (see imports at top of sm.py)
    "copy", "datetime", "hashlib", "json", "uuid", "pathlib", "typing",
    # Inline imports inside main() / __main__ guard
    "os", "sys",
    # Permitted-but-may-not-be-present (defensive — future use without
    # tripping this audit). Add only stdlib modules that pose no posture risk.
    "re", "io", "collections", "itertools", "functools", "string",
    "enum", "dataclasses", "textwrap", "argparse",
}


_TOP_LEVEL_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+([\w\.]+)|from\s+([\w\.]+)\s+import\s)",
    re.MULTILINE,
)


def _imported_top_level_modules(src: str) -> set[str]:
    mods: set[str] = set()
    for m in _TOP_LEVEL_IMPORT_RE.finditer(src):
        full = m.group(1) or m.group(2)
        if not full:
            continue
        mods.add(full.split(".", 1)[0])
    return mods


def test_all_imports_are_stdlib_only():
    """Every imported top-level module must be in the allowed stdlib set.
    A new import is fine — but it must be added to `_ALLOWED_TOP_LEVEL_MODULES`
    deliberately, which forces a posture review.
    """
    mods = _imported_top_level_modules(_source())
    unexpected = mods - _ALLOWED_TOP_LEVEL_MODULES
    assert not unexpected, (
        f"sm.py imports modules outside the allowed stdlib set: "
        f"{sorted(unexpected)}. If this is intentional, add them to "
        f"_ALLOWED_TOP_LEVEL_MODULES in this test file after a posture review."
    )


def test_no_pip_or_setuptools_runtime_use():
    """sm.py must not invoke pip or setuptools at runtime."""
    src = _source()
    assert not _has_import(src, "pip")
    assert not _has_import(src, "setuptools")
    assert not _has_import(src, "pkg_resources")


_ALLOWED_RUNTIME_DEPS = frozenset({"anthropic"})


def test_pyproject_declares_only_allowed_runtime_dependencies():
    """Iter 2 retires the strict stdlib-only posture and explicitly allows
    the `anthropic` SDK as a runtime dep (Iter 2 Story 1). The audit still
    rejects any OTHER runtime dependency — only the explicit allowlist
    above is permitted. Iter 1's broader stdlib-only invariant is now
    scoped to the Python standard library + `anthropic`.
    """
    pyproject = PACKAGE_DIR / "pyproject.toml"
    assert pyproject.is_file()
    text = pyproject.read_text(encoding="utf-8")
    m = re.search(
        r"^\s*dependencies\s*=\s*\[([^\]]*)\]",
        text,
        re.MULTILINE,
    )
    if m is None:
        # No dependencies block at all — fine (no deps to audit).
        return
    inside = m.group(1).strip()
    # Strip comments and split on commas; each entry should be a quoted
    # string naming a package, optionally with version/extras spec.
    non_comment = re.sub(r"#[^\n]*", "", inside)
    raw_entries = [e.strip().strip(",").strip() for e in non_comment.split(",")]
    entries = [e for e in raw_entries if e]
    for entry in entries:
        # Strip the surrounding quotes and any version/extras spec to get
        # the bare package name.
        unquoted = entry.strip("'\"")
        # Bare package name is everything before the first comparator,
        # bracket (extras), or whitespace.
        pkg_name = re.split(r"[<>=!~\[\s]", unquoted, maxsplit=1)[0].strip()
        assert pkg_name in _ALLOWED_RUNTIME_DEPS, (
            f"pyproject.toml declares runtime dependency {pkg_name!r}; "
            f"only {sorted(_ALLOWED_RUNTIME_DEPS)} are allowed in Iter 2. "
            f"Add to _ALLOWED_RUNTIME_DEPS only after a deliberate "
            f"posture review."
        )
