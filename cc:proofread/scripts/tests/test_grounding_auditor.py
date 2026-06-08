"""ccp-009 grounding auditor — prompt template tests.

The grounding lens prompt is the architectural countermeasure for
"coherence promoted to correctness". Its prompt template MUST:

    1. NOT contain an ``{artifact}`` placeholder — the lens never
       sees the draft prose, only the pointers (this is the
       convergent-blind-spot guard at the prompt-template level).
    2. Contain the read-only contract verbatim.
    3. Contain the convergent-blind-spot CHECK paragraph (with the
       four-field enumeration of what the lens may see).
    4. Contain the substrate-access instruction (re-fetch
       independently; pass only ``pointer_value`` to helpers).
    5. Contain the forbidden-word ("clean") constraint from ccp-006.
    6. Have the substrate-less artifact path: when 0 pointers, the
       lens emits ``no-checkable-claims-found`` advisory + a
       coverage record with ``substrate_refs_found: 0``.

Run:
    pytest -v ~/cc-skills/cc:proofread/scripts/tests/test_grounding_auditor.py
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.substrate_query import (  # noqa: E402
    _NO_POINTERS_BANNER,
    _format_pointers_for_prompt,
    render_grounding_prompt,
)

GROUNDING_PROMPT = (
    SKILL_ROOT / "scripts" / "prompts" / "grounding_auditor.md"
)
FIXTURES = SKILL_ROOT / "scripts" / "tests" / "fixtures" / "grounding"


# ---------------------------------------------------------------------------
# Fake check object — mirrors orchestrator.ScopeCheck without importing
# the orchestrator (the orchestrator wiring is intentionally deferred to
# post-Wave-4 integration, so the test must not depend on it).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _GroundingCheck:
    id: str
    description: str
    trigger_heuristic: str
    remediation_language: str
    example_failure_mode: str


_SAMPLE_CHECKS = [
    _GroundingCheck(
        id="contradicting-data-not-addressed",
        description="A single data point in the substrate materially contradicts the conclusion and is not addressed.",
        trigger_heuristic="A substrate cell points the opposite direction.",
        remediation_language="The substrate value at X contradicts the draft claim Y.",
        example_failure_mode="V2 won decisively asserted while May 12 parity says otherwise.",
    ),
    _GroundingCheck(
        id="stale-references",
        description="A numerical claim was written from memory and not reconciled with the substrate.",
        trigger_heuristic="A round number in prose does not match any table cell.",
        remediation_language="The narrative cites N but the substrate shows M.",
        example_failure_mode="Opening says ~30-50/day; substrate shows 86/day.",
    ),
]


def _load_v2_pointers() -> list[dict]:
    out: list[dict] = []
    with (FIXTURES / "v2_pointers.jsonl").open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Static prompt-template assertions
# ---------------------------------------------------------------------------


def test_prompt_template_exists_on_disk() -> None:
    assert GROUNDING_PROMPT.exists()


def test_prompt_does_NOT_contain_artifact_placeholder() -> None:
    """THE convergent-blind-spot guard at the template level.

    The grounding lens MUST NOT have an ``{artifact}`` placeholder —
    that is what physically prevents the draft prose from flowing into
    the prompt. If a future refactor reintroduces it (e.g. by copying
    from scope_auditor.md), this test fires.
    """
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    assert "{artifact}" not in text, (
        "grounding_auditor.md contains an {artifact} placeholder — "
        "this would convergent-blind the lens by feeding it draft prose. "
        "Remove the placeholder and feed pointers only."
    )


def test_prompt_template_has_only_documented_placeholders() -> None:
    """The grounding prompt has exactly three placeholders:
    ``{output_path}``, ``{grounding_checks}``, ``{pointers}``. Anything
    else (especially ``{artifact}`` or ``{source_text_excerpt}``) is
    a convergent-blind-spot risk."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    import re

    # Match {single_word_placeholder} — same shape as the substituted
    # tokens. Exclude JSON braces by requiring no whitespace inside.
    placeholder_re = re.compile(r"\{(?!\")([a-z_]+)\}")
    found = set(placeholder_re.findall(text))
    allowed = {"output_path", "grounding_checks", "pointers"}
    assert found <= allowed, (
        f"grounding_auditor.md has unexpected placeholders: "
        f"{found - allowed}"
    )


def test_prompt_contains_read_only_contract() -> None:
    """The read-only contract paragraph is load-bearing and must
    match the scope lens's wording (the orchestrator integration
    enforces the same contract across both lenses)."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert (
        "You emit findings to the named JSONL path. You do NOT modify "
        "the artifact. You do NOT read or write any other file."
    ) in flat


def test_prompt_contains_convergent_blind_spot_check() -> None:
    """The CONVERGENT-BLIND-SPOT CHECK section must enumerate the
    four-field pointer schema explicitly and include the bug-flag
    instruction for when the prompt leaks draft text."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    # Header.
    assert "Convergent-blind-spot CHECK" in text
    # Field enumeration — at least the four allowed fields named.
    for field in ["pointer_type", "pointer_value", "anchored_claim", "line_range"]:
        assert field in text, f"expected pointer field {field!r} in prompt"
    # The architectural bug-flag instruction.
    assert "convergent-blind-spot violation" in flat or (
        "If your prompt accidentally includes draft text around the "
        "pointers, that is a bug"
    ) in flat


def test_prompt_contains_substrate_access_instruction() -> None:
    """The lens must be instructed to re-fetch independently and to
    pass only ``pointer_value`` to the helpers."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "re-fetch the substrate value INDEPENDENTLY" in flat
    assert "ONLY the `pointer_value` to substrate-query helpers" in flat
    # The blindness statement.
    assert "substrate query is blind to the draft" in flat


def test_prompt_contains_forbidden_word_constraint() -> None:
    """ccp-006 / scope-lens parity: the grounding lens must be told
    explicitly to avoid the word 'clean' — otherwise its JSONL
    findings can poison the final output."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    assert "Do not use the word \"clean\" in your findings" in text


def test_prompt_contains_substrate_unresolvable_path() -> None:
    """The lens must know how to handle a pointer whose substrate
    doesn't resolve — emit ``substrate-unresolvable`` advisory rather
    than silently dropping the pointer."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    assert "substrate-unresolvable" in text


def test_prompt_contains_no_checkable_claims_path() -> None:
    """The substrate-less artifact path must be documented in the
    prompt itself, so the lens emits the advisory rather than going
    silent."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    assert "no-checkable-claims-found" in text
    assert "substrate_refs_found: 0" in text


def test_prompt_contains_coverage_shape() -> None:
    """The grounding coverage record's substrate-specific counters
    (verified / contradicted / unresolvable) must appear in the
    prompt's example coverage block."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    for field in [
        "substrate_refs_found",
        "substrate_refs_verified",
        "substrate_refs_contradicted",
        "substrate_refs_unresolvable",
    ]:
        assert field in text, f"expected coverage field {field!r} in prompt"


# ---------------------------------------------------------------------------
# ccp-019 — STRICT location-shape instruction (lens + per-check fragments)
# ---------------------------------------------------------------------------
#
# The 2026-05-29 bank-details smoke test exposed that the structural lens
# was emitting 10 findings with prose-string locations — all silently
# rejected by the schema validator. The grounding lens dodged the bug on
# that one run because it emitted zero findings, but the same prompt
# language was at fault. ccp-019 tightens all three lens prompts and all
# 16 per-check fragments. The grounding fragments live in this file's
# ownership; the assertions below pin the contract.


_GROUNDING_FRAGMENTS = [
    "grounding-baseline-not-checked-attribution.md",
    "grounding-citation-traceability-missing.md",
    "grounding-contradicting-data-not-addressed.md",
    "grounding-stale-references.md",
]
_CHECKS_DIR = SKILL_ROOT / "scripts" / "prompts" / "checks"


def test_grounding_prompt_contains_strict_location_header() -> None:
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    assert "**Location schema (STRICT):**" in text


def test_grounding_prompt_contains_correct_location_example() -> None:
    """Literal CORRECT JSON example. Pinning the literal string defends
    against the LLM paraphrasing 'line_range_start' into 'start' (the
    most common drift, and the one that triggered the smoke-test
    rejections)."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    assert "CORRECT location:" in text
    assert (
        '"location": {"line_range_start": 38, "line_range_end": 42}'
        in text
    )


def test_grounding_prompt_contains_rejected_location_examples() -> None:
    """All four observed failure modes must appear in the REJECTED
    block: prose section-name string, prose other-string, {start, end}
    shorthand, array form."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    assert "REJECTED location:" in text
    # The grounding REJECTED block uses substrate-specific prose
    # examples (vs the section-bullet shape in scope/structural) since
    # the lens is wikilink/chart/yaml oriented.
    assert '"the wikilink in the TL;DR"' in text
    assert '"## Substrate references"' in text
    assert '{"start": 38, "end": 42}' in text
    assert "[38, 42]" in text


def test_grounding_prompt_contains_rejection_consequence_sentence() -> None:
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "will be rejected by the schema validator" in flat
    assert "lens will be reported as failed in the run's coverage line" in flat


def test_grounding_prompt_explains_pointer_to_location_mapping() -> None:
    """The grounding lens copies the integers from the pointer's
    `line_range`. The key names DIFFER (`start`/`end` →
    `line_range_start`/`line_range_end`) so the lens must be warned
    explicitly — otherwise it would pass `line_range` through
    unchanged and the validator would reject every finding."""
    text = GROUNDING_PROMPT.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "Copy these integers from the pointer's `line_range`" in flat
    assert (
        "Do not pass the pointer's `line_range` object through unchanged"
        in flat
    )


@pytest.mark.parametrize("fragment_name", _GROUNDING_FRAGMENTS)
def test_grounding_fragment_contains_strict_location_header(
    fragment_name: str,
) -> None:
    text = (_CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert "**Location schema (STRICT):**" in text, (
        f"{fragment_name} missing the strict-location header"
    )


@pytest.mark.parametrize("fragment_name", _GROUNDING_FRAGMENTS)
def test_grounding_fragment_contains_correct_location_example(
    fragment_name: str,
) -> None:
    text = (_CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert "CORRECT location:" in text, (
        f"{fragment_name} missing 'CORRECT location:' label"
    )
    assert (
        '"location": {"line_range_start": 38, "line_range_end": 42}'
        in text
    ), f"{fragment_name} missing the literal CORRECT JSON example"


@pytest.mark.parametrize("fragment_name", _GROUNDING_FRAGMENTS)
def test_grounding_fragment_contains_rejected_location_examples(
    fragment_name: str,
) -> None:
    text = (_CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert "REJECTED location:" in text, (
        f"{fragment_name} missing 'REJECTED location:' label"
    )
    assert '{"start": 38, "end": 42}' in text, (
        f"{fragment_name} missing {{start, end}} REJECTED example"
    )
    assert "[38, 42]" in text, (
        f"{fragment_name} missing array-form REJECTED example"
    )


@pytest.mark.parametrize("fragment_name", _GROUNDING_FRAGMENTS)
def test_grounding_fragment_contains_rejection_consequence_sentence(
    fragment_name: str,
) -> None:
    text = (_CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "will be rejected by the schema validator" in flat, (
        f"{fragment_name} missing 'will be rejected by the validator' "
        "sentence — the LLM may treat the schema as a recommendation"
    )
    assert "lens will be reported as failed" in flat, (
        f"{fragment_name} missing the coverage-line consequence sentence"
    )


# ---------------------------------------------------------------------------
# Rendered-prompt assertions
# ---------------------------------------------------------------------------


def test_prompt_template_renders_with_pointers(tmp_path: Path) -> None:
    """Round-trip: substitute the 3 placeholders against a real
    pointer list (the V2 fixture). The rendered prompt must contain
    the load-bearing fragments and the substituted pointer values."""
    pointers = _load_v2_pointers()
    output_path = tmp_path / "grounding.jsonl"
    rendered = render_grounding_prompt(
        pointers=pointers,
        grounding_checks=_SAMPLE_CHECKS,
        output_path=output_path,
    )

    # Placeholders are gone.
    assert "{output_path}" not in rendered
    assert "{grounding_checks}" not in rendered
    assert "{pointers}" not in rendered

    # Output path is the literal grounding.jsonl path.
    assert str(output_path) in rendered

    # Check list rendered.
    assert "contradicting-data-not-addressed" in rendered
    assert "stale-references" in rendered

    # Pointer values rendered.
    assert "checkout_redesign_v2" in rendered
    assert "abc123xy" in rendered

    # Critically: the artifact placeholder is gone (and was never in
    # the template; this re-asserts at the render layer).
    assert "{artifact}" not in rendered


def test_rendered_prompt_does_not_contain_source_text_excerpt(
    tmp_path: Path,
) -> None:
    """Render-layer enforcement of the convergent-blind-spot property.

    Even if a future change to discovery.py started emitting
    ``source_text_excerpt`` with post-pointer narrative, the grounding
    render path must drop it. The renderer rebuilds each pointer
    record with only the four allowed fields.
    """
    tainted_pointer = {
        "pointer_type": "file_path",
        "pointer_value": "vault/knowledge/foo.md",
        "anchored_claim": "the claim being audited",
        "line_range": {"start": 1, "end": 1},
        "source_text_excerpt": (
            "DRAFT_NARRATIVE_LEAKED — the draft asserts this means X"
        ),
    }
    rendered = render_grounding_prompt(
        pointers=[tainted_pointer],
        grounding_checks=_SAMPLE_CHECKS,
        output_path=tmp_path / "g.jsonl",
    )
    assert "DRAFT_NARRATIVE_LEAKED" not in rendered
    # source_text_excerpt field name itself should also not appear in
    # the rendered pointer block (the template MAY mention it in prose
    # — that's fine; the runtime data must not include it).
    # We verify by inspecting the pointers section specifically.
    pointers_section = rendered.split("---POINTERS---", 1)[1]
    assert "source_text_excerpt" not in pointers_section
    assert "DRAFT_NARRATIVE_LEAKED" not in pointers_section


def test_substrate_less_prompt_path(tmp_path: Path) -> None:
    """Render with 0 pointers. The pointer block must carry the
    'no checkable claims' banner, instructing the lens to emit the
    documented advisory + zero-counts coverage."""
    rendered = render_grounding_prompt(
        pointers=[],
        grounding_checks=_SAMPLE_CHECKS,
        output_path=tmp_path / "g.jsonl",
    )
    assert _NO_POINTERS_BANNER in rendered
    assert "no-checkable-claims-found" in rendered
    assert "substrate_refs_found=0" in rendered or "substrate_refs_found: 0" in rendered


def test_render_grounding_prompt_substitution_order(
    tmp_path: Path,
) -> None:
    """Edge case: a pointer's anchored_claim that literally contains
    the text ``{grounding_checks}`` must NOT trigger further
    substitution. The render order (output_path → grounding_checks →
    pointers last) is the guarantee."""
    sneaky = {
        "pointer_type": "file_path",
        "pointer_value": "/a/path",
        "anchored_claim": (
            "this claim mentions {grounding_checks} verbatim"
        ),
        "line_range": {"start": 1, "end": 1},
    }
    rendered = render_grounding_prompt(
        pointers=[sneaky],
        grounding_checks=_SAMPLE_CHECKS,
        output_path=tmp_path / "g.jsonl",
    )
    # The placeholder appears inside the rendered pointer JSON — it
    # must NOT have been replaced by the checks block.
    pointers_section = rendered.split("---POINTERS---", 1)[1]
    assert "{grounding_checks}" in pointers_section


def test_format_pointers_sanitises_to_four_fields() -> None:
    """The render helper is the second layer of the
    convergent-blind-spot defence. Even when handed extra fields, it
    must emit only the four allowed ones in the rendered JSON line."""
    tainted = {
        "pointer_type": "wikilink",
        "pointer_value": "my-slug",
        "anchored_claim": "the audited claim",
        "line_range": {"start": 5, "end": 7},
        # Forbidden fields:
        "source_text_excerpt": "DRAFT_LEAK",
        "internal_offset": 9999,
        "some_other_field": "DRAFT_LEAK_2",
    }
    out = _format_pointers_for_prompt([tainted])
    assert "DRAFT_LEAK" not in out
    assert "DRAFT_LEAK_2" not in out
    assert "source_text_excerpt" not in out
    assert "internal_offset" not in out
    # The four allowed fields are present.
    assert "pointer_type" in out
    assert "pointer_value" in out
    assert "anchored_claim" in out
    assert "line_range" in out


# ---------------------------------------------------------------------------
# Fixture sanity
# ---------------------------------------------------------------------------


def test_v2_fixture_has_may12_parity_pointer() -> None:
    """The V2 fixture MUST contain the May 12 parity state.yaml pointer
    — this is the architectural anchor for the convergent-blind-spot
    guarantee. ccp-012's E2E test will check the same pointer."""
    pointers = _load_v2_pointers()
    state_yaml_pointers = [
        p for p in pointers
        if p["pointer_type"] == "state_yaml_dotted"
    ]
    assert state_yaml_pointers, "expected at least one state.yaml pointer"
    assert any(
        "checkout_redesign_v2" in p["pointer_value"]
        and "purchase_success_early_read" in p["pointer_value"]
        for p in state_yaml_pointers
    )


def test_v2_fixture_has_chart_id_pointer() -> None:
    pointers = _load_v2_pointers()
    chart_pointers = [
        p for p in pointers if p["pointer_type"] == "chart_id"
    ]
    assert chart_pointers
    assert any(p["pointer_value"] == "abc123xy" for p in chart_pointers)
