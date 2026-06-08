---
name: "cc:review"
description: "Multi-layer code review: functional correctness, security, architecture, and adversarial testing. Use after /cc:build or before shipping."
---

# /cc:review — Multi-Layer Review

Review code through four lenses, each catching things the others miss.

## Step 0: Consult Prior Knowledge

Before Layer 1, check whether the project has a knowledge vault configured:

1. **If env var `CLAUDE_KNOWLEDGE_VAULT` is set** — treat its value as the vault path (relative to project root).
2. **Else, if `vault/knowledge/` exists at the project root** — use it as the default.
3. **Else** — skip this step.

When a vault is found, invoke the `learnings-researcher` subagent with the change being reviewed (file names, area, the feature) as the query. Surface 2-5 high-relevance hits (title • type • one-line why relevant) before starting Layer 1. Look especially for past review gotchas in the same domain — past `type: solution` docs about "this looks correct but actually breaks when Y" are gold for adversarial Layer 4.

Why: known gotchas are the cheapest review wins. Past docs already enumerated the failure modes; the review just needs to verify the current change doesn't re-introduce them.

**External API usage:** when the change calls into a third-party library or framework, verify the calls against current, version-specific docs via Context7 (or the `framework-docs-researcher` subagent, which uses it) — deprecated methods, changed signatures, and removed options are misses that internal vault knowledge won't catch.

## Layer 1: Functional Correctness

Does the code actually work?

- Run tests: do they pass?
- Trace every user interaction: does every button have an onPress?
- Check every Supabase query: correct table names, column names, conflict keys?
- Check error handling: does every async call handle failure?
- Check loading states: is there a spinner/skeleton while data loads?

```bash
# Quick functional checks
grep -rn "<Pressable" app/ components/ --include="*.tsx" | grep -v onPress  # Dead buttons
grep -rn "catch.*{}" app/ components/ --include="*.tsx"  # Silent failures
grep -rn "Coming soon" app/ components/ --include="*.tsx"  # Dead ends
```

## Layer 2: Security

Can this be exploited?

- Input sanitization: is user input escaped before queries? (especially `.or()`, `.ilike()`)
- Authorization: are server-side checks in place? (not just client UI gating)
- Token handling: are tokens crypto-random? Stored securely?
- Data exposure: do public routes leak private data?
- Secrets: any hardcoded keys, passwords, or tokens?

## Layer 3: Architecture (from improve-codebase-architecture)

Is the code organized well?

- Are there files that do too many things?
- Are there concepts split across too many files?
- Is there duplication between components that should share logic?
- Are naming conventions consistent?
- Would a new developer understand this code?

Look for structural confusion — places where understanding one concept requires bouncing between many files.

## Layer 4: Adversarial

Try to break it on purpose.

- What if the user double-taps?
- What if two users modify the same data simultaneously?
- What if the network drops mid-operation?
- What if data is null/empty/malformed?
- What if counts go negative?

## Output

For each finding:
```
[SEVERITY] File:line — Description
Fix: What to change
```

Severity: CRITICAL (blocks ship) → HIGH (should fix) → MEDIUM (should fix soon) → LOW (nice to have)

## After Review

- Fix all CRITICAL and HIGH issues immediately
- Create issues for MEDIUM (via `/cc:decompose` if needed)
- Accept or document LOW issues
- Then: "Review complete. Run `/cc:learn` to document findings."
