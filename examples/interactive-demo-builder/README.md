# Build Custom Demos for Amazon Quick

> [!CAUTION]
> **This skill is destructive by design. Loading or creating a showcase wipes the signed-in user's data before importing: it deletes the user's Knowledge Graph and memories, clears its Activity Feed, and deletes your other conversations. Run it only while signed in as a dedicated demo user, never in your work or personal account.**
>
> - Treat everything cleared as permanent. Amazon Quick documents no restore for the Knowledge Graph or memories.
> - The first time it runs, it shows how many Knowledge Graph entities, memories, feed items, and other conversations it will delete and asks you to confirm. Every run, nothing is removed until you select **Proceed** and then type `DELETE`.
> - Your data belongs to your Quick **user**, not to the device. Another laptop, VM, or EC2 instance signed in as you is the same user.
> - Do **not** connect your real connectors or knowledge bases (mail, chat, drives, data sources) to the demo user.
>
> Read [Before you install or run anything](#before-you-install-or-run-anything) first.

A desktop skill that loads or creates ready-to-present Amazon Quick showcases: sample workspaces with a populated Knowledge Graph, an Activity Feed, memories, and a presenter demo script, with no data seeded by hand.

## Overview

**Quick Showcase Studio** is an [Amazon Quick desktop](https://docs.aws.amazon.com/quick/latest/userguide/) skill for anyone who presents Amazon Quick and wants a believable, repeatable demo environment.

A showcase is a demo workspace built from a single scenario file. The skill works in two modes:

- **Load** — import the bundled built-in example (a marketing-consultancy scenario, no setup) or a showcase you saved earlier.
- **Create** — author a new showcase for a named customer or an industry, build a use-case story deck (PPTX and HTML) from it, save it to a Quick-Showcases space for reuse, and import it for preview.

It uses only Quick's built-in capabilities (Spaces, Presentations, and HTML Design), so there is nothing extra to connect, and the built-in example needs no Space at all.

## Before you install or run anything

> [!WARNING]
> Loading or creating a showcase **clears the signed-in user's data before importing**. It will:
>
> - **Delete** the user's Knowledge Graph and memories
> - **Clear** its Activity Feed
> - **Delete** your other conversations
>
> Treat everything cleared as **permanent**.
>
> **Set this up first:**
>
> - **Use a dedicated Amazon Quick demo environment** — a separate "demo" Amazon Quick user, or a throwaway Amazon Quick account, set up only for demos. Never run this in your personal or work Amazon Quick account.
> - **Do NOT connect your real connectors or knowledge bases** (mail, chat, drives, data sources, etc.) to that demo environment. The showcase content is fictitious and the cleanup is indiscriminate, so anything connected is at risk.
>
> Nothing is removed until you select **Proceed** and then type `DELETE` at the skill's confirmation prompt.

## Pre-requisites

- The Amazon Quick desktop app (works on any plan, including Free).
- The built-in Spaces, Presentations, and HTML Design capabilities, which are Amazon-owned system skills on by default. If Spaces has been turned off, re-enable it under **Customize > Skills**.

Create mode saves every showcase to a Quick-Showcases space, including the customer name you provide. If the space does not exist, the skill creates it with you as owner (visible only to you until shared). If it already exists, the skill reuses it with its current sharing, so check who has access before authoring a showcase for a real customer.

## Install

The skill lives in this repo at [`examples/interactive-demo-builder/skills/quick-showcase-studio`](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/tree/main/examples/interactive-demo-builder/skills/quick-showcase-studio).

1. Download or clone this repository.
2. Open the Amazon Quick desktop app and go to **Customize > Skills**.
3. Click **Create > From folder** and select the `quick-showcase-studio` folder (keep the subfolders next to `SKILL.md`; they are all used at runtime).
4. Turn the skill's toggle **On**.

More help: [Adding and managing skills](https://docs.aws.amazon.com/quick/latest/userguide/skills-desktop.html).

## Getting started

With the skill installed and on, try:

- **"load the example demo"** — loads the built-in marketing-consultancy showcase (populated Knowledge Graph and feed) and links its demo script and use-case story page.
- **"create a showcase for Acme Corp"** or **"build a demo for the healthcare industry"** — authors a new showcase, saves it to the Quick-Showcases space, and imports it for preview.
- **"load the acme-corp-manufacturing showcase"** — reloads a showcase you created. With no name, a picker lists the built-in example and every saved showcase.
- **"quick showcase studio"** — asks whether to create or load.

Memories are queued for background processing, so they can appear a little after the Knowledge Graph and feed. That is expected.

## How it is organized

The skill is a self-contained folder you install as-is:

- `SKILL.md` — the orchestrator the skill runtime reads.
- `README.md` — the skill's own pre-requisites and usage (read by the skill at runtime to check pre-requisites).
- `references/` — mode playbooks (create, load, presentation generation) and the scenario-data schema.
- `assets/marketing-consultancy/` — the bundled built-in example (scenario data, demo script, use-case deck).
- `scripts/` — the cleanup and import helpers.
- `evals/` — the evaluation spec used to test the skill.

Browse the source: [`skills/quick-showcase-studio/`](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/tree/main/examples/interactive-demo-builder/skills/quick-showcase-studio).

## Good to know

The skill only drafts and prepares demo material; it never sends messages, shares items, or creates schedules. Imported content always uses times relative to when you load it, so a saved showcase stays current whenever you reload it.
