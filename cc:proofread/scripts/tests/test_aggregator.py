"""ccp-008 aggregator tests.

The aggregator's job is narrow: concatenate JSONL files, validate each
record, partition by record_type, log+skip invalid records. These tests
nail down all four corners so the orchestrator can rely on its return
shape.

Run:
    pytest -v ~/cc-skills/cc:proofread/scripts/tests/test_aggregator.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.aggregator import (  # noqa: E402
    CLAIM_SIMILARITY_THRESHOLD,
    MIN_LINE_RANGE_OVERLAP,
    aggregate_findings,
    aggregate_with_dedup,
    build_run_record,
)
from scripts.validate_finding import validate_finding  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _finding(lens: str, check_id: str, line: int = 1) -> dict:
    """Build a valid Finding dict matching the schema."""
    return {
        "record_type": "finding",
        "id": f"00000000-0000-4000-8000-{line:012d}",
        "lens": lens,
        "check_id": check_id,
        "severity": "advisory",
        "claim": "some claim",
        "location": {"line_range_start": line, "line_range_end": line},
        "finding": "this claim contradicts the substrate.",
        "remediation": "fix it.",
    }


def _coverage(lens: str, examined: int = 0, flagged: int = 0) -> dict:
    return {
        "record_type": "coverage",
        "lens": lens,
        "checks_attempted": 4,
        "checks_completed": 4,
        "claims_examined": examined,
        "claims_flagged": flagged,
    }


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_partitions(tmp_path: Path) -> None:
    findings, coverage, errors = aggregate_findings([])
    assert findings == []
    assert coverage == []
    assert errors == []


def test_missing_file_returns_error_not_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.jsonl"
    findings, coverage, errors = aggregate_findings([missing])
    assert findings == []
    assert coverage == []
    assert len(errors) == 1
    assert "file not found" in errors[0]


def test_empty_jsonl_file_returns_empty_partitions(tmp_path: Path) -> None:
    """A zero-byte JSONL file is a legitimate state (lens ran, emitted
    nothing). Should NOT produce an error."""
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    findings, coverage, errors = aggregate_findings([p])
    assert findings == []
    assert coverage == []
    assert errors == []


# ---------------------------------------------------------------------------
# Happy path — 3 JSONL files with a mix of findings + coverage
# ---------------------------------------------------------------------------


def test_three_files_partition_correctly(tmp_path: Path) -> None:
    mech = tmp_path / "mechanical.jsonl"
    _write_jsonl(mech, [
        _finding("mechanical", "hedge-promotion-regex", line=3),
        _finding("mechanical", "hedge-promotion-regex", line=7),
        _coverage("mechanical", examined=10, flagged=2),
    ])
    disc = tmp_path / "discovery.jsonl"
    _write_jsonl(disc, [
        _finding("mechanical", "citation-traceability-missing", line=4),
    ])
    scope = tmp_path / "scope.jsonl"
    _write_jsonl(scope, [
        _finding("scope", "scope-drift", line=2),
        _coverage("scope", examined=8, flagged=1),
    ])

    findings, coverage, errors = aggregate_findings([mech, disc, scope])

    assert len(findings) == 4
    assert len(coverage) == 2
    assert errors == []

    # Coverage records preserved with their lens labels.
    lenses = {c["lens"] for c in coverage}
    assert lenses == {"mechanical", "scope"}

    # Findings preserved across lenses.
    finding_lenses = sorted(f["lens"] for f in findings)
    assert finding_lenses == ["mechanical", "mechanical", "mechanical", "scope"]


# ---------------------------------------------------------------------------
# Robustness — invalid lines are skipped + logged, valid lines aggregated
# ---------------------------------------------------------------------------


def test_invalid_json_line_is_skipped(tmp_path: Path) -> None:
    p = tmp_path / "mixed.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(_finding("scope", "scope-drift")) + "\n")
        fh.write("not json at all\n")
        fh.write(json.dumps(_coverage("scope")) + "\n")
    findings, coverage, errors = aggregate_findings([p])
    assert len(findings) == 1
    assert len(coverage) == 1
    assert len(errors) == 1
    assert "invalid JSON" in errors[0]


def test_schema_invalid_record_is_skipped(tmp_path: Path) -> None:
    """A line that parses as JSON but fails schema validation must be
    skipped and logged — the orchestrator should not crash on a single
    bad lens output."""
    p = tmp_path / "bad_schema.jsonl"
    bad = {"record_type": "finding", "lens": "not-a-real-lens"}
    good = _coverage("scope", examined=3, flagged=0)
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(bad) + "\n")
        fh.write(json.dumps(good) + "\n")
    findings, coverage, errors = aggregate_findings([p])
    assert findings == []
    assert len(coverage) == 1
    assert len(errors) == 1
    assert "schema validation failed" in errors[0]


def test_blank_lines_silently_ignored(tmp_path: Path) -> None:
    p = tmp_path / "with_blanks.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write("\n\n")
        fh.write(json.dumps(_coverage("scope")) + "\n")
        fh.write("\n")
    findings, coverage, errors = aggregate_findings([p])
    assert coverage == [_coverage("scope")]
    assert errors == []


# ---------------------------------------------------------------------------
# Run record construction
# ---------------------------------------------------------------------------


def test_build_run_record_validates_shape() -> None:
    rec = build_run_record(
        run_id="00000000-0000-4000-8000-000000000001",
        started_at=datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 5, 29, 12, 0, 30, tzinfo=timezone.utc),
        mode="default",
        criteria_source="/path/to/default-criteria.md",
        artifact_path="vault/knowledge/some-file.md",
    )
    assert rec["record_type"] == "run_record"
    assert rec["started_at"] == "2026-05-29T12:00:00Z"
    assert rec["completed_at"] == "2026-05-29T12:00:30Z"
    assert rec["mode"] == "default"
    assert rec["artifact_path"] == "vault/knowledge/some-file.md"


def test_build_run_record_requires_path_or_hash() -> None:
    with pytest.raises(ValueError, match="artifact_path or artifact_hash"):
        build_run_record(
            run_id="00000000-0000-4000-8000-000000000001",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            mode="default",
            criteria_source="x",
        )


def test_build_run_record_accepts_artifact_hash() -> None:
    rec = build_run_record(
        run_id="00000000-0000-4000-8000-000000000001",
        started_at=datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 5, 29, 12, 0, 1, tzinfo=timezone.utc),
        mode="default",
        criteria_source="/x",
        artifact_hash="a" * 64,
    )
    assert rec["artifact_hash"] == "a" * 64
    assert "artifact_path" not in rec


# ===========================================================================
# ccp-011 — convergent-finding dedup
# ===========================================================================


def _f(
    *,
    lens: str,
    check_id: str = "scope-drift",
    severity: str = "advisory",
    claim: str = "default claim",
    start: int = 1,
    end: int | None = None,
    finding: str = "this claim contradicts X.",
    remediation: str = "fix it.",
    confidence: float | None = None,
    fid: str | None = None,
) -> dict:
    """Build a schema-valid Finding for dedup tests."""
    if end is None:
        end = start
    rec: dict = {
        "record_type": "finding",
        "id": fid or f"00000000-0000-4000-8000-{start:012d}",
        "lens": lens,
        "check_id": check_id,
        "severity": severity,
        "claim": claim,
        "location": {"line_range_start": start, "line_range_end": end},
        "finding": finding,
        "remediation": remediation,
    }
    if confidence is not None:
        rec["confidence"] = confidence
    return rec


def test_dedup_empty_input_returns_empty() -> None:
    assert aggregate_with_dedup([]) == []


def test_dedup_single_finding_passes_through() -> None:
    f = _f(lens="scope", claim="V2 won decisively.", start=10)
    out = aggregate_with_dedup([f])
    assert out == [f]


def test_dedup_three_lenses_same_claim_same_location_converge() -> None:
    """Same claim verbatim + same line, three lenses → one convergent
    with all three contributors. Mirrors the canonical case the
    convergent-render fixture (render/05-convergent) targets."""
    claim = "V2 comfortably met the success bar on signup_completed."
    a = _f(
        lens="scope", check_id="alternative-interpretation-missing",
        severity="advisory", claim=claim, start=23,
        confidence=0.7, fid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    b = _f(
        lens="structural", check_id="hedge-vs-data-semantic",
        severity="blocking", claim=claim, start=23,
        confidence=0.9, fid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    c = _f(
        lens="mechanical", check_id="hedge-promotion-regex",
        severity="advisory", claim=claim, start=23,
        confidence=0.6, fid="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    )
    out = aggregate_with_dedup([a, b, c])
    assert len(out) == 1
    merged = out[0]
    assert merged["lens"] == "convergent"
    # Blocking severity wins → structural's check_id + severity inherited.
    assert merged["severity"] == "blocking"
    assert merged["check_id"] == "hedge-vs-data-semantic"
    # Primary's id stays on the record; other contributors in
    # convergent_with.
    assert merged["id"] == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    assert set(merged["convergent_with"]) == {
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    }
    # Average confidence: (0.7 + 0.9 + 0.6) / 3 = 0.7333...
    assert merged["confidence"] == pytest.approx(0.7333, abs=1e-4)


def test_dedup_low_similarity_does_not_converge() -> None:
    """Two claims that are textually adjacent but below the threshold
    (~0.7 SequenceMatcher ratio) must NOT cluster — guards against
    over-eager merging."""
    a = _f(
        lens="scope",
        claim="The V2 cohort reached parity on signup_completed by day 14.",
        start=10,
    )
    b = _f(
        lens="structural",
        claim="V2 outperforms control on the headline switch rate.",
        start=10,
    )
    # Sanity: ratio should land below 0.80.
    from difflib import SequenceMatcher

    ratio = SequenceMatcher(None, a["claim"], b["claim"]).ratio()
    assert ratio < CLAIM_SIMILARITY_THRESHOLD
    out = aggregate_with_dedup([a, b])
    assert len(out) == 2
    lenses = {f["lens"] for f in out}
    assert lenses == {"scope", "structural"}


def test_dedup_same_claim_non_overlapping_lines_does_not_converge() -> None:
    """Identical claim text but disjoint line ranges → not convergent.
    Same wording in two paragraphs is still two separate findings."""
    claim = "V2 won decisively on signup_completed."
    a = _f(lens="grounding", claim=claim, start=10, end=12)
    b = _f(lens="structural", claim=claim, start=40, end=42)
    out = aggregate_with_dedup([a, b])
    assert len(out) == 2
    assert all(f["lens"] != "convergent" for f in out)


def test_dedup_mechanical_and_structural_may_converge() -> None:
    """PRD §Edge Cases: mechanical hedge-regex hit + structural
    hedge-vs-data finding on the same word ARE allowed to converge."""
    claim = "V2 comfortably met the success bar."
    mech = _f(
        lens="mechanical", check_id="hedge-promotion-regex",
        severity="advisory", claim=claim, start=23,
        fid="11111111-1111-4111-8111-111111111111",
    )
    struct = _f(
        lens="structural", check_id="hedge-vs-data-semantic",
        severity="blocking", claim=claim, start=23,
        fid="22222222-2222-4222-8222-222222222222",
    )
    out = aggregate_with_dedup([mech, struct])
    assert len(out) == 1
    assert out[0]["lens"] == "convergent"
    # Structural (blocking) wins the primary.
    assert out[0]["severity"] == "blocking"
    assert out[0]["check_id"] == "hedge-vs-data-semantic"


def test_dedup_severity_tie_breaks_by_lens_enum_order() -> None:
    """When two contributors share the highest severity, the
    convergent record inherits from the earlier lens per the documented
    order: scope < grounding < structural < mechanical."""
    claim = "V2 won decisively on signup_completed."
    # Both blocking; grounding should win (scope=0, grounding=1, structural=2).
    # We INTENTIONALLY put structural FIRST in the input to prove that
    # input order doesn't override lens-rank tie-break.
    struct = _f(
        lens="structural", check_id="number-source-divergence",
        severity="blocking", claim=claim, start=20,
        fid="11111111-1111-4111-8111-111111111111",
    )
    ground = _f(
        lens="grounding", check_id="contradicting-data-inventory",
        severity="blocking", claim=claim, start=20,
        fid="22222222-2222-4222-8222-222222222222",
    )
    out = aggregate_with_dedup([struct, ground])
    assert len(out) == 1
    merged = out[0]
    # grounding wins the tie-break, even though structural appears first.
    assert merged["check_id"] == "contradicting-data-inventory"
    assert merged["id"] == "22222222-2222-4222-8222-222222222222"


def test_dedup_standalone_and_convergent_coexist() -> None:
    """Mixed input: two convergent + one standalone → 1 merged + 1
    standalone (2 records, preserved input order)."""
    a = _f(
        lens="scope", claim="V2 won decisively on signup_completed.",
        severity="advisory", start=10,
        fid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    b = _f(
        lens="structural", claim="V2 won decisively on signup_completed.",
        severity="blocking", start=10,
        fid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    standalone = _f(
        lens="grounding", check_id="stale-reference",
        severity="advisory", claim="The 14d window number is stale.",
        start=80, fid="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    )
    out = aggregate_with_dedup([a, b, standalone])
    assert len(out) == 2
    convergent = [f for f in out if f["lens"] == "convergent"]
    standalones = [f for f in out if f["lens"] != "convergent"]
    assert len(convergent) == 1
    assert len(standalones) == 1
    assert standalones[0]["check_id"] == "stale-reference"


def test_dedup_overlapping_line_ranges_converge() -> None:
    """Partial line overlap (>= MIN_LINE_RANGE_OVERLAP) plus high-similarity
    claim → converge. Guards against requiring exact line equality."""
    claim = "V2 won decisively on signup_completed."
    a = _f(lens="scope", claim=claim, start=10, end=15)
    b = _f(lens="grounding", claim=claim, start=14, end=20)
    overlap_lines = max(0, min(15, 20) - max(10, 14) + 1)
    assert overlap_lines >= MIN_LINE_RANGE_OVERLAP
    out = aggregate_with_dedup([a, b])
    assert len(out) == 1
    assert out[0]["lens"] == "convergent"


def test_dedup_transitive_clustering() -> None:
    """Single-linkage closure: if A~B and B~C cluster but A~C don't
    directly meet the threshold, all three still merge. Documents the
    clustering semantic."""
    # Three claims; A and C differ enough that direct similarity drops
    # below 0.80, but both are highly similar to B.
    a = _f(
        lens="scope",
        claim="V2 won decisively on signup_completed across May 12.",
        start=10, fid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    b = _f(
        lens="grounding",
        claim="V2 won decisively on signup_completed across May 12.",
        start=10, severity="blocking",
        fid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    c = _f(
        lens="structural",
        claim="V2 won decisively on signup_completed across May 12.",
        start=10, fid="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    )
    out = aggregate_with_dedup([a, b, c])
    assert len(out) == 1
    merged = out[0]
    assert merged["lens"] == "convergent"
    assert len(merged["convergent_with"]) == 2


def test_dedup_preserves_evidence_from_primary() -> None:
    """Grounding findings carry an Evidence dict; it must travel onto
    the merged record so render_output can surface the substrate
    counter-evidence block."""
    claim = "V2 won decisively on signup_completed."
    ground = _f(
        lens="grounding", check_id="contradicting-data-inventory",
        severity="blocking", claim=claim, start=20,
        fid="11111111-1111-4111-8111-111111111111",
    )
    ground["evidence"] = {
        "substrate_field": "state.yaml.experiments.v2.note",
        "substrate_value": "May 12 parity (delta +0.3pp)",
        "draft_claim": claim,
    }
    struct = _f(
        lens="structural", check_id="hedge-vs-data-semantic",
        severity="advisory", claim=claim, start=20,
        fid="22222222-2222-4222-8222-222222222222",
    )
    out = aggregate_with_dedup([ground, struct])
    assert len(out) == 1
    assert out[0]["evidence"]["substrate_field"] == \
        "state.yaml.experiments.v2.note"


def test_dedup_merged_record_passes_schema_validation() -> None:
    """The merged convergent record MUST round-trip through the same
    validator the orchestrator uses before handing to render_output."""
    claim = "V2 won decisively on signup_completed."
    a = _f(
        lens="scope", claim=claim, severity="advisory", start=20,
        fid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", confidence=0.7,
    )
    b = _f(
        lens="grounding", check_id="contradicting-data-inventory",
        claim=claim, severity="blocking", start=20,
        fid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", confidence=0.9,
    )
    out = aggregate_with_dedup([a, b])
    assert len(out) == 1
    # If this raises, the orchestrator → renderer pipeline would die at
    # the schema gate before render_output ever sees the record.
    validate_finding(out[0])


def test_dedup_render_output_round_trip() -> None:
    """End-to-end: dedup output → render_output.render() produces the
    'Caught by N/3 lenses:' prefix that ccp-006's renderer ships."""
    from scripts.render_output import render

    claim = "V2 comfortably met the success bar on signup_completed."
    a = _f(
        lens="scope", check_id="alternative-interpretation-missing",
        severity="advisory", claim=claim, start=23,
        fid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    b = _f(
        lens="structural", check_id="hedge-vs-data-semantic",
        severity="blocking", claim=claim, start=23,
        fid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    deduped = aggregate_with_dedup([a, b])
    rendered = render(
        findings=deduped,
        coverage=[],
        run_record=None,
    )
    assert "Caught by 2/3 lenses:" in rendered
    assert "hedge-vs-data-semantic" in rendered
    # And no "scope auditor" / "structural auditor" header for the
    # merged record (the convergent prefix replaces the per-lens header).
    assert "scope auditor — alternative-interpretation-missing" not in rendered


def test_dedup_v2_may_12_parity_fixture_e2e() -> None:
    """Feed the V2 retention writeup fixture (grounding + scope both
    flagging the May 12 cohort parity contradiction) through the dedup
    layer; verify exactly one convergent record falls out and that it
    inherits grounding's evidence + check_id (grounding is blocking,
    scope is advisory)."""
    fixture = (
        SKILL_ROOT
        / "scripts"
        / "tests"
        / "fixtures"
        / "aggregator"
        / "v2_may_12_parity.input.jsonl"
    )
    records: list[dict] = []
    with fixture.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            records.append(json.loads(stripped))
    out = aggregate_with_dedup(records)
    assert len(out) == 1
    merged = out[0]
    assert merged["lens"] == "convergent"
    # Grounding (blocking) beats scope (advisory) for primary.
    assert merged["check_id"] == "contradicting-data-inventory"
    assert merged["severity"] == "blocking"
    assert merged["evidence"]["substrate_field"].startswith(
        "state.yaml.experiments.checkout_redesign_v2"
    )
    assert merged["convergent_with"] == [
        "22222222-2222-4222-8222-222222222222"
    ]


def test_dedup_passes_through_non_finding_records() -> None:
    """Defensive: orchestrator shouldn't pass coverage/run_record into
    this function, but if it does, they should not be lost."""
    cov = {
        "record_type": "coverage",
        "lens": "scope",
        "checks_attempted": 6,
        "checks_completed": 6,
        "claims_examined": 4,
        "claims_flagged": 1,
    }
    f = _f(lens="scope", claim="A claim.", start=5)
    out = aggregate_with_dedup([cov, f])
    assert cov in out
    assert f in out


# ===========================================================================
# ccp-020 — synthetic coverage on silent lens-output rejection
# ===========================================================================
#
# Bank-details smoke test (2026-05-29) motivated this: structural emitted
# 10 substantive findings with `location` as a prose string instead of a
# Location dict; ALL got silently rejected; the coverage transparency
# line omitted structural entirely. The aggregator now emits a synthetic
# coverage record so the renderer's lens-failure render path
# (ccp-006 wave 2) can surface the gap.
#
# The synthetic-record shape (`error:` field) is contractually consumed
# by ccp-021's frontmatter tag-writer to refuse the `proofread-passed`
# tag when any lens silently failed. Adding fields is safe; the keyed
# ones (`record_type`, `lens`, `error`) are load-bearing.


def _invalid_finding(lens: str, check_id: str = "title-mismatch") -> dict:
    """A finding-shaped record whose `location` is a prose string instead
    of a Location dict — schema-invalid in exactly the way the
    bank-details smoke test surfaced. Used to seed the all-rejected
    fixture programmatically."""
    return {
        "record_type": "finding",
        "id": "00000000-0000-4000-8000-000000000099",
        "lens": lens,
        "check_id": check_id,
        "severity": "advisory",
        "claim": "Some claim.",
        # Wrong shape: prose string instead of Location dict.
        "location": "the title and first paragraph",
        "finding": "something something.",
        "remediation": "fix it.",
    }


def _invalid_coverage(lens: str) -> dict:
    """A coverage-shaped record whose `claims_examined` is a string
    instead of an int — schema-invalid. Used to seed the
    coverage-rejected-but-findings-validated fixture."""
    return {
        "record_type": "coverage",
        "lens": lens,
        "checks_attempted": 6,
        "checks_completed": 6,
        # Wrong shape.
        "claims_examined": "all paragraphs reviewed end-to-end",
        "claims_flagged": 0,
    }


def test_ccp020_all_findings_rejected_emits_synthetic_coverage(tmp_path: Path) -> None:
    """The motivating case: structural emits N findings, all schema-invalid,
    no valid coverage record. The aggregator MUST emit a synthetic
    coverage record so the renderer doesn't silently omit structural
    from the Coverage transparency surface."""
    p = tmp_path / "structural.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for i in range(10):
            rec = _invalid_finding("structural", check_id=f"check-{i:02d}")
            fh.write(json.dumps(rec) + "\n")

    findings, coverage, errors = aggregate_findings([p])

    # No valid findings — they all got rejected.
    assert findings == []
    # But coverage gains a synthetic record naming structural.
    assert len(coverage) == 1
    synthetic = coverage[0]
    assert synthetic["record_type"] == "coverage"
    assert synthetic["lens"] == "structural"
    # The error field is the load-bearing surface ccp-021 keys off.
    assert "error" in synthetic
    assert "10 findings emitted" in synthetic["error"]
    assert "0 schema-valid" in synthetic["error"]
    assert "lens output rejected" in synthetic["error"]
    # Sentinel -1 for unknown counts.
    assert synthetic["checks_attempted"] == -1
    assert synthetic["claims_examined"] == -1
    assert synthetic["checks_completed"] == 0
    assert synthetic["claims_flagged"] == 0
    # The underlying per-line validation errors are still in the
    # `errors` list — synthetic-coverage augments, doesn't replace.
    assert len(errors) == 10


def test_ccp020_bank_details_style_fixture_e2e(tmp_path: Path) -> None:
    """Replay the bank-details smoke test (2026-05-29): structural lens
    emits 10 invalid findings + 1 invalid coverage. End-to-end the
    aggregator surfaces structural as a failed lens AND the renderer
    emits both a top-level `### structural auditor — failed: ...`
    block and a coverage line."""
    from scripts.render_output import render

    fixture = (
        SKILL_ROOT
        / "scripts"
        / "tests"
        / "fixtures"
        / "aggregator"
        / "bank_details_structural_all_rejected.input.jsonl"
    )
    findings, coverage, errors = aggregate_findings([fixture])

    assert findings == []
    assert len(coverage) == 1
    synthetic = coverage[0]
    assert synthetic["lens"] == "structural"
    assert "error" in synthetic
    # 11 lines attempted (10 findings + 1 coverage), all rejected.
    assert len(errors) == 11

    # Render the failure path. The renderer's existing
    # `_render_lens_failure_block` (ccp-006 wave 2) catches `error:` on
    # the coverage record and emits both a Findings block and a
    # Coverage line.
    rendered = render(findings=[], coverage=coverage, run_record=None)
    assert "### structural auditor — failed:" in rendered
    assert "structural auditor — failed:" in rendered  # appears once in coverage too
    assert "10 findings emitted, 0 schema-valid" in rendered


def test_ccp020_findings_validate_but_coverage_rejected_emits_synthetic(tmp_path: Path) -> None:
    """Per AC #3: when N findings validate but the lens's coverage
    record is schema-invalid, emit the same synthetic block.

    Why this case is distinct from "lens never emitted a coverage
    record" (legitimate discovery-stream shape): we observed an actual
    coverage line in the file, and it failed validation. Silent omission
    of a broken coverage record is the same anti-theatre violation as
    silent omission of a broken finding."""
    p = tmp_path / "structural.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        # 2 valid findings.
        fh.write(json.dumps(_finding("structural", "title-mismatch", line=10)) + "\n")
        fh.write(json.dumps(_finding("structural", "denominator-shift", line=42)) + "\n")
        # 1 malformed coverage record.
        fh.write(json.dumps(_invalid_coverage("structural")) + "\n")

    findings, coverage, errors = aggregate_findings([p])

    # The 2 findings made it through.
    assert len(findings) == 2
    # Coverage contains the synthetic, NOT the rejected original.
    assert len(coverage) == 1
    synthetic = coverage[0]
    assert synthetic["record_type"] == "coverage"
    assert synthetic["lens"] == "structural"
    assert "error" in synthetic
    assert "2 findings emitted" in synthetic["error"]
    # 1 validation error from the bad coverage record.
    assert len(errors) == 1
    assert "schema validation failed" in errors[0]


def test_ccp020_partial_rejection_mixed_does_not_synthesize(tmp_path: Path) -> None:
    """Mixed case: some findings reject, others validate, and the coverage
    record validates. The lens DID land output on the coverage surface —
    no synthetic record needed. The rejected findings are accounted for
    in the `errors` list and the aggregation-warnings tail; this isn't
    a silent-lens-failure scenario."""
    p = tmp_path / "structural.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(_finding("structural", "title-mismatch", line=10)) + "\n")
        # Schema-invalid finding.
        fh.write(json.dumps(_invalid_finding("structural", check_id="bad-shape")) + "\n")
        fh.write(json.dumps(_finding("structural", "denominator-shift", line=42)) + "\n")
        fh.write(json.dumps(_coverage("structural", examined=8, flagged=2)) + "\n")

    findings, coverage, errors = aggregate_findings([p])

    # Only the valid findings.
    assert len(findings) == 2
    # ONE coverage record — the original (valid) one. No synthetic
    # appended; the renderer can read the real counts straight off it.
    assert len(coverage) == 1
    assert coverage[0]["claims_examined"] == 8
    assert "error" not in coverage[0]
    # The rejected finding is in the errors list (aggregation-warnings tail).
    assert len(errors) == 1


def test_ccp020_missing_file_does_not_synthesize(tmp_path: Path) -> None:
    """AC #4: if the JSONL file never existed (subagent dispatch
    failed), keep existing behavior — report as missing lens, no
    synthetic-coverage record. The orchestrator handles missing-file
    semantics differently (it knows the lens was scheduled but never
    landed); the aggregator's synthetic-coverage path only fires on
    files that DID land output and DID get rejected."""
    missing = tmp_path / "structural.jsonl"
    # Deliberately do NOT create the file.

    findings, coverage, errors = aggregate_findings([missing])

    assert findings == []
    assert coverage == []  # NO synthetic
    assert len(errors) == 1
    assert "file not found" in errors[0]


def test_ccp020_empty_file_does_not_synthesize(tmp_path: Path) -> None:
    """An empty JSONL file is a legitimate state (lens ran, emitted
    nothing). NO synthetic-coverage record — there's nothing rejected
    to surface. The orchestrator decides whether the empty output is
    itself a problem."""
    p = tmp_path / "structural.jsonl"
    p.write_text("", encoding="utf-8")

    findings, coverage, errors = aggregate_findings([p])

    assert findings == []
    assert coverage == []  # NO synthetic
    assert errors == []


def test_ccp020_only_blank_lines_does_not_synthesize(tmp_path: Path) -> None:
    """A JSONL file with only blank lines is functionally empty; the
    aggregator's blank-line skip MUST short-circuit the synthetic-
    coverage path so we don't fabricate a failure for a file the lens
    legitimately wrote nothing to."""
    p = tmp_path / "structural.jsonl"
    p.write_text("\n\n\n", encoding="utf-8")

    findings, coverage, errors = aggregate_findings([p])

    assert findings == []
    assert coverage == []  # NO synthetic
    assert errors == []


def test_ccp020_lens_resolved_from_jsonl_record_when_present(tmp_path: Path) -> None:
    """Lens label on the synthetic record comes from the rejected
    record's own `lens` field when present — most accurate, because
    the lens authored it. Filename stem is fallback only."""
    # Filename stem says `mechanical`, but the rejected lines self-
    # identify as `scope`. The synthetic record should name `scope`.
    p = tmp_path / "mechanical.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(_invalid_finding("scope")) + "\n")

    findings, coverage, errors = aggregate_findings([p])

    assert coverage[0]["lens"] == "scope"


def test_ccp020_lens_resolved_from_filename_when_jsonl_unparseable(tmp_path: Path) -> None:
    """When every line is JSON-garbage (no parseable `lens` field), the
    synthetic record falls back to the filename stem mapped via
    _STEM_TO_LENS so the user still sees WHICH lens went silent.
    Discovery stems map to the mechanical lens (it shares the label)."""
    p = tmp_path / "structural.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write("{still not valid json\n")
        fh.write("} } }\n")

    findings, coverage, errors = aggregate_findings([p])

    assert len(coverage) == 1
    assert coverage[0]["lens"] == "structural"
    # All three lines rejected as JSON-invalid.
    assert len(errors) == 3


def test_ccp020_discovery_stem_maps_to_mechanical_lens(tmp_path: Path) -> None:
    """The orchestrator writes `discovery.jsonl` for the discovery
    sub-step of the mechanical lens. When discovery output is all
    JSON-garbage, the synthetic record should name `mechanical` — the
    lens the discovery step belongs to."""
    p = tmp_path / "discovery.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write("not parseable\n")

    findings, coverage, errors = aggregate_findings([p])

    assert len(coverage) == 1
    assert coverage[0]["lens"] == "mechanical"


def test_ccp020_synthetic_coverage_has_error_field_for_ccp021_consumer(tmp_path: Path) -> None:
    """ccp-021 (frontmatter tag-writer) keys off `error:` on coverage
    records to refuse the `proofread-passed` tag when any lens
    silently failed. This test PINS the surface ccp-021 depends on —
    changing the keys here is a breaking change to ccp-021's contract."""
    p = tmp_path / "structural.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for i in range(3):
            fh.write(json.dumps(_invalid_finding("structural", check_id=f"c-{i}")) + "\n")

    findings, coverage, errors = aggregate_findings([p])

    assert len(coverage) == 1
    synthetic = coverage[0]
    # Load-bearing keys for ccp-021's refusal check.
    assert synthetic["record_type"] == "coverage"
    assert synthetic["lens"] == "structural"
    assert isinstance(synthetic["error"], str)
    assert synthetic["error"]  # non-empty
    # ccp-021's diagnostic message templates off the error string,
    # which should describe what went wrong without leaking the entire
    # validator stack trace.
    assert "lens output rejected" in synthetic["error"]


def test_ccp020_findings_pass_through_dedup_with_synthetic_coverage(tmp_path: Path) -> None:
    """Defensive: after aggregate_findings emits a synthetic coverage,
    the orchestrator's next step (aggregate_with_dedup) only operates
    on findings — the synthetic must NOT accidentally get pulled into
    dedup and silently dropped. Coverage records pass through dedup
    untouched."""
    p = tmp_path / "structural.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for i in range(2):
            fh.write(json.dumps(_invalid_finding("structural", check_id=f"c-{i}")) + "\n")

    findings, coverage, errors = aggregate_findings([p])

    # No valid findings → dedup is trivially a no-op, but the synthetic
    # coverage must survive a hypothetical roundtrip that feeds it into
    # aggregate_with_dedup defensively (the orchestrator doesn't do
    # this, but other callers might).
    deduped = aggregate_with_dedup(findings + coverage)
    # Coverage record passes through.
    assert coverage[0] in deduped


def test_ccp020_renderer_emits_failure_block_in_findings_and_coverage(tmp_path: Path) -> None:
    """End-to-end with the renderer: a synthetic coverage record
    surfaces in BOTH the Findings section (top-level `### lens —
    failed: ...` block) AND the Coverage section (one-liner). The
    user can't dismiss the audit without seeing the gap."""
    from scripts.render_output import render

    p = tmp_path / "structural.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for i in range(4):
            fh.write(json.dumps(_invalid_finding("structural", check_id=f"c-{i}")) + "\n")

    findings, coverage, errors = aggregate_findings([p])
    rendered = render(findings=findings, coverage=coverage, run_record=None)

    # Findings section: the failure block.
    assert "### structural auditor — failed:" in rendered
    # Coverage section: the one-line entry.
    assert "- structural auditor — failed:" in rendered
    # Specific error text propagates to the user.
    assert "4 findings emitted, 0 schema-valid" in rendered


def test_ccp020_findings_valid_coverage_rejected_fixture_e2e() -> None:
    """Replay the fixture-form of AC #3: findings validate, coverage
    record specifically fails. Verifies the same synthetic-error
    surface ships for both rejection modes."""
    fixture = (
        SKILL_ROOT
        / "scripts"
        / "tests"
        / "fixtures"
        / "aggregator"
        / "structural_findings_valid_coverage_rejected.input.jsonl"
    )
    findings, coverage, errors = aggregate_findings([fixture])

    assert len(findings) == 2
    assert all(f["lens"] == "structural" for f in findings)
    assert len(coverage) == 1
    synthetic = coverage[0]
    assert synthetic["lens"] == "structural"
    assert "error" in synthetic
    assert "2 findings emitted" in synthetic["error"]
    assert len(errors) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
