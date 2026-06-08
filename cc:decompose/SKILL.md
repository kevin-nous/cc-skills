---
name: "cc:decompose"
description: "Break a PRD into independently buildable issues with detailed plans. Use after /cc:discover to create a backlog that agents can swarm on."
---

# /cc:decompose — PRD to Buildable Issues

Take a PRD and split it into independent, testable issues that can be worked on in parallel.

## Input

Look for the most recent PRD in `docs/prds/`. If none exists, ask the user to run `/cc:discover` first.

## Step 0: Consult Prior Knowledge

Before Step 1, check whether the project has a knowledge vault configured:

1. **If env var `CLAUDE_KNOWLEDGE_VAULT` is set** — treat its value as the vault path (relative to project root).
2. **Else, if `vault/knowledge/` exists at the project root** — use it as the default.
3. **Else** — skip this step.

When a vault is found, invoke the `learnings-researcher` subagent with the PRD's topic / title. Surface 2-5 high-relevance hits (title • type • one-line why relevant) before slicing. Look especially for past decompositions of similar PRDs — they often suggest the right vertical-slice shape.

Why: prior decomposition patterns compound. If a previous similar PRD was sliced cleanly, mirror it; if it was sliced badly, learn from that too.

## Process

### Step 1: Identify Vertical Slices

Read the PRD's acceptance criteria. Each criterion (or small group of related criteria) becomes one issue. The goal: each issue delivers a working, testable increment.

Bad slicing: "Build the frontend" then "Build the backend"
Good slicing: "User can search by username" then "User can send friend request" then "User can accept/decline"

### Step 2: Map Dependencies

Which issues block others? Draw the dependency graph:
- Independent issues can be swarmed in parallel
- Dependent issues must be sequenced
- Flag any issue that requires a database migration (these go first)

### Step 3: Write Issues

Create `docs/issues/NNN-<title>.md` for each:

```markdown
---
id: NNN
title: [Short title]
status: ready
depends_on: []  # list of issue IDs
effort: S/M/L
# When /cc:build closes this issue, it will add:
#   shipped_at: YYYY-MM-DD
#   shipped_commit: <12-char-sha>
---

# [Title]

## What
One paragraph: what does this issue deliver?

## Why
Which PRD acceptance criterion does this satisfy?

## Acceptance Criteria
- [ ] Specific, testable (these become TDD test cases in /cc:build)

## Technical Approach
- Files to create/modify
- Key patterns to follow (reference existing code)
- Supabase tables/columns involved

## Test Plan
- What tests prove this works?
- What edge cases to cover?
```

## Issue Lifecycle

The status field on each issue file moves through a small state machine:

- **`ready`** — written by `/cc:decompose` (this skill). Means "decomposed, awaiting build."
- **`in_progress`** (optional, used by `/cc:build` while working) — means "actively being implemented."
- **`shipped`** — set by `/cc:build` when all acceptance criteria pass. Also stamps `shipped_at` (date) and `shipped_commit` (12-char SHA) on the frontmatter so the audit trail is intact.
- **`deferred`** — set by `/cc:build` when the work is abandoned or de-prioritized. Should also stamp `deferred_reason`.

**Why this matters:** without explicit status transitions, `docs/issues/` becomes a graveyard of `status: ready` files that are actually long since shipped (one project had 205/209 in this state before a cleanup). When you read an issue, the status should always tell you the truth. `/cc:build` is responsible for closing the loop; this skill (`/cc:decompose`) is responsible for opening it.

If you ever inherit a directory full of stale `ready` issues, a `scripts/backfill_issue_status.py` is a reference implementation for retroactive triage (git-log slug matching + age-inferred fallback).

### Step 4: Prioritize

Sort issues into waves for parallel execution:

```
Wave 1 (no dependencies): Issue 1, Issue 2, Issue 3
Wave 2 (depends on wave 1): Issue 4, Issue 5
Wave 3 (depends on wave 2): Issue 6
```

## Rules
- Each issue must be completable in < 1 hour by one agent
- Each issue must be independently testable
- Each issue must list exact files to modify
- Never create an issue that says "and other related changes" — be specific
- Migrations always go in Wave 1

## Handoff
Present the issue list with waves and say: "Ready to build. Run `/cc:build [issue-number]` for each, or swarm Wave 1 in parallel."
