"""Regex constants used by scripts/discovery.py.

Separate file from ccp-004's scripts/patterns.py to avoid the parallel-build
race — discovery owns its own pattern surface. If the two converge in a
later wave, this file is the merge target.

Patterns are compiled with `re.MULTILINE | re.VERBOSE` where useful so the
.pattern attribute reads cleanly in failure messages.

Pattern types this file owns:

- FILE_PATH_RE — relative paths to known project-shape locations:
    state.yaml, vault/knowledge/<slug>.md, experiments/<slug>.md
  Deliberately conservative: only the directories the discovery
  step is documented to recognise (per ccp-005 AC). Absolute paths
  and arbitrary URLs are intentionally OUT of scope; we want
  high-precision pointers, not a generic URL grabber.

- CHART_ID_RE — Amplitude saved-chart URL shape `chart/<8-char-alnum>`.
  Matches both the bare path token and the same token inside a
  fully-qualified analytics.amplitude.com URL — we just key on the
  `chart/<id>` substring.

- STATE_YAML_DOTTED_RE — `state.yaml.<field>(.<field>)+` dotted path.
  Two or more dot-separated identifier segments after the literal
  `state.yaml`. Single-segment `state.yaml.foo` is intentionally
  not matched — the AC examples are always multi-segment, and a
  one-segment match would over-fire on the literal filename
  occurring next to a possessive ("state.yaml's contents").

- WIKILINK_RE — `[[slug]]` with optional pipe alias `[[slug|display]]`.
  Used as a passthrough when discovery is asked to consume
  mechanical-prepass wikilinks; we ALSO scan them here so discovery
  is independently usable when the prepass JSONL isn't available.

- NUMERICAL_VALUE_RE — `+6.4pp`, `7%`, `1.5x`, `2×`. The companion
  citation-traceability-missing check fires when one of these
  occurs without an adjacent pointer.

- CITATION_HINT_RE — substrings that count as "a citation is here":
  `[chart <id>]`, `(state.yaml § ...)`, `[[<wikilink>]]`, or a bare
  file-path token from FILE_PATH_RE. Used by the
  citation-traceability check; deliberately permissive on the
  citation side (low false-positive rate on the COMPLAINT — we'd
  rather miss flagging a stale claim than complain about a claim
  that actually does carry a pointer).

The CONVERGENT-BLIND-SPOT GUARD (load-bearing): source_text_excerpt
is built by the discovery driver, not the patterns module. These
patterns identify WHERE pointers are; the driver decides what context
to include — and per ccp-005, the rule is "100 chars BEFORE the
pointer, never after." See discovery.py for the slicing logic.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# File paths (project-shape; precision over recall)
# ---------------------------------------------------------------------------
# state.yaml as a standalone token, or vault/knowledge/<slug>.md, or
# experiments/<slug>.md. Slugs allow [a-z0-9-]; trailing .md required for
# vault/experiment files. The literal `state.yaml` matches even without
# a directory prefix because that's how it's cited throughout the corpus.
FILE_PATH_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_/.])                   # left boundary: not in middle of word/path
    (?:
        state\.yaml                        # bare state.yaml token
      | vault/knowledge/[a-z0-9][a-z0-9-]*\.md
      | experiments/[a-z0-9][a-z0-9-]*\.md
    )
    (?![A-Za-z0-9_/])                     # right boundary
    """,
    re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Amplitude chart IDs
# ---------------------------------------------------------------------------
# Match `chart/<id>` where <id> is exactly 8 lowercase alphanumerics. We
# capture the id; the full match is `chart/<id>` so excerpts can show context
# around the citation rather than the bare id.
CHART_ID_RE = re.compile(r"\bchart/([a-z0-9]{8})\b")

# ---------------------------------------------------------------------------
# state.yaml dotted-path references
# ---------------------------------------------------------------------------
# `state.yaml.experiments.<id>.<field>...` — two or more identifier
# segments after the literal. Identifier = `[A-Za-z_][A-Za-z0-9_]*`.
STATE_YAML_DOTTED_RE = re.compile(
    r"\bstate\.yaml(?:\.[A-Za-z_][A-Za-z0-9_]*){2,}\b"
)

# ---------------------------------------------------------------------------
# Wikilinks
# ---------------------------------------------------------------------------
# `[[slug]]` or `[[slug|display text]]`. Slug capture is non-greedy and
# stops at `|` or `]`. Malformed `[[unclosed...` is NOT matched (no
# trailing `]]`), which is the desired behaviour per ccp-005 edge case
# "handled gracefully, no crash".
WIKILINK_RE = re.compile(r"\[\[([^\[\]|\n]+?)(?:\|[^\[\]\n]+?)?\]\]")

# ---------------------------------------------------------------------------
# Numerical claims (for the citation-traceability companion check)
# ---------------------------------------------------------------------------
# Match `+6.4pp`, `6.4pp`, `7%`, `1.5x`, `2×`. The leading sign is
# optional. We DO NOT match bare integers without a unit suffix — those
# are too noisy (page numbers, footnote refs, version numbers etc.).
NUMERICAL_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_])"                # left boundary
    r"[+\-−]?"                          # optional sign (ASCII +/- or U+2212)
    r"\d+(?:\.\d+)?"                    # integer or decimal
    r"(?:pp|%|x|×)"                     # required unit suffix
    r"(?![A-Za-z0-9_])"                 # right boundary
)

# ---------------------------------------------------------------------------
# Citation hints (used by the citation-traceability companion check)
# ---------------------------------------------------------------------------
# Permissive intentionally — if any of these patterns appears within the
# proximity window of a numerical value, we consider the value "cited".
# Bracketed/parenthetical citations carry more semantic weight; bare
# pointer tokens also count.
CITATION_HINT_RE = re.compile(
    r"""
    (?:
        \[\s*chart\s+[a-z0-9]{8}\s*\]      # [chart abc123xy]
      | \(\s*state\.yaml[^)]*\)            # (state.yaml § X)
      | \[\[[^\[\]\n]+?\]\]                # [[wikilink]]
      | chart/[a-z0-9]{8}                  # bare chart/<id>
      | state\.yaml(?:\.[A-Za-z_][A-Za-z0-9_]*)+  # dotted state.yaml path
      | vault/knowledge/[a-z0-9][a-z0-9-]*\.md
      | experiments/[a-z0-9][a-z0-9-]*\.md
    )
    """,
    re.VERBOSE,
)


__all__ = [
    "FILE_PATH_RE",
    "CHART_ID_RE",
    "STATE_YAML_DOTTED_RE",
    "WIKILINK_RE",
    "NUMERICAL_VALUE_RE",
    "CITATION_HINT_RE",
]
