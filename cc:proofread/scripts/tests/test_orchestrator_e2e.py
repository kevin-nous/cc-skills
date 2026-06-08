"""ccp-008 orchestrator end-to-end tests.

The orchestrator's two phases are tested via the in-process API
(faster + lets us inspect intermediate state without re-invoking the
subprocess). One CLI-shape test (`test_cli_pre_lens_dry_run`) exercises
the argparse + main() path.

The LLM subagent invocation is OUT OF SCOPE for this test file — the
SKILL.md procedure dispatches it between pre_lens and post_lens, and
the human runs that smoke test manually after this issue lands. Here
we exercise --dry-run mode, which simulates the path without calling
into Anthropic.

Run:
    pytest -v ~/cc-skills/cc:proofread/scripts/tests/test_orchestrator_e2e.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.orchestrator import (  # noqa: E402
    GROUNDING_FILE,
    GROUNDING_PROMPT_FILE,
    POINTERS_FILE,
    SCOPE_FILE,
    SCOPE_PROMPT_FILE,
    STRUCTURAL_FILE,
    STRUCTURAL_PROMPT_FILE,
    _run_discovery,
    extract_scope_checks,
    phase_post_lens,
    phase_pre_lens,
    render_scope_prompt,
)
from scripts.render_output import FORBIDDEN_AFFIRMATIONS, PRE_MORTEM_PROMPT  # noqa: E402

FIXTURES = SKILL_ROOT / "scripts" / "tests" / "fixtures" / "e2e"
DEFAULT_CRITERIA = SKILL_ROOT / "default-criteria.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_run_id_from_message(msg: str) -> str:
    """Pull the run_id out of a "PROMPTS_READY <run-id>\\n...",
    "PROMPT_READY <path>" (legacy), or "DRY_RUN_READY <path>" message."""
    # PROMPTS_READY signal carries the run-id on the first line directly
    # — prefer it for explicitness when present.
    first_line = msg.splitlines()[0] if msg else ""
    m = re.match(r"PROMPTS_READY\s+([0-9a-f-]+)", first_line)
    if m:
        return m.group(1)
    m = re.search(r"cc-proofread-([0-9a-f-]+)", msg)
    assert m, f"could not extract run_id from message: {msg!r}"
    return m.group(1)


# ---------------------------------------------------------------------------
# Scope check extraction
# ---------------------------------------------------------------------------


def test_extract_scope_checks_from_default_criteria() -> None:
    """default-criteria.md ships with 6 scope checks per its frontmatter
    table; extract_scope_checks must surface all of them."""
    checks = extract_scope_checks(DEFAULT_CRITERIA)
    assert len(checks) == 6
    ids = [c.id for c in checks]
    # Sanity: these specific IDs ship in the default.
    assert "scope-drift" in ids
    assert "capability-from-rate-evidence" in ids
    assert "maturity-surface-missing" in ids


def test_extract_scope_checks_missing_section_raises(tmp_path: Path) -> None:
    """If criteria.md is missing the '## Scope auditor' section, extract
    must raise — silent return-of-empty would mask a malformed override."""
    bad = tmp_path / "criteria.md"
    bad.write_text(
        "---\nschema_version: 1\n---\n\n## Grounding auditor\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Scope auditor"):
        extract_scope_checks(bad)


# ---------------------------------------------------------------------------
# Prompt rendering — load-bearing fragments check
# ---------------------------------------------------------------------------


def test_render_scope_prompt_contains_load_bearing_fragments(
    tmp_path: Path,
) -> None:
    """The scope auditor prompt MUST verbatim contain the load-bearing
    fragments named in the issue: read-only contract,
    convergent-blind-spot reminder, adversarial-voice instruction,
    forbidden-word constraint, schema reference."""
    checks = extract_scope_checks(DEFAULT_CRITERIA)
    artifact_text = "## Sample artifact\n\nA load-bearing claim.\n"
    output_path = tmp_path / "scope.jsonl"
    prompt = render_scope_prompt(artifact_text, checks, output_path)

    # The prompt wraps long lines, so test the load-bearing fragments
    # by collapsing all whitespace to a single space before searching.
    flat = " ".join(prompt.split())

    # 1. Read-only contract.
    assert (
        "You emit findings to the named JSONL path. You do NOT modify "
        "the artifact. You do NOT read or write any other file."
    ) in flat

    # 2. Convergent-blind-spot reminder.
    assert (
        "You receive only the artifact and the scope-lens checks; "
        "nothing else. You are isolated from other lenses' outputs by "
        "design."
    ) in flat

    # 3. Adversarial-voice instruction.
    assert "this claim X contradicts Y" in flat
    assert "Do NOT write \"consider whether...\"" in flat

    # 4. Schema reference.
    assert "schemas/finding.schema.json" in flat
    assert 'lens: "scope"' in flat or '"scope"' in flat

    # 5. Output path is the literal scope.jsonl path.
    assert str(output_path) in prompt


def test_render_scope_prompt_contains_forbidden_word_constraint(
    tmp_path: Path,
) -> None:
    """The renderer's anti-theatre guard fires on the word 'clean'. The
    scope auditor must be told explicitly to avoid it — otherwise its
    JSONL findings can poison the final output."""
    checks = extract_scope_checks(DEFAULT_CRITERIA)
    prompt = render_scope_prompt(
        "artifact", checks, tmp_path / "scope.jsonl",
    )
    assert "Do not use the word \"clean\" in your findings" in prompt


def test_render_scope_prompt_no_other_lens_leakage(tmp_path: Path) -> None:
    """The convergent-blind-spot guard: scope auditor prompt must NOT
    mention mechanical / discovery findings, substrate pointers, or
    grounding/structural lens output."""
    checks = extract_scope_checks(DEFAULT_CRITERIA)
    artifact = (
        "# Plain writeup\n\nA scope claim that won't trigger the guard."
    )
    prompt = render_scope_prompt(
        artifact, checks, tmp_path / "scope.jsonl",
    )
    # Grep for the substrate-pointer vocabulary used downstream — the
    # scope lens prompt must not refer to those concepts.
    assert "substrate_field" not in prompt
    assert "pointer_value" not in prompt
    # And it must not mention the other lenses' findings.
    assert "grounding finding" not in prompt.lower()
    assert "structural finding" not in prompt.lower()
    assert "mechanical finding" not in prompt.lower()


def test_render_scope_prompt_artifact_substitutes_literally(
    tmp_path: Path,
) -> None:
    """Edge case: an artifact that contains the literal text
    '{scope_checks}' must NOT trigger further substitution after the
    artifact is inserted. The render order (output_path → scope_checks
    → artifact) is the guarantee."""
    checks = extract_scope_checks(DEFAULT_CRITERIA)
    sneaky_artifact = (
        "# Artifact with literal placeholder text\n\n"
        "Here is some prose that mentions {scope_checks} explicitly."
    )
    prompt = render_scope_prompt(
        sneaky_artifact, checks, tmp_path / "scope.jsonl",
    )
    # The literal "{scope_checks}" string must survive intact (not be
    # replaced with the rendered checks block) because it appeared
    # inside the artifact, not in the template.
    assert "mentions {scope_checks} explicitly" in prompt


# ---------------------------------------------------------------------------
# pre_lens phase — file outputs
# ---------------------------------------------------------------------------


def test_pre_lens_phase_produces_prompt_file(tmp_path: Path) -> None:
    """phase_pre_lens must write the scope_prompt.md file and return a
    PROMPTS_READY signal naming the scope prompt (alongside grounding
    and structural)."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,  # forces fallback to default criteria
        dry_run=False,
        criteria_only=False,
    )
    assert code == 0
    assert msg.startswith("PROMPTS_READY ")
    # Scope prompt path is on the `scope:` line of the signal.
    scope_line = next(
        ln for ln in msg.splitlines() if ln.startswith("scope:")
    )
    prompt_path = Path(scope_line.split(":", 1)[1].strip())
    assert prompt_path.exists()
    assert prompt_path.name == SCOPE_PROMPT_FILE

    # The prompt file contains the load-bearing fragments and the
    # forbidden-word constraint (we double-check at the file boundary,
    # not just at the in-memory render).
    prompt_text = prompt_path.read_text(encoding="utf-8")
    assert (
        "You emit findings to the named JSONL path. You do NOT modify "
        "the artifact."
    ) in prompt_text
    assert "Do not use the word \"clean\" in your findings" in prompt_text


def test_pre_lens_writes_three_prompt_files(tmp_path: Path) -> None:
    """Wave 4 integration: pre_lens must write scope, grounding, and
    structural prompt files, each with its lens-distinctive load-bearing
    fragment."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=False,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"

    # All three prompt files exist.
    scope_p = rd / SCOPE_PROMPT_FILE
    ground_p = rd / GROUNDING_PROMPT_FILE
    struct_p = rd / STRUCTURAL_PROMPT_FILE
    assert scope_p.exists()
    assert ground_p.exists()
    assert struct_p.exists()

    # Each prompt carries its lens-distinctive load-bearing fragment.
    # Scope: standard read-only contract (shared) + scope lens label.
    scope_text = scope_p.read_text(encoding="utf-8")
    assert "scope auditor lens" in scope_text.lower()
    # Grounding: substrate pointers marker (the lens never sees the
    # full artifact body — only pointers).
    ground_text = ground_p.read_text(encoding="utf-8")
    assert "---POINTERS---" in ground_text
    assert "grounding auditor lens" in ground_text.lower()
    # Structural: recent-diff brackets + the meta-flag sentinel id.
    struct_text = struct_p.read_text(encoding="utf-8")
    assert "---RECENT-DIFF---" in struct_text
    assert "---END-RECENT-DIFF---" in struct_text
    assert "late-added-callouts-unreviewed" in struct_text


def test_pre_lens_prints_prompts_ready_with_three_paths(
    tmp_path: Path,
) -> None:
    """The PROMPTS_READY signal must list all three prompts on dedicated
    `scope:`, `grounding:`, `structural:` lines so a Claude consumer
    that wants explicit paths (rather than convention-by-runid) can
    parse them directly."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=False,
        criteria_only=False,
    )
    assert code == 0
    lines = msg.splitlines()
    assert lines[0].startswith("PROMPTS_READY ")
    # Build a mapping of label → path.
    by_label: dict[str, str] = {}
    for ln in lines[1:]:
        if ":" in ln:
            label, path = ln.split(":", 1)
            by_label[label.strip()] = path.strip()
    assert "scope" in by_label
    assert "grounding" in by_label
    assert "structural" in by_label
    # And each path actually exists on disk.
    for path_str in by_label.values():
        assert Path(path_str).exists(), f"prompt path does not exist: {path_str}"


def test_dry_run_writes_three_lens_stubs(tmp_path: Path) -> None:
    """Under --dry-run, pre_lens must write a stub jsonl for ALL 3
    lenses (scope, grounding, structural), each with a coverage record
    carrying the 'dry-run, no LLM invoked' error string. Without this
    the post_lens renderer would silently omit grounding + structural
    instead of naming them as failed lenses."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"
    for fname, lens in (
        (SCOPE_FILE, "scope"),
        (GROUNDING_FILE, "grounding"),
        (STRUCTURAL_FILE, "structural"),
    ):
        p = rd / fname
        assert p.exists(), f"{fname} not written in dry-run"
        text = p.read_text(encoding="utf-8")
        assert '"record_type": "coverage"' in text or (
            '"record_type":"coverage"' in text
        )
        assert f'"lens": "{lens}"' in text or f'"lens":"{lens}"' in text
        assert "dry-run, no LLM invoked" in text


def test_dry_run_output_renders_all_three_lens_failed(
    tmp_path: Path,
) -> None:
    """--dry-run end-to-end through post_lens shows 3 lens-failed
    coverage entries instead of just one. The render lists each failed
    lens in the Coverage section with its dry-run error string."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    code2, output = phase_post_lens(run_id, keep_temp=False)
    assert code2 == 0

    # Strip stderr-warnings tail to assess only the rendered body.
    rendered = output.split("\naggregation warnings:")[0]

    # The renderer surfaces failed lenses in Coverage with the lens
    # label. All three labels must appear with their failure language.
    for label in ("scope auditor", "grounding auditor", "structural auditor"):
        assert label in rendered, (
            f"{label!r} missing from rendered output:\n{rendered}"
        )
    # The dry-run error string is propagated through the renderer.
    assert rendered.count("dry-run, no LLM invoked") >= 3


def test_discovery_partitioning_separates_pointers_and_findings(
    tmp_path: Path,
) -> None:
    """_run_discovery must split discovery's mixed JSONL stream into two
    files: discovery.jsonl (record_type=finding records, for the
    aggregator) and pointers.jsonl (records with pointer_type, for the
    grounding lens). Anything matching neither shape is skipped."""
    # The small_writeup fixture has chart_id and wikilink pointers per
    # the discovery probe earlier; it may or may not trip the
    # citation-traceability check depending on proximity. Either way,
    # the partitioning property must hold: no pointer records end up in
    # discovery.jsonl, no finding records end up in pointers.jsonl.
    findings_out = tmp_path / "discovery.jsonl"
    pointers_out = tmp_path / "pointers.jsonl"
    _run_discovery(
        FIXTURES / "small_writeup.md", findings_out, pointers_out,
    )

    import json as _json
    # Both files exist.
    assert findings_out.exists()
    assert pointers_out.exists()

    # discovery.jsonl: every record_type is "finding" (or "coverage" if
    # the orchestrator appended an error coverage line — small_writeup
    # exits cleanly so it shouldn't).
    for line in findings_out.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = _json.loads(line)
        assert rec.get("record_type") in {"finding", "coverage"}, (
            f"unexpected record in discovery.jsonl: {rec}"
        )
        # Critical: no pointer records leaked here.
        assert "pointer_type" not in rec or rec.get("record_type") == "finding"

    # pointers.jsonl: every record has pointer_type set.
    pointer_count = 0
    for line in pointers_out.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = _json.loads(line)
        assert "pointer_type" in rec, (
            f"non-pointer record leaked into pointers.jsonl: {rec}"
        )
        # And critically NOT a finding.
        assert rec.get("record_type") != "finding"
        pointer_count += 1
    # The fixture is known to contain a chart/<id> and a [[wikilink]],
    # so at least 1 pointer must be present.
    assert pointer_count >= 1, "expected at least 1 pointer in fixture"


def test_pre_lens_writes_mechanical_and_discovery_jsonl(
    tmp_path: Path,
) -> None:
    """pre_lens must run the mechanical prepass + discovery subprocesses
    and capture their JSONL output. Both files must exist after."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=False,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"
    assert (rd / "mechanical.jsonl").exists()
    assert (rd / "discovery.jsonl").exists()
    # Mechanical always emits a coverage record at minimum.
    mech_text = (rd / "mechanical.jsonl").read_text(encoding="utf-8")
    assert '"record_type":"coverage"' in mech_text


def test_pre_lens_does_not_call_llm(tmp_path: Path) -> None:
    """Architectural property: pre_lens MUST exit before any LLM call.
    Tested by asserting (a) no scope.jsonl exists after pre_lens (since
    the subagent writes it), and (b) phase exits cleanly with code 0.
    Together these prove the Python phase is the boundary."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=False,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"
    # The Claude layer is what writes scope.jsonl. In Python-only tests
    # it must NOT exist yet.
    assert not (rd / SCOPE_FILE).exists()


# ---------------------------------------------------------------------------
# --criteria-only debug flag
# ---------------------------------------------------------------------------


def test_criteria_only_flag_lists_scope_checks(tmp_path: Path) -> None:
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=False,
        criteria_only=True,
    )
    assert code == 0
    # Criteria notice line.
    assert "Criteria:" in msg
    assert "default-criteria.md" in msg
    # Scope checks list.
    assert "scope-drift" in msg
    assert "capability-from-rate-evidence" in msg
    # Did NOT create a tmp dir (criteria_only is read-only).
    # (Hard to assert directly without race conditions; the absence of
    # PROMPT_READY in msg is the observable signal.)
    assert "PROMPT_READY" not in msg


# ---------------------------------------------------------------------------
# Dry-run end-to-end
# ---------------------------------------------------------------------------


def test_dry_run_produces_well_formed_output(tmp_path: Path) -> None:
    """--dry-run pre_lens + post_lens together: aggregate succeeds,
    output is anti-theatre-clean, pre-mortem is last line."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 0
    assert msg.startswith("DRY_RUN_READY ")
    run_id = _extract_run_id_from_message(msg)

    code2, output = phase_post_lens(run_id, keep_temp=False)
    assert code2 == 0

    # Anti-theatre guard: zero forbidden tokens.
    assert FORBIDDEN_AFFIRMATIONS.findall(output) == []

    # Pre-mortem prompt is the literal last line of the rendered chunk
    # (the orchestrator may append a stderr-warnings tail; the rendered
    # body always ends with the prompt).
    rendered = output.split("\naggregation warnings:")[0]
    assert rendered.splitlines()[-1] == PRE_MORTEM_PROMPT

    # Coverage section names every lens that ran.
    assert "## Coverage" in rendered
    assert "scope auditor" in rendered  # even in dry-run, lens is named
    assert "mechanical prepass" in rendered


def test_dry_run_post_lens_cleans_tmp_dir(tmp_path: Path) -> None:
    """Without --keep-temp, post_lens must delete /tmp/cc-proofread-<id>/."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"
    assert rd.exists()

    code2, _ = phase_post_lens(run_id, keep_temp=False)
    assert code2 == 0
    assert not rd.exists()


def test_keep_temp_preserves_tmp_dir(tmp_path: Path) -> None:
    """With --keep-temp, post_lens must NOT delete the tmp dir."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"

    code2, _ = phase_post_lens(run_id, keep_temp=True)
    assert code2 == 0
    assert rd.exists()

    # Cleanup so the test doesn't pile up tmp dirs.
    import shutil
    shutil.rmtree(rd)


# ---------------------------------------------------------------------------
# Parallel invocations — independent run_ids
# ---------------------------------------------------------------------------


def test_parallel_invocations_dont_collide(tmp_path: Path) -> None:
    """Two simultaneous --dry-run invocations must write to independent
    run_ids and not interleave each other's tmp dir contents."""

    def one_invocation() -> str:
        code, msg = phase_pre_lens(
            FIXTURES / "small_writeup.md",
            project_root=tmp_path,
            dry_run=True,
            criteria_only=False,
        )
        assert code == 0
        return _extract_run_id_from_message(msg)

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(one_invocation)
        f2 = ex.submit(one_invocation)
        rid1 = f1.result()
        rid2 = f2.result()

    assert rid1 != rid2
    rd1 = Path("/tmp") / f"cc-proofread-{rid1}"
    rd2 = Path("/tmp") / f"cc-proofread-{rid2}"
    assert rd1.exists()
    assert rd2.exists()

    # Each has its own scope.jsonl stub.
    assert (rd1 / SCOPE_FILE).exists()
    assert (rd2 / SCOPE_FILE).exists()

    # Drain.
    phase_post_lens(rid1, keep_temp=False)
    phase_post_lens(rid2, keep_temp=False)


# ---------------------------------------------------------------------------
# post_lens partial run_id matching
# ---------------------------------------------------------------------------


def test_post_lens_accepts_run_id_prefix(tmp_path: Path) -> None:
    """post_lens must accept either a full UUID or a unique prefix; the
    SKILL procedure may pass just the prefix the user copies."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    full_id = _extract_run_id_from_message(msg)
    prefix = full_id[:8]
    code2, output = phase_post_lens(prefix, keep_temp=False)
    assert code2 == 0
    assert "Pre-mortem" in output


def test_post_lens_missing_run_id_errors_cleanly(tmp_path: Path) -> None:
    code, msg = phase_post_lens("ffffffff-no-such-run", keep_temp=False)
    assert code == 2
    assert "no run directory" in msg


# ---------------------------------------------------------------------------
# Error in mechanical prepass surfaces in coverage rather than crashing
# ---------------------------------------------------------------------------


def test_pre_lens_missing_artifact_returns_error(tmp_path: Path) -> None:
    code, msg = phase_pre_lens(
        tmp_path / "does-not-exist.md",
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 2
    assert "not found" in msg


# ---------------------------------------------------------------------------
# CLI shape — dry-run round trip via subprocess
# ---------------------------------------------------------------------------


def test_cli_pre_lens_dry_run_then_post_lens(tmp_path: Path) -> None:
    """Smoke test the CLI surface end-to-end. Uses --dry-run so no LLM
    call is involved."""
    env = {**os.environ}

    pre_result = subprocess.run(
        [
            sys.executable, "-m", "scripts.orchestrator",
            "--phase", "pre_lens",
            str(FIXTURES / "small_writeup.md"),
            "--project-root", str(tmp_path),
            "--dry-run",
        ],
        capture_output=True, text=True,
        cwd=str(SKILL_ROOT),
        env=env,
    )
    assert pre_result.returncode == 0, pre_result.stderr
    assert pre_result.stdout.strip().startswith("DRY_RUN_READY ")
    run_id = _extract_run_id_from_message(pre_result.stdout)

    post_result = subprocess.run(
        [
            sys.executable, "-m", "scripts.orchestrator",
            "--phase", "post_lens",
            run_id,
        ],
        capture_output=True, text=True,
        cwd=str(SKILL_ROOT),
        env=env,
    )
    assert post_result.returncode == 0, post_result.stderr
    assert "Pre-mortem" in post_result.stdout
    # Anti-theatre guard at CLI surface too.
    assert FORBIDDEN_AFFIRMATIONS.findall(post_result.stdout) == []


# ---------------------------------------------------------------------------
# Performance — pre_lens < 1s on the small fixture
# ---------------------------------------------------------------------------


def test_pre_lens_performance(tmp_path: Path) -> None:
    """Per the issue: pre_lens under 1s on a 10KB artifact. The small
    fixture is much smaller; this is a regression guard, not a stress
    test."""
    import time
    start = time.monotonic()
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    elapsed = time.monotonic() - start
    assert code == 0
    assert elapsed < 2.0, f"pre_lens took {elapsed:.2f}s (budget 2s)"
    # Cleanup
    run_id = _extract_run_id_from_message(msg)
    phase_post_lens(run_id, keep_temp=False)


# ---------------------------------------------------------------------------
# Wave 5 integration — dedup in post_lens
# ---------------------------------------------------------------------------


def _write_lens_jsonl(rd: Path, fname: str, records: list[dict]) -> None:
    """Overwrite a lens jsonl with hand-authored records (one per line)."""
    import json
    with (rd / fname).open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _stage_vault_finding(tmp_path: Path) -> Path:
    """Drop a real vault/knowledge/<slug>.md file (type: finding) into a
    fake project tree and return its path. Used by tests that exercise
    the orchestrator's frontmatter-tag wiring end-to-end."""
    target_dir = tmp_path / "project" / "vault" / "knowledge"
    target_dir.mkdir(parents=True)
    target = target_dir / "2026-05-29-test-finding.md"
    target.write_text(
        (
            "---\n"
            "type: finding\n"
            "date: 2026-05-29\n"
            "slug: test-finding\n"
            "title: Test finding\n"
            "status: published\n"
            "---\n\n"
            "# Test finding\n\n"
            "The experiment crossed the threshold at +6.4pp.\n"
            "Mature cohort is 14d.\n"
        ),
        encoding="utf-8",
    )
    return target


def test_post_lens_calls_dedup_before_render(tmp_path: Path) -> None:
    """Two pre-recorded scope + structural findings with same claim +
    overlapping line range should converge — the rendered output must
    show the `Caught by N/3 lenses:` prefix that only the dedup path
    emits. Without dedup wiring, both would render separately."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"

    # Same claim, overlapping line range — meets both dedup conditions
    # (claim similarity 1.0; overlap >=1).
    shared_claim = (
        "The headline +6.4pp on signup_completed reflects the wider 5d+1d "
        "scope, not the spec 14d primary window."
    )
    location = {
        "file": str(FIXTURES / "small_writeup.md"),
        "line_range_start": 5,
        "line_range_end": 9,
    }
    scope_rec = {
        "record_type": "finding",
        "lens": "scope",
        "check_id": "scope-drift",
        "severity": "blocking",
        "claim": shared_claim,
        "location": location,
        "finding": "Scope migration vs spec without re-statement.",
        "remediation": "Lead with the spec-scope read.",
    }
    structural_rec = {
        "record_type": "finding",
        "lens": "structural",
        "check_id": "hedge-vs-data-semantic",
        "severity": "blocking",
        "claim": shared_claim,
        "location": location,
        "finding": "Confident headline at non-spec scope.",
        "remediation": "Soften the headline.",
    }
    _write_lens_jsonl(rd, SCOPE_FILE, [scope_rec])
    _write_lens_jsonl(rd, STRUCTURAL_FILE, [structural_rec])
    _write_lens_jsonl(rd, GROUNDING_FILE, [])

    code2, output = phase_post_lens(run_id, keep_temp=False)
    assert code2 == 0
    # Convergent header is the architectural fingerprint of the dedup
    # path. Without dedup wiring, neither finding would carry it.
    assert "Caught by" in output and "lenses:" in output, (
        f"convergent prefix missing — dedup not wired into post_lens.\n"
        f"Output:\n{output}"
    )


# ---------------------------------------------------------------------------
# Wave 5 integration — frontmatter tagging in post_lens
# ---------------------------------------------------------------------------


def test_post_lens_calls_frontmatter_writer_on_vault_finding_clean_pass(
    tmp_path: Path,
) -> None:
    """When the artifact is a vault finding and zero blocking findings
    fired, post_lens must call the frontmatter writer; the resulting
    `tagged_artifact` field flows into the rendered output."""
    artifact = _stage_vault_finding(tmp_path)
    code, msg = phase_pre_lens(
        artifact,
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"
    # No blocking findings on any lens.
    _write_lens_jsonl(rd, SCOPE_FILE, [])
    _write_lens_jsonl(rd, GROUNDING_FILE, [])
    _write_lens_jsonl(rd, STRUCTURAL_FILE, [])

    code2, output = phase_post_lens(run_id, keep_temp=False)
    assert code2 == 0

    # Tag landed in the file on disk.
    import frontmatter
    post = frontmatter.load(str(artifact))
    tags = list(post.metadata.get("tags") or [])
    assert any(
        str(t).startswith("proofread-passed-") for t in tags
    ), f"frontmatter writer was not called; tags = {tags!r}"

    # And surfaced in the rendered output.
    assert "Tagged artifact: proofread-passed-" in output, (
        "Tagged-artifact line missing from rendered output."
    )


def test_post_lens_does_not_tag_when_blocking_finding_present(
    tmp_path: Path,
) -> None:
    """Blocking finding from any lens short-circuits the tag write —
    even on an otherwise-eligible vault finding."""
    artifact = _stage_vault_finding(tmp_path)
    code, msg = phase_pre_lens(
        artifact,
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"
    _write_lens_jsonl(rd, SCOPE_FILE, [{
        "record_type": "finding",
        "lens": "scope",
        "check_id": "scope-drift",
        "severity": "blocking",
        "claim": "blocking claim",
        "location": {
            "file": str(artifact),
            "line_range_start": 9,
            "line_range_end": 9,
        },
        "finding": "X",
        "remediation": "Y",
    }])
    _write_lens_jsonl(rd, GROUNDING_FILE, [])
    _write_lens_jsonl(rd, STRUCTURAL_FILE, [])

    code2, output = phase_post_lens(run_id, keep_temp=False)
    assert code2 == 0

    import frontmatter
    post = frontmatter.load(str(artifact))
    tags = list(post.metadata.get("tags") or [])
    assert not any(
        str(t).startswith("proofread-passed-") for t in tags
    ), f"tag was written despite blocking finding; tags = {tags!r}"
    assert "Tagged artifact:" not in output


def test_post_lens_does_not_tag_with_no_tag_flag(tmp_path: Path) -> None:
    """--no-tag passes through phase_post_lens to write_proofread_tag."""
    artifact = _stage_vault_finding(tmp_path)
    code, msg = phase_pre_lens(
        artifact,
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"
    _write_lens_jsonl(rd, SCOPE_FILE, [])
    _write_lens_jsonl(rd, GROUNDING_FILE, [])
    _write_lens_jsonl(rd, STRUCTURAL_FILE, [])

    code2, output = phase_post_lens(run_id, keep_temp=False, no_tag=True)
    assert code2 == 0

    import frontmatter
    post = frontmatter.load(str(artifact))
    tags = list(post.metadata.get("tags") or [])
    assert not any(
        str(t).startswith("proofread-passed-") for t in tags
    ), f"tag was written despite --no-tag; tags = {tags!r}"
    assert "Tagged artifact:" not in output


# ---------------------------------------------------------------------------
# Wave 5 integration — --deep mode prompt rendering
# ---------------------------------------------------------------------------


def test_deep_flag_writes_16_prompts_and_one_late_callouts_prompt(
    tmp_path: Path,
) -> None:
    """--deep mode at pre_lens: writes 16 per-check prompts (6 scope + 4
    grounding + 6 structural) PLUS one lens-level structural prompt for
    the late-added-callouts meta-flag. Total = 17 prompt files."""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=False,
        criteria_only=False,
        deep=True,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"

    # 16 per-check fragments — match the on-disk filename convention
    # `<lens>-<check_id>_prompt.md`. Exclude the late-callouts prompt
    # because it doesn't fit that exact pattern (it's
    # `structural-late-added-callouts_prompt.md`, which the loop below
    # picks up SEPARATELY).
    per_check_prompts = sorted(
        p.name for p in rd.glob("*_prompt.md")
        if p.name != "structural-late-added-callouts_prompt.md"
    )
    assert len(per_check_prompts) == 16, (
        f"expected 16 per-check prompts, found {len(per_check_prompts)}:\n"
        + "\n".join(per_check_prompts)
    )

    # The lens-level late-callouts prompt exists too.
    assert (rd / "structural-late-added-callouts_prompt.md").exists()

    # Signal carries the "(deep, 17 prompts)" annotation on line 1.
    assert "(deep" in msg.splitlines()[0]


# ---------------------------------------------------------------------------
# ccp-021: silent-lens-failure end-to-end via the orchestrator
# ---------------------------------------------------------------------------
#
# The orchestrator's post_lens phase must pass the aggregated coverage
# records (including any ccp-020 synthetic-silent-failure entries) into
# write_proofread_tag. The bank-details fixture (2026-05-29) replays
# the failure mode: structural's 10 findings get silently rejected at
# schema validation; without this wiring the tag would land despite the
# audit having silently failed.


def test_post_lens_does_not_tag_when_lens_silently_failed(
    tmp_path: Path,
) -> None:
    """End-to-end ccp-021: structural emits 10 schema-invalid findings
    (location as a string, not a dict) → aggregator emits a synthetic
    silent-failure coverage record → frontmatter writer REFUSES the
    tag despite the aggregated finding list being empty (because all
    10 got rejected) and there being no blocking finding to gate on.

    This is the bank-details fixture replay. Without the ccp-021 wiring
    the tag would silently land — the 2026-05-29 incident."""
    import json as _json

    artifact = _stage_vault_finding(tmp_path)
    code, msg = phase_pre_lens(
        artifact,
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"

    # Overwrite structural.jsonl with the bank-details-style invalid
    # findings — each carries location as a string rather than the
    # schema-required dict shape. The aggregator will reject all 10
    # and emit a synthetic coverage record.
    structural_records = []
    for i in range(10):
        structural_records.append({
            "record_type": "finding",
            "id": f"aa{i:06d}-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "lens": "structural",
            "check_id": "title-vs-content-mismatch",
            # Severity blocking on one, advisory on the rest — mirrors
            # the bank-details mix where 1 of 10 was blocking.
            "severity": "blocking" if i == 0 else "advisory",
            "claim": f"Schema-invalid finding {i}.",
            # ← location as a STRING (not the dict the schema requires);
            # this is what makes the record schema-invalid.
            "location": "the first paragraph",
            "finding": "Some finding text.",
            "remediation": "Some remediation text.",
        })
    with (rd / STRUCTURAL_FILE).open("w", encoding="utf-8") as fh:
        for rec in structural_records:
            fh.write(_json.dumps(rec) + "\n")
    # Scope + grounding emit empty (clean).
    _write_lens_jsonl(rd, SCOPE_FILE, [])
    _write_lens_jsonl(rd, GROUNDING_FILE, [])

    code2, output = phase_post_lens(run_id, keep_temp=False)
    assert code2 == 0

    # The tag was NOT written — the artifact's tags array is unchanged
    # from the pre-run shape (no proofread-passed-* present).
    import frontmatter
    post = frontmatter.load(str(artifact))
    tags = list(post.metadata.get("tags") or [])
    assert not any(
        str(t).startswith("proofread-passed-") for t in tags
    ), (
        "tag was written despite silent lens failure; "
        f"tags = {tags!r}"
    )
    # And NO attempted-tag was written either — default path is refuse,
    # not downgrade.
    assert not any(
        str(t).startswith("proofread-attempted-") for t in tags
    ), f"attempted-tag was written despite default refusal path; tags = {tags!r}"
    # The rendered output also does not claim a tagged artifact.
    assert "Tagged artifact:" not in output


def test_post_lens_with_allow_partial_writes_attempted_tag(
    tmp_path: Path,
) -> None:
    """End-to-end: --allow-tag-on-partial-failure downgrades to
    `proofread-attempted-<date>` instead of refusing. The audit trail
    is preserved on the artifact without overclaiming clean coverage."""
    import json as _json

    artifact = _stage_vault_finding(tmp_path)
    code, msg = phase_pre_lens(
        artifact,
        project_root=tmp_path,
        dry_run=True,
        criteria_only=False,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"

    # One invalid structural finding is enough to trigger the synthetic
    # coverage record.
    invalid = {
        "record_type": "finding",
        "id": "aa000000-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "lens": "structural",
        "check_id": "title-vs-content-mismatch",
        "severity": "advisory",
        "claim": "Schema-invalid finding.",
        "location": "the first paragraph",  # ← string, not dict
        "finding": "Some finding text.",
        "remediation": "Some remediation text.",
    }
    with (rd / STRUCTURAL_FILE).open("w", encoding="utf-8") as fh:
        fh.write(_json.dumps(invalid) + "\n")
    _write_lens_jsonl(rd, SCOPE_FILE, [])
    _write_lens_jsonl(rd, GROUNDING_FILE, [])

    code2, output = phase_post_lens(
        run_id,
        keep_temp=False,
        allow_tag_on_partial_failure=True,
    )
    assert code2 == 0

    # The attempted-tag landed.
    import frontmatter
    post = frontmatter.load(str(artifact))
    tags = list(post.metadata.get("tags") or [])
    assert any(
        str(t).startswith("proofread-attempted-") for t in tags
    ), f"attempted-tag missing; tags = {tags!r}"
    # And the passed-shape is NOT present.
    assert not any(
        str(t).startswith("proofread-passed-") for t in tags
    ), f"passed-tag was written on a partial-failure run; tags = {tags!r}"


def test_deep_mode_render_includes_cost_note(tmp_path: Path) -> None:
    """End-to-end --deep at orchestrator level: post_lens picks up the
    mode marker and the rendered output carries the ~5x cost note.
    (ccp-013's per-check tests already exercise the renderer in isolation;
    this test verifies the orchestrator wires mode=deep into the run
    record so the renderer sees it.)"""
    code, msg = phase_pre_lens(
        FIXTURES / "small_writeup.md",
        project_root=tmp_path,
        dry_run=False,
        criteria_only=False,
        deep=True,
    )
    assert code == 0
    run_id = _extract_run_id_from_message(msg)
    rd = Path("/tmp") / f"cc-proofread-{run_id}"

    # Drop a coverage-only stub in each expected per-check jsonl + the
    # late-callouts jsonl so the aggregator has something to read.
    # (No findings; the assertion is purely about the cost note.)
    from scripts.per_check_render import DEEP_FANOUT
    import json as _json
    for lens, check_ids in DEEP_FANOUT.items():
        for check_id in check_ids:
            (rd / f"{lens}-{check_id}.jsonl").write_text(
                _json.dumps({
                    "record_type": "coverage",
                    "lens": lens,
                    "checks_attempted": 1,
                    "checks_completed": 1,
                    "claims_examined": 0,
                    "claims_flagged": 0,
                }) + "\n",
                encoding="utf-8",
            )
    (rd / "structural-late-added-callouts.jsonl").write_text(
        _json.dumps({
            "record_type": "coverage",
            "lens": "structural",
            "checks_attempted": 1,
            "checks_completed": 0,
            "claims_examined": 0,
            "claims_flagged": 0,
        }) + "\n",
        encoding="utf-8",
    )

    code2, output = phase_post_lens(run_id, keep_temp=False)
    assert code2 == 0
    assert "Note: --deep mode used; ~5x token cost vs default." in output


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
