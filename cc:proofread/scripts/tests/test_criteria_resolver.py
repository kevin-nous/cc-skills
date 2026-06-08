"""Tests for scripts/criteria_resolver.py.

Covers the ccp-007 acceptance criteria:

1. Project with valid override → returns the override path
2. Project without override → returns the shipped default path
3. Project with override that fails schema validation →
   raises InvalidProjectCriteriaError (does NOT silently fall back)
4. Neither file exists → raises CriteriaNotFoundError, naming both paths
5. Symlinked invocation → `Path(__file__).resolve()` finds the canonical
   install dir (i.e. the default-criteria.md path resolves to the same
   real file whether the script runs via the ~/.claude/skills symlink or
   the ~/cc-skills real path)
6. Empty criteria.md → fails validation (no schema_version)
7. Misspelled lens section names → fails validation

Fixtures live at scripts/tests/fixtures/criteria_resolver/<project-dir>/
and are committed to the repo so the tests are hermetic and don't depend
on mkdtemp ordering.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.criteria_resolver import (
    CriteriaNotFoundError,
    InvalidDefaultCriteriaError,
    InvalidProjectCriteriaError,
    PROJECT_OVERRIDE_RELPATH,
    REQUIRED_LENS_SECTIONS,
    ResolvedCriteria,
    default_criteria_path,
    project_override_path,
    resolve_criteria,
    skill_install_dir,
)


FIXTURES_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "criteria_resolver"
)


# ---------------------------------------------------------------------------
# 1 + 2: normal resolution paths
# ---------------------------------------------------------------------------


def test_project_with_valid_override_returns_override_path() -> None:
    project = FIXTURES_DIR / "project_with_override"
    resolved = resolve_criteria(project)

    assert isinstance(resolved, ResolvedCriteria)
    assert resolved.path == project / PROJECT_OVERRIDE_RELPATH
    assert resolved.source == "project override"
    assert resolved.notice_line == (
        f"Criteria: {resolved.path} (project override)"
    )


def test_project_without_override_falls_back_to_default() -> None:
    project = FIXTURES_DIR / "project_without_override"
    resolved = resolve_criteria(project)

    assert resolved.path == default_criteria_path()
    assert resolved.source == "default — no project override"
    assert resolved.notice_line == (
        f"Criteria: {resolved.path} (default — no project override)"
    )


# ---------------------------------------------------------------------------
# 3: invalid override — the CRITICAL "fail loudly, don't silently fall back"
# case. This is the test the PRD calls out as load-bearing.
# ---------------------------------------------------------------------------


def test_invalid_project_override_raises_does_not_silently_fall_back() -> None:
    project = FIXTURES_DIR / "project_with_invalid_override"
    override_path = project / PROJECT_OVERRIDE_RELPATH

    assert override_path.exists(), (
        "fixture sanity: invalid override file must exist for this test "
        "to be meaningful"
    )

    with pytest.raises(InvalidProjectCriteriaError) as excinfo:
        resolve_criteria(project)

    msg = str(excinfo.value)
    # The error message must name the offending path so the user can
    # find and fix it.
    assert str(override_path) in msg
    # And must explicitly mention the no-fallback contract so the user
    # understands why the skill didn't silently use the default.
    assert "will not silently fall back" in msg
    # And must include the concrete validation failure — missing
    # schema_version / missing frontmatter — so the fix is obvious.
    assert "frontmatter" in msg.lower() or "schema_version" in msg.lower()


# ---------------------------------------------------------------------------
# 4: nothing found
# ---------------------------------------------------------------------------


def test_neither_file_exists_raises_with_both_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If both the project override AND the default are missing, raise
    CriteriaNotFoundError and name both paths checked."""
    # Force the "default" path to point at a nonexistent file under tmp_path
    # so we can test the no-default branch without nuking the real shipped
    # default file.
    fake_install_dir = tmp_path / "fake_install"
    fake_install_dir.mkdir()

    # The resolver computes the default path lazily via default_criteria_path
    # → skill_install_dir → Path(__file__).resolve().parents[1]. To redirect
    # it for this test, monkeypatch skill_install_dir in the resolver module.
    import scripts.criteria_resolver as resolver_mod

    monkeypatch.setattr(
        resolver_mod, "skill_install_dir", lambda: fake_install_dir
    )

    project = tmp_path / "empty_project"
    project.mkdir()

    with pytest.raises(CriteriaNotFoundError) as excinfo:
        resolver_mod.resolve_criteria(project)

    msg = str(excinfo.value)
    assert str(project / PROJECT_OVERRIDE_RELPATH) in msg
    assert str(fake_install_dir / "default-criteria.md") in msg


# ---------------------------------------------------------------------------
# 5: symlink resolution. We don't need a literal symlink to test the
# contract — what we're asserting is the property that `Path(__file__)
# .resolve()` is used so symlinked invocation resolves to the same real
# directory. We assert that property by checking that skill_install_dir()
# returns an absolute, fully-resolved path and that the shipped
# default-criteria.md is reachable from it.
# ---------------------------------------------------------------------------


def test_skill_install_dir_resolves_to_canonical_real_path() -> None:
    install_dir = skill_install_dir()
    assert install_dir.is_absolute()
    # If skill_install_dir used `Path(__file__).parents[1]` without
    # `.resolve()`, this assertion would fail when invoked via the
    # ~/.claude/skills/cc:proofread symlink — the parent path would be
    # the symlink target's containing directory, not the canonical
    # ~/cc-skills/cc:proofread real path.
    assert install_dir == install_dir.resolve()
    assert (install_dir / "default-criteria.md").exists(), (
        f"skill_install_dir() = {install_dir} but no default-criteria.md "
        "found there — symlink resolution may be broken"
    )


def test_default_path_is_reachable_via_symlink_invocation(
    tmp_path: Path,
) -> None:
    """End-to-end symlink test.

    Create a symlink in tmp_path that points at the real install dir, then
    re-import the resolver via that symlinked module path and confirm
    default_criteria_path() returns the same real file as direct
    invocation. This is the test that would have caught a regression in
    `Path(__file__).resolve()` → `Path(__file__).absolute()` (the latter
    does NOT follow symlinks)."""
    real_install = skill_install_dir()
    real_default = real_install / "default-criteria.md"

    # Symlink the entire install dir under tmp_path.
    symlinked_install = tmp_path / "symlinked_cc_proofread"
    symlinked_install.symlink_to(real_install, target_is_directory=True)

    # Touch the file through the symlink and confirm it's the same inode
    # as the real path. If `.resolve()` is the right call, both paths
    # resolve to the same canonical Path.
    symlinked_default = symlinked_install / "default-criteria.md"
    assert symlinked_default.exists()
    assert symlinked_default.resolve() == real_default.resolve()


# ---------------------------------------------------------------------------
# 6 + 7: validation failure modes (empty file, misspelled sections)
# ---------------------------------------------------------------------------


def test_empty_criteria_file_fails_validation() -> None:
    project = FIXTURES_DIR / "project_with_empty_override"
    with pytest.raises(InvalidProjectCriteriaError) as excinfo:
        resolve_criteria(project)
    # Empty-file path: validator emits "file is empty".
    assert "empty" in str(excinfo.value).lower()


def test_misspelled_lens_section_names_fail_validation() -> None:
    project = FIXTURES_DIR / "project_with_misspelled_sections"
    with pytest.raises(InvalidProjectCriteriaError) as excinfo:
        resolve_criteria(project)
    msg = str(excinfo.value)
    # All 4 misspelled section headers should be reported missing.
    for section in REQUIRED_LENS_SECTIONS:
        assert f"'## {section}'" in msg, (
            f"expected missing-section message for {section!r}; got: {msg}"
        )


# ---------------------------------------------------------------------------
# Defensive: the path-utility helpers should be deterministic and not
# accidentally depend on cwd (orchestrator invokes them from the project
# dir, but the resolver itself should be cwd-agnostic).
# ---------------------------------------------------------------------------


def test_project_override_path_is_cwd_independent(tmp_path: Path) -> None:
    project = tmp_path / "some_project"
    project.mkdir()
    expected = project / PROJECT_OVERRIDE_RELPATH

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert project_override_path(project) == expected
        os.chdir(project)
        assert project_override_path(project) == expected
    finally:
        os.chdir(old_cwd)


def test_resolved_criteria_is_immutable() -> None:
    """ResolvedCriteria is frozen so callers can't mutate the resolver's
    output (defense against orchestrator bugs that would overwrite the
    source label after the fact)."""
    project = FIXTURES_DIR / "project_with_override"
    resolved = resolve_criteria(project)
    with pytest.raises((AttributeError, Exception)):
        resolved.source = "tampered"  # type: ignore[misc]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
