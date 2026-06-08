"""ccp-009 substrate_query.py — helper unit tests.

Two classes of tests:

1. Happy-path + unresolvable-path tests for each helper.
2. THE CONVERGENT-BLIND-SPOT GUARD: assert the helpers cannot accept
   or read draft text. The architectural property is that substrate
   access is blind to the draft; the test must prove the helpers
   physically cannot pull draft prose into substrate-fetch calls.

The guard test uses two independent techniques:

    a) Inspect helper function signatures via ``inspect`` — assert
       the parameter list does NOT include any draft-context field
       names (``anchored_claim``, ``source_text_excerpt``, ``claim``,
       ``draft_text``).

    b) Pass a sentinel-tracking dict subclass as a pointer to
       ``query_pointer`` and assert that NO key other than
       ``pointer_type`` and ``pointer_value`` is read. This is the
       runtime guarantee — even if a future change widens
       ``query_pointer``'s contract, the test catches the leak the
       moment the dispatcher touches a forbidden field.

Run:
    pytest -v ~/cc-skills/cc:proofread/scripts/tests/test_substrate_query.py
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts import substrate_query  # noqa: E402
from scripts.substrate_query import (  # noqa: E402
    _AMPLITUDE_STUB_ERROR,
    query_amplitude_chart,
    query_file,
    query_pointer,
    query_state_yaml,
    query_vault_finding,
)


# ---------------------------------------------------------------------------
# query_file
# ---------------------------------------------------------------------------


def test_query_file_text_happy(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("# Hello\n\nbody.\n", encoding="utf-8")
    result = query_file(str(target))
    assert result["resolved"] is True
    assert result["value"] == "# Hello\n\nbody.\n"
    assert result["resolved_path"] == str(target)
    assert result["error"] is None


def test_query_file_yaml_with_key_path(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text(
        "experiments:\n  expA:\n    headline: 42.0\n",
        encoding="utf-8",
    )
    result = query_file(
        str(target), key_path=["experiments", "expA", "headline"]
    )
    assert result["resolved"] is True
    assert result["value"] == 42.0


def test_query_file_missing(tmp_path: Path) -> None:
    target = tmp_path / "nope.md"
    result = query_file(str(target))
    assert result["resolved"] is False
    assert "does not exist" in result["error"]
    assert result["resolved_path"] == str(target)


def test_query_file_empty_path() -> None:
    result = query_file("")
    assert result["resolved"] is False
    assert "empty path" in result["error"]


def test_query_file_key_path_into_markdown_rejects(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("body", encoding="utf-8")
    result = query_file(str(target), key_path=["x"])
    assert result["resolved"] is False
    assert "requires .yaml" in result["error"]


def test_query_file_yaml_missing_key(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("experiments:\n  expA:\n    other: 5\n", encoding="utf-8")
    result = query_file(
        str(target), key_path=["experiments", "expA", "absent"]
    )
    assert result["resolved"] is False
    assert "not found" in result["error"]
    assert result["resolved_path"] == str(target)


def test_query_file_relative_with_project_root(tmp_path: Path) -> None:
    target = tmp_path / "rel.md"
    target.write_text("hi", encoding="utf-8")
    result = query_file("rel.md", project_root=tmp_path)
    assert result["resolved"] is True
    assert result["value"] == "hi"


# ---------------------------------------------------------------------------
# query_state_yaml
# ---------------------------------------------------------------------------


def test_query_state_yaml_happy(tmp_path: Path) -> None:
    (tmp_path / "state.yaml").write_text(
        "experiments:\n  exp1:\n    latest_read:\n      headline: 8.8\n",
        encoding="utf-8",
    )
    result = query_state_yaml(
        "state.yaml.experiments.exp1.latest_read.headline", tmp_path
    )
    assert result["resolved"] is True
    assert result["value"] == 8.8


def test_query_state_yaml_bad_key_path(tmp_path: Path) -> None:
    (tmp_path / "state.yaml").write_text(
        "experiments:\n  exp1: {a: 1}\n", encoding="utf-8"
    )
    result = query_state_yaml(
        "state.yaml.experiments.exp1.NONEXISTENT.field", tmp_path
    )
    assert result["resolved"] is False
    assert "not found" in result["error"]


def test_query_state_yaml_too_short() -> None:
    result = query_state_yaml("state", Path("/tmp"))
    assert result["resolved"] is False
    assert "too short" in result["error"]


def test_query_state_yaml_empty_path(tmp_path: Path) -> None:
    result = query_state_yaml("", tmp_path)
    assert result["resolved"] is False
    assert "empty" in result["error"]


def test_query_state_yaml_missing_file(tmp_path: Path) -> None:
    result = query_state_yaml(
        "state.yaml.experiments.exp1.x", tmp_path
    )
    assert result["resolved"] is False
    assert "does not exist" in result["error"]


# ---------------------------------------------------------------------------
# query_vault_finding
# ---------------------------------------------------------------------------


def test_query_vault_finding_vault_knowledge(tmp_path: Path) -> None:
    vault_dir = tmp_path / "vault" / "knowledge"
    vault_dir.mkdir(parents=True)
    (vault_dir / "checkout-redesign.md").write_text(
        "# checkout-redesign\nbody", encoding="utf-8"
    )
    result = query_vault_finding("checkout-redesign", tmp_path)
    assert result["resolved"] is True
    assert "checkout-redesign" in result["value"]
    assert "vault/knowledge/checkout-redesign.md" in result["resolved_path"]


def test_query_vault_finding_experiments_fallback(tmp_path: Path) -> None:
    exp_dir = tmp_path / "experiments"
    exp_dir.mkdir()
    (exp_dir / "v2-test.md").write_text("body", encoding="utf-8")
    result = query_vault_finding("v2-test", tmp_path)
    assert result["resolved"] is True
    assert "experiments/v2-test.md" in result["resolved_path"]


def test_query_vault_finding_missing(tmp_path: Path) -> None:
    result = query_vault_finding("does-not-exist", tmp_path)
    assert result["resolved"] is False
    assert "not found" in result["error"]


def test_query_vault_finding_empty_slug(tmp_path: Path) -> None:
    result = query_vault_finding("", tmp_path)
    assert result["resolved"] is False
    assert "empty" in result["error"]


# ---------------------------------------------------------------------------
# query_amplitude_chart (stub)
# ---------------------------------------------------------------------------


def test_query_amplitude_chart_stub_response() -> None:
    """The stub MUST return a documented error response (not raise)."""
    result = query_amplitude_chart("abc123xy")
    assert result["resolved"] is False
    assert result["error"] == _AMPLITUDE_STUB_ERROR
    # The chart_id passes through as the resolved_path so the lens
    # can report which chart was unresolvable.
    assert result["resolved_path"] == "abc123xy"


def test_query_amplitude_chart_empty_id() -> None:
    result = query_amplitude_chart("")
    assert result["resolved"] is False
    assert "empty" in result["error"]


def test_query_amplitude_chart_stub_message_documents_stub() -> None:
    """The stub message must mention the orchestrator-level path that
    will eventually replace the stub. This is a documentation contract
    so consumers know how to upgrade."""
    assert "orchestrator-level Agent call" in _AMPLITUDE_STUB_ERROR
    assert "Amplitude" in _AMPLITUDE_STUB_ERROR or "amplitude" in _AMPLITUDE_STUB_ERROR


# ---------------------------------------------------------------------------
# query_pointer dispatcher
# ---------------------------------------------------------------------------


def test_query_pointer_routes_file_path(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("body", encoding="utf-8")
    result = query_pointer(
        {"pointer_type": "file_path", "pointer_value": str(target)},
        tmp_path,
    )
    assert result["resolved"] is True
    assert result["value"] == "body"


def test_query_pointer_routes_state_yaml(tmp_path: Path) -> None:
    (tmp_path / "state.yaml").write_text(
        "experiments:\n  e: {x: 1}\n", encoding="utf-8"
    )
    result = query_pointer(
        {
            "pointer_type": "state_yaml_dotted",
            "pointer_value": "state.yaml.experiments.e.x",
        },
        tmp_path,
    )
    assert result["resolved"] is True
    assert result["value"] == 1


def test_query_pointer_routes_wikilink(tmp_path: Path) -> None:
    (tmp_path / "vault" / "knowledge").mkdir(parents=True)
    (tmp_path / "vault" / "knowledge" / "foo.md").write_text(
        "concept body", encoding="utf-8"
    )
    result = query_pointer(
        {"pointer_type": "wikilink", "pointer_value": "foo"},
        tmp_path,
    )
    assert result["resolved"] is True
    assert "concept body" in result["value"]


def test_query_pointer_routes_chart_id(tmp_path: Path) -> None:
    result = query_pointer(
        {"pointer_type": "chart_id", "pointer_value": "abc123"},
        tmp_path,
    )
    # Stub: unresolvable.
    assert result["resolved"] is False
    assert result["error"] == _AMPLITUDE_STUB_ERROR


def test_query_pointer_unknown_type(tmp_path: Path) -> None:
    result = query_pointer(
        {"pointer_type": "unicorn", "pointer_value": "x"},
        tmp_path,
    )
    assert result["resolved"] is False
    assert "unknown pointer_type" in result["error"]


def test_query_pointer_missing_fields(tmp_path: Path) -> None:
    result = query_pointer({"pointer_value": "x"}, tmp_path)
    assert result["resolved"] is False
    assert "pointer_type" in result["error"]

    result = query_pointer({"pointer_type": "file_path"}, tmp_path)
    assert result["resolved"] is False
    assert "pointer_value" in result["error"]


# ---------------------------------------------------------------------------
# CONVERGENT-BLIND-SPOT GUARD (the architectural property)
# ---------------------------------------------------------------------------


# Field names that, if present in a helper's parameter list, would mean
# draft prose could flow into substrate fetches. The guard fails loudly
# if any helper grows such a parameter.
_FORBIDDEN_PARAM_NAMES = frozenset({
    "anchored_claim",
    "source_text_excerpt",
    "claim",
    "draft_text",
    "draft_claim",
    "narrative",
    "artifact",
    "artifact_text",
})


@pytest.mark.parametrize(
    "fn_name",
    [
        "query_file",
        "query_state_yaml",
        "query_vault_finding",
        "query_amplitude_chart",
    ],
)
def test_substrate_query_helpers_have_no_draft_text_params(
    fn_name: str,
) -> None:
    """SIGNATURE-LEVEL GUARD.

    Each non-dispatcher helper must NOT accept any of the forbidden
    parameter names. This is the static guarantee: the helper has no
    syntax for draft prose to enter.
    """
    fn = getattr(substrate_query, fn_name)
    sig = inspect.signature(fn)
    forbidden_hits = set(sig.parameters.keys()) & _FORBIDDEN_PARAM_NAMES
    assert not forbidden_hits, (
        f"{fn_name} accepts forbidden draft-context params: "
        f"{sorted(forbidden_hits)}"
    )


def test_query_pointer_signature_takes_only_pointer_and_root() -> None:
    """The dispatcher is the one function that takes a full pointer
    record. Its parameter list must still be JUST (pointer, project_root)
    — no extra channels for draft text."""
    sig = inspect.signature(query_pointer)
    param_names = list(sig.parameters.keys())
    assert param_names == ["pointer", "project_root"]


class _SentinelTrackingDict(dict):
    """A dict subclass that records every key looked up via __getitem__
    and `.get`. Used to enforce that query_pointer reads ONLY the
    allowed fields off a pointer record at runtime."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accessed_keys: set[str] = set()

    def __getitem__(self, key):
        self.accessed_keys.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.accessed_keys.add(key)
        return super().get(key, default)


def test_substrate_query_never_includes_draft_text(tmp_path: Path) -> None:
    """RUNTIME GUARD — the load-bearing convergent-blind-spot test.

    Construct a pointer with the dangerous fields populated
    (``source_text_excerpt``, ``anchored_claim``, ``line_range``) and
    pass it to query_pointer. Assert that query_pointer reads ONLY
    ``pointer_type`` and ``pointer_value`` — never any draft-context
    field. If a future change adds e.g. ``pointer.get('anchored_claim')``
    to the dispatcher, this test fires.
    """
    target = tmp_path / "doc.md"
    target.write_text("substrate value", encoding="utf-8")

    pointer = _SentinelTrackingDict({
        "pointer_type": "file_path",
        "pointer_value": str(target),
        # These three are the contamination payload. If the dispatcher
        # touches any of them, the architectural property fails.
        "anchored_claim": (
            "the draft asserts that this value is 70.6% and won decisively"
        ),
        "source_text_excerpt": (
            "the draft asserts that this value is 70.6%"
        ),
        "line_range": {"start": 1, "end": 1},
    })

    result = query_pointer(pointer, tmp_path)
    assert result["resolved"] is True
    assert result["value"] == "substrate value"

    forbidden_accessed = pointer.accessed_keys - {
        "pointer_type", "pointer_value",
    }
    assert not forbidden_accessed, (
        f"query_pointer leaked draft-context fields: {sorted(forbidden_accessed)}"
    )


def test_query_pointer_passes_only_pointer_value_to_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end runtime check: monkeypatch each downstream helper
    to record its received arguments, then verify the dispatcher
    forwarded ONLY the pointer_value (plus project_root)."""
    captured: dict[str, tuple] = {}

    def fake_file(*args, **kwargs):
        captured["file"] = (args, kwargs)
        return _ok_stub()

    def fake_state(*args, **kwargs):
        captured["state"] = (args, kwargs)
        return _ok_stub()

    def fake_vault(*args, **kwargs):
        captured["vault"] = (args, kwargs)
        return _ok_stub()

    def fake_amp(*args, **kwargs):
        captured["amp"] = (args, kwargs)
        return _ok_stub()

    monkeypatch.setattr(substrate_query, "query_file", fake_file)
    monkeypatch.setattr(substrate_query, "query_state_yaml", fake_state)
    monkeypatch.setattr(substrate_query, "query_vault_finding", fake_vault)
    monkeypatch.setattr(substrate_query, "query_amplitude_chart", fake_amp)

    base_pointer = {
        "anchored_claim": "DRAFT_NARRATIVE_DO_NOT_LEAK",
        "source_text_excerpt": "DRAFT_NARRATIVE_DO_NOT_LEAK",
        "line_range": {"start": 1, "end": 1},
    }
    for pt, pv, key in [
        ("file_path", "/some/path", "file"),
        ("state_yaml_dotted", "state.yaml.a.b", "state"),
        ("wikilink", "myslug", "vault"),
        ("chart_id", "chart123", "amp"),
    ]:
        captured.clear()
        substrate_query.query_pointer(
            {**base_pointer, "pointer_type": pt, "pointer_value": pv},
            tmp_path,
        )
        args, kwargs = captured[key]
        joined = " ".join(repr(a) for a in args) + " " + " ".join(
            f"{k}={v!r}" for k, v in kwargs.items()
        )
        assert "DRAFT_NARRATIVE_DO_NOT_LEAK" not in joined, (
            f"helper {key} received draft narrative: {joined}"
        )


def _ok_stub() -> dict:
    return {
        "resolved": True,
        "value": None,
        "resolved_path": None,
        "error": None,
    }
