---
description: "Human-facing overview, pre-requisites, installation, and getting started for the Amazon Quick Showcase Studio skill."
last_updated: 2026-10-02
origin: original
---

# Amazon Quick Showcase Studio

Loads or creates Amazon Quick showcases: sample workspaces with a populated Knowledge Graph, an Activity Feed with a day plan, memories, and a presenter demo script.

## Overview

A showcase is a ready-to-present demo environment built from one scenario JSON file. Load mode imports the bundled built-in example (a marketing consultancy scenario, no setup) or a showcase you saved earlier. Create mode authors a new showcase for a named customer or an industry, builds a use-case story deck (PPTX and HTML) from it, saves it as its own folder in a Quick-Showcases space in your account, and imports it for preview. It is for anyone who presents Amazon Quick and wants a believable, repeatable demo without seeding data by hand.

> [!CAUTION]
> ## 🛑 STOP — READ THIS FIRST
>
> **DESTRUCTIVE: this clears the account before importing.** Loading or creating a showcase will:
>
> - 🗑️ **Delete** this account's Knowledge Graph and memories
> - 🧹 **Clear** its Activity Feed
> - 💬 **Delete** your other conversations
>
> **Treat everything cleared as permanent.**
>
> ### ⚠️ Do this before you run anything
>
> - ✅ **Use a dedicated Amazon Quick demo environment** — a separate "demo" Amazon Quick user, or a throwaway Amazon Quick account, set up only for demos. Never run this in your personal or work Amazon Quick account.
> - ⛔ **Do NOT connect your real connectors or knowledge bases** (mail, chat, drives, data sources, etc.) to that demo environment. The showcase content is fictitious and the cleanup is indiscriminate, so anything connected is at risk.
>
> Nothing is removed until you select **Proceed** and then type **`DELETE`** at the confirmation prompt.

## Pre-requisites

- Amazon Quick desktop app.

Works on any plan, including Free, and uses only Quick's built-in capabilities (Spaces, Presentations, and HTML Design), which are Amazon-owned system skills that are on by default. There is nothing extra to install or connect, and the built-in example needs no Space at all. (If Spaces has been turned off, re-enable it under Customize > Skills.)

**About the Quick-Showcases space.** Create mode saves every showcase there, including the customer name you provide. If the space does not exist yet, the skill creates it with you as its owner, so it is visible only to you until you share it. If a space with that name already exists, the skill reuses it with its current sharing, so check who has access before creating a showcase for a real customer.

## Installation

1. Open the Amazon Quick desktop app and go to **Customize > Skills**.
2. Click **Create > From folder**, select the `quick-showcase-studio` folder, and confirm. Keep the subfolders next to `SKILL.md`; they are all used at runtime.
3. Turn the skill's toggle **On**.

That is it. The Spaces, Presentations, and HTML Design capabilities are built in and on by default, so there is no connector to set up.

More help: Adding and managing skills (https://docs.aws.amazon.com/quick/latest/userguide/skills-desktop.html)

## Getting started

- "load the example demo": loads the built-in marketing consultancy showcase (40 Knowledge Graph entities, 18 feed items) and links its demo script and use-case story page.
- "create a showcase for Acme Corp" or "build a demo for the healthcare industry": authors a new showcase, saves it to the Quick-Showcases space (for example, as `acme-corp-manufacturing`), and imports it for preview.
- "load the acme-corp-manufacturing showcase": reloads a showcase you created. With no name, a picker lists the built-in example and every saved showcase.
- "quick showcase studio": asks whether to create or load.

Memories are queued for background processing, so they can appear a little after the Knowledge Graph and feed. That is expected.

## Good to know

The skill only drafts and prepares demo material; it never sends messages, shares items, or creates schedules. For extra safety, run it in an account with no live mail or messaging connectors, since the showcase's mail and messages are fictitious. Imported content always uses times relative to when you load it, so a saved showcase stays current whenever you reload it.

## Directory structure

```
quick-showcase-studio/
  SKILL.md
  README.md
  references/
    create-showcase.md
    load-showcase.md
    presentation-generation.md
    demo_scenario_data_schema.json
  assets/
    marketing-consultancy/
      demo-scenario-data.json
      demo-script.md
      use-case-deck.html
  scripts/
    cleanup_all.py
    import_demo_data.py
    download_demo_folder.py
    md_to_docx_converter.js
```
