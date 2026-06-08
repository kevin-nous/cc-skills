"""Tests for default-criteria.md structure and domain-neutrality.

The criteria file is YAML-with-markdown: a YAML frontmatter block
followed by markdown body that embeds per-check YAML blocks (one
fenced ```yaml block per check). The orchestrator parses the fenced
YAML blocks programmatically; humans read the markdown around them.

Tests assert:
1. File parses (frontmatter is valid YAML; embedded check blocks all
   parse as valid YAML)
2. All 12 substantive + 3 mechanical checks present by id
3. Zero matches for project-specific terms
4. Each check has the required fields
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Path resolution: this test sits at
# cc:proofread/scripts/tests/test_criteria_validation.py;
# default-criteria.md sits at cc:proofread/default-criteria.md.
CRITERIA_PATH = Path(__file__).resolve().parents[2] / "default-criteria.md"


# Substantive checks: 6 scope + 4 grounding + 6 structural (incl. meta) = 16
# Mechanical checks: 4 (PRD §Technical Considerations lists "3 mechanical"
# but enumerates 4 items; AC ccp-002 confirms 4 mechanical names).
EXPECTED_CHECK_IDS = {
    # Scope (6)
    "scope-drift",
    "capability-from-rate-evidence",
    "alternative-interpretation-missing",
    "mechanism-as-clock",
    "maturity-surface-missing",
    "confound-inventory-missing",
    # Grounding (4)
    "stale-references",
    "baseline-not-checked-attribution",
    "contradicting-data-not-addressed",
    "citation-traceability-missing",
    # Structural (5 + 1 meta-flag = 6)
    "zombie-claims",
    "number-source-divergence",
    "narrative-table-contradiction",
    "hedge-vs-data-semantic",
    "causal-claim-labelling",
    "audience-fit",
    "late-added-callouts",
    # Mechanical (4)
    "hedge-promotion-regex",
    "wikilink-resolution",
    "could-vs-will-detection",
    "acronym-on-first-use",
}

REQUIRED_CHECK_FIELDS = {
    "id",
    "description",
    "trigger_heuristic",
    "remediation_language",
    "example_failure_mode",
}

PROJECT_SPECIFIC_TERMS = [
    r"\bcohort\b",
    r"\bAmplitude\b",
    r"\bstate\.yaml\b",
    r"\bStatsig\b",
    r"\bSnowflake\b",
]


def _read_criteria_text() -> str:
    assert CRITERIA_PATH.exists(), f"default-criteria.md missing at {CRITERIA_PATH}"
    return CRITERIA_PATH.read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> dict:
    """Extract the YAML frontmatter (first --- ... --- block)."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "default-criteria.md must start with --- YAML frontmatter ---"
    return yaml.safe_load(m.group(1))


def _parse_check_blocks(text: str) -> list[dict]:
    """Extract every fenced ```yaml block in the body and parse it.

    The convention: each substantive/mechanical check is documented as
    its own fenced YAML block. The orchestrator iterates these to load
    the check registry.
    """
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
    parsed = []
    for raw in blocks:
        loaded = yaml.safe_load(raw)
        # A check block must be a mapping with an `id` field; the
        # frontmatter is fenced as --- not ```yaml so it won't appear
        # here.
        assert isinstance(loaded, dict), f"check block did not parse to dict: {raw[:80]}"
        parsed.append(loaded)
    return parsed


def test_frontmatter_has_schema_and_metadata() -> None:
    text = _read_criteria_text()
    fm = _parse_frontmatter(text)
    assert fm.get("schema_version") == 1, "schema_version must be 1"
    assert isinstance(fm.get("description"), str) and fm["description"].strip(), (
        "description must be a non-empty string"
    )
    assert "last_updated" in fm, "last_updated must be present"


def test_all_15_plus_checks_present_by_id() -> None:
    text = _read_criteria_text()
    checks = _parse_check_blocks(text)
    ids = {c["id"] for c in checks if "id" in c}
    missing = EXPECTED_CHECK_IDS - ids
    extra = ids - EXPECTED_CHECK_IDS
    assert not missing, f"missing check ids: {sorted(missing)}"
    assert not extra, f"unexpected extra check ids: {sorted(extra)}"


def test_each_check_has_required_fields() -> None:
    text = _read_criteria_text()
    checks = _parse_check_blocks(text)
    for c in checks:
        cid = c.get("id", "<unknown>")
        missing = REQUIRED_CHECK_FIELDS - set(c.keys())
        assert not missing, f"check {cid!r} missing fields: {sorted(missing)}"
        for field in REQUIRED_CHECK_FIELDS:
            value = c[field]
            assert isinstance(value, str) and value.strip(), (
                f"check {cid!r} field {field!r} must be a non-empty string"
            )


def test_no_project_specific_terms() -> None:
    text = _read_criteria_text()
    hits = []
    for pattern in PROJECT_SPECIFIC_TERMS:
        for m in re.finditer(pattern, text):
            # Capture context window for the failure message.
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            hits.append(f"{pattern!r} → …{text[start:end]}…")
    assert not hits, (
        "default-criteria.md must be domain-neutral but found "
        f"project-specific terms:\n" + "\n".join(hits)
    )


def test_header_points_at_per_project_override_pattern() -> None:
    text = _read_criteria_text()
    # The AC requires a header pointing readers at the per-project
    # override location. We assert the canonical path string appears.
    assert ".claude/cc:proofread/criteria.md" in text, (
        "default-criteria.md must reference the per-project override path "
        "'<project-root>/.claude/cc:proofread/criteria.md'"
    )


def test_four_sections_present() -> None:
    text = _read_criteria_text()
    for required_section in ("Scope auditor", "Grounding auditor", "Structural auditor", "Mechanical prepass"):
        assert required_section in text, f"missing section header: {required_section!r}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
