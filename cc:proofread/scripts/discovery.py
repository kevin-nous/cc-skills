"""Discovery step — substrate pointer extraction from an analytical artifact.

CLI:
    python -m scripts.discovery <artifact-path>            → JSONL pointer records to stdout
    python -m scripts.discovery <artifact-path> --findings → also stream citation-traceability-missing
                                                             findings (one JSON record per line) to stdout,
                                                             tagged record_type=finding for the mechanical
                                                             prepass to fold into its output.

Output: newline-delimited JSON. Pointer records have shape::

    {
      "record_type":          "pointer",
      "pointer_type":         "file_path" | "chart_id" | "state_yaml_dotted" | "wikilink",
      "pointer_value":        "<the matched substring>",
      "anchored_claim":       "<sentence the pointer sits inside>",
      "source_text_excerpt":  "<up to 100 chars BEFORE the pointer; NEVER content after>",
      "line_range":           {"start": <1-indexed>, "end": <1-indexed>}
    }

Plus, with --findings, finding records validated against
schemas/finding.schema.json (lens=mechanical, check_id=citation-traceability-missing,
severity=advisory).

CONVERGENT-BLIND-SPOT GUARD (load-bearing architectural property)
-----------------------------------------------------------------
The grounding lens that consumes this output is meant to re-query
substrates independently of the draft's narrative. That property only
holds if discovery never leaks the draft's *interpretation* of a
pointer to downstream consumers.

The mechanical guarantee: source_text_excerpt is built by taking the
N chars BEFORE the pointer's start offset — not the M chars around
it. The text that comes after a substrate pointer is precisely where
draft authors typically write the sentence asserting what the pointer
*means* ("…see chart abc123xy — V2 won by 6.4pp"). Including that
sentence would convergent-blind the grounding lens to the same
conclusion the draft already reached. So we don't.

The `anchored_claim` field is a separate (and intentional)
trade-off: it carries the sentence the pointer is embedded in so
the grounding lens knows WHAT to verify against substrate. This
sentence necessarily contains some of the draft's framing — but
it's the claim being audited, not the verdict being inherited. The
distinction is that anchored_claim is "what to check" while
source_text_excerpt is "where to find it in the doc"; the grounding
lens prompt feeds anchored_claim to the auditor and uses
source_text_excerpt only for location reporting.

If a future change appears to widen source_text_excerpt to include
post-pointer text, it belongs in a different field — not this one.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Iterable

from scripts.discovery_patterns import (
    CHART_ID_RE,
    CITATION_HINT_RE,
    FILE_PATH_RE,
    NUMERICAL_VALUE_RE,
    STATE_YAML_DOTTED_RE,
    WIKILINK_RE,
)

# Window for source_text_excerpt — 100 chars BEFORE the pointer, per
# ccp-005 AC. The guard test exercises that we never read past the
# pointer's start offset.
EXCERPT_BEFORE_CHARS = 100

# Proximity window for the citation-traceability check. AC ccp-005:
# "any numerical value that is NOT within 100 chars of an extracted
# pointer → emit citation-traceability-missing".
CITATION_PROXIMITY_CHARS = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _line_range_for_offsets(text: str, start: int, end: int) -> dict[str, int]:
    """1-indexed line range covering byte offsets [start, end) in text."""
    # count newlines before start / end to derive line numbers
    start_line = text.count("\n", 0, start) + 1
    end_line = text.count("\n", 0, max(start, end - 1)) + 1
    return {"start": start_line, "end": end_line}


def _source_text_excerpt(text: str, pointer_start: int) -> str:
    """Return the up-to-EXCERPT_BEFORE_CHARS chars of text BEFORE pointer_start.

    Critical: the slice end is `pointer_start`, never `pointer_start + N`.
    The slice is taken from raw text so newlines are preserved (the
    consumer can decide whether to normalise whitespace; preserving
    layout makes hand-inspection on fixtures cleaner).
    """
    excerpt_start = max(0, pointer_start - EXCERPT_BEFORE_CHARS)
    return text[excerpt_start:pointer_start]


# Sentence-boundary heuristic for anchored_claim. Conservative — we don't
# need NLP correctness, just a stable rule that picks the surrounding
# sentence. Definition: the sentence is bounded by the nearest preceding
# `. ` / `! ` / `? ` / `\n\n` and the nearest following `. ` / `! ` /
# `? ` / `\n\n`. If no boundary is found, we fall back to the surrounding
# 200-char window so we don't return an empty string.
_SENTENCE_END_RE = re.compile(r"(?:[.!?](?:\s|$)|\n\n)")
_SENTENCE_START_RE = re.compile(r"(?:[.!?](?:\s|$)|\n\n)")


def _anchored_claim(text: str, pointer_start: int, pointer_end: int) -> str:
    """Return the sentence that contains the pointer.

    Why this is acceptable in the convergent-blind-spot model: the
    anchored_claim is the IN-DRAFT CLAIM BEING AUDITED. The grounding
    lens needs to know what to verify, not just that there's a pointer.
    The narrative AFTER the sentence (the next paragraph, the
    explanatory bullets) is the draft's interpretation layer — that
    is what we strip via source_text_excerpt's before-only slice.
    """
    # Search backwards for sentence start
    window_start = max(0, pointer_start - 400)
    pre = text[window_start:pointer_start]
    last_break = None
    for match in _SENTENCE_START_RE.finditer(pre):
        last_break = match
    if last_break is not None:
        sentence_start = window_start + last_break.end()
    else:
        sentence_start = window_start

    # Search forwards for sentence end
    window_end = min(len(text), pointer_end + 400)
    post = text[pointer_end:window_end]
    first_break = _SENTENCE_END_RE.search(post)
    if first_break is not None:
        sentence_end = pointer_end + first_break.end()
    else:
        sentence_end = window_end

    return text[sentence_start:sentence_end].strip()


# ---------------------------------------------------------------------------
# Pointer extractors
# ---------------------------------------------------------------------------

def _extract_with_pattern(
    text: str,
    pattern: re.Pattern[str],
    pointer_type: str,
    *,
    value_group: int = 0,
) -> list[dict]:
    """Run a regex over the artifact and emit pointer records.

    value_group=0 → pointer_value is the full match.
    value_group=N → pointer_value is the Nth capture group (e.g. the
                    bare chart id from `chart/<id>`).
    """
    out: list[dict] = []
    for m in pattern.finditer(text):
        ptr_start = m.start()
        ptr_end = m.end()
        pointer_value = m.group(value_group) if value_group else m.group(0)
        out.append({
            "record_type": "pointer",
            "pointer_type": pointer_type,
            "pointer_value": pointer_value,
            "anchored_claim": _anchored_claim(text, ptr_start, ptr_end),
            "source_text_excerpt": _source_text_excerpt(text, ptr_start),
            "line_range": _line_range_for_offsets(text, ptr_start, ptr_end),
            # internal-only: pointer offset, used downstream for
            # proximity calculations. Stripped before stdout emission.
            "_offset": ptr_start,
        })
    return out


def extract_pointers(text: str) -> list[dict]:
    """Run all pointer extractors and return one list of pointer records.

    Order of types is stable for deterministic CLI output: file_path,
    chart_id, state_yaml_dotted, wikilink. Within a type, order is
    the regex match order (i.e. by appearance in the artifact).
    """
    pointers: list[dict] = []
    pointers += _extract_with_pattern(text, FILE_PATH_RE, "file_path")
    pointers += _extract_with_pattern(
        text, CHART_ID_RE, "chart_id", value_group=1
    )
    pointers += _extract_with_pattern(
        text, STATE_YAML_DOTTED_RE, "state_yaml_dotted"
    )
    pointers += _extract_with_pattern(
        text, WIKILINK_RE, "wikilink", value_group=1
    )
    return pointers


# ---------------------------------------------------------------------------
# Citation-traceability companion check
# ---------------------------------------------------------------------------

def find_uncited_numerical_claims(text: str) -> list[dict]:
    """Detect numerical values with no citation hint within proximity.

    Returns a list of finding records (record_type=finding, lens=mechanical,
    check_id=citation-traceability-missing, severity=advisory) that the
    mechanical prepass can fold into its own JSONL output.

    Heuristic: for each numerical-value match, scan a window of
    +/- CITATION_PROXIMITY_CHARS chars around it; if no
    CITATION_HINT_RE match overlaps that window, emit a finding.
    """
    findings: list[dict] = []
    citation_matches = list(CITATION_HINT_RE.finditer(text))

    for m in NUMERICAL_VALUE_RE.finditer(text):
        num_start = m.start()
        num_end = m.end()
        win_start = num_start - CITATION_PROXIMITY_CHARS
        win_end = num_end + CITATION_PROXIMITY_CHARS

        # Does any citation hint overlap this window?
        has_citation = any(
            cm.end() > win_start and cm.start() < win_end
            for cm in citation_matches
        )
        if has_citation:
            continue

        loc = _line_range_for_offsets(text, num_start, num_end)
        findings.append({
            "record_type": "finding",
            "id": str(uuid.uuid4()),
            "lens": "mechanical",
            "check_id": "citation-traceability-missing",
            "severity": "advisory",
            "claim": _anchored_claim(text, num_start, num_end),
            "location": {
                "line_range_start": loc["start"],
                "line_range_end": loc["end"],
            },
            "finding": (
                f"Numerical claim '{m.group(0)}' has no substrate pointer "
                f"within {CITATION_PROXIMITY_CHARS} chars (no chart id, "
                f"state.yaml path, wikilink, or file path nearby). The "
                f"grounding lens cannot independently verify this number."
            ),
            "remediation": (
                "Add a substrate pointer next to the claim: a chart id, a "
                "state.yaml dotted path, a [[vault-finding]] wikilink, or "
                "a file path the reader can re-query."
            ),
        })
    return findings


# ---------------------------------------------------------------------------
# Output emission
# ---------------------------------------------------------------------------

def _strip_internal_fields(record: dict) -> dict:
    """Remove leading-underscore keys before emission (internal-only)."""
    return {k: v for k, v in record.items() if not k.startswith("_")}


def emit_records(records: Iterable[dict], stream=None) -> None:
    """Write records as newline-delimited JSON.

    stream defaults to sys.stdout but is resolved at call time, not at
    function-definition time, so pytest's capsys can intercept it.
    """
    if stream is None:
        stream = sys.stdout
    for r in records:
        stream.write(json.dumps(_strip_internal_fields(r), ensure_ascii=False))
        stream.write("\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(artifact_path: Path, *, emit_findings: bool) -> int:
    if not artifact_path.exists():
        print(f"discovery: artifact not found: {artifact_path}", file=sys.stderr)
        return 2
    try:
        text = artifact_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"discovery: artifact is not UTF-8: {exc}", file=sys.stderr)
        return 2

    pointers = extract_pointers(text)
    emit_records(pointers)
    if emit_findings:
        emit_records(find_uncited_numerical_claims(text))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="discovery",
        description=(
            "Extract substrate pointers from an analytical artifact. "
            "Emits newline-delimited JSON to stdout. Read-only."
        ),
    )
    parser.add_argument("artifact_path", type=Path)
    parser.add_argument(
        "--findings",
        action="store_true",
        help=(
            "Also emit citation-traceability-missing findings (companion "
            "check folded into mechanical prepass output)"
        ),
    )
    args = parser.parse_args(argv)
    return run(args.artifact_path, emit_findings=args.findings)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
