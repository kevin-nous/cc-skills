---
name: "cc:proofread"
description: "Multi-lens pre-publish reviewer for analytical writing — vault findings, PRDs, Slack drafts, planning artifacts. Runs 3 parallel fresh-context audit lenses + mechanical prepass. Read-only: never writes to the artifact, never green-checkmarks. Use before publishing prose you'd regret being wrong about."
---

# /cc:proofread — Multi-lens pre-publish review for analytical writing

`cc:proofread` is a manually-invoked review skill that runs three parallel
fresh-context audit lenses + a mechanical prepass over any analytical or
planning artifact (vault finding, experiment writeup, PRD, Slack draft,
research plan, exec brief). It catches the failure mode where an internally
consistent argument is persuasive and wrong — *coherence promoted to
correctness* — by re-querying the project's substrates independently of the
draft's narrative. Use it before publishing prose you'd regret being wrong
about. For code review, use `/cc:review` instead — that skill owns
functional / security / architecture / adversarial review.

---

## Invocation

```
/cc:proofread <file-path>            # audit a file (vault finding, PRD, etc.)
/cc:proofread                        # then paste / pipe text in
/cc:proofread --dry-run <path>       # print the lens prompts; don't dispatch
/cc:proofread --no-tag <path>        # skip the proofread-passed-<date> frontmatter write
/cc:proofread --deep <path>          # one fresh-context subagent per check (12–15 agents); slower
```

Run with no arguments to print this usage and exit cleanly.

The `--dry-run`, `--no-tag`, and `--deep` flags are all wired through the
orchestrator since Wave 5 (see § Orchestration below for behaviour and
§ Debug flags for `--dry-run` / `--criteria-only` / `--keep-temp`).
See `docs/issues/ccp-*.md` for the per-wave build log.

---

## Read-only contract (load-bearing — do not weaken)

This is the architectural property that distinguishes `cc:proofread` from
"another LLM-as-judge that grades my draft." Treat it as binding.

- **Auditors NEVER write to the artifact.** Lenses produce findings;
  the user reads them and decides what to fix. No auto-fix mode.
- **Auditors NEVER write to any shared file.** No edits to `state.yaml`,
  `vault/knowledge/*`, `.claude/*`, project configs, or any file outside
  this skill's own scratch space.
- The **only** artifact write the skill itself performs is appending
  `proofread-passed-<YYYY-MM-DD>` to the `tags:` array of a vault
  finding's frontmatter when the run completes cleanly (single field,
  single date — replaced on re-run, not multi-logged). That write lives
  in ccp-014 and is opt-out-able via `--no-tag`.
- Per-lens findings go to `/tmp/cc-proofread-<run-id>/<lens>.jsonl`
  only. Nothing under `/tmp/cc-proofread-*` is checked in or read by
  any other skill — it is run-local scratch.
- No green checkmarks. No "audit passed." No cumulative pass language.
  Output is "0 findings" or "N findings" plus a coverage transparency
  line that names what was and wasn't checked.

If a future change appears to violate any of the above, it belongs in a
different skill — not in `cc:proofread`.

---

## Convergent-blind-spot countermeasure

The grounding lens MUST re-query substrates independently of the draft's
narrative — encoded as a testable architecture criterion (see
`docs/prds/2026-05-29-cc-proofread.md` § Architecture). The grounding
lens's prompt receives only the substrate pointers extracted in the
discovery step; it does NOT receive the draft's interpretation of those
substrate values. This is the property that prevents the lenses from
inheriting the same blind spot the draft has.

---

## Criteria resolution

The actual check list lives in a criteria file, not inline in this
SKILL.md. At invocation, the skill resolves:

1. **Project override (preferred):** `<project-root>/.claude/cc:proofread/criteria.md`
2. **Skill default (fallback):** `~/cc-skills/cc:proofread/default-criteria.md`

If the project file exists, it takes precedence outright (no merge — the
project owns its catches). If it doesn't, the skill silently falls back
to the default. Missing project file is not an error.

The default ships with the 12 substantive checks + 3 mechanical checks in
domain-neutral language (see `default-criteria.md` for the full list).
A project can ship an override at
`<project-root>/.claude/cc:proofread/criteria.md` that re-flavours the
language in project terms (e.g. cohort, baseline, conversion window).

To add a project override:

```bash
mkdir -p <project-root>/.claude/cc:proofread/
cp ~/cc-skills/cc:proofread/default-criteria.md \
   <project-root>/.claude/cc:proofread/criteria.md
# edit to your domain
```

---

## Architecture (one-screen reference)

```
mechanical prepass (single process, <500ms, regex only)
   │
   ▼
orchestrator: extract substrate pointers (discovery step)
   │
   ├──▶ scope auditor subagent (fresh context)
   ├──▶ grounding auditor subagent (fresh context, independent substrate access)
   └──▶ structural auditor subagent (fresh context)
        (3 parallel via Agent tool, run_in_background: true, single message)
   │
   ▼
orchestrator: aggregate findings, dedupe by root cause, emit output
   ├─ findings list per lens (no green checkmarks)
   ├─ coverage transparency line
   └─ pre-mortem prompt (last line)
```

Lenses do not see each other's outputs. The grounding lens does not
receive the draft's narrative interpretation of substrate values — only
the substrate pointers themselves. Both properties are testable by
inspecting prompt construction.

---

## Output shape

Every run produces, in this order:

1. Per-lens findings, labelled by lens. Each finding has shape
   `<lens>: <claim being audited> — <contradiction or concern> — <suggested resolution>`.
2. A **coverage transparency** line:
   `Coverage: scope auditor [N claims checked, M flagged] / grounding auditor [P substrate refs, Q verified, R contradicted, S unresolvable] / structural auditor [T cross-refs checked]`
3. A **pre-mortem prompt** as the *last* line:
   `Pre-mortem: imagine this is proven wrong in 6 weeks. Which sentence is the culprit?`

Quantified passes are emitted explicitly: if the grounding lens has
substrate and finds nothing wrong, output reads
`grounding auditor verified N numerical claims against substrate; none contradicted`.

---

## Orchestration

The skill's runtime is a two-phase Python harness (`scripts/orchestrator.py`)
bracketing one Claude-runtime step (Anthropic Agent dispatch). The split
exists because Python can't natively invoke the Agent tool, but the
deterministic parts (criteria resolution, mechanical prepass, discovery,
prompt assembly, aggregation, render) must stay testable in isolation —
hence two CLI phases either side of the LLM call.

When `/cc:proofread <artifact>` is invoked, Claude executes this procedure:

1. **Run pre_lens.** Invoke:

   ```
   python3 scripts/orchestrator.py --phase pre_lens <artifact-path> \
       --project-root <project-root>
   ```

   Capture stdout. A normal run prints a multi-line `PROMPTS_READY`
   signal:

   ```
   PROMPTS_READY <run-id>
   scope: /tmp/cc-proofread-<run-id>/scope_prompt.md
   grounding: /tmp/cc-proofread-<run-id>/grounding_prompt.md
   structural: /tmp/cc-proofread-<run-id>/structural_prompt.md
   ```

   The first line carries the `<run-id>` directly; the next three lines
   name each prompt path explicitly. A consumer that reads only the
   first line can still locate prompts by convention
   (`/tmp/cc-proofread-<run-id>/<lens>_prompt.md`), but the explicit
   listing is preferred. With `--dry-run` the signal is
   `DRY_RUN_READY <tmp-dir>` instead and no prompts are written.

2. **Invoke 3 lens subagents IN PARALLEL.** Send a SINGLE message
   containing 3 Agent tool calls — one for the scope lens, one for the
   grounding lens, one for the structural lens. Each call uses
   `subagent_type=general-purpose` and `run_in_background: true`. Each
   subagent's prompt is the *contents* of its respective
   `<lens>_prompt.md` file (read each file and pass the text as the
   prompt; the prompt is self-contained — artifact / pointers / checks /
   diff are already inlined, and the prompt names the exact
   `/tmp/cc-proofread-<run-id>/<lens>.jsonl` path the subagent must
   write to).

   No subagent receives any other context (no prior conversation, no
   other lens's output, no mechanical findings). That isolation is the
   convergent-blind-spot countermeasure (see section above) — the three
   lenses run independently so they cannot converge on a shared blind
   spot inherited from the draft's narrative.

   The parallelism is load-bearing. Running the lenses sequentially via
   chained Agent calls would let the second lens accidentally see the
   first lens's findings via context bleed; three independent
   background dispatches in one outer message prevents that.

3. **Wait for all 3 subagent completions.** Claude Code surfaces a
   task-notification when each background agent finishes. Wait for all
   three. Do not poll; do not assume any subagent succeeded — the next
   step verifies by reading each `<lens>.jsonl`.

4. **Run post_lens.** Invoke:

   ```
   python3 scripts/orchestrator.py --phase post_lens <run-id>
   ```

   The post_lens phase aggregates `mechanical.jsonl`, `discovery.jsonl`,
   `scope.jsonl`, `grounding.jsonl`, and `structural.jsonl`, builds the
   run record, calls `render_output.render()`, and prints the final
   report to stdout. Tmp dir is cleaned up after (use `--keep-temp` for
   debug).

If any subagent fails to write its `<lens>.jsonl` (subagent crashed,
network error, malformed response), the aggregator records it as a
missing-file error and the render's Coverage section reports that lens
as failed — not silently omitted. The other two lenses still render
their findings normally; one lens failing does not abort the run.

### --deep mode (high-stakes artifacts)

For high-stakes artifacts (PRDs, ship-decisions, exec briefs), invoke
with `--deep`. The pre_lens phase writes 16 per-check prompts (6 scope +
4 grounding + 6 structural) instead of 3 lens-level prompts, PLUS one
extra lens-level structural prompt dedicated to the
`late-added-callouts` meta-flag (which depends on the git diff, not on
per-check semantic — so it sits at the lens level even in deep mode).

Dispatch 17 Agent calls in a single message — each subagent reads its
own `<lens>-<check_id>_prompt.md` (or `structural-late-added-callouts_prompt.md`)
and writes its findings to the matching `.jsonl` path named inside the
prompt. The post_lens phase fans in all 17 JSONL files automatically.

Cost is ~5× default mode. The rendered output carries a final
`Note: --deep mode used; ~5x token cost vs default.` line so the cost
is visible at audit time.

### --no-tag mode (skip the audit-tag write)

The post_lens phase appends `proofread-passed-<YYYY-MM-DD>` to the
artifact's frontmatter `tags:` array on clean passes (zero blocking
findings) — but only when the artifact is a vault finding
(`vault/knowledge/*.md` or `findings/*.md` with `type: finding` in
frontmatter). For non-vault artifacts (PRDs, Slack drafts), no tag is
ever written.

Pass `--no-tag` to suppress the write even for eligible vault findings.
The rest of the run is unchanged; only the `Tagged artifact:` line in
the rendered output is omitted.

### Validation

The skill ships an end-to-end validation gate at
`tests/v2-validation/manual-smoke-test.md`. Run that procedure against
a known-bad artifact to verify the 5 named failure modes (scope drift,
mechanism-as-clock, contradicting data unaddressed, causal mislabelling,
hedge-vs-data semantic) all surface in the rendered output. The gate
runs the FULL pipeline (real LLM lenses, real substrates) — the
deterministic CI tests under `tests/v2-validation/run_test.py` mock the
lens outputs but cover the same architectural assertions.

### Debug flags

- `--dry-run` (pre_lens) — skip the LLM call, write a stub `scope.jsonl`
  marking the lens as not-invoked. Lets you exercise the deterministic
  layers without burning subagent quota.
- `--criteria-only` (pre_lens) — print which criteria.md was loaded
  plus the scope-lens checks extracted from it; exit. Useful for
  verifying a project override took effect.
- `--keep-temp` (post_lens) — do not delete `/tmp/cc-proofread-<run-id>/`
  after rendering. Inspect the raw JSONL by hand.

---

## Status

Built in waves under `docs/issues/ccp-*.md`. See that issue
list for current build state — each ccp-NNN issue corresponds to one
piece of the skill (scaffold, criteria, lens prompts, orchestration,
output formatting, frontmatter tag, integration tests). The skill is
usable when ccp-001 through ccp-014 are green.

## Related

- PRD: `docs/prds/2026-05-29-cc-proofread.md`
- Reference skill (similar shape): `~/cc-skills/cc:learn/SKILL.md`
- Sibling skill (code review, different scope): `~/cc-skills/cc:review/SKILL.md`
- Failure-mode catalogue: `vault/knowledge/2026-04-14-incremental-editing-drift.md`
