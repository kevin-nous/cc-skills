---
schema_version: 1
description: Default criteria registry for cc:proofread — 12 substantive + 4 mechanical checks across scope, grounding, and structural lenses, in domain-neutral language.
last_updated: 2026-05-29
mechanical_prepass:
  # ccp-017: cap each mechanical check's emitted findings at this many.
  # When a check exceeds the cap, the first N findings are emitted plus a
  # single sentinel "<check_id>-truncated" finding naming the suppressed
  # count. Coverage's claims_flagged still reports the FULL pre-truncation
  # total so the reader sees the true scale.
  #
  # Two shapes accepted:
  #   max_findings_per_check: 5                # uniform cap across all 4 checks
  #   max_findings_per_check:                  # per-check override map
  #     default: 5
  #     hedge-promotion-regex: 10
  #     citation-traceability-missing: 5
  #
  # Override via CLI: --max-findings-per-check N. Set N=0 to silence a
  # check entirely (the sentinel still fires so the suppression is
  # visible in the coverage record).
  max_findings_per_check: 5
---

<!--
  This is the DEFAULT criteria registry consumed by cc:proofread when no
  project-specific override is present.

  Projects override this file at:

      <project-root>/.claude/cc:proofread/criteria.md

  The override pattern lets each project add its own domain vocabulary
  (e.g. specific event names, dataset names, table names, supplier names)
  and project-specific check refinements on top of these defaults. The
  resolution is "project override OR fall back to this file" — never an
  error.

  This file is YAML-with-markdown: a YAML frontmatter block (above), a
  human-readable section per lens (below), and one fenced ```yaml``` block
  per check. The orchestrator iterates the fenced blocks to load the check
  registry programmatically; humans read the surrounding prose.

  Domain-neutral language discipline: this file MUST NOT mention any
  product-specific terms. Per-project overrides are where domain words
  live. Default vocabulary uses general analytical-writing terms like
  "the claim's population vs the evidence's population", "the substrate
  the analysis is grounded in", "the underlying data store".
-->

# cc:proofread — default criteria

This file ships with the skill and is read when no per-project override
exists at `<project-root>/.claude/cc:proofread/criteria.md`. It defines
the checks each lens runs against an analytical or planning artifact
(finding, writeup, PRD, draft message, research plan).

Three substantive lenses run in parallel as fresh-context subagents:

| Lens | Purpose | Number of checks |
|---|---|---|
| **Scope auditor** | Does the claim's scope match the evidence's scope? | 6 |
| **Grounding auditor** | Are the numerical claims actually true against the underlying data? | 4 |
| **Structural auditor** | Is the artifact internally consistent and fit for its audience? | 5 + 1 meta-flag |

A mechanical prepass (regex-only, no LLM) runs *before* the lenses and
adds 4 fast checks.

Each check below has five fields:

- `id` — kebab-case identifier the orchestrator addresses the check by
- `description` — one-line statement of what the check looks for
- `trigger_heuristic` — concrete signal in the artifact that fires the check
- `remediation_language` — how the lens phrases the finding to the user (adversarial voice, not "consider whether...")
- `example_failure_mode` — one-sentence concrete instance the lens prompt can cite

---

## Scope auditor

The scope auditor compares the population, time window, and
conditioning of every load-bearing claim against the population, time
window, and conditioning of the evidence the claim cites. Most "the
piece is internally coherent but wrong" failures are scope mismatches.

```yaml
id: scope-drift
description: The claim's population, window, or filter does not match the population, window, or filter of the evidence it cites.
trigger_heuristic: A headline number is sourced from a wider or narrower slice than the spec / methodology section declares; or a claim about "all users" is supported by a number computed on a subset (or vice versa).
remediation_language: "The headline cites scope X, but the underlying evidence is computed on scope Y. Either restate the claim at scope Y, or recompute the evidence at scope X. Do not silently swap scopes between sections."
example_failure_mode: A piece reports +6.4pp as the headline result, where +6.4pp is the favourable wider-scope number; the spec methodology section declares the primary window is narrower and the narrower-scope read is only +8.8pp at p=0.059.
```

```yaml
id: capability-from-rate-evidence
description: A structural / capability claim ("X can do Y", "X causes Y") is supported only by rate-comparison evidence, which cannot establish capability without a counterfactual mechanism.
trigger_heuristic: The artifact contains words like "preserves", "causes", "enables", "unlocks", "drives" but the supporting evidence is a between-group rate delta, a before/after comparison, or a single segmented breakdown.
remediation_language: "This is framed as a capability or causal claim, but the evidence shown is a rate comparison. Either downgrade the framing ('rate is consistent with', 'no obvious deterioration') or add the mechanism evidence (decomposition, dose-response, or a direct manipulation) that would justify the structural claim."
example_failure_mode: A writeup says "the new flow preserves voluntary conversion" based on a flat rate delta in a historical full-ship comparison with no control arm — the rate could be flat for many reasons, and "preserves" asserts a mechanism the evidence cannot reach.
```

```yaml
id: alternative-interpretation-missing
description: The strongest contrary interpretation of the evidence is not addressed (or even named) in the artifact.
trigger_heuristic: The artifact reaches a single confident interpretation of an ambiguous result without surfacing what else the same numbers could mean; or it lists alternative interpretations only as a perfunctory caveat without engaging them.
remediation_language: "An equally plausible alternative interpretation of this evidence is X. The piece does not address it. Either explain why X is ruled out, or weaken the framing to reflect that the evidence is consistent with multiple readings."
example_failure_mode: A piece concludes that a UX change caused a conversion lift, without addressing the alternative that a concurrent supply-side change to the underlying product offering could equally explain the same lift.
```

```yaml
id: mechanism-as-clock
description: A piece of evidence is cited as behavioural learning when the mechanism is in fact structurally guaranteed by the system's design.
trigger_heuristic: The artifact reports a difference between arms (or before/after) where one side is structurally incapable of producing the observation in the window measured — e.g. an event that can only fire after a built-in delay, compared at a time before that delay elapses.
remediation_language: "This is dressed as a behavioural signal but it is mechanically forced by the design. Arm B literally cannot produce this event in the measured window; the observed gap is a clock artefact, not a learning. Either restate it as a structural property of the design, or move the comparison to a window where both arms can produce the event."
example_failure_mode: A writeup says "Arm A has 30× more active rejections in the first three days than Arm B" — but Arm B is structurally configured to delay the surface that produces rejections, so Arm B cannot fire the event in three days regardless of user behaviour.
```

```yaml
id: maturity-surface-missing
description: For analyses with delayed-conversion or multi-step outcome metrics, each group's maturity (i.e. how much of the conversion window has elapsed for the group's members) is not surfaced or is implied to be uniform when it is not.
trigger_heuristic: The artifact reports a between-group comparison on a metric with a conversion window > 0 days, without explicitly stating each group's maturity date (when members entered the group and how much of the window has elapsed), or it conflates a short-tail "early read" with a fully matured read.
remediation_language: "This compares groups on a metric with a multi-day conversion window, but does not surface each group's maturity. Add a maturity calendar (entry date, conversion-window length, effective-mature date, strict-mature date) so the reader knows whether the groups are being compared at equal age."
example_failure_mode: A writeup compares two arms on a 14-day outcome metric when one arm's earliest members are only 7 days past entry — the observed gap is partly that the late arm's tail has not yet matured, not a true effect difference.
```

```yaml
id: confound-inventory-missing
description: Known confounds for this kind of analysis are not named in the artifact, even when the artifact does not attempt to address them.
trigger_heuristic: The artifact reaches a directional conclusion in a domain where well-known confounds apply (seasonality, selection, opt-in shifts, supply-side mix shifts, regression to mean, novelty effects) without listing them in a "what we did not control for" section.
remediation_language: "Standard confounds for this kind of analysis include X, Y, Z. The piece does not name them. A reader cannot judge how much weight to give the conclusion without knowing which confounds were considered and which were not. Add a confound inventory even if no remediation is offered."
example_failure_mode: A writeup attributes a quarterly lift in a metric to a product change, without noting that the same period contains a known seasonality bump and a concurrent pricing change in the underlying market.
```

---

## Grounding auditor

The grounding auditor re-queries the underlying data store(s)
independently of the draft's interpretation. This is the architectural
countermeasure for "coherence promoted to correctness": the lens never
sees what the draft says about the numbers — only the pointers to where
the numbers live — and verifies the claims against fresh queries.

```yaml
id: stale-references
description: Numerical claims in the prose were written from memory or from an earlier draft and have not been reconciled against a re-query of the underlying data.
trigger_heuristic: A round number, range, or rate appears in the narrative text but is not also present (with matching value) in a table or query result later in the same artifact; or values across drafts of the same piece have drifted without explanation.
remediation_language: "The narrative cites a number that does not appear in any data section of the artifact, or that disagrees with the data section by more than rounding. Either re-query and update the narrative to the current value, or pin the cited number to the table cell it comes from."
example_failure_mode: A draft opens with "we see ~30-50 retention events per day" written from memory during the first pass; the table built later from the actual query shows 86/day, and the opening narrative was never updated.
```

```yaml
id: baseline-not-checked-attribution
description: A trend or shift is attributed to a cause without first checking whether the historical baseline shows the same shift in periods where the proposed cause does not apply.
trigger_heuristic: The artifact contains "X went up after we did Y" or "X is higher now because Y", but no comparison series from a period before Y was introduced is presented; or no comparison group unaffected by Y is shown.
remediation_language: "Before attributing the observed shift to Y, verify against the historical baseline. Show what X looked like in a comparable period before Y existed, or show an unaffected control series for the same period. Without that, the attribution is decoration, not evidence."
example_failure_mode: A writeup says "registration rate jumped after we shipped the new onboarding flow" without checking that the same week-over-week jump appears every year at the same calendar moment regardless of product changes.
```

```yaml
id: contradicting-data-not-addressed
description: A single data point in the underlying data store materially contradicts the artifact's conclusion and is not addressed (or even named).
trigger_heuristic: The grounding lens, on independent inspection of the substrate, finds at least one cell, row, or metric value that points the opposite direction from the artifact's verdict and is absent from the artifact's discussion.
remediation_language: "The underlying data store contains observation X, which points the opposite direction from this artifact's conclusion. The piece does not address X. Either explain why X is consistent with the conclusion despite first appearances, or downgrade the conclusion to reflect that the evidence is mixed."
example_failure_mode: A writeup concludes Arm A clearly won on the headline metric, while the underlying data store records a same-group parity result on the conditional rate that this experiment's design was specifically set up to detect as a kill signal.
```

```yaml
id: citation-traceability-missing
description: A non-trivial numerical claim has no substrate pointer (chart link, query name, file path, table reference) that a reader can follow to verify it.
trigger_heuristic: A number appears in the prose that is not in any table in the same artifact and is not accompanied by a link / path / query name that a reader could use to re-derive it.
remediation_language: "This number has no traceable source. A reader cannot verify it without re-querying from scratch. Add a pointer (chart link, query SQL, file:line, table cell reference) next to every load-bearing number, or move the number into a table whose source is named."
example_failure_mode: A finding says "the long-tail segment is 7.2pp lower than the head segment" with no link to the chart, no query block, and no table; a reader who wants to audit the number has no way in.
```

---

## Structural auditor

The structural auditor checks internal consistency of the artifact — do
sections agree with each other? Do tables agree with the prose around
them? Is the verdict still derivable from the body after the latest
round of edits? Is the format fit for the consumer?

```yaml
id: zombie-claims
description: A claim was removed or weakened in one section of the artifact but the same claim (in different words) survives elsewhere.
trigger_heuristic: A targeted edit in section A weakens a claim; section B (or the abstract, or the closing) still contains the original claim in slightly different phrasing.
remediation_language: "The claim 'X' was weakened in section A, but the same claim still appears in section B as 'X-rephrased'. Either propagate the weakening, or explain why the two sections are intentionally saying different things."
example_failure_mode: A draft removes "higher-quality registrations" from the headline after the user flags that registration quality is confounded, but the same claim survives verbatim in the "what we do not know yet" section because the edit was a local string replacement.
```

```yaml
id: number-source-divergence
description: Two sections of the artifact present the same metric computed by different methods (or at different times), producing two values for what reads as one claim.
trigger_heuristic: The TL;DR / abstract cites a value V1 for metric M; a table later in the same artifact cites a value V2 for metric M; the prose treats V1 and V2 as the same number while the values differ by more than rounding.
remediation_language: "The TL;DR cites V1 for metric M; the table cites V2 for the same metric M. These were computed by different methods. Pick one method, recompute both sections from it, and state the method in the methodology block."
example_failure_mode: An abstract reports a metric as 22.3% computed from a simple daily average, while the table reports the same metric as 23.0% computed from a pooled rate — the abstract reader and the table reader walk away with different numbers.
```

```yaml
id: narrative-table-contradiction
description: A narrative summary directly contradicts the numbers in the table it summarizes.
trigger_heuristic: A sentence asserts a qualitative property ("essentially flat", "all segments aligned", "no movement") immediately above or below a table whose cells include a delta that contradicts the property.
remediation_language: "The narrative says 'X' (e.g. 'essentially flat'), but the table directly below contains a cell that contradicts X (e.g. a +100% relative cell). Either rewrite the narrative to match the table, or recompute the table if the narrative was written against an earlier version of the numbers."
example_failure_mode: A finding's narrative claims "all segments are essentially flat" while the table below it shows one segment at +2.4pp / +100% relative to its baseline, because the narrative was drafted against an older baseline that made the same absolute delta look small.
```

```yaml
id: hedge-vs-data-semantic
description: The strength of an assertion is not calibrated to the strength of the underlying evidence — either over-claimed ("comfortably", "decisively") on borderline evidence, or under-claimed on robust evidence.
trigger_heuristic: A sentence uses a strong adverb ("comfortably", "decisively", "clearly", "obviously", "definitively") next to a number whose confidence interval barely clears the threshold being claimed; or a sentence hedges ("seems to suggest") next to a result with very strong evidence and no obvious confounds.
remediation_language: "The wording asserts the result 'comfortably' / 'decisively' / 'clearly', but the underlying evidence is at p≈0.05 / the CI barely crosses zero / the effect size is small relative to noise. Either soften the adverb, or strengthen the evidence base before keeping the adverb."
example_failure_mode: A writeup says a metric "comfortably met the success threshold" when the value is one standard error above the threshold and the lower CI bound sits right at it.
```

```yaml
id: causal-claim-labelling
description: Claims are not explicitly labelled as observed vs inferred vs hypothesised — the reader cannot tell which load-bearing statements are evidence and which are interpretation.
trigger_heuristic: A causal verb ("because", "causes", "drives", "due to") appears in the body without an explicit label nearby tagging the claim as hypothesis, inferred, or observed; or interpretation language is rendered in the same voice as the data, making the layer ambiguous.
remediation_language: "This sentence makes a causal or interpretive claim that is presented in the same voice as the data. Label it explicitly — hypothesis, inferred, or observed — so the reader knows which load-bearing statements rest on data and which rest on interpretation."
example_failure_mode: An artifact says "the lift in Arm A is because we removed friction step F" — the lift is observed, the attribution to F is inferred, and the writeup treats both as equally established.
```

```yaml
id: audience-fit
description: The artifact's format does not match its intended consumer — e.g. dense tables in a message-format channel that does not render them, or in-line methodology explainers in a piece going to an audience that already shares the methodology.
trigger_heuristic: The artifact is destined for a known channel / audience (named in the prompt or inferrable from the file path) whose format constraints (renders markdown? prefers prose? expects verdict-first?) conflict with the artifact's structure.
remediation_language: "This artifact is going to audience X, whose format expectations are Y. The current structure does Z, which is a mismatch. Either restructure to Y, or change the destination."
example_failure_mode: A piece destined for a chat-format channel contains a four-column markdown table the channel does not render — the recipient sees raw pipe-separated text and gives up on the message.
```

```yaml
id: late-added-callouts
description: "Meta-flag: top-level summary, abstract, headline, or callout was added or revised after the body without being re-reviewed against the body."
trigger_heuristic: The artifact has a top-of-page summary / callout / TL;DR whose claims do not appear verbatim or in close paraphrase anywhere in the body; or the summary aggregates across frames that the body keeps separate.
remediation_language: "The top-level summary makes claim X, which is not derivable from the body as currently written. Either remove the claim from the summary, or add the supporting passage to the body. Late-added callouts are a leading source of zombie claims in this artifact class."
example_failure_mode: A writeup gains a "Quick read" callout in the final edit pass that aggregates a wider-scope number and a narrower-scope number into one statement; the body section that supports each frame separately is left unchanged and now disagrees with the callout.
```

---

## Mechanical prepass

The mechanical prepass runs *first*, completes in under 500ms, and uses
only regex / file-existence checks — no LLM call. Findings here are
cheap and pre-filter the lens prompts so the lenses do not waste tokens
re-detecting them.

```yaml
id: hedge-promotion-regex
description: Detect strong-adverb / certainty-promoting language anywhere in the artifact for the structural lens to calibrate against the evidence.
trigger_heuristic: Regex match on the case-insensitive set ["comfortably", "decisively", "clearly", "obviously", "definitely", "definitively", "undoubtedly", "certainly"] occurring in body prose (not inside fenced code or table headers).
remediation_language: "Strong-certainty word 'X' detected at <location>. Flag for structural lens to verify the underlying evidence supports this strength of claim; soften if it does not."
example_failure_mode: A draft contains the sentence "the experiment comfortably crossed the win threshold" — the mechanical pass surfaces the word so the structural lens can check whether the underlying value really sits comfortably above the threshold or only marginally.
```

```yaml
id: wikilink-resolution
description: Every [[wikilink]] reference in the artifact resolves to a file that actually exists in the project's knowledge base.
trigger_heuristic: For every [[slug]] or [[slug|display]] pattern in the artifact, attempt to resolve the slug to a file in the configured knowledge directory; flag any that does not resolve.
remediation_language: "Wikilink [[X]] does not resolve to an existing file. Either fix the slug, create the target file, or remove the link if the reference is no longer relevant."
example_failure_mode: A finding cites [[old-concept-name]] in its references section; the file was renamed in a prior cleanup and the link now points nowhere — the reader clicks through to a 404.
```

```yaml
id: could-vs-will-detection
description: Hedging language in a planning artifact ("we could X", "we might Y", "one option is Z") is not later treated as if it were a firm decision.
trigger_heuristic: The artifact contains a "could / might / one option" sentence introducing option O early on; a later section refers to O as the chosen path without an intervening explicit decision marker ("decided", "we will", "ship", "go with").
remediation_language: "Earlier in the artifact, X was framed as an option ('we could X'). Later sections treat X as the decided plan without a recorded decision. Either insert the decision explicitly, or weaken the later language to keep X as one option among several."
example_failure_mode: A planning doc opens with "we could route this through service A or service B" and later sections lay out the rollout plan assuming service A — the reader cannot tell whether the routing question was actually decided or simply assumed.
```

```yaml
id: acronym-on-first-use
description: Acronyms and abbreviations are defined on first use, unless they are universally known to the audience.
trigger_heuristic: Detect a token matching /\b[A-Z]{2,}\b/ on its first occurrence in the body; if the same token is not preceded or followed by an inline expansion in parentheses, flag it.
remediation_language: "Acronym 'X' is used without expansion on first occurrence. Either spell it out the first time and put the acronym in parentheses, or move the artifact to a context where 'X' is universally known to the audience."
example_failure_mode: A writeup uses "SRM" in the abstract with no expansion; a new joiner reading the piece does not know whether SRM means sample-ratio-mismatch, the SRM metric family, or something else entirely, and bounces.
```

---

## Notes for project overrides

A project override at `<project-root>/.claude/cc:proofread/criteria.md`
should follow the same YAML-with-fenced-blocks shape so the orchestrator
can parse both files with one routine. An override can:

- **Add** new project-specific checks with their own `id` values
- **Refine** an existing check by adding a `project_refinement` field with project-specific trigger heuristics or example failure modes (the orchestrator merges these into the default check definition)
- **Suppress** a default check by adding it to a `suppress:` list in the override's frontmatter, with a `reason:` field for each suppression

The default checks above are the universal baseline. Anything
domain-specific belongs in the override, not here.
