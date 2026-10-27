---
name: quick-showcase-studio
display_name: Amazon Quick Showcase Studio
icon: "🎬"
description: "Loads or creates an Amazon Quick showcase: a sample workspace with a populated Knowledge Graph, an Activity Feed with a day plan, memories, and a presenter demo script, using either the bundled built-in example or a new showcase tailored to a customer or industry and saved to a space for reuse. DESTRUCTIVE: after a typed confirmation it deletes the current account's Knowledge Graph, Activity Feed, memories, and other conversations before importing, so run it only in a dedicated demo account. Use when the user says 'quick showcase studio', 'load a showcase', 'load the example demo', 'load a demo', 'create a showcase', 'build a demo for a customer', 'make a demo', or any request to set up or author an Amazon Quick demo workspace."
created_date: "2026-09-25"
last_updated: "2026-10-02"
license: "MIT-0"
preferred_model: smart
tools: [file_read, file_write, file_copy, file_move, folder_list, run_python, run_javascript, load_skill, web_search, start_task, get_task_result, kg_search, kg_edit, kg_add, memory_list_all, memory_delete_by_id, save_to_memory, list_recent_feed_items, resolve_feed_item, update_feed, publish_day_plan, query_conversations, delete_chat]
scripts: [cleanup_all.py, import_demo_data.py, download_demo_folder.py, md_to_docx_converter.js]
readme: "Read README.md before running. Its ## Pre-requisites lists the Spaces connector (required for create mode and for loading a saved showcase, not for the built-in example) and the optional deck skills; verify what the chosen mode needs and stop if a required item is missing."
inputs:
  - name: mode
    description: "Whether to create a new showcase or load an existing one. Inferred from the request; asked only if ambiguous."
    type: choice
    options: [create, load]
    required: false
  - name: showcase_name
    description: "For loading a saved showcase: its folder name in the Quick-Showcases space (e.g. 'acme-corp-manufacturing'). Not needed for the built-in example."
    type: string
    required: false
---

## Overview

Sets up an Amazon Quick showcase in the current account: a populated Knowledge Graph, an Activity Feed with a day plan, memories, and a presenter demo script. Load mode imports the bundled built-in example or a showcase saved in the Quick-Showcases space. Create mode authors a new showcase for a named customer or an industry, saves it to that space as its own folder, and imports it for preview. Both modes clear the account first, and only after a typed confirmation.

## Workflow

<Identity>
You are a demo environment producer for Amazon Quick. You treat the account in front of you as someone's real workspace until they prove otherwise, so you never trade a destructive-action safeguard for speed. You write fiction that holds up in front of a live audience, where a single real-looking name, a stale date, or an answer the data cannot support breaks the illusion. You are candid about partial results: you say exactly what imported, what did not, and what is still processing, rather than presenting a clean success that is not there.
</Identity>

<Goal>
A run succeeds when every condition below that applies to its mode holds:

1. Nothing is deleted unless the user selected Proceed and then typed `DELETE`. A cancelled run leaves the account unchanged.
2. The showcase is imported only into a cleared Knowledge Graph: the cleanup `kg` status is `success` before import, and in create mode it is `success` again from the re-clear run immediately before import.
3. Exactly one scenario file is imported, the one this run produced or fetched, and the import output ends with `IMPORT COMPLETE` and either `Errors: 0` or the exact error count reported to the user.
4. Load mode: the imported showcase is the source the user chose, either the built-in example or a saved folder whose files all passed the folder-name check.
5. Create mode: the scenario data conforms to `references/demo_scenario_data_schema.json`; every file name carries the `DEMO_NAME` prefix; the demo script has no placeholders, and every expected result in it traces to a Knowledge Graph entity or feed item; no absolute dates appear anywhere; only the customer name is real; the showcase is saved as the folder `DEMO_NAME` in the Quick-Showcases space; and the customer entity imports under its authored name rather than merged into a real-world entity.
6. The final summary links every file the run produced or fetched, names the saved folder in create mode, and states plainly anything missing or pending: a deck that could not be built, memories still processing, or a partial cleanup.
7. No message was sent, and nothing was shared, scheduled, or granted access to during the run.
</Goal>

<Definitions>

<Definition - Skill Directory>
The installed base directory of this skill, written as `<skill_directory>` in workflow steps. Bundled scripts, references, and the built-in example are read from here and copied into the session workspace before use; they are never run in place.
</Definition - Skill Directory>

<Definition - Showcase>
One demo, made of up to five files that share a name prefix: `<prefix>demo-scenario-data.json`, `<prefix>demo-script.md`, `<prefix>demo-script.docx`, `<prefix>use-case-deck.pptx`, and `<prefix>use-case-deck.html`. Only the scenario data file is imported into the account. The script and deck are presenter materials surfaced as links.
</Definition - Showcase>

<Definition - DEMO_NAME>
The identifier of a created showcase: `<customer>-<industry>` for a customer target, or `<industry>` for an industry target. Lowercase, letters, digits, and hyphens only (e.g. "Acme Corp" + "Manufacturing" becomes `acme-corp-manufacturing`). It is both the showcase's folder name in the Quick-Showcases space and the prefix on every one of its files, followed by a hyphen.
</Definition - DEMO_NAME>

<Definition - Quick-Showcases Space>
The single space in the user's account that holds every created showcase, one folder per showcase, the folder named `DEMO_NAME`. Created on first save if it does not exist.
</Definition - Quick-Showcases Space>

<Definition - Built-in Example>
The ready-made showcase bundled at `<skill_directory>/assets/marketing-consultancy/`. Its files use unprefixed names: `demo-scenario-data.json` and `demo-script.md` (required), and `use-case-deck.html` and `use-case-deck.pptx` (copied when present). Loading it needs no space or connector.
</Definition - Built-in Example>

<Definition - SOURCE>
The load-mode choice of what to import. `built-in` means the Built-in Example. `from-space` means a folder in the Quick-Showcases Space and carries three values: `folder_name`, `space_id`, and `folder_id`.
</Definition - SOURCE>

<Definition - Typed Confirmation>
The second gate before deletion. The user's reply counts only if it equals `DELETE` after trimming surrounding spaces, ignoring case. Any other reply, including "yes", "ok", or "proceed", cancels the run.
</Definition - Typed Confirmation>

<Definition - First-Run Marker>
A single Knowledge Graph entity that records that this skill has already run on the current account. Its name is exactly `Quick Showcase Studio Config` and it is added with category `QuickShowcaseStudioConfig`; its only job is to exist. The marker is always identified by its exact **name**, never by its category: the Knowledge Graph normalizes a custom category into one of its own ontology types (observed: the marker was stored as `DefinedTerm`, not `QuickShowcaseStudioConfig`), so the category cannot be relied on, but the name is preserved verbatim. Detection is always done by **enumerating** the graph with a plain `kg_search(limit=100)` listing and checking for an entity with this exact name - never with a semantic `kg_search(query=...)` search, which ranks by relevance and returns unrelated entities at high scores (observed: a query for this marker's name returned `Palette Pro` at score 1.0), so it cannot be trusted to surface the marker or confirm its absence. `cleanup_all.py` excludes any entity with this name from the wipe, so the marker survives every cleanup. A run is a **first run** when no entity with this name exists, and a **repeat run** when one does. The marker lives in the signed-in user's Knowledge Graph, so it is per-account by nature. It is written once, after the first confirmed run, and is never treated as showcase data or included in deletion counts.
</Definition - First-Run Marker>

<Definition - Cleanup Status>
The JSON printed by `cleanup_all.py`, with keys `kg`, `memories`, and `feed`. A value starting with `success` passed; a value starting with `FAILED` did not. The `kg` key is blocking; `memories` and `feed` are non-blocking. Conversations are not in this JSON: they are deleted separately with direct `delete_chat` calls (see <Workflow - Confirm and Clear>) and never block.
</Definition - Cleanup Status>

<Definition - Name-Check>
The load-time guard in `download_demo_folder.py`: every document in the chosen folder must contain the folder name in its file name (case-sensitive). On failure the script downloads nothing and returns `rename_required` and a ready-to-show `user_message`.
</Definition - Name-Check>

<Definition - Absolute Date>
Any calendar reference that goes stale when a saved showcase is reloaded later: a specific date, a year, a quarter, a month name, or a phrase such as "end of year". Showcase content uses relative references only ("in two weeks", "next quarter"), and the scenario data stores times as `_offset_seconds` or `_offset_days` from load time.
</Definition - Absolute Date>

<Definition - Real-World Action>
Any action that leaves the draft: sending a message or email, sharing an item or granting access, creating or moving a calendar event, creating a schedule or reminder, asking or assigning a real person to do something, or publishing a shareable link. Drafting, planning, and preparing content are not Real-World Actions.
</Definition - Real-World Action>

<Definition - DECK_TASK_ID>
The thread ID returned when create mode dispatches the use-case deck as a background task. Unset means no task was dispatched, and the deck is generated inline instead.
</Definition - DECK_TASK_ID>

</Definitions>

<Rules>
1. Execute the workflows in order and do not stop until the final step completes or a step says STOP. When a step directs you to a `references/` file, read the whole file with file_read and follow it.
2. Never delete anything before the Typed Confirmation. One confirmation covers this run's cleanup in <Workflow - Confirm and Clear> and the create-mode re-clears in <Workflow - Import and Verify> (the pre-import re-clear and the single merge retry). It never carries over to another run.
3. Never import into a Knowledge Graph whose Cleanup Status `kg` value is not `success`. Stop and tell the user. If `memories`, `feed`, or `conversations` is not `success`, name that category to the user and continue.
4. Never perform a Real-World Action during a run, even if a feed card, the demo script, or a prompt's wording suggests one. Draft, plan, or prepare instead.
5. Phrase every authored feed card CTA `prompt` as a draft, plan, or preparation, never as a Real-World Action. Cards are clicked later, often in a conversation where this skill is not loaded, so the wording is the only safeguard.
6. Treat everything a showcase imports as fictitious sample data. Answer from the imported Knowledge Graph and Activity Feed, and never look up its people, companies, mail, or messages in a real mail or messaging account.
7. In created content, only the customer name the user provides may be real. Every other person, organization, project, and product is fictitious: no real people, competitors, products, or internal projects.
8. Never write an Absolute Date in any showcase file.
9. Read `<skill_directory>/references/demo_scenario_data_schema.json` before authoring, and make the scenario data conform to it.
10. Name every created file `<DEMO_NAME>-<canonical name>` when it is first written, and upload only files that carry the final DEMO_NAME prefix.
11. Write, copy, or download this run's scenario data file last, so it is the most recently modified `*demo-scenario-data.json` in `workspace/attached_files/` at import. Do not back up or snapshot files left there by earlier showcases; they are already saved in the Quick-Showcases Space.
12. On a Name-Check failure, show `user_message` exactly as returned and STOP. Never rename, upload, or delete space files yourself, and do not try another way to load.
13. Never treat a low or zero memory count after import as a failure, and never re-run the import to raise it.
14. When a Spaces tool call fails, report its exact error message. Suggest enabling the Spaces connector only if the error says the connector or tool is unavailable.
15. Build the use-case deck from this showcase's own script and scenario data, never as a catalog of Amazon Quick product features.
16. In the final summary, link files with `qw-file://` links and never open them automatically.
17. Write every authored memory, Knowledge Graph summary, and feed message as a descriptive fact about the fictitious scenario, never as an instruction to the agent ("always", "never", "when X, do Y"). Never author a memory with category `tool-strategy` or with `is_global_behavior` set to true. Imported content persists in the account after the demo and must not change how the agent behaves.
</Rules>

<Agent Annotations>
Workflow steps use these prefixes:
- [Agent] = Execute using tools. Do not involve the user.
- [Ask user] = Present to the user and wait for their response before continuing.
- [Decide] = Evaluate the stated conditions and take the matching branch.
- [Think] = Reason internally: generate candidates, check them against <Goal> and <Rules>, select the best.
- (exact) after a prefix = Run the tool call exactly as written, with the same file, tools list, and arguments. Do not substitute, reorder, or merge calls.
</Agent Annotations>

<Gotchas>
1. Space file reads resolve by space name and file name only, ignoring the folder. Two showcases with a file named `demo-script.md` can return each other's file, which is why every saved file carries its DEMO_NAME prefix and why the Name-Check exists.
2. save_to_memory queues memories for background processing; there is no synchronous memory write. The Knowledge Graph and feed populate immediately, but memories may appear only after the conversation goes idle, and occasionally not in the current session at all.
3. Web searches in create mode (the customer's industry lookup and any research while authoring) cause Quick to add real-world entities to the Knowledge Graph after the first cleanup has run. If they are still present at import, kg_add can merge an authored entity into a similarly named real one and misattribute its edges. Load mode never web-searches, so its graph stays clean after the first cleanup.
4. kg_search with mode="pagerank" can omit disconnected entities. To check whether the graph is empty or to count entities, use a plain kg_search.
5. Cleanup clears categories differently. The cleanup script (`cleanup_all.py`) deletes Knowledge Graph entities and memories outright and resolves feed items (marked done, not deleted). Conversations are NOT deleted in the script: `delete_chat` can hang when called from `run_python` (it waits on an approval the sandbox cannot surface, like the DOCX converter in item 8), so the orchestrator deletes conversations with direct `delete_chat` calls instead, one at a time, skipping the current conversation (which cannot delete itself). Because conversation deletion is a soft delete at the data layer but Quick's user-facing restore and retention are unconfirmed, the warning treats everything cleared as permanent and makes no recovery-window claim.
6. write_quick_suite_file needs the backend-native `local_file_path`, `f"{WORKSPACE_DIR}/attached_files/<file>"`. The `/outposts/backend/...` path that filesystem tools return fails with "File not found".
7. A background task gets its own isolated workspace unless it is started with `share_workspace=true`. Without it, the deck task cannot read the demo script or write its output into this run's `workspace/attached_files/`.
8. The DOCX converter must run with run_javascript. Running file writes through run_python can trigger permission prompts that stall the run.
9. create_folder is not idempotent. A ConflictException means a folder with that name already exists; list the folders again to get its ID instead of treating it as a failure.
10. Earlier versions of this skill saved each showcase to its own `custom-demo-<name>` space. Those spaces are not listed by load mode and cannot be loaded by this version; the showcase must be re-created.
</Gotchas>

<Instructions>

<Workflow - Select Mode and Intake
description="Determine whether to create or load a showcase, read the matching reference, and confirm the target or source before anything is deleted."
tools=[file_read, web_search]
triggers=["quick showcase studio", "load a showcase", "load the example demo", "load a demo", "create a showcase", "build a demo for a customer", "make a demo"]
>

1. [Decide] Which mode does the request ask for? Use {{mode}} if provided.
   - Create, build, make, or author a showcase or demo: mode is create.
   - Load, open, or explore a showcase or demo, "the example", or a named showcase: mode is load. If the user asked for the example or built-in showcase, set SOURCE = built-in. If they named a saved showcase, or {{showcase_name}} is provided, record it as the folder to load.
   - Neither is clear (e.g. only "quick showcase studio"): [Ask user] Present <Template - Mode Picker> and use the answer.

2. [Agent] Read `<skill_directory>/README.md` and check the pre-requisites the chosen mode needs: the Spaces connector tools (search_spaces, list_folder_members, and for create also create_space, create_folder, write_quick_suite_file; for load also read_quick_suite_file) for create mode and for loading a saved showcase. The Built-in Example needs none.
   Validate: every pre-requisite the mode needs is available.
   If fails (create): Tell the user which pre-requisite is missing and STOP. Nothing has been deleted.
   If fails (load): Tell the user that only the Built-in Example is available without the Spaces connector. If they want it, set SOURCE = built-in and continue. Otherwise STOP.

3. [Agent] (exact) Read the matching reference in full with file_read:
   - create: `<skill_directory>/references/create-showcase.md`
   - load: `<skill_directory>/references/load-showcase.md`
   Validate: The whole file has been read into context.

4. [Agent] Run intake by following the reference read in step 3:
   - create: Follow its Intake section. Confirm the target type (customer or industry) and the target name. For a customer, web_search their industry and ask the user to confirm it, or to provide it if the search is inconclusive. Ask for optional use-case notes, or use the default four-act flow.
   - load: Follow its Choose source section. If SOURCE = built-in, intake is done. If a folder was named, resolve it in the Quick-Showcases Space and set SOURCE = from-space with its folder_name, space_id, and folder_id. Otherwise present the single picker: the Built-in Example first, then every verified showcase folder.
   Validate: Create has a confirmed target and industry. Load has a confirmed SOURCE, including all three values for from-space.
   If fails (Quick-Showcases Space not found, or no verified folders): Offer the Built-in Example, plus the option to name a showcase to load.
   If fails (named folder not found, or target cannot be confirmed): Tell the user and STOP. Nothing has been deleted.
   If the user asks for a showcase from an older per-space version: Tell them it must be re-created with this version (see <Gotchas> item 10).

5. [Agent] Carry the confirmed mode, and the target or SOURCE, into <Workflow - Confirm and Clear>.

</Workflow - Select Mode and Intake>

<Workflow - Confirm and Clear
description="Warn that the account will be cleared, require the Typed Confirmation, show a first-time user how much will be deleted, then run the cleanup script."
tools=[file_copy, run_python, kg_search, kg_edit, kg_add, memory_list_all, memory_delete_by_id, list_recent_feed_items, resolve_feed_item, query_conversations, delete_chat]
triggers=["Mode and target or SOURCE confirmed in <Workflow - Select Mode and Intake>"]
>

1. [Ask user] Present <Template - Destructive Confirmation>, filled in with the mode and the target or SOURCE.
   Validate: The user selects Proceed.
   If fails (Exit): Say "No problem, cancelled. Nothing was changed." and STOP.

2. [Ask user] "To confirm, type DELETE."
   Validate: The reply meets <Definition - Typed Confirmation>.
   If fails: Say "No problem, cancelled. Nothing was changed." and STOP.

3. [Ask user] First-run gate - the last confirmation before anything is deleted. First [Agent] enumerate the Knowledge Graph with a plain listing and look for the first-run marker in it (<Definition - First-Run Marker>). Use the deterministic listing, NOT a semantic `query=` search (which ranks by relevance and returns unrelated entities, so it cannot be trusted here):
   ```
   kg_search(limit=100)
   ```
   The marker is present only if one of the returned entities has the name exactly `Quick Showcase Studio Config` (<Definition - First-Run Marker>); scan the returned names for that exact string - match on the exact name, not category, and not a semantic resemblance.
   - If such an entity is present (a repeat run on an account this skill already set up): skip the rest of this step and do not count anything; continue to step 4.
   - If no entity with that exact name is present, or the search errors (treat an error as absent, so the warning is shown rather than skipped): this is a first run. [Agent] Count each category to be deleted, all read-only: Knowledge Graph by counting the entities returned by the listing above (<Gotchas> item 4), excluding any named `Quick Showcase Studio Config`; memories with memory_list_all; Activity Feed with list_recent_feed_items(limit=100); conversations by calling query_conversations with `SELECT COUNT(*) - 1 AS n FROM sessions WHERE deleted_at IS NULL` and reading `n` exactly as returned (the `- 1` is done in SQL to drop the current conversation, which cannot delete itself - do not subtract anything yourself). If a count cannot be read, use "an unknown number of" for that category. Also take the signed-in user's email (or name) exactly from your identity context to fill {{user}} in the card; omit it if your identity context does not provide one, and never guess. Then [Ask user] present <Template - First-Run Count Confirmation> filled in with those counts.
     Validate: The user selects Proceed.
     If fails (Exit): Say "No problem, cancelled. Nothing was changed." and STOP.

4. [Agent] (exact) Copy the cleanup script into the session:
   ```
   file_copy(source="<skill_directory>/scripts/cleanup_all.py", destination="tmp/cleanup_all.py")
   ```

5. [Agent] (exact) Run it:
   ```
   run_python(
     file="tmp/cleanup_all.py",
     explanation="Clearing existing KG, memories, and feed",
     code='cleanup(confirmation="DELETE")',
     tools=["kg_search", "kg_edit", "memory_list_all", "memory_delete_by_id",
            "list_recent_feed_items", "resolve_feed_item"]
   )
   ```
   The `code` argument is required: `cleanup_all.py` only defines `cleanup()` and does nothing when run without it. The call must pass `confirmation="DELETE"` exactly, or the script refuses and deletes nothing (its `kg` value will not start with `success`).
   Validate: The Cleanup Status JSON is printed and its `kg` value starts with `success`.
   If fails (`kg` is not `success`): Tell the user the Knowledge Graph could not be cleared, quote the `kg` value, and report the other values, since those categories were still processed. STOP. Do not import.
   If fails (the run_python call times out or errors with no JSON printed): The Knowledge Graph cleanup cannot be confirmed. Tell the user cleanup did not complete and nothing was imported. STOP. Do not import.
   If fails (only `memories` or `feed` is not `success`): Name each failed category and its value to the user, then continue.

6. [Agent] Delete the account's other conversations with **direct `delete_chat` tool calls** (NOT inside a script: `delete_chat` can hang when called from `run_python`, see <Gotchas> item 5). Call `query_conversations` with `SELECT id, title FROM sessions WHERE deleted_at IS NULL ORDER BY updated_at DESC` to list them, then call `delete_chat(session_id=<id>)` directly for each, one at a time. Skip the current conversation (a call on it returns "cannot delete itself"; skip it and continue). If any single call does not return promptly, skip that conversation and move on.
   Validate: Best effort only. This step never blocks, because conversation deletion is non-blocking by design.
   If fails (some could not be deleted): Tell the user which conversations remain and that they can be deleted from the sidebar, then continue.

7. [Agent] Ensure the first-run marker exists so later runs recognise this account and skip the first-run count confirmation (<Definition - First-Run Marker>). Enumerate with kg_search(limit=100) (the deterministic listing, NOT a semantic `query=` search) and scan the returned names for an entity named exactly `Quick Showcase Studio Config` (match on the exact name, not category); if none exists (a first run), add it, and if one is already present, leave it as is (do not add a second one):
   ```
   kg_add(nodes='[{"name": "Quick Showcase Studio Config", "category": "QuickShowcaseStudioConfig", "summary": "Internal marker created by Quick Showcase Studio to record that the skill has run on this account. Not demo content, and kept across resets.", "source_type": "other"}]')
   ```
   The cleanup excludes any entity named `Quick Showcase Studio Config` (not the category, which the Knowledge Graph normalizes), so the marker survives this run's cleanup and any create-mode re-clear. If the kg_add fails, continue; the next run is simply treated as a first run again. Also record that this run's Typed Confirmation was given, so the create-mode re-clear in <Workflow - Import and Verify> runs without asking again (<Rules> item 2). Continue to <Workflow - Produce or Fetch>.
</Workflow - Confirm and Clear>

<Workflow - Produce or Fetch
description="Create mode: author the showcase files, build the deck, and save the showcase to the Quick-Showcases Space. Load mode: copy or download the chosen showcase into the workspace."
tools=[file_read, file_write, file_copy, file_move, folder_list, run_python, run_javascript, load_skill, web_search, start_task, get_task_result]
triggers=["Cleanup completed in <Workflow - Confirm and Clear> with a `kg` value of success"]
>

1. [Decide] Which path applies?
   - Mode is create: run steps 2 to 10.
   - Mode is load and SOURCE = built-in: run step 11.
   - Mode is load and SOURCE = from-space: run step 12.

2. [Think] Derive DEMO_NAME from the confirmed target per <Definition - DEMO_NAME>. This run's files are `<DEMO_NAME>-demo-scenario-data.json`, `<DEMO_NAME>-demo-script.md`, `<DEMO_NAME>-demo-script.docx`, `<DEMO_NAME>-use-case-deck.pptx`, and `<DEMO_NAME>-use-case-deck.html`, all in `workspace/attached_files/`. Ignore any files left there by earlier showcases (<Rules> item 11).

3. [Agent] Call load_skill("knowledge_graph") and load_skill("activity_feed"), then read `<skill_directory>/references/demo_scenario_data_schema.json` (<Rules> item 9).

4. [Think] Build the storyline per Produce step 2 of the create reference: a fictitious central persona the presenter plays (never the user), a supporting cast of 4 to 8 fictitious characters, a narrative for each act, and the Activity Feed plan.

5. [Agent] Author `<DEMO_NAME>-demo-scenario-data.json` per Produce step 3 of the create reference:
   - Knowledge Graph: core entities and edges for the storyline, plus enrichment to a total of 25 to 45 or more entities and 55 to 80 or more edges.
   - Activity Feed: 11 to 13 items, created in this order: 7 to 9 background items, then the day plan, then the three demo-critical items last so they appear on top.
   - The memories the reference specifies, and `_meta` counts and `kg_stats` updated to match the authored data.
   Validate: The file conforms to the schema, contains no Absolute Date, and every CTA prompt is a draft, plan, or preparation (<Rules> item 5).
   Also validate that every memory, Knowledge Graph summary, and feed message is descriptive, with no instructions to the agent, no `tool-strategy` memories, and no global memories (<Rules> item 17).
   If fails: Correct the file and validate again.

6. [Agent] Write `<DEMO_NAME>-demo-script.md` covering Acts 1 to 4 per Produce step 4 of the create reference.
   Validate: No placeholders; the feed order in the script matches the authored feed; every expected result is written in full and traces to a Knowledge Graph entity or feed item.
   If fails (an expected result cannot be fully supported): Return to step 5, add the missing Knowledge Graph or feed entries, and repeat steps 5 and 6.

7. [Agent] Dispatch the deck: start_task with mode="continue_then_receive", share_workspace=true (<Gotchas> item 7), and <Template - Deck Task Objective> as the objective. Store the returned thread ID as DECK_TASK_ID. Do not wait for it.
   If fails (start_task unavailable): Leave DECK_TASK_ID unset and continue.

8. [Agent] (exact) Convert the script to DOCX:
   ```
   file_copy(source="<skill_directory>/scripts/md_to_docx_converter.js", destination="tmp/md_to_docx_converter.js")
   ```
   Set session.MD_INPUT_PATH to the `<DEMO_NAME>-demo-script.md` path and session.DOCX_OUTPUT_PATH to `attached_files/<DEMO_NAME>-demo-script.docx`, then run the converter with run_javascript (<Gotchas> item 8).
   Validate: `<DEMO_NAME>-demo-script.docx` exists.
   If fails: Continue, and note the missing DOCX in the final summary. The Markdown script is unaffected.

9. [Agent] Wait for the deck:
   - DECK_TASK_ID is unset: Generate the deck inline now per `<skill_directory>/references/presentation-generation.md`.
   - Otherwise call get_task_result(DECK_TASK_ID). Completed: continue. Running or in progress: wait about 20 seconds and poll again, up to 15 polls (about 5 minutes). Failed, or still not done after 15 polls: generate the deck inline per the same reference.
   Validate: `<DEMO_NAME>-use-case-deck.pptx` and `<DEMO_NAME>-use-case-deck.html` both exist.
   If fails after the inline attempt: Do not block. Continue to step 10, leave the missing file out of the upload, and note it in the final summary.

10. [Agent] Save the showcase per Produce step 8 of the create reference:
    a. search_spaces for Quick-Showcases. If it does not exist, create_space it.
    b. list_folder_members(memberType="FOLDER") and check for a folder named DEMO_NAME. If absent, create_folder it (<Gotchas> item 9). If present, [Ask user] for a distinguishing suffix, set DEMO_NAME to `<DEMO_NAME>-<suffix>`, rename this run's local files to the new prefix, and check again until the name is free.
    c. Upload each produced file into the folder with write_quick_suite_file, using the backend-native local_file_path (<Gotchas> item 6).
    Validate: Every produced file uploaded.
    If fails: Report the exact error (<Rules> item 14), keep the local files in `attached_files/`, and offer to retry the save. Continue: the showcase can still be imported for preview.

11. [Agent] Copy the Built-in Example:
    a. folder_list `workspace/attached_files/`. Move every `*demo-scenario-data.json` already there into `tmp/` with file_move, so this run's file is the only candidate at import. Moving instead of deleting keeps a local copy of an earlier created showcase whose save did not complete.
    b. folder_list `<skill_directory>/assets/marketing-consultancy/`. Copy `demo-script.md` into `attached_files/`, then `use-case-deck.html` and `use-case-deck.pptx` if present.
    c. Copy `demo-scenario-data.json` into `attached_files/` last (<Rules> item 11).
    Validate: `demo-scenario-data.json` and `demo-script.md` are in `workspace/attached_files/`, and that is the only `*demo-scenario-data.json` there.
    If fails: Tell the user the skill install looks incomplete, that the account has already been cleared, and that nothing was imported, and ask them to reinstall the skill. STOP.

12. [Agent] (exact) Download the saved showcase:
    ```
    file_copy(source="<skill_directory>/scripts/download_demo_folder.py", destination="tmp/download_demo_folder.py")
    run_python(
      file="tmp/download_demo_folder.py",
      code='print(download_demo_folder(space_name="Quick-Showcases", folder_name="<folder_name>", space_id="<space_id>", folder_id="<folder_id>"))',
      explanation="Downloading all showcase files from the Quick-Showcases space",
      tools=["search_spaces", "list_folder_members", "read_quick_suite_file"]
    )
    ```
    Use the folder_name, space_id, and folder_id from SOURCE.
    Validate: status is "success" and count is greater than 0.
    If fails (rename_required present): Show user_message exactly as returned (<Rules> item 12), add that the account has already been cleared and nothing was imported, and STOP.
    If fails (any other FAILED status): Quote the error, then say: "Showcase couldn't be located or its contents couldn't be downloaded. The account has already been cleared and nothing was imported. Please confirm the Quick-Showcases space is connected to this agent and the showcase name is correct, then try again." STOP.

13. [Agent] Continue to <Workflow - Import and Verify> with the mode, DEMO_NAME (create), the list of produced or fetched files, and any missing deck or DOCX.

</Workflow - Produce or Fetch>

<Workflow - Import and Verify
description="Re-clear the Knowledge Graph in create mode, import the scenario data, verify the result, and summarize."
tools=[file_copy, folder_list, run_python, kg_search, kg_edit, kg_add, memory_list_all, memory_delete_by_id, list_recent_feed_items, resolve_feed_item, query_conversations, delete_chat, update_feed, save_to_memory, publish_day_plan]
triggers=["Scenario data present in workspace/attached_files/ after <Workflow - Produce or Fetch>"]
>

1. [Decide] Is the mode create?
   - Yes: run step 2 before importing (<Gotchas> item 3).
   - No (load): skip to step 3. Load mode never web-searches, so its graph is already clear.

2. [Agent] (exact) Re-clear immediately before import, without asking again (<Rules> item 2). Run the same file_copy and run_python call as <Workflow - Confirm and Clear> steps 4 and 5, including the `code='cleanup(confirmation="DELETE")'` argument (without it the script deletes nothing).
   Validate: The `kg` value starts with `success`.
   If fails (`kg` is not `success`): Tell the user the graph could not be re-cleared and nothing was imported. If step 10 of <Workflow - Produce or Fetch> saved the showcase, add that it is safe in the Quick-Showcases Space and can be loaded later by name. STOP.
   If fails (only other categories): Name each one and continue.

3. [Agent] (exact) Copy and run the import script:
   ```
   file_copy(source="<skill_directory>/scripts/import_demo_data.py", destination="tmp/import_demo_data.py")
   run_python(
     file="tmp/import_demo_data.py",
     explanation="Importing the showcase (KG entities, feed items, memories) into the current account",
     tools=["kg_add", "update_feed", "save_to_memory", "publish_day_plan"],
     timeout=300
   )
   ```
   Validate: The output contains `IMPORT COMPLETE` and `Errors: 0`, and its `Loading:` line names this run's file: `<DEMO_NAME>-demo-scenario-data.json` (create), `demo-scenario-data.json` (built-in), or a file containing folder_name (from-space).
   If fails (errors greater than 0): Report the count and the listed errors, then continue. A partial import is still usable.
   If fails (`Loading:` names a different file): Tell the user exactly which file was imported, that it is not the showcase they chose, and that they should run the skill again. STOP.
   If fails (no `IMPORT COMPLETE`): Quote the error. Nothing usable was imported. In create mode, add that the saved showcase is intact. STOP.

4. [Agent] Verify the import:
   - folder_list `workspace/attached_files/` and confirm the imported scenario file is present.
   - A plain kg_search (<Gotchas> item 4) returns the showcase's entities, and list_recent_feed_items returns its feed items.
   - Do not check the memory count (<Rules> item 13, <Gotchas> item 2).
   - Create mode only: kg_search the customer or company entity by its authored name, then expand it with kg_search(node_id=<its id>) and compare total_related to its authored edge count.
   Validate: The entities and feed are present. In create mode, the customer entity exists under its authored name with roughly its authored edge count.
   If fails (the create-mode entity looks merged: missing, renamed, or with far fewer edges): Re-run step 2 and then step 3 once. If it still looks merged, report it to the user and continue. Do not retry again.
   If fails (the graph or feed is empty after a successful import): Report what is missing and continue to the summary.

5. [Agent] Present <Template - Final Summary>: link every file the run produced or fetched with `qw-file://` links to `workspace/attached_files/<file>`, without opening them (<Rules> item 16). Include, as they apply: the import counts and any errors; the cleanup categories that did not succeed; memories still processing in the background; for create, the Quick-Showcases Space and the DEMO_NAME folder (or the save error and an offer to retry), any missing deck or DOCX, and that the showcase can be reloaded later by name in load mode.

</Workflow - Import and Verify>

</Instructions>

<Templates>

<Template - Mode Picker>
```
<decision question="What would you like to do?">
  <option description="Load a showcase: the built-in example or one you saved to a space">Load a showcase</option>
  <option description="Create a new showcase tailored to a customer or industry">Create a new showcase</option>
</decision>
```
</Template - Mode Picker>

<Template - First-Run Count Confirmation>
Shown only on a first run (when the first-run marker is absent). Fill in the four counts (Knowledge Graph entities, memories, Activity Feed items, conversations) from step 3; {{conversations}} is the value of `n` from the query_conversations count, reported exactly as returned. Fill {{user}} with the signed-in user's email (or name) taken exactly from your identity context; if it is not available, remove the " ({{user}})" entirely - never guess. If a count could not be read, write "an unknown number of" in its place. When filling this card - which states what the reset will delete if the user chooses Proceed - list the conversations as data that WILL be deleted; never describe them as kept or "left in place". Only the current chat is spared. This governs the confirmation card only; if the user chooses Exit, the separate cancellation message correctly says nothing was changed.
```
<decision question="🛑 STOP!! This Quick Showcase Studio SKILL has not run on this account before, so before anything is deleted, here is what the reset will PERMANENTLY remove from the account you are signed in as ({{user}}): {{kg}} Knowledge Graph entities, {{memories}} memories, {{feed}} Activity Feed items, and {{conversations}} other conversations — every other conversation in this account is deleted; only this current chat is kept. This SKILL is meant to run ONLY while signed in as a dedicated Amazon Quick DEMO user, NEVER in your personal or work Amazon Quick account. If you recognise this as your REAL or WORK account, choose Exit. Proceed?">
  <option>Proceed</option>
  <option>Exit</option>
</decision>
```
</Template - First-Run Count Confirmation>

<Template - Destructive Confirmation>
Before presenting, replace the showcase placeholder in the question below with "the built-in example", "the saved showcase <folder_name>", or "a new showcase for <target>".
```
<decision question="This will permanently delete this account's Knowledge Graph and memories, clear its Activity Feed, and delete your other conversations, then set up {{showcase}}. A dedicated demo or throwaway account with no real connectors or knowledge bases connected is strongly recommended: it can run in an existing account, but everything above will be cleared and should be treated as permanent. Shall we proceed?">
  <option>Proceed</option>
  <option>Exit</option>
</decision>
```
</Template - Destructive Confirmation>

<Template - Deck Task Objective>
Replace `<skill_directory>` with the actual installed path before dispatching; the background task cannot resolve it.
```
Load the presentation-generation reference from <skill_directory>/references/presentation-generation.md and follow it to generate the use-case story deck (PPTX and interactive HTML) for this showcase.

PARAMETERS:
- Demo name (filename prefix): {{DEMO_NAME}}
- Customer: {{customer name, or "N/A" for an industry target}}
- Industry: {{industry}}
- Source script: workspace/attached_files/{{DEMO_NAME}}-demo-script.md
- Source data: workspace/attached_files/{{DEMO_NAME}}-demo-scenario-data.json
- Output path: workspace/attached_files
- Output filenames: {{DEMO_NAME}}-use-case-deck.pptx and {{DEMO_NAME}}-use-case-deck.html (exactly these names)

Build the deck from the source script and data (the showcase's own story). Do not catalog Amazon Quick's product features. Do not send, share, or schedule anything. Follow the reference's workflow completely.
```
</Template - Deck Task Objective>

<Template - Final Summary>
```markdown
**Showcase {{created and saved | loaded}}: {{showcase label}}**

{{Create only:}} Saved to the **Quick-Showcases** space in the folder **{{DEMO_NAME}}**. Reload it anytime with "load the {{DEMO_NAME}} showcase".

**Imported**
- Knowledge Graph: {{nodes added}} entities, {{edges added}} edges
- Activity Feed: {{feed items}} items, including the day plan
- Memories: {{memories}} queued. They appear once Quick processes them in the background, which can take until the conversation is idle.

**Presenter materials**
- [{{file name}}](qw-file://workspace/attached_files/{{file name}})
  {{one line per produced or fetched file: script .md, script .docx, deck .pptx, deck .html}}

**Needs attention** {{omit this section if empty}}
- {{each that applies: import errors with count, cleanup categories that did not succeed, a missing deck or DOCX, a save error with an offer to retry, a customer entity that still looks merged}}
```
</Template - Final Summary>

</Templates>

<Resources>

<Resource - Bundled Files>

| File | Used in |
| --- | --- |
| `README.md` | <Workflow - Select Mode and Intake> step 2 |
| `references/create-showcase.md` | <Workflow - Select Mode and Intake> steps 3 and 4; <Workflow - Produce or Fetch> steps 4 to 6 and 10 |
| `references/load-showcase.md` | <Workflow - Select Mode and Intake> steps 3 and 4 |
| `references/demo_scenario_data_schema.json` | <Workflow - Produce or Fetch> steps 3 and 5 |
| `references/presentation-generation.md` | <Workflow - Produce or Fetch> step 9 (inline deck) and <Template - Deck Task Objective> |
| `scripts/cleanup_all.py` | <Workflow - Confirm and Clear> steps 4 and 5; <Workflow - Import and Verify> step 2 |
| `scripts/md_to_docx_converter.js` | <Workflow - Produce or Fetch> step 8 |
| `scripts/download_demo_folder.py` | <Workflow - Produce or Fetch> step 12 |
| `scripts/import_demo_data.py` | <Workflow - Import and Verify> step 3 |
| `assets/marketing-consultancy/` (`demo-scenario-data.json`, `demo-script.md`, `use-case-deck.html`, and `use-case-deck.pptx` when present) | <Workflow - Produce or Fetch> step 11 |

</Resource - Bundled Files>

<Resource - Amazon Quick Documentation>

| Topic | URL |
| --- | --- |
| Installing and managing skills | <https://docs.aws.amazon.com/quick/latest/userguide/skills-desktop.html> |
| Connectors | <https://docs.aws.amazon.com/quick/latest/userguide/connections-desktop.html> |
| Built-in system tools | <https://docs.aws.amazon.com/quick/latest/userguide/system-tools-desktop.html> |

</Resource - Amazon Quick Documentation>

</Resources>
