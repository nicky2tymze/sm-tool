"""sm-tool — scrum-master pipeline (skeleton).

Iteration 1 lives here. The skeleton defines the module shape and the
LOG_PATH constant; behavior lands in subsequent stories.

Stdlib only; Python 3.10+.
"""

from __future__ import annotations

import copy as _copy
import datetime as _dt
import hashlib as _hashlib
import json
import uuid
from pathlib import Path
from typing import Callable, Iterator, Optional

_CANONICAL_ROLES = ("sm_agent", "test_writer", "coder", "reviewer")

LOG_PATH: Path = Path(__file__).resolve().parent / "log.jsonl"

_RESERVED_KEYS = ("id", "type", "timestamp")

_TERMINAL_STATES = frozenset({"accepted", "rejected", "force_closed"})
_VALID_TRANSITIONS: dict = {
    "planned": frozenset({"in_progress", "force_closed"}),
    "in_progress": frozenset({"in_review", "force_closed"}),
    "in_review": frozenset({"accepted", "rejected", "force_closed"}),
    "accepted": frozenset(),
    "rejected": frozenset(),
    "force_closed": frozenset(),
}

__all__ = [
    "LOG_PATH",
    "build_entry",
    "read_entries",
    "derive_state",
    "ingest",
    "IngestJSONError",
    "IngestShapeError",
    "IngestDuplicateError",
    "IngestActiveError",
    "resolve_role_spec",
    "RoleSpecNotFoundError",
    "decompose",
    "DecomposeAgentError",
    "DecomposeOutputParseError",
    "DecomposeOutputShapeError",
    "DecomposeUnknownRequirementError",
    "TestWriterAgentError",
    "CoderAgentError",
    "ReviewerAgentError",
    "parse_agent_json",
    "sprint_cut",
    "SprintCutError",
    "SprintCutLockedError",
    "transition_story",
    "StoryTransitionError",
    "record_review",
    "ReviewError",
    "AcceptGateError",
    "status",
    "aggregate_requirements",
    "AggregateError",
    "close_iteration",
    "IterationCloseError",
    "EXIT_CLOSE",
    "force_close",
    "ForceCloseError",
    "execute",
    "ExecuteError",
    "resolve_api_key",
    "MissingAPIKeyError",
    "EXIT_AGENT_ERROR",
    "resolve_model",
    "resolve_max_tokens",
    "ConfigError",
]


# ---------------------------------------------------------------------------
# Story 6 — typed ingest errors. Each subclass narrows ValueError so the
# existing `pytest.raises(ValueError)` callers keep working, while the CLI
# can map the class to a distinct exit code (see `_cli_main`).
# ---------------------------------------------------------------------------

class IngestJSONError(ValueError):
    """Handoff file is not valid JSON (malformed / empty)."""


class IngestShapeError(ValueError):
    """Handoff JSON is well-formed but does not match the required shape
    (missing/wrong-typed top-level fields, bad/duplicate requirements)."""


class IngestDuplicateError(ValueError):
    """The handoff's iteration_id matches a prior `iteration_open` entry —
    open OR closed. Distinct from `IngestActiveError`, which fires only
    while another iteration is currently open."""


class IngestActiveError(ValueError):
    """An iteration is currently open; cannot ingest a new handoff until
    it is closed."""


class RoleSpecNotFoundError(FileNotFoundError):
    """Raised when a canonical role-spec file does not exist on disk at the
    resolved path. Subclasses FileNotFoundError so existing
    `except FileNotFoundError` callers keep working."""


# ---------------------------------------------------------------------------
# Story 9 — typed decompose errors. Both narrow ValueError so existing
# `except ValueError` callers keep working, while distinguishing the parse
# failure mode (agent output isn't valid JSON) from the shape failure mode
# (JSON parsed but doesn't match the required schema).
# ---------------------------------------------------------------------------

class DecomposeAgentError(ValueError):
    """Raised when the decompose agent spawn fails or returns malformed
    output. Iter 2 Story 4 rebased this from RuntimeError to ValueError so
    the four per-role agent-error classes share a uniform hierarchy and
    existing `except ValueError` handlers keep working. `parse_agent_json`
    raises this class on json.loads failures for role="decompose"."""


class TestWriterAgentError(ValueError):
    """Raised when the test_writer agent spawn fails or returns malformed
    output. Iter 2 Story 4: typed parse error for `parse_agent_json` when
    called with role="test_writer"."""


class CoderAgentError(ValueError):
    """Raised when the coder agent spawn fails or returns malformed
    output. Iter 2 Story 4: typed parse error for `parse_agent_json` when
    called with role="coder"."""


class ReviewerAgentError(ValueError):
    """Raised when the reviewer agent spawn fails or returns malformed
    output. Iter 2 Story 4: typed parse error for `parse_agent_json` when
    called with role="reviewer"."""


class DecomposeOutputParseError(DecomposeAgentError):
    """The agent returned output that is not valid JSON. Iter 2 Story 4
    rebased the parent class from ValueError to DecomposeAgentError so
    parse failures route uniformly through the shared agent-error
    hierarchy; ValueError catch paths still work because
    DecomposeAgentError -> ValueError."""


class DecomposeOutputShapeError(ValueError):
    """The agent's JSON parsed cleanly, but does not match the required
    story-backlog schema (missing keys, wrong types, bad sizes, non-1..N
    sequences, etc.)."""


class DecomposeUnknownRequirementError(ValueError):
    """A story's `requirement_ids` references an id that does not appear
    in the active iteration's handoff requirements list. Distinct from
    `DecomposeOutputShapeError` — both subclass ValueError so existing
    `except ValueError` callers keep working, but callers can branch on
    the exact class for cross-reference vs shape failures."""


# ---------------------------------------------------------------------------
# Iter 2 Story 4 — JSON ask-and-parse helper with typed parse errors.
#
# Single-source-of-truth: every spawn default routes agent-response JSON
# through this helper. `json.loads` of agent text happens exactly once
# (here); on parse failure the helper raises the role's typed error class
# carrying the role name and a truncated raw snippet (≤200 chars).
# ---------------------------------------------------------------------------

_PARSE_ROLE_TO_ERROR = {
    "decompose": DecomposeAgentError,
    "test_writer": TestWriterAgentError,
    "coder": CoderAgentError,
    "reviewer": ReviewerAgentError,
}
_VALID_PARSE_ROLES = frozenset(_PARSE_ROLE_TO_ERROR.keys())
_PARSE_SNIPPET_LIMIT = 200


def parse_agent_json(raw, role):
    """Parse an agent response string and return the resulting dict or list.

    On `json.JSONDecodeError`, raises the role's typed parse-error class
    (a `ValueError` subclass) carrying the role name, the underlying
    decoder message, and a snippet of `raw` truncated to the first 200
    characters.

    Args:
        raw: The agent's raw response text. Must be a `str`.
        role: One of "decompose", "test_writer", "coder", "reviewer".

    Returns:
        The parsed JSON object (dict or list).

    Raises:
        TypeError: if `raw` is not a string.
        ValueError: if `role` is not one of the four canonical roles
            (this includes non-string `role` values).
        DecomposeAgentError / TestWriterAgentError / CoderAgentError /
        ReviewerAgentError: if `raw` is not valid JSON.
    """
    # Type guard on raw — the helper is documented as a string helper.
    if not isinstance(raw, str):
        raise TypeError(
            f"raw must be a string, got {type(raw).__name__}"
        )
    # Role argument validation. Non-string and unknown-string both fail
    # as ValueError; the message enumerates the four valid roles so the
    # operator can self-correct.
    if not isinstance(role, str) or role not in _VALID_PARSE_ROLES:
        valid = sorted(_VALID_PARSE_ROLES)
        raise ValueError(
            f"unknown role {role!r}; valid roles are "
            f"{valid!r} (one of: decompose, test_writer, coder, reviewer)"
        )

    err_class = _PARSE_ROLE_TO_ERROR[role]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        snippet = raw[:_PARSE_SNIPPET_LIMIT]
        raise err_class(
            f"{role} agent returned unparseable JSON: {e}; "
            f"raw[:{_PARSE_SNIPPET_LIMIT}]={snippet!r}"
        ) from e


# ---------------------------------------------------------------------------
# Story 11 — typed sprint_cut error. Subclasses ValueError so existing
# `except ValueError` callers keep working, while the CLI maps the class to
# a distinct exit code (see `_cli_main`).
# ---------------------------------------------------------------------------

class SprintCutError(ValueError):
    """Raised when sprint_cut command fails: no active iteration, no story
    backlog yet, or N out of range (zero, negative, or > len(backlog))."""


# ---------------------------------------------------------------------------
# Story 12 — sprint-cut re-run lock once any in-sprint story leaves planned.
# Subclasses SprintCutError so existing `except SprintCutError` callers (and
# the CLI handler that maps it to EXIT_SPRINT_CUT) keep catching it, while
# allowing branch-on-class for "this re-cut is locked, not just any failure".
# ---------------------------------------------------------------------------

class SprintCutLockedError(SprintCutError):
    """Raised when re-cut is attempted after any in-sprint story (per the
    LATEST prior sprint_cut entry's in_sprint_story_ids) has left the
    `planned` state. Lock is replay-derived — no separate flag persisted.
    Operator must close or force-close the iteration to proceed."""


# ---------------------------------------------------------------------------
# Story 13 — per-story lifecycle state machine writer. The graph used by
# `transition_story` is intentionally narrower than `_VALID_TRANSITIONS`
# (which `derive_state` uses): Story 13 does not call `force_closed` — that
# transition is Story 19's lane.
# ---------------------------------------------------------------------------

class StoryTransitionError(ValueError):
    """Raised when transition_story rejects a transition: no active
    iteration, no active sprint, story not in sprint, terminal-already,
    invalid state name, or illegal lifecycle transition. Subclasses
    ValueError so existing `except ValueError` callers keep working."""


# ---------------------------------------------------------------------------
# Story 15 — reviewer-approval entry + accept gate. ReviewError covers
# record_review's semantic failures (whitespace test_result, etc.). It
# subclasses ValueError so existing `except ValueError` callers keep working.
# AcceptGateError narrows StoryTransitionError so existing transition-error
# catches keep matching it, while allowing branch-on-class for the specific
# "accept fired without a valid prior reviewer_approval" failure mode.
# ---------------------------------------------------------------------------

class ReviewError(ValueError):
    """Raised when record_review rejects its arguments on semantic grounds
    (empty / whitespace-only test_result, etc.). Type errors stay TypeError;
    only value-shaped failures route here."""


class AcceptGateError(StoryTransitionError):
    """Raised when transition_story(... 'accepted') fires without a valid
    prior reviewer_approval entry for the same story_id. 'Valid' means the
    LATEST reviewer_approval for that story_id has approved=True AND a
    test_result that is non-empty after strip. Subclasses
    StoryTransitionError so the CLI's existing handler maps it to
    EXIT_TRANSITION (9) automatically."""


# ---------------------------------------------------------------------------
# Story 17 — typed aggregation error. Subclasses ValueError so existing
# `except ValueError` callers keep working. Raised by aggregate_requirements
# on two defense-in-depth conditions: no active iteration, and orphan
# requirement_ids (a requirement in the iteration with no story rolling up).
# ---------------------------------------------------------------------------

class AggregateError(ValueError):
    """Raised when aggregate_requirements cannot produce a result: no active
    iteration, or one-or-more orphan requirement_ids (requirements declared
    on the iteration with no story rolling up to them). Subclasses
    ValueError so existing `except ValueError` callers keep working."""


# ---------------------------------------------------------------------------
# Story 18 — typed iteration-close error. Subclasses ValueError so existing
# `except ValueError` callers keep working, while the CLI maps the class to
# a distinct exit code (EXIT_CLOSE = 11). Raised by close_iteration on every
# validation failure (no active iteration, no backlog, no sprint_cut,
# non-terminal in-sprint stories).
# ---------------------------------------------------------------------------

class IterationCloseError(ValueError):
    """Raised when close_iteration cannot close the active iteration: no
    active iteration, no story backlog, no sprint_cut, or one-or-more
    in-sprint stories still in a non-terminal state. Subclasses ValueError
    so existing `except ValueError` callers keep working."""


# ---------------------------------------------------------------------------
# Story 19 — typed force-close error. Subclasses ValueError so existing
# `except ValueError` callers keep working, while the CLI maps the class to
# EXIT_CLOSE (force-close is a close variant). Raised by force_close on
# every validation failure (empty/whitespace reason, no active iteration,
# no backlog, no sprint_cut).
# ---------------------------------------------------------------------------

class ForceCloseError(ValueError):
    """Raised when force_close cannot proceed: empty/whitespace-only reason,
    no active iteration, no story backlog, or no sprint_cut. Subclasses
    ValueError so existing `except ValueError` callers keep working."""


# ---------------------------------------------------------------------------
# Story 23 — typed execute error. Subclasses ValueError so existing
# `except ValueError` callers keep working. Raised by `execute` on every
# state-validation failure (no active iteration, no sprint_cut, story not in
# sprint, current state not in {planned, in_progress}). Type errors stay
# TypeError; the NotImplementedError default-spawn case stays a stdlib
# NotImplementedError so the CLI maps it via the catch-all.
# ---------------------------------------------------------------------------

class ExecuteError(ValueError):
    """Raised when execute pipeline cannot proceed."""


# ---------------------------------------------------------------------------
# Iter 2 Story 2 — typed MissingAPIKeyError. Subclasses ValueError so existing
# `except ValueError` callers keep working. Raised by `resolve_api_key()` when
# ANTHROPIC_API_KEY is unset, empty, or whitespace-only. The SDK is NOT
# imported on this failure path — the resolver is a pure stdlib helper.
# ---------------------------------------------------------------------------

class MissingAPIKeyError(ValueError):
    """Raised when ANTHROPIC_API_KEY is unset or empty.

    The CLI dispatcher catches this at the top level, prints the
    single-line message verbatim to stderr (no traceback), and exits with
    EXIT_AGENT_ERROR (12). Subclasses ValueError so existing handlers in
    the codebase still match.
    """


def resolve_api_key() -> str:
    """Return the ANTHROPIC_API_KEY env var value, or raise MissingAPIKeyError.

    Single source of truth for API-key reads across every real-agent spawn
    path (decompose, test_writer, coder, reviewer). Reads `os.environ` on
    every call — values are not cached, so the resolver honors mid-process
    env mutations.

    Returns the env var VERBATIM on success (no whitespace stripping — a
    leading/trailing space is part of the value the operator chose).

    Raises:
        MissingAPIKeyError: env var is unset, empty string, or
            whitespace-only. The message names the env var and points at
            the remediation step (single line, no traceback when the CLI
            handles it). The SDK is NOT imported on this path.
    """
    import os as _os  # local import keeps the failure path stdlib-only
    key = _os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or not key.strip():
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set; export it before running this "
            "command (see https://console.anthropic.com/ for keys)"
        )
    return key


# ---------------------------------------------------------------------------
# Iter 2 Story 3 — model + max_tokens resolution with precedence.
#
# `resolve_model(role)` returns the SDK model identifier and
# `resolve_max_tokens(role)` returns the max_tokens cap, each with the
# same three-level precedence chain:
#
#     per-spawn env var  >  global env var  >  hard-coded default
#
# Empty / whitespace-only env var values fall through to the next level
# (operator typo for "unset", not a parse error). Invalid integer values
# for any `*_MAX_TOKENS` env var raise the typed `ConfigError` BEFORE
# any SDK call. Negative caps are rejected; zero is allowed.
#
# Single-source-of-truth: each model / max_tokens env var name appears
# at most ONCE in this file (inside the matching resolver). A grep audit
# in `tests/test_resolve_model.py` pins this; same posture as Story 2's
# `resolve_api_key`. Stdlib only — no SDK import on this path.
# ---------------------------------------------------------------------------

# The exact Anthropic SDK identifier for Claude Haiku 4.5 (ASSUMPTION 2
# of Iter 2 Story 3). Pinned in a single module-level constant so the
# default-path return and the grep audit observe the same string.
_HAIKU_4_5_MODEL: str = "claude-haiku-4-5-20251001"

# Default max_tokens cap when neither per-spawn nor global env var is
# set (Story 3 acceptance). Operator overrides via SM_MAX_TOKENS or one
# of the four per-spawn SM_*_MAX_TOKENS env vars.
_DEFAULT_MAX_TOKENS: int = 4096

# Per-spawn env var names keyed by canonical role. The four roles match
# the spawn-agent surface (decompose / test_writer / coder / reviewer).
_ROLE_MODEL_ENV: dict = {
    "decompose": "SM_DECOMPOSE_MODEL",
    "test_writer": "SM_TEST_WRITER_MODEL",
    "coder": "SM_CODER_MODEL",
    "reviewer": "SM_REVIEWER_MODEL",
}
_ROLE_MAX_TOKENS_ENV: dict = {
    "decompose": "SM_DECOMPOSE_MAX_TOKENS",
    "test_writer": "SM_TEST_WRITER_MAX_TOKENS",
    "coder": "SM_CODER_MAX_TOKENS",
    "reviewer": "SM_REVIEWER_MAX_TOKENS",
}
_VALID_RESOLVER_ROLES: frozenset = frozenset(_ROLE_MODEL_ENV.keys())


class ConfigError(ValueError):
    """Raised when a configuration env var has an invalid value.

    Currently emitted only by `resolve_max_tokens` when one of the
    `*_MAX_TOKENS` env vars fails to parse as a non-negative integer.
    Subclasses ValueError so existing `except ValueError` handlers keep
    working; distinct class identity lets callers branch on
    `except ConfigError` for env-var-specific recovery.
    """


def _validate_resolver_role(role) -> None:
    """Shared role-arg validator for both resolvers.

    Non-string roles raise TypeError naming the bad class. Unknown role
    strings (including empty / whitespace-only) raise ValueError naming
    the valid role set so the operator can correct the call site.
    """
    if not isinstance(role, str):
        raise TypeError(
            f"role must be a string, got {role.__class__.__name__}"
        )
    if role not in _VALID_RESOLVER_ROLES:
        raise ValueError(
            f"unknown role {role!r}; valid roles are "
            f"{sorted(_VALID_RESOLVER_ROLES)!r}"
        )


def resolve_model(role: str) -> str:
    """Return the SDK model identifier for `role`, honoring precedence.

    Precedence chain (first non-empty wins):
      1. Per-spawn env var (`SM_DECOMPOSE_MODEL`, `SM_TEST_WRITER_MODEL`,
         `SM_CODER_MODEL`, `SM_REVIEWER_MODEL`).
      2. `SM_MODEL` global env var.
      3. The Haiku 4.5 default constant `_HAIKU_4_5_MODEL`.

    Empty-string and whitespace-only values are treated as "unset" and
    fall through to the next level (operator typo for "not set", not a
    parse error — there is no parse step for model strings).

    Args:
        role: One of `"decompose"`, `"test_writer"`, `"coder"`,
            `"reviewer"`. Non-string raises TypeError; unknown string
            raises ValueError naming the valid set.

    Returns:
        The resolved model id as a `str`. Whitespace at the edges of
        an env-var value is stripped before return — the SDK will not
        accept padded model ids and a leading space is always a typo.
    """
    import os as _os  # local import keeps the resolver stdlib-only
    _validate_resolver_role(role)

    per_spawn_raw = _os.environ.get(_ROLE_MODEL_ENV[role], "")
    per_spawn = per_spawn_raw.strip() if per_spawn_raw else ""
    if per_spawn:
        return per_spawn

    glob_raw = _os.environ.get("SM_MODEL", "")
    glob = glob_raw.strip() if glob_raw else ""
    if glob:
        return glob

    return _HAIKU_4_5_MODEL


def resolve_max_tokens(role: str) -> int:
    """Return the max_tokens cap for `role`, honoring precedence.

    Precedence chain (first non-empty wins):
      1. Per-spawn env var (`SM_DECOMPOSE_MAX_TOKENS`,
         `SM_TEST_WRITER_MAX_TOKENS`, `SM_CODER_MAX_TOKENS`,
         `SM_REVIEWER_MAX_TOKENS`).
      2. `SM_MAX_TOKENS` global env var.
      3. The default constant `_DEFAULT_MAX_TOKENS` (4096).

    Empty-string and whitespace-only values fall through to the next
    level (treated as "unset"). NON-empty values that fail to parse as
    a non-negative integer raise `ConfigError` (a ValueError subclass)
    naming both the offending env var and its value — the operator
    needs to know exactly which env var to fix. Negative values are
    rejected; zero is allowed (operator's call).

    Invalid per-spawn values do NOT silently fall through to the global
    env var or the default: invalid means invalid.

    Args:
        role: One of `"decompose"`, `"test_writer"`, `"coder"`,
            `"reviewer"`. Non-string raises TypeError; unknown string
            raises ValueError naming the valid set.

    Returns:
        The resolved cap as an `int`.

    Raises:
        ConfigError: A non-empty env-var value could not be parsed as a
            non-negative integer.
        TypeError / ValueError: Invalid `role` (delegated to
            `_validate_resolver_role`).
    """
    import os as _os  # local import keeps the resolver stdlib-only
    _validate_resolver_role(role)

    def _parse(env_name: str, raw: str) -> int:
        # `int(raw)` accepts ints in any case (e.g. "0x10" not — int()
        # without base rejects hex literals), and rejects float-strings
        # ("42.5"), mixed alphanumeric ("123abc"), and pure alphabetic
        # ("abc"). Catch ValueError specifically; ConfigError subclasses
        # ValueError so we re-raise as the typed error.
        try:
            n = int(raw)
        except ValueError:
            raise ConfigError(
                f"{env_name}={raw!r} is not a valid integer; "
                f"max_tokens env vars must be non-negative integers"
            ) from None
        if n < 0:
            raise ConfigError(
                f"{env_name}={raw!r} is negative; max_tokens env vars "
                f"must be non-negative integers"
            )
        return n

    per_spawn_name = _ROLE_MAX_TOKENS_ENV[role]
    per_spawn_raw = _os.environ.get(per_spawn_name, "")
    per_spawn = per_spawn_raw.strip() if per_spawn_raw else ""
    if per_spawn:
        return _parse(per_spawn_name, per_spawn)

    glob_raw = _os.environ.get("SM_MAX_TOKENS", "")
    glob = glob_raw.strip() if glob_raw else ""
    if glob:
        return _parse("SM_MAX_TOKENS", glob)

    return _DEFAULT_MAX_TOKENS


# Story 13 graph — exposes only the operator-driven transitions. The
# force_closed transitions remain in `_VALID_TRANSITIONS` for derive_state
# replay; Story 19 will own the writer.
_STORY_13_TRANSITIONS: dict = {
    "planned": frozenset({"in_progress"}),
    "in_progress": frozenset({"in_review"}),
    "in_review": frozenset({"accepted", "rejected"}),
    "accepted": frozenset(),       # terminal
    "rejected": frozenset(),       # terminal
}

# Full set of state names recognized by the lifecycle. Used to distinguish
# "invalid state name" (e.g. typos, ' in_progress', 'PLANNED') from
# "valid name but illegal transition from current state" — both raise
# StoryTransitionError, but the error message differs.
_STORY_STATES: frozenset = frozenset({
    "planned", "in_progress", "in_review", "accepted", "rejected",
    "force_closed",
})


# ---------------------------------------------------------------------------
# Iter 2 Story 5 — provider seam: single Anthropic SDK invocation point.
#
# `_invoke_anthropic` is the ONLY place in this module that imports or
# invokes the Anthropic SDK. All four real-agent spawn defaults
# (decompose / test_writer / coder / reviewer) route their SDK calls
# through this seam so that swapping providers later is a refactor, not
# a rewrite. Private (leading underscore), NOT in __all__.
#
# Behavior:
#   - Type-validates the four inputs BEFORE constructing the SDK client.
#     `messages` non-list, `model`/`api_key` non-string, `max_tokens`
#     non-int (or bool — int subclass, rejected explicitly) raise
#     TypeError. This is the only behavioral wrapping the seam performs.
#   - Lazy import: `import anthropic` lives inside the function body so
#     `import sm` is SDK-free. Tests rely on this to inject a fake SDK.
#   - Constructs `Anthropic(api_key=api_key)` on every call (no cache).
#   - Calls `client.messages.create(model=..., max_tokens=...,
#     messages=...)` and returns `response.content[0].text` verbatim.
#   - SDK exceptions propagate AS-IS. Story 5 is SDK-shaped, not
#     role-shaped; callers wrap into role-specific typed errors.
# ---------------------------------------------------------------------------

def _invoke_anthropic(
    messages: list,
    model: str,
    max_tokens: int,
    api_key: str,
) -> str:
    """Single Anthropic SDK invocation point — provider seam.

    All four real-agent spawn defaults route their SDK calls through this
    function. Inputs are type-validated before the SDK is constructed;
    SDK exceptions propagate unchanged for callers to wrap.
    """
    # Type validation FIRST — before any SDK import / construction so a
    # bad-typed call never reaches (or instantiates) the client.
    if not isinstance(messages, list):
        raise TypeError(
            f"messages must be a list, got {type(messages).__name__}"
        )
    if not isinstance(model, str):
        raise TypeError(
            f"model must be a string, got {type(model).__name__}"
        )
    # `bool` is a subclass of `int` in Python; a bool max-token count is
    # a caller bug, so reject it explicitly before the isinstance check.
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise TypeError(
            f"max_tokens must be an int, got {type(max_tokens).__name__}"
        )
    if not isinstance(api_key, str):
        raise TypeError(
            f"api_key must be a string, got {type(api_key).__name__}"
        )

    # Lazy import — NOT at module top level. Tests inject a fake
    # `anthropic` module into sys.modules before the call; the import
    # below finds the fake and never touches the real SDK.
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=messages,
    )
    return response.content[0].text


def resolve_role_spec(role: str) -> Path:
    """Resolve the absolute path to a canonical role-spec markdown file.

    Returns an absolute pathlib.Path to `<package_dir>/roles/<role>.md`,
    where `<package_dir>` is `LOG_PATH.parent` (the same anchor used for
    log lookup, so monkeypatching LOG_PATH redirects role-spec lookup
    consistently with the rest of the suite).

    Validation:
      - `role` must be a `str`. Non-string raises TypeError naming the
        class of the bad value.
      - `role` must be non-empty and not whitespace-only. Empty/blank
        raises ValueError.
      - `role` must be one of the four canonical names. Anything else
        raises ValueError naming the offending string.

    If the resolved path does not exist on disk, raises
    `RoleSpecNotFoundError` (a FileNotFoundError subclass) naming the role.
    """
    if not isinstance(role, str):
        raise TypeError(
            f"role must be a string, got {role.__class__.__name__}"
        )
    if not role or not role.strip():
        raise ValueError("role must be a non-empty, non-whitespace string")
    if role not in _CANONICAL_ROLES:
        raise ValueError(
            f"unknown role {role!r}; valid roles are {_CANONICAL_ROLES!r}"
        )

    # Anchor at LOG_PATH.parent so monkeypatching LOG_PATH redirects
    # role-spec lookup the same way it redirects log lookup. Resolve to
    # absolute so the returned Path is always absolute, even when LOG_PATH
    # is set to a relative path.
    package_dir = Path(LOG_PATH).resolve().parent
    roles_dir = package_dir / "roles"
    spec_path = (roles_dir / f"{role}.md").resolve()

    if spec_path.is_file():
        return spec_path

    # Iter 2 Story 6 — when the LOG_PATH-anchored roles/ dir does NOT
    # exist (CLI subprocess with SM_LOG_PATH override pointing at a
    # temp dir that has no roles/ staged), fall back to the directory
    # holding sm.py itself. Tests that anchor on LOG_PATH and expect
    # RoleSpecNotFoundError stage an empty roles/ subdir (see
    # test_resolve_role_spec.temp_roles_dir) so this fallback does NOT
    # fire there — preserving the missing-file contract.
    if not roles_dir.is_dir():
        sm_anchor = Path(__file__).resolve().parent
        fallback_path = (sm_anchor / "roles" / f"{role}.md").resolve()
        if fallback_path.is_file():
            return fallback_path

    raise RoleSpecNotFoundError(
        f"role-spec file for role {role!r} not found at {spec_path!s}"
    )


def _role_spec_hash(role: str) -> str:
    """Return the SHA-256 hex digest of the role-spec file's bytes.

    Validation flows through `resolve_role_spec` — unknown / empty /
    non-string roles raise the same errors, and a missing file raises
    `RoleSpecNotFoundError`. Same role + same bytes -> same digest.
    """
    spec_path = resolve_role_spec(role)
    return _hashlib.sha256(spec_path.read_bytes()).hexdigest()


def _append_entry(entry: dict) -> None:
    """Append one JSON object as a single LF-terminated line to LOG_PATH."""
    if not isinstance(entry, dict):
        raise TypeError(
            f"_append_entry requires a dict, got {type(entry).__name__}"
        )
    json_line = json.dumps(entry, ensure_ascii=False)
    with open(LOG_PATH, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json_line + "\n")
        fh.flush()


def read_entries() -> Iterator[dict]:
    """Yield each line of `LOG_PATH` parsed as a dict, in file order.

    Returns an empty iterator if the log is missing or zero-byte. Raises
    `ValueError` naming the 1-based line number if any line is malformed
    (invalid JSON, blank/whitespace-only, or a top-level non-dict value).
    CRLF line endings are tolerated; the trailing `\\r` is stripped before
    parsing.
    """
    # Resolve LOG_PATH at call-time so monkeypatching `sm.LOG_PATH` works.
    log_path = LOG_PATH

    if not log_path.exists():
        return
    if log_path.stat().st_size == 0:
        return

    with open(log_path, "r", encoding="utf-8", newline="") as fh:
        for line_no, raw in enumerate(fh, start=1):
            # Strip a single trailing newline (LF or CRLF). Do NOT strip
            # other whitespace — blank/whitespace-only lines must raise.
            if raw.endswith("\r\n"):
                line = raw[:-2]
            elif raw.endswith("\n"):
                line = raw[:-1]
            else:
                # Final line with no trailing LF.
                line = raw
            # Tolerate a stray trailing \r (e.g. mixed/odd line endings).
            if line.endswith("\r"):
                line = line[:-1]

            if not line or not line.strip():
                raise ValueError(
                    f"Malformed log entry on line {line_no}: blank or "
                    f"whitespace-only line"
                )

            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Malformed log entry on line {line_no}: {e.msg}"
                ) from e

            if not isinstance(obj, dict):
                raise ValueError(
                    f"Malformed log entry on line {line_no}: top-level "
                    f"value must be a JSON object, got "
                    f"{type(obj).__name__}"
                )

            yield obj


def build_entry(type: str, content: dict) -> dict:
    """Build a canonical log entry dict from a `type` and a `content` payload.

    Returns a new dict whose first three keys are auto-stamped — `id` (a fresh
    32-char lowercase-hex uuid4), `type` (the param verbatim), `timestamp`
    (ISO 8601 with local timezone offset, via
    `datetime.now().astimezone().isoformat()`) — followed by the content
    fields in their original insertion order, merged at the top level.

    Validation:
      - `type` must be a non-empty, non-whitespace-only `str`. Non-string
        raises `TypeError`; empty/whitespace-only raises `ValueError`.
      - `content` must be a `dict` (or dict subclass). Other types raise
        `TypeError`. Empty dict is accepted.
      - `content` must not contain top-level keys `'id'`, `'type'`, or
        `'timestamp'` (case-sensitive — `'ID'`, `'Type'` are allowed; nested
        keys are not flagged). Violation raises `ValueError` naming the
        offending key.

    The returned dict is a fresh object — mutating it does not affect the
    input, and mutating the input after the call does not affect the result.
    """
    # NOTE: the `type` parameter shadows the builtin inside this function.
    # Use `.__class__.__name__` to format type names in error messages —
    # never call `type(x)`.

    # --- Validate `type` parameter ---
    # Reject bool explicitly: bool is not a str subclass, so isinstance check
    # below already covers it. Strict str-only.
    if not isinstance(type, str):
        raise TypeError(
            f"type must be a string, got {type.__class__.__name__}"
        )
    if not type or not type.strip():
        raise ValueError("type must be a non-empty, non-whitespace string")

    # --- Validate `content` parameter ---
    if not isinstance(content, dict):
        raise TypeError(
            f"content must be a dict, got {content.__class__.__name__}"
        )

    # --- Reserved-key check (case-sensitive, top-level only) ---
    for k in _RESERVED_KEYS:
        if k in content:
            raise ValueError(
                f"content must not contain reserved key {k!r}"
            )

    # --- Build the result dict (auto-stamped fields first, then content) ---
    result: dict = {
        "id": uuid.uuid4().hex,
        "type": type,
        "timestamp": _dt.datetime.now().astimezone().isoformat(),
    }
    for k, v in content.items():
        result[k] = v
    return result


def derive_state() -> dict:
    """Replay the full event log and return the derived current state.

    Pure read: log bytes are not modified, no sidecar files written. Two
    consecutive calls produce equal (and independent) results.

    Returns a dict with five top-level keys:
      - active_iteration: dict {iteration_id, requirements: [...]} or None
      - story_backlog:    list[dict] of story records, ordered by `sequence`
      - sprint_cut:       int or None (latest sprint_cut entry wins)
      - story_states:     dict {story_id: state} where state is one of
                          {planned, in_progress, in_review, accepted,
                           rejected, force_closed}
      - close_status:     dict {closed_by, reason, accepted_count,
                          rejected_count, force_closed_count} or None
                          (cleared to None on a new iteration_open)

    Raises ValueError naming the offending entry id when:
      - a state_change targets an unknown story_id
      - a state_change is an illegal lifecycle transition
      - a second iteration_open lands with no intervening iteration_close

    Unknown entry types are no-ops (forward-compatibility).
    """
    state: dict = {
        "active_iteration": None,
        "story_backlog": [],
        "sprint_cut": None,
        "story_states": {},
        "close_status": None,
    }

    # Iter 2 Story 6: track whether a story_backlog has been written
    # for the currently-active iteration. A subsequent `iteration_open`
    # with a story_backlog in between is treated as an implicit
    # close-then-open cycle (the prior iteration's decomposition phase
    # completed). Two `iteration_open` entries with NO story_backlog (or
    # iteration_close) between them remain an invariant violation —
    # the strict single-active contract pinned by
    # `test_two_iteration_opens_no_close_raises` is preserved.
    _decomposed_since_open = False

    for entry in read_entries():
        etype = entry.get("type")
        eid = entry.get("id")

        if etype == "iteration_open":
            if state["active_iteration"] is not None:
                if not _decomposed_since_open:
                    raise ValueError(
                        f"iteration_open while another iteration is already "
                        f"open (entry id {eid!r})"
                    )
                # Implicit close: prior iteration had a story_backlog;
                # accept the new iter_open as the active iteration.
            state["active_iteration"] = {
                "iteration_id": entry.get("iteration_id"),
                "requirements": list(entry.get("requirements", [])),
            }
            state["close_status"] = None  # clear on new open
            _decomposed_since_open = False

        elif etype == "iteration_close":
            state["active_iteration"] = None
            state["close_status"] = {
                "closed_by": entry.get("closed_by"),
                "reason": entry.get("reason"),
                "accepted_count": entry.get("accepted_count", 0),
                "rejected_count": entry.get("rejected_count", 0),
                "force_closed_count": entry.get("force_closed_count", 0),
            }
            _decomposed_since_open = False

        elif etype == "story_decomposed" or etype == "story_backlog":
            stories = entry.get("stories", [])
            new_backlog = sorted(
                (_copy.deepcopy(s) for s in stories),
                key=lambda s: s["sequence"],
            )
            state["story_backlog"] = new_backlog
            state["story_states"] = {
                s["story_id"]: "planned" for s in new_backlog
            }
            # Iter 2 Story 6: mark decomposition complete for the
            # active iteration so a subsequent iter_open can implicitly
            # close it (see iteration_open branch above).
            _decomposed_since_open = True

        elif etype == "sprint_cut":
            state["sprint_cut"] = entry.get("cut_position")

        elif etype == "story_state_change":
            sid = entry.get("story_id")
            to_state = entry.get("to_state")
            if sid not in state["story_states"]:
                raise ValueError(
                    f"story_state_change for unknown story_id {sid!r} "
                    f"(entry id {eid!r})"
                )
            current = state["story_states"][sid]
            allowed = _VALID_TRANSITIONS.get(current, frozenset())
            if to_state not in allowed:
                raise ValueError(
                    f"illegal story state transition from {current!r} to "
                    f"{to_state!r} for story {sid!r} (entry id {eid!r})"
                )
            state["story_states"][sid] = to_state

        # Unknown entry types: no-op (forward-compat).

    return state


def ingest(path) -> dict:
    """Ingest a PO Tool iteration-open handoff JSON file at `path`.

    Reads + validates the handoff, then writes a single `iteration_open`
    log entry via the canonical `build_entry` + `_append_entry` path.
    Returns the appended entry dict.

    Validation failures raise `ValueError` (with no log write). Filesystem
    errors are stdlib-canonical: missing path → `FileNotFoundError`,
    directory path → `IsADirectoryError`.

    Accepts either `str` or `pathlib.Path`. Failure invariant: log.jsonl
    is byte-for-byte unchanged on any validation/parse/IO failure.
    """
    p = Path(path)

    # --- Filesystem checks (stdlib-canonical errors) ---
    if not p.exists():
        raise FileNotFoundError(f"handoff file not found: {p!s}")
    if p.is_dir():
        raise IsADirectoryError(f"handoff path is a directory: {p!s}")

    # --- Read + parse JSON ---
    raw = p.read_text(encoding="utf-8")
    try:
        handoff = json.loads(raw)
    except json.JSONDecodeError as e:
        raise IngestJSONError(
            f"handoff file is not valid JSON: {e.msg}"
        ) from e

    # --- Top-level shape ---
    if not isinstance(handoff, dict):
        raise IngestShapeError(
            f"handoff top-level must be a JSON object, got "
            f"{handoff.__class__.__name__}"
        )

    # iteration_id
    if "iteration_id" not in handoff:
        raise IngestShapeError(
            "handoff missing required field 'iteration_id'"
        )
    iter_id = handoff["iteration_id"]
    if not isinstance(iter_id, str) or not iter_id.strip():
        raise IngestShapeError(
            "handoff 'iteration_id' must be a non-empty string"
        )

    # requirements
    if "requirements" not in handoff:
        raise IngestShapeError(
            "handoff missing required field 'requirements'"
        )
    reqs = handoff["requirements"]
    if not isinstance(reqs, list):
        raise IngestShapeError(
            f"handoff 'requirements' must be a list, got "
            f"{reqs.__class__.__name__}"
        )
    if len(reqs) == 0:
        raise IngestShapeError(
            "handoff 'requirements' must not be empty"
        )

    # Per-requirement validation + duplicate-id check
    seen_ids: dict = {}
    for i, req in enumerate(reqs):
        if not isinstance(req, dict):
            raise IngestShapeError(
                f"handoff 'requirements'[{i}] must be a dict, got "
                f"{req.__class__.__name__}"
            )
        if "requirement_id" not in req:
            raise IngestShapeError(
                f"handoff 'requirements'[{i}] missing required field "
                f"'requirement_id'"
            )
        rid = req["requirement_id"]
        if not isinstance(rid, str) or not rid.strip():
            raise IngestShapeError(
                f"handoff 'requirements'[{i}] 'requirement_id' must be a "
                f"non-empty string"
            )
        if rid in seen_ids:
            raise IngestShapeError(
                f"handoff 'requirements' contains duplicate "
                f"requirement_id {rid!r}"
            )
        seen_ids[rid] = i

    # --- Single-active-iteration enforcement (via derive_state).
    # Story 7 precedence: this check fires BEFORE the dup-id check. When
    # both would fire (i.e., the new handoff's iteration_id matches the
    # currently-open iteration), the operator gets the actionable
    # "close it first" message rather than the cosmetic dup-id one.
    state = derive_state()
    if state["active_iteration"] is not None:
        open_id = state["active_iteration"]["iteration_id"]
        raise IngestActiveError(
            f"cannot ingest: iteration {open_id!r} is already open; "
            f"close before re-ingesting"
        )

    # --- Duplicate iteration_id check (Story 6).
    # Scan ALL prior `iteration_open` entries — including ones that have
    # since been closed or force-closed. With Story 7's precedence flip,
    # this only fires when nothing is currently open AND the new id was
    # used by a prior (now-closed) iteration. Pure read of the log; no write.
    for prior in read_entries():
        if (prior.get("type") == "iteration_open"
                and prior.get("iteration_id") == iter_id):
            raise IngestDuplicateError(
                f"cannot ingest: iteration_id {iter_id!r} was already "
                f"used by a prior iteration_open entry"
            )

    # --- All validation passed; build + append ---
    entry = build_entry("iteration_open", handoff)
    _append_entry(entry)
    return entry


# ---------------------------------------------------------------------------
# Iter 2 Story 6 — real `spawn_agent` default for `decompose`.
#
# `_default_decompose_spawn` is the real (non-injected) spawn-agent the
# `decompose` function falls back to when no callable is injected. It
# composes Stories 2 (resolve_api_key), 3 (resolve_model /
# resolve_max_tokens), and 5 (_invoke_anthropic provider seam) into a
# single SDK call. The function matches the injectable signature pinned
# by Iter 1 Story 9 exactly — `(role_spec_path: str,
# requirements: list[dict]) -> str` — so swapping default <-> injected
# is signature-transparent. PRIVATE; NOT in __all__.
#
# Behavior:
#   - Resolves the API key at call time (raises MissingAPIKeyError if
#     unset — propagates unchanged so the CLI's exit-12 mapping fires).
#   - Resolves model + max_tokens at call time so env-var overrides
#     are honored on every call (no caching).
#   - Reads the role-spec file the caller resolved and packages its
#     content + the requirements list into a single user-role message.
#   - Calls `_invoke_anthropic(...)` (the provider seam — the ONLY SDK
#     import site in this module).
#   - Returns the seam's response string AS-IS for the caller to route
#     through `parse_agent_json`.
#   - SDK exceptions wrap as `DecomposeAgentError` with the original
#     chained via `__cause__`. MissingAPIKeyError propagates unchanged.
# ---------------------------------------------------------------------------

def _default_decompose_spawn(
    role_spec_path: str,
    requirements: list,
) -> str:
    """Default spawn_agent for `decompose` — calls the real Anthropic SDK.

    Composes Story 2 (resolve_api_key) + Story 3 (resolve_model,
    resolve_max_tokens) + role-spec file read + Story 5
    (_invoke_anthropic) into one SDK round-trip. SDK exceptions wrap as
    `DecomposeAgentError` with `__cause__` chained; MissingAPIKeyError
    propagates unchanged so the CLI maps it to exit 12.

    Signature matches the injectable contract pinned by Iter 1 Story 9.
    """
    # Resolve at call time so env-var overrides are honored every call.
    api_key = resolve_api_key()  # raises MissingAPIKeyError if unset
    model = resolve_model("decompose")
    max_tokens = resolve_max_tokens("decompose")

    # Read the role-spec file the caller already resolved. Any OS error
    # (FileNotFoundError, PermissionError) propagates verbatim — the
    # caller decides whether to wrap. The Story 6 spec leaves this to
    # the Coder's discretion; raw-OS-error propagation is the simpler
    # and more debuggable contract for an operational misconfiguration.
    role_spec_text = Path(role_spec_path).read_text(encoding="utf-8")

    # Single user-role message bundling role-spec + requirements (as
    # JSON). Story 6 leaves exact framing to the Coder; tests pin that
    # both pieces appear in the message content.
    user_content = (
        f"{role_spec_text}\n\n"
        f"## Active iteration requirements\n\n"
        f"{json.dumps(requirements, indent=2)}\n\n"
        f"Return your story decomposition as a JSON object per the "
        f"role spec."
    )
    messages = [{"role": "user", "content": user_content}]

    try:
        return _invoke_anthropic(messages, model, max_tokens, api_key)
    except MissingAPIKeyError:
        # Should not fire here (resolver already ran), but if a downstream
        # path raises it, propagate unchanged so the CLI's exit-12
        # mapping covers it.
        raise
    except DecomposeAgentError:
        # Already typed for the caller — pass through.
        raise
    except Exception as e:
        raise DecomposeAgentError(
            f"decompose agent SDK call failed: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Iter 2 Story 7 — real `spawn_test_writer` default for `execute`.
#
# `_default_execute_test_writer_spawn` is the real (non-injected) spawn-
# agent the `execute` function falls back to for the TestWriter stage when
# no callable is injected. Mirrors Story 6's `_default_decompose_spawn`
# shape exactly except:
#   - signature is `(role_spec_path: str, story: dict) -> str` per Iter 1
#     Story 23's injectable contract
#   - role is "test_writer" (drives resolve_model / resolve_max_tokens)
#   - user message bundles role-spec text + story dict (as JSON) + an
#     instruction to return test code
#   - the returned text is the SDK response VERBATIM — TestWriter returns
#     code, not JSON, so the default does NOT route through
#     `parse_agent_json`
#   - SDK exceptions wrap as `TestWriterAgentError` with `__cause__`
#     chained; MissingAPIKeyError AND ConfigError propagate UNCHANGED
#     (operator needs the typed errors for diagnosis / exit-12 mapping)
#
# PRIVATE; NOT in __all__.
# ---------------------------------------------------------------------------

def _default_execute_test_writer_spawn(
    role_spec_path: str,
    story: dict,
) -> str:
    """Default spawn_test_writer for `execute` — calls the real Anthropic
    SDK.

    Composes Story 2 (resolve_api_key) + Story 3 (resolve_model,
    resolve_max_tokens) + role-spec file read + Story 5
    (_invoke_anthropic) into one SDK round-trip. SDK exceptions wrap as
    `TestWriterAgentError` with `__cause__` chained; MissingAPIKeyError
    and ConfigError propagate unchanged so the CLI maps
    MissingAPIKeyError to exit 12 and ConfigError stays diagnosable.

    Signature matches the injectable contract pinned by Iter 1 Story 23.
    The returned string is the SDK response VERBATIM — TestWriter
    returns code, not JSON, so no parse_agent_json call.
    """
    # Resolve at call time so env-var overrides are honored every call.
    api_key = resolve_api_key()  # raises MissingAPIKeyError if unset
    model = resolve_model("test_writer")
    max_tokens = resolve_max_tokens("test_writer")

    # Read the role-spec file the caller already resolved. Any OS error
    # (FileNotFoundError, PermissionError) propagates verbatim — the
    # caller decides whether to wrap. Mirrors Story 6's decision.
    role_spec_text = Path(role_spec_path).read_text(encoding="utf-8")

    # Single user-role message bundling role-spec + story dict (as JSON)
    # + instruction to return test code. Exact framing left to the
    # Coder; tests pin that all pieces appear in the message content.
    user_content = (
        f"{role_spec_text}\n\n"
        f"## Active story\n\n"
        f"{json.dumps(story, indent=2)}\n\n"
        f"Return the test code for this story per the role spec."
    )
    messages = [{"role": "user", "content": user_content}]

    try:
        return _invoke_anthropic(messages, model, max_tokens, api_key)
    except MissingAPIKeyError:
        # Should not fire here (resolver already ran), but if a downstream
        # path raises it, propagate unchanged so the CLI's exit-12
        # mapping covers it.
        raise
    except ConfigError:
        # Should not fire here either (resolvers already ran), but if a
        # downstream path raises it, propagate unchanged so the operator
        # gets the typed error for diagnosis.
        raise
    except TestWriterAgentError:
        # Already typed for the caller — pass through.
        raise
    except Exception as e:
        raise TestWriterAgentError(
            f"test_writer agent SDK call failed: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Iter 2 Story 8 — real `spawn_coder` default for `execute`.
#
# `_default_execute_coder_spawn` is the real (non-injected) spawn-agent the
# `execute` function falls back to for the Coder stage when no callable is
# injected. Mirrors Story 7's `_default_execute_test_writer_spawn` shape
# with three deltas:
#   - signature is `(role_spec_path: str, story: dict, test_code: str) -> str`
#     per Iter 1 Story 23's injectable contract (Coder takes test_code)
#   - role is "coder" (drives resolve_model / resolve_max_tokens)
#   - user message bundles role-spec text + story dict (as JSON) + test_code
#     + an instruction to return implementation code
#   - SDK exceptions wrap as `CoderAgentError` with `__cause__` chained;
#     MissingAPIKeyError AND ConfigError propagate UNCHANGED
#
# Returns the SDK response VERBATIM — Coder returns implementation code,
# not JSON, so the default does NOT route through `parse_agent_json`.
#
# PRIVATE; NOT in __all__.
# ---------------------------------------------------------------------------

def _default_execute_coder_spawn(
    role_spec_path: str,
    story: dict,
    test_code: str,
) -> str:
    """Default spawn_coder for `execute` — calls the real Anthropic SDK.

    Composes Story 2 (resolve_api_key) + Story 3 (resolve_model,
    resolve_max_tokens) + role-spec file read + Story 5
    (_invoke_anthropic) into one SDK round-trip. SDK exceptions wrap as
    `CoderAgentError` with `__cause__` chained; MissingAPIKeyError and
    ConfigError propagate unchanged so the CLI maps MissingAPIKeyError
    to exit 12 and ConfigError stays diagnosable.

    Signature matches the injectable contract pinned by Iter 1 Story 23.
    The returned string is the SDK response VERBATIM — Coder returns
    implementation code, not JSON, so no parse_agent_json call.
    """
    # Resolve at call time so env-var overrides are honored every call.
    api_key = resolve_api_key()  # raises MissingAPIKeyError if unset
    model = resolve_model("coder")
    max_tokens = resolve_max_tokens("coder")

    # Read the role-spec file the caller already resolved. Any OS error
    # (FileNotFoundError, PermissionError) propagates verbatim — the
    # caller decides whether to wrap. Mirrors Story 7's decision.
    role_spec_text = Path(role_spec_path).read_text(encoding="utf-8")

    # Single user-role message bundling role-spec + story dict (as JSON)
    # + test_code + instruction to return implementation code. Exact
    # framing left to the Coder; tests pin that all pieces appear in the
    # message content.
    user_content = (
        f"{role_spec_text}\n\n"
        f"## Active story\n\n"
        f"{json.dumps(story, indent=2)}\n\n"
        f"## Test code to implement against\n\n"
        f"{test_code}\n\n"
        f"Return the implementation code per the role spec."
    )
    messages = [{"role": "user", "content": user_content}]

    try:
        return _invoke_anthropic(messages, model, max_tokens, api_key)
    except MissingAPIKeyError:
        # Should not fire here (resolver already ran), but if a downstream
        # path raises it, propagate unchanged so the CLI's exit-12
        # mapping covers it.
        raise
    except ConfigError:
        # Should not fire here either (resolvers already ran), but if a
        # downstream path raises it, propagate unchanged so the operator
        # gets the typed error for diagnosis.
        raise
    except CoderAgentError:
        # Already typed for the caller — pass through.
        raise
    except Exception as e:
        raise CoderAgentError(
            f"coder agent SDK call failed: {e}"
        ) from e


def decompose(spawn_agent: Optional[Callable] = None) -> dict:
    """Spawn an SM Agent (or an injected stub) to decompose the active
    iteration's requirements into a sequence of stories, then write a single
    `story_backlog` log entry on success.

    Iter 2 Story 6 contract (supersedes Iter 1 Story 9's NotImplementedError
    default):

      - `spawn_agent` defaults to `None`; passing `None` (explicit or
        implicit) routes to the real `_default_decompose_spawn` which
        calls the Anthropic SDK via the Story 5 provider seam. Operators
        / tests may still inject a callable to bypass the SDK entirely.

      - Reads the active iteration via `derive_state()`. No active
        iteration → `ValueError("no active iteration; ingest a handoff
        first")`. No log write.

      - Resolves the SM Agent role-spec via `resolve_role_spec("sm_agent")`
        and computes the role-spec hash via `_role_spec_hash("sm_agent")`.

      - Calls `spawn_agent(role_spec_path: str, requirements: list[dict])`
        synchronously (blocks until the agent returns).

      - Parses the agent's JSON output. Parse failure raises
        `DecomposeOutputParseError`. Shape failure raises
        `DecomposeOutputShapeError`. Either way: NO log write. If the
        spawn_agent callable itself raises, that exception propagates
        verbatim and the log is unchanged.

      - On success: assigns each story a fresh uuid4-hex `story_id` (the
        operator's job, not the agent's — any agent-supplied `story_id` is
        overridden), then writes a single `story_backlog` log entry via
        `build_entry` + `_append_entry`. Returns the entry dict.
    """
    if spawn_agent is None:
        # Iter 2 Story 6: fall back to the real default. Bind from the
        # module so monkeypatches in tests (`monkeypatch.setattr(sm,
        # "_default_decompose_spawn", ...)`) take effect.
        import sys as _sys
        spawn_agent = _sys.modules[__name__]._default_decompose_spawn

    state = derive_state()
    if state["active_iteration"] is None:
        raise ValueError("no active iteration; ingest a handoff first")

    iteration = state["active_iteration"]
    requirements = iteration["requirements"]

    role_spec_path = resolve_role_spec("sm_agent")
    role_spec_hash = _role_spec_hash("sm_agent")

    # Synchronous call — blocks until the agent returns. Any exception the
    # callable raises propagates verbatim (no log write).
    output_str = spawn_agent(str(role_spec_path), requirements)

    # --- Parse JSON (Story 4: route through shared helper) ---
    # `parse_agent_json` is the single source of truth for agent-response
    # JSON parsing. On parse failure it raises `DecomposeAgentError`; we
    # re-raise as `DecomposeOutputParseError` (now a subclass of
    # DecomposeAgentError) so Iter 1's parse-error contract is preserved.
    # A non-string from `spawn_agent` (helper raises TypeError) is treated
    # the same way — a malformed agent return shape.
    try:
        output = parse_agent_json(output_str, "decompose")
    except DecomposeAgentError as e:
        raise DecomposeOutputParseError(str(e)) from e.__cause__
    except TypeError as e:
        raise DecomposeOutputParseError(
            f"agent output is not valid JSON: {e}"
        ) from e

    # --- Validate top-level shape ---
    if not isinstance(output, dict):
        raise DecomposeOutputShapeError(
            f"agent output must be a JSON object, got "
            f"{output.__class__.__name__}"
        )
    if "stories" not in output:
        raise DecomposeOutputShapeError(
            "agent output missing required 'stories' key"
        )
    stories = output["stories"]
    if not isinstance(stories, list):
        raise DecomposeOutputShapeError(
            f"'stories' must be a list, got "
            f"{stories.__class__.__name__}"
        )
    if len(stories) == 0:
        raise DecomposeOutputShapeError("'stories' must be non-empty")

    REQUIRED_FIELDS = (
        "sequence",
        "title",
        "size",
        "requirement_ids",
        "acceptance_criteria",
    )
    VALID_SIZES = {"S", "M", "L"}

    # --- Per-story shape validation ---
    for idx, s in enumerate(stories):
        if not isinstance(s, dict):
            raise DecomposeOutputShapeError(
                f"story at index {idx} must be a dict, got "
                f"{s.__class__.__name__}"
            )
        for field in REQUIRED_FIELDS:
            if field not in s:
                raise DecomposeOutputShapeError(
                    f"story at index {idx} missing required field "
                    f"{field!r}"
                )
        # size validation
        if s["size"] not in VALID_SIZES:
            raise DecomposeOutputShapeError(
                f"story at index {idx} has invalid size {s['size']!r}; "
                f"must be one of {sorted(VALID_SIZES)!r}"
            )
        # requirement_ids validation
        rids = s["requirement_ids"]
        if not isinstance(rids, list):
            raise DecomposeOutputShapeError(
                f"story at index {idx} requirement_ids must be a list, "
                f"got {rids.__class__.__name__}"
            )
        if len(rids) == 0:
            raise DecomposeOutputShapeError(
                f"story at index {idx} requirement_ids must be non-empty"
            )
        if not all(isinstance(r, str) for r in rids):
            raise DecomposeOutputShapeError(
                f"story at index {idx} requirement_ids must be a list of "
                f"strings"
            )
        # acceptance_criteria non-empty after strip (Story 10 tightening).
        # Story 9 already requires the field be present; Story 10 adds the
        # rule that whitespace-only AC (incl. empty string, tabs, newlines)
        # is a shape error — the field must carry substance.
        ac_val = s["acceptance_criteria"]
        if not isinstance(ac_val, str) or not ac_val.strip():
            seq_marker = s.get("sequence", idx + 1)
            title_marker = s.get("title", f"index {idx}")
            raise DecomposeOutputShapeError(
                f"story {seq_marker!r} ({title_marker!r}) field "
                f"'acceptance_criteria' must be a non-empty, "
                f"non-whitespace-only string"
            )

    # --- Sequence validation: must be exactly 1..N strictly increasing ---
    sequences = [s["sequence"] for s in stories]
    expected = list(range(1, len(stories) + 1))
    if sequences != expected:
        raise DecomposeOutputShapeError(
            f"sequences must be strictly 1..N, got {sequences!r} "
            f"(expected {expected!r})"
        )

    # --- Cross-reference check (Story 10): every requirement_id in every
    # story must appear in the active iteration's handoff requirements
    # list. Unknown ids → DecomposeUnknownRequirementError (distinct from
    # shape errors). Runs AFTER shape validation (so we know stories have
    # the right shape) and BEFORE story_id minting + log write (so any
    # failure leaves the log byte-for-byte unchanged).
    valid_ids = {
        r["requirement_id"]
        for r in requirements
        if isinstance(r, dict) and "requirement_id" in r
    }
    for s in stories:
        for rid in s["requirement_ids"]:
            if rid not in valid_ids:
                sequence = s.get("sequence")
                title = s.get("title")
                raise DecomposeUnknownRequirementError(
                    f"story {sequence!r} ({title!r}) references unknown "
                    f"requirement_id {rid!r}; valid ids are "
                    f"{sorted(valid_ids)!r}"
                )

    # --- Mint story_ids (override any agent-supplied id) ---
    enriched_stories = []
    for s in stories:
        new_s = dict(s)
        new_s["story_id"] = uuid.uuid4().hex
        enriched_stories.append(new_s)

    # --- Build + append the entry ---
    entry = build_entry(
        "story_backlog",
        {
            "stories": enriched_stories,
            "role_spec_path": str(role_spec_path),
            "role_spec_hash": role_spec_hash,
        },
    )
    _append_entry(entry)
    return entry


def sprint_cut(n: int) -> dict:
    """Cut the story backlog at position N.

    Story 11 contract:

      - Reads the active iteration's story backlog via `derive_state()`.
      - Validates type-first: bool is rejected (not a real int), and any
        other non-int type raises `TypeError`.
      - Validates state: no active iteration → `SprintCutError`. No story
        backlog yet → `SprintCutError`. Both with no log write.
      - Validates range: 1 <= N <= len(backlog). Out-of-range →
        `SprintCutError`. No log write.
      - On success: writes a single `sprint_cut` entry whose content
        carries `cut_position` (int N), `in_sprint_story_ids` (story_ids
        1..N in sequence order), and `deferred_story_ids` (story_ids
        N+1..end in sequence order). Returns the appended entry dict.
      - Re-cut is allowed regardless of story states — the lock-when-
        not-planned rule is Story 12's responsibility, not Story 11's.

    Failure invariant: log.jsonl is byte-for-byte unchanged on any
    validation/argument failure (TypeError or SprintCutError).
    """
    # Type validation FIRST — bool is int subclass, reject explicitly.
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError(
            f"n must be int, got {n.__class__.__name__}"
        )

    state = derive_state()
    if state["active_iteration"] is None:
        raise SprintCutError(
            "no active iteration; ingest a handoff first"
        )

    backlog = state["story_backlog"]
    if not backlog:
        raise SprintCutError(
            "no story backlog yet; run decompose first"
        )

    L = len(backlog)
    if n < 1:
        raise SprintCutError(
            f"position must be >= 1, got {n}"
        )
    if n > L:
        raise SprintCutError(
            f"position {n} exceeds backlog length {L}"
        )

    # Story 12 — re-cut lock check. After all type/state/range validation
    # passes, scan the log for the LATEST prior sprint_cut entry. If one
    # exists, this is a re-cut: any in-sprint story (per that entry's
    # in_sprint_story_ids) whose state has left "planned" locks the cut.
    # Lock is replay-derived from `state` already in hand — no separate
    # flag is persisted, and no log write happens on failure.
    latest_prior_in_sprint = None
    for entry in read_entries():
        if entry.get("type") == "sprint_cut":
            latest_prior_in_sprint = entry.get("in_sprint_story_ids", [])

    if latest_prior_in_sprint is not None:
        story_states = state["story_states"]
        offenders = [
            sid for sid in latest_prior_in_sprint
            if story_states.get(sid, "planned") != "planned"
        ]
        if offenders:
            raise SprintCutLockedError(
                f"sprint cut locked — these in-sprint stories have left "
                f"planned state: {offenders!r}; close or force-close the "
                f"iteration before re-cutting"
            )

    # Build the cut: stories 1..N in sprint, N+1..L deferred. derive_state
    # already returns the backlog sorted by sequence, so slicing preserves
    # sequence order.
    in_sprint_ids = [s["story_id"] for s in backlog[:n]]
    deferred_ids = [s["story_id"] for s in backlog[n:]]

    entry = build_entry(
        "sprint_cut",
        {
            "cut_position": n,
            "in_sprint_story_ids": in_sprint_ids,
            "deferred_story_ids": deferred_ids,
        },
    )
    _append_entry(entry)
    return entry


def transition_story(
    story_id: str,
    to_state: str,
    notes: str = "",
) -> dict:
    """Transition one in-sprint story to a new lifecycle state.

    Story 13 contract (Sprint 2, first story):

      - Validates type-first: `story_id`, `to_state`, `notes` must each be
        `str` (bool is rejected explicitly — `True`/`False` are not strings
        of value). Any non-string raises `TypeError`. No log write.

      - Reads state via `derive_state()`. Failure modes (all raise
        `StoryTransitionError`, no log write):
            * no active iteration
            * no active sprint (no `sprint_cut` entry yet)
            * `story_id` is not in the active sprint (unknown OR deferred)
            * current state is terminal (`accepted` / `rejected`)
            * `to_state` is not a recognized state name (typo, whitespace,
              wrong case, etc.)
            * `to_state` is recognized but the transition is illegal from
              the current state (skip / backwards / self-loop / force_closed
              — force_closed is Story 19's lane, not Story 13's)

      - Story 13 allowed transitions (force_closed handled by Story 19):
            planned     -> in_progress
            in_progress -> in_review
            in_review   -> accepted
            in_review   -> rejected
            accepted, rejected are TERMINAL

      - On success: writes a single `story_state_change` log entry via
        `build_entry` + `_append_entry`. Entry shape:
            {
              "id", "type", "timestamp" (auto-stamped),
              "story_id": "<hex>",
              "from_state": "<current>",
              "to_state":   "<requested>",
              "notes":      "<free text — may be empty>",
            }
        Returns the appended entry dict.

      - Failure invariant: log.jsonl is byte-for-byte unchanged on any
        validation/argument failure (TypeError or StoryTransitionError).
    """
    # --- Type validation FIRST (before any log read) ---
    # bool is an int subclass, not a str subclass, so isinstance(x, str)
    # already rejects True/False. No special-case needed here, but spell
    # the rejection out for symmetry with the rest of the module.
    if not isinstance(story_id, str):
        raise TypeError(
            f"story_id must be a string, got "
            f"{story_id.__class__.__name__}"
        )
    if not isinstance(to_state, str):
        raise TypeError(
            f"to_state must be a string, got "
            f"{to_state.__class__.__name__}"
        )
    if not isinstance(notes, str):
        raise TypeError(
            f"notes must be a string, got "
            f"{notes.__class__.__name__}"
        )

    # --- Replay state. Uses module-level derive_state so test
    # monkeypatching of sm.derive_state takes effect. ---
    state = derive_state()

    if state["active_iteration"] is None:
        raise StoryTransitionError(
            "no active iteration; ingest a handoff first before "
            "transitioning stories"
        )

    if state["sprint_cut"] is None:
        raise StoryTransitionError(
            "no active sprint (no sprint_cut entry yet); cut the sprint "
            "before transitioning stories"
        )

    # --- Find the LATEST sprint_cut entry's in_sprint_story_ids.
    # derive_state stores only the cut_position int; we need the actual id
    # list to enforce in-sprint membership. Pure read of the log.
    in_sprint_ids: list = []
    for entry in read_entries():
        if entry.get("type") == "sprint_cut":
            in_sprint_ids = entry.get("in_sprint_story_ids", []) or []

    if story_id not in in_sprint_ids:
        raise StoryTransitionError(
            f"story_id {story_id!r} is not in the active sprint; "
            f"only in-sprint stories may be transitioned"
        )

    # --- Determine current state. Membership in in_sprint_ids already
    # guarantees the story exists in the backlog, hence in story_states.
    current_state = state["story_states"].get(story_id, "planned")

    if current_state in {"accepted", "rejected", "force_closed"}:
        raise StoryTransitionError(
            f"story {story_id!r} is in terminal state {current_state!r}; "
            f"cannot transition further"
        )

    # --- Validate the to_state name itself (typos, casing, whitespace) ---
    if to_state not in _STORY_STATES:
        raise StoryTransitionError(
            f"to_state {to_state!r} is not a recognized lifecycle state; "
            f"must be one of {sorted(_STORY_STATES)!r}"
        )

    # --- Validate the transition is legal from the current state under the
    # Story 13 graph (force_closed deliberately not exposed here — Story 19).
    allowed = _STORY_13_TRANSITIONS.get(current_state, frozenset())
    if to_state not in allowed:
        raise StoryTransitionError(
            f"illegal transition from {current_state!r} to {to_state!r} "
            f"for story {story_id!r}; allowed targets from "
            f"{current_state!r} are {sorted(allowed)!r}"
        )

    # --- Story 15 — accept gate. Only the `accepted` target is gated; the
    # other transitions (in_progress, in_review, rejected) bypass entirely.
    # Rule: there must be at least one prior `reviewer_approval` log entry
    # for this story_id, and the LATEST such entry (last write wins on
    # replay) must have approved=True AND a test_result that is a string,
    # non-empty after strip. Synthetic entries with empty/whitespace
    # test_result do NOT satisfy the gate (defense in depth — the writer
    # rule and the reader rule must agree).
    if to_state == "accepted":
        latest_approval = None
        for entry in read_entries():
            if (entry.get("type") == "reviewer_approval"
                    and entry.get("story_id") == story_id):
                latest_approval = entry

        def _approval_satisfies_gate(approval: Optional[dict]) -> bool:
            if approval is None:
                return False
            if approval.get("approved") is not True:
                return False
            tr = approval.get("test_result")
            if not isinstance(tr, str) or not tr.strip():
                return False
            return True

        if not _approval_satisfies_gate(latest_approval):
            raise AcceptGateError(
                f"cannot accept story {story_id!r}: missing a valid "
                f"reviewer_approval entry (need approved=True with a "
                f"non-empty test_result); record one via record_review "
                f"before accepting"
            )

    # --- All validation passed; build + append a single entry ---
    entry = build_entry(
        "story_state_change",
        {
            "story_id": story_id,
            "from_state": current_state,
            "to_state": to_state,
            "notes": notes,
        },
    )
    _append_entry(entry)
    return entry


def record_review(story_id: str, approved: bool, test_result: str) -> dict:
    """Record a reviewer's approval (or rejection) for an in-sprint story.

    Story 15 contract:

      - Validates type-first: `story_id` must be `str`; `approved` must be a
        strict `bool` (NOT an int — `isinstance(x, bool)` is checked
        explicitly so `1` / `0` are rejected); `test_result` must be `str`.
        Any type mismatch raises `TypeError`. No log write.

      - Validates value: `test_result` must be non-empty after `.strip()`.
        Empty / whitespace-only raises `ReviewError`. No log write.

      - On success: appends a single `reviewer_approval` log entry via
        `build_entry` + `_append_entry`. Entry shape:
            {
              "id", "type": "reviewer_approval", "timestamp" (auto-stamped),
              "story_id":    "<hex>",
              "approved":    True | False,
              "test_result": "<verbatim string from caller>",
            }
        Returns the appended entry dict.

      - Failure invariant: log.jsonl is byte-for-byte unchanged on any
        validation failure (TypeError or ReviewError).

      - Note: this writer enforces the non-whitespace rule. The accept gate
        in `transition_story` enforces the same rule on the read side as
        defense in depth — synthetic entries planted directly into the log
        with whitespace-only test_result do NOT satisfy the gate.
    """
    # --- Type validation FIRST (before any log read or write) ---
    if not isinstance(story_id, str):
        raise TypeError(
            f"story_id must be a string, got "
            f"{story_id.__class__.__name__}"
        )
    # Strict bool check: bool is an int subclass, so `isinstance(x, int)`
    # would accept True/False. Here we want the OPPOSITE — only True/False,
    # never 1/0/"true"/None. Test pinned this explicitly.
    if not isinstance(approved, bool):
        raise TypeError(
            f"approved must be a bool, got {approved.__class__.__name__}"
        )
    if not isinstance(test_result, str):
        raise TypeError(
            f"test_result must be a string, got "
            f"{test_result.__class__.__name__}"
        )

    # --- Value validation: test_result must carry substance ---
    if not test_result.strip():
        raise ReviewError(
            "test_result must be a non-empty, non-whitespace string"
        )

    # --- Build + append the reviewer_approval entry ---
    entry = build_entry(
        "reviewer_approval",
        {
            "story_id": story_id,
            "approved": approved,
            "test_result": test_result,
        },
    )
    _append_entry(entry)
    return entry


def status() -> str:
    """Render a human-readable snapshot of the active iteration's state.

    Story 16 contract:

      - Pure read: calls `derive_state()` only. Never calls `_append_entry`,
        never touches `log.jsonl`, never creates sidecar files. Two
        consecutive calls return equal strings on a frozen log.

      - Returns a non-empty string. Implementor's choice between print and
        return — this implementation returns the string (the CLI handler
        prints it).

      - No active iteration: returned string contains the substring
        ``"no active iteration"``. Covers empty log, post-close, and
        missing-LOG_PATH-on-disk cases (derive_state already handles all
        three uniformly).

      - Active iteration: header line names the iteration_id. Each backlog
        story (if any) gets one line carrying:
            * sequence
            * story_id
            * membership label — ``in-sprint`` or ``deferred`` (depending
              on whether the latest sprint_cut placed it in or after the
              cut). Pre-cut, all stories are labelled ``deferred`` —
              tests don't pin a specific choice here.
            * lifecycle state from ``state["story_states"]``
        Stories render in sequence-ascending order (derive_state already
        sorts the backlog).

    Returns:
        str: rendered snapshot.
    """
    state = derive_state()

    if state["active_iteration"] is None:
        return "no active iteration"

    iteration_id = state["active_iteration"].get("iteration_id")
    backlog = state["story_backlog"]
    story_states = state["story_states"]

    # Find the LATEST sprint_cut entry's in_sprint_story_ids so per-story
    # membership labels can be derived. derive_state stores only the cut
    # position int; we need the actual id set for label rendering.
    in_sprint_ids: set = set()
    cut_seen = False
    for entry in read_entries():
        if entry.get("type") == "sprint_cut":
            cut_seen = True
            in_sprint_ids = set(entry.get("in_sprint_story_ids", []) or [])

    lines: list = [f"iteration: {iteration_id}"]

    if not backlog:
        lines.append("no story backlog")
    else:
        for s in backlog:
            sid = s.get("story_id")
            seq = s.get("sequence")
            lifecycle = story_states.get(sid, "planned")
            if cut_seen:
                membership = "in-sprint" if sid in in_sprint_ids else "deferred"
            else:
                # Pre-cut: tests don't pin a specific label. Mark all
                # stories deferred by default — none are formally in-sprint
                # until sprint_cut runs.
                membership = "deferred"
            lines.append(
                f"  [{seq}] {sid} {membership} {lifecycle}"
            )

    return "\n".join(lines) + "\n"


def aggregate_requirements(state: dict) -> dict:
    """Aggregate story lifecycle states up to a per-requirement status.

    Story 17 contract:

      - Pure function: never calls `_append_entry`, never calls
        `read_entries`. Operates on the `state` argument only — the dict
        shape produced by `derive_state()`. Two calls produce equal results
        on the same input; mutating the returned dict does not affect a
        subsequent call; the input dict is not mutated.

      - Inputs:
            state["active_iteration"]: dict or None
                  iteration_id + requirements (list of {requirement_id})
            state["story_backlog"]: list[dict]
                  each story dict carries `story_id` and `requirement_ids`
            state["story_states"]: dict[story_id -> lifecycle_state]

      - Returns dict[requirement_id -> status] where status is one of
        `"accepted"`, `"rejected"`, or `"partial"`.

      - Rules:
            * `accepted` — every story rolling up to the requirement is in
              lifecycle state `accepted`.
            * `rejected` — any story rolling up to the requirement is in
              `rejected` OR `force_closed`. Wins over accepted/partial.
            * `partial` — mixed states without triggering rejected (some
              accepted + some in-flight, or all in-flight).

      - Raises `AggregateError` (a ValueError subclass) when:
            * `state["active_iteration"]` is None — operator can't
              aggregate against nothing. Message mentions "no active
              iteration".
            * Any requirement_id declared in the iteration has no story
              rolling up to it. Message names every orphan id.
    """
    # --- No active iteration --------------------------------------------
    active = state.get("active_iteration")
    if active is None:
        raise AggregateError(
            "no active iteration; ingest a handoff and decompose before "
            "aggregating requirements"
        )

    # --- Collect declared requirement_ids in iteration-declared order ---
    declared_reqs = []
    seen = set()
    for r in active.get("requirements", []) or []:
        if not isinstance(r, dict):
            continue
        rid = r.get("requirement_id")
        if rid is None or rid in seen:
            continue
        declared_reqs.append(rid)
        seen.add(rid)

    backlog = state.get("story_backlog", []) or []
    story_states = state.get("story_states", {}) or {}

    # --- Build req_id -> list of story lifecycle states. Multi-requirement
    # stories contribute to every requirement they roll up to.
    req_to_states: dict = {rid: [] for rid in declared_reqs}
    for s in backlog:
        sid = s.get("story_id")
        rids = s.get("requirement_ids", []) or []
        lifecycle = story_states.get(sid, "planned")
        for rid in rids:
            # Only count contributions to declared requirements. Stories
            # carrying a requirement_id that isn't on the iteration are
            # not aggregated against (defense in depth — Story 10
            # validation already prevents this on the live path).
            if rid in req_to_states:
                req_to_states[rid].append(lifecycle)

    # --- Orphan check: declared requirement with zero rolling-up stories.
    orphans = [rid for rid in declared_reqs if not req_to_states[rid]]
    if orphans:
        # Stable order — preserves the iteration-declared order so the
        # operator sees the orphan(s) in the same sequence as the handoff.
        names = ", ".join(repr(rid) for rid in orphans)
        raise AggregateError(
            f"orphan requirement(s) with no story rolling up: {names}; "
            f"every iteration requirement must be covered by at least "
            f"one story (Story 10 validation should prevent this — this "
            f"check is defense in depth)"
        )

    # --- Apply the aggregation rule per requirement.
    result: dict = {}
    for rid in declared_reqs:
        states_for_req = req_to_states[rid]
        # Rejected rule short-circuits: any story rejected OR force_closed
        # → requirement is rejected.
        if any(
            s == "rejected" or s == "force_closed"
            for s in states_for_req
        ):
            result[rid] = "rejected"
            continue
        # Accepted rule: every story must be accepted.
        if all(s == "accepted" for s in states_for_req):
            result[rid] = "accepted"
            continue
        # Otherwise partial.
        result[rid] = "partial"

    return result


def close_iteration(
    closed_by: str = "operator",
    reason: Optional[str] = None,
) -> dict:
    """Close the active iteration: produce the close handoff JSON sidecar
    file AND append a single `iteration_close` log entry.

    Story 18 contract:

      - Reads state via `derive_state()`. Validation cascade — each failure
        raises `IterationCloseError`, no log write, no handoff file:
            * no active iteration
            * no story_backlog (decompose not yet run)
            * no sprint_cut yet
            * one-or-more in-sprint stories in a non-terminal state. The
              error message names every offender (story_id + current state).

      - On success: calls `aggregate_requirements(state)`, writes a single
        `close_handoff_<iteration_id>.json` file at `LOG_PATH.parent`, then
        appends a single `iteration_close` log entry via `build_entry` +
        `_append_entry`. Returns the appended entry dict.

      - Handoff file contents:
            {
              "iteration_id":          "<id>",
              "iteration_goal":        "<copied from iteration_open>",
              "per_requirement_status": {"req-1": "accepted", ...},
              "stories": [
                {"story_id", "sequence", "title",
                 "requirement_ids", "outcome"},
                ...
              ],
              "closed_at": "<ISO 8601>",
            }

      - iteration_close entry shape:
            {
              "id", "type": "iteration_close", "timestamp" (auto-stamped),
              "iteration_id":           "<id>",
              "handoff_file_path":      "<absolute string>",
              "per_requirement_status": {"req-1": "accepted", ...},
              "closed_by":              "operator" (default),
              "reason":                 None (default),
              "accepted_count":         <int>,
              "rejected_count":         <int>,
              "force_closed_count":     <int>,
            }

      - Failure invariant: log.jsonl is byte-for-byte unchanged AND no
        handoff JSON file appears on every failure path.

      - Story 19 reuse: `closed_by` and `reason` are kwargs with defaults
        ("operator", None) so Story 19's force-close can call
        `close_iteration(closed_by="force-close", reason="<text>")` without
        breaking change.
    """
    # --- Replay state (pure read; no log write). ---
    state = derive_state()

    # --- Validation cascade ---
    if state["active_iteration"] is None:
        raise IterationCloseError(
            "no active iteration; nothing to close"
        )

    backlog = state["story_backlog"]
    if not backlog:
        raise IterationCloseError(
            "no story backlog yet; run decompose before closing the "
            "iteration"
        )

    if state["sprint_cut"] is None:
        raise IterationCloseError(
            "no sprint_cut yet; cut the sprint before closing the "
            "iteration"
        )

    # --- Find the LATEST sprint_cut entry's in_sprint_story_ids, plus the
    # iteration_goal from the matching iteration_open entry. derive_state
    # doesn't carry either, so do one targeted log scan.
    iteration_id = state["active_iteration"].get("iteration_id")
    in_sprint_ids: list = []
    iteration_goal: Optional[str] = None
    for entry in read_entries():
        etype = entry.get("type")
        if etype == "iteration_open" and entry.get("iteration_id") == iteration_id:
            iteration_goal = entry.get("iteration_goal")
        elif etype == "sprint_cut":
            in_sprint_ids = entry.get("in_sprint_story_ids", []) or []

    # --- Gate: every in-sprint story must be in a terminal state. ---
    story_states = state["story_states"]
    non_terminal = []
    for sid in in_sprint_ids:
        cur = story_states.get(sid, "planned")
        if cur not in _TERMINAL_STATES:
            non_terminal.append((sid, cur))

    if non_terminal:
        details = ", ".join(
            f"{sid!r} (state={state_name!r})"
            for sid, state_name in non_terminal
        )
        raise IterationCloseError(
            f"cannot close iteration {iteration_id!r}: the following "
            f"in-sprint stories are still non-terminal: {details}; every "
            f"in-sprint story must be accepted, rejected, or force-closed "
            f"before close"
        )

    # --- Aggregate per-requirement status (Story 17). Pure function over
    # the state dict — no log read, no log write. May raise AggregateError
    # if the iteration has orphan requirements; surface those as a
    # close-domain failure so the CLI maps to EXIT_CLOSE.
    try:
        per_requirement_status = aggregate_requirements(state)
    except AggregateError as e:
        raise IterationCloseError(
            f"cannot close iteration {iteration_id!r}: aggregation failed "
            f"({e})"
        ) from e

    # --- Compute counts over the in-sprint stories. ---
    accepted_count = 0
    rejected_count = 0
    force_closed_count = 0
    for sid in in_sprint_ids:
        cur = story_states.get(sid, "planned")
        if cur == "accepted":
            accepted_count += 1
        elif cur == "rejected":
            rejected_count += 1
        elif cur == "force_closed":
            force_closed_count += 1

    # --- Build the handoff JSON payload. Stories list mirrors the
    # in-sprint backlog (stories not in the sprint are excluded — the
    # close handoff documents what was attempted in this sprint).
    backlog_by_id = {s["story_id"]: s for s in backlog}
    handoff_stories: list = []
    for sid in in_sprint_ids:
        s = backlog_by_id.get(sid)
        if s is None:
            # Should not happen: in_sprint_ids comes from a sprint_cut entry
            # whose ids were minted from the same backlog. Defense in depth.
            continue
        outcome = story_states.get(sid, "planned")
        handoff_stories.append({
            "story_id": s["story_id"],
            "sequence": s["sequence"],
            "title": s.get("title", ""),
            "requirement_ids": list(s.get("requirement_ids", []) or []),
            "outcome": outcome,
        })

    closed_at = _dt.datetime.now().astimezone().isoformat()
    handoff_payload = {
        "iteration_id": iteration_id,
        "iteration_goal": iteration_goal,
        "per_requirement_status": dict(per_requirement_status),
        "stories": handoff_stories,
        "closed_at": closed_at,
    }

    # --- Determine the handoff file path. Anchor at LOG_PATH.parent so
    # tests' monkeypatching of sm.LOG_PATH redirects sidecar lookup the
    # same way it redirects log lookup. Make the path absolute.
    handoff_path = Path(LOG_PATH).resolve().parent / (
        f"close_handoff_{iteration_id}.json"
    )

    # --- Serialize + write the handoff JSON file. Use Path.write_text so
    # the codebase's "no write-mode open()" invariant stays clean (the only
    # write-mode open in sm.py is the _append_entry append). Path.write_text
    # delegates to the lower-level stdlib write API, not Python's open().
    # LOG_PATH.parent must already exist for any prior log write to have
    # succeeded; if not, write_text raises FileNotFoundError and the log
    # remains untouched (no _append_entry call has happened yet).
    handoff_text = json.dumps(
        handoff_payload, ensure_ascii=False, indent=2
    ) + "\n"
    handoff_path.write_text(handoff_text, encoding="utf-8")

    # --- Build + append the iteration_close log entry. The handoff file
    # has already been written; if _append_entry fails, the handoff file
    # is orphaned — but on the happy path tested here, the log write is
    # the last step.
    entry = build_entry(
        "iteration_close",
        {
            "iteration_id": iteration_id,
            "handoff_file_path": str(handoff_path),
            "per_requirement_status": dict(per_requirement_status),
            "closed_by": closed_by,
            "reason": reason,
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "force_closed_count": force_closed_count,
        },
    )
    _append_entry(entry)
    return entry


# ---------------------------------------------------------------------------
# Story 19 — force-close. Public `force_close(reason)` transitions every
# non-terminal in-sprint story to `force_closed` (bypassing Story 13's
# narrow operator-only writer graph), then calls close_iteration with
# closed_by="force-close" + reason to produce the close-handoff entry and
# sidecar via Story 18's path.
# ---------------------------------------------------------------------------


def _force_close_story(story_id: str, current_state: str, reason: str) -> dict:
    """Private writer for force_closed transitions — bypasses Story 13's
    narrow operator-only transition graph (which does NOT include
    force_closed). The `_VALID_TRANSITIONS` graph (used by `derive_state`
    replay) DOES allow every non-terminal -> force_closed, so the resulting
    state_change entry replays cleanly.

    Appends a single `story_state_change` entry. Returns the appended dict.
    No validation here — `force_close` owns the gate.
    """
    entry = build_entry(
        "story_state_change",
        {
            "story_id": story_id,
            "from_state": current_state,
            "to_state": "force_closed",
            "notes": f"force-close: {reason}",
        },
    )
    _append_entry(entry)
    return entry


def force_close(reason: str) -> dict:
    """Force-close the active iteration with a mandatory operator-supplied
    `reason`.

    Story 19 contract:

      - Validation (raises BEFORE any log write; log byte-for-byte
        unchanged on every failure path; no handoff JSON file appears):
            * non-string reason -> TypeError
            * empty / whitespace-only reason -> ForceCloseError
            * no active iteration -> ForceCloseError
            * no story_backlog -> ForceCloseError
            * no sprint_cut yet -> ForceCloseError

      - On success: every in-sprint story whose current lifecycle state is
        NOT in {accepted, rejected, force_closed} receives a single
        `story_state_change` entry transitioning it to `force_closed`
        (via `_force_close_story`). Already-terminal stories are NOT
        re-transitioned. Then `close_iteration(closed_by="force-close",
        reason=<verbatim>)` runs to produce the handoff sidecar and the
        iteration_close log entry.

      - Returns the `iteration_close` entry dict (the final appended log
        entry).

      - Failure invariant: when validation fails, neither
        `_force_close_story` nor `close_iteration` is invoked, so no log
        write or handoff file occurs.
    """
    # --- Reason type check (TypeError, before any state read). ---
    if not isinstance(reason, str):
        raise TypeError(
            f"reason must be a string, got {reason.__class__.__name__}"
        )

    # --- Reason emptiness check (ForceCloseError, before any state read).
    if not reason or not reason.strip():
        raise ForceCloseError(
            "reason must be non-empty and not whitespace-only"
        )

    # --- Replay state (pure read; no log write). ---
    state = derive_state()

    # --- Pre-condition cascade. Each raises ForceCloseError with no log
    # write and no handoff file. Order matches close_iteration so the
    # operator sees consistent error semantics across the two paths.
    if state["active_iteration"] is None:
        raise ForceCloseError(
            "no active iteration; nothing to force-close"
        )

    if not state["story_backlog"]:
        raise ForceCloseError(
            "no story backlog yet; run decompose before force-closing the "
            "iteration"
        )

    if state["sprint_cut"] is None:
        raise ForceCloseError(
            "no sprint_cut yet; cut the sprint before force-closing the "
            "iteration"
        )

    # --- Find the LATEST sprint_cut entry's in_sprint_story_ids. The
    # derive_state dict carries the cut position but not the id list, so
    # do a targeted log scan.
    in_sprint_ids: list = []
    for entry in read_entries():
        if entry.get("type") == "sprint_cut":
            in_sprint_ids = entry.get("in_sprint_story_ids", []) or []

    # --- Identify non-terminal in-sprint stories. Already-terminal stories
    # (accepted / rejected / force_closed) are skipped — no duplicate
    # state_change entry is written for them.
    story_states = state["story_states"]
    non_terminal: list = []
    for sid in in_sprint_ids:
        cur = story_states.get(sid, "planned")
        if cur not in _TERMINAL_STATES:
            non_terminal.append((sid, cur))

    # --- Transition each non-terminal story to force_closed. After this
    # loop the log carries one new story_state_change entry per non-
    # terminal story; close_iteration's own replay will pick them up and
    # see every in-sprint story as terminal.
    for sid, current in non_terminal:
        _force_close_story(sid, current, reason)

    # --- Hand off to close_iteration to produce the iteration_close entry
    # and the handoff JSON sidecar. closed_by + reason flow through.
    return close_iteration(closed_by="force-close", reason=reason)


# ---------------------------------------------------------------------------
# Story 23 — TestWriter -> Coder -> Reviewer execution pipeline.
#
# `execute(story_id, spawn_test_writer, spawn_coder, spawn_reviewer)` drives
# the full per-story build pipeline:
#
#   Step 1: planned -> in_progress             (story_state_change)
#   Step 2: spawn_test_writer(spec, story)     -> testwriter_output entry
#   Step 3: spawn_coder(spec, story, tc)       -> coder_output entry
#   Step 4: in_progress -> in_review           (story_state_change)
#   Step 5: spawn_reviewer(spec, story, tc, ic) -> reviewer_approval entry
#   Step 6: in_review -> accepted | rejected   (story_state_change)
#
# Each spawn callable is injected — Iter 1 ships a stub-driven path; real
# agent integration arrives in Iter 2 (the NotImplementedError default
# default makes that explicit).
# ---------------------------------------------------------------------------


def execute(
    story_id: str,
    spawn_test_writer: Optional[Callable] = None,
    spawn_coder: Optional[Callable] = None,
    spawn_reviewer: Optional[Callable] = None,
) -> dict:
    """Drive one in-sprint story through the full TestWriter -> Coder ->
    Reviewer build pipeline.

    Story 23 contract:

      - All three `spawn_*` kwargs default to `None`. If ANY of them is
        `None`, raises `NotImplementedError` mentioning Iter 2. No state read,
        no log write.

      - Type validation: `story_id` must be `str`. Non-string raises
        `TypeError`. No log write, no spawn callable invoked.

      - State validation (all raise `ExecuteError`, no log write, no spawn
        callable invoked):
            * no active iteration
            * no sprint_cut yet
            * `story_id` not in the active sprint (unknown OR deferred)
            * current state not in {planned, in_progress}

      - On valid input the pipeline runs in fixed order:
            Step 1 (only if current state is planned):
                transition planned -> in_progress
            Step 2:
                spawn_test_writer(role_spec_path, story) -> test_code (str)
                append `testwriter_output` entry carrying story_id,
                role_spec_path, role_spec_hash, output (test_code)
            Step 3:
                spawn_coder(role_spec_path, story, test_code) -> impl_code (str)
                append `coder_output` entry carrying story_id, role_spec_path,
                role_spec_hash, output (impl_code)
            Step 4:
                transition in_progress -> in_review
            Step 5:
                spawn_reviewer(role_spec_path, story, test_code, impl_code)
                    -> {"approved": bool, "test_result": str}
            Step 6:
                If approved is True AND test_result.strip() is non-empty:
                    record_review(story_id, True, test_result)
                    transition in_review -> accepted
                Else:
                    record_review(story_id, False, test_result) if non-empty,
                    else write a placeholder reviewer_approval entry directly
                    so the audit trail is honest about the reviewer's call.
                    transition in_review -> rejected

      - Returns the FINAL `story_state_change` entry (accepted or rejected).

      - Failure invariants:
            * Pre-spawn validation failures leave the log byte-for-byte
              unchanged and never invoke any spawn callable.
            * Post-spawn partial failures leave already-written entries in
              place (truthful audit trail).
    """
    # --- Default-spawn check FIRST (before any type / state validation).
    # Iter 2 Stories 7 + 8 inverted spawn_test_writer's AND spawn_coder's
    # defaults: None now routes to the real
    # `_default_execute_test_writer_spawn` / `_default_execute_coder_spawn`
    # (both resolved at call time via sys.modules so monkeypatches take
    # effect). spawn_reviewer remains `None`-defaults-to-
    # NotImplementedError until Story 9 ships. NotImplementedError must
    # fire regardless of state, so callers exploring "what happens if I
    # just call execute()" get the right signal without leaking log
    # entries.
    if spawn_reviewer is None:
        raise NotImplementedError(
            "real agent integration ships in Iter 2 — pass "
            "spawn_reviewer for testing/manual ops "
            "(reviewer ships in Story 9)"
        )
    import sys as _sys
    if spawn_test_writer is None:
        # Iter 2 Story 7: fall back to the real default. Bind from the
        # module so monkeypatches in tests (`monkeypatch.setattr(sm,
        # "_default_execute_test_writer_spawn", ...)`) take effect.
        spawn_test_writer = (
            _sys.modules[__name__]._default_execute_test_writer_spawn
        )
    if spawn_coder is None:
        # Iter 2 Story 8: fall back to the real default. Bind from the
        # module so monkeypatches in tests (`monkeypatch.setattr(sm,
        # "_default_execute_coder_spawn", ...)`) take effect.
        spawn_coder = (
            _sys.modules[__name__]._default_execute_coder_spawn
        )

    # --- Type validation: story_id must be str (before any state read). ---
    if not isinstance(story_id, str):
        raise TypeError(
            f"story_id must be a string, got "
            f"{story_id.__class__.__name__}"
        )

    # --- Replay state (pure read; no log write). ---
    state = derive_state()

    if state["active_iteration"] is None:
        raise ExecuteError(
            "no active iteration; ingest a handoff first before executing "
            "a story"
        )

    if state["sprint_cut"] is None:
        raise ExecuteError(
            "no active sprint (no sprint_cut entry yet); cut the sprint "
            "before executing a story"
        )

    # --- Find the LATEST sprint_cut entry's in_sprint_story_ids. ---
    in_sprint_ids: list = []
    for entry in read_entries():
        if entry.get("type") == "sprint_cut":
            in_sprint_ids = entry.get("in_sprint_story_ids", []) or []

    if story_id not in in_sprint_ids:
        raise ExecuteError(
            f"story_id {story_id!r} is not in the active sprint; only "
            f"in-sprint stories may be executed"
        )

    # --- Look up the full story dict from the backlog. Membership in
    # in_sprint_ids guarantees the story exists in the backlog.
    story_dict: Optional[dict] = None
    for s in state["story_backlog"]:
        if s.get("story_id") == story_id:
            story_dict = s
            break
    if story_dict is None:
        raise ExecuteError(
            f"story {story_id!r} not found in story backlog"
        )

    # --- Current-state gate: only planned / in_progress are executable. ---
    current_state = state["story_states"].get(story_id, "planned")
    if current_state not in ("planned", "in_progress"):
        raise ExecuteError(
            f"story {story_id!r} is in state {current_state!r}; execute "
            f"requires the story to be in 'planned' or 'in_progress'"
        )

    # ----------------------------------------------------------------------
    # All validation passed. From here on, the pipeline runs and writes
    # entries to the log. Partial failures leave written entries in place.
    # ----------------------------------------------------------------------

    # --- Step 1: planned -> in_progress (skip if already in_progress). ---
    if current_state == "planned":
        transition_story(
            story_id,
            "in_progress",
            notes="execute: starting pipeline",
        )

    # --- Step 2: TestWriter. ---
    tw_path = resolve_role_spec("test_writer")
    tw_hash = _role_spec_hash("test_writer")
    test_code = spawn_test_writer(str(tw_path), story_dict)
    tw_entry = build_entry(
        "testwriter_output",
        {
            "story_id": story_id,
            "role_spec_path": str(tw_path),
            "role_spec_hash": tw_hash,
            "output": test_code,
        },
    )
    _append_entry(tw_entry)

    # --- Step 3: Coder. ---
    coder_path = resolve_role_spec("coder")
    coder_hash = _role_spec_hash("coder")
    impl_code = spawn_coder(str(coder_path), story_dict, test_code)
    coder_entry = build_entry(
        "coder_output",
        {
            "story_id": story_id,
            "role_spec_path": str(coder_path),
            "role_spec_hash": coder_hash,
            "output": impl_code,
        },
    )
    _append_entry(coder_entry)

    # --- Step 4: in_progress -> in_review. ---
    transition_story(
        story_id,
        "in_review",
        notes="execute: pipeline complete, in review",
    )

    # --- Step 5: Reviewer. ---
    reviewer_path = resolve_role_spec("reviewer")
    reviewer_result = spawn_reviewer(
        str(reviewer_path), story_dict, test_code, impl_code
    )

    approved_raw = reviewer_result.get("approved") if isinstance(
        reviewer_result, dict
    ) else None
    test_result_raw = reviewer_result.get("test_result") if isinstance(
        reviewer_result, dict
    ) else None

    approved = bool(approved_raw)
    test_result_str = test_result_raw if isinstance(
        test_result_raw, str
    ) else ""

    # Defense in depth: empty / whitespace-only test_result routes to
    # rejected even if the reviewer said approved=True. The accept gate in
    # transition_story enforces the same rule on the read side.
    if not test_result_str.strip():
        approved = False

    # --- Step 6: record review + final transition. ---
    if approved:
        # Happy approve path: record_review writes the reviewer_approval
        # entry that satisfies Story 15's accept gate, then transition
        # in_review -> accepted.
        record_review(story_id, True, test_result_str)
        final_entry = transition_story(
            story_id,
            "accepted",
            notes="execute: reviewer approved",
        )
    else:
        # Reject path: write a reviewer_approval entry capturing the
        # reviewer's actual call (approved bool + test_result text), then
        # transition in_review -> rejected. record_review enforces the
        # non-empty test_result rule; if the reviewer returned empty /
        # whitespace text we fall back to a synthetic placeholder so the
        # audit trail stays honest about the reviewer's verdict.
        if test_result_str.strip():
            try:
                record_review(story_id, False, test_result_str)
            except Exception:
                # Defense in depth: don't block rejection on the
                # reviewer_approval write failing.
                pass
        else:
            placeholder = build_entry(
                "reviewer_approval",
                {
                    "story_id": story_id,
                    "approved": False,
                    "test_result": test_result_str,
                },
            )
            _append_entry(placeholder)
        final_entry = transition_story(
            story_id,
            "rejected",
            notes="execute: reviewer rejected",
        )

    return final_entry


# ---------------------------------------------------------------------------
# CLI surface — `python -m sm <command> <args...>`
# ---------------------------------------------------------------------------

# Story 6 — documented CLI exit codes. Exposed so callers and docs can
# reference them by name. Every error class maps to exactly one code,
# distinct from every other class and from success (0).
EXIT_OK = 0
EXIT_OTHER = 1
EXIT_PATH = 2
EXIT_JSON = 3
EXIT_SHAPE = 4
EXIT_DUP_ID = 5
EXIT_SINGLE_ACTIVE = 6
EXIT_UNKNOWN_REQ = 7
EXIT_SPRINT_CUT = 8
EXIT_TRANSITION = 9
# Story 18 — distinct exit code for iteration-close failures.
EXIT_CLOSE = 11
# Iter 2 Story 2 — distinct exit code for agent-side errors (missing
# ANTHROPIC_API_KEY, downstream SDK auth failure, etc.). LOCKED_DECISION 7.
EXIT_AGENT_ERROR = 12


_HELP_TEXT = """\
usage: python -m sm <command> [args...]

Commands:
  ingest <path>    Ingest a PO Tool iteration_open handoff JSON file.

Exit codes (return codes) for `ingest`:
  0  success
  1  unexpected / other error
  2  path error           (file not found, path is a directory)
  3  JSON parse error     (malformed or empty handoff JSON)
  4  shape error          (handoff missing/wrong-typed fields, bad reqs)
  5  duplicate iteration_id (id was used by a prior iteration_open,
                             open or closed)
  6  single-active-iteration violation (another iteration is open)
"""


def _cli_main(argv: list) -> int:
    """Dispatch CLI subcommands. Returns the exit code.

    Story 6 — distinct exit codes per error class:
        0 success, 1 other, 2 path, 3 json, 4 shape, 5 dup-id,
        6 single-active.
    """
    global LOG_PATH

    import os
    import sys as _sys

    if len(argv) < 1:
        print(_HELP_TEXT, file=_sys.stderr)
        return EXIT_OTHER

    cmd = argv[0]

    if cmd in ("--help", "-h", "help"):
        print(_HELP_TEXT)
        return EXIT_OK

    if cmd == "decompose":
        # Honor SM_LOG_PATH for hermetic subprocess testing.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        # Iter 2 Story 2 — gate the default real-agent path on a resolved
        # API key. Failure here exits 12 (EXIT_AGENT_ERROR), prints the
        # actionable message verbatim to stderr, and never imports the SDK.
        try:
            resolve_api_key()
        except MissingAPIKeyError as e:
            print(str(e), file=_sys.stderr)
            return EXIT_AGENT_ERROR

        try:
            entry = decompose()
        except MissingAPIKeyError as e:
            print(str(e), file=_sys.stderr)
            return EXIT_AGENT_ERROR
        except NotImplementedError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except DecomposeOutputParseError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_JSON
        except DecomposeOutputShapeError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_SHAPE
        except DecomposeUnknownRequirementError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_UNKNOWN_REQ
        except ValueError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        print(entry["id"])
        return EXIT_OK

    if cmd == "sprint-cut":
        if len(argv) >= 2 and argv[1] in ("--help", "-h"):
            print(_HELP_TEXT)
            return EXIT_OK
        if len(argv) != 2:
            print(
                "usage: python -m sm sprint-cut <N>", file=_sys.stderr
            )
            print(_HELP_TEXT, file=_sys.stderr)
            return EXIT_OTHER

        # Honor SM_LOG_PATH for hermetic subprocess testing.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        # Parse N — invalid integer string -> EXIT_SPRINT_CUT (recognized
        # command, validation failure path).
        try:
            n = int(argv[1])
        except (ValueError, TypeError) as e:
            print(f"error: invalid N {argv[1]!r}: {e}", file=_sys.stderr)
            return EXIT_SPRINT_CUT

        try:
            entry = sprint_cut(n)
        except SprintCutError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_SPRINT_CUT
        except TypeError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        print(entry["id"])
        return EXIT_OK

    if cmd == "ingest":
        if len(argv) >= 2 and argv[1] in ("--help", "-h"):
            print(_HELP_TEXT)
            return EXIT_OK
        if len(argv) != 2:
            print("usage: python -m sm ingest <path>", file=_sys.stderr)
            print(_HELP_TEXT, file=_sys.stderr)
            return EXIT_OTHER

        # Honor SM_LOG_PATH env var if set, so subprocess CLI tests stay
        # hermetic and the package's real log isn't touched.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        try:
            entry = ingest(argv[1])
        except IngestDuplicateError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_DUP_ID
        except IngestActiveError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_SINGLE_ACTIVE
        except IngestShapeError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_SHAPE
        except IngestJSONError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_JSON
        except (FileNotFoundError, IsADirectoryError) as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_PATH
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        print(entry["iteration_id"])
        return EXIT_OK

    # Story 14 — per-story lifecycle subcommands.
    # Each routes to `transition_story(story_id, <target>)` with a fixed
    # target state. The four are isomorphic: same shape of arg validation,
    # same exception → exit code mapping, same success/failure surface.
    _LIFECYCLE_TARGETS = {
        "start": "in_progress",
        "submit": "in_review",
        "accept": "accepted",
        "reject": "rejected",
    }

    if cmd in _LIFECYCLE_TARGETS:
        target_state = _LIFECYCLE_TARGETS[cmd]

        if len(argv) >= 2 and argv[1] in ("--help", "-h"):
            print(_HELP_TEXT)
            return EXIT_OK
        if len(argv) != 2:
            print(
                f"usage: python -m sm {cmd} <story_id>", file=_sys.stderr
            )
            return EXIT_OTHER

        story_id = argv[1]

        # Honor SM_LOG_PATH for hermetic subprocess testing.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        try:
            transition_story(story_id, target_state)
        except StoryTransitionError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_TRANSITION
        except (TypeError, ValueError) as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        print(f"story {story_id} -> {target_state}")
        return EXIT_OK

    # Story 15 — record-review subcommand. Args:
    #   record-review <story_id> --approved <true|false> --test-result <text>
    # Exit codes: EXIT_OK on success; EXIT_OTHER on bad args (missing flags,
    # unparseable bool); EXIT_TRANSITION on any record_review failure
    # (TypeError or ReviewError) — distinct from "unknown command", so the
    # subcommand is recognized.
    if cmd == "record-review":
        if len(argv) >= 2 and argv[1] in ("--help", "-h"):
            print(_HELP_TEXT)
            return EXIT_OK

        # Honor SM_LOG_PATH for hermetic subprocess testing.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        # Parse argv: positional story_id, then --approved <bool> and
        # --test-result <str> as flag pairs (order-insensitive).
        if len(argv) < 2:
            print(
                "usage: python -m sm record-review <story_id> "
                "--approved <true|false> --test-result <text>",
                file=_sys.stderr,
            )
            return EXIT_OTHER

        story_id = argv[1]
        approved_raw: Optional[str] = None
        test_result: Optional[str] = None

        i = 2
        while i < len(argv):
            tok = argv[i]
            if tok == "--approved":
                if i + 1 >= len(argv):
                    print(
                        "error: --approved requires a value (true|false)",
                        file=_sys.stderr,
                    )
                    return EXIT_OTHER
                approved_raw = argv[i + 1]
                i += 2
            elif tok == "--test-result":
                if i + 1 >= len(argv):
                    print(
                        "error: --test-result requires a value",
                        file=_sys.stderr,
                    )
                    return EXIT_OTHER
                test_result = argv[i + 1]
                i += 2
            else:
                print(
                    f"error: unexpected argument {tok!r}",
                    file=_sys.stderr,
                )
                return EXIT_OTHER

        if approved_raw is None:
            print(
                "error: --approved is required (true|false)",
                file=_sys.stderr,
            )
            return EXIT_OTHER
        if test_result is None:
            print(
                "error: --test-result is required",
                file=_sys.stderr,
            )
            return EXIT_OTHER

        # Map "true"/"false" (case-insensitive) to a real bool. Anything
        # else is a recognized failure (not "unknown command").
        if approved_raw.lower() == "true":
            approved = True
        elif approved_raw.lower() == "false":
            approved = False
        else:
            print(
                f"error: --approved must be 'true' or 'false', got "
                f"{approved_raw!r}",
                file=_sys.stderr,
            )
            return EXIT_TRANSITION

        try:
            entry = record_review(story_id, approved, test_result)
        except ReviewError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_TRANSITION
        except TypeError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_TRANSITION
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        print(entry["id"])
        return EXIT_OK

    # Story 16 — status read-only query subcommand. No args, always exits 0.
    if cmd == "status":
        if len(argv) >= 2 and argv[1] in ("--help", "-h"):
            print(_HELP_TEXT)
            return EXIT_OK

        # Honor SM_LOG_PATH for hermetic subprocess testing.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        out = status()
        # `status()` returns a string. Print verbatim — read-only query
        # never fails on "nothing to report".
        print(out, end="" if out.endswith("\n") else "\n")
        return EXIT_OK

    # Story 18 — close iteration subcommand. No args; produces the close
    # handoff JSON sidecar + appends a single iteration_close log entry.
    # Exit codes:
    #   EXIT_OK          on success
    #   EXIT_CLOSE       on IterationCloseError (validation failure)
    #   EXIT_OTHER       on bad args (extra positional) or unexpected errors
    if cmd == "close":
        if len(argv) >= 2 and argv[1] in ("--help", "-h"):
            print(_HELP_TEXT)
            return EXIT_OK
        if len(argv) != 1:
            print(
                "usage: python -m sm close (no arguments)",
                file=_sys.stderr,
            )
            return EXIT_OTHER

        # Honor SM_LOG_PATH for hermetic subprocess testing.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        try:
            entry = close_iteration()
        except IterationCloseError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_CLOSE
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        print(entry["id"])
        return EXIT_OK

    # Story 19 — force-close iteration subcommand. Args:
    #   force-close --reason <text>
    # Exit codes:
    #   EXIT_OK     on success
    #   EXIT_CLOSE  on ForceCloseError (validation failure — empty reason,
    #               no active iter, no backlog, no cut). Force-close is a
    #               close variant; reuses EXIT_CLOSE.
    #   EXIT_OTHER  on missing flag / bad args / unexpected errors
    if cmd == "force-close":
        if len(argv) >= 2 and argv[1] in ("--help", "-h"):
            print(_HELP_TEXT)
            return EXIT_OK

        # Honor SM_LOG_PATH for hermetic subprocess testing.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        # Parse --reason <text>. No positional form; the flag is the only
        # path so the empty case is unambiguous.
        reason: Optional[str] = None
        i = 1
        while i < len(argv):
            tok = argv[i]
            if tok == "--reason":
                if i + 1 >= len(argv):
                    print(
                        "error: --reason requires a value",
                        file=_sys.stderr,
                    )
                    return EXIT_OTHER
                reason = argv[i + 1]
                i += 2
            else:
                print(
                    f"error: unexpected argument {tok!r}",
                    file=_sys.stderr,
                )
                return EXIT_OTHER

        if reason is None:
            print(
                "usage: python -m sm force-close --reason <text>",
                file=_sys.stderr,
            )
            return EXIT_OTHER

        try:
            entry = force_close(reason)
        except ForceCloseError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_CLOSE
        except TypeError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        print(entry["id"])
        return EXIT_OK

    # Story 23 — execute pipeline subcommand. Args:
    #   execute <story_id>
    # No injection point for spawn callables from the CLI in Iter 1 — the
    # default-spawn path raises NotImplementedError and the CLI maps it to
    # EXIT_OTHER. Real-agent injection ships in Iter 2.
    # Exit codes:
    #   EXIT_OK         on accepted (happy approve path)
    #   EXIT_TRANSITION on rejected (a valid completion that isn't accept)
    #   EXIT_OTHER      on validation failure / NotImplementedError / other
    if cmd == "execute":
        if len(argv) >= 2 and argv[1] in ("--help", "-h"):
            print(_HELP_TEXT)
            return EXIT_OK
        if len(argv) != 2:
            print(
                "usage: python -m sm execute <story_id>",
                file=_sys.stderr,
            )
            return EXIT_OTHER

        story_id = argv[1]

        # Honor SM_LOG_PATH for hermetic subprocess testing.
        env_log = os.environ.get("SM_LOG_PATH")
        if env_log:
            LOG_PATH = Path(env_log)

        try:
            final_entry = execute(story_id)
        except NotImplementedError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except ExecuteError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except TypeError as e:
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER
        except Exception as e:  # noqa: BLE001 — catch-all
            print(f"error: {e}", file=_sys.stderr)
            return EXIT_OTHER

        # Map the final state to an exit code. Accepted -> success;
        # rejected -> EXIT_TRANSITION (a valid completion, but not "accept").
        to_state = (
            final_entry.get("to_state")
            if isinstance(final_entry, dict)
            else None
        )
        if to_state == "accepted":
            print(f"story {story_id} -> accepted")
            return EXIT_OK
        if to_state == "rejected":
            print(f"story {story_id} -> rejected")
            return EXIT_TRANSITION
        # Any other terminal would be unexpected here; report it generically.
        print(f"story {story_id} -> {to_state!r}")
        return EXIT_OTHER

    print(f"unknown command: {cmd!r}", file=_sys.stderr)
    print(_HELP_TEXT, file=_sys.stderr)
    return EXIT_OTHER


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli_main(_sys.argv[1:]))
