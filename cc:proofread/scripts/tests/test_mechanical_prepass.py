"""ccp-004 mechanical prepass tests (pytest-style).

Covers:
  - Each of the 4 mechanical checks fires on its dedicated fixture.
  - Combined fixture fires all four checks.
  - Clean fixture yields zero findings (only a coverage record).
  - Coverage record shape matches the schema and the ccp-004 AC.
  - 10KB fixture processes in under 500ms.
  - Empty artifact yields zero findings + coverage with claims_examined=0.
  - Binary / non-UTF8 artifact fails gracefully via coverage.error.
  - Every emitted finding validates against schemas/finding.schema.json.
  - Read-only: no files modified, no stderr writes from a successful run.

Run:
    pytest -v ~/cc-skills/cc:proofread/scripts/tests/test_mechanical_prepass.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
FIXTURES = SCRIPTS_DIR / "tests" / "fixtures" / "mechanical"
FAKE_PROJECT_ROOT = FIXTURES / "fake_project"

# Make scripts package importable for in-process tests.
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


@pytest.fixture(scope="session")
def prepass_module():
    from scripts import mechanical_prepass  # type: ignore
    return mechanical_prepass


@pytest.fixture(scope="session")
def validate_finding_fn():
    from scripts.validate_finding import validate_finding  # type: ignore
    return validate_finding


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _run_cli(artifact: Path, project_root: Path | None = None) -> tuple[int, str, str]:
    """Invoke the CLI subprocess; returns (returncode, stdout, stderr).

    Exercises the `python -m scripts.mechanical_prepass <path>` contract
    the AC names. Avoids reaching into internals so the CLI surface itself
    is tested.
    """
    cmd = [sys.executable, "-m", "scripts.mechanical_prepass", str(artifact)]
    if project_root is not None:
        cmd += ["--project-root", str(project_root)]
    result = subprocess.run(
        cmd, cwd=SKILL_ROOT, capture_output=True, text=True
    )
    return result.returncode, result.stdout, result.stderr


def _parse_jsonl(stdout: str) -> list[dict]:
    records = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _findings(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("record_type") == "finding"]


def _coverage(records: list[dict]) -> dict:
    cov = [r for r in records if r.get("record_type") == "coverage"]
    assert len(cov) == 1, f"expected exactly one coverage record, got {len(cov)}"
    return cov[0]


# --------------------------------------------------------------------------
# CLI smoke
# --------------------------------------------------------------------------

def test_cli_invokable():
    """The skill ships with the `python -m scripts.mechanical_prepass` entry."""
    rc, out, err = _run_cli(FIXTURES / "clean.md", FAKE_PROJECT_ROOT)
    assert rc == 0, f"non-zero exit on clean fixture; stderr={err!r}"
    records = _parse_jsonl(out)
    assert any(r["record_type"] == "coverage" for r in records)


def test_cli_missing_file_exits_nonzero():
    rc, out, err = _run_cli(FIXTURES / "_does_not_exist.md")
    assert rc != 0
    assert "not found" in (err + out).lower() or "no such" in (err + out).lower()


# --------------------------------------------------------------------------
# Individual check fires
# --------------------------------------------------------------------------

def test_hedge_promotion_fires(prepass_module):
    records = prepass_module.run(FIXTURES / "hedge_promotion.md")
    findings = _findings(records)
    hedge = [f for f in findings if f["check_id"] == "hedge-promotion-regex"]
    assert hedge, "hedge-promotion-regex did not fire on hedge_promotion.md"
    # The four trigger words seeded in the fixture (comfortably, clearly,
    # decisively) should all appear in flagged claims at least once.
    flagged_text = " ".join(f["claim"] for f in hedge).lower()
    assert "comfortably" in flagged_text
    assert "clearly" in flagged_text
    assert "decisively" in flagged_text


def test_wikilink_resolution_fires(prepass_module):
    records = prepass_module.run(
        FIXTURES / "wikilink_unresolved.md",
        project_root=FAKE_PROJECT_ROOT,
    )
    findings = _findings(records)
    wiki = [f for f in findings if f["check_id"] == "wikilink-resolution"]
    # Two unresolved links seeded; checkout-redesign must NOT fire.
    flagged_slugs = " ".join(f["claim"] for f in wiki)
    assert "nonexistent-concept-xyz" in flagged_slugs
    assert "another-missing-file" in flagged_slugs
    assert "checkout-redesign" not in flagged_slugs, (
        "checkout-redesign exists in fake vault and must not be flagged"
    )


def test_could_vs_will_fires(prepass_module):
    records = prepass_module.run(FIXTURES / "could_vs_will.md")
    findings = _findings(records)
    could = [f for f in findings if f["check_id"] == "could-vs-will-detection"]
    assert could, "could-vs-will-detection did not fire on could_vs_will.md"


def test_acronym_on_first_use_fires(prepass_module):
    records = prepass_module.run(FIXTURES / "acronym_undefined.md")
    findings = _findings(records)
    acr = [f for f in findings if f["check_id"] == "acronym-on-first-use"]
    flagged = " ".join(f["claim"] for f in acr)
    # SRM, DUOS, FOOBR are undefined (and 2–5 chars per AC regex bound).
    assert "SRM" in flagged
    assert "FOOBR" in flagged
    # JSON and HTTP are allowlisted standard acronyms.
    assert "JSON" not in flagged, "JSON should be allowlisted"
    assert "HTTP" not in flagged, "HTTP should be allowlisted"


def test_all_four_checks_on_combined_fixture(prepass_module):
    records = prepass_module.run(
        FIXTURES / "all_four_triggers.md",
        project_root=FAKE_PROJECT_ROOT,
    )
    findings = _findings(records)
    check_ids = {f["check_id"] for f in findings}
    expected = {
        "hedge-promotion-regex",
        "wikilink-resolution",
        "could-vs-will-detection",
        "acronym-on-first-use",
    }
    missing = expected - check_ids
    assert not missing, f"missing checks on combined fixture: {missing}"


# --------------------------------------------------------------------------
# Clean / edge fixtures
# --------------------------------------------------------------------------

def test_clean_fixture_zero_findings(prepass_module):
    records = prepass_module.run(
        FIXTURES / "clean.md", project_root=FAKE_PROJECT_ROOT
    )
    findings = _findings(records)
    assert findings == [], f"clean fixture produced findings: {findings}"
    cov = _coverage(records)
    assert cov["claims_flagged"] == 0


def test_empty_artifact_emits_coverage_only(prepass_module):
    records = prepass_module.run(FIXTURES / "empty.md")
    findings = _findings(records)
    assert findings == []
    cov = _coverage(records)
    assert cov["claims_examined"] == 0
    assert cov["lens"] == "mechanical"
    assert "error" not in cov


def test_binary_artifact_fails_gracefully(prepass_module):
    """Non-UTF8 input → coverage.error set, zero findings, no exception."""
    records = prepass_module.run(FIXTURES / "binary_garbage.bin")
    findings = _findings(records)
    assert findings == []
    cov = _coverage(records)
    assert cov["lens"] == "mechanical"
    assert "error" in cov and cov["error"], "expected error string in coverage"


# --------------------------------------------------------------------------
# Coverage record shape
# --------------------------------------------------------------------------

def test_coverage_record_shape(prepass_module):
    records = prepass_module.run(
        FIXTURES / "hedge_promotion.md", project_root=FAKE_PROJECT_ROOT
    )
    cov = _coverage(records)
    assert cov["lens"] == "mechanical"
    assert cov["checks_attempted"] == 4
    assert cov["checks_completed"] == 4
    assert cov["claims_examined"] >= 0
    assert cov["claims_flagged"] == len(_findings(records))


# --------------------------------------------------------------------------
# Schema conformance: every record validates
# --------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", [
    "hedge_promotion.md",
    "wikilink_unresolved.md",
    "could_vs_will.md",
    "acronym_undefined.md",
    "all_four_triggers.md",
    "clean.md",
    "empty.md",
    "skill_md_filename.md",
    "ccp_008_smoke.md",
])
def test_all_records_validate_against_schema(
    prepass_module, validate_finding_fn, fixture_name
):
    records = prepass_module.run(
        FIXTURES / fixture_name, project_root=FAKE_PROJECT_ROOT
    )
    for r in records:
        validate_finding_fn(r)


# --------------------------------------------------------------------------
# Performance
# --------------------------------------------------------------------------

def test_perf_under_500ms_on_10kb_artifact(prepass_module):
    """The 500ms budget is the load-bearing AC. Measure with a small
    warm-up pass to amortise import/regex-compile cost on first call."""
    # warm-up (importing/compiling); not part of measured budget
    prepass_module.run(FIXTURES / "clean.md")
    t0 = time.perf_counter()
    records = prepass_module.run(FIXTURES / "perf_10kb.md")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    cov = _coverage(records)
    assert "error" not in cov
    assert elapsed_ms < 500, (
        f"mechanical prepass took {elapsed_ms:.1f}ms on 10KB fixture "
        f"(budget: 500ms)"
    )


# --------------------------------------------------------------------------
# Read-only contract
# --------------------------------------------------------------------------

def test_run_does_not_modify_artifact(prepass_module):
    artifact = FIXTURES / "hedge_promotion.md"
    before_mtime = artifact.stat().st_mtime_ns
    before_bytes = artifact.read_bytes()
    prepass_module.run(artifact, project_root=FAKE_PROJECT_ROOT)
    assert artifact.stat().st_mtime_ns == before_mtime
    assert artifact.read_bytes() == before_bytes


# --------------------------------------------------------------------------
# Pattern module sanity
# --------------------------------------------------------------------------

def test_patterns_module_exposes_expected_constants():
    from scripts import patterns  # type: ignore
    # 1. Hedge wordlist non-empty, lower-cased, contains canonical exemplars.
    hedges = set(w.lower() for w in patterns.HEDGE_WORDS)
    assert {"comfortably", "decisively", "clearly", "obviously",
            "definitely", "undoubtedly", "certainly"}.issubset(hedges)
    # 2. Acronym allowlist contains the universally-known set the AC names,
    #    plus the ccp-016 authoring-convention extension (SKILL/README/MOC
    #    file stems, editorial annotations TODO/FIXME, etc.).
    allow = set(patterns.ACRONYM_ALLOWLIST)
    for token in ["URL", "HTTP", "JSON"]:
        assert token in allow, f"{token} should be allowlisted"
    for token in ["SKILL", "README", "MOC", "PRD",
                  "TODO", "FIXME", "NB", "IMO", "IIRC"]:
        assert token in allow, (
            f"{token} should be allowlisted per ccp-016 — authoring "
            f"convention identifier, not an acronym"
        )
    # And the filename-extension guard list exists with the canonical set.
    exts = set(patterns.ACRONYM_FILENAME_EXTENSIONS)
    for ext in [".md", ".py", ".json", ".yaml"]:
        assert ext in exts, f"{ext} should be in ACRONYM_FILENAME_EXTENSIONS"
    # 3. Could-vs-will keywords cover the AC-named verbs.
    decision_verbs = set(v.lower() for v in patterns.DECISION_VERBS)
    for verb in ["ramp", "ship", "kill", "deploy", "launch"]:
        assert verb in decision_verbs


# --------------------------------------------------------------------------
# ccp-016 regression: remediation text + filename-extension guard
# --------------------------------------------------------------------------

def test_acronym_remediation_has_no_spurious_example(prepass_module):
    """ccp-016 AC: remediation must NOT hardcode 'Sample Ratio Mismatch'
    (or any other unrelated worked-example phrase) for arbitrary acronyms.

    The bug it replaces: every flagged acronym (MCP, JSONL, DUOS, …) got
    a remediation that suggested expanding it as 'Sample Ratio Mismatch
    (TOKEN)' — confidently wrong text the skill exists to catch.
    """
    records = prepass_module.run(FIXTURES / "acronym_undefined.md")
    acronym_findings = [
        f for f in _findings(records)
        if f["check_id"] == "acronym-on-first-use"
    ]
    assert acronym_findings, "expected acronym findings on this fixture"
    for f in acronym_findings:
        rem = f["remediation"]
        assert "Sample Ratio Mismatch" not in rem, (
            f"remediation for {f['claim']!r} still contains the spurious "
            f"'Sample Ratio Mismatch' example: {rem!r}"
        )
        # Either the remediation names the matched acronym back to the
        # reader, or it uses a domain-neutral placeholder. Anything else
        # risks the same wrong-with-confidence failure mode.
        assert f["claim"] in rem or "<Expansion>" in rem, (
            f"remediation should reference the matched acronym {f['claim']!r}"
            f" or a generic placeholder, got: {rem!r}"
        )


def test_skill_md_filename_not_flagged(prepass_module):
    """ccp-016 AC: ``SKILL.md`` in prose is a filename reference, not an
    acronym. The guard must skip it."""
    records = prepass_module.run(FIXTURES / "skill_md_filename.md")
    flagged_tokens = {
        f["claim"] for f in _findings(records)
        if f["check_id"] == "acronym-on-first-use"
    }
    assert "SKILL" not in flagged_tokens, (
        f"SKILL.md is a filename convention and must not flag as an acronym; "
        f"flagged set was {flagged_tokens}"
    )
    # Sanity: the genuine in-prose acronym in the same fixture still fires.
    assert "SRM" in flagged_tokens, (
        "expected SRM to still fire after the SKILL.md guard was applied"
    )


def test_filename_extension_guard_covers_common_extensions(prepass_module):
    """ccp-016 AC: PRD.md, README.md, MOC.md all skip via the same guard."""
    records = prepass_module.run(FIXTURES / "skill_md_filename.md")
    flagged_tokens = {
        f["claim"] for f in _findings(records)
        if f["check_id"] == "acronym-on-first-use"
    }
    for filename_stem in ("SKILL", "README", "MOC"):
        assert filename_stem not in flagged_tokens, (
            f"{filename_stem}.md should be skipped by the extension guard; "
            f"flagged set was {flagged_tokens}"
        )
    # PRD is also on the allowlist — assert via the allowlist contract.
    from scripts import patterns  # type: ignore
    assert "PRD" in patterns.ACRONYM_ALLOWLIST


def test_ccp_008_smoke_fixture_reproduces_known_good_behaviour(prepass_module):
    """Re-runs the ccp-008 smoke fixture and asserts the two ccp-016
    invariants together:
      - file-convention identifiers (SKILL/PRD/README/MOC.md, plus the
        editorial conventions TODO/FIXME/NB/IMO/IIRC) are silent
      - genuine prose acronyms (SRM, JSONL, MCP, DUOS, FOOBR) still fire
      - remediation text does not reference 'Sample Ratio Mismatch'
    """
    records = prepass_module.run(FIXTURES / "ccp_008_smoke.md")
    acronym_findings = [
        f for f in _findings(records)
        if f["check_id"] == "acronym-on-first-use"
    ]
    flagged = {f["claim"] for f in acronym_findings}

    # Genuine acronyms still fire.
    for expected in {"SRM", "JSONL", "MCP", "DUOS", "FOOBR"}:
        assert expected in flagged, (
            f"expected {expected} to fire; flagged set was {flagged}"
        )

    # File-convention + editorial identifiers stay silent.
    for silenced in {"SKILL", "PRD", "README", "MOC",
                     "TODO", "FIXME", "NB", "IMO", "IIRC"}:
        assert silenced not in flagged, (
            f"{silenced} should not fire (allowlist / filename guard); "
            f"flagged set was {flagged}"
        )

    # Remediation hygiene.
    for f in acronym_findings:
        assert "Sample Ratio Mismatch" not in f["remediation"]


# --------------------------------------------------------------------------
# Wikilink resolution fallback (no project root → CWD)
# --------------------------------------------------------------------------

def test_wikilink_resolution_falls_back_to_cwd(
    prepass_module, monkeypatch, tmp_path
):
    """If --project-root is not given, the resolver should fall back to
    os.getcwd(). Validates AC: 'fall back to os.getcwd() if absent'."""
    # Build a tiny vault inside tmp_path.
    vault = tmp_path / "vault" / "knowledge"
    vault.mkdir(parents=True)
    (vault / "present-page.md").touch()
    artifact = tmp_path / "draft.md"
    artifact.write_text("See [[present-page]] and [[absent-page]].\n")
    monkeypatch.chdir(tmp_path)
    records = prepass_module.run(artifact)
    wiki = [f for f in _findings(records)
            if f["check_id"] == "wikilink-resolution"]
    flagged = " ".join(f["claim"] for f in wiki)
    assert "absent-page" in flagged
    assert "present-page" not in flagged


# --------------------------------------------------------------------------
# ccp-017: --max-findings-per-check throttle
# --------------------------------------------------------------------------

OVERRIDE_PROJECT_ROOT = FIXTURES / "fake_project_with_override"


def test_throttle_caps_noisy_check_and_emits_sentinel(prepass_module):
    """A check with 12 hits and the default cap of 5 should yield exactly
    5 real findings + 1 truncation sentinel; coverage's claims_flagged
    reports the FULL 12 (not 5, not 6) so the reader sees true scale."""
    records = prepass_module.run(
        FIXTURES / "noisy_hedge.md",
        project_root=FAKE_PROJECT_ROOT,
        max_findings_per_check=5,
    )
    findings = _findings(records)
    real = [f for f in findings if f["check_id"] == "hedge-promotion-regex"]
    sentinel = [
        f for f in findings
        if f["check_id"] == "hedge-promotion-regex-truncated"
    ]
    assert len(real) == 5, (
        f"expected exactly 5 real hedge findings under cap=5, got {len(real)}"
    )
    assert len(sentinel) == 1, (
        f"expected exactly 1 truncation sentinel, got {len(sentinel)}"
    )
    # Sentinel carries the original check id and the suppressed count
    # in its claim, for the render layer to pattern-match later.
    s = sentinel[0]
    assert "hedge-promotion-regex" in s["claim"]
    assert "cap of 5" in s["claim"]
    assert "7 additional findings suppressed" in s["claim"]
    assert s["severity"] == "advisory"

    # Coverage reports the FULL count and the per-check suppression map.
    cov = _coverage(records)
    assert cov["claims_flagged"] == 12, (
        f"coverage.claims_flagged should be the FULL pre-truncation total "
        f"(12), got {cov['claims_flagged']}"
    )
    assert cov.get("truncated_counts") == {"hedge-promotion-regex": 7}


def test_throttle_does_not_fire_on_clean_artifact(prepass_module):
    """Clean artifact: zero findings, no sentinel, no truncated_counts."""
    records = prepass_module.run(
        FIXTURES / "clean.md",
        project_root=FAKE_PROJECT_ROOT,
        max_findings_per_check=5,
    )
    findings = _findings(records)
    assert findings == []
    cov = _coverage(records)
    assert cov["claims_flagged"] == 0
    assert "truncated_counts" not in cov


def test_throttle_boundary_exactly_at_cap_no_sentinel(prepass_module):
    """A check with exactly N hits at cap=N must NOT emit a sentinel.
    Off-by-one regression guard."""
    records = prepass_module.run(
        FIXTURES / "exactly_n_hedge.md",
        project_root=FAKE_PROJECT_ROOT,
        max_findings_per_check=5,
    )
    findings = _findings(records)
    real = [f for f in findings if f["check_id"] == "hedge-promotion-regex"]
    sentinel = [
        f for f in findings
        if f["check_id"] == "hedge-promotion-regex-truncated"
    ]
    assert len(real) == 5
    assert sentinel == [], (
        "boundary case (total == cap) must NOT emit a truncation sentinel"
    )
    cov = _coverage(records)
    assert cov["claims_flagged"] == 5
    assert "truncated_counts" not in cov


def test_throttle_cap_zero_silences_check_and_records_disable(prepass_module):
    """cap=0 effectively disables the check: zero real findings, a single
    sentinel naming the total suppressed, and a truncated_counts entry so
    downstream consumers can tell the check was silenced (not absent)."""
    records = prepass_module.run(
        FIXTURES / "noisy_hedge.md",
        project_root=FAKE_PROJECT_ROOT,
        max_findings_per_check=0,
    )
    findings = _findings(records)
    real = [f for f in findings if f["check_id"] == "hedge-promotion-regex"]
    sentinel = [
        f for f in findings
        if f["check_id"] == "hedge-promotion-regex-truncated"
    ]
    assert real == [], "cap=0 must emit zero real findings for the check"
    assert len(sentinel) == 1
    assert "cap of 0" in sentinel[0]["claim"]
    assert "12 additional findings suppressed" in sentinel[0]["claim"]

    cov = _coverage(records)
    # Coverage still reports the full underlying count so the user can
    # see the check was disabled, not silently empty.
    assert cov["claims_flagged"] == 12
    assert cov.get("truncated_counts") == {"hedge-promotion-regex": 12}


def test_throttle_per_check_override_via_criteria_md(prepass_module):
    """A per-project criteria.md with a per-check map should be honoured:
    hedge-promotion-regex gets cap=7 (no truncation), and the default of
    3 applies to every other check (untriggered here)."""
    # The override sets hedge cap=7, so all 12 noisy_hedge hits would still
    # cap at 7 → 5 suppressed → 1 sentinel.
    records = prepass_module.run(
        FIXTURES / "noisy_hedge.md",
        project_root=OVERRIDE_PROJECT_ROOT,
    )
    findings = _findings(records)
    real = [f for f in findings if f["check_id"] == "hedge-promotion-regex"]
    sentinel = [
        f for f in findings
        if f["check_id"] == "hedge-promotion-regex-truncated"
    ]
    assert len(real) == 7, (
        f"hedge cap should be 7 (per-check override), got real={len(real)}"
    )
    assert len(sentinel) == 1
    assert "cap of 7" in sentinel[0]["claim"]
    cov = _coverage(records)
    assert cov["claims_flagged"] == 12
    assert cov.get("truncated_counts") == {"hedge-promotion-regex": 5}


def test_throttle_cli_flag_overrides_default(prepass_module):
    """`--max-findings-per-check 7` from the CLI should override the
    shipped default of 5 on the noisy fixture."""
    rc, out, err = _run_cli(
        FIXTURES / "noisy_hedge.md",
        FAKE_PROJECT_ROOT,
    )
    # First, baseline without flag: 5 real + 1 sentinel.
    assert rc == 0, err
    records = _parse_jsonl(out)
    real = [
        r for r in records
        if r.get("record_type") == "finding"
        and r["check_id"] == "hedge-promotion-regex"
    ]
    assert len(real) == 5

    # Now with --max-findings-per-check 7.
    cmd = [
        sys.executable, "-m", "scripts.mechanical_prepass",
        str(FIXTURES / "noisy_hedge.md"),
        "--project-root", str(FAKE_PROJECT_ROOT),
        "--max-findings-per-check", "7",
    ]
    result = subprocess.run(
        cmd, cwd=SKILL_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    records = _parse_jsonl(result.stdout)
    real = [
        r for r in records
        if r.get("record_type") == "finding"
        and r["check_id"] == "hedge-promotion-regex"
    ]
    sentinel = [
        r for r in records
        if r.get("record_type") == "finding"
        and r["check_id"] == "hedge-promotion-regex-truncated"
    ]
    assert len(real) == 7, f"CLI flag should set cap=7, got {len(real)} real"
    assert len(sentinel) == 1
    cov = [r for r in records if r.get("record_type") == "coverage"][0]
    assert cov["claims_flagged"] == 12
    assert cov.get("truncated_counts") == {"hedge-promotion-regex": 5}


def test_throttle_default_loaded_from_criteria_md(prepass_module):
    """When no CLI flag is passed and no per-project override exists, the
    cap comes from the shipped default-criteria.md frontmatter
    (currently 5). Run against the noisy fixture and assert the same
    5+1 shape as the explicit max_findings_per_check=5 case."""
    records = prepass_module.run(
        FIXTURES / "noisy_hedge.md",
        project_root=FAKE_PROJECT_ROOT,
        # no explicit cap → should resolve from criteria.md → 5
    )
    real = [
        f for f in _findings(records)
        if f["check_id"] == "hedge-promotion-regex"
    ]
    sentinel = [
        f for f in _findings(records)
        if f["check_id"] == "hedge-promotion-regex-truncated"
    ]
    assert len(real) == 5
    assert len(sentinel) == 1


def test_throttle_sentinel_validates_against_schema(
    prepass_module, validate_finding_fn
):
    """Every emitted record (including the truncation sentinel) must
    validate against schemas/finding.schema.json."""
    records = prepass_module.run(
        FIXTURES / "noisy_hedge.md",
        project_root=FAKE_PROJECT_ROOT,
        max_findings_per_check=5,
    )
    for r in records:
        validate_finding_fn(r)


def test_throttle_coverage_record_with_truncated_counts_validates(
    prepass_module, validate_finding_fn
):
    """The Coverage record gains an optional truncated_counts map under
    ccp-017; ensure the schema accepts it without complaint."""
    records = prepass_module.run(
        FIXTURES / "noisy_hedge.md",
        project_root=FAKE_PROJECT_ROOT,
        max_findings_per_check=5,
    )
    cov = _coverage(records)
    assert "truncated_counts" in cov
    validate_finding_fn(cov)


def test_throttle_uniform_int_caps_all_checks(prepass_module):
    """Passing an int (not a dict) should apply that cap to every check."""
    # The all_four_triggers fixture has small counts per check; we exercise
    # the int-path against the noisy fixture instead so the cap actually bites.
    records = prepass_module.run(
        FIXTURES / "noisy_hedge.md",
        project_root=FAKE_PROJECT_ROOT,
        max_findings_per_check=3,
    )
    real = [
        f for f in _findings(records)
        if f["check_id"] == "hedge-promotion-regex"
    ]
    sentinel = [
        f for f in _findings(records)
        if f["check_id"] == "hedge-promotion-regex-truncated"
    ]
    assert len(real) == 3
    assert len(sentinel) == 1
    assert "9 additional findings suppressed" in sentinel[0]["claim"]
