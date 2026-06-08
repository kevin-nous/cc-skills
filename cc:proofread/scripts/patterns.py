"""Regex pattern constants for the cc:proofread mechanical prepass (ccp-004).

These are intentionally co-located here rather than loaded dynamically from
``default-criteria.md`` to avoid a Wave-2 race with ccp-007's
``criteria_resolver.py``. Once ccp-007's resolver lands, the reconcile
commit will refactor this module to source the wordlists from the YAML
blocks in ``default-criteria.md`` directly — at which point this file
becomes a thin facade or disappears entirely.

The values below MUST stay in lockstep with the four ``mechanical prepass``
YAML blocks in ``default-criteria.md`` (the canonical source of record).
If you edit a wordlist here, edit the matching block there in the same
commit; the test suite does not yet enforce cross-file parity but will
once ccp-007 lands.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# hedge-promotion-regex
# --------------------------------------------------------------------------
# Canonical wordlist from default-criteria.md § Mechanical prepass.
HEDGE_WORDS: tuple[str, ...] = (
    "comfortably",
    "decisively",
    "clearly",
    "obviously",
    "definitely",
    "definitively",
    "undoubtedly",
    "certainly",
)

# --------------------------------------------------------------------------
# wikilink-resolution
# --------------------------------------------------------------------------
# Standard Obsidian-style wikilink: [[slug]] or [[slug|display text]].
# Slug is the canonical identifier; display text is rendered only.
WIKILINK_PATTERN = r"\[\[([^\]\|]+?)(?:\|[^\]]*)?\]\]"

# Directories under <project-root> a wikilink slug may resolve into. Order
# is significant: the resolver walks the list in order, first match wins.
WIKILINK_SEARCH_DIRS: tuple[str, ...] = (
    "vault/knowledge",
    "experiments",
)

# --------------------------------------------------------------------------
# could-vs-will-detection
# --------------------------------------------------------------------------
# Hedging modal + decision verb. AC names: could/might/may × ramp/ship/
# kill/deploy/launch. The full pattern is built at runtime so the verb
# list stays a single source of truth.
HEDGE_MODALS: tuple[str, ...] = ("could", "might", "may")
DECISION_VERBS: tuple[str, ...] = (
    "ramp",
    "ship",
    "kill",
    "deploy",
    "launch",
    "roll",      # "roll out / roll back"
    "absorb",
)

# Explicit decision markers that, when present between the hedge and a
# later mention of the verb, neutralise the could-vs-will flag.
DECISION_MARKERS: tuple[str, ...] = (
    "decided",
    "we will",
    "we'll",
    "ship",
    "shipping",
    "go with",
    "going with",
    "chose",
    "chosen",
)

# --------------------------------------------------------------------------
# acronym-on-first-use
# --------------------------------------------------------------------------
# /\b[A-Z]{2,5}\b/ per the AC. The 2-5 cap avoids flagging long uppercase
# product strings (e.g. "BEFORE-AFTER") while still catching short acronyms.
ACRONYM_PATTERN = r"\b[A-Z]{2,5}\b"

# Universally-known acronyms the AC explicitly allowlists, plus a thin
# layer of analytical-writing standards (CSV/JSON/etc.). Keep tight:
# anything domain-specific belongs in a project criteria.md override,
# not here.
ACRONYM_ALLOWLIST: frozenset[str] = frozenset({
    # AC-named exemplars
    "URL", "HTTP", "JSON",
    # Network / transport standards
    "HTTPS", "TCP", "UDP", "DNS", "IP", "API", "REST",
    # Encodings / formats
    "CSV", "YAML", "XML", "PDF", "HTML", "CSS", "JS", "SQL", "UTF",
    # Time / calendar
    "UTC", "GMT", "PST", "EST", "AM", "PM",
    # Geography / orgs
    "UK", "US", "USA", "EU",
    # Common business
    "CEO", "CTO", "CFO", "VP", "PM", "PR", "QA",
    # Misc unambiguous
    "ID", "OS", "VM", "AI", "ML", "OK", "TBD", "FAQ", "PRD",
    "TLDR", "TLDR;", "MVP", "RFC",
    # "TL;DR" splits across the ; into TL + DR under \b — allowlist both.
    "TL", "DR",
    # Authoring / repo conventions (markdown file names, code annotations).
    # Added per ccp-016 — these recur in cc:* / pan:* / vault prose as
    # file-convention identifiers, not acronyms.
    "SKILL", "README", "MOC", "TODO", "FIXME", "NB", "IMO", "IIRC",
    # Pronouns/affixes treated as acronyms by the regex
    "A", "I", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE",
    "IF", "IN", "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR",
    "SO", "TO", "UP", "US", "WE",
})

# Filename extensions: if an ACRONYM-shaped token is immediately followed
# by one of these (e.g. ``SKILL.md``, ``PRD.md``, ``config.yaml``), it's a
# filename reference, not an acronym in prose. Skipped by the acronym check.
# Added per ccp-016.
ACRONYM_FILENAME_EXTENSIONS: tuple[str, ...] = (
    ".md", ".py", ".json", ".yaml", ".yml", ".txt",
)

# Window (in chars) around the first occurrence to scan for an inline
# expansion of the form "Acronym (Expansion)" or "Expansion (ACRONYM)".
# The AC names 200 chars.
ACRONYM_DEFINITION_WINDOW: int = 200

# --------------------------------------------------------------------------
# Body / frontmatter exclusion helpers
# --------------------------------------------------------------------------
# Mechanical checks should not fire inside fenced code blocks or YAML
# frontmatter. The body extractor uses these.
FRONTMATTER_DELIM = "---"
FENCE_DELIMS: tuple[str, ...] = ("```", "~~~")
