"""ccp-019 scope auditor — prompt template tests.

The scope lens prompt template MUST carry the STRICT location-shape
instruction: an explicit JSON example block with CORRECT and REJECTED
forms, plus the explicit "will be rejected by the schema validator"
sentence. This test pins the contract — if a future refactor softens
the wording, the LLM is free to emit prose-string locations again and
findings get silently dropped (see the 2026-05-29 bank-details smoke
test that motivated this issue).

The companion assertion for the per-check fragments (scope-* under
``scripts/prompts/checks/``) lives in this same file so all scope-lens
ownership stays in one place.

Run:
    pytest -v ~/cc-skills/cc:proofread/scripts/tests/test_scope_auditor.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

SCOPE_PROMPT = SKILL_ROOT / "scripts" / "prompts" / "scope_auditor.md"
CHECKS_DIR = SKILL_ROOT / "scripts" / "prompts" / "checks"

SCOPE_FRAGMENTS = [
    "scope-alternative-interpretation-missing.md",
    "scope-capability-from-rate-evidence.md",
    "scope-confound-inventory-missing.md",
    "scope-maturity-surface-missing.md",
    "scope-mechanism-as-clock.md",
    "scope-scope-drift.md",
]


# ---------------------------------------------------------------------------
# Lens-level prompt
# ---------------------------------------------------------------------------


def test_scope_prompt_template_exists() -> None:
    assert SCOPE_PROMPT.exists()


def test_scope_prompt_contains_strict_location_header() -> None:
    """The strict-location block opens with a load-bearing header that
    the LLM cannot miss."""
    text = SCOPE_PROMPT.read_text(encoding="utf-8")
    assert "**Location schema (STRICT):**" in text


def test_scope_prompt_contains_correct_location_example() -> None:
    """The CORRECT example shows the exact JSON shape the validator
    accepts. Pinning the literal string defends against the LLM
    paraphrasing 'line_range_start' into 'start' (the most common
    drift)."""
    text = SCOPE_PROMPT.read_text(encoding="utf-8")
    assert "CORRECT location:" in text
    assert (
        '"location": {"line_range_start": 38, "line_range_end": 42}'
        in text
    )


def test_scope_prompt_contains_rejected_location_examples() -> None:
    """The REJECTED block enumerates the four observed failure modes
    from the 2026-05-29 smoke test:
      - prose section-name string
      - prose TL;DR-style string
      - {start, end} shorthand
      - array form
    """
    text = SCOPE_PROMPT.read_text(encoding="utf-8")
    assert "REJECTED location:" in text
    # Prose section-name form (the literal one from the smoke test).
    assert '"## The events — fire-once bullet"' in text
    # Prose TL;DR-style form.
    assert '"TL;DR paragraph"' in text
    # {start, end} shorthand.
    assert '{"start": 38, "end": 42}' in text
    # Array form.
    assert "[38, 42]" in text


def test_scope_prompt_contains_rejection_consequence_sentence() -> None:
    """The 'will be rejected by the validator + lens reported as failed
    in the coverage line' sentence is what makes the instruction
    non-optional. Without the consequence, the LLM may interpret the
    schema as a recommendation."""
    text = SCOPE_PROMPT.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "will be rejected by the schema validator" in flat
    assert "lens will be reported as failed in the run's coverage line" in flat


# ---------------------------------------------------------------------------
# Per-check fragments — scope lens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fragment_name", SCOPE_FRAGMENTS)
def test_scope_fragment_contains_strict_location_header(
    fragment_name: str,
) -> None:
    text = (CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert "**Location schema (STRICT):**" in text, (
        f"{fragment_name} missing the strict-location header"
    )


@pytest.mark.parametrize("fragment_name", SCOPE_FRAGMENTS)
def test_scope_fragment_contains_correct_location_example(
    fragment_name: str,
) -> None:
    text = (CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert "CORRECT location:" in text, (
        f"{fragment_name} missing 'CORRECT location:' label"
    )
    assert (
        '"location": {"line_range_start": 38, "line_range_end": 42}'
        in text
    ), f"{fragment_name} missing the literal CORRECT JSON example"


@pytest.mark.parametrize("fragment_name", SCOPE_FRAGMENTS)
def test_scope_fragment_contains_rejected_location_examples(
    fragment_name: str,
) -> None:
    text = (CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert "REJECTED location:" in text, (
        f"{fragment_name} missing 'REJECTED location:' label"
    )
    # All four failure-mode examples must appear.
    assert '"## The events — fire-once bullet"' in text, (
        f"{fragment_name} missing prose-section REJECTED example"
    )
    assert '{"start": 38, "end": 42}' in text, (
        f"{fragment_name} missing {{start, end}} REJECTED example"
    )
    assert "[38, 42]" in text, (
        f"{fragment_name} missing array-form REJECTED example"
    )


@pytest.mark.parametrize("fragment_name", SCOPE_FRAGMENTS)
def test_scope_fragment_contains_rejection_consequence_sentence(
    fragment_name: str,
) -> None:
    text = (CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "will be rejected by the schema validator" in flat, (
        f"{fragment_name} missing 'will be rejected by the validator' "
        "sentence — the LLM may treat the schema as a recommendation"
    )
    assert "lens will be reported as failed" in flat, (
        f"{fragment_name} missing the coverage-line consequence sentence"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
