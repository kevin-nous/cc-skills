"""ccp-003 finding schema validation.

Asserts:
1. schemas/finding.schema.json parses as a valid JSON Schema draft 2020-12.
2. All example records under schemas/examples/ validate against the schema.
3. The schema discriminates the three record_types correctly via the
   `record_type` field (finding | coverage | run_record).
4. Constructed invalid records fail validation with informative errors:
     a. Finding missing a required field (claim).
     b. Coverage with an extra/typo'd field (additionalProperties: false).
     c. RunRecord with a bad enum value (mode: "shallow").
     d. Finding with severity outside the enum.
5. The V2 "May 12 parity" example finding contains the canonical anchor —
   substrate_field points at state.yaml, lens is grounding, severity is
   blocking, check_id is contradicting-data-not-addressed.

Run from anywhere:
    python3 ~/cc-skills/cc:proofread/scripts/tests/test_finding_schema.py
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, ValidationError
except ModuleNotFoundError:
    print("FAIL: jsonschema package not installed. pip install jsonschema",
          file=sys.stderr)
    sys.exit(1)

SKILL_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = SKILL_ROOT / "schemas" / "finding.schema.json"
EXAMPLES_DIR = SKILL_ROOT / "schemas" / "examples"

EXPECTED_EXAMPLES = [
    "example-grounding-finding.json",
    "example-scope-finding.json",
    "example-mechanical-finding.json",
    "example-coverage-scope.json",
    "example-coverage-grounding.json",
    "example-run-record.json",
]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_schema() -> dict:
    if not SCHEMA_PATH.exists():
        fail(f"schema not found at {SCHEMA_PATH}")
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"schema is not valid JSON: {exc}")


def check_schema_parses_as_draft_2020_12(schema: dict) -> None:
    # Schema declaration must point at draft 2020-12.
    declared = schema.get("$schema", "")
    if "2020-12" not in declared:
        fail(f"schema $schema must be draft 2020-12; got {declared!r}")
    # Validator constructs successfully → schema is structurally valid.
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        fail(f"schema fails Draft202012Validator.check_schema: {exc}")


def check_examples_exist_and_validate(schema: dict) -> None:
    validator = Draft202012Validator(schema)
    for name in EXPECTED_EXAMPLES:
        path = EXAMPLES_DIR / name
        if not path.exists():
            fail(f"required example missing: {path}")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"example {name} is not valid JSON: {exc}")
        errors = sorted(validator.iter_errors(record), key=lambda e: e.path)
        if errors:
            msgs = [f"{list(e.path)}: {e.message}" for e in errors]
            fail(f"example {name} fails schema validation: {msgs}")


def check_discriminator_works(schema: dict) -> None:
    validator = Draft202012Validator(schema)
    # A finding-shaped record claiming to be a coverage record should fail —
    # discriminator must fan out into the right $def.
    fake = {
        "record_type": "coverage",
        "lens": "grounding",
        "check_id": "contradicting-data-not-addressed",
        "severity": "blocking",
        "claim": "x",
        "location": {"line_range_start": 1, "line_range_end": 2},
        "finding": "y",
        "remediation": "z",
    }
    errors = list(validator.iter_errors(fake))
    if not errors:
        fail("schema accepted a finding-shaped record with record_type=coverage")


def check_invalid_records_rejected(schema: dict, examples: dict) -> None:
    validator = Draft202012Validator(schema)

    # (a) Finding missing required field `claim`.
    bad = copy.deepcopy(examples["example-grounding-finding.json"])
    del bad["claim"]
    if not list(validator.iter_errors(bad)):
        fail("schema accepted a Finding missing the required `claim` field")

    # (b) Coverage with an extra field (additionalProperties: false).
    bad = copy.deepcopy(examples["example-coverage-scope.json"])
    bad["bogus_field"] = "should_be_rejected"
    if not list(validator.iter_errors(bad)):
        fail("schema accepted a Coverage record with an extra field "
             "(additionalProperties: false is not enforced)")

    # (c) RunRecord with bad mode enum value.
    bad = copy.deepcopy(examples["example-run-record.json"])
    bad["mode"] = "shallow"
    if not list(validator.iter_errors(bad)):
        fail("schema accepted a RunRecord with mode='shallow' "
             "(should be default|deep only)")

    # (d) Finding with severity outside enum.
    bad = copy.deepcopy(examples["example-grounding-finding.json"])
    bad["severity"] = "critical"
    if not list(validator.iter_errors(bad)):
        fail("schema accepted a Finding with severity='critical' "
             "(should be blocking|advisory only)")


def check_v2_may12_parity_example(examples: dict) -> None:
    """The grounding example is the canonical anchor for the whole build —
    it must encode the May 12 parity case in a way the orchestrator can use.
    """
    grounding = examples["example-grounding-finding.json"]
    if grounding.get("record_type") != "finding":
        fail("grounding example record_type is not 'finding'")
    if grounding.get("lens") != "grounding":
        fail("grounding example lens is not 'grounding'")
    if grounding.get("severity") != "blocking":
        fail("grounding example severity is not 'blocking'")
    if grounding.get("check_id") != "contradicting-data-not-addressed":
        fail("grounding example check_id should be "
             "'contradicting-data-not-addressed'")
    evidence = grounding.get("evidence") or {}
    if "state.yaml" not in evidence.get("substrate_field", ""):
        fail("grounding example evidence.substrate_field must point at "
             "state.yaml (the May 12 parity anchor)")


def main() -> None:
    schema = load_schema()
    check_schema_parses_as_draft_2020_12(schema)
    check_examples_exist_and_validate(schema)
    check_discriminator_works(schema)

    examples = {}
    for name in EXPECTED_EXAMPLES:
        examples[name] = json.loads(
            (EXAMPLES_DIR / name).read_text(encoding="utf-8"))

    check_invalid_records_rejected(schema, examples)
    check_v2_may12_parity_example(examples)
    print("PASS: ccp-003 finding schema (parse + examples + discriminator + "
          "invalid rejection + V2 May 12 parity anchor)")


if __name__ == "__main__":
    main()
