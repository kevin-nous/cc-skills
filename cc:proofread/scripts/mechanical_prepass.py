"""cc:proofread — mechanical prepass (ccp-004 + ccp-017 throttle).

Runs the four regex-only checks defined in ``default-criteria.md`` §
Mechanical prepass on an analytical artifact, before any LLM lens fires.
Emits JSONL records (findings + one terminal coverage record) to stdout;
each record validates against ``schemas/finding.schema.json``.

CLI:
    python -m scripts.mechanical_prepass <artifact-path>
                                         [--project-root PATH]
                                         [--max-findings-per-check N]

Library:
    from scripts.mechanical_prepass import run
    records = run(Path("draft.md"), project_root=Path("./project"))

Design notes:
- Read-only contract. Never writes anywhere except stdout.
- No LLM, no network, no shared-file writes. Pure regex + filesystem
  existence checks for wikilink resolution.
- Uses the ``regex`` library (third-party) — faster than stdlib ``re``
  for the few longer patterns and gives consistent Unicode handling.
- Patterns live in ``scripts/patterns.py``; once ccp-007's
  ``criteria_resolver.py`` lands, that module will pull them from
  ``default-criteria.md`` instead.

ccp-017 throttle:
  Each check's emit list is capped at ``max_findings_per_check`` findings
  (default 5; configurable via CLI flag or criteria.md frontmatter). When
  a check exceeds its cap, the first N findings are kept and a single
  sentinel finding ``check_id: <original>-truncated`` is appended naming
  the suppressed count. Coverage's ``claims_flagged`` continues to report
  the FULL pre-truncation total so the reader sees the true scale; the
  optional ``truncated_counts`` map carries per-check suppressed counts.
"""

from __future__ import annotations

import argparse
import json
import os
import re as _stdlib_re  # used only for frontmatter parsing
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import regex as re  # third-party `regex`, not stdlib `re`

from scripts import patterns
from scripts.validate_finding import validate_finding


# --------------------------------------------------------------------------
# ccp-017: throttle defaults + criteria.md resolution
# --------------------------------------------------------------------------

# Default cap when no CLI flag and no criteria.md override are present.
DEFAULT_MAX_FINDINGS_PER_CHECK: int = 5

# Canonical list of mechanical check ids — used to validate per-check
# override maps coming in from criteria.md frontmatter.
_MECHANICAL_CHECK_IDS: tuple[str, ...] = (
    "hedge-promotion-regex",
    "wikilink-resolution",
    "could-vs-will-detection",
    "acronym-on-first-use",
)


def _resolve_cap(
    check_id: str,
    max_findings_per_check: int | dict[str, int] | None,
) -> int:
    """Resolve the cap for a single check from the call-site config.

    Accepts an int (applied to every check), a dict (per-check override
    with a default for unmapped checks), or None (use the module default).
    """
    if max_findings_per_check is None:
        return DEFAULT_MAX_FINDINGS_PER_CHECK
    if isinstance(max_findings_per_check, int):
        return max(0, max_findings_per_check)
    if isinstance(max_findings_per_check, dict):
        if check_id in max_findings_per_check:
            return max(0, int(max_findings_per_check[check_id]))
        # Dict may also carry a "default" key for the unmapped fallback.
        if "default" in max_findings_per_check:
            return max(0, int(max_findings_per_check["default"]))
        return DEFAULT_MAX_FINDINGS_PER_CHECK
    raise TypeError(
        f"max_findings_per_check must be int | dict | None, "
        f"got {type(max_findings_per_check).__name__}"
    )


def _load_criteria_caps(
    project_root: Path,
) -> int | dict[str, int] | None:
    """Read the resolved criteria.md and pull the
    ``mechanical_prepass.max_findings_per_check`` frontmatter field.

    Returns an int, a dict, or None if the field is absent / criteria
    cannot be located. Failures here are non-fatal — the caller falls back
    to the module default.
    """
    try:
        # Lazy import: criteria_resolver pulls in PyYAML and we want the
        # throttle path to keep working even if PyYAML is missing (we just
        # silently use the module default).
        from scripts.criteria_resolver import resolve_criteria
        import yaml  # type: ignore
    except Exception:
        return None

    try:
        resolved = resolve_criteria(Path(project_root))
        text = resolved.path.read_text(encoding="utf-8")
        m = _stdlib_re.match(r"^---\n(.*?)\n---\n", text, _stdlib_re.DOTALL)
        if not m:
            return None
        fm = yaml.safe_load(m.group(1)) or {}
        if not isinstance(fm, dict):
            return None
        block = fm.get("mechanical_prepass")
        if block is None:
            return None
        if isinstance(block, int):
            # Shorthand: mechanical_prepass: 5
            return block
        if isinstance(block, dict):
            cap = block.get("max_findings_per_check")
            if cap is None:
                return None
            if isinstance(cap, int):
                return cap
            if isinstance(cap, dict):
                # Per-check override map. Filter to known check ids + "default".
                cleaned: dict[str, int] = {}
                for k, v in cap.items():
                    if k == "default" or k in _MECHANICAL_CHECK_IDS:
                        try:
                            cleaned[k] = int(v)
                        except (TypeError, ValueError):
                            continue
                return cleaned or None
        return None
    except Exception:
        return None


# --------------------------------------------------------------------------
# Pre-compiled regexes
# --------------------------------------------------------------------------

_HEDGE_RE = re.compile(
    r"\b(" + "|".join(patterns.HEDGE_WORDS) + r")\b",
    flags=re.IGNORECASE,
)

_WIKILINK_RE = re.compile(patterns.WIKILINK_PATTERN)

_ACRONYM_RE = re.compile(patterns.ACRONYM_PATTERN)

_COULD_WILL_RE = re.compile(
    r"\bwe\s+(" + "|".join(patterns.HEDGE_MODALS) + r")\s+("
    + "|".join(patterns.DECISION_VERBS) + r")\b",
    flags=re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Body extraction (strip frontmatter + fenced code blocks)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _Line:
    """A body line with its original 1-indexed line number."""
    lineno: int
    text: str


def _extract_body_lines(text: str) -> list[_Line]:
    """Strip YAML frontmatter and fenced code blocks; preserve original
    line numbers so finding locations stay traceable."""
    lines = text.splitlines()
    out: list[_Line] = []
    i = 0
    n = len(lines)

    # Frontmatter: only at the very top, --- ... ---
    if i < n and lines[i].strip() == patterns.FRONTMATTER_DELIM:
        j = i + 1
        while j < n and lines[j].strip() != patterns.FRONTMATTER_DELIM:
            j += 1
        if j < n:
            i = j + 1  # advance past closing delim

    in_fence = False
    fence_marker: str | None = None
    while i < n:
        line = lines[i]
        stripped = line.lstrip()
        if not in_fence:
            for delim in patterns.FENCE_DELIMS:
                if stripped.startswith(delim):
                    in_fence = True
                    fence_marker = delim
                    break
            if not in_fence:
                out.append(_Line(lineno=i + 1, text=line))
        else:
            if stripped.startswith(fence_marker or ""):
                in_fence = False
                fence_marker = None
        i += 1
    return out


# --------------------------------------------------------------------------
# Finding construction helper
# --------------------------------------------------------------------------

def _finding(
    *,
    check_id: str,
    severity: str,
    claim: str,
    line_start: int,
    line_end: int,
    finding_text: str,
    remediation: str,
    file: str | None = None,
    section: str | None = None,
) -> dict:
    """Build a Finding record (validated by caller)."""
    location: dict = {
        "line_range_start": max(1, line_start),
        "line_range_end": max(1, line_end),
    }
    if file is not None:
        location["file"] = file
    if section is not None:
        location["section"] = section
    return {
        "record_type": "finding",
        "id": str(uuid.uuid4()),
        "lens": "mechanical",
        "check_id": check_id,
        "severity": severity,
        "claim": claim,
        "location": location,
        "finding": finding_text,
        "remediation": remediation,
    }


# --------------------------------------------------------------------------
# Check 1: hedge-promotion-regex
# --------------------------------------------------------------------------

def _check_hedge_promotion(
    body: list[_Line], artifact_path: Path | None
) -> list[dict]:
    findings: list[dict] = []
    file_str = str(artifact_path) if artifact_path else None
    for line in body:
        for m in _HEDGE_RE.finditer(line.text):
            word = m.group(1)
            findings.append(_finding(
                check_id="hedge-promotion-regex",
                severity="advisory",
                claim=line.text.strip()[:200] or word,
                line_start=line.lineno,
                line_end=line.lineno,
                finding_text=(
                    f"Strong-certainty word '{word}' detected at line "
                    f"{line.lineno}. Flag for the structural lens to verify "
                    "the underlying evidence supports this strength of claim; "
                    "soften if it does not."
                ),
                remediation=(
                    f"Either replace '{word}' with the literal margin "
                    "(e.g. CI bound, σ count) or soften to a hedge that "
                    "matches the strength of evidence."
                ),
                file=file_str,
            ))
    return findings


# --------------------------------------------------------------------------
# Check 2: wikilink-resolution
# --------------------------------------------------------------------------

def _resolve_wikilink(slug: str, project_root: Path) -> bool:
    """Return True if a file matching the slug exists under any of the
    configured WIKILINK_SEARCH_DIRS."""
    slug = slug.strip()
    if not slug:
        return False
    for subdir in patterns.WIKILINK_SEARCH_DIRS:
        candidate = project_root / subdir / f"{slug}.md"
        if candidate.exists():
            return True
    return False


def _check_wikilink_resolution(
    body: list[_Line],
    project_root: Path,
    artifact_path: Path | None,
) -> list[dict]:
    findings: list[dict] = []
    file_str = str(artifact_path) if artifact_path else None
    seen: set[str] = set()
    for line in body:
        for m in _WIKILINK_RE.finditer(line.text):
            slug = m.group(1).strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            if _resolve_wikilink(slug, project_root):
                continue
            findings.append(_finding(
                check_id="wikilink-resolution",
                severity="advisory",
                claim=f"[[{slug}]]",
                line_start=line.lineno,
                line_end=line.lineno,
                finding_text=(
                    f"Wikilink [[{slug}]] does not resolve to an existing "
                    f"file under {project_root}/(vault/knowledge|experiments)."
                ),
                remediation=(
                    f"Fix the slug, create {slug}.md in the appropriate "
                    "knowledge directory, or remove the link if the "
                    "reference is no longer relevant."
                ),
                file=file_str,
            ))
    return findings


# --------------------------------------------------------------------------
# Check 3: could-vs-will-detection
# --------------------------------------------------------------------------

def _check_could_vs_will(
    body: list[_Line], artifact_path: Path | None
) -> list[dict]:
    """Detect hedged option introductions ("we could ramp X") where the
    same verb later appears as a firm plan without an intervening
    decision marker.
    """
    findings: list[dict] = []
    file_str = str(artifact_path) if artifact_path else None

    # Stage 1: collect every hedged introduction.
    # (lineno, modal, verb, char-offset-of-match-end on that line).
    hedges: list[tuple[int, str, str, int]] = []
    for line in body:
        for m in _COULD_WILL_RE.finditer(line.text):
            hedges.append((
                line.lineno, m.group(1).lower(), m.group(2).lower(), m.end()
            ))

    if not hedges:
        return findings

    marker_re = re.compile(
        r"\b(" + "|".join(re.escape(mk) for mk in patterns.DECISION_MARKERS)
        + r")\b",
        flags=re.IGNORECASE,
    )

    # Stage 2: for each hedge, scan forward for a firm restatement
    # (same verb, no hedging modal, no intervening decision marker).
    # The scan starts on the same line right after the hedge match —
    # firm restatement can appear on the same sentence ("we could ramp
    # this … The rollout assumes the ramp.").
    for hedge_lineno, modal, verb, hedge_end in hedges:
        verb_re = re.compile(r"\b" + re.escape(verb) + r"\b", flags=re.IGNORECASE)
        decided = False
        firm_lineno: int | None = None
        for line in body:
            if line.lineno < hedge_lineno:
                continue
            text = line.text
            if line.lineno == hedge_lineno:
                # Only consider the portion after the hedge match.
                text = text[hedge_end:]
            if marker_re.search(text):
                decided = True
                break
            # Skip lines (or remainders) that are themselves another
            # "we could/might/may" — those restate the hedge, not a firm plan.
            if _COULD_WILL_RE.search(text):
                continue
            if verb_re.search(text):
                firm_lineno = line.lineno
                break
        if firm_lineno is not None and not decided:
            findings.append(_finding(
                check_id="could-vs-will-detection",
                severity="advisory",
                claim=(
                    f"line {hedge_lineno}: we {modal} {verb} … → "
                    f"line {firm_lineno}: treated as firm plan"
                ),
                line_start=hedge_lineno,
                line_end=firm_lineno,
                finding_text=(
                    f"Earlier in the artifact, '{verb}' was framed as an "
                    f"option ('we {modal} {verb}'). Line {firm_lineno} "
                    "treats it as the decided plan without an intervening "
                    "decision marker (decided / we will / chose / etc.)."
                ),
                remediation=(
                    "Insert the decision explicitly between the option and "
                    "the firm restatement, or weaken the later language to "
                    "keep the option open."
                ),
                file=file_str,
            ))
    return findings


# --------------------------------------------------------------------------
# Check 4: acronym-on-first-use
# --------------------------------------------------------------------------

def _check_acronym_first_use(
    body: list[_Line], artifact_path: Path | None
) -> list[dict]:
    findings: list[dict] = []
    file_str = str(artifact_path) if artifact_path else None

    # Single concatenated body string for the 200-char window scan, plus a
    # parallel index → lineno lookup so we can locate hits cheaply.
    chunks: list[str] = []
    offsets: list[int] = []  # cumulative char offset where each line starts
    line_lookup: list[int] = []  # lineno for each char position
    running = 0
    for ln in body:
        offsets.append(running)
        chunks.append(ln.text)
        for _ in ln.text:
            line_lookup.append(ln.lineno)
        line_lookup.append(ln.lineno)  # the implicit newline
        running += len(ln.text) + 1
    full_text = "\n".join(chunks)

    seen: set[str] = set()
    for m in _ACRONYM_RE.finditer(full_text):
        token = m.group(0)
        if token in seen:
            continue
        seen.add(token)
        if token in patterns.ACRONYM_ALLOWLIST:
            continue
        start = m.start()
        end = m.end()
        # Filename-convention guard (ccp-016): tokens immediately followed
        # by a known file extension (``SKILL.md``, ``PRD.md``, ``config.yaml``)
        # are filename references, not acronyms in prose.
        trailing = full_text[end:end + 8]
        if any(
            trailing.startswith(ext)
            for ext in patterns.ACRONYM_FILENAME_EXTENSIONS
        ):
            continue
        # Inline expansion heuristic — accept only TWO shapes, both tight:
        #   (a) "ACRONYM (Expansion words)" — paren opens within 3 chars
        #       after the acronym.
        #   (b) "Expansion words (ACRONYM)" — paren containing the
        #       acronym anywhere in the surrounding window.
        # A loose "any parens within 200 chars" rule produces false
        # negatives whenever the artifact uses unrelated parentheticals
        # nearby (e.g. "(resolves)" elsewhere on the line).
        after = full_text[end:end + 3]
        if re.match(r"\s*\(\s*[A-Za-z]", after):
            continue
        window_start = max(0, start - patterns.ACRONYM_DEFINITION_WINDOW)
        window_end = min(len(full_text), end + patterns.ACRONYM_DEFINITION_WINDOW)
        window = full_text[window_start:window_end]
        if re.search(r"\([^)]*\b" + re.escape(token) + r"\b[^)]*\)", window):
            continue
        lineno = (
            line_lookup[start]
            if start < len(line_lookup) else (body[-1].lineno if body else 1)
        )
        findings.append(_finding(
            check_id="acronym-on-first-use",
            severity="advisory",
            claim=token,
            line_start=lineno,
            line_end=lineno,
            finding_text=(
                f"Acronym '{token}' is used without an inline expansion on "
                "first occurrence. A reader unfamiliar with the term has "
                "no way to disambiguate."
            ),
            remediation=(
                f"Spell out '{token}' on first use with the expansion in "
                "parentheses, or move the artifact to a context where the "
                "term is universally known."
            ),
            file=file_str,
        ))
    return findings


# --------------------------------------------------------------------------
# ccp-017: truncation helpers
# --------------------------------------------------------------------------

def _truncation_finding(
    *,
    check_id: str,
    cap: int,
    total: int,
    artifact_path: Path | None,
) -> dict:
    """Build the sentinel finding emitted when a check exceeds its cap.

    ``check_id`` is the original check's id; the sentinel uses
    ``<original>-truncated`` so the render layer can pattern-match and
    style it distinctly. Located at line 1 because the truncation is an
    aggregate property of the whole artifact, not a specific line.
    """
    suppressed = max(0, total - cap)
    return _finding(
        check_id=f"{check_id}-truncated",
        severity="advisory",
        claim=(
            f"{check_id} hit cap of {cap} findings; "
            f"{suppressed} additional findings suppressed"
        ),
        line_start=1,
        line_end=1,
        finding_text=(
            f"Mechanical prepass {check_id} exceeded the configured cap. "
            "Consider widening the cap or reducing the artifact's surface "
            "area for this check."
        ),
        remediation=(
            "Increase --max-findings-per-check, or fix the underlying "
            "class of issue in bulk."
        ),
        file=str(artifact_path) if artifact_path else None,
    )


def _apply_cap(
    check_id: str,
    findings_for_check: list[dict],
    max_findings_per_check: int | dict[str, int] | None,
    artifact_path: Path | None,
) -> tuple[list[dict], int]:
    """Apply the per-check cap.

    Returns ``(emitted_findings, suppressed_count)``. ``emitted_findings``
    is the truncated prefix (length ≤ cap) optionally followed by a
    single sentinel ``<check_id>-truncated`` finding when suppression
    happened. ``suppressed_count`` is ``max(0, total - cap)`` — used to
    populate the coverage record's ``truncated_counts`` map.

    Cap == 0 disables the check (zero findings emitted) but still produces
    a sentinel naming the suppressed count, so downstream consumers can
    tell the check was silenced rather than absent.
    """
    total = len(findings_for_check)
    cap = _resolve_cap(check_id, max_findings_per_check)
    if total <= cap:
        return findings_for_check, 0
    truncated = list(findings_for_check[:cap])
    truncated.append(_truncation_finding(
        check_id=check_id,
        cap=cap,
        total=total,
        artifact_path=artifact_path,
    ))
    return truncated, total - cap


# --------------------------------------------------------------------------
# Public entry: run()
# --------------------------------------------------------------------------

def run(
    artifact_path: Path,
    project_root: Path | None = None,
    max_findings_per_check: int | dict[str, int] | None = None,
) -> list[dict]:
    """Run all four checks against the artifact and return the list of
    records (findings + a single terminal coverage record).

    Read-only. Never writes anywhere. Returns the records; the CLI wraps
    this and prints JSONL to stdout.

    ``max_findings_per_check`` (ccp-017): caps per-check output. Accepts
    an int (uniform cap), a dict mapping check_id → int (with optional
    ``"default"`` key), or ``None`` (resolve from criteria.md frontmatter,
    falling back to ``DEFAULT_MAX_FINDINGS_PER_CHECK``).
    """
    artifact_path = Path(artifact_path)
    if project_root is None:
        project_root = Path(os.getcwd())
    project_root = Path(project_root)

    # Resolve cap config: explicit caller value wins; otherwise consult
    # criteria.md frontmatter; otherwise fall through to the module default.
    if max_findings_per_check is None:
        max_findings_per_check = _load_criteria_caps(project_root)

    records: list[dict] = []
    findings: list[dict] = []
    claims_examined = 0
    error_text: str | None = None
    truncated_counts: dict[str, int] = {}
    full_flagged_total = 0

    try:
        text = artifact_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        error_text = (
            f"artifact is not valid UTF-8: {exc.reason} at byte {exc.start}"
        )
    except FileNotFoundError:
        raise
    except OSError as exc:
        error_text = f"could not read artifact: {exc}"

    if error_text is None:
        body = _extract_body_lines(text)
        # claims_examined = total non-blank body lines scanned.
        claims_examined = sum(1 for l in body if l.text.strip())

        # Run each check, then cap independently. The cap is applied per
        # check (not across the whole artifact) so a noisy hedge sweep
        # doesn't crowd out a single legitimate wikilink failure.
        per_check_raw: list[tuple[str, list[dict]]] = [
            ("hedge-promotion-regex",
             _check_hedge_promotion(body, artifact_path)),
            ("wikilink-resolution",
             _check_wikilink_resolution(body, project_root, artifact_path)),
            ("could-vs-will-detection",
             _check_could_vs_will(body, artifact_path)),
            ("acronym-on-first-use",
             _check_acronym_first_use(body, artifact_path)),
        ]
        for check_id, raw_findings in per_check_raw:
            full_flagged_total += len(raw_findings)
            emitted, suppressed = _apply_cap(
                check_id, raw_findings, max_findings_per_check, artifact_path,
            )
            findings.extend(emitted)
            if suppressed > 0:
                truncated_counts[check_id] = suppressed

    records.extend(findings)
    coverage: dict = {
        "record_type": "coverage",
        "lens": "mechanical",
        "checks_attempted": 4,
        "checks_completed": 4 if error_text is None else 0,
        "claims_examined": claims_examined,
        # ccp-017: report the FULL pre-truncation total, not len(findings)
        # (which includes truncation sentinels and excludes suppressed
        # entries). The user needs to see the true scale.
        "claims_flagged": full_flagged_total,
    }
    if truncated_counts:
        coverage["truncated_counts"] = truncated_counts
    if error_text is not None:
        coverage["error"] = error_text
    records.append(coverage)

    # Validate at emit-time so a bug in a check surfaces here, not in the
    # orchestrator's downstream consumer.
    for r in records:
        validate_finding(r)
    return records


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _emit_jsonl(records: Iterable[dict]) -> None:
    for r in records:
        sys.stdout.write(json.dumps(r, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mechanical_prepass",
        description=(
            "Run cc:proofread's mechanical prepass on a single artifact. "
            "Emits JSONL records (findings + one coverage record) to stdout."
        ),
    )
    parser.add_argument("artifact", help="Path to the artifact to audit.")
    parser.add_argument(
        "--project-root",
        default=None,
        help=(
            "Path to the project root for wikilink resolution. Defaults "
            "to the current working directory."
        ),
    )
    parser.add_argument(
        "--max-findings-per-check",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Cap each mechanical check's output at N findings; the (N+1)th "
            "and beyond are replaced by a single sentinel "
            "'<check_id>-truncated' finding. Default: read from "
            "criteria.md frontmatter "
            "(mechanical_prepass.max_findings_per_check), else "
            f"{DEFAULT_MAX_FINDINGS_PER_CHECK}. Set to 0 to silence a "
            "check entirely (the sentinel still fires so the suppression "
            "is visible in the coverage record)."
        ),
    )
    args = parser.parse_args(argv)

    artifact_path = Path(args.artifact)
    if not artifact_path.exists():
        print(f"error: artifact not found: {artifact_path}", file=sys.stderr)
        return 2

    project_root = (
        Path(args.project_root) if args.project_root else Path(os.getcwd())
    )

    records = run(
        artifact_path,
        project_root=project_root,
        max_findings_per_check=args.max_findings_per_check,
    )
    _emit_jsonl(records)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
