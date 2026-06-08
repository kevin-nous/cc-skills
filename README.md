# cc: skills

Personal Claude Code workflow skills. Combines the best of compound engineering, TDD, PRD patterns, and architecture review into one coherent pipeline.

## The Workflow

```
/cc:discover  →  /cc:decompose  →  /cc:build  →  /cc:review  →  /cc:learn
   idea            issues           TDD code      check it       document
```

Each step produces an artifact the next step consumes. Enter at any point.

## Skills

| Skill | Purpose | Inputs | Outputs |
|---|---|---|---|
| `/cc:discover` | Explore idea → stress test → PRD | Vague idea | `docs/prds/*.md` |
| `/cc:decompose` | PRD → independent issues | PRD file | `docs/issues/*.md` + wave plan |
| `/cc:build` | TDD red-green-refactor per issue | Issue file | Working code + tests |
| `/cc:review` | 4-layer review (functional, security, architecture, adversarial) | Code changes | Findings + fixes |
| `/cc:proofread` | Multi-lens pre-publish review for analytical writing (vault findings, PRDs, Slack drafts). Read-only. | Prose artifact (file or piped text) | Per-lens findings + coverage line + pre-mortem prompt |
| `/cc:learn` | Document what was learned | Any experience | `docs/solutions/*.md` |

## Companion helpers

The skills above depend on two helpers that ship in this repo:

| File | Installs to | Role |
|---|---|---|
| `commands/grill-me.md` | `~/.claude/commands/grill-me.md` | Vault-aware grilling skill. Invoked by `cc:discover` Phase 2. Stress-tests a plan against existing concept pages, queues proposed concept-page edits + decision findings for batch confirmation at the end. |
| `agents/learnings-researcher.md` | `~/.claude/agents/learnings-researcher.md` | Vendored subagent. Invoked by `cc:discover` Step 0 to surface prior vault knowledge before the dialogue starts. |

## What it combines

| Source | What we took | What we dropped |
|---|---|---|
| **Compound Engineering** | Compound learning system, multi-agent review, grill-me interrogation | Separate brainstorm/grill (merged into discover) |
| **Matt Pocock TDD** | Red-green-refactor discipline, vertical slicing | — |
| **Matt Pocock PRD** | Structured PRD with testable acceptance criteria | — |
| **Matt Pocock Issues** | Independent issue decomposition with waves | — |
| **Matt Pocock Architecture** | Structural confusion detection | Standalone skill (merged into review) |

## Install

```bash
# Clone
git clone https://github.com/kevin-nous/cc-skills.git ~/cc-skills

# Symlink into global Claude skills
ln -sf ~/cc-skills/cc:discover ~/.claude/skills/cc:discover
ln -sf ~/cc-skills/cc:decompose ~/.claude/skills/cc:decompose
ln -sf ~/cc-skills/cc:build ~/.claude/skills/cc:build
ln -sf ~/cc-skills/cc:review ~/.claude/skills/cc:review
ln -sf ~/cc-skills/cc:proofread ~/.claude/skills/cc:proofread
ln -sf ~/cc-skills/cc:learn ~/.claude/skills/cc:learn

# Companion helpers (cc:discover invokes these)
ln -sf ~/cc-skills/commands/grill-me.md ~/.claude/commands/grill-me.md
ln -sf ~/cc-skills/agents/learnings-researcher.md ~/.claude/agents/learnings-researcher.md
```

## When to use what

- **Starting something new?** → `/cc:discover`
- **Know what to build, need a plan?** → `/cc:decompose`
- **Ready to code?** → `/cc:build`
- **Code is done, check it?** → `/cc:review`
- **Learned something worth keeping?** → `/cc:learn`
- **Quick fix, no ceremony needed?** → Just code. Not everything needs the full pipeline.

## Tests

The `cc:proofread` engine ships a full pytest suite (496 tests — schema validation, lens orchestration, snapshot-based output rendering, mechanical-prepass checks). All paths resolve relative to the repo, so it runs straight from a clone:

```bash
pip install -r requirements.txt
cd cc:proofread && python3 -m pytest scripts/tests/ -q
```
