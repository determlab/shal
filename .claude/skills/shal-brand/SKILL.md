---
name: shal-brand
description: SHAL's visual identity & design system — logo concept and tests, color palette, typography, the blueprint diagram language, and ready-to-use AI image-generation prompts (hero, OG, social). Use whenever creating or reviewing a SHAL visual asset, a logo, a diagram, or an image-generation prompt, or when something must look "on brand."
---

# SHAL brand & design system

SHAL is **agent-native infrastructure** for labs and production systems. The brand
must read as **precise, trustworthy, engineering-grade** — calm and exact, not
flashy. The visual idea is the product idea: **a bus is a node that carries its
children**, so the recurring motif is a **node-graph / tree**.

**Locked design choices**
- **Style:** technical / blueprint — thin line-art, schematic, dotted grid, monospace labels.
- **Color:** a **blue → violet gradient** reads "AI / agent" and marks the *active capability path*.
- **Logo motif:** a branching node-graph (root → children), edges drawn as routed bus traces.

---

## Color

| Token | Hex | Use |
|---|---|---|
| **Signal Blue** | `#1f6feb` | gradient start; primary solid; links |
| **Agent Violet** | `#7c3aed` | gradient end; accent |
| **Brand gradient** | `#1f6feb → #7c3aed` @135° | the mark, and the one "active path" in any diagram |
| **Ink** | `#0d1117` | dark background / text on light |
| **Paper** | `#f6f8fa` (or `#ffffff`) | light background |
| **Line (dark)** | `#30363d` | grid + inactive strokes on Ink |
| **Line (light)** | `#d0d7de` | grid + inactive strokes on Paper |

**Rules**
- The gradient is **reserved** for the logo mark and the single highlighted path
  (devices → SHAL → agent). Everything else is muted line color. Don't gradient-wash
  the whole asset.
- Maintain WCAG-AA contrast for any text. Gradient never carries small text.
- Works on Ink (default, dev-native) and Paper. Provide both.

---

## Typography

- **Wordmark / display:** Space Grotesk (or Inter) — geometric, tight, engineering-friendly.
- **Body:** Inter.
- **Mono (labels, schematic captions, code):** JetBrains Mono / IBM Plex Mono.

Wordmark **SHAL** is set in caps, slightly tightened tracking, with an optional
monospace tagline beneath: `agent-native infrastructure`.

---

## Logo

**Concept** — a small **node-graph tree**: one root node fanning out to children
across 2–3 levels. Nodes are small filled dots (or 2px-stroke rounded squares);
edges are **orthogonal "bus traces"** with rounded corners. The brand gradient runs
**root (blue) → leaves (violet)**, encoding "capabilities flow outward to the agent."

```
        ●            root  (Signal Blue)
       ╱ ╲
      ●   ●          buses
     ╱ ╲
    ●   ●            leaves (Agent Violet)     SHAL
```

**Lockups**
- **Horizontal:** mark + `SHAL` wordmark (default; READMEs, sites).
- **Stacked:** mark over wordmark (square placements, avatars).
- **Mark only:** the node-graph (favicon, app icon).

**Construction**
- Equal stroke weight; nodes sized in a 1 : 1.5 ratio to stroke.
- Clear space = height of the root node on all sides.
- Min sizes: mark 16 px (favicon), horizontal lockup 96 px wide.

### Logo tests (validate every revision)
- [ ] **16 px favicon** — still legible as a node-graph, not mush.
- [ ] **1-color** (pure Ink, and pure Paper knockout) — works with no gradient.
- [ ] **On Ink and on Paper** — both backgrounds.
- [ ] **Grayscale** — survives without color.
- [ ] **Edge crop / avatar circle** — mark stays centered, no clipping.
- [ ] **Beside the wordmark** — optical balance, shared baseline.
- [ ] **Tiny + huge** — no detail that breaks at 16 px or looks bare at 512 px.

**Don't:** add bevels/3D, photoreal textures, drop shadows, more than the two brand
hues, or skeuomorphic chips/wires. Keep it flat, schematic, exact.

---

## Diagram language (blueprint)

All SHAL diagrams share one language so the README, docs, and slides feel of a piece:
- **Near-black (Ink) canvas** with a faint **dotted grid**.
- **Thin uniform strokes** (~1.5–2 px) in Line color; **rounded orthogonal connectors**
  ("bus traces"), minimal arrowheads.
- **Nodes** = small dots or 2px rounded squares; **mono labels**.
- **One gradient path** highlights the flow being explained; all else muted.
- Flat — no fills, glassy cards, or 3D.

---

## AI image generation

**Prompt formula** (always): `Subject + Setting + Style + Lighting + Composition + Technical`.

**Model choice**
- **Text-free** illustration (preferred — overlay labels later): GPT Image / ChatGPT
  Images, Flux, or Gemini.
- **Crisp short labels baked in:** Ideogram 3.0 (best text), else overlay in
  Figma/HTML. Most models butcher text — keep it out of the prompt and overlay.

**Sizes**
- GitHub social / OG: **1280×640** (2:1). GPT Image native: generate **1536×1024**
  landscape and crop to a banner — leave margins.
- Website hero: 1536×1024 → crop to ~1600×600.

**Reusable SHAL prompt block** (paste, then describe the specific scene):
```
Style: precise blueprint / schematic line-art, thin uniform strokes, flat (no 3D,
no bevels, no drop shadows), engineering-diagram aesthetic. Near-black canvas
(#0d1117) with a faint dotted grid; inactive strokes in muted blue-grey (#30363d).
A single blue→violet gradient (#1f6feb → #7c3aed) is reserved for the active path.
Rounded orthogonal "bus-trace" connectors; small node dots. Even flat lighting,
subtle outer glow only on the gradient path. High contrast, vector-clean crisp
edges. No text, no logos, no people, no photorealism. Leave generous margins for
banner cropping. 1536×1024 landscape.
```

**Hero example** (the four-block diagram, upgraded) — see the chat output / the
README replacement; subject = real-world devices (sensor, power supply, robot,
server, cloud) as labeled nodes on the left, converging into a central SHAL
node-graph hub, emitting a stack of identical "tool" cards to a minimalist AI-agent
mark on the right; gradient traces devices → SHAL → agent.

**Optimization:** export WebP (q 80) + PNG fallback; set width/height; add alt text;
keep < 200 KB.

---

## Asset checklist
- [ ] `docs/assets/logo.svg` (horizontal), `logo-mark.svg`, `logo-stacked.svg`
- [ ] `favicon.svg` / `favicon.png` (16, 32, 180)
- [ ] `docs/assets/hero.png` + `.webp` (replaces the README mermaid block)
- [ ] `og.png` (1280×640) for social previews
- [ ] Both Ink and Paper variants where shown on either background
