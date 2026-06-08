"""ccp-014 frontmatter writer tests.

Pins the eligibility gate, the tag-replacement semantics, the
atomic-write contract, and the idempotency property of
`write_proofread_tag`.

The atomic-write tests are load-bearing: cc:proofread's only artifact
write must not corrupt a vault finding on a failure path. Tests assert
both byte-equality and mtime preservation on the failure-path
short-circuits.

Run:
    pytest -v ~/cc-skills/cc:proofread/scripts/tests/test_frontmatter_writer.py
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from datetime import date
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

import frontmatter  # noqa: E402

from scripts.frontmatter_writer import (  # noqa: E402
    WriteResult,
    write_proofread_tag,
)

FIXTURES = SKILL_ROOT / "scripts" / "tests" / "fixtures" / "frontmatter_writer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stage(tmp_path: Path, *, name: str, subdir: str = "vault/knowledge") -> Path:
    """Copy a fixture into a vault-like tmp tree and return the path."""
    target_dir = tmp_path / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    shutil.copy(FIXTURES / name, target)
    return target


def _read_tags(path: Path) -> list:
    post = frontmatter.load(str(path))
    return list(post.metadata.get("tags") or [])


def _read_body(path: Path) -> str:
    return frontmatter.load(str(path)).content


# ---------------------------------------------------------------------------
# Happy-path: vault finding, no existing tags
# ---------------------------------------------------------------------------


def test_vault_finding_no_existing_tags_appends(tmp_path: Path) -> None:
    target = _stage(tmp_path, name="finding-no-tags.md")
    body_before = _read_body(target)

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "written"
    assert result.tag_added == "proofread-passed-2026-05-29"
    assert result.previous_tag is None
    assert _read_tags(target) == ["proofread-passed-2026-05-29"]
    # Body untouched.
    assert _read_body(target) == body_before


# ---------------------------------------------------------------------------
# Happy-path: vault finding with existing tags — preserve + append
# ---------------------------------------------------------------------------


def test_vault_finding_existing_tags_preserved(tmp_path: Path) -> None:
    target = _stage(tmp_path, name="finding-with-tags.md")

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "written"
    assert result.previous_tag is None
    assert _read_tags(target) == [
        "analysis-quality",
        "codex-review",
        "proofread-passed-2026-05-29",
    ]


# ---------------------------------------------------------------------------
# Tag replacement: stale proofread-passed-* entry is replaced not duplicated
# ---------------------------------------------------------------------------


def test_stale_proofread_tag_replaced(tmp_path: Path) -> None:
    target = _stage(tmp_path, name="finding-with-stale-tag.md")

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "written"
    assert result.tag_added == "proofread-passed-2026-05-29"
    assert result.previous_tag == "proofread-passed-2026-04-15"
    tags = _read_tags(target)
    # Exactly one proofread-passed-* entry, with the new date.
    proofread_tags = [t for t in tags if str(t).startswith("proofread-passed-")]
    assert proofread_tags == ["proofread-passed-2026-05-29"]
    # Sibling tags preserved.
    assert "analysis-quality" in tags
    assert "codex-review" in tags


def test_multiple_stale_proofread_tags_collapsed(tmp_path: Path) -> None:
    """Defensive: if someone hand-edited multiple proofread-passed-*
    entries into the array, the writer collapses them to one current
    tag rather than appending a third."""
    target = _stage(tmp_path, name="finding-multiple-stale.md")

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "written"
    # previous_tag captures the FIRST stale entry encountered.
    assert result.previous_tag == "proofread-passed-2026-04-15"
    tags = _read_tags(target)
    proofread_tags = [t for t in tags if str(t).startswith("proofread-passed-")]
    assert proofread_tags == ["proofread-passed-2026-05-29"]


# ---------------------------------------------------------------------------
# Idempotency: writing the same date twice is a no-op
# ---------------------------------------------------------------------------


def test_same_date_is_idempotent(tmp_path: Path) -> None:
    target = _stage(tmp_path, name="finding-no-tags.md")

    first = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )
    assert first.status == "written"
    first_mtime = target.stat().st_mtime_ns

    second = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )
    assert second.status == "written"
    # Same date counts as previous == new; no churn on the second call.
    assert second.tag_added == "proofread-passed-2026-05-29"
    assert second.previous_tag == "proofread-passed-2026-05-29"
    # mtime unchanged — we short-circuited rather than rewriting.
    assert target.stat().st_mtime_ns == first_mtime
    # And the on-disk tag list is exactly one entry.
    assert _read_tags(target) == ["proofread-passed-2026-05-29"]


# ---------------------------------------------------------------------------
# Gate: --no-tag opt-out
# ---------------------------------------------------------------------------


def test_no_tag_flag_skips_write(tmp_path: Path) -> None:
    target = _stage(tmp_path, name="finding-no-tags.md")
    before = target.read_bytes()

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[], no_tag=True
    )

    assert result.status == "skipped_no_tag"
    assert result.tag_added is None
    assert target.read_bytes() == before


# ---------------------------------------------------------------------------
# Gate: blocking findings present
# ---------------------------------------------------------------------------


def test_blocking_finding_skips_write(tmp_path: Path) -> None:
    target = _stage(tmp_path, name="finding-no-tags.md")
    before = target.read_bytes()

    findings = [
        {"severity": "advisory", "check_id": "foo"},
        {"severity": "blocking", "check_id": "bar"},
    ]
    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=findings
    )

    assert result.status == "skipped_blocking_findings_present"
    assert target.read_bytes() == before


def test_advisory_findings_do_not_block(tmp_path: Path) -> None:
    target = _stage(tmp_path, name="finding-no-tags.md")
    findings = [{"severity": "advisory", "check_id": "foo"}]

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=findings
    )

    assert result.status == "written"


# ---------------------------------------------------------------------------
# Gate: path discriminator (not a vault finding)
# ---------------------------------------------------------------------------


def test_prd_file_not_tagged(tmp_path: Path) -> None:
    """A `type: finding` frontmatter at a non-vault path is still
    skipped — path discrimination is the load-bearing gate."""
    target_dir = tmp_path / "docs" / "prds"
    target_dir.mkdir(parents=True)
    target = target_dir / "some-prd.md"
    shutil.copy(FIXTURES / "finding-no-tags.md", target)
    before = target.read_bytes()

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "skipped_not_vault_finding"
    assert target.read_bytes() == before


def test_legacy_findings_folder_accepted(tmp_path: Path) -> None:
    """Pre-Phase-2 layout (`findings/<slug>.md`) is also a vault
    finding for compat."""
    target = _stage(tmp_path, name="finding-no-tags.md", subdir="findings")

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "written"


def test_non_finding_type_in_vault_skipped(tmp_path: Path) -> None:
    """A file IN vault/knowledge/ with `type: solution` (not finding)
    is skipped — frontmatter type is the second load-bearing gate."""
    target = _stage(tmp_path, name="solution-in-vault.md")
    before = target.read_bytes()

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "skipped_not_vault_finding"
    assert target.read_bytes() == before


# ---------------------------------------------------------------------------
# Edge: malformed frontmatter
# ---------------------------------------------------------------------------


def test_malformed_yaml_does_not_corrupt(tmp_path: Path) -> None:
    target = _stage(tmp_path, name="malformed-yaml.md")
    before = target.read_bytes()
    before_mtime = target.stat().st_mtime_ns

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "skipped_artifact_unreadable"
    assert result.error  # carries the parse error
    # File on disk is byte-identical AND mtime unchanged.
    assert target.read_bytes() == before
    assert target.stat().st_mtime_ns == before_mtime


def test_unsupported_tags_shape_is_unreadable(tmp_path: Path) -> None:
    """`tags:` as a dict (not a list/string) is unsupported. We refuse
    rather than guess."""
    target = _stage(tmp_path, name="finding-dict-tags.md")
    before = target.read_bytes()

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "skipped_artifact_unreadable"
    assert target.read_bytes() == before


def test_missing_file(tmp_path: Path) -> None:
    target = tmp_path / "vault" / "knowledge" / "does-not-exist.md"
    target.parent.mkdir(parents=True)

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "skipped_artifact_unreadable"


# ---------------------------------------------------------------------------
# Edge: read-only file
# ---------------------------------------------------------------------------


def test_read_only_file_returns_failed_atomic(tmp_path: Path) -> None:
    """When the directory is writable but the file isn't, the read
    succeeds, the YAML parse succeeds, but os.replace() fails. The
    original file must be unchanged."""
    target = _stage(tmp_path, name="finding-no-tags.md")
    before = target.read_bytes()
    before_mtime = target.stat().st_mtime_ns

    # Make the file AND its parent directory read-only so neither the
    # in-place replace nor the tmp file creation can succeed. (POSIX
    # rename requires write on the parent dir.)
    parent = target.parent
    target.chmod(0o444)
    parent.chmod(0o555)
    try:
        result = write_proofread_tag(
            target, date=date(2026, 5, 29), findings=[]
        )
    finally:
        # Restore permissions so pytest tmp_path cleanup works.
        parent.chmod(0o755)
        target.chmod(0o644)

    assert result.status == "failed_atomic_write_aborted"
    assert result.error
    assert target.read_bytes() == before
    assert target.stat().st_mtime_ns == before_mtime


# ---------------------------------------------------------------------------
# Atomic-write: simulated mid-write failure
# ---------------------------------------------------------------------------


def test_simulated_write_failure_leaves_original_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monkeypatch os.replace to raise mid-operation. The original file
    must be byte-identical after the failure, and no `.proofread.tmp`
    file may be left behind."""
    target = _stage(tmp_path, name="finding-no-tags.md")
    before = target.read_bytes()
    before_mtime = target.stat().st_mtime_ns

    import scripts.frontmatter_writer as fw

    real_replace = os.replace

    def boom(src, dst):
        # Simulate the failure AFTER the tmp file has been written;
        # this is the worst case for atomicity.
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(fw.os, "replace", boom)

    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[]
    )

    assert result.status == "failed_atomic_write_aborted"
    assert "simulated mid-write failure" in (result.error or "")
    # Original byte-identical, mtime preserved.
    assert target.read_bytes() == before
    assert target.stat().st_mtime_ns == before_mtime
    # No leftover tmp file.
    leftover = list(target.parent.glob("*.proofread.tmp"))
    assert leftover == []

    # Restore for any later code paths.
    monkeypatch.setattr(fw.os, "replace", real_replace)


# ---------------------------------------------------------------------------
# Default date = today UTC
# ---------------------------------------------------------------------------


def test_default_date_uses_today_utc(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    target = _stage(tmp_path, name="finding-no-tags.md")
    today = datetime.now(timezone.utc).date().isoformat()

    result = write_proofread_tag(target, findings=[])

    assert result.status == "written"
    assert result.tag_added == f"proofread-passed-{today}"


# ---------------------------------------------------------------------------
# Codex-review tag convention mirror — pin the exact shape
# ---------------------------------------------------------------------------


def test_tag_pattern_mirrors_codex_review_convention(tmp_path: Path) -> None:
    """The codex-review tag is a flat kebab-case string in a YAML
    `tags:` array (see e.g. vault/knowledge/2026-04-13-immediate-
    proposals-segment-diagnostic-lessons.md). Our tag mirrors that
    exactly — kebab-case, ISO-date suffix, sits as a peer entry."""
    import re

    target = _stage(tmp_path, name="finding-with-tags.md")
    write_proofread_tag(target, date=date(2026, 5, 29), findings=[])

    tags = _read_tags(target)
    new_tag = tags[-1]
    assert isinstance(new_tag, str)
    # Exact pattern (kebab-case + ISO date).
    assert re.match(r"^proofread-passed-\d{4}-\d{2}-\d{2}$", new_tag)
    # Sits alongside codex-review — same array, same shape.
    assert "codex-review" in tags


# ---------------------------------------------------------------------------
# Would-be integration with render_output — skip-marked
# ---------------------------------------------------------------------------


def test_render_output_includes_tagged_artifact_line() -> None:
    """Forward-looking: when the writer returns a `written` result,
    the orchestrator should pass `tagged_artifact=<tag>` (or an
    equivalent shape) into render(), and the rendered output should
    contain a single line `Tagged artifact: proofread-passed-<date>`
    appearing BEFORE the Pre-mortem section (anti-theatre — not the
    very last line)."""
    from scripts.render_output import PRE_MORTEM_PROMPT, render

    # Sketch of the expected shape — finalised in the integration commit.
    run_record = {
        "record_type": "run_record",
        "run_id": "test",
        "started_at": "2026-05-29T00:00:00Z",
        "completed_at": "2026-05-29T00:00:01Z",
        "artifact_path": "vault/knowledge/2026-05-29-foo.md",
        "tagged_artifact": "proofread-passed-2026-05-29",
    }
    out = render(findings=[], coverage=[], run_record=run_record)

    assert "Tagged artifact: proofread-passed-2026-05-29" in out
    pre_mortem_idx = out.index(PRE_MORTEM_PROMPT)
    tag_idx = out.index("Tagged artifact: proofread-passed-2026-05-29")
    assert tag_idx < pre_mortem_idx


# ---------------------------------------------------------------------------
# ccp-021: silent-lens-failure refusal
# ---------------------------------------------------------------------------
#
# When a lens emits findings that all get rejected by the ccp-008
# schema validator, ccp-020's aggregator emits a synthetic coverage
# record carrying `error:` + `checks_attempted: -1`. The tag writer
# must refuse to tag the artifact in that case — the aggregated finding
# list is misleadingly clean (the rejected findings are simply absent),
# and writing `proofread-passed-<date>` would be a green checkmark on
# an audit that half-failed.
#
# Discriminator: `checks_attempted == -1` (ccp-020 sentinel). This is
# distinct from legitimate failure paths (dry-run stubs, mechanical
# process-exit errors) which use `checks_attempted >= 0`. The
# distinction matters because the orchestrator's dry-run mode still
# expects to be able to write the tag.


def _synthetic_silent_failure(lens: str = "structural") -> dict:
    """Return a ccp-020-shaped synthetic coverage record matching what
    `aggregator._make_synthetic` emits when a lens's whole output was
    schema-rejected."""
    return {
        "record_type": "coverage",
        "lens": lens,
        "checks_attempted": -1,
        "checks_completed": 0,
        "claims_examined": -1,
        "claims_flagged": 0,
        "error": (
            "10 findings emitted, 0 schema-valid — lens output rejected "
            "(see aggregation warnings at end of output)"
        ),
    }


def _legitimate_failed_coverage(lens: str = "mechanical") -> dict:
    """A coverage record with `error:` set BUT NOT the ccp-020 sentinel
    (checks_attempted >= 0). Models the orchestrator's existing dry-run
    stub / mechanical-process-exit shape — these must NOT trigger the
    silent-failure refusal."""
    return {
        "record_type": "coverage",
        "lens": lens,
        "checks_attempted": 0,
        "checks_completed": 0,
        "claims_examined": 0,
        "claims_flagged": 0,
        "error": "dry-run, no LLM invoked",
    }


def test_clean_run_with_lens_coverage_writes_tag(tmp_path: Path) -> None:
    """Baseline: when lens_coverage is provided and contains no
    silent-failure records, the writer behaves exactly as it did before
    ccp-021 — tag is written for a clean vault finding."""
    target = _stage(tmp_path, name="finding-no-tags.md")
    coverage = [
        {
            "record_type": "coverage",
            "lens": "scope",
            "checks_attempted": 6,
            "checks_completed": 6,
            "claims_examined": 12,
            "claims_flagged": 0,
        },
        {
            "record_type": "coverage",
            "lens": "grounding",
            "checks_attempted": 4,
            "checks_completed": 4,
            "claims_examined": 3,
            "claims_flagged": 0,
        },
    ]

    result = write_proofread_tag(
        target,
        date=date(2026, 5, 29),
        findings=[],
        lens_coverage=coverage,
    )

    assert result.status == "written"
    assert result.tag_added == "proofread-passed-2026-05-29"


def test_silent_lens_failure_refuses_tag(tmp_path: Path) -> None:
    """Core ccp-021 case: synthetic-silent-failure coverage record +
    zero blocking findings → tag REFUSED with `skipped_silent_lens_failure`
    status. Error message names the silent-failed lens.

    Mirrors the bank-details 2026-05-29 smoke test where structural's
    10 findings were silently rejected at schema validation."""
    target = _stage(tmp_path, name="finding-no-tags.md")
    before = target.read_bytes()
    coverage = [_synthetic_silent_failure(lens="structural")]

    result = write_proofread_tag(
        target,
        date=date(2026, 5, 29),
        findings=[],
        lens_coverage=coverage,
    )

    assert result.status == "skipped_silent_lens_failure"
    assert result.tag_added is None
    assert result.error
    # The diagnostic must name the silent-failed lens.
    assert "structural" in result.error
    # And carry the actionable "re-run" hint.
    assert "Re-run" in result.error
    # File on disk is byte-identical — no tag was written.
    assert target.read_bytes() == before


def test_silent_lens_failure_plus_blocking_finding_blocking_wins(
    tmp_path: Path,
) -> None:
    """When BOTH a silent lens failure AND a blocking finding are
    present, the tag is still refused — but the blocking-findings
    status takes precedence in the diagnostic (the user-facing signal
    is actionable; the silent failure is surfaced in `error` as
    context)."""
    target = _stage(tmp_path, name="finding-no-tags.md")
    before = target.read_bytes()
    coverage = [_synthetic_silent_failure(lens="structural")]
    findings = [
        {"severity": "blocking", "check_id": "scope-drift", "lens": "scope"},
    ]

    result = write_proofread_tag(
        target,
        date=date(2026, 5, 29),
        findings=findings,
        lens_coverage=coverage,
    )

    assert result.status == "skipped_blocking_findings_present"
    # The error message surfaces both conditions: the blocking finding
    # is the headline, the silent failure context is preserved.
    assert result.error
    assert "blocking" in result.error.lower()
    assert "structural" in result.error
    # File unchanged.
    assert target.read_bytes() == before


def test_legitimate_failed_coverage_does_not_refuse(tmp_path: Path) -> None:
    """A coverage record with `error:` set but `checks_attempted >= 0`
    is a LEGITIMATE failure path (dry-run stub, mechanical process
    exit) — not the ccp-020 silent-rejection shape. The writer must
    NOT confuse the two; the tag must still be written.

    This is the backwards-compat guard that keeps the orchestrator's
    dry-run mode working — every dry-run lens stub carries
    `error: "dry-run, no LLM invoked"` but with checks_attempted set to
    the per-lens check count (>=0), not the -1 sentinel."""
    target = _stage(tmp_path, name="finding-no-tags.md")
    coverage = [
        _legitimate_failed_coverage(lens="mechanical"),
        _legitimate_failed_coverage(lens="scope"),
    ]

    result = write_proofread_tag(
        target,
        date=date(2026, 5, 29),
        findings=[],
        lens_coverage=coverage,
    )

    assert result.status == "written"
    assert result.tag_added == "proofread-passed-2026-05-29"


def test_allow_tag_on_partial_failure_downgrades_to_attempted(
    tmp_path: Path,
) -> None:
    """The opt-in `allow_tag_on_partial_failure=True` path. When a
    silent lens failure is detected, the writer downgrades to
    `proofread-attempted-<date>` instead of refusing — the audit trail
    is preserved without overclaiming clean coverage."""
    target = _stage(tmp_path, name="finding-no-tags.md")
    coverage = [_synthetic_silent_failure(lens="structural")]

    result = write_proofread_tag(
        target,
        date=date(2026, 5, 29),
        findings=[],
        lens_coverage=coverage,
        allow_tag_on_partial_failure=True,
    )

    assert result.status == "written"
    assert result.tag_added == "proofread-attempted-2026-05-29"
    assert result.previous_tag is None
    tags = _read_tags(target)
    assert "proofread-attempted-2026-05-29" in tags
    # And the unwanted shape is NOT also written.
    assert "proofread-passed-2026-05-29" not in tags


def test_attempted_tag_replaces_stale_passed_tag(tmp_path: Path) -> None:
    """Cross-prefix stale-tag collapse: a previous
    `proofread-passed-<old-date>` must be REPLACED (not kept alongside)
    when this run downgrades to `proofread-attempted-<new-date>`. Same
    contract in the reverse direction — there should only ever be ONE
    proofread tag on an artifact at a time."""
    target = _stage(tmp_path, name="finding-with-stale-tag.md")
    coverage = [_synthetic_silent_failure(lens="structural")]

    result = write_proofread_tag(
        target,
        date=date(2026, 5, 29),
        findings=[],
        lens_coverage=coverage,
        allow_tag_on_partial_failure=True,
    )

    assert result.status == "written"
    assert result.tag_added == "proofread-attempted-2026-05-29"
    # Previous passed tag is captured for the audit trail and replaced.
    assert result.previous_tag == "proofread-passed-2026-04-15"
    tags = _read_tags(target)
    proofread_tags = [
        t for t in tags
        if isinstance(t, str) and (
            t.startswith("proofread-passed-")
            or t.startswith("proofread-attempted-")
        )
    ]
    # Exactly one proofread-* entry, with the new attempted shape.
    assert proofread_tags == ["proofread-attempted-2026-05-29"]


def test_lens_coverage_none_preserves_ccp014_behavior(tmp_path: Path) -> None:
    """Backwards-compat: `lens_coverage=None` (the default) bypasses
    the ccp-021 silent-failure check entirely — ccp-014's behaviour
    is preserved bit-for-bit. Callers that haven't been updated to
    pass coverage records continue to work."""
    target = _stage(tmp_path, name="finding-no-tags.md")

    # No lens_coverage passed at all — same call shape as ccp-014 tests.
    result = write_proofread_tag(
        target, date=date(2026, 5, 29), findings=[],
    )
    assert result.status == "written"
    assert result.tag_added == "proofread-passed-2026-05-29"


def test_multi_lens_silent_failure_names_all_in_error(tmp_path: Path) -> None:
    """When TWO lenses silently fail in the same run, the diagnostic
    error must name BOTH so the user can fix each. The leading lens
    appears in the main error sentence; subsequent ones are listed."""
    target = _stage(tmp_path, name="finding-no-tags.md")
    coverage = [
        _synthetic_silent_failure(lens="structural"),
        _synthetic_silent_failure(lens="grounding"),
    ]

    result = write_proofread_tag(
        target,
        date=date(2026, 5, 29),
        findings=[],
        lens_coverage=coverage,
    )

    assert result.status == "skipped_silent_lens_failure"
    assert result.error
    assert "structural" in result.error
    assert "grounding" in result.error


def test_silent_failure_other_lens_findings_unrelated(tmp_path: Path) -> None:
    """When the silent failure is on structural but the scope lens
    successfully emitted (zero) findings, the tag is STILL refused —
    even one silent-failed lens means the audit didn't fully land.
    The aggregated finding list being empty doesn't rescue the run."""
    target = _stage(tmp_path, name="finding-no-tags.md")
    coverage = [
        {
            "record_type": "coverage",
            "lens": "scope",
            "checks_attempted": 6,
            "checks_completed": 6,
            "claims_examined": 12,
            "claims_flagged": 0,
        },
        _synthetic_silent_failure(lens="structural"),
    ]

    result = write_proofread_tag(
        target,
        date=date(2026, 5, 29),
        findings=[],
        lens_coverage=coverage,
    )

    assert result.status == "skipped_silent_lens_failure"
    assert "structural" in (result.error or "")
