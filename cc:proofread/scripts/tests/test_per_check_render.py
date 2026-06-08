"""ccp-013 per-check rendering tests.

These tests defend the `--deep` mode architectural properties:

1. Every substantive check has its own fragment file on disk.
2. Each fragment carries the same load-bearing contract as the
   lens-level prompt — read-only paragraph, isolation reminder,
   forbidden-word constraint, adversarial voice.
3. `render_per_check_prompts` produces N=16 prompts when the default
   criteria registry has 16 substantive checks (6 scope + 4 grounding +
   6 structural).
4. Each rendered prompt mentions ONLY its own check_id — no
   cross-check contamination. A scope-drift fragment must not reference
   `mechanism-as-clock`, otherwise the per-check subagent would have
   sibling-check overlap and the `--deep` cost wouldn't buy isolation.
5. Grounding fragments NEVER contain `{artifact}` — the
   convergent-blind-spot guard at the fragment-template layer.
6. Scope + structural fragments DO contain `{artifact}` (the check
   needs the whole artifact).
7. Cost note: render_output emits the `~5x token cost` line when the
   run record carries `mode="deep"`.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT))

from scripts.per_check_render import (  # noqa: E402
    DEEP_FANOUT,
    PerCheckPrompt,
    render_per_check_prompts,
)
from scripts.render_output import render  # noqa: E402


CHECKS_DIR = SKILL_ROOT / "scripts" / "prompts" / "checks"
DEFAULT_CRITERIA = SKILL_ROOT / "default-criteria.md"

ALL_FRAGMENTS = [
    f"{lens}-{check_id}.md"
    for lens, check_ids in DEEP_FANOUT.items()
    for check_id in check_ids
]


# ---------------------------------------------------------------------------
# Fragment-file existence + shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fragment_name", ALL_FRAGMENTS)
def test_fragment_file_exists(fragment_name: str) -> None:
    path = CHECKS_DIR / fragment_name
    assert path.exists(), f"per-check fragment missing on disk: {path}"


def test_exactly_sixteen_fragment_files() -> None:
    """The deep-mode fan-out is 16 substantive checks. Adding or
    removing a fragment without updating DEEP_FANOUT is the failure
    this test catches."""
    found = sorted(p.name for p in CHECKS_DIR.glob("*.md"))
    expected = sorted(ALL_FRAGMENTS)
    assert found == expected, (
        f"fragment directory drift — found {len(found)} files, "
        f"expected {len(expected)}.\n  extra: {set(found) - set(expected)}\n"
        f"  missing: {set(expected) - set(found)}"
    )


@pytest.mark.parametrize("fragment_name", ALL_FRAGMENTS)
def test_fragment_has_forbidden_word_constraint(fragment_name: str) -> None:
    """Anti-theatre invariant: every per-check fragment must instruct
    the subagent to avoid the word 'clean' in findings. Otherwise the
    final aggregated output could leak past the FORBIDDEN_AFFIRMATIONS
    guard in render_output.
    """
    text = (CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    assert 'Do not use the word "clean" in your findings' in text, (
        f"{fragment_name} missing the forbidden-word constraint"
    )


@pytest.mark.parametrize("fragment_name", ALL_FRAGMENTS)
def test_fragment_has_read_only_contract(fragment_name: str) -> None:
    """Same wording the lens-level prompts use; matched flattened so
    incidental line-wrapping differences don't trip the assertion."""
    text = (CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert (
        "You emit findings to the named JSONL path. You do NOT modify "
        "the artifact. You do NOT read or write any other file."
    ) in flat, f"{fragment_name} missing the read-only contract"


@pytest.mark.parametrize("fragment_name", ALL_FRAGMENTS)
def test_fragment_has_convergent_blind_spot_reminder(
    fragment_name: str,
) -> None:
    """The `--deep` value proposition is per-check isolation. Each
    fragment must remind the subagent NOT to widen its scope to sibling
    checks — otherwise the architectural win evaporates."""
    text = (CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "Do NOT widen your scope to checks adjacent to yours" in flat, (
        f"{fragment_name} missing the convergent-blind-spot reminder"
    )


@pytest.mark.parametrize("fragment_name", ALL_FRAGMENTS)
def test_fragment_has_adversarial_voice_block(fragment_name: str) -> None:
    """The voice instruction is identical across lenses (worded around
    the lens's typical finding shape). Common predicate: it forbids
    'consider whether'. Flatten the file first because the phrase
    legitimately straddles a line break in some fragments."""
    text = (CHECKS_DIR / fragment_name).read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "consider whether" in flat, (
        f"{fragment_name} missing the adversarial voice instruction "
        "(should forbid 'consider whether' phrasing)"
    )


# ---------------------------------------------------------------------------
# Lens-specific input contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("check_id", DEEP_FANOUT["grounding"])
def test_grounding_fragment_does_NOT_contain_artifact_placeholder(
    check_id: str,
) -> None:
    """THE convergent-blind-spot guard at the per-check template level.

    Grounding fragments must never have an `{artifact}` placeholder.
    If a future refactor copies from a scope fragment, this test fires.
    """
    path = CHECKS_DIR / f"grounding-{check_id}.md"
    text = path.read_text(encoding="utf-8")
    assert "{artifact}" not in text, (
        f"{path.name} contains an {{artifact}} placeholder — this would "
        "convergent-blind the grounding lens by feeding it draft prose."
    )


@pytest.mark.parametrize("check_id", DEEP_FANOUT["grounding"])
def test_grounding_fragment_contains_pointers_placeholder(
    check_id: str,
) -> None:
    """Grounding fragments substitute pointers (not artifact). Without
    this placeholder, the renderer would silently produce a prompt with
    no input data."""
    path = CHECKS_DIR / f"grounding-{check_id}.md"
    text = path.read_text(encoding="utf-8")
    assert "{pointers}" in text, (
        f"{path.name} missing the {{pointers}} placeholder"
    )


@pytest.mark.parametrize("check_id", DEEP_FANOUT["scope"])
def test_scope_fragment_contains_artifact_placeholder(check_id: str) -> None:
    path = CHECKS_DIR / f"scope-{check_id}.md"
    text = path.read_text(encoding="utf-8")
    assert "{artifact}" in text, (
        f"{path.name} missing the {{artifact}} placeholder"
    )


@pytest.mark.parametrize("check_id", DEEP_FANOUT["structural"])
def test_structural_fragment_contains_artifact_placeholder(
    check_id: str,
) -> None:
    path = CHECKS_DIR / f"structural-{check_id}.md"
    text = path.read_text(encoding="utf-8")
    assert "{artifact}" in text, (
        f"{path.name} missing the {{artifact}} placeholder"
    )


@pytest.mark.parametrize("check_id", DEEP_FANOUT["structural"])
def test_structural_fragment_contains_recent_diff_placeholder(
    check_id: str,
) -> None:
    path = CHECKS_DIR / f"structural-{check_id}.md"
    text = path.read_text(encoding="utf-8")
    assert "{recent_diff}" in text, (
        f"{path.name} missing the {{recent_diff}} placeholder"
    )


# ---------------------------------------------------------------------------
# render_per_check_prompts — end-to-end behavior
# ---------------------------------------------------------------------------


def _sample_artifact() -> str:
    return (
        "# A sample finding\n\n"
        "We see +12pp lift in conversion this week. The change is "
        "comfortably above the 5pp threshold.\n\n"
        "See [[some-concept]] for context. Detail at state.yaml.foo.bar.\n"
    )


def _sample_pointers() -> list[dict]:
    return [
        {
            "pointer_type": "wikilink",
            "pointer_value": "some-concept",
            "anchored_claim": "See some-concept for context.",
            "line_range": {"start": 4, "end": 4},
        },
        {
            "pointer_type": "state_yaml_dotted",
            "pointer_value": "state.yaml.foo.bar",
            "anchored_claim": "Detail at state.yaml.foo.bar.",
            "line_range": {"start": 4, "end": 4},
        },
    ]


def test_renders_sixteen_prompts_by_default(tmp_path: Path) -> None:
    """Against the shipped default-criteria.md (which has 6+4+6=16
    substantive checks), the renderer produces 16 prompts."""
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text=_sample_artifact(),
        pointers=_sample_pointers(),
        recent_diff="",
        output_dir=tmp_path,
    )
    assert len(results) == 16, (
        f"expected 16 per-check prompts, got {len(results)}"
    )

    # Every result writes a prompt file on disk + names a JSONL path.
    for r in results:
        assert r.prompt_path.exists(), (
            f"prompt file not written for {r.lens}-{r.check_id}"
        )
        assert r.prompt_path.name.endswith("_prompt.md")
        assert r.jsonl_path.name.endswith(".jsonl")
        # Prompt + JSONL names share the lens-checkid prefix.
        assert r.prompt_path.name.startswith(f"{r.lens}-{r.check_id}")
        assert r.jsonl_path.name.startswith(f"{r.lens}-{r.check_id}")


def test_returns_canonical_lens_order(tmp_path: Path) -> None:
    """Results are scope first, grounding second, structural third —
    matches the default-mode renderer's ordering so the orchestrator's
    fan-out logic stays simple."""
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text=_sample_artifact(),
        pointers=_sample_pointers(),
        recent_diff="",
        output_dir=tmp_path,
    )
    lenses = [r.lens for r in results]
    # All scopes precede grounding, which precedes structural.
    assert lenses == sorted(
        lenses, key=lambda L: ("scope", "grounding", "structural").index(L)
    )


def test_rendered_prompts_have_check_fields_substituted(
    tmp_path: Path,
) -> None:
    """The 4 `{check_*}` placeholders should be replaced with the
    YAML-loaded values, not left as literal placeholders."""
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text=_sample_artifact(),
        pointers=_sample_pointers(),
        recent_diff="",
        output_dir=tmp_path,
    )
    for r in results:
        text = r.prompt_path.read_text(encoding="utf-8")
        for placeholder in (
            "{check_description}",
            "{check_trigger_heuristic}",
            "{check_remediation_language}",
            "{check_example_failure_mode}",
        ):
            assert placeholder not in text, (
                f"{r.prompt_path.name} still contains {placeholder} — "
                "substitution did not run"
            )


def test_rendered_scope_prompts_contain_artifact_text(tmp_path: Path) -> None:
    artifact = _sample_artifact()
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text=artifact,
        pointers=_sample_pointers(),
        recent_diff="",
        output_dir=tmp_path,
    )
    for r in results:
        if r.lens != "scope":
            continue
        text = r.prompt_path.read_text(encoding="utf-8")
        assert "We see +12pp lift in conversion this week." in text


def test_rendered_grounding_prompts_DO_NOT_contain_artifact_text(
    tmp_path: Path,
) -> None:
    """Convergent-blind-spot guard: even after rendering, grounding
    prompts must not carry the draft body. The fragment-level test
    guards the template; this test guards the render path."""
    # Use a distinctive marker that won't appear in any pointer claim.
    marker = "ZZZTHISISDISTINCTIVEMARKERZZZ"
    artifact = f"# Header\n\n{marker}\n\n## body\n"
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text=artifact,
        pointers=_sample_pointers(),
        recent_diff="",
        output_dir=tmp_path,
    )
    for r in results:
        if r.lens != "grounding":
            continue
        text = r.prompt_path.read_text(encoding="utf-8")
        assert marker not in text, (
            f"{r.prompt_path.name} leaked draft text into the grounding "
            "prompt — convergent-blind-spot violation"
        )


def test_rendered_grounding_prompts_contain_pointer_data(
    tmp_path: Path,
) -> None:
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text=_sample_artifact(),
        pointers=_sample_pointers(),
        recent_diff="",
        output_dir=tmp_path,
    )
    for r in results:
        if r.lens != "grounding":
            continue
        text = r.prompt_path.read_text(encoding="utf-8")
        # The pointer_value should be substituted into the prompt.
        assert "state.yaml.foo.bar" in text or "some-concept" in text


def test_rendered_structural_prompts_contain_recent_diff(
    tmp_path: Path,
) -> None:
    diff = "DIFF-MARKER-XYZ\n+ new line"
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text=_sample_artifact(),
        pointers=_sample_pointers(),
        recent_diff=diff,
        output_dir=tmp_path,
    )
    for r in results:
        if r.lens != "structural":
            continue
        text = r.prompt_path.read_text(encoding="utf-8")
        assert "DIFF-MARKER-XYZ" in text


def test_rendered_prompts_mention_only_their_own_check_id(
    tmp_path: Path,
) -> None:
    """No cross-check contamination: a scope-drift fragment must not
    reference sibling check ids like 'mechanism-as-clock' or
    'confound-inventory-missing'. The 'only valid value' instruction in
    each fragment is what makes the per-check subagent stay in its lane;
    if a sibling check id leaks in, the subagent could legitimately
    emit findings for it and overlap with the sibling subagent.

    We tolerate the fragment's own check_id appearing many times, and
    we tolerate the criteria-loaded fields (description / heuristic /
    remediation / example) containing arbitrary prose. The test scopes
    to the FRAGMENT TEMPLATE region only — i.e. the prompt text minus
    the substituted check fields — so domain-neutral check descriptions
    that happen to mention another concept don't fire the assertion.
    """
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text="",  # don't seed the artifact with check ids
        pointers=[],  # don't seed pointers either
        recent_diff="",
        output_dir=tmp_path,
    )
    all_check_ids: set[str] = set()
    for ids in DEEP_FANOUT.values():
        all_check_ids.update(ids)

    for r in results:
        # Re-load the fragment TEMPLATE rather than the rendered prompt
        # — substituted check-field prose is allowed to mention other
        # concepts (e.g. the maturity check's description naturally
        # references conversion windows). Only the static fragment
        # shell is held to the "no sibling check ids" invariant.
        template_text = (
            CHECKS_DIR / f"{r.lens}-{r.check_id}.md"
        ).read_text(encoding="utf-8")
        siblings = all_check_ids - {r.check_id}
        for sibling in siblings:
            # Match `sibling` as a whole token to avoid false positives
            # from substring overlap (e.g. 'stale-references' is a
            # substring of nothing, but be defensive).
            pattern = r"\b" + re.escape(sibling) + r"\b"
            assert not re.search(pattern, template_text), (
                f"{r.prompt_path.name} template mentions sibling check "
                f"id {sibling!r} — cross-contamination risk"
            )


def test_render_time_scales_linearly_with_check_count(
    tmp_path: Path,
) -> None:
    """Sanity check: rendering 16 prompts is fast (well under a second).
    A regression to O(n²) somewhere (e.g. re-reading criteria.md per
    check from disk) would blow this budget."""
    start = time.perf_counter()
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text=_sample_artifact(),
        pointers=_sample_pointers(),
        recent_diff="",
        output_dir=tmp_path,
    )
    elapsed = time.perf_counter() - start
    assert len(results) == 16
    # 1.0s is wildly generous — actual is sub-100ms on a modern laptop —
    # but leaves headroom for CI variance.
    assert elapsed < 1.0, (
        f"render_per_check_prompts took {elapsed:.3f}s for 16 checks; "
        "expected sub-second. Possible O(n²) regression."
    )


def test_jsonl_paths_match_expected_naming(tmp_path: Path) -> None:
    """The orchestrator (post-Wave-5) matches subagent JSONL outputs to
    their prompts by filename convention. Lock the convention here so a
    later refactor doesn't break the aggregator's fan-in."""
    results = render_per_check_prompts(
        criteria_path=DEFAULT_CRITERIA,
        artifact_text=_sample_artifact(),
        pointers=_sample_pointers(),
        recent_diff="",
        output_dir=tmp_path,
    )
    for r in results:
        assert r.jsonl_path == tmp_path / f"{r.lens}-{r.check_id}.jsonl"
        assert r.prompt_path == (
            tmp_path / f"{r.lens}-{r.check_id}_prompt.md"
        )


def test_per_check_prompt_dataclass_is_frozen() -> None:
    """The returned records carry path identity; mutating them after
    return would diverge from the on-disk state. Enforce immutability
    so callers don't surprise themselves."""
    rec = PerCheckPrompt(
        lens="scope",
        check_id="scope-drift",
        prompt_path=Path("/tmp/a_prompt.md"),
        jsonl_path=Path("/tmp/a.jsonl"),
    )
    with pytest.raises(Exception):
        rec.lens = "grounding"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# render_output cost note for mode="deep"
# ---------------------------------------------------------------------------


def _minimal_run_record(mode: str) -> dict:
    return {
        "record_type": "run_record",
        "run_id": "11111111-2222-3333-4444-555555555555",
        "started_at": "2026-05-29T10:00:00Z",
        "completed_at": "2026-05-29T10:01:23Z",
        "mode": mode,
        "criteria_source": "default-criteria.md",
        "artifact_path": "/tmp/sample.md",
    }


def test_render_output_emits_cost_note_in_deep_mode() -> None:
    """The PRD requires that when --deep is used, the rendered output
    ends with `Note: --deep mode used; ~5x token cost vs default.`
    AFTER the pre-mortem prompt. This test pins the contract; if
    render_output doesn't yet handle mode=deep, this test fails and the
    minimal fix lands in render_output.
    """
    rendered = render(
        findings=[],
        coverage=[],
        run_record=_minimal_run_record(mode="deep"),
    )
    assert "Note: --deep mode used; ~5x token cost vs default." in rendered
    # And it must be AFTER the pre-mortem prompt, not before.
    note_idx = rendered.index(
        "Note: --deep mode used; ~5x token cost vs default."
    )
    premortem_idx = rendered.index("Imagine this artifact is proven wrong")
    assert note_idx > premortem_idx, (
        "cost note must appear after the pre-mortem prompt"
    )


def test_render_output_omits_cost_note_in_default_mode() -> None:
    """The cost note is a `--deep`-only signal. Default-mode runs must
    not get it (no false signal that the run was more expensive than it
    was)."""
    rendered = render(
        findings=[],
        coverage=[],
        run_record=_minimal_run_record(mode="default"),
    )
    assert (
        "Note: --deep mode used; ~5x token cost vs default." not in rendered
    )
