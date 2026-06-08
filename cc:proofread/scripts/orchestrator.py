"""ccp-008 orchestrator — the driver script for /cc:proofread.

Split into two phases so the Python parts stay testable while the
Claude-runtime parts (Anthropic Agent invocation, wait-for-subagent) are
described in SKILL.md as procedural instructions:

    --phase pre_lens <artifact>
        1. resolve criteria.md (ccp-007)
        2. allocate run_id + /tmp/cc-proofread-<run-id>/
        3. run mechanical prepass → mechanical.jsonl
        4. run discovery → discovery.jsonl + pointers.jsonl
        5. render scope_auditor.md, grounding_auditor.md, and
           structural_auditor.md prompts to <lens>_prompt.md
        6. print a multi-line "PROMPTS_READY <run-id>" signal listing
           the three prompt paths and exit

    [Claude layer in between: dispatch 3 lens subagents IN PARALLEL via
     3 Agent tool calls in a single message; each writes its own
     <lens>.jsonl; wait for all 3 completion notifications]

    --phase post_lens <run-id>
        7. aggregate /tmp/cc-proofread-<run-id>/*.jsonl
        8. render_output.render() → stdout

Flags:
    --dry-run     skip the LLM call entirely; write stub scope.jsonl /
                  grounding.jsonl / structural.jsonl entries marking
                  "dry-run, no LLM invoked" so the post_lens phase can
                  run for testing
    --keep-temp   do not delete /tmp/cc-proofread-<run-id>/ after
                  post_lens; useful for hand-inspection during debug
    --criteria-only
                  print the resolved criteria path + the scope-lens
                  checks extracted from it; exit. Used to verify a
                  project override took effect.

Read-only contract: the orchestrator NEVER writes outside
/tmp/cc-proofread-<run-id>/ and stdout/stderr. Every other write is
delegated to a sub-step that obeys its own read-only contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts import git_helpers
from scripts.aggregator import (
    aggregate_findings,
    aggregate_with_dedup,
    build_run_record,
)
from scripts.criteria_checks import (
    GroundingCheck,
    ScopeCheck,
    StructuralCheck,
    _extract_lens_checks,
    extract_grounding_checks,
    extract_scope_checks,
    extract_structural_checks,
)
from scripts.criteria_resolver import resolve_criteria
from scripts.frontmatter_writer import write_proofread_tag
from scripts.per_check_render import DEEP_FANOUT, render_per_check_prompts
from scripts.render_output import render
from scripts.substrate_query import render_grounding_prompt

# Re-exported for back-compat: callers (including tests) historically
# import the YAML-extractor symbols + ScopeCheck/GroundingCheck/
# StructuralCheck from this module. The definitions now live in
# scripts.criteria_checks (ccp-018) but the names stay reachable here.
__all__ = [
    "GroundingCheck",
    "ScopeCheck",
    "StructuralCheck",
    "_extract_lens_checks",
    "extract_grounding_checks",
    "extract_scope_checks",
    "extract_structural_checks",
]


# ---------------------------------------------------------------------------
# Layout: paths inside /tmp/cc-proofread-<run-id>/
# ---------------------------------------------------------------------------

TMP_PREFIX = "cc-proofread-"

MECHANICAL_FILE = "mechanical.jsonl"
DISCOVERY_FILE = "discovery.jsonl"
POINTERS_FILE = "pointers.jsonl"
SCOPE_FILE = "scope.jsonl"
GROUNDING_FILE = "grounding.jsonl"
STRUCTURAL_FILE = "structural.jsonl"
SCOPE_PROMPT_FILE = "scope_prompt.md"
GROUNDING_PROMPT_FILE = "grounding_prompt.md"
STRUCTURAL_PROMPT_FILE = "structural_prompt.md"

# --deep mode: the late-added-callouts meta-flag is a structural concern
# that depends on git diff (lens-level), not a per-check semantic. So it
# gets its OWN dispatch in --deep mode — separate from the 16 per-check
# fragments — using a filename that won't collide with per-check outputs.
DEEP_LATE_CALLOUTS_JSONL = "structural-late-added-callouts.jsonl"
DEEP_LATE_CALLOUTS_PROMPT = "structural-late-added-callouts_prompt.md"


def run_dir(run_id: str) -> Path:
    return Path("/tmp") / f"{TMP_PREFIX}{run_id}"


def _find_run_dir_by_partial(run_id: str) -> Path:
    """Locate /tmp/cc-proofread-<run-id>/, accepting either a full UUID
    or a UUID prefix. Raises FileNotFoundError with a precise hint if
    nothing matches."""
    direct = run_dir(run_id)
    if direct.exists():
        return direct
    tmp = Path("/tmp")
    matches = sorted(tmp.glob(f"{TMP_PREFIX}{run_id}*"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(
            f"no run directory matching /tmp/{TMP_PREFIX}{run_id}* "
            "found; was --phase pre_lens run first?"
        )
    raise FileNotFoundError(
        f"ambiguous run id prefix {run_id!r} — multiple matches: "
        + ", ".join(p.name for p in matches)
    )


# ---------------------------------------------------------------------------
# Prompt formatting (lens-check extraction lives in scripts.criteria_checks)
# ---------------------------------------------------------------------------


def format_scope_checks_for_prompt(checks: list[ScopeCheck]) -> str:
    """Render the extracted checks as a numbered block for the prompt."""
    if not checks:
        return "(no scope checks defined in criteria.md)"
    lines: list[str] = []
    for i, c in enumerate(checks, start=1):
        lines.append(f"### {i}. `{c.id}`")
        lines.append("")
        lines.append(f"**Description:** {c.description}")
        lines.append("")
        lines.append(f"**Trigger heuristic:** {c.trigger_heuristic}")
        lines.append("")
        lines.append(
            f"**Remediation language (voice template):** "
            f"{c.remediation_language}"
        )
        lines.append("")
        lines.append(f"**Example failure mode:** {c.example_failure_mode}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Prompt template loading + rendering
# ---------------------------------------------------------------------------


def _scope_prompt_template() -> str:
    template_path = (
        Path(__file__).resolve().parent / "prompts" / "scope_auditor.md"
    )
    return template_path.read_text(encoding="utf-8")


def _structural_prompt_template() -> str:
    template_path = (
        Path(__file__).resolve().parent / "prompts" / "structural_auditor.md"
    )
    return template_path.read_text(encoding="utf-8")


def render_scope_prompt(
    artifact_text: str,
    scope_checks: list[ScopeCheck],
    output_path: Path,
) -> str:
    """Compose the scope auditor prompt by substituting the template
    placeholders. Uses str.replace rather than .format because the
    template contains many literal curly braces in JSON examples."""
    template = _scope_prompt_template()
    body = template
    body = body.replace("{output_path}", str(output_path))
    body = body.replace(
        "{scope_checks}", format_scope_checks_for_prompt(scope_checks)
    )
    # Substitute the artifact LAST so its contents (which may contain
    # other placeholder strings) don't trigger further substitution.
    body = body.replace("{artifact}", artifact_text)
    return body


def render_structural_prompt(
    artifact_text: str,
    structural_checks: list[StructuralCheck],
    recent_diff: str,
    output_path: Path,
) -> str:
    """Compose the structural auditor prompt by substituting the four
    template placeholders. Uses str.replace rather than .format because
    the template contains many literal curly braces in JSON examples.

    Substitution order: output_path → structural_checks → recent_diff →
    artifact (LAST). The artifact last so its body can contain literal
    `{structural_checks}` or `{recent_diff}` text without re-triggering
    substitution; recent_diff before artifact so the diff text itself
    can also contain `{artifact}`-shaped tokens without leaking.
    """
    template = _structural_prompt_template()
    body = template
    body = body.replace("{output_path}", str(output_path))
    body = body.replace(
        "{structural_checks}",
        format_scope_checks_for_prompt(structural_checks),
    )
    body = body.replace("{recent_diff}", recent_diff)
    body = body.replace("{artifact}", artifact_text)
    return body


# ---------------------------------------------------------------------------
# Subprocess wrappers
# ---------------------------------------------------------------------------


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _run_mechanical(artifact: Path, project_root: Path, out_path: Path) -> None:
    """Run the mechanical prepass subprocess; write stdout to out_path."""
    cmd = [
        sys.executable, "-m", "scripts.mechanical_prepass",
        str(artifact),
        "--project-root", str(project_root),
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(SKILL_ROOT),
        check=False,
    )
    out_path.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        # Append a coverage record marking failure, so post_lens still
        # has something to render.
        err_record = {
            "record_type": "coverage",
            "lens": "mechanical",
            "checks_attempted": 0,
            "checks_completed": 0,
            "claims_examined": 0,
            "claims_flagged": 0,
            "error": (
                f"mechanical prepass exited {result.returncode}: "
                f"{result.stderr.strip()[:200]}"
            ),
        }
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(err_record) + "\n")


def _run_discovery(
    artifact: Path,
    findings_out: Path,
    pointers_out: Path,
) -> None:
    """Run discovery subprocess; partition its mixed JSONL output.

    Discovery emits two record types:
      - ``record_type: "finding"`` (citation-traceability-missing
        advisories) → aggregator picks these up via the findings file.
      - ``pointer_type: ...`` records (substrate pointers extracted from
        the artifact) → grounding lens consumes these via the pointers
        file.

    Lines that match neither shape are logged to stderr and skipped.
    """
    cmd = [
        sys.executable, "-m", "scripts.discovery",
        str(artifact),
        "--findings",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(SKILL_ROOT),
        check=False,
    )
    finding_lines: list[str] = []
    pointer_lines: list[str] = []
    for raw in result.stdout.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            print(
                f"discovery: skipping non-JSON line: {stripped[:120]!r}",
                file=sys.stderr,
            )
            continue
        if rec.get("record_type") == "finding":
            finding_lines.append(stripped)
        elif rec.get("pointer_type") is not None:
            pointer_lines.append(stripped)
        else:
            print(
                f"discovery: skipping unrecognised record (no "
                f"record_type=finding nor pointer_type): "
                f"{stripped[:120]!r}",
                file=sys.stderr,
            )
    # Synthesise a coverage record for discovery's contribution. The
    # discovery script doesn't emit one because it's a half-lens
    # (pointer extractor + companion check); the orchestrator owns the
    # coverage line for the citation-traceability check, attributed to
    # the mechanical lens (matches how render_output groups it).
    findings_out.write_text(
        "\n".join(finding_lines) + ("\n" if finding_lines else ""),
        encoding="utf-8",
    )
    pointers_out.write_text(
        "\n".join(pointer_lines) + ("\n" if pointer_lines else ""),
        encoding="utf-8",
    )
    if result.returncode != 0:
        err_record = {
            "record_type": "coverage",
            "lens": "mechanical",
            "checks_attempted": 0,
            "checks_completed": 0,
            "claims_examined": 0,
            "claims_flagged": 0,
            "error": (
                f"discovery exited {result.returncode}: "
                f"{result.stderr.strip()[:200]}"
            ),
        }
        with findings_out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(err_record) + "\n")


# ---------------------------------------------------------------------------
# Phase implementations
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _started_at_path(run_dir: Path) -> Path:
    return run_dir / "_started_at"


def _criteria_path_path(run_dir: Path) -> Path:
    return run_dir / "_criteria_source"


def _artifact_path_path(run_dir: Path) -> Path:
    return run_dir / "_artifact_path"


def _mode_path(run_dir: Path) -> Path:
    """Marker file written when --deep was passed at pre_lens time, so
    post_lens can read the mode without the caller having to pass it."""
    return run_dir / "_mode"


def phase_pre_lens(
    artifact: Path,
    project_root: Path,
    *,
    dry_run: bool,
    criteria_only: bool,
    deep: bool = False,
) -> tuple[int, str]:
    """Execute the pre_lens phase.

    Returns (exit_code, primary_message). The primary_message is what
    the CLI prints to stdout — either the PROMPT_READY line or, for
    --criteria-only, the criteria notice + checks dump.
    """
    if not artifact.exists():
        return 2, f"error: artifact not found: {artifact}"

    # 1. Resolve criteria.md.
    resolved = resolve_criteria(project_root)
    scope_checks = extract_scope_checks(resolved.path)
    grounding_checks = extract_grounding_checks(resolved.path)
    structural_checks = extract_structural_checks(resolved.path)

    if criteria_only:
        out_lines = [resolved.notice_line, "", "Scope-lens checks:"]
        for c in scope_checks:
            out_lines.append(f"  - {c.id}: {c.description}")
        return 0, "\n".join(out_lines)

    # 2. Allocate run_id + tmp dir.
    run_id = str(uuid.uuid4())
    rd = run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=False)

    # Record start time + criteria source for post_lens to pick up.
    _started_at_path(rd).write_text(
        _utc_now().isoformat(), encoding="utf-8"
    )
    _criteria_path_path(rd).write_text(
        str(resolved.path), encoding="utf-8"
    )
    _artifact_path_path(rd).write_text(
        str(artifact.resolve()), encoding="utf-8"
    )
    # Persist mode marker so post_lens can read it without re-passing.
    # Empty string = default mode (we still write the file so the absence
    # of a value is unambiguous from a partially-written run dir).
    _mode_path(rd).write_text("deep" if deep else "default", encoding="utf-8")

    # 3. Mechanical prepass.
    _run_mechanical(artifact, project_root, rd / MECHANICAL_FILE)

    # 4. Discovery: writes both findings (for aggregator) and pointers
    # (for the grounding lens) by partitioning discovery's mixed output.
    _run_discovery(artifact, rd / DISCOVERY_FILE, rd / POINTERS_FILE)

    # Output paths for the 3 lens JSONLs and prompts.
    scope_jsonl_path = rd / SCOPE_FILE
    grounding_jsonl_path = rd / GROUNDING_FILE
    structural_jsonl_path = rd / STRUCTURAL_FILE
    scope_prompt_path = rd / SCOPE_PROMPT_FILE
    grounding_prompt_path = rd / GROUNDING_PROMPT_FILE
    structural_prompt_path = rd / STRUCTURAL_PROMPT_FILE

    if dry_run:
        # In dry-run mode we still emit stub <lens>.jsonl files so
        # post_lens has something to aggregate. The stubs record that no
        # LLM was invoked, which preserves the coverage transparency
        # contract — the rendered output names each lens as failed
        # rather than silently dropping it.
        _write_lens_stub(scope_jsonl_path, "scope", len(scope_checks))
        _write_lens_stub(
            grounding_jsonl_path, "grounding", len(grounding_checks),
        )
        _write_lens_stub(
            structural_jsonl_path,
            "structural",
            # Structural's checks_attempted counts the meta-flag too.
            len(structural_checks) + 1,
        )
        # Do NOT write the prompts in dry-run — the SKILL-procedure
        # signal is "no subagent invocation pending". A consumer that
        # still wants to inspect the prompts can omit --dry-run.
        return 0, f"DRY_RUN_READY {rd}"

    # 5. Render lens prompts. Default mode = 3 lens-level prompts.
    # --deep mode = 16 per-check fragments PLUS one extra lens-level
    # structural prompt dedicated to the late-added-callouts meta-flag
    # (which depends on git diff, not per-check semantics — see
    # `DEEP_LATE_CALLOUTS_*` constants above).
    artifact_text = artifact.read_text(encoding="utf-8")
    pointers = _read_pointers(rd / POINTERS_FILE)
    # recent_diff_for() never raises — empty string is the documented
    # degraded response when the artifact is not in a git repo / has no
    # recent commit. The structural prompt treats empty diff as "skip
    # the late-added-callouts meta-flag" per its own SKIP rule.
    recent_diff = git_helpers.recent_diff_for(artifact)

    if deep:
        # Per-check fan-out: 16 prompts (scope-* / grounding-* /
        # structural-*) + 1 lens-level structural prompt for the
        # late-callouts meta-flag.
        per_check = render_per_check_prompts(
            criteria_path=resolved.path,
            artifact_text=artifact_text,
            pointers=pointers,
            recent_diff=recent_diff,
            output_dir=rd,
        )
        # Late-callouts is dispatched SEPARATELY as a lens-level prompt
        # because the structural late-added-callouts meta-flag depends
        # on the recent diff (a lens-level concern), not on per-check
        # semantic. We re-use the existing structural lens template,
        # which already routes the meta-flag via the same {recent_diff}
        # block — the only difference here is the output filename.
        late_callouts_prompt_path = rd / DEEP_LATE_CALLOUTS_PROMPT
        late_callouts_jsonl_path = rd / DEEP_LATE_CALLOUTS_JSONL
        late_callouts_prompt = render_structural_prompt(
            artifact_text=artifact_text,
            structural_checks=structural_checks,
            recent_diff=recent_diff,
            output_path=late_callouts_jsonl_path,
        )
        late_callouts_prompt_path.write_text(
            late_callouts_prompt, encoding="utf-8",
        )

        actual_run_id = rd.name[len(TMP_PREFIX):]
        # Build a multi-line PROMPTS_READY signal listing every prompt
        # path. The first line carries run-id + the "(deep, 17 prompts)"
        # annotation so a consumer that reads only the first line can
        # tell this is the deep fan-out.
        lines = [f"PROMPTS_READY {actual_run_id} (deep, {len(per_check) + 1} prompts)"]
        for r in per_check:
            lines.append(f"{r.lens}-{r.check_id}: {r.prompt_path}")
        lines.append(
            f"structural-late-added-callouts: {late_callouts_prompt_path}"
        )
        return 0, "\n".join(lines)

    scope_prompt = render_scope_prompt(
        artifact_text=artifact_text,
        scope_checks=scope_checks,
        output_path=scope_jsonl_path,
    )
    scope_prompt_path.write_text(scope_prompt, encoding="utf-8")

    grounding_prompt = render_grounding_prompt(
        pointers=pointers,
        grounding_checks=grounding_checks,
        output_path=grounding_jsonl_path,
    )
    grounding_prompt_path.write_text(grounding_prompt, encoding="utf-8")

    structural_prompt = render_structural_prompt(
        artifact_text=artifact_text,
        structural_checks=structural_checks,
        recent_diff=recent_diff,
        output_path=structural_jsonl_path,
    )
    structural_prompt_path.write_text(structural_prompt, encoding="utf-8")

    # Multi-line PROMPTS_READY signal. The first line carries the run-id
    # (so a Claude consumer that reads only the first line can still
    # locate prompts by convention); the next 3 lines name each prompt
    # path explicitly.
    actual_run_id = rd.name[len(TMP_PREFIX):]
    msg = (
        f"PROMPTS_READY {actual_run_id}\n"
        f"scope: {scope_prompt_path}\n"
        f"grounding: {grounding_prompt_path}\n"
        f"structural: {structural_prompt_path}"
    )
    return 0, msg


def _write_lens_stub(out_path: Path, lens: str, checks_attempted: int) -> None:
    """Write a single coverage record marking the lens as not-invoked.
    Used by --dry-run to keep the post_lens render path exercised
    without burning subagent quota."""
    stub_coverage = {
        "record_type": "coverage",
        "lens": lens,
        "checks_attempted": checks_attempted,
        "checks_completed": 0,
        "claims_examined": 0,
        "claims_flagged": 0,
        "error": "dry-run, no LLM invoked",
    }
    out_path.write_text(json.dumps(stub_coverage) + "\n", encoding="utf-8")


def _read_pointers(pointers_path: Path) -> list[dict]:
    """Read the partitioned pointers.jsonl and return parsed records.

    Returns an empty list if the file is missing or empty — the
    grounding prompt's substrate-less artifact instructions handle that
    case by emitting the no-checkable-claims-found advisory.
    """
    if not pointers_path.exists():
        return []
    out: list[dict] = []
    for raw in pointers_path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            out.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return out


def phase_post_lens(
    run_id: str,
    *,
    keep_temp: bool,
    no_tag: bool = False,
    allow_tag_on_partial_failure: bool = False,
) -> tuple[int, str]:
    """Execute the post_lens phase: aggregate JSONL + dedup + tag + render.

    `allow_tag_on_partial_failure` (ccp-021): when True AND the
    frontmatter writer detects a silent lens failure (ccp-020 synthetic
    coverage shape), downgrade the tag from `proofread-passed-<date>`
    to `proofread-attempted-<date>` rather than refusing outright.
    Default False — the default behaviour is to refuse the tag when an
    audit lens silently failed.
    """
    try:
        rd = _find_run_dir_by_partial(run_id)
    except FileNotFoundError as exc:
        return 2, f"error: {exc}"

    # Pull metadata recorded at pre_lens time.
    started_at_str = _started_at_path(rd).read_text(encoding="utf-8").strip()
    started_at = datetime.fromisoformat(started_at_str)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    criteria_source = _criteria_path_path(rd).read_text(encoding="utf-8").strip()
    artifact_path_str = _artifact_path_path(rd).read_text(encoding="utf-8").strip()

    # The actual run_id is the directory suffix, not the prefix the
    # caller passed in (which may have been a partial match).
    actual_run_id = rd.name[len(TMP_PREFIX):]

    # Mode marker — written at pre_lens time. Absent on legacy run dirs
    # (pre-Wave-5); default to "default" in that case for back-compat.
    mode_path = _mode_path(rd)
    mode = (
        mode_path.read_text(encoding="utf-8").strip()
        if mode_path.exists()
        else "default"
    )

    # Aggregate. In --deep mode we fan in 16 per-check JSONLs (one per
    # substantive check) + the late-callouts lens-level JSONL, rather
    # than the 3 lens-level JSONLs. The mechanical + discovery files
    # are picked up in both modes.
    jsonl_files: list[Path] = [
        rd / MECHANICAL_FILE,
        rd / DISCOVERY_FILE,
    ]
    if mode == "deep":
        for lens, check_ids in DEEP_FANOUT.items():
            for check_id in check_ids:
                jsonl_files.append(rd / f"{lens}-{check_id}.jsonl")
        jsonl_files.append(rd / DEEP_LATE_CALLOUTS_JSONL)
    else:
        jsonl_files.extend([
            rd / SCOPE_FILE,
            rd / GROUNDING_FILE,
            rd / STRUCTURAL_FILE,
        ])
    findings_raw, coverage, errors = aggregate_findings(jsonl_files)

    # Dedup convergent findings. We keep the raw list around for the
    # audit trail (run_record["findings_raw"]) but the renderer only
    # sees the deduped output — convergent records carry "Caught by
    # N/3 lenses:" prefixes that depend on the merge.
    deduped_findings = aggregate_with_dedup(findings_raw)

    # Build run record.
    completed_at = _utc_now()
    run_record = build_run_record(
        run_id=actual_run_id,
        started_at=started_at,
        completed_at=completed_at,
        mode=mode,
        criteria_source=criteria_source,
        artifact_path=artifact_path_str,
    )
    # Audit-trail: preserve every original per-lens finding the dedup
    # collapsed. Out-of-band; not rendered, but available to debug a
    # convergent-grouping question.
    run_record["findings_raw"] = findings_raw

    # Frontmatter write (ccp-014 + ccp-021). Eligibility (vault-finding
    # path + type:finding + zero blocking + --no-tag opt-out + no
    # silent-lens-failure) is the writer's responsibility; we just pass
    # the inputs. `coverage` here is the SAME aggregated list the
    # renderer consumes (post-ccp-020-synthetic) — passing it lets the
    # writer key off the synthetic `error:` records that mark a silent
    # lens failure. `allow_tag_on_partial_failure=False` is the default
    # surface; a future orchestrator flag can flip it without further
    # changes to this call site.
    artifact_path = Path(artifact_path_str)
    write_result = write_proofread_tag(
        artifact_path=artifact_path,
        findings=deduped_findings,
        no_tag=no_tag,
        lens_coverage=coverage,
        allow_tag_on_partial_failure=allow_tag_on_partial_failure,
    )
    if write_result.status == "written" and write_result.tag_added:
        run_record["tagged_artifact"] = write_result.tag_added
    elif write_result.status == "skipped_silent_lens_failure":
        # ccp-021: surface the refusal in the rendered output so the user
        # knows the audit was inconclusive (lens output silently rejected
        # by the schema validator — see ccp-020 synthetic coverage).
        # Pre-mortem still the literal last line; "Tag refused:" goes in
        # the same render slot as "Tagged artifact:".
        run_record["tag_refused"] = write_result.error or "tag refused (no reason given)"

    # Render — sees deduped findings only.
    output = render(deduped_findings, coverage, run_record)

    # Cleanup (unless --keep-temp).
    if not keep_temp:
        shutil.rmtree(rd, ignore_errors=True)

    # Aggregation errors go to stderr so they don't pollute the rendered
    # markdown that downstream tools / the user reads on stdout.
    stderr_msg = ""
    if errors:
        stderr_msg = "aggregation warnings:\n" + "\n".join(
            f"  {e}" for e in errors
        )

    return 0, output + ("\n" + stderr_msg if stderr_msg else "")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cc:proofread.orchestrator",
        description=(
            "Driver script for /cc:proofread. Two-phase Python harness; "
            "the Anthropic Agent dispatch lives in SKILL.md between the "
            "two phases."
        ),
    )
    p.add_argument(
        "--phase",
        choices=("pre_lens", "post_lens"),
        required=True,
        help=(
            "pre_lens: run criteria + mechanical + discovery + scope "
            "prompt rendering. post_lens: aggregate + render."
        ),
    )
    p.add_argument(
        "target",
        nargs="?",
        help=(
            "For pre_lens: path to the artifact under audit. "
            "For post_lens: the run_id (or its prefix) returned by pre_lens."
        ),
    )
    p.add_argument(
        "--project-root",
        default=None,
        help=(
            "Path to the project root for criteria resolution and "
            "wikilink lookups. Defaults to the current working directory."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "(pre_lens only) skip the LLM subagent path; write a stub "
            "scope.jsonl so post_lens can still run."
        ),
    )
    p.add_argument(
        "--keep-temp",
        action="store_true",
        help=(
            "(post_lens only) do not delete /tmp/cc-proofread-<run-id>/ "
            "after rendering; useful for debug."
        ),
    )
    p.add_argument(
        "--criteria-only",
        action="store_true",
        help=(
            "(pre_lens only) print resolved criteria.md path + scope-lens "
            "checks; exit. Useful for verifying a project override took."
        ),
    )
    p.add_argument(
        "--deep",
        action="store_true",
        help=(
            "(pre_lens only) render one prompt per substantive check "
            "(16 per-check fragments + 1 lens-level late-callouts) "
            "instead of 3 lens-level prompts. Costs ~5x default mode."
        ),
    )
    p.add_argument(
        "--no-tag",
        action="store_true",
        help=(
            "(post_lens only) skip the `proofread-passed-<date>` "
            "frontmatter write for clean vault-finding passes."
        ),
    )
    p.add_argument(
        "--allow-tag-on-partial-failure",
        action="store_true",
        help=(
            "(post_lens only) when a silent lens failure is detected "
            "(ccp-020 synthetic coverage), write "
            "`proofread-attempted-<date>` instead of refusing the tag. "
            "NOT default — only use when you explicitly want the audit "
            "trail of a partial run preserved in the artifact's tags."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    project_root = (
        Path(args.project_root) if args.project_root else Path(os.getcwd())
    )

    if args.phase == "pre_lens":
        if not args.target:
            print(
                "error: --phase pre_lens requires an artifact path",
                file=sys.stderr,
            )
            return 2
        artifact = Path(args.target)
        code, msg = phase_pre_lens(
            artifact, project_root,
            dry_run=args.dry_run,
            criteria_only=args.criteria_only,
            deep=args.deep,
        )
    else:
        if not args.target:
            print(
                "error: --phase post_lens requires a run_id",
                file=sys.stderr,
            )
            return 2
        code, msg = phase_post_lens(
            args.target,
            keep_temp=args.keep_temp,
            no_tag=args.no_tag,
            allow_tag_on_partial_failure=args.allow_tag_on_partial_failure,
        )

    if code == 0:
        print(msg)
    else:
        print(msg, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
