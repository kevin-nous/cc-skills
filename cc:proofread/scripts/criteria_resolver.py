"""Resolve the criteria.md file cc:proofread runs against.

Resolution rule (per ccp-007 AC):

    1. If `<project_root>/.claude/cc:proofread/criteria.md` exists → return it
    2. Else return `<skill_install_dir>/default-criteria.md`
    3. If neither exists → raise CriteriaNotFoundError naming both paths

`<skill_install_dir>` is computed via `Path(__file__).resolve()` so that the
canonical install path is found whether the script is invoked directly from
`~/cc-skills/cc:proofread/scripts/` or via the
`~/.claude/skills/cc:proofread/scripts/` symlink (the symlink is what
`/cc:proofread` is invoked through at runtime).

The resolver validates whichever file it returns before returning it. The
project override path is held to the same schema as the default — if the
project file exists but is malformed, the resolver raises
`InvalidProjectCriteriaError` rather than silently falling back to the
default. The "fail loudly, don't silently fall back" rule is load-bearing:
a project owner who put effort into authoring an override should not have
the skill quietly ignore it because of a typo.

Library use:

    from scripts.criteria_resolver import resolve_criteria, ResolvedCriteria
    resolved = resolve_criteria(Path.cwd())
    # resolved.path → Path to criteria file
    # resolved.source → "project override" | "default — no project override"
    # resolved.notice_line → ready-to-print orchestrator notice

The function returns a `ResolvedCriteria` namedtuple rather than a bare path
because the orchestrator needs both the path (for `criteria_source` in the
run record) and the rendered notice line (for the CLI banner) — keeping both
on one object avoids duplicating the source-label logic at every call site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - install-time error
    raise ModuleNotFoundError(
        "PyYAML is required (pip install pyyaml)"
    ) from exc


# The 4 lens section headers the validator requires. Both default-criteria.md
# and any project override must surface all four — even if the override
# suppresses checks within a section, the section header itself stays so the
# orchestrator's parser has a deterministic shape to traverse.
REQUIRED_LENS_SECTIONS = (
    "Scope auditor",
    "Grounding auditor",
    "Structural auditor",
    "Mechanical prepass",
)

# Canonical relative path of a per-project criteria override, off the
# project root. Centralized here so callers and tests can name the same
# string.
PROJECT_OVERRIDE_RELPATH = Path(".claude/cc:proofread/criteria.md")


class CriteriaResolverError(Exception):
    """Base class for all resolver errors. Catch this if you want to catch
    every resolver failure mode in one except clause."""


class CriteriaNotFoundError(CriteriaResolverError):
    """Neither project override nor default-criteria.md exists.

    The message includes both paths checked so the user can see exactly
    what was looked for.
    """


class InvalidProjectCriteriaError(CriteriaResolverError):
    """Project override file exists but failed schema validation.

    The PRD requires that we do NOT silently fall back to the default in this
    case — the project owner authored the override on purpose and a silent
    fallback would mask their intent.
    """


class InvalidDefaultCriteriaError(CriteriaResolverError):
    """The shipped default-criteria.md failed schema validation. This is a
    build-time bug; raising rather than running with broken defaults."""


@dataclass(frozen=True)
class ResolvedCriteria:
    """Bundle returned by `resolve_criteria`.

    Attributes:
        path: absolute Path to the criteria file the orchestrator will read
        source: short label, either "project override" or
            "default — no project override". Used in the notice line and
            in the run record's `criteria_source_label` field if the
            orchestrator wants the short form rendered alongside the path.
        notice_line: ready-to-print one-line CLI banner of the form
            "Criteria: <path> (<source>)" — kept here so every call site
            renders the notice identically.
    """

    path: Path
    source: str

    @property
    def notice_line(self) -> str:
        return f"Criteria: {self.path} ({self.source})"


def skill_install_dir() -> Path:
    """Return the canonical install dir for cc:proofread.

    Computed via `Path(__file__).resolve()` so symlinked invocation
    (`~/.claude/skills/cc:proofread/scripts/criteria_resolver.py`) and direct
    invocation (`~/cc-skills/cc:proofread/scripts/criteria_resolver.py`)
    return the same canonical real-path. `.parents[1]` walks up from the
    scripts/ subdirectory to the skill root.
    """
    return Path(__file__).resolve().parents[1]


def default_criteria_path() -> Path:
    """Return the canonical path of the shipped default-criteria.md."""
    return skill_install_dir() / "default-criteria.md"


def project_override_path(project_root: Path) -> Path:
    """Return the canonical project-override path under `project_root`.

    No existence check — callers decide whether to test for presence.
    """
    return Path(project_root) / PROJECT_OVERRIDE_RELPATH


def _parse_frontmatter(text: str) -> dict | None:
    """Extract the YAML frontmatter (first --- ... --- block).

    Returns None if no frontmatter found; raises ValueError if the
    frontmatter is malformed YAML (caller decides how to surface).
    """
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return None
    try:
        loaded = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from exc
    if loaded is None:
        # Empty frontmatter — treated as "no schema declared" by callers.
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"frontmatter must be a YAML mapping, got {type(loaded).__name__}"
        )
    return loaded


def _validate_criteria_text(text: str) -> list[str]:
    """Return a list of human-readable validation problems.

    Empty list ⇒ file is valid. Each entry is a single concrete defect so
    the caller can join them into a multi-line error message.

    Validation surface (kept narrow on purpose — the resolver only checks
    structural well-formedness; per-check field validation lives in
    ccp-002's test_criteria_validation.py):

    1. Frontmatter exists
    2. Frontmatter is a YAML mapping
    3. `schema_version: 1` declared
    4. All 4 lens section headers present
    """
    problems: list[str] = []

    if not text.strip():
        problems.append("file is empty")
        return problems

    try:
        fm = _parse_frontmatter(text)
    except ValueError as exc:
        problems.append(str(exc))
        return problems

    if fm is None:
        problems.append(
            "missing YAML frontmatter (expected leading '---' block)"
        )
    else:
        schema_version = fm.get("schema_version")
        if schema_version != 1:
            if schema_version is None:
                problems.append("frontmatter missing schema_version")
            else:
                problems.append(
                    f"schema_version must be 1, got {schema_version!r}"
                )

    for header in REQUIRED_LENS_SECTIONS:
        # Section headers are `## <name>` in the markdown body. We match a
        # `## ` prefix so we don't trip on the header being mentioned in
        # prose. Anchor at line start (re.MULTILINE).
        if not re.search(rf"^##\s+{re.escape(header)}\s*$", text, re.MULTILINE):
            problems.append(f"missing required section header: '## {header}'")

    return problems


def resolve_criteria(project_root: Path) -> ResolvedCriteria:
    """Resolve which criteria.md cc:proofread should load.

    Args:
        project_root: absolute path to the project being audited (typically
            `Path.cwd()` at orchestrator entry, but tests pass a fixture
            directory).

    Returns:
        ResolvedCriteria(path, source) — see the dataclass docstring.

    Raises:
        CriteriaNotFoundError: neither project override nor default exists.
        InvalidProjectCriteriaError: project override exists but fails
            schema validation. The default is NOT used as a silent fallback.
        InvalidDefaultCriteriaError: default file exists but is malformed.
            Indicates a build / install bug.
    """
    override = project_override_path(project_root)
    default = default_criteria_path()

    if override.exists():
        problems = _validate_criteria_text(override.read_text(encoding="utf-8"))
        if problems:
            joined = "\n  - ".join(problems)
            raise InvalidProjectCriteriaError(
                f"project criteria.md at {override} is invalid; "
                f"cc:proofread will not silently fall back to the default. "
                f"Fix or delete the override.\n  - {joined}"
            )
        return ResolvedCriteria(path=override, source="project override")

    if default.exists():
        problems = _validate_criteria_text(default.read_text(encoding="utf-8"))
        if problems:
            joined = "\n  - ".join(problems)
            raise InvalidDefaultCriteriaError(
                f"shipped default-criteria.md at {default} is invalid "
                f"(install / build bug):\n  - {joined}"
            )
        return ResolvedCriteria(
            path=default, source="default — no project override"
        )

    raise CriteriaNotFoundError(
        "no criteria.md found. cc:proofread checked:\n"
        f"  - project override: {override}\n"
        f"  - shipped default:  {default}\n"
        "Install the skill (default-criteria.md should ship with it), or "
        "place a project override at the first path."
    )
