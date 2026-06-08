---
name: grill-me
description: Interview the user relentlessly about a plan until reaching shared understanding. Vault-aware — challenges fuzzy language against existing concept pages and queues proposed concept edits + decision-record findings for batch confirmation at the end. Use when user wants to stress-test a plan, get grilled on a design, or mentions "grill me".
---

Interview me relentlessly about every aspect of this plan until we reach shared understanding. Walk down each branch of the decision tree, resolving dependencies one-by-one. For each question, provide your recommended answer.

**One question at a time.** Wait for feedback before the next.

If a question can be answered by exploring the codebase or the vault, do that instead of asking.

---

## Vault detection (Step 0)

Before the first question, detect the project's knowledge vault:

1. If env var `CLAUDE_KNOWLEDGE_VAULT` is set → treat its value as the vault path.
2. Else if `vault/knowledge/` exists at the project root → use it.
3. Else → no vault; run a plain grilling session and skip the vault-aware behaviors below.

If a vault is found, invoke the `learnings-researcher` subagent with a 1-2 sentence summary of the plan. Surface 2-5 high-relevance hits before the first question (title • type • one-line why relevant). Tell the user: *"Here's what we already know — flag any of these I should challenge you against."*

Also check for an `vault/INDEX.md` and a `team/glossary.md` — read them if present to learn the project's language.

## During the session — vault-aware behaviors

### Challenge against existing concept pages

When the user uses a term that conflicts with how a concept page defines it, call it out immediately: *"The `checkout-redesign` concept defines `DelayedV2` as X — you're using it to mean Y. Same thing or different?"*

### Sharpen fuzzy language

When the user uses vague or overloaded terms, propose the canonical term from the vault: *"You said 'switch' — do you mean `switch_registration`, `purchase_success`, or the supplier-side cutover? Different concepts."*

### Discuss concrete scenarios

Stress-test relationships with specific scenarios. Invent edge cases that force precision: *"A household has two services on different suppliers, one in retention, one not. Which arm of your experiment do they end up in?"*

### Cross-reference with code AND with the vault

When the user states how something works, check the code, AND check whether existing concept pages or recent findings agree. Surface contradictions either way: *"The `recommendation-engine` concept says soft-rec filters run after hard eligibility. Your plan reverses them. Intentional?"*

## Queued edits — apply at the end, not inline

**Do not edit concept pages or write findings mid-conversation.** Instead, maintain an internal queue:

- **Concept-page edits** — when grilling resolves a term, refines a definition, or surfaces a contradiction that should update a living concept page, queue the proposed edit (file path + diff summary). Don't write yet.
- **Finding writeups (kind: decision)** — when a decision in the conversation meets all three:
  1. Hard to reverse,
  2. Surprising without context,
  3. The result of a real trade-off,

  queue a proposed `type: finding, kind: decision` writeup. If any of the three is missing, skip.

  If any of the three criteria is missing, don't queue an ADR — say so and move on.

- **Finding writeups (other kinds)** — when grill-me is invoked from inside a vault-writing skill (a vault-writing skill (e.g. `cc:learn`)) and the grill resolves claims that will be persisted by that caller, queue the **claim refinements** (not a full writeup) for the caller to fold back into its draft. Supported kinds: `investigation`, `diagnosis`, `retro`, `closing-read`, `weekly_review`, plus `solution` and `compound` when invoked via `cc:learn`. The caller is the writer; grill-me's job is to deliver the agreed claim set + hedging language back to it. Do **not** write the file from grill-me — single source of truth for file-writes lives in the caller's skill (avoids the parallel-write trap where two skills write competing versions of the same file).

## End of session — batch confirm

When the grilling reaches resolution, present the queue as **one batch** for confirmation:

```
Proposed vault writes:
  1. EDIT  vault/knowledge/checkout-redesign.md
       └─ Clarify DelayedV2 vs ImmPropV5 wording in §Active variants
  2. NEW   vault/knowledge/2026-05-14-decision-blueprint-routing-tie-break.md
       └─ type: finding, kind: decision — when two variants tie on cost, pick the one with longer conversion history

Refinements for caller (when invoked from a vault-writing skill, e.g. cc:learn):
  3. CLAIM REFINEMENTS for in-progress draft:
       └─ "V9 trails downstream" → hedge to "V9 trails downstream (−0.83pp); chaser-trigger mechanism is hypothesis, not observation"
       └─ Drop unsupported claim: "banner reduces CTA tap-through" (step-health chart confounded; needs strict-blueprint funnel)

Apply all / pick which / skip?
```

Apply only what the user confirms. If they pick "skip", leave the conversation residue in the chat — they can hand-write the docs later.

For the **claim-refinement** items (case 3 above): the caller skill receives the refinements as a structured list and folds them into its draft before its own write step. Grill-me does not write findings of those kinds directly — that responsibility stays with the caller (single source of truth for file-writes).

## When no vault exists

Fall back to plain interview behavior: one question at a time, explore codebase before asking, provide recommended answers. Skip all the queue-and-confirm machinery. Don't invent a vault.
