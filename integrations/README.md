# integrations/ — agent-host adapters (not the core)

SHAL's core is **agent- and model-agnostic**: the framework, the `shal` CLI, the
MCP bridge, and the authoring **contract** (`docs/SDK.md` + the shipped `shal docs`
guide) don't privilege any one agent host. Anything specific to a single host —
Claude Code, Cursor, Codex, … — is an *adapter* and lives here, never in `src/shal/`.

This folder is a **seam**: it is expected to graduate into its own
`shal-integrations` repo. Keep each host self-contained under `integrations/<host>/`
so the lift-and-shift stays clean.

## Layout

```
integrations/
  claude-code/
    skills/            # Claude Code skills (shal-build-*, shal-generate-driver, shal-brand)
```

## What these are (and aren't)

- They **render** the agnostic authoring contract (`docs/SDK.md`,
  `src/shal/AGENT_GUIDE.md`) for one host. They are not a second source of truth —
  if a skill and the contract disagree, the contract wins (see AGENTS.md,
  "Keep the skills in sync").
- They are **not** shipped in the `pyshal` wheel. A `pip install pyshal` user gets
  the neutral path (`shal docs` + `docs/SDK.md`); the rich, host-specific skills are
  installed separately.

## Using the Claude Code skills

They are auto-discovered only from a Claude Code skills directory (e.g.
`~/.claude/skills/` or a repo-root `.claude/skills/`). To use them, install from here
— copy/symlink `integrations/claude-code/skills/shal-*` into your skills directory,
or publish/add them via a skill hub (e.g. ClawHub: `clawhub skill publish <path>`).
