"""Shared YAML-check extractors for criteria.md files.

Owns the parsing of the three lens sections (`## Scope auditor`,
`## Grounding auditor`, `## Structural auditor`) into structured
``ScopeCheck`` records. Lives outside ``scripts.orchestrator`` so both
the orchestrator and ``scripts.per_check_render`` can import these
helpers without forming a circular dependency — extracted in ccp-018.

The criteria schema is uniform across the three lenses: each ``## <Lens>
auditor`` section contains one or more fenced ``yaml`` blocks, each
declaring an ``id`` / ``description`` / ``trigger_heuristic`` /
``remediation_language`` / ``example_failure_mode`` block. The
``GroundingCheck`` / ``StructuralCheck`` aliases keep call sites
self-documenting without paying for parallel dataclasses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ScopeCheck:
    """One YAML check block under the '## Scope auditor' section of a
    criteria.md file. Five fields per the criteria schema."""
    id: str
    description: str
    trigger_heuristic: str
    remediation_language: str
    example_failure_mode: str


# Grounding + structural checks have the SAME field shape as ScopeCheck —
# the criteria schema is uniform across the 3 lenses. Type aliases keep
# call sites self-documenting without paying for parallel dataclasses.
GroundingCheck = ScopeCheck
StructuralCheck = ScopeCheck


# Match a section's content from the header line to the next ## header.
_SECTION_RE = re.compile(
    r"^##\s+(?P<name>[^\n]+?)\s*\n(?P<body>.*?)(?=^##\s|\Z)",
    flags=re.MULTILINE | re.DOTALL,
)

# Match a fenced ```yaml ... ``` block.
_YAML_FENCE_RE = re.compile(
    r"```yaml\s*\n(?P<body>.*?)```",
    flags=re.DOTALL,
)


def _extract_lens_checks(
    criteria_path: Path, section_name: str,
) -> list[ScopeCheck]:
    """Shared YAML-check extractor for any `## <Lens> auditor` section."""
    text = criteria_path.read_text(encoding="utf-8")
    section_body: str | None = None
    for m in _SECTION_RE.finditer(text):
        name = m.group("name").strip()
        if name.lower() == section_name.lower():
            section_body = m.group("body")
            break
    if section_body is None:
        raise ValueError(
            f"{criteria_path}: no '## {section_name}' section found"
        )

    checks: list[ScopeCheck] = []
    for fence in _YAML_FENCE_RE.finditer(section_body):
        body = fence.group("body")
        try:
            parsed = yaml.safe_load(body)
        except yaml.YAMLError as exc:
            raise ValueError(
                f"{criteria_path}: malformed YAML in {section_name} "
                f"check: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            continue
        required = {
            "id", "description", "trigger_heuristic",
            "remediation_language", "example_failure_mode",
        }
        missing = required - set(parsed.keys())
        if missing:
            raise ValueError(
                f"{criteria_path}: {section_name} check missing fields "
                f"{sorted(missing)} in block:\n{body.strip()[:200]}"
            )
        checks.append(ScopeCheck(
            id=str(parsed["id"]),
            description=str(parsed["description"]),
            trigger_heuristic=str(parsed["trigger_heuristic"]),
            remediation_language=str(parsed["remediation_language"]),
            example_failure_mode=str(parsed["example_failure_mode"]),
        ))
    return checks


def extract_scope_checks(criteria_path: Path) -> list[ScopeCheck]:
    """Parse the criteria.md file and return all YAML checks under the
    '## Scope auditor' section.

    Returns an empty list if the section exists but contains no fenced
    YAML blocks. Raises FileNotFoundError / ValueError if the file is
    missing / malformed.
    """
    return _extract_lens_checks(criteria_path, "Scope auditor")


def extract_grounding_checks(criteria_path: Path) -> list[GroundingCheck]:
    """Parse the criteria.md file and return all YAML checks under the
    '## Grounding auditor' section. Same shape as extract_scope_checks."""
    return _extract_lens_checks(criteria_path, "Grounding auditor")


def extract_structural_checks(criteria_path: Path) -> list[StructuralCheck]:
    """Parse the criteria.md file and return all YAML checks under the
    '## Structural auditor' section. Same shape as extract_scope_checks."""
    return _extract_lens_checks(criteria_path, "Structural auditor")
