---
name: "cc:discover"
description: "Explore a feature idea through structured dialogue, stress-test it, and produce a formal PRD. Use when starting something new — before any planning or coding."
---

# /cc:discover — From Idea to PRD

Take a vague idea and turn it into a bulletproof Product Requirements Document through three phases.

## Step 0: Consult Prior Knowledge (MANDATORY when a vault exists — do not skip)

Before Phase 1, check whether the project has a knowledge vault configured:

1. **If env var `CLAUDE_KNOWLEDGE_VAULT` is set** — treat its value as the vault path (relative to project root).
2. **Else, if `vault/knowledge/` exists at the project root** — use it as the default.
3. **Else** — skip this step.

When a vault is found, run the **prior-knowledge sweep** in this exact order. Each step is cheap; the failure mode is doing them out of order or skipping the early ones.

### 0a. Filename grep first (highest signal, lowest cost)

Curators pick on-the-nose slugs for canonical docs. Filename grep finds them in <200ms with high recall.

```bash
ls <vault>/ | grep -iE '<2-3 topic keywords joined by |>'
```

Pick keywords from the user's request (the noun + the modifier — e.g. "opt-in", "routing", "BG retention"). Run 2–3 variants if the first returns nothing. Open every result that looks plausible.

### 0b. INDEX → MOC routing (vault's curated map)

Read `<vault>/INDEX.md` (or the equivalent index file the vault uses) **before any content grep**. Identify which MOC owns the topic (`moc-<area>.md`) and read it. MOCs link to the canonical findings + concept pages for their area — they exist exactly to prevent flat-grep misses.

This is the rule from CLAUDE.md (where present): "Do not grep the vault flat without consulting INDEX first." Honor it.

### 0c. `learnings-researcher` subagent

Invoke the `learnings-researcher` subagent (bare slug — that's the registered `subagent_type`; the `ce-agents:` prefix is a skill namespace and is **not** a valid agent name) with a 1-2 sentence summary of the user's ask. It is frontmatter-aware and not subject to the truncation traps of raw grep. **Do not skip this when a vault exists**, even when short-circuiting later phases. Surface 2-5 high-relevance hits (title • type • one-line why relevant) before opening Phase 1.

### 0d. Content grep — fallback only

Only after 0a–0c have run. If you use it:

- **Never `head -N` grep output without first running `wc -l`.** If the match count exceeds your cap, either widen the cap (`head -200`) or narrow the regex — don't silently truncate. Directory-walk order is not alphabetical and the canonical doc can easily fall outside a small window.
- Prefer slug-style regexes that anchor on the topic noun (`routing`, `retention`), not generic words that hit every finding.

### When the user's real ask is "update an existing doc", not "build a new thing"

cc:discover is the PRD-from-idea skill, but users sometimes invoke it for an adjacent task — most commonly "update the existing finding/concept page on X". In that case:

- Steps 0a–0c are still mandatory (you need to find the existing doc).
- Phases 1–3 (Explore / Stress Test / PRD) are skipped — there's no new feature to scope.
- Present the candidate doc(s) you found via AskUserQuestion before proposing changes. Do not create a new doc when a canonical living one exists — update the existing one. If you're unsure which is canonical, ask.

The failure mode this guards against: offering the user a "create new doc" option when a canonical living doc already exists but you didn't find it in 0a–0c.

Why this section exists: on 2026-05-20 a cc:discover invocation skipped to flat-grep + `head -30`, missed `2026-05-05-immprop-opt-in-routing-logic-timeline.md` (the canonical living finding it should have updated), and offered the user a "create new concept page" option. The user — fairly — called this silly. Indexing was fine; the skill skipped its own Step 0 routing.

## Phase 1: Explore (2-5 minutes)

Understand the idea through collaborative dialogue. Use AskUserQuestion — ONE question at a time:
- What problem does this solve? Who feels this pain?
- What does success look like? How will you know it worked?
- What's the simplest version that delivers value?
- Are there existing patterns in the codebase? (Search `docs/solutions/` and the repo)

Stay curious. Don't jump to solutions.

## Phase 2: Stress Test — delegate to `grill-me`

Hand the stress-test off to the `grill-me` skill. It already:
- Auto-detects the vault (same fallback as Step 0).
- Surfaces relevant concept pages + recent findings before the first question.
- Challenges fuzzy language against existing concept pages.
- Cross-references claims with code AND with the vault.
- Queues proposed concept-page edits and `type: finding, kind: decision` writeups for batch confirmation at the end.

Invoke it with a 1-2 sentence summary of the idea (and a pointer to anything Phase 1 surfaced that needs probing). Resume Phase 3 once grill-me reports resolution.

Why delegate: one source of truth for "how to grill". Updates to the grilling pattern (e.g. new vault primitives) flow into `cc:discover` automatically.

## Phase 3: Write the PRD

Write to `docs/prds/YYYY-MM-DD-<topic>.md`:

```markdown
---
title: [Feature Name]
date: YYYY-MM-DD
status: draft
---

# [Feature Name]

## Problem
What's broken or missing? Why does it matter? Who feels this pain?

## Solution
What are we building? One paragraph, plain language.

## User Stories
- As a [user], I want to [action] so that [benefit]

## Acceptance Criteria
- [ ] Testable requirement 1
- [ ] Testable requirement 2
(Each criterion should be verifiable with a test)

## Edge Cases
- What if [X]?
- What if [Y]?

## Out of Scope
What we are explicitly NOT building. Be specific.

## Technical Considerations
Architecture, dependencies, risks, existing patterns to follow.

## Open Questions
Anything unresolved. These must be answered before /cc:build.
```

## Rules
- The PRD must be so clear that ANY developer could build it without asking questions.
- Every acceptance criterion must be testable (this feeds into /cc:build's TDD loop).
- If a section feels vague, it's not done — ask the user to clarify.
- Open Questions must have zero items before moving to /cc:decompose.

## Handoff
Present the PRD and say: "PRD ready. Run `/cc:decompose` to break this into buildable issues."
