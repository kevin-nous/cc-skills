"""Unit tests for `scripts.git_helpers` — the read-only git inspection
helpers used by the structural auditor's late-added-callouts meta-flag.

Both helpers MUST degrade silently (empty string / False) when git is
unavailable, the path isn't in a repo, or there's no recent commit
modifying it. None of them may raise.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.git_helpers import (  # noqa: E402
    CALLOUT_OPENER_RE,
    CALLOUT_WINDOW_CHARS,
    recent_diff_for,
    was_callout_added_in_last_commit,
)


# ---------------------------------------------------------------------------
# Helpers — set up a real git repo in tmp_path, commit files, etc.
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git subcommand in `cwd`. Configures author/email locally so
    `git commit` doesn't fail on CI machines that lack global config."""
    return subprocess.run(
        ["git", *args],
        capture_output=True, text=True, cwd=str(cwd), check=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git("init", "--initial-branch=main", "-q", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test User", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)


def _commit(repo: Path, msg: str) -> None:
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", msg, "-q", cwd=repo)


# ---------------------------------------------------------------------------
# recent_diff_for — happy path
# ---------------------------------------------------------------------------


def test_recent_diff_for_returns_diff_when_file_in_repo(
    tmp_path: Path,
) -> None:
    """Happy path: file has been committed once → diff contains the
    add-content."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    artifact = repo / "doc.md"
    artifact.write_text("hello world\nline two\n", encoding="utf-8")
    _commit(repo, "initial: doc")

    diff = recent_diff_for(artifact)
    assert diff != ""
    # The diff should include the added content lines.
    assert "+hello world" in diff
    assert "+line two" in diff


def test_recent_diff_for_returns_diff_from_most_recent_commit_only(
    tmp_path: Path,
) -> None:
    """Multiple commits → recent_diff_for returns ONLY the last one."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    artifact = repo / "doc.md"
    artifact.write_text("v1 content\n", encoding="utf-8")
    _commit(repo, "first commit")
    artifact.write_text("v1 content\nv2 addition\n", encoding="utf-8")
    _commit(repo, "second commit — append a line")

    diff = recent_diff_for(artifact)
    # Most recent diff shows the added line, not the original file body.
    assert "+v2 addition" in diff
    # The added-marker `+v1 content` only appears in the FIRST commit;
    # `-1` limits to the most recent. (Context lines look like ` v1
    # content` with a leading space, NOT `+`.)
    assert "+v1 content" not in diff


# ---------------------------------------------------------------------------
# recent_diff_for — failure modes return empty string, never raise
# ---------------------------------------------------------------------------


def test_recent_diff_for_file_not_in_git_repo_returns_empty(
    tmp_path: Path,
) -> None:
    """File exists but no surrounding git repo → empty string, no crash."""
    artifact = tmp_path / "loose.md"
    artifact.write_text("not under git\n", encoding="utf-8")
    assert recent_diff_for(artifact) == ""


def test_recent_diff_for_file_untracked_in_repo_returns_empty(
    tmp_path: Path,
) -> None:
    """File exists in a git repo but was never committed → empty string."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    # Make at least one commit on a different file so `git log` doesn't
    # bail because the repo has no commits at all.
    (repo / "README.md").write_text("placeholder\n", encoding="utf-8")
    _commit(repo, "placeholder")

    artifact = repo / "uncommitted.md"
    artifact.write_text("never committed\n", encoding="utf-8")
    assert recent_diff_for(artifact) == ""


def test_recent_diff_for_nonexistent_file_returns_empty(
    tmp_path: Path,
) -> None:
    assert recent_diff_for(tmp_path / "does-not-exist.md") == ""


# ---------------------------------------------------------------------------
# was_callout_added_in_last_commit — happy path: callout added in last commit
# ---------------------------------------------------------------------------


def test_was_callout_added_returns_true_when_callout_in_last_commit(
    tmp_path: Path,
) -> None:
    """File first committed without a callout; second commit prepends a
    `> **Quick read** ...` callout → True."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    artifact = repo / "writeup.md"
    body = "# Title\n\nBody paragraph that says some normal prose.\n"
    artifact.write_text(body, encoding="utf-8")
    _commit(repo, "initial body")

    # Prepend a callout at the top.
    new_text = (
        "> **Quick read**: V2 won decisively over the matured cohort.\n\n"
        + body
    )
    artifact.write_text(new_text, encoding="utf-8")
    _commit(repo, "add quick-read callout")

    assert was_callout_added_in_last_commit(artifact) is True


def test_was_callout_added_returns_false_when_only_body_changed(
    tmp_path: Path,
) -> None:
    """Last commit modifies the body but does not add a callout → False.
    This is the explicit false-positive guard from the issue's Test Plan."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    artifact = repo / "writeup.md"
    artifact.write_text(
        "# Title\n\nOriginal body paragraph.\n", encoding="utf-8",
    )
    _commit(repo, "initial body")

    artifact.write_text(
        "# Title\n\nOriginal body paragraph.\nA second sentence appended.\n",
        encoding="utf-8",
    )
    _commit(repo, "extend body")

    assert was_callout_added_in_last_commit(artifact) is False


def test_was_callout_added_returns_false_when_no_callout_in_current_file(
    tmp_path: Path,
) -> None:
    """File has no `> **...**` opener within the callout window at all
    → False, regardless of commit history."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    artifact = repo / "writeup.md"
    artifact.write_text(
        "# Title\n\nBody with no callout markers anywhere.\n",
        encoding="utf-8",
    )
    _commit(repo, "no callout here")
    assert was_callout_added_in_last_commit(artifact) is False


def test_was_callout_added_returns_false_when_not_in_git_repo(
    tmp_path: Path,
) -> None:
    """File has a callout but no surrounding git repo → False, no crash."""
    artifact = tmp_path / "loose.md"
    artifact.write_text(
        "> **Quick read**: a callout but no git repo.\n\n# Title\n",
        encoding="utf-8",
    )
    assert was_callout_added_in_last_commit(artifact) is False


def test_was_callout_added_returns_false_when_callout_is_old(
    tmp_path: Path,
) -> None:
    """Callout exists in the file from an earlier commit; the most recent
    commit modified body only → False (not added in LAST commit)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    artifact = repo / "writeup.md"
    artifact.write_text(
        "> **Quick read**: callout that has been here all along.\n\n"
        "# Title\n\nBody v1.\n",
        encoding="utf-8",
    )
    _commit(repo, "initial with callout")
    # Now modify only the body.
    artifact.write_text(
        "> **Quick read**: callout that has been here all along.\n\n"
        "# Title\n\nBody v2 — modified.\n",
        encoding="utf-8",
    )
    _commit(repo, "modify body only")

    assert was_callout_added_in_last_commit(artifact) is False


def test_was_callout_added_returns_true_when_file_is_brand_new_with_callout(
    tmp_path: Path,
) -> None:
    """Edge case from issue spec: file recently added with a top-level
    callout → True (the first commit is also the last commit, and the
    callout WAS added in it)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    # Make a placeholder commit so this isn't the only commit (avoids
    # the empty-repo edge case in git).
    (repo / "README.md").write_text("placeholder\n", encoding="utf-8")
    _commit(repo, "placeholder")

    artifact = repo / "fresh.md"
    artifact.write_text(
        "> **Quick read**: brand-new doc with a callout from line 1.\n\n"
        "# Title\n\nBody content.\n",
        encoding="utf-8",
    )
    _commit(repo, "add fresh.md with callout")
    assert was_callout_added_in_last_commit(artifact) is True


# ---------------------------------------------------------------------------
# Internal heuristic sanity
# ---------------------------------------------------------------------------


def test_callout_window_chars_is_advertised_size() -> None:
    """The 200-char window constant is referenced in the prompt; if it
    drifts, the prompt's documented behaviour becomes a lie. Pin it."""
    assert CALLOUT_WINDOW_CHARS == 200


def test_callout_opener_regex_matches_canonical_form() -> None:
    """Sanity-check that the regex matches the load-bearing form
    `> **Title**` at the start of a line. Includes a non-match for
    `> regular blockquote` (no bold)."""
    matches = CALLOUT_OPENER_RE.findall(
        "> **Quick read**: V2 won.\n> a normal blockquote\n"
    )
    assert len(matches) == 1
    assert matches[0].startswith("> **Quick read**")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
