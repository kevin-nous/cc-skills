"""ccp-006 output renderer.

Composes the final markdown the orchestrator prints to the user from
finding / coverage / run records (shapes defined in
schemas/finding.schema.json).

Anti-theatre design is structural, not policy. The renderer:

1. Never emits a green checkmark, "audit passed", "all clear", or
   analogous affirmative tokens. The FORBIDDEN_AFFIRMATIONS regex is
   pinned and tests assert zero matches against every rendered output.
2. Names every lens that ran in the Coverage section — even when zero
   findings. Coverage is transparency, not a victory lap.
3. Renders findings in adversarial voice exactly as the lens authored
   them (see schemas/finding.schema.json :: Finding.finding — "this
   claim contradicts X", not "consider whether...").
4. Emits a quantified pass when the grounding lens has substrate and
   found nothing wrong: "grounding auditor verified N numerical claims
   against substrate; none contradicted". Silence would read as theatre.
5. Ends with the pre-mortem prompt as the literal last line. No "skill
   complete" footer, no trailing whitespace. PRE_MORTEM_PROMPT is a
   pinned constant — change it here and every test snapshot must move.

Library use:
    from scripts.render_output import render
    text = render(findings=[...], coverage=[...], run_record={...})

CLI use:
    python3 -m scripts.render_output <jsonl-file>

The CLI partitions a JSONL stream by record_type and prints the
rendered output to stdout. Exit code 0 iff the input parsed.

Why this lives at scripts/render_output.py:
    The orchestrator (ccp-008/011) aggregates JSONL records emitted by
    the lenses (ccp-009/010) and the mechanical prepass (ccp-004), then
    calls render() exactly once at the end of a run. This module owns
    that final composition; nothing else writes to stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Pinned constants — these define the anti-theatre contract.
# ---------------------------------------------------------------------------

PRE_MORTEM_PROMPT = (
    "Imagine this artifact is proven wrong in 6 weeks. "
    "Which sentence is the culprit?"
)

# Forbidden affirmative tokens. The test guard runs this regex against
# every rendered output and asserts zero matches. Adding new affirmative
# language to the renderer must update this list too (and the snapshot
# tests will catch any token that slips through).
FORBIDDEN_AFFIRMATIONS = re.compile(
    r"(✓|✅|\[PASSED\]|audit passed|all clear|clean)",
    flags=re.IGNORECASE,
)

# Lens display labels. The orchestrator (ccp-011) sets lens="convergent"
# on findings caught by multiple lenses; rendering only.
_LENS_LABELS = {
    "mechanical": "mechanical prepass",
    "scope": "scope auditor",
    "grounding": "grounding auditor",
    "structural": "structural auditor",
    "convergent": "convergent finding",
}

# Order findings groups are rendered in. Convergent first (highest
# signal), then the substantive lenses, then mechanical. Lenses without
# findings are silently omitted from the Findings section but always
# appear in Coverage.
_FINDING_GROUP_ORDER = [
    "convergent",
    "grounding",
    "scope",
    "structural",
    "mechanical",
]

# Order coverage lines are rendered in. Mechanical last (smallest
# information content); substantive lenses lead.
_COVERAGE_ORDER = ["scope", "grounding", "structural", "mechanical"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lens_label(lens: str) -> str:
    return _LENS_LABELS.get(lens, lens)


def _format_location(loc: dict) -> str:
    """Render a Location dict as 'path:start-end (section)' or similar.

    Location is required to carry line_range_start/end; file and section
    are optional (piped text has no file path; some lenses don't
    pinpoint a section).
    """
    start = loc.get("line_range_start")
    end = loc.get("line_range_end")
    file_part = loc.get("file") or ""
    section = loc.get("section")
    if start == end:
        line_part = f"line {start}"
    else:
        line_part = f"lines {start}-{end}"
    prefix = f"{file_part}:{line_part}" if file_part else line_part
    if section:
        return f"{prefix} ({section})"
    return prefix


def _format_severity(severity: str) -> str:
    """Render severity as a short bracketed tag. blocking is loud
    (uppercase); advisory is calmer (lowercase). No green / red emoji."""
    if severity == "blocking":
        return "[BLOCKING]"
    return "[advisory]"


def _render_finding(finding: dict) -> str:
    """Render a single finding block (Markdown ### header + body).

    Convergent findings (lens == "convergent" OR convergent_with non-empty)
    render with a "Caught by N/3 lenses:" prefix to give them extra
    visual weight per ccp-006 AC.
    """
    lens = finding.get("lens", "")
    check_id = finding.get("check_id", "")
    severity = _format_severity(finding.get("severity", "advisory"))
    claim = finding.get("claim", "")
    loc = finding.get("location", {})
    body = finding.get("finding", "")
    remediation = finding.get("remediation", "")
    evidence = finding.get("evidence")
    convergent_with = finding.get("convergent_with") or []

    # Header: convergent gets a prefix.
    is_convergent = lens == "convergent" or len(convergent_with) > 0
    if is_convergent:
        # +1 for the finding's own lens (counted in convergent_with as
        # the cross-references from sibling lenses). Cap at 3 — three
        # substantive lenses is the architectural maximum.
        caught_by = min(3, len(convergent_with) + 1)
        header = f"### Caught by {caught_by}/3 lenses: {check_id} {severity}"
    else:
        header = f"### {_lens_label(lens)} — {check_id} {severity}"

    lines = [header, ""]
    lines.append(f"**Claim:** {claim}")
    lines.append(f"**Location:** {_format_location(loc)}")
    lines.append("")
    lines.append(body)

    if evidence:
        lines.append("")
        lines.append("**Substrate evidence:**")
        lines.append(f"- field: `{evidence.get('substrate_field', '')}`")
        substrate_value = evidence.get("substrate_value", "")
        lines.append(f"- value: {substrate_value}")
        lines.append(f"- contradicts: \"{evidence.get('draft_claim', '')}\"")

    if remediation:
        lines.append("")
        lines.append(f"**Remediation:** {remediation}")

    return "\n".join(lines)


def _coverage_for(coverage: list[dict], lens: str) -> dict | None:
    """Find the coverage record for a given lens. Returns None if the
    lens didn't run at all (in which case it's silently omitted)."""
    for rec in coverage:
        if rec.get("lens") == lens:
            return rec
    return None


def _render_coverage_line(rec: dict) -> str:
    """Render one coverage line. Lens-failed records get explicit
    failure language per AC; otherwise quantified counts.

    For grounding with substrate and zero contradictions, emit the
    quantified-pass phrasing required by the PRD:
        "grounding auditor verified N numerical claims against
        substrate; none contradicted"
    """
    lens = rec.get("lens", "")
    label = _lens_label(lens)
    error = rec.get("error")
    if error:
        return f"- {label} — failed: {error}"

    examined = rec.get("claims_examined", 0)
    flagged = rec.get("claims_flagged", 0)

    if lens == "grounding":
        found = rec.get("substrate_refs_found", 0)
        verified = rec.get("substrate_refs_verified", 0)
        contradicted = rec.get("substrate_refs_contradicted", 0)
        unresolvable = rec.get("substrate_refs_unresolvable", 0)
        # Quantified pass: substrate present, nothing contradicted.
        if found > 0 and contradicted == 0:
            return (
                f"- {label} verified {verified} numerical claims against "
                f"substrate; none contradicted "
                f"({found} substrate refs found, "
                f"{unresolvable} unresolvable)"
            )
        return (
            f"- {label}: {found} substrate refs found, "
            f"{verified} verified, {contradicted} contradicted, "
            f"{unresolvable} unresolvable"
        )

    # scope / structural / mechanical: claim-count shape.
    return (
        f"- {label}: {examined} claims checked, {flagged} flagged"
    )


def _render_lens_failure_block(rec: dict) -> str:
    """When a lens errored out, surface a top-level block in the
    Findings section in addition to the Coverage line — per AC, the
    user should not have to scan to Coverage to learn a lens failed."""
    lens = rec.get("lens", "")
    error = rec.get("error", "unknown error")
    return f"### {_lens_label(lens)} — failed: {error}"


def _format_run_header(run_record: dict | None, artifact_label: str) -> list[str]:
    """Render the top-of-output header (artifact + run metadata)."""
    lines = [f"# cc:proofread — {artifact_label}"]
    if run_record:
        run_id = run_record.get("run_id", "")
        started = run_record.get("started_at", "")
        completed = run_record.get("completed_at", "")
        lines.append(
            f"Run: {run_id} · started {started} · completed {completed}"
        )
    return lines


def _artifact_label(run_record: dict | None) -> str:
    """The header's right-hand side: file path if present, else hash
    snippet, else a placeholder."""
    if not run_record:
        return "(no run record)"
    path = run_record.get("artifact_path")
    if path:
        return path
    h = run_record.get("artifact_hash")
    if h:
        return f"sha256:{h[:12]}"
    return "(no artifact identifier)"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render(
    findings: list[dict],
    coverage: list[dict],
    run_record: dict | None = None,
) -> str:
    """Compose the full markdown output. Returns a single string.

    The output MUST:
    - end with PRE_MORTEM_PROMPT as the literal last line (no trailing
      newline, no whitespace after).
    - contain zero matches against FORBIDDEN_AFFIRMATIONS.
    - render a Coverage line for every lens that has a coverage record
      (failed lenses included, with explicit failure language).

    Inputs are taken in record form (schemas/finding.schema.json
    shape) — callers should NOT pre-format anything. This function owns
    the entire string composition.
    """
    out: list[str] = []

    # --- Header ---------------------------------------------------------
    out.extend(_format_run_header(run_record, _artifact_label(run_record)))
    out.append("")

    # --- Findings -------------------------------------------------------
    total = len(findings)
    out.append(f"## Findings ({total})")
    out.append("")

    if total == 0:
        # Per AC: NOT "audit passed" / "✓ clean". Plain neutral sentence.
        out.append("No findings on this run.")
        out.append("")
    else:
        # Group by lens; render in _FINDING_GROUP_ORDER.
        grouped: dict[str, list[dict]] = {}
        for f in findings:
            grouped.setdefault(f.get("lens", ""), []).append(f)

        first_group = True
        for lens in _FINDING_GROUP_ORDER:
            group = grouped.get(lens)
            if not group:
                continue
            if not first_group:
                out.append("")
            first_group = False
            for f in group:
                out.append(_render_finding(f))
                out.append("")
        # Any lens labels we didn't anticipate (shouldn't happen per
        # schema enum, but defensive): drop them at the end.
        for lens, group in grouped.items():
            if lens in _FINDING_GROUP_ORDER:
                continue
            for f in group:
                out.append(_render_finding(f))
                out.append("")

        # Lens failures surface as a top-level Findings block too, so
        # the user doesn't have to scan to Coverage to learn a lens
        # errored out.
        for lens in _COVERAGE_ORDER:
            rec = _coverage_for(coverage, lens)
            if rec and rec.get("error"):
                out.append(_render_lens_failure_block(rec))
                out.append("")

    # If there are no findings but a lens failed, still surface the
    # failure block — otherwise the failure only appears in Coverage.
    if total == 0:
        failed_blocks_added = False
        for lens in _COVERAGE_ORDER:
            rec = _coverage_for(coverage, lens)
            if rec and rec.get("error"):
                if not failed_blocks_added:
                    # Drop the trailing blank from "No findings..." so
                    # the failure blocks sit flush.
                    pass
                out.append(_render_lens_failure_block(rec))
                out.append("")
                failed_blocks_added = True

    # --- Coverage -------------------------------------------------------
    out.append("## Coverage")
    out.append("")
    if not coverage:
        # Edge case: no lens ran. Still well-formed; still honest.
        out.append("No lenses ran on this artifact.")
        out.append("")
    else:
        for lens in _COVERAGE_ORDER:
            rec = _coverage_for(coverage, lens)
            if rec is None:
                continue
            out.append(_render_coverage_line(rec))
        # Any lens we didn't anticipate: render at the end.
        seen = set(_COVERAGE_ORDER)
        for rec in coverage:
            if rec.get("lens") in seen:
                continue
            out.append(_render_coverage_line(rec))
        out.append("")

    # --- Tagged artifact (ccp-014, between coverage and pre-mortem) -----
    # When the orchestrator wrote a `proofread-passed-<date>` tag to the
    # artifact's frontmatter, surface it ONCE in the rendered output so
    # the user has a record of the side-effect without scrolling back
    # through stdout. Placed BEFORE the pre-mortem so the pre-mortem
    # prompt remains the literal last line of default-mode output
    # (anti-theatre guard, see ccp-006).
    if run_record and run_record.get("tagged_artifact"):
        out.append(f"Tagged artifact: {run_record['tagged_artifact']}")
        out.append("")
    elif run_record and run_record.get("tag_refused"):
        # ccp-021: refusal mode — silent lens failure means the tag is
        # premature even though no blocking findings landed. Pre-mortem
        # still the literal last line; this slot mirrors the
        # "Tagged artifact:" position so the anti-theatre guard holds.
        out.append(f"Tag refused: {run_record['tag_refused']}")
        out.append("")

    # --- Pre-mortem (literal last line of the default-mode output) -----
    out.append("## Pre-mortem")
    out.append("")
    out.append(PRE_MORTEM_PROMPT)

    # --- --deep cost note (ccp-013) -------------------------------------
    # When the run used --deep mode, append a one-line cost-projection
    # note AFTER the pre-mortem. Anywhere else and the user would think
    # the note is itself part of the audit instead of meta-info about
    # how the audit was run. Default-mode runs get no note (no signal
    # there's anything unusual about token cost).
    if run_record and run_record.get("mode") == "deep":
        out.append("")
        out.append("Note: --deep mode used; ~5x token cost vs default.")

    # Join and strip ANY trailing whitespace/newlines so the closing
    # signal — pre-mortem in default mode, cost note in --deep mode —
    # is the literal last line of output.
    text = "\n".join(out).rstrip()
    return text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _partition_records(records: Iterable[dict]) -> tuple[list[dict], list[dict], dict | None]:
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


def _iter_jsonl(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            yield json.loads(stripped)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: python3 -m scripts.render_output <jsonl-file>",
            file=sys.stderr,
        )
        return 2
    path = Path(os.path.expanduser(argv[1]))
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 1
    findings, coverage, run_record = _partition_records(_iter_jsonl(path))
    sys.stdout.write(render(findings, coverage, run_record))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
