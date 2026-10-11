---
description: "Detailed create-mode instructions for quick-showcase-studio: intake (customer or industry target, industry resolution) and produce (author the scenario data, demo script, and use-case deck, then save to the shared Quick-Showcases space). Loaded on demand by SKILL.md."
last_updated: 2026-10-02
origin: original
---

# Create a Showcase — detailed instructions

This reference is loaded by `SKILL.md` when the user wants to **create** a showcase. It has two sections: **Intake** (Step 2 of the orchestrator) and **Produce** (Step 5). The orchestrator handles the destructive warning, cleanup, import, and summary around these.

## ⚠️ Critical rules for authoring

- **Use fictitious names for everything except the customer.** The customer name the user provides MAY appear in the content. Every other person, organization, product, and project MUST be fictitious: no real people, companies, competitors, vendors, products or software tools, regulators, accreditation or standards bodies, government agencies, or internal projects. This covers every text field, not only entity names: Knowledge Graph summaries and edge reasons, feed titles, summaries, and messages, CTA prompts, memories, the demo script, and the deck. A qualifier such as "-style" or "like" does not make a real name acceptable. Generic practices and topics (an accreditation survey, a security audit, a design tool) may be described without naming a real organization or product. Before saving, re-read every free-text field for real names; a check on entity names alone misses them. Invent realistic names.
- **Never hardcode dates.** The showcase is saved and reused, so any absolute date makes it stale. Do NOT write calendar dates, years, quarters, or month names (no "2026", "Q3", "March", "end of year"). Use **relative** references only ("2 weeks from now", "next quarter"). The schema stores all times as relative offsets from load time — respect that in every entity, feed item, and memory.
- **Author against the schema.** Everything you write into the showcase's scenario JSON must conform to `<skill_directory>/references/demo_scenario_data_schema.json`. Read it before authoring.
- **Namespace every file with the showcase's folder name.** All showcases live in **one shared space** (`Quick-Showcases`), one **folder** per showcase. Because Amazon Quick reads files by space + filename (ignoring the folder), every file MUST carry the folder name so a read can't cross showcases. Author all files as `<DEMO_NAME>-<canonical-name>` from the start (see the `DEMO_NAME` preamble in Produce).

## Default flow (used unless the user gives custom notes)

A 4-act narrative arc:
1. **Email Response (cross-channel synthesis)** — an urgent email needing context scattered across other emails AND team-chat messages; Quick drafts a comprehensive response.
2. **Meeting Prep Notes** — an upcoming meeting (next 1–2 days) for which Quick generates prep notes from related emails, chats, docs, and KG context.
3. **Team-Chat Query Response** — a chat message (e.g. Slack-style) Quick helps draft a response to, using project context.
4. **Knowledge Graph Showcase** — demonstrate KG features: Focus, Summarize, Ask.

If the user provides custom notes (a department/role, specific scenarios, or an alternative flow), adapt all content to their input instead.

---

## Intake

1. **Target type** — ask, unless already clear:
   ```
   <decision question="Would you like this showcase built for a specific customer or for an industry?">
     <option description="Tailor it for a specific company">Customer</option>
     <option description="Tailor it for an industry vertical">Industry</option>
   </decision>
   ```
2. **Target name** — Customer → "What is the customer name?"; Industry → "What industry?"
3. **Resolve industry** — if Customer: **web-search** the customer's industry, then confirm ("Based on my research, [Customer] appears to be in **[Industry]**. Is that correct?"); if the search is inconclusive, ask the user to provide it. If Industry: proceed.
4. **Optional use-case notes** — "Any specific use-case notes (a department/role, particular scenarios), or should I use the default flow (Email → Meeting Prep → Team-Chat → KG Showcase)?" Incorporate notes if given.

> **⚠️ Web-research residue (real customers).** The industry lookup above — and any further research while authoring — runs **web searches**, and Quick builds Knowledge Graph entities from that activity. For a well-known customer this seeds **real-world** nodes (e.g. researching a real company creates an **Amazon**/**AWS** entity) that can persist past the orchestrator's cleanup. If they survive to import, `kg_add` may **merge** your authored entity into the real one (the demo's **Amazon.com** absorbed into a web-seeded **Amazon**), misattributing edges. You don't need to hand-fix this: the orchestrator **re-clears the KG immediately before import** for create mode (SKILL.md Step 6.1). Keep authored entity names natural regardless — the pre-import re-clear is what guarantees a clean graph.

Return to the orchestrator for the destructive warning and cleanup, then continue with **Produce**.

---

## Produce

> **Derive `DEMO_NAME` first (used everywhere below).** From the confirmed target, build `DEMO_NAME` = `<customer>-<industry>` (or just `<industry>` for an industry target), **lowercase, hyphen-separated, only letters/digits/hyphens** (e.g. "Acme Corp" + "Manufacturing" → `acme-corp-manufacturing`). This single token is the showcase's **folder name** AND the **prefix on every file**. Author these five files in `workspace/attached_files/`:
> - `<DEMO_NAME>-demo-scenario-data.json`
> - `<DEMO_NAME>-demo-script.md`
> - `<DEMO_NAME>-demo-script.docx`
> - `<DEMO_NAME>-use-case-deck.pptx`
> - `<DEMO_NAME>-use-case-deck.html`

> **Note — leftover files from a previous showcase (do NOT back them up).** If a showcase was created earlier in this session, its files may still be in `workspace/attached_files/`. **You do not need to preserve or back them up** — every created showcase is already saved to a folder in the shared `Quick-Showcases` space and can be reloaded anytime. Do not copy `attached_files/` or run a write step to snapshot it (that just triggers permission prompts and errors). Because files are prefixed with their own `DEMO_NAME`, a previous showcase's files (`<other-name>-*`) don't collide with this one's and are simply ignored — they aren't uploaded to this showcase's folder. Optionally delete them to keep the workspace tidy, but it's not required.

### 1. Load KG + Activity Feed skills and review the schema
Call `load_skill("knowledge_graph")` and `load_skill("activity_feed")`. Read `<skill_directory>/references/demo_scenario_data_schema.json` — every entity, feed item, day-plan, and memory must conform to it.

### 2. Build the storyline (held in context)
- **Central character** — a fictitious demo persona (realistic name, title, department, company) the user "plays." NOT the user.
- **Supporting cast** — 4–8 fictitious characters (managers, teammates, cross-functional partners, external contacts), each with a name, title, and relationship.
- **Scenario narratives** — for each act: the trigger, what data exists across channels (emails, chats, meetings), the exact demo prompt, and what the expected output should cover.
- **Activity Feed plan** — which feed items appear, in what order.

### 3. Author the scenario data (`workspace/attached_files/<DEMO_NAME>-demo-scenario-data.json`)
Use native file tools; conform to the schema; relative dates only.

**a. Knowledge Graph** — entities + edges for ALL storyline characters/projects:
- People (central + cast), Projects/initiatives, Organizations (teams, depts, companies), Events (meetings, deadlines, milestones).
- Relationships: `reportsTo`, `worksOn`, `collaboratesWith`, `attendsMeeting`, `manages`, `memberOf`, `authored`, `madeDecision`, `dependsOn`, `relatedTo`, etc.

**b. KG enrichment (neural-network effect)** — add **15–30 more entities** and **25+ more edges** so the graph looks like a realistic network: industry context (trends, fictitious regulators and accreditation bodies, fictitious competitors, generic standards), org depth (depts, platforms), peripheral people (HR/IT, mentors, fictitious vendors), adjacent projects, external ecosystem (fictitious partners, analysts, events, and products or tools), historical context. Every name added here follows the fictitious-names rule above. Connect new entities to existing ones AND each other; create multiple clusters bridged by shared nodes. **Target topology: 25–45+ entities, 55–80+ edges total.**

**c. Activity Feed** — **11–13 items** that look like a realistic active inbox. Creation order matters (feed shows most recent on top):
1. First, **7–9 background filler items** (team-chat updates, admin emails; mostly `fyi`, occasional `important`; each with a CTA referencing real storyline characters/projects).
2. Then a **day-plan** item (`event_type: "day_plan"`): the Act-2 meeting + 2–3 other fictional meetings + at least one recommendation (prep/focus/conflict).
3. Last, the **top 3 demo-critical items** for Acts 1–3 (they appear on top; carry the CTAs the presenter clicks).
Every fact in an expected demo response must trace to a feed item or KG entity.

**CTA safety — draft only.** A card's CTA `prompt` is submitted to the agent verbatim when the presenter clicks it, so it must never ask the agent to take a real external action. Phrase every CTA as **draft/plan/prepare**, not **send/share/schedule/grant/deliver/move/reschedule/remind/assign/publish**, and do not **capture, add, log, record, file, or enter** an item into a review, agenda, tracker, task list, or other system of record. Producing a reviewable artifact for the presenter is fine ("Draft a reply", "Prepare notes", "Create a proposal", "Build a tracker document"); recording or entering something into a standing list, agenda, or review as a real item is not, because in a later conversation it can turn into a to-do or an edit. Good: "Draft a reply to ...", "Prepare the newsletter ready to send (draft only)", "Draft a plan to onboard ...", "Draft a message asking <person> to ...", "a tracker I could share later". Avoid: "... then send it", "Share X with <person>", "give <person> access", "Set up a weekly digest and deliver it by email", "Move/reschedule the meeting and reply", "Remind me next week" (creates a schedule), "Ask/Have <person> do X" (messages a real person), "share it as a link" (publishes), "Capture X as a discussion item for the service-line review" / "Log/record X" / "Add X to the tracker" (records into a system of record). A CTA is clicked later, often in a new conversation where this skill (and its safety rules) are not loaded, so the card's wording is the only safeguard. This keeps a showcase safe even if the demo account has live mail/messaging connectors.

**d. Demo-context memory** — append this exact memory (per schema) and update `_meta` counts/`kg_stats` to reflect the authored data. It is a plain descriptive fact (not a standing instruction): keep `is_global_behavior: false` and do not phrase it as a command.
```json
{
  "id": "ee23c7839ce9",
  "type": "fact",
  "category": "preference",
  "provenance": "explicit",
  "confidence": 0.77,
  "created_offset_days": -73.8,
  "tags": ["demo", "knowledge-graph", "activity-feed"],
  "text": "This is a demo workspace. The people, companies, and the mail and messaging entries in the knowledge graph and activity feed are fictitious sample data and do not correspond to any real mail or messaging account.",
  "is_global_behavior": false
}
```

### 4. Write the demo script (`workspace/attached_files/<DEMO_NAME>-demo-script.md`)
Cover Acts 1–4. For each act: **What the audience sees** (the feed state, in the exact order it appears; the trigger item), **Demo Action** (the exact natural prompt; what Quick pulls from), **Expected Result** (the FULL expected response, written out completely), **Talking Points**. Rules: feed order matches the feed; every prompt yields a complete result (if you can't write it fully, add more KG/feed data); no placeholders; every fact traces to a KG entity or feed item.

### 5. Dispatch the use-case deck task (background sub-task)
The deck + interactive HTML summarize **this showcase's story** (persona, the acts, the business value) — so dispatch it now, after the script exists, and let it build in the background while you finish the DOCX and save.
- **Tool:** `start_task` with `mode="continue_then_receive"` and `share_workspace=true` (so it reads the script and writes into the same `workspace/attached_files`).
- **Objective:**
  ```
  Load the presentation-generation reference from <skill_directory>/references/presentation-generation.md and follow it to generate the use-case story deck (PPTX + interactive HTML) for this showcase.

  PARAMETERS:
  - Demo name (filename prefix): <DEMO_NAME>
  - Customer: [customer name or "N/A"]
  - Industry: [industry name]
  - Source script: workspace/attached_files/<DEMO_NAME>-demo-script.md
  - Source data:   workspace/attached_files/<DEMO_NAME>-demo-scenario-data.json
  - Output path: workspace/attached_files
  - Output filenames: <DEMO_NAME>-use-case-deck.pptx and <DEMO_NAME>-use-case-deck.html (EXACTLY these names)

  Build the deck FROM the source script/data (the showcase's own story) — do NOT catalog Amazon Quick's product features. Follow the reference's workflow completely.
  ```
- **Store the returned thread_id as `DECK_TASK_ID`.** Do NOT wait here — continue. If `start_task` is unavailable, skip the dispatch (leave `DECK_TASK_ID` unset) — Step 7 will generate the deck inline instead.

### 6. Convert the script to DOCX (`workspace/attached_files/<DEMO_NAME>-demo-script.docx`)
1. `file_copy(source="<skill_directory>/scripts/md_to_docx_converter.js", destination="tmp/md_to_docx_converter.js")`
2. Set `session.MD_INPUT_PATH` to the `<DEMO_NAME>-demo-script.md` path and `session.DOCX_OUTPUT_PATH` to `attached_files/<DEMO_NAME>-demo-script.docx`, then run the converter with `run_javascript` (NOT `run_python`, to avoid write-permission prompts). Confirm the `.docx` exists.

### 7. Wait for the deck task (blocking gate)
The deck should finish before you save.
- **If `DECK_TASK_ID` is unset** (dispatch was skipped because `start_task` was unavailable) → generate the deck inline in this thread now, per `references/presentation-generation.md` (`load_skill("canvas_pptx")` + `load_skill("html_design")`, build from the script/data).
- Otherwise call `get_task_result(DECK_TASK_ID)`:
  - `completed` → gate passes.
  - `running` / `in_progress` → wait ~20s and poll again; up to **15 polls (~5 min)**.
  - `failed`, or still not done after 15 polls → **fall back to inline generation** per `references/presentation-generation.md`.
- **Verify** `<DEMO_NAME>-use-case-deck.pptx` and `<DEMO_NAME>-use-case-deck.html` exist in `workspace/attached_files/`. If one is still missing after the inline attempt (e.g. `canvas_pptx`/`html_design` isn't available in this account), **do not block** — continue to save, omit the missing file from the upload, and note it in the Step 7 summary.

### 8. Save the showcase to the shared `Quick-Showcases` space
- **Tools:** `search_spaces`, `create_space`, `list_folder_members`, `create_folder`, `write_quick_suite_file`.
- **Ensure the space exists.** `search_spaces` for **`Quick-Showcases`**. If it doesn't exist, `create_space` it. (One shared space holds every showcase, one folder each.)
- **Create the showcase's folder.** In `Quick-Showcases`, `list_folder_members(memberType="FOLDER")` and check for a folder named `DEMO_NAME`.
  - If absent, `create_folder` it.
  - If it already exists, tell the user and ask for a distinguishing suffix; set `DEMO_NAME` = `<DEMO_NAME>-<suffix>`, re-check until free, then create it. **If you added a suffix, first rename the local files** in `attached_files/` so their prefix matches the final `DEMO_NAME` (every uploaded file must contain the folder name, or the load-time name-check will reject the showcase).
- **Upload** the showcase's files into that folder with `write_quick_suite_file` (only files that were produced): `<DEMO_NAME>-demo-scenario-data.json`, `<DEMO_NAME>-demo-script.md`, `<DEMO_NAME>-demo-script.docx`, `<DEMO_NAME>-use-case-deck.pptx`, `<DEMO_NAME>-use-case-deck.html`.
  - **Path note:** `write_quick_suite_file` needs the **backend-native** `local_file_path` = `f"{WORKSPACE_DIR}/attached_files/<file>"`. Do NOT pass the `/outposts/backend/...` VFS form the filesystem tools return — it fails with "File not found".
- Confirm all produced files uploaded.
- **If a Spaces tool call fails, do NOT assume the connector is disabled.** Report the **actual error message verbatim** to the user, keep the local files in `attached_files/`, and offer to retry the save. The Spaces connector may well be enabled while the call fails for another reason (a wrong `local_file_path` form, a permission prompt that was denied, or a transient error). Only suggest enabling the Spaces connector if the error text specifically says the connector or tool is unavailable — otherwise surface the real error so it can be diagnosed.

After Produce, return to the orchestrator: it imports `<DEMO_NAME>-demo-scenario-data.json` for preview (Step 6) and shows the summary (Step 7). The showcase is usable in the account even if the save step failed; the save can be retried once the underlying error is resolved.

## Failure handling (create)

| If | Then |
| --- | --- |
| Web search can't resolve the customer's industry | Ask the user to provide it, then continue. |
| The deck task fails or times out | Fall back to inline generation (Step 7). If the deck still can't be produced, save the rest, tell the user the deck is missing, and offer to regenerate it. |
| Folder `DEMO_NAME` already exists in `Quick-Showcases` | Ask for a distinguishing suffix; update `DEMO_NAME`, rename the local files to match, re-check until free. |
| A file fails to upload | Report which one; local copies remain in `attached_files/` for a retry. |
| Can't write a complete expected response | Insufficient data — add KG/feed entries until every expected result is supported. |
