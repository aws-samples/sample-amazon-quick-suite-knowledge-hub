---
description: "Detailed load-mode instructions for quick-showcase-studio: choose a source (the built-in example or a folder in the shared Quick-Showcases space) and fetch its files into the workspace for import, with a name-check guard. Loaded on demand by SKILL.md."
last_updated: 2026-10-01
origin: original
---

# Load a Showcase — detailed instructions

This reference is loaded by `SKILL.md` when the user wants to **load** a showcase. It has two sections: **Choose source** (Step 2 of the orchestrator) and **Fetch** (Step 5). The orchestrator handles the destructive warning, cleanup, import, and summary around these.

A showcase can come from two sources:
- **Built-in example** — a ready-made showcase bundled with this skill at `<skill_directory>/assets/marketing-consultancy/`. No space, upload, or connection needed.
- **From the shared space** — a showcase you created with this skill. All created showcases live in **one shared space, `Quick-Showcases`**, as **one folder per showcase** (folder name = the showcase's `DEMO_NAME`, e.g. `acme-corp-manufacturing`).

---

## Choose source

> **If `SOURCE = built-in`** was set in Step 1 (the user asked for the built-in example): skip this whole section and go straight to **Fetch**.
> **If the user named a specific showcase/folder** in Step 1: skip the picker (Steps A–C) and resolve it directly — `search_spaces` for `Quick-Showcases` → `space_id`, then `list_folder_members(spaceId, memberType="FOLDER")` to find the folder whose name matches. Set `SOURCE = from-space` with `folder_name`, `space_id`, and `folder_id`, then go to **Fetch**.
> **Otherwise** (no source chosen yet): build the picker below.

Present **one picker** listing the **built-in example** plus **every folder in the `Quick-Showcases` space**, and let them choose.

### Step A — Discover saved showcases
Every saved showcase is a folder in the single `Quick-Showcases` space.

1. **Resolve the space.** `search_spaces` for `Quick-Showcases`. If it isn't found (or `search_spaces` is unavailable), there are no saved showcases — see Fallbacks. Record `space_id`.
2. **List its folders.** `list_folder_members(spaceId=<space_id>, memberType="FOLDER")`. Each folder is a candidate showcase.
3. **Verify each folder is a real showcase.** For each folder, `list_folder_members(spaceId=<space_id>, folderId=<folder_id>, memberType="DOCUMENT")` and confirm a `*demo-scenario-data.json` is present (the file is prefixed with the folder name, e.g. `<folder>-demo-scenario-data.json`). Keep only folders that pass; **record each one's `folder_name`, `space_id`, and `folder_id`** (you already have all three). Skip (don't fail on) any folder whose members can't be listed. Passing these resolved ids to Fetch avoids re-resolving by name.

### Step B — Present the picker
List the built-in example first, then one option per verified folder:
```
<decision question="Which showcase would you like to load?">
  <option description="Ready-made example — no space or setup needed">Built-in example (marketing consultancy)</option>
  <option description="Saved showcase">acme-corp-manufacturing</option>
  <option description="Saved showcase">healthcare</option>
  ...
</decision>
```

### Step C — Record the choice
- **Built-in example** → set `SOURCE = built-in`.
- **A saved showcase** → set `SOURCE = from-space` with that `folder_name`, `space_id`, and `folder_id`.

### Fallbacks
- **`Quick-Showcases` not found / no folders yet** (or `search_spaces` unavailable/errors): present just the built-in example, and offer *"…or tell me a showcase name to load from `Quick-Showcases`."* If the user gives a name, resolve it (`search_spaces` → `space_id`; `list_folder_members` to find the folder → `folder_id`) and set `SOURCE = from-space`.
- **Older per-space showcases won't appear.** Earlier versions of this skill saved each showcase to its own `custom-demo-<…>` space; those are **not** listed here (this version reads only folders in `Quick-Showcases`). If the user asks for one, tell them it must be re-created with the current skill.
- **Spaces connector disabled:** only the built-in example is available; say so plainly and load it if the user wants.

Return to the orchestrator for the destructive warning and cleanup, then continue with **Fetch**.

---

## Fetch

Goal: get the chosen showcase's files into `workspace/attached_files/`.

**If `SOURCE = built-in` — copy the bundled example (no space needed):**
The built-in example lives at `<skill_directory>/assets/marketing-consultancy/`. It ships `demo-scenario-data.json`, `demo-script.md`, and a use-case story page `use-case-deck.html` (and may include `use-case-deck.pptx` if one was added).
1. Copy the two required files:
   ```
   file_copy(source="<skill_directory>/assets/marketing-consultancy/demo-scenario-data.json",
             destination="attached_files/demo-scenario-data.json")
   file_copy(source="<skill_directory>/assets/marketing-consultancy/demo-script.md",
             destination="attached_files/demo-script.md")
   ```
2. **Copy any companions that are present** — list `<skill_directory>/assets/marketing-consultancy/` and `file_copy` each of these that exists into `attached_files/` (skip any that aren't there): `use-case-deck.html`, `use-case-deck.pptx`.

Only `demo-scenario-data.json` is imported into the account; the script and story page are surfaced as `qw-file://` links in the Step 7 summary. Confirm `demo-scenario-data.json` and `demo-script.md` are present in `workspace/attached_files/`.
- **On failure:** if the two required files are missing, the skill install may be incomplete — tell the user to reinstall and STOP.

**Otherwise — download from the `Quick-Showcases` space:**
1. `file_copy(source="<skill_directory>/scripts/download_demo_folder.py", destination="tmp/download_demo_folder.py")`
2. Run it, **passing the `space_id` and `folder_id` you resolved during Choose source** (this avoids re-resolving by name, which is more brittle):
   ```
   run_python(
     file="tmp/download_demo_folder.py",
     code='print(download_demo_folder(space_name="Quick-Showcases", folder_name="<folder_name>", space_id="<space_id>", folder_id="<folder_id>"))',
     explanation="Downloading all showcase files from the Quick-Showcases space",
     tools=["search_spaces", "list_folder_members", "read_quick_suite_file"],
   )
   ```
   (If you don't have `space_id`/`folder_id`, you can omit them — the script falls back to resolving by name.)
3. The script enumerates all documents in the folder and downloads them; it returns a dict with `status`, `files`, `count`, `error`, and — on a name-check failure — `rename_required` + `user_message`.
- **Success criterion:** `status: "success"` and `count > 0`.
- **Name-check failure** (`rename_required` present): because Amazon Quick reads files by space + filename (ignoring the folder), every file in the folder must contain the folder name so a read can't cross showcases. If some don't, the script downloads nothing and returns a `user_message`. **Show `user_message` to the user exactly as returned** (it lists each file and its correct name and the download → rename → re-upload steps), then STOP — do not rename/upload/delete the files yourself, and do not try to load another way.
- **Other failure** (`status == "FAILED"` without `rename_required`): output the error and STOP:
  > "Showcase couldn't be located or its contents couldn't be downloaded. Please confirm the `Quick-Showcases` space is connected to this agent and the showcase name is correct, then try again."

After Fetch, return to the orchestrator: it imports the scenario data (Step 6) and shows the summary with links to any supporting materials (Step 7).
