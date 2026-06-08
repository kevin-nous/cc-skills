"""ccp-005 discovery step — substrate pointer extraction tests.

Pytest-style. Asserts:

1. Each pointer type extracts from a fixture with rich pointers.
2. CONVERGENT-BLIND-SPOT GUARD: source_text_excerpt for every pointer
   does NOT include the sentence after the pointer (where the draft's
   interpretation lives). Hand-inspected on the V2-style fixture.
3. Citation-traceability companion check fires on naked numbers, stays
   silent on cited numbers, and emits findings that validate against
   schemas/finding.schema.json.
4. Edge cases: empty artifact, no pointers, malformed wikilink.
5. Performance: 10KB artifact processes in <200ms.
6. CLI is read-only (no writes outside stdout).
7. ccp-012 anchor: the V2 fixture's chart_id abc123xy and the named
   state.yaml dotted path are both extracted with the canonical
   pointer_value the V2 validation gate will key on.

Run from anywhere:
    pytest ~/cc-skills/cc:proofread/scripts/tests/test_discovery.py -v
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Make `scripts.discovery` importable when pytest is run from anywhere.
SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.discovery import (  # noqa: E402
    EXCERPT_BEFORE_CHARS,
    emit_records,
    extract_pointers,
    find_uncited_numerical_claims,
    main as discovery_main,
)
from scripts.validate_finding import validate_finding  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "discovery"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Pointer extraction — rich fixture
# ---------------------------------------------------------------------------

def test_v2_writeup_extracts_all_pointer_types() -> None:
    text = _read("v2_writeup.md")
    pointers = extract_pointers(text)

    types_seen = {p["pointer_type"] for p in pointers}
    assert {"file_path", "chart_id", "state_yaml_dotted", "wikilink"} <= types_seen, (
        f"missing pointer types in v2_writeup; saw {types_seen}"
    )


def test_v2_writeup_chart_id_is_abc123xy_for_ccp012_anchor() -> None:
    """ccp-012 (V2 validation gate) keys on this exact chart id."""
    text = _read("v2_writeup.md")
    chart_ids = [
        p["pointer_value"]
        for p in extract_pointers(text)
        if p["pointer_type"] == "chart_id"
    ]
    assert "abc123xy" in chart_ids, (
        f"expected ccp-012 anchor chart id abc123xy in pointers; got {chart_ids}"
    )


def test_v2_writeup_state_yaml_dotted_path_is_canonical_for_ccp012_anchor() -> None:
    """ccp-012 keys on this exact state.yaml dotted path."""
    text = _read("v2_writeup.md")
    paths = [
        p["pointer_value"]
        for p in extract_pointers(text)
        if p["pointer_type"] == "state_yaml_dotted"
    ]
    expected = (
        "state.yaml.experiments.checkout_redesign_v2"
        ".latest_read.purchase_success_early_read.note"
    )
    assert expected in paths, (
        f"expected canonical V2 dotted path; got {paths}"
    )


def test_file_path_pointers_recognise_known_shapes() -> None:
    text = _read("v2_writeup.md")
    file_paths = [
        p["pointer_value"]
        for p in extract_pointers(text)
        if p["pointer_type"] == "file_path"
    ]
    # state.yaml mentioned as a bare token
    assert any("state.yaml" == fp for fp in file_paths)
    # vault/knowledge/<slug>.md and experiments/<slug>.md from the body
    assert any(fp.startswith("vault/knowledge/") for fp in file_paths)
    assert any(fp.startswith("experiments/") for fp in file_paths)


def test_each_pointer_has_required_fields() -> None:
    text = _read("v2_writeup.md")
    for p in extract_pointers(text):
        for field in (
            "record_type",
            "pointer_type",
            "pointer_value",
            "anchored_claim",
            "source_text_excerpt",
            "line_range",
        ):
            assert field in p, f"pointer missing field {field!r}: {p}"
        assert p["record_type"] == "pointer"
        assert p["line_range"]["start"] >= 1
        assert p["line_range"]["end"] >= p["line_range"]["start"]


# ---------------------------------------------------------------------------
# CONVERGENT-BLIND-SPOT GUARD
# ---------------------------------------------------------------------------
# This is the load-bearing test. For each pointer, source_text_excerpt is
# 100 chars BEFORE the pointer — never the sentence after. The fixture is
# crafted so that the sentence after each pointer contains a literal
# phrase ("won decisively by 6.4pp" / "exceeds V5's") that we can search
# for in the excerpt to fail the test on regression.

# The fixture is crafted so that the sentence/paragraph after each pointer
# contains a clearly-labelled interpretation phrase. For each pointer, we
# look up which phrases physically come AFTER that pointer's offset in the
# fixture text, then assert none of them appear in the pointer's excerpt.
# This is stronger than a global forbidden list — it tracks the per-pointer
# boundary directly.
INTERPRETATION_PHRASES = [
    "won decisively by 6.4pp",
    "inherit the draft's blind spot",
    "exceeds V5's",
    "favourable cohort window",
    "exactly what the grounding lens MUST NOT see",
]


def test_source_text_excerpt_never_includes_text_after_pointer() -> None:
    """CONVERGENT-BLIND-SPOT GUARD.

    The architectural guarantee: source_text_excerpt is built from text
    that comes BEFORE the pointer's start offset. Text that comes after
    a pointer typically carries the draft's interpretation of the
    pointer's value; including it would leak the draft's blind spot to
    the grounding lens.

    Per-pointer check: for each pointer P, look up the substring offsets
    of every interpretation phrase in the fixture, then assert that no
    phrase whose offset is >= P's pointer offset appears in P's excerpt.
    Phrases preceding P are upstream document context and ARE legitimately
    in scope for the excerpt — the rule is "nothing FROM AFTER this
    pointer", not "nothing matching this list anywhere".
    """
    text = _read("blind_spot_guard.md")
    pointers = extract_pointers(text)
    assert pointers, "fixture should produce pointers"

    phrase_offsets = {phr: text.find(phr) for phr in INTERPRETATION_PHRASES}
    for phr, off in phrase_offsets.items():
        assert off >= 0, f"interpretation phrase {phr!r} not present in fixture"

    for p in pointers:
        # Find this pointer's offset in the source. Use the same
        # search-token convention as the white-box slice test.
        if p["pointer_type"] == "chart_id":
            search_token = f"chart/{p['pointer_value']}"
        elif p["pointer_type"] == "wikilink":
            search_token = f"[[{p['pointer_value']}"
        else:
            search_token = p["pointer_value"]
        pointer_offset = text.find(search_token)
        assert pointer_offset >= 0

        excerpt = p["source_text_excerpt"]
        for phr, phr_off in phrase_offsets.items():
            if phr_off < pointer_offset:
                # This phrase is upstream of the pointer — it's allowed
                # to appear in the excerpt as ordinary leading context.
                continue
            assert phr not in excerpt, (
                f"source_text_excerpt for {p['pointer_type']}="
                f"{p['pointer_value']!r} leaked POST-POINTER text {phr!r}; "
                f"excerpt was: {excerpt!r}"
            )


def test_source_text_excerpt_slice_endpoint_equals_pointer_offset() -> None:
    """White-box assertion of the slicing rule.

    Reconstruct the slice from the artifact text. The end of the excerpt
    must equal the pointer's start offset, never beyond. This catches
    any future refactor that widens the window to include trailing
    context.
    """
    text = _read("blind_spot_guard.md")
    for p in extract_pointers(text):
        excerpt = p["source_text_excerpt"]
        pointer_value = p["pointer_value"]

        # Find where the pointer's literal value starts in text. For
        # chart_id we only emit the bare id (abc123xy) but in the
        # source the literal occurrence is `chart/abc123xy`. We search
        # for the pointer's value as a substring; if the chart id is
        # the bare form, look for the `chart/<id>` form because the
        # bare id won't appear independently.
        if p["pointer_type"] == "chart_id":
            search_token = f"chart/{pointer_value}"
        elif p["pointer_type"] == "wikilink":
            search_token = f"[[{pointer_value}"
        else:
            search_token = pointer_value
        offset = text.find(search_token)
        assert offset >= 0, f"pointer literal not found in fixture: {search_token!r}"

        expected_excerpt = text[max(0, offset - EXCERPT_BEFORE_CHARS):offset]
        assert excerpt == expected_excerpt, (
            f"excerpt for {p['pointer_type']}={pointer_value!r} did not match "
            f"the BEFORE-only slice; expected {expected_excerpt!r}, got {excerpt!r}"
        )


def test_excerpt_length_bounded_to_window() -> None:
    text = _read("v2_writeup.md")
    for p in extract_pointers(text):
        assert len(p["source_text_excerpt"]) <= EXCERPT_BEFORE_CHARS


# ---------------------------------------------------------------------------
# Citation-traceability companion check
# ---------------------------------------------------------------------------

def test_naked_numbers_emits_findings_for_each_value() -> None:
    text = _read("naked_numbers.md")
    findings = find_uncited_numerical_claims(text)
    # Three naked numerical values in the fixture:
    #   +6.4pp, 7%, 1.5x
    flagged_claims = [f["claim"] for f in findings]
    assert len(findings) >= 3, (
        f"expected >= 3 citation-traceability findings; got {len(findings)}: "
        f"{flagged_claims}"
    )


def test_cited_numbers_emits_no_citation_traceability_findings() -> None:
    text = _read("cited_numbers.md")
    findings = find_uncited_numerical_claims(text)
    assert findings == [], (
        f"expected zero findings on cited_numbers; got {len(findings)}: "
        f"{[f['claim'] for f in findings]}"
    )


def test_no_pointers_no_numbers_emits_nothing() -> None:
    text = _read("no_pointers.md")
    assert extract_pointers(text) == []
    assert find_uncited_numerical_claims(text) == []


def test_citation_traceability_findings_validate_against_schema() -> None:
    text = _read("naked_numbers.md")
    findings = find_uncited_numerical_claims(text)
    assert findings, "fixture should produce findings"
    for f in findings:
        validate_finding(f)
        assert f["lens"] == "mechanical"
        assert f["check_id"] == "citation-traceability-missing"
        assert f["severity"] == "advisory"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_artifact_returns_empty_lists() -> None:
    text = _read("empty.md")
    assert extract_pointers(text) == []
    assert find_uncited_numerical_claims(text) == []


def test_malformed_wikilink_does_not_crash() -> None:
    text = _read("malformed_wikilinks.md")
    pointers = extract_pointers(text)
    # The valid [[a-valid-link]] should appear; the malformed
    # [[unclosed... should not — and the function should not raise.
    wikilink_values = [
        p["pointer_value"]
        for p in pointers
        if p["pointer_type"] == "wikilink"
    ]
    assert "a-valid-link" in wikilink_values
    assert not any("unclosed" in v for v in wikilink_values)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

def test_10kb_artifact_processes_under_200ms() -> None:
    text = _read("perf_10kb.md")
    assert len(text) >= 9_000, f"perf fixture is {len(text)} bytes; need ~10KB"
    # warm-up to amortise import / first-run compile costs
    extract_pointers(text)
    find_uncited_numerical_claims(text)

    start = time.perf_counter()
    extract_pointers(text)
    find_uncited_numerical_claims(text)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert elapsed_ms < 200.0, (
        f"discovery + citation-traceability on 10KB took {elapsed_ms:.1f}ms; "
        f"AC requires <200ms"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_emits_jsonl_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    rc = discovery_main([str(FIXTURES / "v2_writeup.md")])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "CLI emitted no output"
    records = [json.loads(line) for line in out]
    for r in records:
        assert r["record_type"] == "pointer"
        # internal _offset field must NOT leak to stdout
        assert "_offset" not in r


def test_cli_findings_flag_emits_findings(capsys: pytest.CaptureFixture[str]) -> None:
    rc = discovery_main([str(FIXTURES / "naked_numbers.md"), "--findings"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    records = [json.loads(line) for line in out]
    finding_records = [r for r in records if r["record_type"] == "finding"]
    assert finding_records, "--findings flag did not produce findings"
    for r in finding_records:
        assert r["check_id"] == "citation-traceability-missing"


def test_cli_missing_file_returns_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = discovery_main([str(FIXTURES / "does_not_exist.md")])
    assert rc == 2


def test_cli_does_not_write_outside_stdout(tmp_path: Path) -> None:
    """Read-only contract: discovery must not write files anywhere."""
    fixture_dir_before = {p.name: p.stat().st_mtime for p in FIXTURES.iterdir()}
    rc = discovery_main([str(FIXTURES / "v2_writeup.md"), "--findings"])
    assert rc == 0
    # No new files in the fixtures directory
    fixture_dir_after = {p.name: p.stat().st_mtime for p in FIXTURES.iterdir()}
    assert set(fixture_dir_before.keys()) == set(fixture_dir_after.keys())
    # No new files in tmp (we never wrote there either)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# emit_records helper
# ---------------------------------------------------------------------------

def test_emit_records_strips_internal_underscore_fields() -> None:
    buf = io.StringIO()
    emit_records([{"record_type": "pointer", "pointer_value": "x", "_offset": 7}], stream=buf)
    payload = json.loads(buf.getvalue())
    assert "_offset" not in payload
    assert payload["pointer_value"] == "x"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
