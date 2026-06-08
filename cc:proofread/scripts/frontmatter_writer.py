"""ccp-014 — `proofread-passed-<YYYY-MM-DD>` frontmatter tag writer.

On a clean run against a vault finding, cc:proofread appends a single
audit tag to the artifact's frontmatter `tags:` array. This is the ONLY
write cc:proofread performs against the audited artifact — every other
output path goes through /tmp/cc-proofread-<run-id>/ JSONL files.

The tag convention mirrors the existing `codex-review` tag used across
the project vault (see e.g.
`vault/knowledge/2026-04-13-immediate-proposals-segment-diagnostic-lessons.md`).

Semantics

- Tag shape: `proofread-passed-YYYY-MM-DD` (ISO date suffix, kebab-case).
- Single-write field, not multi-pass log. If a previous
  `proofread-passed-<old-date>` tag exists, it is REPLACED with the new
  date (not appended). Defensive: if multiple stale entries exist (e.g.
  hand-edited), ALL are replaced with a single new tag.
- Body content of the artifact is never touched. Only the frontmatter
  `tags:` array is modified.
- Idempotency: writing the same date twice is a successful no-content-
  change — the result records the previous tag equal to the new tag.

Eligibility (ALL must hold to perform a write):

1. `artifact_path` matches `**/vault/knowledge/*.md` OR `**/findings/*.md`.
2. The artifact's frontmatter has `type: finding`.
3. The provided `findings` list contains ZERO records with
   `severity == "blocking"` (advisory findings do NOT gate the tag).
4. `no_tag` is False (the `--no-tag` user opt-out).
5. The artifact is readable and writable.
6. (ccp-021) If `lens_coverage` is provided, NO coverage record carries
   the ccp-020 synthetic-silent-failure shape (`error:` set AND the
   `checks_attempted == -1` sentinel that ccp-020's aggregator emits
   when a lens's whole output was schema-rejected). A silent failure
   means the audit didn't actually land for that lens — the
   aggregated finding list is misleadingly clean. The tag would
   represent a green-checkmark on an audit that half-failed.

Any of these failing produces a `WriteResult` with a specific
`skipped_*` / `failed_*` status; no write occurs, and the original file
is unchanged.

Alternative path: passing `allow_tag_on_partial_failure=True` downgrades
the silent-failure refusal to a `proofread-attempted-<date>` tag instead
(audit trail preserved without overclaiming). NOT default — surface via
a `--allow-tag-on-partial-failure` orchestrator flag if/when a user
needs the audit-trail-but-partial-coverage shape. The default behaviour
remains refusal.

Atomic-write contract

The write path is read → modify in-memory → write to a sibling tmp file
→ `os.replace()`. `os.replace` is atomic on POSIX; if it raises, the
original file is unchanged. The tmp file is cleaned up on the failure
path. We do NOT write through a system temp dir on a different
filesystem — that would defeat atomic-rename and risks a partial copy
across filesystems.

Library use

    from scripts.frontmatter_writer import write_proofread_tag
    result = write_proofread_tag(
        artifact_path=Path("vault/knowledge/2026-05-29-foo.md"),
        findings=findings_dicts,        # list[dict]; checked for blocking
        no_tag=False,
    )
    if result.status == "written":
        print(f"Tagged artifact: {result.tag_added}")

The orchestrator wires this in after aggregation (post-Wave-5 work,
NOT done in this module).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional

try:
    import frontmatter
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "python-frontmatter is required for scripts.frontmatter_writer. "
        "Install with: python3 -m pip install --user "
        "--break-system-packages python-frontmatter"
    ) from exc


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The audit tag prefix. The full tag is `proofread-passed-YYYY-MM-DD`.
_TAG_PREFIX = "proofread-passed-"

# ccp-021 audit-trail downgrade prefix. Used when
# `allow_tag_on_partial_failure=True` and a silent lens failure was
# detected — tag shape is `proofread-attempted-YYYY-MM-DD` so the audit
# log records the run without overclaiming clean coverage.
_ATTEMPTED_PREFIX = "proofread-attempted-"

# Matches any existing proofread-passed-<ISO date> entry. We use this
# both to detect a stale entry to replace and to defend against multiple
# stale entries (e.g. from hand-edited tags arrays).
_TAG_RE = re.compile(r"^proofread-passed-\d{4}-\d{2}-\d{2}$")

# Matches any existing proofread-attempted-<ISO date> entry. Same
# stale-replacement logic as _TAG_RE — we collapse old attempted tags
# to one current entry rather than appending.
_ATTEMPTED_RE = re.compile(r"^proofread-attempted-\d{4}-\d{2}-\d{2}$")

# Sentinel value emitted by the ccp-020 aggregator's synthetic coverage
# record (`_make_synthetic` writes -1 for `checks_attempted` and
# `claims_examined`). No legitimate path in the orchestrator emits a
# negative count — this is the contractual discriminator that
# distinguishes a SILENT-LENS-FAILURE coverage record from a
# legitimate-but-failed one (e.g. mechanical-process-exit error or
# dry-run stub, both of which use 0 or a positive integer).
_SILENT_FAILURE_SENTINEL = -1

# Vault-finding path discriminator. Accepts both `vault/knowledge/<slug>.md`
# (the unified-vault layout since 2026-05-11) AND the legacy
# `findings/<slug>.md` layout for backward compat. Anywhere on the path
# qualifies — supports worktrees and symlinked checkouts.
_VAULT_PATH_RE = re.compile(
    r"(^|/)(vault/knowledge|findings)/[^/]+\.md$"
)


Status = Literal[
    "written",
    "skipped_no_tag",
    "skipped_not_vault_finding",
    "skipped_blocking_findings_present",
    "skipped_silent_lens_failure",
    "skipped_artifact_unreadable",
    "failed_atomic_write_aborted",
]


@dataclass
class WriteResult:
    """Result of a `write_proofread_tag` call.

    `status` is the single source of truth for what happened.
    `tag_added` is the full tag when status is `written` — either
    `proofread-passed-<date>` (clean run) or, when
    `allow_tag_on_partial_failure=True` combined with a detected silent
    lens failure, `proofread-attempted-<date>` (audit-trail downgrade).
    None on any skip / fail path.
    `previous_tag` records the prior `proofread-passed-<date>` (or
    `proofread-attempted-<date>`) entry that was REPLACED, if any —
    None when no prior tag existed.
    `error` carries the underlying exception message for failure paths
    and the diagnostic message for the silent-lens-failure refusal;
    None on the clean-write success path.
    """

    status: Status
    tag_added: Optional[str] = None
    previous_tag: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_vault_finding_path(artifact_path: Path) -> bool:
    """Path discriminator. Frontmatter `type: finding` is checked
    separately after parsing — both must hold."""
    # Use forward-slash-normalised string for cross-platform matching.
    return bool(_VAULT_PATH_RE.search(str(artifact_path).replace("\\", "/")))


def _has_blocking_finding(findings: Iterable[dict] | None) -> bool:
    if not findings:
        return False
    for f in findings:
        if f.get("severity") == "blocking":
            return True
    return False


def _silent_failed_lenses(
    lens_coverage: Iterable[dict] | None,
) -> list[dict]:
    """Return the subset of coverage records that match the ccp-020
    synthetic-silent-failure shape.

    Discriminator: `error:` field present + non-empty AND
    `checks_attempted == -1` (the sentinel `_make_synthetic` writes; no
    legitimate path emits a negative count).

    Why the sentinel matters: the orchestrator also writes `error:` on
    DRY-RUN stubs and on mechanical-prepass / discovery process-exit
    failures, but those use `checks_attempted >= 0`. Keying off the
    sentinel keeps the tag-write working in dry-run mode while still
    refusing on the bank-details-style silent-rejection case.
    """
    if not lens_coverage:
        return []
    out: list[dict] = []
    for c in lens_coverage:
        if not isinstance(c, dict):
            continue
        err = c.get("error")
        if not (isinstance(err, str) and err):
            continue
        if c.get("checks_attempted") != _SILENT_FAILURE_SENTINEL:
            continue
        out.append(c)
    return out


def _today_utc() -> date_cls:
    return datetime.now(timezone.utc).date()


def _format_tag(d: date_cls) -> str:
    return f"{_TAG_PREFIX}{d.isoformat()}"


def _format_attempted_tag(d: date_cls) -> str:
    return f"{_ATTEMPTED_PREFIX}{d.isoformat()}"


def _atomic_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically.

    Strategy: write to a sibling tmp file on the same filesystem, fsync,
    then `os.replace`. `os.replace` is atomic on POSIX — if it raises,
    the original file is unchanged. The tmp file is cleaned up on the
    failure path so callers don't see leftover `.tmp` files.
    """
    tmp_path = path.with_name(path.name + ".proofread.tmp")
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Best-effort cleanup; don't mask the original exception.
        try:
            if tmp_path.exists():
                os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_proofread_tag(
    artifact_path: Path,
    *,
    date: Optional[date_cls] = None,
    findings: Optional[list[dict]] = None,
    no_tag: bool = False,
    lens_coverage: Optional[list[dict]] = None,
    allow_tag_on_partial_failure: bool = False,
) -> WriteResult:
    """Append `proofread-passed-<YYYY-MM-DD>` to the artifact's
    frontmatter `tags:` array, returning a `WriteResult`.

    See module docstring for the eligibility rules and the atomic-write
    contract. A `WriteResult` with a `skipped_*` or `failed_*` status
    guarantees the file on disk is unchanged.

    ccp-021 silent-lens-failure gate:
      When `lens_coverage` is provided (the aggregated coverage records
      consumed by the renderer), the writer scans for the ccp-020
      synthetic-silent-failure shape (`error:` set + sentinel count of
      -1). If any such record is found AND the aggregated finding list
      does NOT include findings from that lens, the tag write is
      REFUSED — `WriteResult(status="skipped_silent_lens_failure", ...)`
      with a diagnostic error naming the silent lens.

      When `allow_tag_on_partial_failure=True` AND a silent failure is
      detected (and no blocking finding), the writer downgrades to
      `proofread-attempted-<date>` instead of refusing. Both prefixes
      get stale-tag-collapse treatment so the array stays single-entry.

      Backwards-compat default: `lens_coverage=None` means "no silent-
      failure check performed" — the ccp-014 behaviour is preserved
      bit-for-bit for callers that haven't been updated.
    """
    # Eligibility gate 4: --no-tag opt-out.
    if no_tag:
        return WriteResult(status="skipped_no_tag")

    # Eligibility gate 3: any blocking finding short-circuits the write.
    # When a silent-lens-failure ALSO holds, blocking still takes
    # precedence in the user-facing diagnostic (the blocking signal is
    # actionable; the silent failure is metadata about coverage). But
    # we still surface the silent-failure context in `error` so the
    # render layer can pin both.
    if _has_blocking_finding(findings):
        silent = _silent_failed_lenses(lens_coverage)
        if silent:
            lens_names = ", ".join(
                str(s.get("lens", "<unknown>")) for s in silent
            )
            error_msg = (
                "blocking findings present; additionally silent lens "
                f"failure(s) on: {lens_names}"
            )
            return WriteResult(
                status="skipped_blocking_findings_present",
                error=error_msg,
            )
        return WriteResult(status="skipped_blocking_findings_present")

    # Eligibility gate 6 (ccp-021): silent-lens-failure detection.
    # When at least one coverage record matches the ccp-020 synthetic
    # shape (error: set + checks_attempted == -1), the audit didn't
    # actually land for that lens — refuse to tag (default) or downgrade
    # to proofread-attempted (when explicitly opted in).
    silent = _silent_failed_lenses(lens_coverage)
    if silent and not allow_tag_on_partial_failure:
        # Multi-lens diagnostic: pin every silent lens, but lead with
        # the first one so the error message reads cleanly when there's
        # only one (the typical case — bank-details was one lens).
        first = silent[0]
        first_lens = str(first.get("lens", "<unknown>"))
        first_err = str(first.get("error", "")) or "schema-rejected"
        if len(silent) == 1:
            error_msg = (
                f"{first_lens}: {first_err}; tag refused. "
                "Re-run after lens output is structurally clean."
            )
        else:
            others = ", ".join(
                str(s.get("lens", "<unknown>")) for s in silent[1:]
            )
            error_msg = (
                f"{first_lens}: {first_err}; additional silent failures "
                f"on: {others}; tag refused. Re-run after lens output "
                "is structurally clean."
            )
        return WriteResult(
            status="skipped_silent_lens_failure",
            error=error_msg,
        )

    # Eligibility gate 1: path discriminator.
    if not _is_vault_finding_path(artifact_path):
        return WriteResult(status="skipped_not_vault_finding")

    # Read + parse frontmatter. ANY exception here is treated as
    # "unreadable" — the file on disk is untouched.
    try:
        post = frontmatter.load(str(artifact_path))
    except FileNotFoundError as exc:
        return WriteResult(
            status="skipped_artifact_unreadable", error=str(exc)
        )
    except Exception as exc:
        # YAML errors, permission errors on read, etc. — same outcome.
        return WriteResult(
            status="skipped_artifact_unreadable", error=str(exc)
        )

    # Eligibility gate 2: frontmatter type discriminator.
    if post.metadata.get("type") != "finding":
        return WriteResult(status="skipped_not_vault_finding")

    # Compute the target tag. When the partial-failure downgrade is
    # active AND silent failures were detected, use the attempted-prefix
    # so the audit trail is preserved without overclaiming clean
    # coverage. `silent` is computed above; reuse it here.
    target_date = date if date is not None else _today_utc()
    downgrade = bool(silent) and allow_tag_on_partial_failure
    if downgrade:
        new_tag = _format_attempted_tag(target_date)
    else:
        new_tag = _format_tag(target_date)

    # Find existing proofread-(passed|attempted)-* entries to replace.
    # Tags may be absent, None, a single string, or a list — normalise
    # to a list.
    existing = post.metadata.get("tags")
    if existing is None:
        tags_list: list = []
    elif isinstance(existing, str):
        # A single scalar `tags: foo` is permissible YAML; treat as
        # one-element list so we round-trip correctly.
        tags_list = [existing]
    elif isinstance(existing, list):
        tags_list = list(existing)
    else:
        # Unsupported tags shape (dict, int, etc.) — surface as
        # unreadable rather than corrupt.
        return WriteResult(
            status="skipped_artifact_unreadable",
            error=f"unsupported tags type: {type(existing).__name__}",
        )

    # Separate stale proofread-(passed|attempted)-* entries from the
    # rest. Multiple stale entries (defensive) all get removed and
    # collapsed to one. We collapse BOTH prefix families regardless of
    # which we're about to write — switching from attempted→passed (or
    # vice-versa on the next run) must not leave behind the prior
    # prefix's stale entry.
    stale: list[str] = []
    preserved: list = []
    for entry in tags_list:
        if isinstance(entry, str) and (
            _TAG_RE.match(entry) or _ATTEMPTED_RE.match(entry)
        ):
            stale.append(entry)
        else:
            preserved.append(entry)

    previous_tag: Optional[str] = stale[0] if stale else None

    # Build the new tags list: preserved items in original order, then
    # the new tag appended. Even if the new tag matches an existing
    # stale one, this collapses duplicates to exactly one entry
    # (idempotency).
    new_tags = preserved + [new_tag]

    # Idempotency short-circuit: if there's exactly one stale tag, it
    # equals the new tag, and the on-disk list already matches what
    # we'd write, return "written" without touching the file. This
    # avoids gratuitous mtime changes on repeated invocations.
    if (
        len(stale) == 1
        and stale[0] == new_tag
        and tags_list == new_tags
    ):
        return WriteResult(
            status="written",
            tag_added=new_tag,
            previous_tag=new_tag,
        )

    post.metadata["tags"] = new_tags

    # Serialise. frontmatter.dumps returns a str; we encode UTF-8 to
    # match what frontmatter.load read in. End with a trailing newline
    # to keep vault convention.
    try:
        serialised = frontmatter.dumps(post)
        if not serialised.endswith("\n"):
            serialised += "\n"
        payload = serialised.encode("utf-8")
    except Exception as exc:  # pragma: no cover — dumps shouldn't raise
        return WriteResult(
            status="failed_atomic_write_aborted", error=str(exc)
        )

    # Atomic write. Permission errors, IO errors, etc. → original file
    # unchanged.
    try:
        _atomic_write(artifact_path, payload)
    except Exception as exc:
        return WriteResult(
            status="failed_atomic_write_aborted", error=str(exc)
        )

    return WriteResult(
        status="written",
        tag_added=new_tag,
        previous_tag=previous_tag,
    )


__all__ = ["WriteResult", "write_proofread_tag"]
