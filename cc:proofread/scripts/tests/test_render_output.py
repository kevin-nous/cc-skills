"""ccp-006 output renderer tests.

The most important test in this file is `test_anti_theatre_guard_all_fixtures`.
It runs the forbidden-token regex against every rendered output and asserts
zero matches. PRD § Anti-theatre design (5 mechanisms) makes "no green
checkmarks anywhere" structural, not policy — this test is what makes it
hold under future edits to render_output.py.

The other load-bearing assertions:
- Pre-mortem prompt is the literal LAST line of every rendered output
  (line-exact equality; no trailing whitespace).
- A Coverage line is present for every lens that has a coverage record,
  even when zero findings AND when the lens errored.
- Snapshot tests: render each of the 5 fixture scenarios and compare to
  the committed expected output. Drift between input fixtures and
  expected snapshots fails the test rather than passing silently.

Run from anywhere:
    python3 -m pytest ~/cc-skills/cc:proofread/scripts/tests/test_render_output.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make scripts/ importable regardless of CWD. ccp-001 ships __init__.py
# at scripts/__init__.py but not at scripts/tests/__init__.py, so the
# parent-of-parent path append matches the other test modules' style.
SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT))

from scripts.render_output import (  # noqa: E402
    FORBIDDEN_AFFIRMATIONS,
    PRE_MORTEM_PROMPT,
    render,
)

FIXTURES_DIR = SKILL_ROOT / "scripts" / "tests" / "fixtures" / "render"

# Discovered fixture scenarios. Keep in lockstep with the .input.jsonl /
# .expected.md pairs in fixtures/render/. New scenarios just need both
# files committed; pytest.parametrize picks them up at collection.
SCENARIOS = [
    "01-clean",
    "02-one-blocking",
    "03-multiple-advisory",
    "04-lens-failure",
    "05-convergent",
]


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            records.append(json.loads(stripped))
    return records


def _partition(records: list[dict]) -> tuple[list[dict], list[dict], dict | None]:
    findings: list[dict] = []
    coverage: list[dict] = []
    run_record: dict | None = None
    for rec in records:
        kind = rec.get("record_type")
        if kind == "finding":
            findings.append(rec)
        elif kind == "coverage":
            coverage.append(rec)
        elif kind == "run_record":
            run_record = rec
    return findings, coverage, run_record


def _render_scenario(name: str) -> str:
    input_path = FIXTURES_DIR / f"{name}.input.jsonl"
    findings, coverage, run_record = _partition(_load_jsonl(input_path))
    return render(findings, coverage, run_record)


def _expected_for(name: str) -> str:
    return (FIXTURES_DIR / f"{name}.expected.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Anti-theatre guard — the most important test in this file.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_anti_theatre_guard_all_fixtures(scenario: str) -> None:
    """Forbidden-token regex must produce ZERO matches against every
    rendered output. This is the structural mechanism that prevents
    "audit passed" / "✓ clean" language from creeping back in via a
    future tweak to render_output.py or a lens prompt change."""
    output = _render_scenario(scenario)
    matches = FORBIDDEN_AFFIRMATIONS.findall(output)
    assert matches == [], (
        f"forbidden affirmative tokens found in {scenario}: {matches!r}"
    )


# ---------------------------------------------------------------------------
# Pre-mortem prompt — literal last line, no trailing whitespace.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_pre_mortem_is_last_line(scenario: str) -> None:
    """The pre-mortem prompt MUST be the literal last line of output."""
    output = _render_scenario(scenario)
    last_line = output.splitlines()[-1]
    assert last_line == PRE_MORTEM_PROMPT, (
        f"{scenario}: last line is {last_line!r}, expected {PRE_MORTEM_PROMPT!r}"
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_no_trailing_whitespace(scenario: str) -> None:
    """Per AC: no trailing whitespace, no 'skill complete' footer."""
    output = _render_scenario(scenario)
    assert output == output.rstrip(), (
        f"{scenario}: output has trailing whitespace"
    )
    # Belt-and-braces: the literal last character must be the '?' that
    # closes PRE_MORTEM_PROMPT.
    assert output.endswith("culprit?"), (
        f"{scenario}: output does not end with PRE_MORTEM_PROMPT terminal '?'"
    )


def test_pre_mortem_prompt_constant_is_canonical() -> None:
    """The pre-mortem prompt is a pinned constant — its wording is part
    of the anti-theatre contract. If this test fails, somebody changed
    the prompt; either revert or update the PRD + this test together."""
    assert PRE_MORTEM_PROMPT == (
        "Imagine this artifact is proven wrong in 6 weeks. "
        "Which sentence is the culprit?"
    )


# ---------------------------------------------------------------------------
# Coverage transparency — every lens that ran must appear.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_coverage_line_per_lens(scenario: str) -> None:
    """For every coverage record in the input, the rendered output's
    Coverage section MUST contain a line referencing that lens — even
    when zero findings and even when the lens failed."""
    input_path = FIXTURES_DIR / f"{scenario}.input.jsonl"
    records = _load_jsonl(input_path)
    coverage_recs = [r for r in records if r.get("record_type") == "coverage"]
    output = _render_scenario(scenario)

    # Extract the Coverage section (between "## Coverage" and the next
    # "## " heading).
    lines = output.splitlines()
    start = lines.index("## Coverage")
    # Next section header.
    end = next(
        (i for i, line in enumerate(lines[start + 1 :], start=start + 1)
         if line.startswith("## ")),
        len(lines),
    )
    coverage_section = "\n".join(lines[start:end])

    lens_labels = {
        "scope": "scope auditor",
        "grounding": "grounding auditor",
        "structural": "structural auditor",
        "mechanical": "mechanical prepass",
    }
    for rec in coverage_recs:
        label = lens_labels[rec["lens"]]
        assert label in coverage_section, (
            f"{scenario}: coverage section missing line for {label}\n"
            f"--- coverage section ---\n{coverage_section}"
        )


def test_lens_failure_renders_failure_phrase() -> None:
    """When a coverage record carries an `error` field, the rendered
    output must show 'failed: <error>' both at the Findings level and
    in the Coverage line."""
    output = _render_scenario("04-lens-failure")
    assert "grounding auditor — failed: subagent dispatch failed" in output, (
        "lens failure not rendered with expected 'failed:' phrasing"
    )
    # The failure appears in two places per AC: a top-level block in
    # Findings, and the Coverage line.
    failure_phrase_count = output.count(
        "grounding auditor — failed: subagent dispatch failed"
    )
    assert failure_phrase_count == 2, (
        f"expected lens-failure phrase in 2 places (Findings + Coverage); "
        f"got {failure_phrase_count}"
    )


# ---------------------------------------------------------------------------
# Zero-findings phrasing — explicit neutral language, never "passed".
# ---------------------------------------------------------------------------


def test_clean_scenario_uses_neutral_zero_findings_phrase() -> None:
    """Per AC: when findings == [], header reads `## Findings (0)`
    followed by `No findings on this run.` — NOT 'audit passed' or
    '✓ clean'."""
    output = _render_scenario("01-clean")
    assert "## Findings (0)" in output
    assert "No findings on this run." in output
    # Negative assertions are also covered by
    # test_anti_theatre_guard_all_fixtures, but make the intent
    # legible at the read site too.
    assert "audit passed" not in output.lower()
    assert "all clear" not in output.lower()


# ---------------------------------------------------------------------------
# Quantified pass — substrate-grounded checks emit a count, not silence.
# ---------------------------------------------------------------------------


def test_grounding_quantified_pass_phrasing() -> None:
    """When grounding lens has substrate and finds nothing wrong, the
    coverage line MUST read 'grounding auditor verified N numerical
    claims against substrate; none contradicted' — silence reads as
    theatre."""
    output = _render_scenario("01-clean")
    assert (
        "grounding auditor verified 3 numerical claims against substrate; "
        "none contradicted"
    ) in output


# ---------------------------------------------------------------------------
# Convergent findings — extra visual weight via "Caught by N/3 lenses:".
# ---------------------------------------------------------------------------


def test_convergent_finding_renders_caught_by_prefix() -> None:
    """Per AC: convergent findings render with 'Caught by N/3 lenses:'
    prefix and extra visual weight. The N is derived from
    len(convergent_with) + 1, capped at 3."""
    output = _render_scenario("05-convergent")
    assert "Caught by 3/3 lenses:" in output


# ---------------------------------------------------------------------------
# Snapshot equality — drift between input fixtures and expected output
# fails the test instead of silently changing user-visible output.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_snapshot_matches_expected(scenario: str) -> None:
    rendered = _render_scenario(scenario)
    expected = _expected_for(scenario)
    if rendered != expected:
        # Produce a side-by-side diff for debug. pytest's repr-diff of
        # long strings is hard to read; build our own.
        from difflib import unified_diff
        diff = "\n".join(
            unified_diff(
                expected.splitlines(),
                rendered.splitlines(),
                fromfile=f"{scenario}.expected.md",
                tofile=f"{scenario}.rendered",
                lineterm="",
            )
        )
        pytest.fail(f"snapshot mismatch for {scenario}\n{diff}")


# ---------------------------------------------------------------------------
# Edge: empty run (no findings, no coverage) — still well-formed, still
# ends with pre-mortem.
# ---------------------------------------------------------------------------


def test_render_with_no_lenses_run_still_ends_with_pre_mortem() -> None:
    """Edge case from ccp-006 Test Plan: rendering with empty findings
    and empty coverage. The output should be well-formed (Findings (0),
    Coverage acknowledged-empty) and still end with the pre-mortem
    prompt."""
    run_record = {
        "record_type": "run_record",
        "run_id": "deadbeef-dead-4eef-8eef-deadbeefdead",
        "artifact_path": "x.md",
        "started_at": "2026-05-29T00:00:00Z",
        "completed_at": "2026-05-29T00:00:01Z",
        "mode": "default",
        "criteria_source": "default",
    }
    output = render([], [], run_record)
    assert output.splitlines()[-1] == PRE_MORTEM_PROMPT
    assert FORBIDDEN_AFFIRMATIONS.findall(output) == []
    assert "## Findings (0)" in output
    assert "## Coverage" in output
    # Acknowledge the empty coverage section honestly.
    assert "No lenses ran on this artifact." in output


def test_render_with_all_lenses_failed_still_ends_with_pre_mortem() -> None:
    """Edge case: all 3 substantive lenses failed. Output shows 3
    failure blocks + honest coverage + pre-mortem terminator."""
    run_record = {
        "record_type": "run_record",
        "run_id": "feedface-feed-4ace-8ace-feedfacefeed",
        "artifact_path": "x.md",
        "started_at": "2026-05-29T00:00:00Z",
        "completed_at": "2026-05-29T00:00:01Z",
        "mode": "default",
        "criteria_source": "default",
    }
    coverage = [
        {"record_type": "coverage", "lens": "scope",
         "checks_attempted": 6, "checks_completed": 0,
         "claims_examined": 0, "claims_flagged": 0,
         "error": "timeout"},
        {"record_type": "coverage", "lens": "grounding",
         "checks_attempted": 4, "checks_completed": 0,
         "claims_examined": 0, "claims_flagged": 0,
         "error": "rate-limited"},
        {"record_type": "coverage", "lens": "structural",
         "checks_attempted": 5, "checks_completed": 0,
         "claims_examined": 0, "claims_flagged": 0,
         "error": "malformed response"},
    ]
    output = render([], coverage, run_record)
    assert output.splitlines()[-1] == PRE_MORTEM_PROMPT
    assert FORBIDDEN_AFFIRMATIONS.findall(output) == []
    # All three failure phrases present.
    assert "scope auditor — failed: timeout" in output
    assert "grounding auditor — failed: rate-limited" in output
    assert "structural auditor — failed: malformed response" in output


# ---------------------------------------------------------------------------
# Allow `python3 path/to/test_render_output.py` invocation too, mirroring
# the other test files in this package.
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
