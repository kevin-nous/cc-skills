"""ccp-001 scaffold validation.

Asserts:
1. SKILL.md exists at the expected path and contains the required sections
   (purpose, invocation, read-only contract, criteria resolution rule).
2. The ~/.claude/skills/cc:proofread symlink resolves to the cc-skills directory.
3. The cc-skills README.md skills table includes a cc:proofread row.

Run from anywhere:
    python3 ~/cc-skills/cc:proofread/scripts/tests/test_scaffold.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD = SKILL_ROOT / "SKILL.md"
SYMLINK = Path(os.path.expanduser("~/.claude/skills/cc:proofread"))
README = Path(os.path.expanduser("~/cc-skills/README.md"))

# Load-bearing content fragments. Match by substring (case-sensitive). If any
# of these regresses out of SKILL.md the test fails — these are the markers
# called out as "must not drop" in the wave-1 brief.
REQUIRED_SKILL_FRAGMENTS = [
    # Purpose-paragraph signal: cc:proofread is multi-lens, manual.
    "Multi-lens",
    # Invocation syntax must appear.
    "/cc:proofread",
    # Read-only contract is load-bearing — explicit phrases required.
    "Auditors NEVER write to the artifact",
    "Auditors NEVER write to any shared file",
    "proofread-passed-",
    "/tmp/cc-proofread-",
    # Convergent-blind-spot countermeasure — exactly one sentence in PRD spec.
    "re-query substrates independently",
    # Criteria resolution rule (project override → default fallback).
    ".claude/cc:proofread/criteria.md",
    "default-criteria.md",
    # Pointer to issue list for build status.
    "docs/issues/ccp-",
]

REQUIRED_README_FRAGMENTS = [
    "cc:proofread",
]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def check_skill_md() -> None:
    if not SKILL_MD.exists():
        fail(f"SKILL.md does not exist at {SKILL_MD}")
    text = SKILL_MD.read_text(encoding="utf-8")
    missing = [frag for frag in REQUIRED_SKILL_FRAGMENTS if frag not in text]
    if missing:
        fail(f"SKILL.md missing required fragments: {missing}")


def check_symlink() -> None:
    if not SYMLINK.exists():
        fail(f"symlink does not exist at {SYMLINK}")
    if not SYMLINK.is_symlink():
        fail(f"{SYMLINK} exists but is not a symlink")
    resolved = SYMLINK.resolve()
    if resolved != SKILL_ROOT.resolve():
        fail(f"symlink resolves to {resolved}, expected {SKILL_ROOT.resolve()}")
    # Sanity: SKILL.md reachable through the symlink.
    if not (SYMLINK / "SKILL.md").exists():
        fail("symlink resolves but SKILL.md is not reachable through it")


def check_readme_table() -> None:
    if not README.exists():
        fail(f"cc-skills README.md does not exist at {README}")
    text = README.read_text(encoding="utf-8")
    missing = [frag for frag in REQUIRED_README_FRAGMENTS if frag not in text]
    if missing:
        fail(f"README.md missing required fragments: {missing}")
    # Soft check: cc:proofread should appear in a markdown table row, not just
    # an offhand mention. Look for a pipe-delimited line containing it.
    table_rows = [
        line for line in text.splitlines()
        if line.startswith("|") and "cc:proofread" in line
    ]
    if not table_rows:
        fail("README.md mentions cc:proofread but not in a markdown table row")


def main() -> None:
    check_skill_md()
    check_symlink()
    check_readme_table()
    print("PASS: ccp-001 scaffold checks (SKILL.md, symlink, README table)")


if __name__ == "__main__":
    main()
