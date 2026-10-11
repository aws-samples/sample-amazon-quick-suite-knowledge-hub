---
description: "Instructions for the background sub-task that builds a showcase's use-case STORY deck (PPTX + interactive HTML) from its demo script and scenario data (persona, acts, business value), not a catalog of Amazon Quick product features. Loaded on demand by the quick-showcase-studio create flow."
last_updated: 2026-10-01
origin: original
---

## Overview

This reference is loaded **inside a background sub-task** dispatched by the `quick-showcase-studio` create flow (see `references/create-showcase.md`). It produces a **use-case story deck** — a leave-behind that summarizes *this showcase's demo*: who the persona is, what happens in each act, and the business value.

> **⚠️ This is NOT a product-features catalog.** Do not enumerate Amazon Quick's capabilities and do NOT load any "Amazon Quick guide." A capabilities list goes stale as the product evolves; a **use-case story** stays evergreen because it describes the customer scenario. Build entirely from the **source script and data** passed in the objective.

**Outputs (both required), saved to `OUTPUT_PATH`:**
1. **`<DEMO_NAME>-use-case-deck.pptx`** — story slide deck
2. **`<DEMO_NAME>-use-case-deck.html`** — interactive story page

---

## ⚠️ OUTPUT FILENAMES — ABSOLUTE RULE

Use the **exact** names from the objective (they are prefixed with the showcase's `DEMO_NAME`):
```
<DEMO_NAME>-use-case-deck.pptx
<DEMO_NAME>-use-case-deck.html
```
No variations. Save both to the `OUTPUT_PATH` from the objective.

---

## Workflow

### Step 1: Parse Parameters from the Objective
- **Mode**: `agentic`
Extract: `DEMO_NAME` (filename prefix), `CUSTOMER_NAME` (may be "N/A"), `INDUSTRY_NAME`, `Source script` path, `Source data` path, `OUTPUT_PATH`, and the exact output filenames.

> **Note — `OUTPUT_PATH` may not exist yet.** Create it if needed (or build in `workspace/artifacts/` first and copy the finished files in). Verify the path resolves before relying on it.

### Step 2: Read the showcase's story (the source of truth)
- **Mode**: `deterministic`
- Read the **source script** (`<DEMO_NAME>-demo-script.md`) — it has the persona, the acts (What the audience sees / Demo Action / Expected Result / Talking Points), and the value.
- Optionally read the **source data** (`<DEMO_NAME>-demo-scenario-data.json`) for KG highlights (key people, projects, relationships) and the activity-feed headline items.
- Everything on the slides/page must come from these files — no invented facts, no product-feature catalog.

### Step 3: Organize the story
- **Mode**: `agentic`
Pull out: the **persona** (name, role, company/industry context), the **setup** (what's on their plate), each **act** (title, the situation/trigger, what Quick produces, why it matters), the **Knowledge Graph highlight** (how connected context powers the payoff), and a **closing value** statement. Keep it faithful to the script.

### Step 4: Build the Story Deck (PPTX)
- **Mode**: `deterministic`
- **Tool**: `load_skill("canvas_pptx")`, then build with pptxgenjs.
- **Output**: `OUTPUT_PATH/<DEMO_NAME>-use-case-deck.pptx`

> **⚠️ Build reliability notes (from prior runs — read before building):**
> - **Await the write; do not rely on a trailing `.then()`.** In the JS sandbox, top-level promises may not flush before the process exits, so `pptx.writeFile({...}).then(...)` often returns "No output". Wrap the build in an awaited async IIFE and log the resolved path:
>   ```js
>   (async () => { await pptx.writeFile({ fileName: outPath }); console.log('WROTE', outPath); })();
>   ```
> - **Sandbox module restrictions.** Only a fixed module set is allowed; `require('child_process')` and `require()` of arbitrary absolute paths are blocked. Inline the build logic — don't shell out or dynamically require external files.
> - If a build call returns no output, confirm the file separately (stat it) rather than rebuilding blindly.

Suggested slides (adapt to the actual number of acts):
- **Slide 1 — Title:** the showcase name ("<Customer/Industry> — Amazon Quick showcase"), a one-line value proposition, and the persona (name + role). Speaker notes: how to open the demo.
- **Slide 2 — The setup:** who the persona is and what's on their plate that morning. Speaker notes: framing.
- **Slides 3…N — One per act:** act title, the situation/trigger, what Quick produces, and the business value. Speaker notes: the exact demo prompt + what to point out.
- **Second-to-last — Knowledge Graph:** how connected context (people/projects/relationships from the data) powers the deliverables. Speaker notes: the KG reveal.
- **Last — Value / close:** the outcomes and the takeaway. Speaker notes: closing talking points.

**Design:** professional, clean, ≤6–7 bullets per slide, consistent hierarchy, meaningful speaker notes (3–5 points) on every slide.

### Step 5: Build the Interactive HTML
- **Mode**: `agentic`
- **Tool**: `load_skill("html_design")`, then create a single self-contained file.
- **Output**: `OUTPUT_PATH/<DEMO_NAME>-use-case-deck.html`

A single-file, self-contained interactive page telling the same story:
- **Header:** the showcase name + persona + one-line value.
- **Act cards/sections:** one per act — title, the situation, what Quick produces, why it matters. Clickable tabs or filters to move between acts; hover effects; smooth scroll.
- **A Knowledge Graph section** summarizing the connected context.
- **Design:** modern, responsive, all CSS/JS inline (no external dependencies).

### Step 6: Validate Outputs
- **Mode**: `deterministic`
- **Tool**: `folder_list` on `OUTPUT_PATH`; confirm both `<DEMO_NAME>-use-case-deck.pptx` and `<DEMO_NAME>-use-case-deck.html` are listed and non-empty. Retry any missing one.

> **Visual QA not available.** Page rasterization for visual QA is unavailable in the sub-task environment (no LibreOffice for PPTX→image). Do NOT block completion on it — validate via the layout code (positions, overlap, footer clearance) and that the file exists and is non-empty.

### Step 7: Complete
- **Mode**: `deterministic`
- **Tool**: `complete`
```
Use-case story deck and interactive HTML generated successfully.
- PPTX: <DEMO_NAME>-use-case-deck.pptx
- HTML: <DEMO_NAME>-use-case-deck.html
- Location: <OUTPUT_PATH>
- Slides: <actual slide count> (Title, Setup, one per act, Knowledge Graph, Value)
```
> Report the ACTUAL slide count — it depends on how many acts the script has. Do not hardcode a number.
