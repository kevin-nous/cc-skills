"""ccp-008 aggregator — concatenate per-lens JSONL files into a single
(findings, coverage, run_record) triple ready for render_output.render().

The aggregator's contract is narrow:

1. Read each input JSONL path line by line.
2. Validate each line against schemas/finding.schema.json.
3. Partition by record_type into findings / coverage records.
4. Build a RunRecord from caller-provided metadata.
5. Return the triple. Skip-and-log on invalid lines; do NOT raise.

Convergent-dedup across lenses is explicitly NOT this issue's job —
that's ccp-011. Here we just collect.

Library use:

    from scripts.aggregator import aggregate_findings, build_run_record
    findings, coverage, errors = aggregate_findings([p1, p2, p3])
    run_record = build_run_record(
        run_id=..., started_at=..., completed_at=...,
        mode="default", criteria_source=...,
        artifact_path=...,
    )
    # Pass to scripts.render_output.render(findings, coverage, run_record)

Why this lives at scripts/aggregator.py:
    The orchestrator (ccp-008) collects JSONL output from the mechanical
    prepass, discovery, and the scope auditor subagent into one tmp dir.
    Each lens / step writes to its own file; this module is where they
    converge before the renderer is called. Splitting it out keeps the
    orchestrator's two phases (pre_lens / post_lens) testable
    independently.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from scripts.validate_finding import validate_finding

try:
    from jsonschema.exceptions import ValidationError
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "jsonschema is required (pip install jsonschema)"
    ) from exc


_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core aggregation
# ---------------------------------------------------------------------------


def aggregate_findings(
    jsonl_files: Iterable[Path],
) -> tuple[list[dict], list[dict], list[str]]:
    """Read all input JSONL files; return (findings, coverage, errors).

    `errors` is a list of human-readable strings (one per skipped line)
    so the orchestrator can surface them as part of its run summary
    without crashing on a single malformed record.

    Missing files are reported as errors but do NOT raise. The orchestrator
    can decide whether a missing scope.jsonl (e.g. subagent never wrote
    it) is fatal or merely a per-lens failure to record in coverage.

    ccp-020 — silent-lens-failure surfacing:
        When a lens's JSONL file existed and had >=1 non-blank line but
        produced zero schema-valid records (or produced valid findings
        but its coverage record specifically failed validation), the
        aggregator emits a synthetic coverage record carrying an
        `error:` string. The renderer (ccp-006 lens-failed path) already
        treats `error:` on a coverage record as a top-level failure
        block in Findings plus an explicit failure line in Coverage,
        so the silent omission of a whole lens from the coverage
        transparency surface is closed at the aggregator.

        These synthetic records DO NOT round-trip through
        `validate_finding` — by construction they violate the schema
        (sentinel -1 counts; lens may be a discovery-style label not in
        LensExceptConvergent). They are aggregator-internal sentinels
        whose only consumers are the renderer (`render_output.render`)
        and the frontmatter writer (`ccp-021`), both of which key off
        the `error:` field shape, not the count fields.
    """
    findings: list[dict] = []
    coverage: list[dict] = []
    errors: list[str] = []

    for path in jsonl_files:
        path = Path(path)
        if not path.exists():
            errors.append(f"{path}: file not found")
            continue

        # Per-file accounting for the ccp-020 synthetic-coverage check.
        # We don't need to look across files — a lens's failure is a
        # property of its own JSONL output, even when the file path
        # shares a lens-stem with another (e.g. discovery shares the
        # `mechanical` lens label).
        per_file_attempted = 0          # non-blank lines we tried to parse
        per_file_valid_finding = 0      # findings that passed validation
        per_file_valid_coverage = 0     # coverage that passed validation
        per_file_finding_attempts = 0   # lines that *claimed* to be findings (valid or not)
        per_file_coverage_attempts = 0  # lines that *claimed* to be coverage (valid or not)
        per_file_lens_hint: str | None = None  # first non-null `lens` we saw in any line

        try:
            with path.open("r", encoding="utf-8") as fh:
                for lineno, raw in enumerate(fh, start=1):
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    per_file_attempted += 1
                    try:
                        record = json.loads(stripped)
                    except json.JSONDecodeError as exc:
                        msg = f"{path}:{lineno}: invalid JSON ({exc.msg})"
                        errors.append(msg)
                        _log.warning(msg)
                        continue
                    # Whether or not it validates, the claimed kind +
                    # lens label inform the synthetic coverage record.
                    claimed_kind = record.get("record_type") if isinstance(record, dict) else None
                    if claimed_kind == "finding":
                        per_file_finding_attempts += 1
                    elif claimed_kind == "coverage":
                        per_file_coverage_attempts += 1
                    if isinstance(record, dict) and per_file_lens_hint is None:
                        lens_val = record.get("lens")
                        if isinstance(lens_val, str) and lens_val:
                            per_file_lens_hint = lens_val
                    try:
                        validate_finding(record)
                    except ValidationError as exc:
                        msg = (
                            f"{path}:{lineno}: schema validation failed "
                            f"({exc.message})"
                        )
                        errors.append(msg)
                        _log.warning(msg)
                        continue
                    kind = record.get("record_type")
                    if kind == "finding":
                        findings.append(record)
                        per_file_valid_finding += 1
                    elif kind == "coverage":
                        coverage.append(record)
                        per_file_valid_coverage += 1
                    # run_record from a sub-step is unusual; the
                    # orchestrator builds the canonical one. Silently
                    # drop any that appear.
        except OSError as exc:
            errors.append(f"{path}: read error: {exc}")
            continue

        # ccp-020 synthetic-coverage emission.
        synthetic = _maybe_synthesize_coverage(
            path=path,
            attempted=per_file_attempted,
            valid_finding=per_file_valid_finding,
            valid_coverage=per_file_valid_coverage,
            finding_attempts=per_file_finding_attempts,
            coverage_attempts=per_file_coverage_attempts,
            lens_hint=per_file_lens_hint,
            coverage_already_emitted=coverage,
        )
        if synthetic is not None:
            coverage.append(synthetic)

    return findings, coverage, errors


# Files whose lens label can be inferred from the filename stem when no
# in-line `lens` field survives parsing. Mirrors the orchestrator's
# constants (MECHANICAL_FILE etc.) — kept local to avoid a circular
# import from scripts.orchestrator.
_STEM_TO_LENS: dict[str, str] = {
    "mechanical": "mechanical",
    "discovery": "mechanical",  # discovery emits mechanical-lens findings
    "scope": "scope",
    "grounding": "grounding",
    "structural": "structural",
}


def _maybe_synthesize_coverage(
    *,
    path: Path,
    attempted: int,
    valid_finding: int,
    valid_coverage: int,
    finding_attempts: int,
    coverage_attempts: int,
    lens_hint: str | None,
    coverage_already_emitted: list[dict],
) -> dict | None:
    """Decide whether to synthesise a coverage record for this file's
    lens; build it if so.

    Two trigger conditions (per ccp-020 AC):

    1. All-rejected:  attempted >= 1 AND valid_finding == 0 AND
       valid_coverage == 0. The lens's whole output was rejected at the
       schema gate — without a synthetic record the lens silently
       vanishes from the Coverage transparency surface.

    2. Coverage-rejected-but-findings-validated:  attempted >= 1 AND
       valid_finding >= 1 AND valid_coverage == 0 AND coverage_attempts
       >= 1. The lens ran and its findings validated, but the coverage
       record itself was malformed. That's a regression to flag —
       coverage counts are how the user dismisses an audit, so they
       can't go missing in silence.

    The "lens emitted findings, never bothered with a coverage record"
    case (valid_finding >= 1, valid_coverage == 0, coverage_attempts
    == 0) is the legitimate discovery-stream shape and does NOT
    trigger a synthetic record.

    Returns the synthetic coverage dict or None. The returned record
    intentionally violates the JSON schema (sentinel -1 counts; lens
    may not appear in LensExceptConvergent if it's an unknown label) —
    callers MUST NOT push it back through validate_finding(). The
    renderer keys off `error:` and ignores the count fields.
    """
    if attempted == 0:
        return None

    if valid_finding == 0 and valid_coverage == 0:
        # All-rejected. The N in the error message is the count of
        # records the lens TRIED to emit — finding_attempts captures
        # what the user would think of as "findings emitted" per the
        # ccp-020 spec wording. If everything was JSON-garbage with
        # no parseable record_type we fall back to the raw line count.
        emitted_n = finding_attempts if finding_attempts > 0 else attempted
        lens = _resolve_lens(path, lens_hint)
        return _make_synthetic(
            lens=lens,
            error=(
                f"{emitted_n} findings emitted, 0 schema-valid — "
                f"lens output rejected (see aggregation warnings at "
                f"end of output)"
            ),
        )

    if (
        valid_finding >= 1
        and valid_coverage == 0
        and coverage_attempts >= 1
    ):
        # Findings made it through, coverage didn't. Per AC #3 use the
        # same synthetic-coverage shape so downstream consumers
        # (renderer + frontmatter writer) treat it identically.
        lens = _resolve_lens(path, lens_hint)
        return _make_synthetic(
            lens=lens,
            error=(
                f"{valid_finding} findings emitted, 0 schema-valid — "
                f"lens output rejected (see aggregation warnings at "
                f"end of output)"
            ),
        )

    return None


def _resolve_lens(path: Path, lens_hint: str | None) -> str:
    """Best-effort lens label for the synthetic record.

    Priority:
      1. The first non-null `lens` field we saw in any parsed JSON line
         (most accurate — comes from the lens's own output).
      2. The filename stem mapped via `_STEM_TO_LENS` (handles the
         all-JSON-garbage case where no record_type ever parsed).
      3. The raw filename stem as last resort.
    """
    if lens_hint:
        return lens_hint
    stem = path.stem
    if stem in _STEM_TO_LENS:
        return _STEM_TO_LENS[stem]
    return stem


def _make_synthetic(*, lens: str, error: str) -> dict:
    """Build the synthetic coverage record per ccp-020 contract.

    Shape locked: ccp-021's tag-write refusal keys off `error:`,
    `record_type`, and `lens`. Adding fields is safe; renaming or
    dropping the keyed ones is a breaking change to the surface.
    """
    return {
        "record_type": "coverage",
        "lens": lens,
        "checks_attempted": -1,
        "checks_completed": 0,
        "claims_examined": -1,
        "claims_flagged": 0,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Run record construction
# ---------------------------------------------------------------------------


def build_run_record(
    *,
    run_id: str,
    started_at: datetime,
    completed_at: datetime,
    mode: str,
    criteria_source: str,
    artifact_path: str | None = None,
    artifact_hash: str | None = None,
) -> dict:
    """Construct the canonical RunRecord dict. Validates against the schema
    before returning. Either artifact_path or artifact_hash must be set —
    enforced by the schema's anyOf rule, surfaced here as a ValueError.
    """
    if not artifact_path and not artifact_hash:
        raise ValueError("either artifact_path or artifact_hash is required")

    record: dict = {
        "record_type": "run_record",
        "run_id": run_id,
        "started_at": _to_iso8601(started_at),
        "completed_at": _to_iso8601(completed_at),
        "mode": mode,
        "criteria_source": criteria_source,
    }
    if artifact_path:
        record["artifact_path"] = artifact_path
    if artifact_hash:
        record["artifact_hash"] = artifact_hash

    validate_finding(record)
    return record


def _to_iso8601(dt: datetime) -> str:
    """Render datetime as ISO 8601 with explicit UTC offset.

    The schema's `format: date-time` follows RFC 3339; we standardise on
    'Z' for UTC to keep snapshot comparisons stable across machines.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    # isoformat emits "+00:00"; convert to the "Z" form the example
    # records (and existing fixtures) use.
    if iso.endswith("+00:00"):
        iso = iso[:-6] + "Z"
    return iso


# ---------------------------------------------------------------------------
# Convergent-finding detection (ccp-011)
# ---------------------------------------------------------------------------
#
# When multiple lenses independently flag the same root cause, the
# orchestrator should surface ONE high-signal convergent record instead
# of N noisy per-lens records. This is the dedup layer between
# aggregate_findings() and render_output.render().
#
# Heuristic (both conditions required):
#   1. claim text similarity >= CLAIM_SIMILARITY_THRESHOLD
#      (difflib.SequenceMatcher ratio — stdlib, no extra deps)
#   2. location.line_range overlap >= MIN_LINE_RANGE_OVERLAP lines
#      (guards against "same wording, different paragraph")
#
# Mechanical ↔ structural convergence IS allowed per PRD §Edge Cases
# (the hedge-promotion-word case study).
#
# Complexity: O(n²) pairwise — fine for n<100 findings. The default-mode
# orchestrator caps at ~3 lenses × ~10 findings/lens = ~30; --deep tops
# out around 60. Past 100 we'd need a blocking strategy
# (e.g. shingle hash of claim text); not worth building now.

CLAIM_SIMILARITY_THRESHOLD: float = 0.80
MIN_LINE_RANGE_OVERLAP: int = 1

# Tie-break order when multiple contributors share the highest severity.
# Picked deliberately: scope auditor and grounding auditor are the
# substantive-claim lenses (highest-leverage framing), structural is the
# craft/readability lens, mechanical is regex-only. When in doubt the
# convergent record inherits its claim/location/finding from the
# substantive end of the stack.
_LENS_TIE_BREAK_ORDER: dict[str, int] = {
    "scope": 0,
    "grounding": 1,
    "structural": 2,
    "mechanical": 3,
    "convergent": 4,  # already-merged input is sorted last; shouldn't occur in practice
}

_SEVERITY_RANK: dict[str, int] = {
    "blocking": 0,  # lower rank wins (higher severity)
    "advisory": 1,
}


def _claim_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio in [0, 1]. Symmetric; case-sensitive.

    We don't lowercase or strip punctuation: the lenses author claims by
    quoting/paraphrasing the draft, so the literal surface form is the
    fairest comparison. Lowercasing would silently bridge claims that
    differ only in proper-noun handling — not the convergence we want.
    """
    return SequenceMatcher(None, a, b).ratio()


def _line_range_overlap(loc_a: dict, loc_b: dict) -> int:
    """Number of overlapping lines between two Location dicts.

    Both ranges are inclusive (per the schema's line_range_start /
    line_range_end semantics). Returns 0 when disjoint, else the
    inclusive overlap count (>=1).
    """
    a_start = loc_a.get("line_range_start")
    a_end = loc_a.get("line_range_end")
    b_start = loc_b.get("line_range_start")
    b_end = loc_b.get("line_range_end")
    if None in (a_start, a_end, b_start, b_end):
        return 0
    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    return max(0, hi - lo + 1)


def _pick_primary(findings: list[dict]) -> dict:
    """Pick the contributor whose claim/location/finding the merged
    record inherits.

    Order:
      1. Highest severity (blocking > advisory).
      2. Tie-break by _LENS_TIE_BREAK_ORDER (scope < grounding <
         structural < mechanical).
      3. Final tie-break: input order (stable — preserves first-write
         wins).
    """
    def key(f: dict) -> tuple[int, int, int]:
        sev_rank = _SEVERITY_RANK.get(f.get("severity", "advisory"), 99)
        lens_rank = _LENS_TIE_BREAK_ORDER.get(f.get("lens", ""), 99)
        return (sev_rank, lens_rank, findings.index(f))
    return min(findings, key=key)


def _merge_cluster(cluster: list[dict]) -> dict:
    """Merge a cluster of >=2 convergent contributors into one record.

    - lens="convergent"
    - convergent_with=ids of OTHER contributors (the primary's own id
      isn't repeated; the renderer adds +1 when computing "Caught by N/3")
    - claim, location, finding inherited from primary
    - severity = primary's severity (already the max by selection)
    - check_id = primary's check_id
    - remediation = primary's remediation
    - confidence = arithmetic mean of contributors' confidences
      (chosen over "1 - prod(1-c_i)" because the conservative read
      matches how a careful reviewer treats convergent findings — three weak signals
      shouldn't sum to certainty; they should average. Documented per
      ccp-011 AC.)
    - evidence = primary's evidence if present (grounding-only field)
    """
    primary = _pick_primary(cluster)
    others = [f for f in cluster if f is not primary]
    other_ids = [f["id"] for f in others if f.get("id")]

    confidences = [f["confidence"] for f in cluster if "confidence" in f]
    if confidences:
        avg_conf = sum(confidences) / len(confidences)
    else:
        avg_conf = None

    merged: dict = {
        "record_type": "finding",
        "lens": "convergent",
        "check_id": primary["check_id"],
        "severity": primary["severity"],
        "claim": primary["claim"],
        "location": dict(primary["location"]),
        "finding": primary["finding"],
        "remediation": primary.get("remediation", ""),
        "convergent_with": other_ids,
    }
    if primary.get("id"):
        merged["id"] = primary["id"]
    if avg_conf is not None:
        merged["confidence"] = round(avg_conf, 4)
    if "evidence" in primary:
        merged["evidence"] = dict(primary["evidence"])
    return merged


def aggregate_with_dedup(findings: list[dict]) -> list[dict]:
    """Cluster + merge convergent findings; return deduped list.

    Contract:
    - Pure function. No I/O, no logging side-effects, no schema
      mutation of inputs. Inputs are read; outputs are fresh dicts.
    - Non-finding records (defensive — shouldn't reach here from the
      orchestrator) pass through unchanged at the head of the list.
    - Empty input → empty output.
    - Single finding → returned unchanged (no convergence possible).
    - Singleton clusters (no peer) pass through unchanged.
    - Multi-member clusters collapse into one "convergent" record;
      original contributors are dropped from the returned list. The
      caller (orchestrator) is responsible for retaining the raw input
      list separately if it wants a findings_raw audit field on the
      run record — this function does not mutate or own that.
    - Clustering uses single-linkage transitive closure: if A~B and
      B~C, all three merge into one cluster even when A and C don't
      directly meet the threshold.

    Complexity: O(n²) pairwise similarity. See module-level note.
    """
    if not findings:
        return []

    # Defensive partition: this function only clusters Finding records.
    # Anything else (coverage / run_record) passes through.
    only_findings = [f for f in findings if f.get("record_type") == "finding"]
    passthrough = [f for f in findings if f.get("record_type") != "finding"]

    n = len(only_findings)
    if n <= 1:
        return list(passthrough) + list(only_findings)

    # Union-Find for transitive clustering.
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        f_i = only_findings[i]
        for j in range(i + 1, n):
            f_j = only_findings[j]
            sim = _claim_similarity(f_i.get("claim", ""), f_j.get("claim", ""))
            if sim < CLAIM_SIMILARITY_THRESHOLD:
                continue
            overlap = _line_range_overlap(
                f_i.get("location", {}), f_j.get("location", {})
            )
            if overlap < MIN_LINE_RANGE_OVERLAP:
                continue
            union(i, j)

    clusters: dict[int, list[dict]] = {}
    for i, f in enumerate(only_findings):
        clusters.setdefault(find(i), []).append(f)

    # Preserve a stable output order: emit clusters in the order their
    # first contributor appeared in the input. Singletons render as-is.
    seen_roots: set[int] = set()
    out: list[dict] = list(passthrough)
    for i in range(n):
        root = find(i)
        if root in seen_roots:
            continue
        seen_roots.add(root)
        members = clusters[root]
        if len(members) == 1:
            out.append(members[0])
        else:
            out.append(_merge_cluster(members))
    return out


# ---------------------------------------------------------------------------
# CLI (debug-only — orchestrator calls into the library directly)
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """Aggregate one or more JSONL files and print the partitioned counts.

    Not part of the orchestrator's hot path — useful for hand-inspecting a
    /tmp/cc-proofread-<run-id>/ directory after a failed run.
    """
    if len(argv) < 2:
        print(
            "usage: python3 -m scripts.aggregator <jsonl-file> "
            "[<jsonl-file> ...]",
            file=sys.stderr,
        )
        return 2

    paths = [Path(a) for a in argv[1:]]
    findings, coverage, errors = aggregate_findings(paths)
    print(f"findings: {len(findings)}")
    print(f"coverage: {len(coverage)}")
    if errors:
        print(f"errors:   {len(errors)}", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
