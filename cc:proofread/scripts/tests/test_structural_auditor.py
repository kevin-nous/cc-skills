"""Unit tests for the structural auditor prompt template (ccp-010).

The orchestrator integration (3-lens parallel batch) is deferred to
post-Wave-4 per the orchestrator's race-condition guard with ccp-009.
These tests exercise the prompt TEMPLATE directly: placeholder
substitution, load-bearing fragment presence, and the two distinguishing
features vs the scope and grounding lenses:

1. Structural DOES receive the full artifact text (grounding does not).
2. Structural has a 6th 'meta-flag' check (late-added-callouts).

We render the prompt via a local helper that mirrors
`scripts.orchestrator.render_scope_prompt` — the orchestrator will be
updated post-Wave-4 to invoke the same shape via the parallel-batch
dispatch. Re-implementing the substitute here keeps these tests
independent of that integration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.orchestrator import (  # noqa: E402
    extract_scope_checks,  # we reuse the YAML extractor for cross-lens parity
    format_scope_checks_for_prompt,
)

FIXTURES = SKILL_ROOT / "scripts" / "tests" / "fixtures" / "structural"
DEFAULT_CRITERIA = SKILL_ROOT / "default-criteria.md"
STRUCTURAL_TEMPLATE = (
    SKILL_ROOT / "scripts" / "prompts" / "structural_auditor.md"
)


# ---------------------------------------------------------------------------
# Local renderer (mirrors render_scope_prompt's substitution order to keep
# the artifact substitution last — so `{recent_diff}` or `{structural_checks}`
# tokens that happen to live inside the artifact text don't re-trigger
# substitution).
# ---------------------------------------------------------------------------


def _render_structural_prompt(
    *,
    artifact_text: str,
    structural_checks_block: str,
    recent_diff: str,
    output_path: Path,
) -> str:
    template = STRUCTURAL_TEMPLATE.read_text(encoding="utf-8")
    body = template
    body = body.replace("{output_path}", str(output_path))
    body = body.replace("{structural_checks}", structural_checks_block)
    body = body.replace("{recent_diff}", recent_diff)
    # Artifact LAST — so artifact contents that contain other placeholder
    # strings don't trigger further substitution.
    body = body.replace("{artifact}", artifact_text)
    return body


# Helper: load default structural checks via the same YAML extractor used
# for scope (default-criteria.md ships both sections; we just point the
# extractor at the structural section by temporarily aliasing the section
# name). For now, lift the structural section's YAML manually so we don't
# depend on a structural-specific extractor that ccp-009 / a post-Wave-4
# refactor may rename.
def _read_default_structural_checks_block() -> str:
    """Return a rendered block string of the 5 structural checks + the
    meta-flag as the prompt would receive it. Re-uses
    `format_scope_checks_for_prompt` since the ScopeCheck dataclass has
    the same field set the structural section uses (id, description,
    trigger_heuristic, remediation_language, example_failure_mode)."""
    text = DEFAULT_CRITERIA.read_text(encoding="utf-8")
    # Slice the structural-auditor section out by section header.
    start = text.find("## Structural auditor")
    assert start >= 0, "default-criteria.md missing ## Structural auditor"
    # End at the next H2 (Mechanical prepass).
    end = text.find("\n## ", start + 1)
    section = text[start:end] if end > 0 else text[start:]

    # Use the same YAML fence extractor as the orchestrator, but inline.
    import re
    import yaml
    fence_re = re.compile(r"```yaml\s*\n(?P<body>.*?)```", re.DOTALL)
    checks_data = []
    for fence in fence_re.finditer(section):
        parsed = yaml.safe_load(fence.group("body"))
        if isinstance(parsed, dict) and "id" in parsed:
            checks_data.append(parsed)
    # Build a fake ScopeCheck-shaped list using duck typing.
    from scripts.orchestrator import ScopeCheck
    checks = [
        ScopeCheck(
            id=str(c["id"]),
            description=str(c["description"]),
            trigger_heuristic=str(c["trigger_heuristic"]),
            remediation_language=str(c["remediation_language"]),
            example_failure_mode=str(c["example_failure_mode"]),
        )
        for c in checks_data
    ]
    return format_scope_checks_for_prompt(checks), [c.id for c in checks]


# ---------------------------------------------------------------------------
# Template renders with all four placeholders substituted
# ---------------------------------------------------------------------------


def test_prompt_template_renders_with_placeholders(tmp_path: Path) -> None:
    """Substituting all 4 placeholders yields a prompt that contains
    every substituted value verbatim, no leftover braces."""
    block, _ids = _read_default_structural_checks_block()
    artifact_text = FIXTURES.joinpath("clean.md").read_text(encoding="utf-8")
    recent_diff = (
        "diff --git a/clean.md b/clean.md\n"
        "+a synthetic added line\n"
    )
    output_path = tmp_path / "structural.jsonl"

    prompt = _render_structural_prompt(
        artifact_text=artifact_text,
        structural_checks_block=block,
        recent_diff=recent_diff,
        output_path=output_path,
    )

    # Every placeholder substituted.
    assert str(output_path) in prompt
    assert "zombie-claims" in prompt  # came from the rendered checks block
    assert "a synthetic added line" in prompt  # came from the recent_diff
    assert "V2 retention" in prompt  # came from the artifact

    # No leftover unrendered placeholders for the four LOAD-BEARING tokens.
    # (We do NOT assert the absence of every `{...}` — JSON examples in
    # the template legitimately contain literal braces.)
    for placeholder in (
        "{output_path}", "{structural_checks}",
        "{recent_diff}", "{artifact}",
    ):
        assert placeholder not in prompt, (
            f"placeholder {placeholder!r} not substituted"
        )


def test_prompt_contains_artifact_placeholder(tmp_path: Path) -> None:
    """Architectural distinction from grounding: the structural prompt
    DOES receive the full artifact text. This is the load-bearing
    difference between the two lenses — verify the placeholder is
    present in the template AND that substituting it actually inlines
    the artifact body."""
    template_text = STRUCTURAL_TEMPLATE.read_text(encoding="utf-8")
    # The template MUST contain the `{artifact}` token literally.
    assert "{artifact}" in template_text, (
        "structural prompt template missing {artifact} placeholder — "
        "structural is supposed to read the whole draft, UNLIKE grounding"
    )
    # And the artifact marker that anchors it.
    assert "---ARTIFACT---" in template_text

    # And substituted, the artifact body shows up.
    block, _ids = _read_default_structural_checks_block()
    artifact_text = "# Distinctive marker phrase 0xDEADBEEF\n\nbody\n"
    prompt = _render_structural_prompt(
        artifact_text=artifact_text,
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    assert "0xDEADBEEF" in prompt


# ---------------------------------------------------------------------------
# Forbidden-word constraint
# ---------------------------------------------------------------------------


def test_prompt_contains_forbidden_word_constraint(tmp_path: Path) -> None:
    """The render_output anti-theatre guard fires on the word 'clean'.
    Structural auditor must be told explicitly to avoid it. Same
    constraint as scope and grounding."""
    block, _ = _read_default_structural_checks_block()
    prompt = _render_structural_prompt(
        artifact_text="# small artifact\n",
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    assert "Do not use the word \"clean\" in your findings" in prompt


# ---------------------------------------------------------------------------
# All 5 structural checks + the meta-flag named in the prompt
# ---------------------------------------------------------------------------


def test_prompt_contains_5_structural_checks_and_meta_flag(
    tmp_path: Path,
) -> None:
    """All 6 check IDs must appear in the rendered prompt — 5 from
    criteria.md's structural section, plus the meta-flag the prompt
    template names directly."""
    block, ids = _read_default_structural_checks_block()
    # default-criteria.md ships 5 structural checks (audit-fit included
    # as the 5th) plus 1 meta-flag = 6 total in the criteria registry.
    # The MUST-include set from ccp-010:
    required_ids = {
        "zombie-claims",
        "number-source-divergence",
        "narrative-table-contradiction",
        "hedge-vs-data-semantic",
        "causal-claim-labelling",
        "audience-fit",
        "late-added-callouts",  # the meta-flag
    }
    assert required_ids.issubset(set(ids)), (
        f"missing check ids in criteria.md structural section: "
        f"{required_ids - set(ids)}"
    )

    prompt = _render_structural_prompt(
        artifact_text="# artifact\n",
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    # All 6 IDs render into the prompt verbatim (via the checks block
    # for the 5 + via the meta-flag section for the 7th sentinel
    # variant "late-added-callouts-unreviewed").
    for cid in required_ids:
        assert cid in prompt, (
            f"check id {cid!r} not present in rendered structural prompt"
        )
    # And the meta-flag's sentinel check_id used by the lens:
    assert "late-added-callouts-unreviewed" in prompt


# ---------------------------------------------------------------------------
# When recent_diff is empty, the prompt must explain the meta-flag is skipped
# ---------------------------------------------------------------------------


def test_prompt_uses_empty_string_when_no_diff(tmp_path: Path) -> None:
    """If the orchestrator passes an empty `recent_diff` (artifact not
    in git, or no recent commit), the prompt must instruct the lens to
    SKIP the meta-flag silently rather than treating empty-diff as
    'callout not added' and emitting a false negative on the wrong
    grounds."""
    block, _ = _read_default_structural_checks_block()
    prompt = _render_structural_prompt(
        artifact_text="# artifact\n",
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    # Look for the SKIP instruction — collapse whitespace to make the
    # match robust to line-wrap variations.
    flat = " ".join(prompt.split())
    assert (
        "If the recent-diff block is empty" in flat
        and "SKIP this check silently" in flat
    ), (
        "prompt must instruct the lens to silently skip the meta-flag "
        "when the recent_diff block is empty"
    )

    # And the diff markers must still bracket the empty value so the
    # lens can see it's intentionally empty, not missing.
    assert "---RECENT-DIFF---" in prompt
    assert "---END-RECENT-DIFF---" in prompt


# ---------------------------------------------------------------------------
# Load-bearing fragments — same shape as scope auditor's contract
# ---------------------------------------------------------------------------


def test_prompt_contains_read_only_contract(tmp_path: Path) -> None:
    """Same verbatim wording as the scope auditor's read-only contract —
    'You emit findings to the named JSONL path. You do NOT modify the
    artifact. You do NOT read or write any other file.'"""
    block, _ = _read_default_structural_checks_block()
    prompt = _render_structural_prompt(
        artifact_text="# artifact\n",
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    flat = " ".join(prompt.split())
    assert (
        "You emit findings to the named JSONL path. You do NOT modify "
        "the artifact. You do NOT read or write any other file."
    ) in flat


def test_prompt_contains_convergent_blind_spot_reminder(
    tmp_path: Path,
) -> None:
    """Per ccp-010: 'You receive the artifact + structural-lens checks
    + optional recent diff. You do NOT receive substrate values from
    grounding or scope.'"""
    block, _ = _read_default_structural_checks_block()
    prompt = _render_structural_prompt(
        artifact_text="# artifact\n",
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    flat = " ".join(prompt.split())
    assert (
        "You receive the artifact + structural-lens checks + optional "
        "recent diff."
    ) in flat
    assert "You do NOT receive substrate values from grounding or scope" in flat


def test_prompt_contains_adversarial_voice_instruction(
    tmp_path: Path,
) -> None:
    """The lens is adversarial — must reject the 'consider whether...'
    style and pin the section-vs-section template."""
    block, _ = _read_default_structural_checks_block()
    prompt = _render_structural_prompt(
        artifact_text="# artifact\n",
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    flat = " ".join(prompt.split())
    assert "section A says X but section B says Y" in flat
    assert "Do NOT write \"consider whether...\"" in flat


def test_prompt_contains_late_added_callouts_doc_pointer(
    tmp_path: Path,
) -> None:
    """The meta-flag finding must point readers at the relevant vault
    pattern doc (`vault/knowledge/2026-05-12-codex-review-late-added-
    callouts.md`)."""
    block, _ = _read_default_structural_checks_block()
    prompt = _render_structural_prompt(
        artifact_text="# artifact\n",
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    assert (
        "vault/knowledge/2026-05-12-codex-review-late-added-callouts.md"
        in prompt
    )


# ---------------------------------------------------------------------------
# Audience-fit detection guidance
# ---------------------------------------------------------------------------


def test_prompt_explains_audience_resolution_order(tmp_path: Path) -> None:
    """Per ccp-010: three strategies in order — frontmatter
    `audience:`, file-path hints, default to vault finding."""
    block, _ = _read_default_structural_checks_block()
    prompt = _render_structural_prompt(
        artifact_text="# artifact\n",
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    flat = " ".join(prompt.split())
    # Strategy 1
    assert "Frontmatter `audience:` field" in flat
    # Strategy 2 — path hint examples
    assert "vault/knowledge/" in flat
    assert "slack" in flat.lower()
    # Strategy 3 — default
    assert "Default: assume vault finding" in flat


# ---------------------------------------------------------------------------
# Order independence — artifact substituted last so it can't leak placeholders
# ---------------------------------------------------------------------------


def test_artifact_containing_placeholder_text_does_not_re_substitute(
    tmp_path: Path,
) -> None:
    """Edge case lifted from the scope auditor's order-independence test:
    if the artifact contains literal `{structural_checks}` text, that
    text must survive verbatim — substitution order is checks → diff →
    artifact, so the artifact's literal braces are untouched."""
    block, _ = _read_default_structural_checks_block()
    sneaky_artifact = (
        "# Sneaky artifact\n\n"
        "This artifact contains the literal string {structural_checks} "
        "and {recent_diff} in its body for some legitimate reason.\n"
    )
    prompt = _render_structural_prompt(
        artifact_text=sneaky_artifact,
        structural_checks_block=block,
        recent_diff="diff body\n",
        output_path=tmp_path / "structural.jsonl",
    )
    # Both literal placeholder strings survive intact inside the artifact
    # section (they came from the artifact, not the template, so the
    # template-substitution pass left them alone).
    assert "the literal string {structural_checks}" in prompt
    assert "and {recent_diff} in its body" in prompt


# ---------------------------------------------------------------------------
# Sanity — JSON output schema field set
# ---------------------------------------------------------------------------


def test_prompt_names_output_schema_fields(tmp_path: Path) -> None:
    """The lens must know the JSONL output shape: record_type, id, lens,
    check_id, severity, claim, location, finding, remediation. Plus the
    coverage record fields including `cross_refs_checked`."""
    block, _ = _read_default_structural_checks_block()
    prompt = _render_structural_prompt(
        artifact_text="# artifact\n",
        structural_checks_block=block,
        recent_diff="",
        output_path=tmp_path / "structural.jsonl",
    )
    for field in (
        "record_type", "lens", "check_id", "severity",
        "claim", "location", "finding", "remediation",
    ):
        assert field in prompt, f"finding field {field!r} not named in prompt"
    # `cross_refs_checked` is the structural-specific coverage field.
    assert "cross_refs_checked" in prompt
    # Lens label is "structural", not "scope" or "grounding".
    assert '"structural"' in prompt
    assert '"scope"' not in prompt
    assert '"grounding"' not in prompt or prompt.count('"grounding"') == 0


# ---------------------------------------------------------------------------
# ccp-019 — STRICT location-shape instruction (lens + per-check fragments)
# ---------------------------------------------------------------------------
#
# Structural was the lens that surfaced the bug on the 2026-05-29
# bank-details smoke test: 10 findings emitted with prose-string
# locations, all silently rejected. The fix is to inject an explicit
# CORRECT/REJECTED JSON example block into the prompt template, plus a
# two-location guidance section for the three checks that legitimately
# want to name two sites (zombie-claims / number-source-divergence /
# narrative-table-contradiction). The assertions below pin both.


_STRUCTURAL_FRAGMENTS_ALL = [
    "structural-audience-fit.md",
    "structural-causal-claim-labelling.md",
    "structural-hedge-vs-data-semantic.md",
    "structural-narrative-table-contradiction.md",
    "structural-number-source-divergence.md",
    "structural-zombie-claims.md",
]
_STRUCTURAL_FRAGMENTS_TWO_LOCATION = [
    "structural-zombie-claims.md",
    "structural-number-source-divergence.md",
    "structural-narrative-table-contradiction.md",
]
_CHECKS_DIR = SKILL_ROOT / "scripts" / "prompts" / "checks"


def test_structural_prompt_contains_strict_location_header() -> None:
    text = STRUCTURAL_TEMPLATE.read_text(encoding="utf-8")
    assert "**Location schema (STRICT):**" in text


def test_structural_prompt_contains_correct_location_example() -> None:
    """Literal CORRECT JSON example. Pinning the literal string defends
    against the LLM paraphrasing 'line_range_start' into 'start' (the
    most common drift, and the one that triggered the smoke-test
    rejections)."""
    text = STRUCTURAL_TEMPLATE.read_text(encoding="utf-8")
    assert "CORRECT location:" in text
    assert (
        '"location": {"line_range_start": 38, "line_range_end": 42}'
        in text
    )


def test_structural_prompt_contains_rejected_location_examples() -> None:
    """All four observed failure modes must appear in the REJECTED
    block: prose section-name string (the literal failure from the
    smoke test), prose TL;DR-style string, {start, end} shorthand,
    array form."""
    text = STRUCTURAL_TEMPLATE.read_text(encoding="utf-8")
    assert "REJECTED location:" in text
    assert '"## The events — fire-once bullet"' in text
    assert '"TL;DR paragraph"' in text
    assert '{"start": 38, "end": 42}' in text
    assert "[38, 42]" in text


def test_structural_prompt_contains_rejection_consequence_sentence() -> None:
    text = STRUCTURAL_TEMPLATE.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "will be rejected by the schema validator" in flat
    assert "lens will be reported as failed in the run's coverage line" in flat


def test_structural_prompt_contains_two_location_guidance() -> None:
    """The three two-location checks (zombie-claims /
    number-source-divergence / narrative-table-contradiction)
    legitimately want to name two sites. Schema allows one. The lens
    must be told: location is the PRIMARY site, secondary goes into
    `finding` PROSE."""
    text = STRUCTURAL_TEMPLATE.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "Two-location guidance" in text
    # The three check IDs that this guidance applies to.
    for cid in (
        "zombie-claims",
        "number-source-divergence",
        "narrative-table-contradiction",
    ):
        assert cid in text, (
            f"two-location guidance missing check id {cid!r}"
        )
    # The mechanical instruction: single object, secondary in prose.
    assert "location` is a SINGLE object" in flat or (
        "`location` is a SINGLE object" in flat
    )
    assert "Name the SECONDARY site reference in the `finding` text PROSE" in flat


@pytest.mark.parametrize("fragment_name", _STRUCTURAL_FRAGMENTS_ALL)
def test_structural_fragment_contains_strict_location_header(
    fragment_name: str,
) -> None:
    text = (_CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert "**Location schema (STRICT):**" in text, (
        f"{fragment_name} missing the strict-location header"
    )


@pytest.mark.parametrize("fragment_name", _STRUCTURAL_FRAGMENTS_ALL)
def test_structural_fragment_contains_correct_location_example(
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


@pytest.mark.parametrize("fragment_name", _STRUCTURAL_FRAGMENTS_ALL)
def test_structural_fragment_contains_rejected_location_examples(
    fragment_name: str,
) -> None:
    text = (_CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert "REJECTED location:" in text, (
        f"{fragment_name} missing 'REJECTED location:' label"
    )
    # The prose-section-name form that triggered the smoke-test bug.
    assert '"## The events — fire-once bullet"' in text, (
        f"{fragment_name} missing prose-section REJECTED example"
    )
    assert '{"start": 38, "end": 42}' in text, (
        f"{fragment_name} missing {{start, end}} REJECTED example"
    )
    assert "[38, 42]" in text, (
        f"{fragment_name} missing array-form REJECTED example"
    )


@pytest.mark.parametrize("fragment_name", _STRUCTURAL_FRAGMENTS_ALL)
def test_structural_fragment_contains_rejection_consequence_sentence(
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


@pytest.mark.parametrize(
    "fragment_name", _STRUCTURAL_FRAGMENTS_TWO_LOCATION
)
def test_two_location_fragment_carries_inline_guidance(
    fragment_name: str,
) -> None:
    """The three structural fragments that name TWO sites must each
    carry an inline two-location guidance section in their own
    fragment file (per-check subagents run in isolation; the
    lens-level prompt's guidance is not in their context). The
    guidance must explicitly tell the subagent that `location` is a
    single object and the secondary site goes into `finding` PROSE."""
    text = (_CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "Two-location guidance" in text, (
        f"{fragment_name} missing the inline two-location guidance "
        "section — per-check subagent runs in isolation from the "
        "lens-level prompt"
    )
    assert "`location` is a SINGLE object" in flat, (
        f"{fragment_name} missing the 'SINGLE object' instruction"
    )
    assert "in the `finding` text PROSE" in flat, (
        f"{fragment_name} missing the secondary-in-finding-prose instruction"
    )


@pytest.mark.parametrize(
    "fragment_name",
    [
        "structural-audience-fit.md",
        "structural-causal-claim-labelling.md",
        "structural-hedge-vs-data-semantic.md",
    ],
)
def test_single_location_structural_fragment_omits_two_location_guidance(
    fragment_name: str,
) -> None:
    """Symmetric check: the three single-location structural fragments
    must NOT carry the two-location guidance section — adding it where
    it doesn't belong invites the subagent to fabricate a secondary
    site reference in `finding` PROSE when there isn't one."""
    text = (_CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert "Two-location guidance" not in text, (
        f"{fragment_name} should NOT carry two-location guidance — "
        "this check has only one site"
    )


# ---------------------------------------------------------------------------
# Fixture sanity — each fixture exists and has the expected trigger text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name, trigger_substring",
    [
        ("zombie_claim.md", "V2 won"),
        ("zombie_claim.md", "V2 lost"),
        ("number_divergence.md", "+6.4pp"),
        ("number_divergence.md", "+8.4pp"),
        ("narrative_table.md", "essentially flat"),
        ("narrative_table.md", "+100% relative"),
        ("hedge_vs_data.md", "comfortably"),
        ("hedge_vs_data.md", "p=0.066"),
        ("causal_unlabelled.md", "caused"),
        ("audience_mismatch_slack.md", "audience: slack"),
        ("audience_mismatch_slack.md", "|"),  # has a markdown table
        ("clean.md", "V2"),
    ],
)
def test_fixture_contains_expected_trigger(
    fixture_name: str, trigger_substring: str,
) -> None:
    text = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    assert trigger_substring in text, (
        f"fixture {fixture_name!r} missing expected trigger "
        f"substring {trigger_substring!r}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
