"""Iter 2 Story 14 — Retro polish: tightened structural test +
honest `build_entry` docstring.

Story 14 (size S, behavior-preserving) closes 2 retro items from Iter 1:

  - Retro item 5: the existing `test_no_inline_entry_construction_in_sm_module`
    in `tests/test_build_entry.py` (line 1128) is too narrow — its body only
    asserts `"def build_entry" in src`, which is not a real check that every
    log writer routes through `build_entry`. Replacement: a structural test
    that walks every `.py` module in the package and fails if ANY inline
    entry-dict construction is found (a literal dict containing all three
    reserved keys `id`, `type`, `timestamp` together, outside the body of
    `build_entry`). The new test must pass on the current tree (the only
    such literal is inside `build_entry` itself).

  - Retro item 6: `build_entry`'s docstring currently ends with
        "The returned dict is a fresh object — mutating it does not affect
        the input, and mutating the input after the call does not affect
        the result."
    which reads as a deep-independence claim. Per LOCKED_DECISION 5
    (Iter 2 Requirements_v2.md), the implementation stays SHALLOW
    (no caller depends on deep-copy semantics, verified in Iter 1 review).
    The docstring must be rewritten to honestly describe shallow-copy
    behavior — the top-level dict is fresh, but nested values are shared
    references with the caller's `content` payload.

These tests pin the cleanup. They MUST fail on first run (no Coder has
touched the module yet) and pass after the Coder lands Story 14.

Anti-lane: this file does NOT modify sm.py and does NOT modify the
existing weak test in `test_build_entry.py`. The Coder owns both edits:
  (a) rewrite `sm.build_entry.__doc__`
  (b) remove the weak `test_no_inline_entry_construction_in_sm_module`
      from `test_build_entry.py` (replaced by the tightened version
      pinned here)

The behavior of `build_entry` itself is unchanged — shallow copy stays.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import sys

import pytest


THIS_FILE = pathlib.Path(__file__).resolve()
PACKAGE_DIR = THIS_FILE.parent.parent
SM_PATH = PACKAGE_DIR / "sm.py"
TESTS_DIR = THIS_FILE.parent
WEAK_TEST_FILE = TESTS_DIR / "test_build_entry.py"

if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


# ---------------------------------------------------------------------------
# Reserved-key set — must match sm._RESERVED_KEYS exactly
# ---------------------------------------------------------------------------

RESERVED = frozenset({"id", "type", "timestamp"})


# ---------------------------------------------------------------------------
# Scanner — the meat of the tightened structural test
# ---------------------------------------------------------------------------


def _package_modules() -> list[pathlib.Path]:
    """Every `.py` module in the package (sm.py today; future-proofed).

    Excludes the `tests/` directory and `iter1/`, `iter2/` doc folders.
    """
    out: list[pathlib.Path] = []
    for path in PACKAGE_DIR.glob("*.py"):
        out.append(path)
    return out


def _find_inline_entry_constructions(
    source: str,
    skip_function_names: frozenset = frozenset({"build_entry"}),
) -> list[tuple[int, str]]:
    """Scan source for dict literals whose keys (as constant strings) include
    ALL THREE reserved keys `id`, `type`, `timestamp`.

    Returns a list of (lineno, snippet) tuples. The snippet is a one-line
    summary for the failure message.

    Excludes any dict literal whose enclosing function is named in
    `skip_function_names` (i.e. `build_entry` itself is exempt — its
    canonical entry-dict construction is the whole point).

    Empty list means no inline construction was found.
    """
    tree = ast.parse(source)

    # Build a parent map so we can walk upward from any node to find its
    # enclosing FunctionDef. ast doesn't track parents by default.
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    def _enclosing_function_name(node: ast.AST) -> str | None:
        cur: ast.AST | None = parents.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
            cur = parents.get(id(cur))
        return None

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        # Collect constant-string keys (skip non-constant / non-string keys).
        keys: set[str] = set()
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.add(k.value)
        if not RESERVED.issubset(keys):
            continue
        fn_name = _enclosing_function_name(node)
        if fn_name in skip_function_names:
            continue
        snippet = (
            f"dict literal with reserved keys {sorted(RESERVED & keys)!r} "
            f"inside function {fn_name!r} at line {node.lineno}"
        )
        hits.append((node.lineno, snippet))
    return hits


# ===========================================================================
# Category A — Tightened structural test (retro item 5)
# ===========================================================================


def test_no_inline_entry_construction_in_sm_module():
    """REPLACEMENT for the weak Story-3 test of the same name.

    Walks `sm.py` AST, finds every dict literal containing ALL THREE
    reserved keys (`id`, `type`, `timestamp`), and fails if any are found
    OUTSIDE `build_entry`'s function body. Pin: ZERO hits.

    This is the actual contract Story 3 intended but did not enforce.
    """
    source = SM_PATH.read_text(encoding="utf-8")
    hits = _find_inline_entry_constructions(source)
    assert hits == [], (
        f"Inline entry-dict construction found OUTSIDE build_entry in "
        f"sm.py ({len(hits)} site(s)). Every log writer must route "
        f"through `build_entry`. Offending sites: {hits!r}"
    )


def test_scanner_walks_every_py_module_in_package():
    """The tightened scanner does not hard-code `sm.py` — it walks every
    `.py` module in the package root so that future module-splits stay
    covered.

    Currently the package is single-module (sm.py only). This test pins
    the discovery mechanism so a new module added tomorrow gets the
    inline-construction check for free.
    """
    modules = _package_modules()
    module_names = {p.name for p in modules}
    assert "sm.py" in module_names, (
        f"Package module discovery must include sm.py; got {module_names!r}"
    )
    # Run the scanner against every discovered module.
    all_hits: dict[str, list[tuple[int, str]]] = {}
    for mod in modules:
        hits = _find_inline_entry_constructions(
            mod.read_text(encoding="utf-8")
        )
        if hits:
            all_hits[mod.name] = hits
    assert all_hits == {}, (
        f"Inline entry-dict construction found OUTSIDE build_entry in "
        f"package modules: {all_hits!r}"
    )


def test_scanner_excludes_build_entry_body_itself():
    """`build_entry`'s own canonical entry-dict construction (the `result =
    {"id": ..., "type": ..., "timestamp": ...}` literal in sm.py) MUST
    NOT be flagged — it is the legitimate source of every entry dict.

    Sanity check: scanning sm.py without the `build_entry` skip rule
    would find at least one hit (the canonical one).
    """
    source = SM_PATH.read_text(encoding="utf-8")
    hits_no_skip = _find_inline_entry_constructions(
        source, skip_function_names=frozenset()
    )
    # Without the skip, build_entry's own dict literal must be found.
    assert len(hits_no_skip) >= 1, (
        "Sanity check failed: scanner did not find build_entry's own "
        "canonical entry-dict construction. The scanner is broken — fix "
        "the scanner before trusting its zero-hit verdict on the rest of "
        "the package."
    )
    # With the skip, every hit is exempted.
    hits_with_skip = _find_inline_entry_constructions(source)
    # The skip rule must reduce the hit count (proving it is doing work).
    assert len(hits_with_skip) < len(hits_no_skip), (
        "Skip rule for `build_entry` did not reduce hit count — either "
        "build_entry's body changed or the skip rule is wired wrong."
    )


def test_scanner_canary_would_be_caught():
    """Meta-test: synthesize a small source string that contains an inline
    entry-dict construction in a function OTHER than `build_entry`, and
    confirm the scanner catches it.

    This is the verification-once pattern from Stories_v1.md Story 14
    acceptance ("Test fails on a deliberately introduced inline
    construction ..."). We do it in-process against a string so the
    real sm.py source is never mutated.
    """
    canary_source = (
        "def some_other_writer():\n"
        "    return {\n"
        "        'id': 'abc',\n"
        "        'type': 'evil',\n"
        "        'timestamp': '2026-05-11T00:00:00+00:00',\n"
        "        'payload': 1,\n"
        "    }\n"
    )
    hits = _find_inline_entry_constructions(canary_source)
    assert len(hits) == 1, (
        f"Scanner must catch the canary inline construction; got "
        f"{len(hits)} hit(s): {hits!r}"
    )
    assert hits[0][1].startswith("dict literal with reserved keys"), (
        f"Canary hit snippet should describe the offending literal; got "
        f"{hits[0][1]!r}"
    )


def test_scanner_does_not_flag_partial_reserved_key_dict():
    """Dicts containing only SOME of the reserved keys (e.g. just `type`,
    or just `id` + `type` with no `timestamp`) MUST NOT be flagged —
    those are legitimate non-entry data shapes.
    """
    # Two keys: id + type, missing timestamp — not an entry dict.
    partial_source = (
        "def f():\n"
        "    return {'id': 'x', 'type': 'y'}\n"
    )
    assert _find_inline_entry_constructions(partial_source) == []
    # Just timestamp — not an entry dict.
    partial_source_2 = (
        "def g():\n"
        "    return {'timestamp': '2026-01-01'}\n"
    )
    assert _find_inline_entry_constructions(partial_source_2) == []


def test_scanner_flags_extra_keys_alongside_reserved_three():
    """An inline construction with the three reserved keys PLUS extra
    payload fields is still a violation — the test must catch it.
    """
    canary_source = (
        "def evil_writer():\n"
        "    e = {\n"
        "        'id': 'abc',\n"
        "        'type': 'evil',\n"
        "        'timestamp': '2026-05-11T00:00:00+00:00',\n"
        "        'extra_a': 1,\n"
        "        'extra_b': 2,\n"
        "    }\n"
        "    return e\n"
    )
    hits = _find_inline_entry_constructions(canary_source)
    assert len(hits) == 1


def test_old_weak_test_is_removed_from_test_build_entry_module():
    """The Story-3 weak version of `test_no_inline_entry_construction_in_sm_module`
    (located at `tests/test_build_entry.py:1128` per Story 14 audit) must
    be REMOVED — not kept alongside the tightened version, which would
    create two functions with the same name and pytest would only run
    one.

    The Coder owns this removal as part of Story 14 (the test in this
    file is the replacement; the weak one in `test_build_entry.py` must
    go).
    """
    src = WEAK_TEST_FILE.read_text(encoding="utf-8")
    # The function name appears in exactly ZERO `def` lines in
    # test_build_entry.py — it was moved to THIS file.
    def_pattern = re.compile(
        r"^\s*def\s+test_no_inline_entry_construction_in_sm_module\s*\(",
        re.MULTILINE,
    )
    matches = def_pattern.findall(src)
    assert len(matches) == 0, (
        f"The weak version of "
        f"`test_no_inline_entry_construction_in_sm_module` must be "
        f"removed from {WEAK_TEST_FILE.name} as part of Story 14; "
        f"found {len(matches)} definition(s) still present."
    )


# ===========================================================================
# Category B — `build_entry` docstring honesty (retro item 6)
# ===========================================================================


def test_build_entry_docstring_mentions_shallow_copy():
    """The rewritten docstring must use the word "shallow" (case-insensitive).

    Per LOCKED_DECISION 5: implementation stays shallow; docstring must
    honestly describe that behavior.
    """
    import sm
    doc = sm.build_entry.__doc__ or ""
    assert "shallow" in doc.lower(), (
        f"build_entry.__doc__ must mention shallow-copy semantics "
        f"(LOCKED_DECISION 5); got: {doc!r}"
    )


def test_build_entry_docstring_does_not_overclaim_deep_independence():
    """The current docstring's deep-independence-style phrasing must be
    removed. Specifically, the trailing two-sentence claim

        "The returned dict is a fresh object — mutating it does not
         affect the input, and mutating the input after the call does
         not affect the result."

    overstates the actual behavior (it only holds for TOP-LEVEL keys;
    nested values are shared references).

    Pin: the rewritten docstring does not contain the bidirectional
    "does not affect" / "deep" / "fully independent" phrasing.

    We collapse whitespace before matching so multi-line phrasings still
    get caught.
    """
    import sm
    raw = sm.build_entry.__doc__ or ""
    # Collapse all runs of whitespace (incl. newlines) to single spaces so
    # multi-line phrasings still match the forbidden substrings.
    doc = re.sub(r"\s+", " ", raw).lower()
    forbidden_substrings = [
        "deep independence",
        "deep copy",
        "deepcopy",
        "fully independent",
        # The exact bidirectional overclaim from the current docstring.
        "mutating it does not affect the input, "
        "and mutating the input",
        "does not affect the input, and mutating the input",
        # "mutating the input ... does not affect the result" is the
        # mirror half of the overclaim — also forbidden.
        "mutating the input after the call does not affect the result",
    ]
    found = [s for s in forbidden_substrings if s in doc]
    assert found == [], (
        f"build_entry.__doc__ still contains overclaim phrase(s) "
        f"{found!r}; rewrite per LOCKED_DECISION 5 to honestly describe "
        f"shallow-copy semantics."
    )


def test_build_entry_docstring_notes_nested_aliasing():
    """The rewritten docstring must make the nested-aliasing consequence
    explicit — that mutating a nested value through the returned entry
    is visible in the caller's payload (and vice versa), because the
    top-level copy is shallow.

    Accept any of several reasonable phrasings (the Coder owns the
    exact wording), but require the docstring contains the word
    "shallow" co-located with an aliasing word AND an observability /
    propagation word.

    The current dishonest docstring DOES contain "nested" (in the
    validation note "nested keys are not flagged") and "mutating" (in
    the overclaim), but does NOT contain "shallow" — so this assertion
    fails until the Coder rewrites.
    """
    import sm
    raw = sm.build_entry.__doc__ or ""
    doc = re.sub(r"\s+", " ", raw).lower()
    group_aliasing = (
        "nested",
        "shared",
        "aliased",
        "aliasing",
        "reference",
        "references",
    )
    group_observability = (
        "mutate",
        "mutating",
        "mutation",
        "observable",
        "reflected",
        "visible",
        "seen",
        "propagate",
        "propagates",
        "propagated",
    )
    has_shallow = "shallow" in doc
    has_aliasing = any(w in doc for w in group_aliasing)
    has_observability = any(w in doc for w in group_observability)
    assert has_shallow and has_aliasing and has_observability, (
        f"build_entry.__doc__ must make nested-aliasing consequence "
        f"explicit (e.g. 'shallow copy: nested values are shared "
        f"references; mutating them through the entry is observable in "
        f"the caller's payload'). "
        f"`shallow` present: {has_shallow}; "
        f"aliasing keyword present: {has_aliasing}; "
        f"observability keyword present: {has_observability}. "
        f"Doc: {doc!r}"
    )


def test_build_entry_shallow_copy_semantics_pinned_behaviorally():
    """The docstring describes reality — pin reality.

    Pass a dict with a nested dict, get an entry, mutate the nested dict
    in the entry. The original payload's nested dict reflects the
    mutation (because shallow copy shares the nested reference). If
    this assertion fails, EITHER the implementation flipped to deep
    copy (in which case the docstring rewrite is wrong) OR the test is
    broken — but the docstring claim must match.
    """
    import sm
    nested = {"k": "v"}
    content = {"payload": nested}
    result = sm.build_entry("smoke", content)
    # Mutate the nested dict THROUGH the result.
    result["payload"]["k"] = "MUTATED"
    # The shared reference means the original payload sees it too.
    assert nested["k"] == "MUTATED", (
        f"Shallow-copy semantics broken: mutating result['payload']['k'] "
        f"did NOT propagate to the original nested dict (got "
        f"{nested['k']!r}). Either the implementation switched to deep "
        f"copy (in which case Story 14's docstring rewrite is wrong) "
        f"or this test is wrong."
    )
    # And the reverse direction also propagates (shared reference).
    nested["k"] = "MUTATED_AGAIN"
    assert result["payload"]["k"] == "MUTATED_AGAIN"


def test_build_entry_shallow_copy_reverse_direction_nested():
    """Mutating a nested dict in the ORIGINAL payload AFTER the build
    call is visible through the entry (the other half of shallow-copy
    semantics — same shared reference, both directions).
    """
    import sm
    nested = {"a": 1}
    content = {"payload": nested}
    result = sm.build_entry("smoke", content)
    # Mutate the original AFTER the build call.
    nested["a"] = 999
    nested["new"] = "added"
    assert result["payload"]["a"] == 999
    assert result["payload"]["new"] == "added"


def test_build_entry_top_level_keys_are_separate():
    """Even though nested values are shared, the TOP-LEVEL dict is a
    fresh object — adding / removing / overwriting a TOP-LEVEL key in
    the result does NOT affect the input. This is the "shallow" half
    of "shallow copy".
    """
    import sm
    content = {"a": 1, "b": 2}
    result = sm.build_entry("smoke", content)
    # Add a new top-level key to the result.
    result["new_top_level_key"] = "added"
    assert "new_top_level_key" not in content
    # Overwrite an existing top-level scalar value.
    result["a"] = 999
    assert content["a"] == 1
    # Delete a top-level key from result.
    del result["b"]
    assert "b" in content


def test_build_entry_top_level_separation_reverse_direction():
    """Mutating the ORIGINAL content's top-level keys after the build
    call does NOT affect the result — top-level dict is independent.
    """
    import sm
    content = {"a": 1, "b": 2}
    result = sm.build_entry("smoke", content)
    content["a"] = 999
    content["new"] = "added"
    del content["b"]
    assert result["a"] == 1
    assert result["b"] == 2
    assert "new" not in result


def test_build_entry_still_appends_id_type_timestamp():
    """Basic behavioral regression — Story 14 changes ONLY the docstring,
    so build_entry must still stamp `id` / `type` / `timestamp` on
    every returned entry.
    """
    import sm
    result = sm.build_entry("smoke", {"event": "hello"})
    assert "id" in result
    assert "type" in result
    assert "timestamp" in result
    assert isinstance(result["id"], str) and len(result["id"]) == 32
    assert result["type"] == "smoke"
    assert isinstance(result["timestamp"], str) and result["timestamp"]


# ===========================================================================
# Category C — Implementation invariance (no impl change in Story 14)
# ===========================================================================


def test_build_entry_signature_unchanged():
    """Story 14 changes NOTHING in `build_entry`'s call signature."""
    import sm
    sig = inspect.signature(sm.build_entry)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == ["type", "content"], (
        f"build_entry signature must remain (type, content); got "
        f"{names!r}. Story 14 is docstring-only — no signature change."
    )


def test_build_entry_returns_dict_with_payload_keys_plus_reserved():
    """Shape regression — Iter 1 contract: result has all payload keys
    PLUS the three reserved fields, in (id, type, timestamp, *payload)
    order.
    """
    import sm
    payload = {"a": 1, "b": "two", "c": [1, 2, 3]}
    result = sm.build_entry("smoke", payload)
    for k in payload:
        assert k in result, f"payload key {k!r} missing from result"
    assert set(result.keys()) == set(payload.keys()) | RESERVED
    # Order: reserved three first, then payload in original order.
    assert list(result.keys())[:3] == ["id", "type", "timestamp"]
    assert list(result.keys())[3:] == list(payload.keys())


def test_build_entry_implementation_body_unchanged_no_deepcopy_import():
    """Cross-check that the Coder did NOT silently flip the implementation
    to deep-copy. `copy.deepcopy` MUST NOT appear inside `build_entry`'s
    function body.

    (The module-level may or may not import `copy` for other reasons;
    we scope this check to `build_entry`'s body specifically.)
    """
    import sm
    src = inspect.getsource(sm.build_entry)
    assert "deepcopy" not in src, (
        f"build_entry body must not call `deepcopy` — LOCKED_DECISION 5 "
        f"keeps shallow-copy semantics. Got source containing 'deepcopy':\n"
        f"{src}"
    )
