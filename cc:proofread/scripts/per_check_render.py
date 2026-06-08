"""ccp-013 per-check prompt rendering — `--deep` mode helper.

In default `cc:proofread` mode, the orchestrator renders ONE prompt per
substantive lens (3 total: scope / grounding / structural). In `--deep`
mode, it instead renders ONE prompt per substantive check (16 total: 6
scope + 4 grounding + 6 structural). This module owns the per-check
rendering; the orchestrator is the consumer (post-Wave-5 work).

Library use:

    from scripts.per_check_render import render_per_check_prompts
    paths = render_per_check_prompts(
        criteria_path=Path("default-criteria.md"),
        artifact_text=artifact_text,
        pointers=pointers,
        recent_diff=recent_diff,
        output_dir=run_dir,
    )
    # paths is a list of <output_dir>/<lens>-<check_id>_prompt.md Paths
    # for the orchestrator to fan out into parallel Agent calls.

Each per-check JSONL output path is derived by the SAME naming rule, so
the orchestrator can match prompts to expected outputs without an
explicit mapping table: `<output_dir>/<lens>-<check_id>.jsonl`.

The fragment templates live at `scripts/prompts/checks/<lens>-<check_id>.md`
and follow a strict shape:

- Read-only contract + isolation reminder
- Adversarial voice block
- Forbidden-word constraint (`Do not use the word "clean"`)
- Per-check description block with 4 `{check_*}` placeholders the
  renderer substitutes from the criteria YAML
- Output schema constrained to ONE valid check_id (the fragment's own)
- Lens-specific input block: `{artifact}` for scope/structural,
  `{pointers}` for grounding (the convergent-blind-spot guard), and
  `{recent_diff}` for structural

Convergent-blind-spot property preserved: the grounding fragments do
NOT carry an `{artifact}` placeholder, mirroring the lens-level
`grounding_auditor.md` contract. Per-check renderers route by lens to
guarantee a grounding fragment never sees draft prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Pulled from scripts.criteria_checks (ccp-018 extraction) — this module
# used to import these names from scripts.orchestrator, which led to a
# circular import because orchestrator also imports render_per_check_prompts
# from this module. The shared criteria_checks module breaks the cycle.
from scripts.criteria_checks import (
    ScopeCheck,
    extract_grounding_checks,
    extract_scope_checks,
    extract_structural_checks,
)
from scripts.substrate_query import _format_pointers_for_prompt


_CHECKS_DIR = Path(__file__).resolve().parent / "prompts" / "checks"


# Lens → list of substantive check ids the deep mode dispatches. This
# is the canonical fan-out set; sibling checks NOT listed here (like the
# `late-added-callouts` structural meta-flag) intentionally do NOT get a
# per-check fragment because they're either mechanical or rendered as a
# lens-level wrapper concern.
DEEP_FANOUT: dict[str, tuple[str, ...]] = {
    "scope": (
        "scope-drift",
        "capability-from-rate-evidence",
        "alternative-interpretation-missing",
        "mechanism-as-clock",
        "maturity-surface-missing",
        "confound-inventory-missing",
    ),
    "grounding": (
        "stale-references",
        "baseline-not-checked-attribution",
        "contradicting-data-not-addressed",
        "citation-traceability-missing",
    ),
    "structural": (
        "zombie-claims",
        "number-source-divergence",
        "narrative-table-contradiction",
        "hedge-vs-data-semantic",
        "causal-claim-labelling",
        "audience-fit",
    ),
}


@dataclass(frozen=True)
class PerCheckPrompt:
    """One rendered prompt and the JSONL path the subagent will write to.

    Returned by ``render_per_check_prompts`` so the orchestrator can
    associate each Agent dispatch with the expected output file without
    having to re-derive the path from the prompt filename.
    """

    lens: str
    check_id: str
    prompt_path: Path
    jsonl_path: Path


def _fragment_path(lens: str, check_id: str) -> Path:
    """Resolve the on-disk fragment template path for one check.

    Raises FileNotFoundError naming the missing path if the fragment is
    not present — surfacing as a build/install error rather than
    silently falling back to the lens-level prompt.
    """
    path = _CHECKS_DIR / f"{lens}-{check_id}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"per-check fragment not found at {path} — ensure ccp-013's "
            "fragment files are installed alongside the lens-level prompts."
        )
    return path


def _substitute_check_fields(template: str, check: ScopeCheck) -> str:
    """Substitute the 4 `{check_*}` placeholders with criteria-loaded
    field values. Uses str.replace rather than .format to tolerate the
    template's literal curly braces in JSON examples.

    The substitution targets are intentionally narrow — only the four
    fields documented in the fragment shape. Adding new placeholders
    later means updating both the fragment template and this helper.
    """
    body = template
    body = body.replace("{check_description}", check.description)
    body = body.replace("{check_trigger_heuristic}", check.trigger_heuristic)
    body = body.replace(
        "{check_remediation_language}", check.remediation_language
    )
    body = body.replace(
        "{check_example_failure_mode}", check.example_failure_mode
    )
    return body


def _render_scope_fragment(
    check: ScopeCheck,
    artifact_text: str,
    output_path: Path,
) -> str:
    """Render one scope fragment with placeholders substituted.

    Substitution order: check fields → output_path → artifact (LAST).
    Artifact last so its body can contain literal placeholder-shaped
    tokens without triggering further substitution.
    """
    template = _fragment_path("scope", check.id).read_text(encoding="utf-8")
    body = _substitute_check_fields(template, check)
    body = body.replace("{output_path}", str(output_path))
    body = body.replace("{artifact}", artifact_text)
    return body


def _render_grounding_fragment(
    check: ScopeCheck,
    pointers: list[dict],
    output_path: Path,
) -> str:
    """Render one grounding fragment with placeholders substituted.

    Critical: the grounding fragments do NOT carry an `{artifact}`
    placeholder. The renderer never receives the artifact body — only
    sanitised pointers. The substitution sequence below would no-op on
    an `{artifact}` token if one accidentally appeared, but the
    fragment-level test enforces the architectural property at the
    template layer.
    """
    template = _fragment_path("grounding", check.id).read_text(
        encoding="utf-8"
    )
    body = _substitute_check_fields(template, check)
    body = body.replace("{output_path}", str(output_path))
    body = body.replace("{pointers}", _format_pointers_for_prompt(pointers))
    return body


def _render_structural_fragment(
    check: ScopeCheck,
    artifact_text: str,
    recent_diff: str,
    output_path: Path,
) -> str:
    """Render one structural fragment with placeholders substituted.

    Substitution order: check fields → output_path → recent_diff →
    artifact (LAST). Same rationale as the lens-level renderer —
    artifact last so its body can contain literal token-shaped strings
    without re-triggering substitution.
    """
    template = _fragment_path("structural", check.id).read_text(
        encoding="utf-8"
    )
    body = _substitute_check_fields(template, check)
    body = body.replace("{output_path}", str(output_path))
    body = body.replace("{recent_diff}", recent_diff)
    body = body.replace("{artifact}", artifact_text)
    return body


def _checks_by_id(checks: list[ScopeCheck]) -> dict[str, ScopeCheck]:
    """Index check list by `id` for O(1) lookup at fan-out time."""
    return {c.id: c for c in checks}


def render_per_check_prompts(
    *,
    criteria_path: Path,
    artifact_text: str,
    pointers: list[dict],
    recent_diff: str,
    output_dir: Path,
) -> list[PerCheckPrompt]:
    """Render the `--deep` fan-out: one prompt per substantive check.

    Arguments:
        criteria_path: resolved criteria.md (per ccp-007) the check
            definitions are pulled from. Project overrides take effect
            here — if an override extends a default check with a
            `project_refinement`, the lens-level extractor already
            merged it before returning.
        artifact_text: the draft body. Passed to scope + structural
            fragments; NEVER passed to grounding fragments (the
            convergent-blind-spot guard).
        pointers: the substrate pointer list from discovery. Passed
            ONLY to grounding fragments. Sanitised down to the four
            allowed fields by the shared `_format_pointers_for_prompt`
            helper before insertion.
        recent_diff: the most-recent-commit diff string. Passed only to
            structural fragments. Empty string for piped / untracked
            artifacts (the structural fragments treat empty diff as
            "skip the late-added-callouts signal").
        output_dir: where to write `<lens>-<check_id>_prompt.md` files.
            Caller (orchestrator) typically passes the run directory
            under `/tmp/cc-proofread-<run-id>/`. Caller must ensure it
            exists; this function does not mkdir.

    Returns:
        A list of `PerCheckPrompt` records — one per substantive check
        in the canonical lens order (scope → grounding → structural).
        Each record carries the rendered prompt path AND the JSONL
        path the subagent will be told to write to.

    The render is deterministic: same criteria + inputs → same
    prompts + paths. No timestamp / no random.
    """
    scope_checks = _checks_by_id(extract_scope_checks(criteria_path))
    grounding_checks = _checks_by_id(extract_grounding_checks(criteria_path))
    structural_checks = _checks_by_id(
        extract_structural_checks(criteria_path)
    )

    rendered: list[PerCheckPrompt] = []

    for check_id in DEEP_FANOUT["scope"]:
        check = scope_checks.get(check_id)
        if check is None:
            # The default-criteria registry always defines all 6; a
            # project override that SUPPRESSES one (via the suppress:
            # list) lawfully removes it. Skip — orchestrator's coverage
            # path handles "lens had fewer checks than the fan-out
            # expected".
            continue
        jsonl_path = output_dir / f"scope-{check_id}.jsonl"
        prompt_path = output_dir / f"scope-{check_id}_prompt.md"
        prompt_text = _render_scope_fragment(
            check=check,
            artifact_text=artifact_text,
            output_path=jsonl_path,
        )
        prompt_path.write_text(prompt_text, encoding="utf-8")
        rendered.append(PerCheckPrompt(
            lens="scope",
            check_id=check_id,
            prompt_path=prompt_path,
            jsonl_path=jsonl_path,
        ))

    for check_id in DEEP_FANOUT["grounding"]:
        check = grounding_checks.get(check_id)
        if check is None:
            continue
        jsonl_path = output_dir / f"grounding-{check_id}.jsonl"
        prompt_path = output_dir / f"grounding-{check_id}_prompt.md"
        prompt_text = _render_grounding_fragment(
            check=check,
            pointers=pointers,
            output_path=jsonl_path,
        )
        prompt_path.write_text(prompt_text, encoding="utf-8")
        rendered.append(PerCheckPrompt(
            lens="grounding",
            check_id=check_id,
            prompt_path=prompt_path,
            jsonl_path=jsonl_path,
        ))

    for check_id in DEEP_FANOUT["structural"]:
        check = structural_checks.get(check_id)
        if check is None:
            continue
        jsonl_path = output_dir / f"structural-{check_id}.jsonl"
        prompt_path = output_dir / f"structural-{check_id}_prompt.md"
        prompt_text = _render_structural_fragment(
            check=check,
            artifact_text=artifact_text,
            recent_diff=recent_diff,
            output_path=jsonl_path,
        )
        prompt_path.write_text(prompt_text, encoding="utf-8")
        rendered.append(PerCheckPrompt(
            lens="structural",
            check_id=check_id,
            prompt_path=prompt_path,
            jsonl_path=jsonl_path,
        ))

    return rendered
