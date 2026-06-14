# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This is a **single-context** repo (one library, not a monorepo).

## Before exploring, read these

This repo predates the `CONTEXT.md` / `docs/adr/` convention, so its locked design
knowledge lives in equivalents:

- **`docs/DESIGN V2.md`** — the locked architecture (the de-facto system ADR set).
- **`docs/DECISIONS - V2.1.md`** — the decision record; treat each entry like an ADR.
- **`docs/SDK.md`** — the authoring contract (how drivers/buses/topologies are written).
- **Module docstrings in `src/shal/*.py`** — each states the file's invariants; this is
  the de-facto glossary. Read the docstring of any module you touch.

If a `CONTEXT.md`, `CONTEXT-MAP.md`, or `docs/adr/` directory appears later, prefer it
and read it alongside the above. If any referenced file doesn't exist, **proceed silently**.

## File structure

Single-context repo:

```
/
├── AGENTS.md                         ← project guide + this Agent skills config
├── docs/
│   ├── DESIGN V2.md                  ← locked architecture (de-facto ADRs)
│   ├── DECISIONS - V2.1.md           ← decision records
│   ├── SDK.md                        ← authoring contract
│   └── agents/                       ← this skill's output
└── src/shal/                         ← the package (module docstrings = glossary)
```

## Use the project's vocabulary

When your output names a domain concept (an issue title, a refactor proposal, a
hypothesis, a test name), use the term as the project uses it — the canonical phrasing
is *"a bus is just a node that provides a transport to its children"*, transport **kinds**
(ByteTransport / CommandTransport / MessageTransport / Stream), **capabilities**,
**drivers** bound by `compatible`, declared **operating limits**. Don't drift to synonyms.

If the concept you need isn't named anywhere in the docs or docstrings, that's a signal —
either you're inventing language the project doesn't use (reconsider) or there's a real gap.

## Flag decision conflicts

The decisions in `docs/DESIGN V2.md` / `docs/DECISIONS - V2.1.md` are **locked**. If your
output contradicts one, surface it explicitly rather than silently overriding:

> _Contradicts the "delivery-unknown writes are never auto-retried" decision — but worth
> reopening because…_
