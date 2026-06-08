---
name: "cc:build"
description: "Build a feature using TDD red-green-refactor. Takes an issue from /cc:decompose and implements it test-first in vertical slices. Use instead of coding directly."
---

# /cc:build — TDD Implementation

Build a feature or fix a bug using test-driven development. Every change starts with a failing test.

## Input

Accepts either:
- An issue file from `docs/issues/NNN-*.md` (from `/cc:decompose`)
- A direct description of what to build
- A plan file from `docs/plans/`

## Step 0: Consult Prior Knowledge

Before the RED-GREEN-REFACTOR loop, check whether the project has a knowledge vault configured:

1. **If env var `CLAUDE_KNOWLEDGE_VAULT` is set** — treat its value as the vault path (relative to project root).
2. **Else, if `vault/knowledge/` exists at the project root** — use it as the default.
3. **Else** — skip this step.

When a vault is found, invoke the `learnings-researcher` subagent with the issue title + acceptance criteria as the query. Surface 2-5 high-relevance hits (title • type • one-line why relevant) to the user before writing the first failing test. If a `type: solution` doc matches closely, start by reading it — the implementation pattern is likely captured there already.

Why: TDD is faster when you start from a known good pattern. If we've solved this shape of problem before, replicate; don't reinvent. Skipping is fine when the issue is genuinely novel, but the check should be reflexive.

**External APIs:** when the acceptance criteria implement against a third-party library or framework, fetch current, version-specific docs via Context7 (or the `framework-docs-researcher` subagent, which uses it) before writing the first failing test — so the test asserts the *current* API surface, not a half-remembered one. Reflexive whenever the criteria name an external SDK.

## The Loop

For each acceptance criterion in the issue:

### RED — Write ONE failing test
```
1. Read the acceptance criterion
2. Write a test that asserts the expected behavior
3. Run the test — it MUST fail
4. If it passes, your test is wrong (it's not testing anything new)
```

### GREEN — Write minimal code to pass
```
1. Write the MINIMUM code to make the test pass
2. No extra features, no "while I'm here" changes
3. Run the test — it MUST pass
4. Run ALL tests — nothing else should break
```

### REFACTOR — Clean up (only when green)
```
1. All tests passing? Now clean up
2. Remove duplication
3. Improve naming
4. Simplify logic
5. Run tests again — still green? Move to next criterion
```

## Rules

### Vertical slices, not horizontal layers
Bad: "Write all the tests" → "Write all the code"
Good: "Test login" → "Code login" → "Test signup" → "Code signup"

### Tests verify behavior, not implementation
Bad: `expect(component.state.loading).toBe(true)`
Good: `expect(screen.getByText("Loading...")).toBeTruthy()`

### One test at a time
Never write multiple failing tests. One red, one green, one refactor. Repeat.

### The test is the spec
Each test should read like a sentence: "it should [do X] when [condition Y]". If you can't write the test, you don't understand the requirement — go back to the issue.

## Progress Tracking

After each red-green-refactor cycle, update progress:
```
✅ Criterion 1: User can search by username [3 tests]
✅ Criterion 2: Results filter out existing friends [2 tests]  
🔴 Criterion 3: Send friend request creates row [writing test...]
⬜ Criterion 4: Duplicate request shows error
```

## When Tests Aren't Practical

For UI-only changes (styling, layout), skip formal tests but still work incrementally:
1. Make ONE change
2. Verify visually (screenshot or simulator)
3. Commit
4. Next change

The principle is the same: small verified increments, not big-bang changes.

## Closing the Issue

When all acceptance criteria pass — **before** the handoff — close the loop on the issue file's frontmatter. This is what prevents `docs/issues/` from accumulating hundreds of `status: ready` files that are actually shipped.

If the input was an issue file (`docs/issues/NNN-*.md`):

1. Get the SHA of the commit that finished the work (usually `HEAD` if you just committed). If the work spanned multiple commits, use the *most recent* one as the canonical reference.
2. Edit the issue file's frontmatter:
   - `status: ready` → `status: shipped`
   - Add `shipped_at: <YYYY-MM-DD>` (today's date)
   - Add `shipped_commit: <12-char-SHA>` (the closing commit)
3. Mention this in the handoff so the user knows the file was updated.

If you can't confidently say the work shipped (e.g. one criterion is still failing, or there's a known follow-up):
- Leave `status: ready` and explain what's still open in the handoff.
- Or, if the work was abandoned, set `status: deferred` with a `deferred_reason:` field.

If the input was not an issue file (direct description / plan file), skip this step.

## Handoff

When all criteria pass and the issue frontmatter is updated: "All tests green. Issue closed — `status: shipped` set on `docs/issues/NNN-*.md`. Run `/cc:review` to verify quality, or `/cc:learn` to document what was learned."
