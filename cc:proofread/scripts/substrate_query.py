"""ccp-009 substrate-query helpers — the independent-fetch layer.

The grounding auditor lens calls these helpers to re-fetch substrate
values *independently of the draft's narrative*. The architectural
property they defend is:

    Substrate access is BLIND to the draft. Every helper accepts only
    the substrate pointer's identifier — never the anchored_claim,
    never the source_text_excerpt, never any draft prose. If a helper
    grew an argument that carried draft text, the convergent-blind-spot
    countermeasure would be compromised.

The test ``test_substrate_query_never_includes_draft_text`` enforces
this by inspecting the helper signatures: each public helper takes
only `pointer_value`-style arguments (plus an optional `project_root`
or `key_path` traversal aid), never the full pointer record.

Helpers:

    query_file(path, key_path=None)
        Read a file relative to project_root. If the file is YAML/JSON
        and `key_path` is supplied, traverse the parsed structure with
        dotted keys.

    query_state_yaml(dotted_path, project_root)
        Split a dotted path like
        ``state.yaml.experiments.X.field`` into the file
        (state.yaml) and the in-file key traversal; parse YAML;
        return the resolved value (or an error record).

    query_vault_finding(slug, project_root)
        Look up a vault knowledge or experiments file by slug; return
        the file contents (the grounding subagent reads the markdown
        body itself; this helper just resolves slug → path → text).

    query_amplitude_chart(chart_id)
        STUB. Returns an error record explaining that Amplitude chart
        fetches require an orchestrator-level Agent call (the MCP isn't
        invocable from a pure helper). The grounding subagent surfaces
        this as an advisory finding rather than crashing.

    query_pointer(pointer, project_root)
        Dispatcher. Takes a pointer record (as emitted by discovery.py),
        routes to the right helper based on `pointer_type`, returns a
        normalised ``{resolved, value, resolved_path, error?}`` record.

All helpers return ``{resolved: bool, value: Any, resolved_path: str|None,
error: str|None}``.  resolved=False with an informative error is the
expected response when a file is missing or a key isn't found — never
a raised exception.

The dispatcher is the ONE function that takes the full pointer record;
even it does not pass any field other than ``pointer_value`` to the
downstream helpers. This is the load-bearing isolation property.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml


# ---------------------------------------------------------------------------
# Grounding prompt rendering (ccp-009)
#
# Lives here rather than in orchestrator.py because orchestrator.py is
# owned by a parallel issue (ccp-010 / orchestrator integration). The
# render function is pure (template + checks + pointers → string); the
# orchestrator simply calls it once the parallel-batch glue lands.
# ---------------------------------------------------------------------------


_GROUNDING_PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "prompts" / "grounding_auditor.md"
)

# Substrate-less artifact sentinel — surfaced to the lens so it knows
# to emit the no-checkable-claims-found advisory rather than silently
# returning a zero-finding pass.
_NO_POINTERS_BANNER = (
    "(none — discovery found 0 substrate pointers in this artifact; "
    "per the substrate-less artifact instructions above, emit ONE finding "
    "with check_id=no-checkable-claims-found, severity=advisory, and a "
    "coverage record with substrate_refs_found=0)"
)


def _format_pointers_for_prompt(pointers: list[dict]) -> str:
    """Render the discovery pointer list as a numbered block for the
    grounding lens. ONLY the four allowed fields per pointer are
    emitted; ``source_text_excerpt`` is deliberately dropped here even
    if it was present on the input record — the grounding prompt
    template's convergent-blind-spot check enumerates the fields the
    lens may see, and we honour that contract at the render layer too.
    """
    if not pointers:
        return _NO_POINTERS_BANNER
    lines: list[str] = []
    for i, ptr in enumerate(pointers, start=1):
        # Hand-build a sanitised dict so we cannot accidentally leak
        # source_text_excerpt or other draft-context fields into the
        # rendered prompt. This is the render-layer mirror of the
        # signature-level guard in query_pointer.
        record = {
            "pointer_type": ptr.get("pointer_type"),
            "pointer_value": ptr.get("pointer_value"),
            "anchored_claim": ptr.get("anchored_claim"),
            "line_range": ptr.get("line_range"),
        }
        lines.append(f"### Pointer {i}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(record, ensure_ascii=False))
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_grounding_checks_for_prompt(checks: list) -> str:
    """Render the grounding-lens check list. Accepts any iterable of
    objects exposing the five fields (id, description, trigger_heuristic,
    remediation_language, example_failure_mode); the orchestrator's
    ``ScopeCheck``-shaped dataclass works untouched."""
    if not checks:
        return "(no grounding checks defined in criteria.md)"
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


def _grounding_prompt_template() -> str:
    return _GROUNDING_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


def render_grounding_prompt(
    pointers: list[dict],
    grounding_checks: list,
    output_path: Path,
) -> str:
    """Compose the grounding auditor prompt by substituting the three
    placeholders. Uses str.replace rather than .format because the
    template contains many literal curly braces in JSON examples.

    Note the substitution order: output_path → grounding_checks →
    pointers (last). Pointer JSON may contain literal placeholder
    strings; substituting it last prevents further substitution.

    Critical property: the template does NOT have an ``{artifact}``
    placeholder. The grounding lens never sees the draft prose — only
    the pointer records, which are themselves sanitised by
    ``_format_pointers_for_prompt`` to drop ``source_text_excerpt``.
    """
    template = _grounding_prompt_template()
    body = template
    body = body.replace("{output_path}", str(output_path))
    body = body.replace(
        "{grounding_checks}",
        _format_grounding_checks_for_prompt(grounding_checks),
    )
    body = body.replace(
        "{pointers}", _format_pointers_for_prompt(pointers)
    )
    return body


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


def _ok(value: Any, resolved_path: str | None) -> dict:
    return {
        "resolved": True,
        "value": value,
        "resolved_path": resolved_path,
        "error": None,
    }


def _err(error: str, resolved_path: str | None = None) -> dict:
    return {
        "resolved": False,
        "value": None,
        "resolved_path": resolved_path,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Generic traversal
# ---------------------------------------------------------------------------


def _traverse(data: Any, key_path: list[str]) -> tuple[bool, Any, str | None]:
    """Walk ``key_path`` over ``data``. Returns (found, value, error)."""
    current: Any = data
    for i, key in enumerate(key_path):
        if isinstance(current, Mapping):
            if key in current:
                current = current[key]
                continue
            return (
                False,
                None,
                f"key {key!r} not found at path "
                f"{'.'.join(key_path[: i + 1])}",
            )
        if isinstance(current, list):
            # Support integer indexing inside a key path (rare for our
            # use case but harmless).
            try:
                idx = int(key)
            except ValueError:
                return (
                    False,
                    None,
                    f"non-integer key {key!r} into list at path "
                    f"{'.'.join(key_path[: i + 1])}",
                )
            if 0 <= idx < len(current):
                current = current[idx]
                continue
            return (
                False,
                None,
                f"index {idx} out of range at path "
                f"{'.'.join(key_path[: i + 1])}",
            )
        return (
            False,
            None,
            f"cannot traverse into non-mapping at path "
            f"{'.'.join(key_path[:i] or ['<root>'])}",
        )
    return True, current, None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def query_file(
    path: str,
    key_path: list[str] | None = None,
    *,
    project_root: Path | None = None,
) -> dict:
    """Read a file and optionally traverse a parsed structure.

    Arguments:
        path: filesystem path (absolute or relative to project_root).
        key_path: optional dotted traversal applied AFTER parsing for
                  YAML/JSON. Ignored for markdown / text files.
        project_root: when ``path`` is relative, joined under this.

    NOTE: ``path`` is the substrate identifier. The caller (the
    grounding subagent) is required to pass only the pointer_value
    here, never the anchored_claim or any draft prose. This signature
    cannot accept draft text because it has no parameter for it.
    """
    if not path:
        return _err("empty path")
    resolved = Path(path)
    if not resolved.is_absolute() and project_root is not None:
        resolved = project_root / path
    if not resolved.exists():
        return _err(f"file does not exist: {resolved}", str(resolved))
    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return _err(f"file is not UTF-8: {exc}", str(resolved))

    if key_path is None:
        return _ok(text, str(resolved))

    # Parse based on extension.
    suffix = resolved.suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            parsed = yaml.safe_load(text)
        elif suffix == ".json":
            parsed = json.loads(text)
        else:
            return _err(
                f"key_path traversal requires .yaml/.yml/.json; got {suffix}",
                str(resolved),
            )
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        return _err(f"parse error: {exc}", str(resolved))

    found, value, traversal_err = _traverse(parsed, key_path)
    if not found:
        return _err(traversal_err or "key not found", str(resolved))
    return _ok(value, str(resolved))


# state.yaml dotted-path syntax: the FIRST two segments (e.g.
# "state.yaml") are the filename; the remainder is the key path.
_STATE_YAML_FILENAME_SEGMENTS = 2


def query_state_yaml(dotted_path: str, project_root: Path) -> dict:
    """Resolve a ``state.yaml.experiments.X.field`` style pointer.

    Split the dotted path: the first two segments form the filename
    (``state.yaml``), and the rest is the key traversal inside the
    parsed mapping. Returns the resolved value or an informative error.

    Argument shape: dotted_path is the substrate identifier (i.e. the
    pointer_value emitted by discovery). project_root locates the file.
    No anchored_claim or draft text is accepted.
    """
    if not dotted_path:
        return _err("empty dotted_path")
    parts = dotted_path.split(".")
    if len(parts) < _STATE_YAML_FILENAME_SEGMENTS:
        return _err(
            f"dotted_path too short: expected state.yaml.<keys>, got "
            f"{dotted_path!r}"
        )
    filename = ".".join(parts[:_STATE_YAML_FILENAME_SEGMENTS])
    key_path = parts[_STATE_YAML_FILENAME_SEGMENTS:]
    return query_file(filename, key_path=key_path, project_root=project_root)


def query_vault_finding(slug: str, project_root: Path) -> dict:
    """Resolve a wikilink-style slug to a vault file.

    Tries, in order:
        vault/knowledge/<slug>.md
        experiments/<slug>.md
    Returns the file contents (the lens reads the markdown body).
    """
    if not slug:
        return _err("empty slug")
    candidates = [
        project_root / "vault" / "knowledge" / f"{slug}.md",
        project_root / "experiments" / f"{slug}.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                return _err(
                    f"file is not UTF-8: {exc}",
                    str(candidate),
                )
            return _ok(text, str(candidate))
    return _err(
        f"slug {slug!r} not found at vault/knowledge/ or experiments/",
        str(candidates[0]),
    )


_AMPLITUDE_STUB_ERROR = (
    "amplitude MCP not invocable from helper; orchestrator-level Agent "
    "call needed for real chart data"
)


def query_amplitude_chart(chart_id: str) -> dict:
    """Amplitude chart fetch — STUB.

    Returns an error record explaining that the MCP isn't reachable
    from this pure helper. The grounding subagent treats this as an
    advisory finding (substrate-unresolvable with the stub error
    message) rather than crashing.

    The eventual non-stub version will dispatch through the
    orchestrator's Agent layer to Amplitude. Until then, the stub
    response is the documented behaviour.
    """
    if not chart_id:
        return _err("empty chart_id")
    return _err(_AMPLITUDE_STUB_ERROR, chart_id)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


# Allowlist of fields the dispatcher reads off a pointer record. This is
# the explicit ENUMERATION of what flows into substrate-query helpers —
# any field outside this set (notably ``source_text_excerpt`` and
# ``anchored_claim``) is never touched. Used by the convergent-blind-spot
# guard test.
_ALLOWED_POINTER_FIELDS = frozenset({"pointer_type", "pointer_value"})


def query_pointer(pointer: dict, project_root: Path) -> dict:
    """Route a pointer record to the right substrate helper.

    Reads ONLY ``pointer_type`` and ``pointer_value`` off the record.
    Everything else (notably ``source_text_excerpt`` and
    ``anchored_claim``) is ignored — that is the convergent-blind-spot
    isolation property.
    """
    pointer_type = pointer.get("pointer_type")
    pointer_value = pointer.get("pointer_value")
    if not pointer_type:
        return _err("pointer missing pointer_type")
    if not pointer_value:
        return _err("pointer missing pointer_value")

    if pointer_type == "file_path":
        return query_file(pointer_value, project_root=project_root)
    if pointer_type == "state_yaml_dotted":
        return query_state_yaml(pointer_value, project_root=project_root)
    if pointer_type == "wikilink":
        return query_vault_finding(pointer_value, project_root=project_root)
    if pointer_type == "chart_id":
        return query_amplitude_chart(pointer_value)
    return _err(f"unknown pointer_type: {pointer_type!r}")
