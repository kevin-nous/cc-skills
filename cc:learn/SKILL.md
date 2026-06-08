---
name: "cc:learn"
description: "Document what was learned from a build, fix, or discovery so future sessions benefit. Creates searchable solution docs. Use after any significant work."
---

# /cc:learn — Compound Your Knowledge

Capture what was learned so the next time this problem arises, it takes minutes not hours.

---

## Step 0 — Signal-phrase routing (am I the right skill?)

Pattern-match what the user just said. If the language signals a different shape, redirect — don't silently write the wrong type. This is mechanical first-pass; the user-facing Diátaxis 2×2 in Step 2.5 is the deliberate confirmation.

| Trigger phrase the user used | Likely shape | Right skill |
|---|---|---|
| "the bug was", "root cause", "the fix is", "missing await", "wrong JOIN", "off-by-one", "PR feedback" | **Solution** (single-instance fix or pattern) | **cc:learn** (continue, `type: solution`) |
| "we keep hitting this", "the pattern is", "every time we …", "across N cases", "this happens whenever" | **Compound** (cross-cutting pattern from 2+ instances) | **cc:learn** (continue, `type: compound`) |
| "conversion moved", "X% drop", "the cohort", "variant V_ is at Y%", "data shows" | **Business finding** (project metrics/experiment) | a project-specific capture skill — redirect |
| "X is defined as", "the system always …", "for future reference", "anatomy of …" | **Concept** (living reference) | Manual write to `vault/knowledge/<slug>.md` (no date prefix) |

Default if signals are mixed or solution-shaped: continue with this skill. If a redirect signal wins, surface it explicitly:

> "What you described reads more like a [finding / concept] (or a compound — for which I stay here). Want me to redirect to [a project capture skill / manual]? Compound writes continue in cc:learn."

---

## When to Use

After any of:
- Fixing a non-obvious bug
- Discovering a pattern that should be reused
- Making a decision with tradeoffs worth remembering
- Completing a build that taught you something

> **Body layout:** if the project has `vault/knowledge/writeup-style.md`, read it before drafting the body. That page is the canonical readability standard across vault doc types; the Problem / Root Cause / Solution / Prevention shape in Step 3 below is one valid instantiation for `type: solution` outputs. Projects without that page fall back to the shape in Step 3.

## Process

### Step 1: Identify the Learning

Ask yourself: "What would I tell a teammate who hits this tomorrow?"

If the answer is "nothing interesting" — skip it. Not everything needs documenting.

If there IS something worth keeping: what was surprising? What was the root cause? What would have prevented it?

### Step 2: Pick a Category

Categories are now frontmatter (no longer subfolders). Pick the one that best fits:

- `build-errors` — compilation, bundling, native module issues
- `runtime-errors` — crashes, null refs, async failures
- `performance` — slow queries, re-renders, memory leaks
- `security` — vulnerabilities, auth issues, data leaks
- `architecture` — structural decisions, patterns, refactors
- `integration-issues` — third-party services, APIs, multi-system issues
- `infrastructure-issues` — CI, deploys, runtime infra
- `analytics` — data sources, Amplitude/Snowflake patterns, metric definitions
- `testing` — test patterns, TDD learnings, QA processes

Free-text categories are allowed for the long tail — pick the most accurate label, don't force a fit.

### Step 2.5: Disambiguate `type` and (if the project uses them) assign `clusters`

Before pre-filling the frontmatter, run two short interactive checks. The routing layer (folder filters, dashboards, cluster-based MOCs) breaks if half the new entries get clusters and half don't, so make these decisions deliberate.

**Diátaxis 2×2 — assign `type`.** Most `cc:learn` output is `type: solution`, but force the deliberate choice rather than defaulting silently. Ask:

> Is this file teaching how to do something (**action**) or recording observed state (**cognition**)? Acquisition or application?
>
> - action + application → `solution` (the default for cc:learn — a fix/workaround for a problem)
> - action + application, abstracted from multiple cases → `compound` (cross-cutting pattern; cc:learn handles both solution and compound writes)
> - cognition + application, tied to a specific question → `finding` (often written by a project-specific capture skill)
> - cognition + application, describing a system property / pattern → `concept` (living doc; no date prefix in filename)

Map the answer onto one of `solution | compound | finding | concept`.

If the project ships a vault conventions doc, point the user at its full type-disambiguation section. Try these locations in order:

1. Env var `CLAUDE_VAULT_CONVENTIONS` — explicit path override.
2. `<vault>/vault-conventions.md` — where `<vault>` is `CLAUDE_KNOWLEDGE_VAULT` if set, else `vault/knowledge/`.
3. Skip the link if neither exists; the four-line summary above is enough.

If the answer is `finding` or `concept`, surface that to the user before continuing — they may have invoked `/cc:learn` by reflex when a project-specific finding-capture skill or a direct concept-page edit is the better path.

**Cluster membership — assign `clusters` (opt-in; skip if the project has no registry).** Try to read a cluster registry from these locations, in order:

1. Env var `CLAUDE_VAULT_CLUSTERS` — explicit path override.
2. `references/vault-clusters.md` at the project root (project convention).
3. If neither exists, **skip this prompt entirely**. Clusters are an opt-in routing layer, not a baseline requirement.

When a registry exists, extract the valid IDs at runtime — don't hard-code, the registry is the source of truth:

```bash
grep -E '^\| `[a-z-]+`' "$REGISTRY_PATH"
```

Show the user the valid IDs and ask:

> Which clusters does this learning belong to? (multi-select, optional. Default: no clusters.) Soft cap: 6 per doc.

Default to no clusters if the user doesn't pick — empty is valid. Don't invent IDs not in the registry; the project's vault validator (if any) may warn on unknown values.

### Step 2.6: Acceleration Test (final gate before write)

Before writing, ask: **"Would this solution doc have prevented the time waste that led to writing it?"**

- ✅ Yes → continue to Step 3.
- ⚠️ Not sure → sharpen the root cause. A solution that just describes the symptom won't surface in a future grep. A solution that names the *exact wrong assumption* will.
- ❌ No → skip. "If it's not worth 5 minutes to write, it's not worth documenting" already says this; the Acceleration Test sharpens the criterion to "would a teammate's future hour have been saved."

### Step 2.7: Quality check — concrete vs vague

Before write, sanity-check the headline against this pattern. If your draft sits in the ❌ column, sharpen before continuing.

| ❌ Vague | ✅ Concrete |
|---|---|
| "Auth was broken — restarted the service" | "Firebase token-refresh returns stale token without erroring when user idle >24h; call `getIdToken(true)` explicitly on long-lived sessions." |
| "Changed line 233 from X to Y" | "Changed `energySwitch/createSwitch.ts:87`: missing `await` on `checkDataSufficiency()` — race condition under load >5 req/s, silently passed under single-user testing." |
| "Follow the convention" | "Follow the pattern in `broadband/generateRecommendation.ts:42` — gate-check → fetch → transform, in that order. Energy must match because data-sufficiency gates can flip between steps." |

The right column survives the "would this surface in a future grep" test. The left column won't.

### Step 3: Write the Solution Doc

> **Implements:** [[writeup-style]] (in projects with a vault). The Problem / Root Cause / Solution / Prevention template below is one valid instantiation for `type: solution` outputs. If the project has `vault/knowledge/writeup-style.md`, read it for principles (verdict-first, tables for ≥3 parallel items), anti-patterns, and the supersedes/Updates pattern. The standard governs principles; the template below is the specific shape.

Write to `vault/knowledge/<date>-<slug>.md` (flat folder — no subfolders). `<date>` is today's date as `YYYY-MM-DD`. `<slug>` is lowercase kebab-case, ASCII only, derived from the title.

```markdown
---
type: solution                        # or compound | finding | concept, per Step 2.5 Diátaxis check
category: [picked-from-step-2]
date: YYYY-MM-DD
slug: <date>-<slug>
title: "[Descriptive title]"
status: draft
source_skill: cc:learn
clusters: [<ids-from-step-2.5>]       # optional, omit if empty or if project has no cluster registry
tags: [relevant, searchable, tags]
---

# [Title]

## Problem
What happened? What was the symptom?

## Root Cause
Why did it happen? (Not just "the code was wrong" — WHY was it wrong?)

## Solution
What fixed it? Include code snippets if helpful.

## Prevention
How to avoid this in the future. Be specific:
- A grep command to detect it
- A pattern to follow
- A rule for the team

## Related
Links to other docs, PRs, or issues. Prefer Obsidian wikilinks (`[[slug]]`) for in-vault references.
```

The frontmatter contract is defined in `references/knowledge-frontmatter-schema.md` — `type` and `date` are required, `slug` and `title` are required, `category` is required for solutions (audit trail). Optional fields like `severity`, `components`, `funnel_area` may be added when applicable.

## Rules

- Lead with the root cause, not the symptom
- Include the grep/check command that would detect this issue
- If it's not worth 5 minutes to write, it's not worth documenting
- Update existing docs if the learning extends a known pattern
- Check `vault/knowledge/` for existing `type: solution` entries first — don't duplicate. A quick grep: `grep -lE "^type: solution" vault/knowledge/*.md | xargs grep -l "<keyword>"`.

## The Compounding Effect

First time: 30 min research → 5 min doc
Second time: 2 min lookup → done

Knowledge compounds. Each doc makes the team faster.
