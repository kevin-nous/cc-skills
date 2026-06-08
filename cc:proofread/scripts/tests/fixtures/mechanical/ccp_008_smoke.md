---
title: ccp-008 smoke fixture — file-convention identifiers + real acronyms
description: Targets the ccp-016 false-positive (SKILL.md) and the
  hardcoded "Sample Ratio Mismatch" remediation bug, while still
  exercising a handful of genuine prose acronyms.
---

# Authoring guide

The SKILL.md declares the skill's purpose. The PRD.md and README.md sit
alongside it. The MOC.md at the vault root indexes the surrounding
directory. None of these should be flagged as undefined acronyms — they
are filename references, not in-prose initialisms.

The TODO and FIXME annotations are allowlist conventions; the NB / IMO /
IIRC abbreviations are similar editorial shorthand. None should fire.

## Real acronyms still expected to fire

The SRM check failed on the latest read, and the JSONL output stream
buffered until the MCP transport closed. The DUOS allocation continues
to be a regional pricing discriminator the FOOBR methodology has not
addressed.

JSON parsing is fine; we use the standard HTTP transport over HTTPS,
serialise to CSV, and route via the REST API. Time fields are UTC. The
artifact targets the UK market and reports back to the CEO via the PM.

The TLDR is: file-convention identifiers should be silent, and the
remaining genuine acronyms (SRM, JSONL, MCP, DUOS, FOOBR) should fire
with sensible remediation text that does not hardcode any unrelated
worked example.

## Padding to ~10KB so the fixture matches the ccp-008 smoke scope

The padding below is filler prose that should produce zero additional
findings. It exists so the fixture file size is in the same ballpark as
the artifacts the orchestrator integration test feeds the prepass —
otherwise a token-budget regression in this file would be invisible
until ccp-008 ran the full end-to-end loop.

The mechanical prepass walks the artifact body line by line, after the
frontmatter and any fenced code blocks have been stripped. The four
checks run in sequence and emit JSONL records on stdout. Coverage is
reported at the tail as a single terminal record. The contract is
read-only: no file is ever modified, no stderr is written from a
successful run, and no network call is ever made. The prepass is meant
to be cheap to run on every save, so the 500ms budget on a 10KB artifact
is the load-bearing acceptance criterion.

A bug surfaced by the ccp-008 end-to-end smoke test motivates this
fixture. First, an acronym remediation that hardcodes an unrelated
worked example (the SRM expansion) is itself the kind of confidently
wrong text the skill is designed to catch. Second, file-convention
identifiers like SKILL.md being flagged as undefined acronyms produces
noise that drowns out the genuine signal in any cc:* authoring
artifact, where SKILL.md is the canonical entry point.

The fix is two-line: extend the allowlist with editorial conventions
and add a filename-extension guard. The test fixtures here are the
regression net. If a future contributor reverts the allowlist or the
guard, this fixture will start flagging SKILL.md again, and the unit
test that loads it will fail loudly.

The padding continues so that the fixture lands close to ten kilobytes
of UTF-8 text. The prepass tokenises the artifact via the `regex`
library, walks the four pre-compiled patterns over the stripped body,
and records a per-check coverage breakdown on the tail. None of the
prose below contains acronyms, hedge words, decision-verb hedges, or
unresolved wikilinks — by design.

The orchestrator (ccp-005) takes the prepass JSONL output, the
structural lens output, and the grounding lens output, and aggregates
them into a single per-check rollup. The render layer (ccp-019)
transforms the rollup into the user-facing markdown report. The
top-level contract is that the report never green-checkmarks the
artifact — every check either flags a problem or stays silent.

The padding above exists purely to land this fixture in the ten-kilobyte
range so the smoke test exercises a realistic artifact size. No claim,
hedge, wikilink, or undefined acronym is hidden in the filler text.
