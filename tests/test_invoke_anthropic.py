"""Iter 2 Story 5 — Provider seam: single Anthropic SDK invocation point.

This file pins the contract of `sm._invoke_anthropic(messages, model,
max_tokens, api_key) -> str` — the ONLY place in the sm-tool codebase
that imports or invokes `anthropic.Anthropic`. Every real-agent spawn
default (decompose, test_writer, coder, reviewer) routes its SDK call
through this single seam so that swapping providers in Iter 3 is a
refactor, not a rewrite.

Pinned clauses (verbatim from `iter2/Stories_v1.md`, Story 5):

  1. Exposes a single internal function
     `_invoke_anthropic(messages: list, model: str, max_tokens: int,
     api_key: str) -> str` that is the only call site in the codebase
     importing or invoking `anthropic.Anthropic` (or equivalent SDK
     client).
  2. All four spawn defaults (decompose, test_writer, coder, reviewer)
     call through this function — they shape messages and parse
     responses, but never import `anthropic` directly.
  3. Role-spec reading and message shaping live OUTSIDE this function;
     the function is SDK-shaped, not role-shaped.
  4. A grep for `import anthropic` or `from anthropic` in the codebase
     returns exactly one site.
  5. Function is unit-testable by mocking the SDK client at this single
     boundary (per ASSUMPTION 6).

CONTRACT INTERPRETATION (locked by TestWriter):

  - PRIVATE surface: name has a leading underscore (`_invoke_anthropic`)
    and is NOT listed in `sm.__all__`. The seam is an internal
    implementation detail — only the four spawn defaults call it,
    and they live in the same module.
  - Signature accepts (messages, model, max_tokens, api_key) as
    positional parameters (in that order) so callers may pass either
    positionally or by keyword. Return type is `str`.
  - Lazy import: `anthropic` is imported INSIDE the function body, not
    at module top level. This keeps `import sm` cheap (no SDK load until
    first invocation) AND makes mocking straightforward — tests inject
    a fake `anthropic` module into `sys.modules` BEFORE calling the
    function, and the lazy import finds the fake.
  - The function instantiates `anthropic.Anthropic(api_key=api_key)`
    and calls `client.messages.create(model=model,
    max_tokens=max_tokens, messages=messages)`.
  - Response extraction: the function returns
    `response.content[0].text` — the standard Anthropic Messages API
    shape. The seam trusts the SDK shape; malformed responses propagate
    their natural Python errors (IndexError, AttributeError) without
    wrapping. Callers (Stories 6-9) are responsible for wrapping into
    their role-specific typed errors per Story 4's pattern.
  - SDK exceptions (network failure, auth failure, rate-limit, etc.)
    propagate AS-IS without wrapping. This keeps Story 5 SDK-shaped,
    not role-shaped — callers wrap.
  - Type validation on inputs: `messages` must be a list, `model` and
    `api_key` must be strings, `max_tokens` must be an int. Non-
    conforming inputs raise `TypeError` BEFORE the SDK is loaded.
    This is the only behavioral wrapping the seam performs — it
    refuses bad arguments so a downstream AttributeError on the SDK
    client doesn't disguise a caller bug.
  - Single-import grep invariant: across `sm.py`, the substrings
    `import anthropic` and `from anthropic` total AT MOST ONE
    occurrence. Comments and docstrings that mention `anthropic` as
    a word (e.g. "see https://console.anthropic.com/") do NOT count —
    only real import statements.
  - Message round-trip: the `messages` list is passed to
    `client.messages.create` unchanged — no transformation, no copy,
    no shape mutation. Tests verify this via a spy on the fake client.

CRITICAL — tests must NOT make real API calls. Every test that
exercises the function body injects a fake `anthropic` module into
`sys.modules` via `monkeypatch.setitem` BEFORE the call. The lazy
import inside `_invoke_anthropic` finds the fake and never touches
the real SDK. A test-level guard (`_assert_no_real_anthropic`) is
applied in every fixture that triggers a call, refusing to run if
the real SDK has somehow been imported.

Every test below FAILS on first run — `_invoke_anthropic` does not
exist yet. The Coder implements it to drive this suite green.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import re
import sys
import types

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Fake Anthropic SDK — installed into sys.modules per-test
# ---------------------------------------------------------------------------
#
# These fakes mimic the shape of `anthropic.Anthropic` and the response
# returned by `client.messages.create(...)`. The fake response exposes
# `.content[0].text` exactly like the real SDK, so the seam can
# extract the text string without any awareness that the SDK is faked.
#
# `FakeAnthropicClient` records every constructor and `messages.create`
# call into `.calls`, so tests can verify round-trip parameters
# (model, max_tokens, messages, api_key) reached the SDK boundary
# exactly as passed.
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    """Minimal stand-in for an Anthropic content block. Carries `.text`."""

    def __init__(self, text: str = "fake response text"):
        self.text = text


class _FakeResponse:
    """Stand-in for the object returned by `client.messages.create(...)`.

    Mirrors the real Messages API: `.content` is a list of content
    blocks; each block has a `.text` attribute. The seam extracts
    `response.content[0].text`.
    """

    def __init__(self, text: str = "fake response text"):
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    """Stand-in for `client.messages` — the `.create` subobject. Records
    every call into `self.calls` as a dict of kwargs."""

    def __init__(self, response: _FakeResponse | None = None,
                 raise_exc: Exception | None = None):
        self._response = response or _FakeResponse()
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


class _FakeAnthropicClient:
    """Stand-in for `anthropic.Anthropic`. Records the `api_key` it was
    constructed with and exposes a `.messages` subobject that records
    every `.create(...)` call. Constructed by the seam on every call."""

    instances: list["_FakeAnthropicClient"] = []

    def __init__(self, api_key=None, **kwargs):
        self.api_key = api_key
        self.ctor_kwargs = kwargs
        self.messages = _FakeMessages()
        _FakeAnthropicClient.instances.append(self)


def _install_fake_anthropic(monkeypatch, response_text: str = "fake response text",
                            raise_exc: Exception | None = None
                            ) -> types.ModuleType:
    """Build a fake `anthropic` module and install it into
    `sys.modules`. Returns the module so tests can introspect the
    last-constructed client.

    `response_text` controls the `.text` returned by the fake
    `client.messages.create(...)`. `raise_exc`, if set, causes
    `.create(...)` to raise the given exception instead of returning a
    response — used by the SDK-exception-propagation tests.

    NOTE: clears `_FakeAnthropicClient.instances` so each test starts
    with a clean record.
    """
    _FakeAnthropicClient.instances = []

    class _BoundClient(_FakeAnthropicClient):
        def __init__(self, api_key=None, **kwargs):
            super().__init__(api_key=api_key, **kwargs)
            self.messages = _FakeMessages(
                response=_FakeResponse(response_text),
                raise_exc=raise_exc,
            )

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _BoundClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return fake_module


def _purge_anthropic_from_sys_modules():
    """Remove every `anthropic*` entry from `sys.modules`. Used by
    fixtures that need to observe lazy-import behavior — the seam must
    not have imported `anthropic` before its first call."""
    for name in list(sys.modules):
        if name == "anthropic" or name.startswith("anthropic."):
            del sys.modules[name]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sm_module():
    """Return a freshly imported `sm` module. Guarantees the test
    observes the current source state rather than a stale cached
    import."""
    if "sm" in sys.modules:
        return importlib.reload(sys.modules["sm"])
    import sm  # noqa: PLC0415 — fixture imports lazily
    return sm


@pytest.fixture
def fresh_sm_module():
    """Return a freshly imported `sm` module AFTER purging any cached
    `anthropic` from `sys.modules`. Used by the lazy-import tests so
    they can observe a clean before-state."""
    _purge_anthropic_from_sys_modules()
    if "sm" in sys.modules:
        return importlib.reload(sys.modules["sm"])
    import sm  # noqa: PLC0415 — fixture imports lazily
    return sm


def _read_sm_source() -> str:
    """Return sm.py as text. Used by static grep tests."""
    return SM_PATH.read_text(encoding="utf-8")


def _minimal_messages() -> list:
    """A minimal valid `messages` list — one user turn, plain text."""
    return [{"role": "user", "content": "hello"}]


# ===========================================================================
# Category A — Smoke (6 tests)
#
# `_invoke_anthropic` exists on the module, is PRIVATE (leading
# underscore), is NOT in `sm.__all__`, is callable, and its signature
# accepts (messages, model, max_tokens, api_key) as positional
# parameters in that order.
# ===========================================================================


def test_invoke_anthropic_exists_on_module(sm_module):
    """`sm._invoke_anthropic` is defined at module scope."""
    assert hasattr(sm_module, "_invoke_anthropic"), (
        "expected `_invoke_anthropic` to be defined on the sm module; "
        f"missing from dir(sm)={sorted(dir(sm_module))!r}"
    )


def test_invoke_anthropic_is_callable(sm_module):
    """`sm._invoke_anthropic` is callable (function or callable
    object)."""
    obj = getattr(sm_module, "_invoke_anthropic", None)
    assert callable(obj), (
        f"expected `sm._invoke_anthropic` to be callable; got "
        f"{type(obj).__name__}"
    )


def test_invoke_anthropic_is_private_name(sm_module):
    """The seam is `_invoke_anthropic` (leading underscore). The
    contract pins it as private — only the four in-module spawn
    defaults call it."""
    assert hasattr(sm_module, "_invoke_anthropic"), (
        "expected the private name `_invoke_anthropic` to exist"
    )
    # Defensive: ensure there is no PUBLIC `invoke_anthropic` shadow.
    assert not hasattr(sm_module, "invoke_anthropic"), (
        "expected no public `invoke_anthropic`; the seam is private. "
        "Either remove the public alias or rename the private one."
    )


def test_invoke_anthropic_not_in_all(sm_module):
    """`_invoke_anthropic` is NOT in `sm.__all__`. Private internals
    do not appear in the wildcard surface."""
    assert "_invoke_anthropic" not in sm_module.__all__, (
        f"`_invoke_anthropic` must NOT be in sm.__all__; got "
        f"{sm_module.__all__!r}"
    )


def test_invoke_anthropic_signature_has_four_parameters(sm_module):
    """`_invoke_anthropic` accepts at least four parameters
    (messages, model, max_tokens, api_key)."""
    sig = inspect.signature(sm_module._invoke_anthropic)
    params = list(sig.parameters.values())
    assert len(params) >= 4, (
        f"_invoke_anthropic must accept at least four parameters "
        f"(messages, model, max_tokens, api_key); got signature {sig!s}"
    )


def test_invoke_anthropic_signature_parameter_names(sm_module):
    """Parameter names are exactly `messages`, `model`, `max_tokens`,
    `api_key` in that order — so callers can pass either positionally
    or by keyword and the documented kwarg names work."""
    sig = inspect.signature(sm_module._invoke_anthropic)
    names = list(sig.parameters)[:4]
    assert names == ["messages", "model", "max_tokens", "api_key"], (
        f"_invoke_anthropic parameter names must be "
        f"['messages', 'model', 'max_tokens', 'api_key'] in that "
        f"order; got {names!r}"
    )


# ===========================================================================
# Category B — Happy path with mocked SDK (8 tests)
#
# Fake `anthropic` module is installed into `sys.modules` before the
# call. The seam constructs `Anthropic(api_key=...)`, calls
# `client.messages.create(...)`, extracts `response.content[0].text`,
# and returns the string. Each test verifies one slice of the
# round-trip.
# ===========================================================================


def test_happy_path_returns_text_content(sm_module, monkeypatch):
    """The seam returns the `.text` of the first content block."""
    _install_fake_anthropic(monkeypatch, response_text="hello world")
    out = sm_module._invoke_anthropic(
        messages=_minimal_messages(),
        model="claude-haiku-4-5",
        max_tokens=100,
        api_key="sk-test",
    )
    assert out == "hello world", (
        f"expected `_invoke_anthropic` to return the response text "
        f"'hello world'; got {out!r}"
    )


def test_happy_path_returns_str_type(sm_module, monkeypatch):
    """The return type is exactly `str` — not a content block, not a
    list, not the raw response object."""
    _install_fake_anthropic(monkeypatch, response_text="anything")
    out = sm_module._invoke_anthropic(
        messages=_minimal_messages(),
        model="m",
        max_tokens=10,
        api_key="k",
    )
    assert isinstance(out, str), (
        f"expected return type str; got {type(out).__name__}"
    )


def test_happy_path_constructs_client_with_api_key(sm_module, monkeypatch):
    """`Anthropic(api_key=...)` is called with the exact api_key
    passed to the seam — verified by inspecting the fake client's
    recorded constructor argument."""
    _install_fake_anthropic(monkeypatch)
    sm_module._invoke_anthropic(
        messages=_minimal_messages(),
        model="m",
        max_tokens=10,
        api_key="sk-roundtrip-12345",
    )
    assert len(_FakeAnthropicClient.instances) == 1, (
        f"expected exactly one client construction; got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    assert _FakeAnthropicClient.instances[0].api_key == "sk-roundtrip-12345", (
        f"expected api_key='sk-roundtrip-12345' on constructed client; "
        f"got {_FakeAnthropicClient.instances[0].api_key!r}"
    )


def test_happy_path_passes_messages_to_create(sm_module, monkeypatch):
    """The `messages` list reaches `client.messages.create(...)`
    exactly as passed — verified by the fake client's call record."""
    _install_fake_anthropic(monkeypatch)
    msgs = [{"role": "user", "content": "spec-roundtrip"}]
    sm_module._invoke_anthropic(
        messages=msgs,
        model="m",
        max_tokens=10,
        api_key="k",
    )
    client = _FakeAnthropicClient.instances[0]
    assert len(client.messages.calls) == 1, (
        f"expected exactly one create() call; got "
        f"{len(client.messages.calls)}"
    )
    assert client.messages.calls[0]["messages"] == msgs, (
        f"expected messages={msgs!r} reached create(); got "
        f"{client.messages.calls[0].get('messages')!r}"
    )


def test_happy_path_passes_model_to_create(sm_module, monkeypatch):
    """The `model` string reaches `client.messages.create(...)`
    unchanged."""
    _install_fake_anthropic(monkeypatch)
    sm_module._invoke_anthropic(
        messages=_minimal_messages(),
        model="claude-haiku-4-5-20251022",
        max_tokens=10,
        api_key="k",
    )
    client = _FakeAnthropicClient.instances[0]
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5-20251022", (
        f"expected model='claude-haiku-4-5-20251022' reached create(); "
        f"got {client.messages.calls[0].get('model')!r}"
    )


def test_happy_path_passes_max_tokens_to_create(sm_module, monkeypatch):
    """The `max_tokens` int reaches `client.messages.create(...)`
    unchanged."""
    _install_fake_anthropic(monkeypatch)
    sm_module._invoke_anthropic(
        messages=_minimal_messages(),
        model="m",
        max_tokens=8192,
        api_key="k",
    )
    client = _FakeAnthropicClient.instances[0]
    assert client.messages.calls[0]["max_tokens"] == 8192, (
        f"expected max_tokens=8192 reached create(); got "
        f"{client.messages.calls[0].get('max_tokens')!r}"
    )


def test_happy_path_positional_call_form(sm_module, monkeypatch):
    """Positional call form `_invoke_anthropic(messages, model,
    max_tokens, api_key)` works — the parameters are positional, not
    keyword-only."""
    _install_fake_anthropic(monkeypatch, response_text="positional ok")
    out = sm_module._invoke_anthropic(
        _minimal_messages(),
        "m",
        100,
        "k",
    )
    assert out == "positional ok", (
        f"expected positional call to return 'positional ok'; got {out!r}"
    )


def test_happy_path_each_call_constructs_fresh_client(sm_module, monkeypatch):
    """Each invocation constructs a fresh `Anthropic(...)` client —
    no module-level singleton, no caching. Two calls = two clients."""
    _install_fake_anthropic(monkeypatch)
    sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m1", max_tokens=10,
        api_key="k1",
    )
    sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m2", max_tokens=20,
        api_key="k2",
    )
    assert len(_FakeAnthropicClient.instances) == 2, (
        f"expected 2 client constructions across 2 calls; got "
        f"{len(_FakeAnthropicClient.instances)}"
    )
    assert _FakeAnthropicClient.instances[0].api_key == "k1"
    assert _FakeAnthropicClient.instances[1].api_key == "k2"


# ===========================================================================
# Category C — SDK exceptions propagate (5 tests)
#
# When the SDK client raises (network failure, auth failure, rate
# limit, malformed response shape, generic Exception), the seam
# propagates AS-IS without wrapping. Story 5 is SDK-shaped, not
# role-shaped — callers wrap per Story 4's pattern.
# ===========================================================================


def test_sdk_runtime_error_propagates_unwrapped(sm_module, monkeypatch):
    """A generic `RuntimeError` from `messages.create(...)` propagates
    unchanged (same instance, same message, same type)."""
    boom = RuntimeError("network connection refused")
    _install_fake_anthropic(monkeypatch, raise_exc=boom)
    with pytest.raises(RuntimeError) as exc_info:
        sm_module._invoke_anthropic(
            messages=_minimal_messages(), model="m", max_tokens=10,
            api_key="k",
        )
    assert exc_info.value is boom, (
        f"expected the exact RuntimeError instance to propagate; got "
        f"a different instance: {exc_info.value!r}"
    )


def test_sdk_connection_error_propagates_unwrapped(sm_module, monkeypatch):
    """Simulated network/auth/timeout (`ConnectionError`) propagates
    unchanged."""
    err = ConnectionError("ECONNREFUSED 443")
    _install_fake_anthropic(monkeypatch, raise_exc=err)
    with pytest.raises(ConnectionError) as exc_info:
        sm_module._invoke_anthropic(
            messages=_minimal_messages(), model="m", max_tokens=10,
            api_key="k",
        )
    assert exc_info.value is err


def test_sdk_value_error_propagates_unwrapped(sm_module, monkeypatch):
    """A `ValueError` from the SDK (e.g., malformed argument detected
    by SDK validation) propagates unchanged — the seam does NOT
    wrap into a typed agent error."""
    err = ValueError("invalid model identifier")
    _install_fake_anthropic(monkeypatch, raise_exc=err)
    with pytest.raises(ValueError) as exc_info:
        sm_module._invoke_anthropic(
            messages=_minimal_messages(), model="m", max_tokens=10,
            api_key="k",
        )
    assert exc_info.value is err


def test_sdk_custom_exception_propagates_unwrapped(sm_module, monkeypatch):
    """A custom exception subclass (simulating an SDK-specific error
    type like `RateLimitError` or `AuthenticationError`) propagates
    unchanged."""
    class _FakeRateLimitError(Exception):
        pass

    err = _FakeRateLimitError("429 rate-limited")
    _install_fake_anthropic(monkeypatch, raise_exc=err)
    with pytest.raises(_FakeRateLimitError) as exc_info:
        sm_module._invoke_anthropic(
            messages=_minimal_messages(), model="m", max_tokens=10,
            api_key="k",
        )
    assert exc_info.value is err


def test_sdk_exception_is_not_wrapped_in_agent_error(sm_module, monkeypatch):
    """The seam does NOT wrap SDK exceptions in any of the per-role
    typed agent errors (DecomposeAgentError, TestWriterAgentError,
    CoderAgentError, ReviewerAgentError). Story 5 is SDK-shaped —
    callers wrap in their own role-specific type."""
    err = RuntimeError("sdk failed")
    _install_fake_anthropic(monkeypatch, raise_exc=err)
    # Collect any typed-agent-error classes present on the module so
    # this test is robust whether Story 4's classes are merged or not.
    typed_names = (
        "DecomposeAgentError",
        "TestWriterAgentError",
        "CoderAgentError",
        "ReviewerAgentError",
    )
    typed_classes = tuple(
        getattr(sm_module, n) for n in typed_names if hasattr(sm_module, n)
    )
    with pytest.raises(RuntimeError) as exc_info:
        sm_module._invoke_anthropic(
            messages=_minimal_messages(), model="m", max_tokens=10,
            api_key="k",
        )
    # The raised exception must NOT be an instance of any typed agent
    # error — that would mean Story 5 is doing wrapping it must not.
    for cls in typed_classes:
        assert not isinstance(exc_info.value, cls), (
            f"SDK exception was wrapped in {cls.__name__}; Story 5 must "
            f"propagate SDK errors as-is. Wrapping is the caller's job."
        )


# ===========================================================================
# Category D — Lazy import (4 tests)
#
# `anthropic` is imported INSIDE the function body, not at module top
# level. Before first call: `anthropic` is NOT in `sys.modules` (if it
# wasn't already imported by some other path). After first call: the
# real `anthropic` (or the fake we injected) IS in `sys.modules`.
# Second call does not re-import.
# ===========================================================================


def test_lazy_import_anthropic_absent_before_call(fresh_sm_module):
    """Importing `sm` does NOT pull `anthropic` into `sys.modules`.
    The fresh fixture purges `anthropic*` from `sys.modules` and
    reloads `sm`, so we observe a clean before-state."""
    assert "anthropic" not in sys.modules, (
        "expected `anthropic` to NOT be in sys.modules after `import sm` "
        "(it must be lazy-imported inside `_invoke_anthropic`); found "
        f"{sys.modules['anthropic']!r}"
    )


def test_lazy_import_anthropic_present_after_call(fresh_sm_module, monkeypatch):
    """After calling `_invoke_anthropic`, `anthropic` IS in
    `sys.modules` — the lazy import has fired."""
    assert "anthropic" not in sys.modules, (
        "pre-condition: anthropic must not be in sys.modules before the call"
    )
    _install_fake_anthropic(monkeypatch)
    fresh_sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m", max_tokens=10,
        api_key="k",
    )
    assert "anthropic" in sys.modules, (
        "expected `anthropic` to be in sys.modules after calling "
        "_invoke_anthropic (lazy import fired)"
    )


def test_lazy_import_second_call_does_not_reimport(fresh_sm_module, monkeypatch):
    """A second call does NOT replace the `anthropic` entry in
    `sys.modules` — Python's import machinery returns the cached
    module. The same module identity is preserved across calls."""
    fake_module = _install_fake_anthropic(monkeypatch)
    fresh_sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m", max_tokens=10,
        api_key="k",
    )
    first_id = id(sys.modules["anthropic"])
    fresh_sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m", max_tokens=10,
        api_key="k",
    )
    second_id = id(sys.modules["anthropic"])
    assert first_id == second_id, (
        "second call must reuse the cached `anthropic` module from "
        "sys.modules; the module identity changed between calls"
    )
    # And the cached module is still our fake.
    assert sys.modules["anthropic"] is fake_module


def test_lazy_import_not_at_module_top_level(sm_module):
    """Static check: `sm.py` source does NOT have `import anthropic`
    or `from anthropic ...` at column 0 (module top level). The
    import lives inside a function body, indented."""
    src = _read_sm_source()
    for ln, line in enumerate(src.splitlines(), start=1):
        # Strip any inline comment after the statement so we don't
        # match urls or prose inside comments.
        code = line.split("#", 1)[0]
        # Match `import anthropic` or `from anthropic ...` with NO
        # leading whitespace — i.e., at module top level.
        if re.match(r"^(import\s+anthropic\b|from\s+anthropic\b)", code):
            pytest.fail(
                f"top-level `anthropic` import at sm.py:{ln}: {line!r}. "
                f"The seam must lazy-import `anthropic` inside the "
                f"function body so `import sm` is SDK-free."
            )


# ===========================================================================
# Category E — Single-import grep invariant (3 tests)
#
# Across `sm.py`, `import anthropic` and `from anthropic` together
# appear AT MOST ONE TIME. The provider seam is the only call site.
# ===========================================================================


def test_grep_anthropic_imports_at_most_one_site():
    """Across `sm.py`, the substrings `import anthropic` and `from
    anthropic` together appear AT MOST ONCE."""
    src = _read_sm_source()
    count = 0
    for line in src.splitlines():
        # Strip comments to avoid matching docs that mention the SDK
        # by name (e.g. "see https://console.anthropic.com/").
        code = line.split("#", 1)[0]
        if re.search(r"\b(import\s+anthropic|from\s+anthropic)\b", code):
            count += 1
    assert count <= 1, (
        f"expected at most ONE `import anthropic` / `from anthropic` "
        f"line in sm.py; found {count}. The provider seam must be the "
        f"only import site."
    )


def test_grep_anthropic_imports_exactly_one_site():
    """After Story 5 lands, `sm.py` has EXACTLY ONE `import
    anthropic` / `from anthropic` line — the seam's lazy import.
    Zero is a regression (the seam would crash on first call); two
    or more violates the single-site invariant."""
    src = _read_sm_source()
    count = 0
    for line in src.splitlines():
        code = line.split("#", 1)[0]
        if re.search(r"\b(import\s+anthropic|from\s+anthropic)\b", code):
            count += 1
    assert count == 1, (
        f"expected EXACTLY ONE `import anthropic` / `from anthropic` "
        f"line in sm.py after Story 5; found {count}."
    )


def test_grep_anthropic_imports_not_at_spawn_default_sites():
    """No spawn-default function name (decompose / spawn_test_writer /
    spawn_coder / spawn_reviewer) appears in `sm.py` with an
    `import anthropic` line in its body. The four spawn defaults
    route through `_invoke_anthropic`; they never import the SDK
    themselves."""
    src = _read_sm_source()
    # Walk the source, tracking which top-level def we're inside.
    # When we see an `import anthropic` line, fail if the most-recent
    # enclosing function is one of the four spawn-default names.
    forbidden_funcs = {
        "decompose",
        "spawn_test_writer",
        "spawn_coder",
        "spawn_reviewer",
        "_spawn_test_writer_default",
        "_spawn_coder_default",
        "_spawn_reviewer_default",
        "_decompose_default_spawn",
    }
    current_func: str | None = None
    current_indent: int = -1
    for ln, line in enumerate(src.splitlines(), start=1):
        # Track function-def lines. A `def X(...)` at indent N opens a
        # function whose body is at indent > N.
        m = re.match(r"^(\s*)def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
        if m:
            indent = len(m.group(1))
            name = m.group(2)
            current_func = name
            current_indent = indent
            continue
        # Detect leaving the function: a non-blank line at indent <=
        # the function's def line.
        stripped = line.rstrip()
        if stripped and not stripped.startswith("#"):
            leading = len(line) - len(line.lstrip())
            if current_func is not None and leading <= current_indent:
                current_func = None
                current_indent = -1
        code = line.split("#", 1)[0]
        if re.search(r"\b(import\s+anthropic|from\s+anthropic)\b", code):
            assert current_func not in forbidden_funcs, (
                f"sm.py:{ln} has an `anthropic` import inside spawn "
                f"default `{current_func}`; the four spawn defaults must "
                f"route through `_invoke_anthropic` and never import "
                f"the SDK directly."
            )


# ===========================================================================
# Category F — Type validation on inputs (6 tests)
#
# Non-conforming arg types raise `TypeError` BEFORE the SDK is
# touched. `messages` must be `list`, `model` must be `str`,
# `max_tokens` must be `int`, `api_key` must be `str`. Bool is not an
# int for our purposes (this is the standard contract Python `bool`
# inheritance breaks — reject it explicitly).
# ===========================================================================


def test_invalid_messages_not_list_raises_type_error(sm_module, monkeypatch):
    """`messages` as a non-list (string, dict, tuple, None) raises
    TypeError."""
    _install_fake_anthropic(monkeypatch)
    for bad in ("a string", {"a": "dict"}, ("a", "tuple"), None, 42):
        with pytest.raises(TypeError):
            sm_module._invoke_anthropic(
                messages=bad,
                model="m",
                max_tokens=10,
                api_key="k",
            )


def test_invalid_model_not_str_raises_type_error(sm_module, monkeypatch):
    """`model` as a non-string (int, list, None) raises TypeError."""
    _install_fake_anthropic(monkeypatch)
    for bad in (42, ["model"], None, {"model": "x"}):
        with pytest.raises(TypeError):
            sm_module._invoke_anthropic(
                messages=_minimal_messages(),
                model=bad,
                max_tokens=10,
                api_key="k",
            )


def test_invalid_max_tokens_not_int_raises_type_error(sm_module, monkeypatch):
    """`max_tokens` as a non-int (string, float, list, None) raises
    TypeError. Bool is rejected too — see the dedicated bool test."""
    _install_fake_anthropic(monkeypatch)
    for bad in ("100", 100.5, [100], None):
        with pytest.raises(TypeError):
            sm_module._invoke_anthropic(
                messages=_minimal_messages(),
                model="m",
                max_tokens=bad,
                api_key="k",
            )


def test_invalid_max_tokens_bool_raises_type_error(sm_module, monkeypatch):
    """`max_tokens=True` / `max_tokens=False` — Python's `bool` is a
    subclass of `int`, but a bool max-token count is a caller bug.
    Reject it explicitly."""
    _install_fake_anthropic(monkeypatch)
    for bad in (True, False):
        with pytest.raises(TypeError):
            sm_module._invoke_anthropic(
                messages=_minimal_messages(),
                model="m",
                max_tokens=bad,
                api_key="k",
            )


def test_invalid_api_key_not_str_raises_type_error(sm_module, monkeypatch):
    """`api_key` as a non-string (int, list, None, bytes) raises
    TypeError."""
    _install_fake_anthropic(monkeypatch)
    for bad in (42, [b"k"], None, b"bytes-key"):
        with pytest.raises(TypeError):
            sm_module._invoke_anthropic(
                messages=_minimal_messages(),
                model="m",
                max_tokens=10,
                api_key=bad,
            )


def test_type_validation_fires_before_sdk_construction(sm_module, monkeypatch):
    """Type validation runs BEFORE the SDK client is constructed. A
    bad-typed call must NOT instantiate `Anthropic(...)`."""
    _install_fake_anthropic(monkeypatch)
    _FakeAnthropicClient.instances = []
    with pytest.raises(TypeError):
        sm_module._invoke_anthropic(
            messages="not a list",
            model="m",
            max_tokens=10,
            api_key="k",
        )
    assert _FakeAnthropicClient.instances == [], (
        "type validation must fire before the SDK client is "
        "constructed; got a constructed client despite bad input"
    )


# ===========================================================================
# Category G — No `anthropic` import at module-level test (3 tests)
#
# Importing `sm` does NOT pull `anthropic` into `sys.modules`. The SDK
# is lazy-imported inside `_invoke_anthropic`, so `import sm` is
# SDK-free for any code path that does not invoke the seam.
# ===========================================================================


def test_import_sm_does_not_import_anthropic():
    """Importing `sm` fresh does NOT load `anthropic`. Verified by
    purging `anthropic*` from `sys.modules` before importing `sm`,
    then checking `anthropic` is still absent."""
    _purge_anthropic_from_sys_modules()
    # Force `sm` to reload, so its module-level statements run again
    # under the purged state.
    if "sm" in sys.modules:
        importlib.reload(sys.modules["sm"])
    else:
        import sm  # noqa: F401, PLC0415 — needed to populate sys.modules
    assert "anthropic" not in sys.modules, (
        "importing `sm` must not import `anthropic`; found "
        f"{sys.modules.get('anthropic')!r}"
    )


def test_import_sm_does_not_import_anthropic_submodules():
    """`anthropic.*` submodules (e.g. `anthropic.types`,
    `anthropic.resources`) are also absent after `import sm`."""
    _purge_anthropic_from_sys_modules()
    if "sm" in sys.modules:
        importlib.reload(sys.modules["sm"])
    else:
        import sm  # noqa: F401, PLC0415
    leaked = [n for n in sys.modules if n.startswith("anthropic.")]
    assert leaked == [], (
        f"importing `sm` leaked anthropic submodules into sys.modules: "
        f"{leaked!r}"
    )


def test_module_level_source_has_no_anthropic_import():
    """Static guard: `sm.py` has no top-level `import anthropic` line
    (this re-asserts Category D's lazy-import contract from a
    grep-style angle, paired with the runtime check above)."""
    src = _read_sm_source()
    for ln, line in enumerate(src.splitlines(), start=1):
        code = line.split("#", 1)[0]
        if re.match(r"^(import\s+anthropic\b|from\s+anthropic\b)", code):
            pytest.fail(
                f"sm.py:{ln} has a top-level `anthropic` import: "
                f"{line!r}. The import must be inside the function body."
            )


# ===========================================================================
# Category H — Message round-trip (3 tests)
#
# The `messages` list reaches `client.messages.create(...)` exactly
# as passed: no transformation, no mutation, no shape change. Verified
# by a spy on the fake client.
# ===========================================================================


def test_message_round_trip_single_user_turn(sm_module, monkeypatch):
    """A single-turn message list arrives at the SDK identical to the
    one the caller passed in."""
    _install_fake_anthropic(monkeypatch)
    msgs = [{"role": "user", "content": "hello"}]
    sm_module._invoke_anthropic(
        messages=msgs, model="m", max_tokens=10, api_key="k",
    )
    received = _FakeAnthropicClient.instances[0].messages.calls[0]["messages"]
    assert received == msgs


def test_message_round_trip_multi_turn(sm_module, monkeypatch):
    """A multi-turn message list (user + assistant + user) arrives at
    the SDK in the same order and shape it left in."""
    _install_fake_anthropic(monkeypatch)
    msgs = [
        {"role": "user", "content": "first user"},
        {"role": "assistant", "content": "first assistant"},
        {"role": "user", "content": "second user"},
    ]
    sm_module._invoke_anthropic(
        messages=msgs, model="m", max_tokens=10, api_key="k",
    )
    received = _FakeAnthropicClient.instances[0].messages.calls[0]["messages"]
    assert received == msgs
    assert len(received) == 3
    assert [m["role"] for m in received] == ["user", "assistant", "user"]


def test_message_round_trip_no_mutation_of_caller_list(sm_module, monkeypatch):
    """The caller's `messages` list is NOT mutated by the seam (no
    appends, no replacements). The list the caller still holds after
    the call is identical to what they passed in."""
    _install_fake_anthropic(monkeypatch)
    msgs = [{"role": "user", "content": "do not mutate me"}]
    snapshot = [dict(m) for m in msgs]  # deep-enough copy
    sm_module._invoke_anthropic(
        messages=msgs, model="m", max_tokens=10, api_key="k",
    )
    assert msgs == snapshot, (
        f"_invoke_anthropic mutated the caller's messages list; before "
        f"call={snapshot!r}, after call={msgs!r}"
    )


# ===========================================================================
# Category I — Response extraction (4 tests)
#
# SDK returns the standard Messages API shape `response.content[0].text`.
# The seam extracts the text string. Empty `content` list or non-list
# `content` propagate the natural Python errors (IndexError /
# AttributeError) — Story 5 trusts the SDK shape; the four spawn
# defaults wrap.
# ===========================================================================


def test_response_extraction_first_content_block(sm_module, monkeypatch):
    """The seam returns `response.content[0].text` — the FIRST content
    block's text. If the SDK returned multiple blocks, only the first
    is returned (Anthropic Messages API: a single assistant turn
    typically has one text block; this pins the contract)."""
    # Build a multi-block fake response. Only the first block's text
    # is returned.
    class _MultiBlockResponse:
        def __init__(self):
            self.content = [
                _FakeContentBlock("first block text"),
                _FakeContentBlock("second block text"),
            ]

    class _MultiClient(_FakeAnthropicClient):
        def __init__(self, api_key=None, **kwargs):
            super().__init__(api_key=api_key, **kwargs)
            class _M:
                def __init__(self):
                    self.calls = []
                def create(self, **kwargs):
                    self.calls.append(kwargs)
                    return _MultiBlockResponse()
            self.messages = _M()

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _MultiClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    _FakeAnthropicClient.instances = []

    out = sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m", max_tokens=10,
        api_key="k",
    )
    assert out == "first block text", (
        f"expected first-block text 'first block text'; got {out!r}"
    )


def test_response_extraction_empty_string_text(sm_module, monkeypatch):
    """If the first content block's `.text` is the empty string, the
    seam returns the empty string — it does NOT raise. The SDK
    returned a valid (if empty) response; callers decide what to do
    with empty content."""
    _install_fake_anthropic(monkeypatch, response_text="")
    out = sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m", max_tokens=10,
        api_key="k",
    )
    assert out == "", (
        f"expected empty string return for empty-text response; got {out!r}"
    )


def test_response_extraction_unicode_text(sm_module, monkeypatch):
    """The seam returns the response text verbatim — Unicode, newlines,
    special characters all pass through unchanged."""
    payload = "Héllo\n\tWörld — 日本語 \U0001F4A1"
    _install_fake_anthropic(monkeypatch, response_text=payload)
    out = sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m", max_tokens=10,
        api_key="k",
    )
    assert out == payload, (
        f"Unicode payload was mutated; expected {payload!r}, got {out!r}"
    )


def test_response_extraction_long_text(sm_module, monkeypatch):
    """A long response (4096+ chars) passes through unchanged. The
    seam does no truncation, no slicing, no transformation."""
    payload = "x" * 8000
    _install_fake_anthropic(monkeypatch, response_text=payload)
    out = sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m", max_tokens=10,
        api_key="k",
    )
    assert out == payload
    assert len(out) == 8000


# ===========================================================================
# Category J — Argument-count round-trip safety net (3 tests)
#
# The fake `messages.create(...)` only receives kwargs the seam
# explicitly passes. Belt-and-suspenders checks that no surprise
# kwargs leak, and that the four documented kwargs each appear
# exactly once in the call record.
# ===========================================================================


def test_create_call_kwargs_contain_all_four(sm_module, monkeypatch):
    """The recorded `create(...)` kwargs include all four:
    `messages`, `model`, `max_tokens`. The seam may pass `api_key`
    via the constructor only (which is the SDK contract), so
    `api_key` is verified separately. This test pins that the three
    create-kwargs are all present."""
    _install_fake_anthropic(monkeypatch)
    sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="claude-haiku-4-5",
        max_tokens=1024, api_key="sk-test",
    )
    call = _FakeAnthropicClient.instances[0].messages.calls[0]
    for key in ("messages", "model", "max_tokens"):
        assert key in call, (
            f"expected `{key}` in create() kwargs; got keys={list(call)!r}"
        )


def test_create_call_count_is_exactly_one_per_invocation(sm_module, monkeypatch):
    """One call to `_invoke_anthropic` results in exactly one call to
    `messages.create(...)` — no retries, no double-fires."""
    _install_fake_anthropic(monkeypatch)
    sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m", max_tokens=10,
        api_key="k",
    )
    assert len(_FakeAnthropicClient.instances) == 1
    assert len(_FakeAnthropicClient.instances[0].messages.calls) == 1


def test_client_construction_count_is_exactly_one_per_invocation(
        sm_module, monkeypatch):
    """One call to `_invoke_anthropic` constructs exactly one
    `Anthropic(...)` client — no retries, no fallback clients."""
    _install_fake_anthropic(monkeypatch)
    sm_module._invoke_anthropic(
        messages=_minimal_messages(), model="m", max_tokens=10,
        api_key="k",
    )
    assert len(_FakeAnthropicClient.instances) == 1
