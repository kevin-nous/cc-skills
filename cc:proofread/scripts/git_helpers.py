"""Git inspection helpers for the structural auditor (ccp-010).

These helpers exist to power the structural lens's `late-added-callouts`
meta-flag: if a recent commit added a top-of-page callout/banner without
re-touching the body, the structural prompt surfaces an advisory
finding pointing the reader at the relevant vault pattern doc.

The helpers are READ-ONLY. They:

- Never write to disk.
- Never raise on "not a git repo" / "file untracked" / "no recent
  commit" — they return the empty string / False instead. The structural
  prompt then silently skips the meta-flag check.

Public API:

    recent_diff_for(artifact_path) -> str
    was_callout_added_in_last_commit(artifact_path) -> bool
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


# -- Heuristic constants ----------------------------------------------------

# Window into the artifact body within which a callout has to land to
# count as "top-level". Per ccp-010's Technical Approach: the first 200
# chars of the artifact (or of the generated zone, if marked).
CALLOUT_WINDOW_CHARS = 200

# generated marker tokens (used by some downstream tooling to
# delimit machine-rewritten regions). If present, the callout window is
# anchored to the start of that zone instead of the literal file start.
GENERATED_MARKER_START = "generated:start"

# Pattern matching a Markdown blockquote-callout opener: a `>` followed
# by `**...**`. Lifted out so the test suite can re-use it as the truth
# table for "what counts as a callout".
CALLOUT_OPENER_RE = re.compile(r"^>\s*\*\*[^*]+\*\*", re.MULTILINE)

# A diff hunk line is "added" if it begins with `+` (and is NOT the
# `+++ b/...` header line). Captured here so tests can sanity-check.
DIFF_ADDED_LINE_RE = re.compile(r"^\+(?!\+\+ )", re.MULTILINE)


# -- recent_diff_for --------------------------------------------------------


def recent_diff_for(artifact_path: Path) -> str:
    """Return the diff of the most recent commit that added or modified
    `artifact_path`, as a single string. Empty string on any failure
    mode (not a git repo, file untracked, no commits touching it, git
    not installed). Never raises.

    Uses ``git log -1 --diff-filter=AM -p -- <path>`` so renames and
    deletions don't show up — we only want add/modify commits, since
    they're what matters for the late-added-callout heuristic.
    """
    artifact_path = Path(artifact_path)
    if not artifact_path.exists():
        return ""

    # Run from the artifact's parent so git auto-discovers the enclosing
    # repo. Pass the artifact as an absolute path so the pathspec is
    # unambiguous regardless of cwd.
    try:
        result = subprocess.run(
            [
                "git", "log", "-1",
                "--diff-filter=AM",
                "-p",
                "--", str(artifact_path.resolve()),
            ],
            capture_output=True,
            text=True,
            cwd=str(artifact_path.parent),
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # git binary missing, or hung — degrade silently.
        return ""

    if result.returncode != 0:
        return ""
    return result.stdout or ""


# -- was_callout_added_in_last_commit --------------------------------------


def _file_callout_window(artifact_text: str) -> str:
    """Return the slice of `artifact_text` in which a callout has to
    land to count as 'top-level'.

    If a `generated:start` marker is present, anchor the window to
    the first character after it. Otherwise anchor to the start of the
    file. Window length is CALLOUT_WINDOW_CHARS.
    """
    start = 0
    idx = artifact_text.find(GENERATED_MARKER_START)
    if idx >= 0:
        # Skip past the marker line itself so the window covers what
        # follows it, not the marker.
        line_end = artifact_text.find("\n", idx)
        start = line_end + 1 if line_end >= 0 else idx + len(GENERATED_MARKER_START)
    return artifact_text[start : start + CALLOUT_WINDOW_CHARS]


def _added_lines_from_diff(diff_text: str) -> list[str]:
    """Extract the actual added content (without the leading `+`) from a
    unified diff. Strips the `+++ b/...` header lines."""
    added: list[str] = []
    for raw in diff_text.splitlines():
        if raw.startswith("+++"):
            continue
        if raw.startswith("+"):
            added.append(raw[1:])
    return added


def was_callout_added_in_last_commit(artifact_path: Path) -> bool:
    """True if the most recent commit modifying `artifact_path` added a
    top-of-page blockquote callout (`> **...**`) within the callout
    window of the current file.

    Heuristic — explicitly not a structural-equivalence check:

    1. Read the current artifact text.
    2. Identify any callout opener (`> **Title**`) lines in the callout
       window of the current file.
    3. Run `recent_diff_for(artifact_path)` to fetch the last
       add/modify commit's diff.
    4. Return True iff at least one of those callout opener lines
       appears verbatim in the diff's added-content set.

    Returns False on every failure mode (no diff available, no callout
    in the current file, callout exists but wasn't in the last commit).
    Never raises.
    """
    artifact_path = Path(artifact_path)
    if not artifact_path.exists():
        return False

    try:
        artifact_text = artifact_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    window = _file_callout_window(artifact_text)
    current_callouts = CALLOUT_OPENER_RE.findall(window)
    if not current_callouts:
        return False

    diff = recent_diff_for(artifact_path)
    if not diff:
        return False

    added_lines = _added_lines_from_diff(diff)
    if not added_lines:
        return False

    # The regex captures the OPENER (`> **Title**`) — not necessarily
    # the full line (the line in the file is typically
    # `> **Title**: some content`). So we check whether any added line
    # CONTAINS a callout opener as a prefix (after stripping).
    for added_line in added_lines:
        stripped = added_line.strip()
        if not stripped:
            continue
        for opener in current_callouts:
            if stripped.startswith(opener.strip()):
                return True
    return False
