# cc:proofread — experiments/checkout-redesign-v2.md
Run: 11111111-1111-4111-8111-111111111111 · started 2026-05-29T15:00:00Z · completed 2026-05-29T15:01:32Z

## Findings (3)

### scope auditor — scope-drift [advisory]

**Claim:** Headline: V2 +6.4pp on signup_completed.
**Location:** experiments/checkout-redesign-v2.md:lines 12-14 (TL;DR)

The +6.4pp headline uses a wider cohort than the experiment's spec scope, which reads +8.8pp p=0.059 on measurement_plan.primary_window_days: 14. The reader sees a confidently positive number that was not the pre-registered scope. The favourable-scope migration is not disclosed.

**Remediation:** Lead with the spec-scope number (+8.8pp p=0.059). If the wider-cohort number is also presented, label its scope and the divergence explicitly.


### structural auditor — hedge-vs-data-semantic [advisory]

**Claim:** V2 isn't shedding switches at handoff.
**Location:** experiments/checkout-redesign-v2.md:line 78 (Latest read)

The assertion's strength outruns the supporting CI. The handoff differential is within sampling noise at the cited n, so 'isn't shedding' reads as a structural claim where the data only supports a wide hedge.

**Remediation:** Either widen to 'no signal of shedding within current n' or cite the explicit interval.


### mechanical prepass — hedge-promotion [advisory]

**Claim:** V2 comfortably met the success bar on signup_completed.
**Location:** line 23 (Result)

Hedge-promotion: 'comfortably' asserts more confidence than the underlying CI supports (p=0.059 just clears the 0.05 threshold). The adverb is doing rhetorical work the data does not authorise.

**Remediation:** Drop 'comfortably' or replace with the literal CI bound. Calibrate the adverb to the actual margin.

## Coverage

- scope auditor: 14 claims checked, 1 flagged
- grounding auditor verified 4 numerical claims against substrate; none contradicted (4 substrate refs found, 0 unresolvable)
- structural auditor: 12 claims checked, 1 flagged
- mechanical prepass: 30 claims checked, 1 flagged

## Pre-mortem

Imagine this artifact is proven wrong in 6 weeks. Which sentence is the culprit?