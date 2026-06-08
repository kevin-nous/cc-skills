"""Validate cc:proofread finding/coverage/run_record JSONL records against
the canonical schema at schemas/finding.schema.json.

Library use:
    from scripts.validate_finding import validate_finding
    validate_finding(record_dict)   # raises ValidationError on failure

CLI use:
    python3 -m scripts.validate_finding <jsonl-file> [<jsonl-file> ...]

The CLI reads each file line by line, validates every record, and prints a
per-file summary. Exit code 0 iff every record validates.

Why this lives at scripts/validate_finding.py:
    Subsequent issues (ccp-004 mechanical prepass, ccp-008 orchestrator,
    ccp-009/010 lenses) all emit records into /tmp/cc-proofread-<run-id>/.
    This is the shared validation entry point so each writer can assert
    its own output before handing back to the orchestrator. The
    file-path-based driver style mirrors the cube subagent pattern
    (vault/knowledge/2026-05-08-mcp-fanout-context-bounds.md).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterable

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "jsonschema is required (pip install jsonschema)"
    ) from exc


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "finding.schema.json"
)


_validator_cache: Draft202012Validator | None = None


def _validator() -> Draft202012Validator:
    """Lazily load and cache the schema validator."""
    global _validator_cache
    if _validator_cache is None:
        with SCHEMA_PATH.open("r", encoding="utf-8") as fh:
            schema = json.load(fh)
        # Fail loudly if the schema itself is malformed.
        Draft202012Validator.check_schema(schema)
        _validator_cache = Draft202012Validator(schema)
    return _validator_cache


def validate_finding(record: dict) -> None:
    """Validate a single record against the canonical schema.

    Raises jsonschema.ValidationError on failure with the standard
    `path` / `message` attributes — callers can render that however
    they like.
    """
    _validator().validate(record)


def iter_jsonl(path: Path) -> Iterable[tuple[int, dict]]:
    """Yield (1-indexed line number, parsed record) tuples. Blank lines
    are skipped silently."""
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                yield lineno, json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{lineno}: invalid JSON ({exc.msg})"
                ) from exc


def validate_file(path: Path) -> tuple[int, list[str]]:
    """Validate every record in a JSONL file. Returns (records_ok, errors)."""
    validator = _validator()
    ok = 0
    errors: list[str] = []
    try:
        for lineno, record in iter_jsonl(path):
            issues = list(validator.iter_errors(record))
            if issues:
                for issue in issues:
                    loc = ".".join(str(p) for p in issue.absolute_path) or "<root>"
                    errors.append(
                        f"{path}:{lineno}: {loc}: {issue.message}"
                    )
            else:
                ok += 1
    except ValueError as exc:
        errors.append(str(exc))
    return ok, errors


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: python3 -m scripts.validate_finding <jsonl-file> "
            "[<jsonl-file> ...]",
            file=sys.stderr,
        )
        return 2

    total_ok = 0
    total_errors: list[str] = []
    for arg in argv[1:]:
        path = Path(arg)
        if not path.exists():
            total_errors.append(f"{path}: file not found")
            continue
        ok, errors = validate_file(path)
        total_ok += ok
        total_errors.extend(errors)
        if errors:
            print(f"{path}: {ok} ok, {len(errors)} invalid")
            for err in errors:
                print(f"  {err}")
        else:
            print(f"{path}: {ok} ok")

    if total_errors:
        print(
            f"\nSUMMARY: {total_ok} valid, {len(total_errors)} invalid",
            file=sys.stderr,
        )
        return 1
    print(f"\nSUMMARY: {total_ok} valid records, all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
