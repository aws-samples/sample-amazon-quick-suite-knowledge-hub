"""
description: Download every document from a saved showcase folder (in the single shared Quick-Showcases space) into workspace/attached_files/. Fully dynamic — paginates the folder, branches on content type, and re-serializes parsed JSON. Enforces a name-check first: because read_quick_suite_file resolves by space + filename (ignoring the folder), every file in the folder must carry the folder name so a read can't cross showcases. Runs via run_python with search_spaces, list_folder_members, read_quick_suite_file injected. Returns a status dict.
last_updated: 2026-09-25
origin: original
"""

# ruff: noqa: F821 - tool callables and WORKSPACE_DIR are injected by the Quick runtime at execution time, not imported.

import fnmatch
import json
import os
import re
import shutil

# ================================================================
# download_demo_folder.py — download helper (single shared space)
#
# Downloads ALL documents from a showcase folder into
# workspace/attached_files/. Fully dynamic: enumerates every DOCUMENT in the
# target folder (with pagination) and downloads each one, branching on content
# type. No hardcoded file list — it picks up whatever files exist in the folder.
#
# USAGE (from the orchestrator, single run_python call):
#   run_python(
#     file="tmp/download_demo_folder.py",
#     code='print(download_demo_folder('
#          '  space_name="Quick-Showcases",'
#          '  folder_name="acme-corp-manufacturing"))',
#     explanation="Downloading all showcase files into the workspace",
#     tools=["search_spaces", "list_folder_members", "read_quick_suite_file"],
#   )
#
# TOOLS required (inject via run_python tools=[...]):
#   ["search_spaces", "list_folder_members", "read_quick_suite_file"]
#
# NAME CHECK: read_quick_suite_file looks documents up by space + document
# name only (no folder), so a generic name like "demo-script.md" that exists in
# several showcase folders may resolve to the WRONG folder's file. Before
# anything is downloaded, every document in the folder must have the folder name
# in its file name (case-sensitive). If any don't, nothing is downloaded and the
# result carries the rename plan plus ready-to-show user instructions.
#
# RETURNS a dict:
#   {"status": "success"|"FAILED", "dest_dir": "...", "files": [...],
#    "count": N, "error": "...",
#    "rename_required": [{"current": "...", "rename_to": "..."}],  # only on name-check failure
#    "user_message": "..."}                                        # only on name-check failure
# ================================================================

# Content types read_quick_suite_file returns INLINE (str, or dict/list for JSON).
# Anything not listed here is returned as a saved binary path and is copied.
TEXT_TYPES = {
    "text/markdown",
    "text/plain",
    "text/html",
    "text/csv",
    "application/json",
}

# Generic showcase file name -> required name. "{folder}" is replaced with the
# folder name. These match the names the create flow generates (folder-prefixed).
_RENAME_RULES = [
    ("demo-scenario-data.json", "{folder}-demo-scenario-data.json"),
    ("demo-script.md", "{folder}-demo-script.md"),
    ("demo-script.docx", "{folder}-demo-script.docx"),
    ("use-case-deck.pptx", "{folder}-use-case-deck.pptx"),
    ("use-case-deck.html", "{folder}-use-case-deck.html"),
]


def _suggest_name(name, folder_name, taken):
    """Suggest a name containing folder_name. Falls back to '<folder>-<name>'
    for unknown files, or when the rule's target is already taken."""
    for pattern, template in _RENAME_RULES:
        if fnmatch.fnmatchcase(name, pattern):
            cand = template.replace("{folder}", folder_name)
            if cand not in taken:
                return cand
            break
    cand = f"{folder_name}-{name}"
    n = 2
    while cand in taken:
        cand = f"{folder_name}-{n}-{name}"
        n += 1
    return cand


def _check_member_names(names, space_name, folder_name):
    """Return (rename_required, user_message). Empty list means all names are OK."""
    bad = [n for n in names if folder_name not in n]
    if not bad:
        return [], ""
    taken = {n for n in names if folder_name in n}
    plan = []
    for n in sorted(bad):
        new = _suggest_name(n, folder_name, taken)
        taken.add(new)
        plan.append({"current": n, "rename_to": new})
    rows = "\n".join(f"| `{p['current']}` | `{p['rename_to']}` |" for p in plan)
    msg = (
        f"The showcase **{folder_name}** can't be loaded yet. Some of its files don't have the "
        f"folder name (`{folder_name}`) in their file names. Amazon Quick finds files by name "
        f"across the whole space, so without the folder name in each file name it can't be sure "
        f"it's reading this showcase's files rather than another one's.\n\n"
        f"Files in **{space_name}** > **{folder_name}**:\n\n"
        f"| Current name | Rename to |\n|---|---|\n{rows}\n\n"
        f"To fix this:\n"
        f"1. In Amazon Quick on the web, open the **{space_name}** space and go to the "
        f"**{folder_name}** folder.\n"
        f"2. Download the files listed above.\n"
        f"3. Delete those files from the **{folder_name}** folder.\n"
        f"4. On your computer, rename the downloaded files as shown in the table.\n"
        f"5. Upload the renamed files back into the **{folder_name}** folder.\n"
        f"6. Load this showcase again."
    )
    return plan, msg


def download_demo_folder(space_name, folder_name, space_id=None, folder_id=None):
    result = {"status": "FAILED", "dest_dir": "", "files": [], "count": 0, "error": ""}
    try:
        session_root = os.path.dirname(WORKSPACE_DIR.rstrip("/"))
        dest_dir = os.path.join(WORKSPACE_DIR, "attached_files")
        os.makedirs(dest_dir, exist_ok=True)

        def _resolve_ws_path(p):
            """Resolve a 'workspace/...' path from a binary read response to an absolute path."""
            if os.path.isabs(p) and os.path.exists(p):
                return p
            cand = os.path.join(session_root, p)
            if os.path.exists(cand):
                return cand
            return os.path.join(WORKSPACE_DIR, p.replace("workspace/", "", 1))

        # --- 1. Resolve the space by name -> space_id (SKIP if caller passed space_id) ---
        # PREFER a caller-provided space_id: the load picker already resolves it reliably
        # (via list_folder_members while enumerating folders). Re-deriving it from the
        # search_spaces text blob is brittle — that blob can contain several "ID:" values
        # (the space plus nested resources), so anchor on the exact space name as a fallback.
        if not space_id:
            sp = search_spaces(searchTerm=space_name)
            text = sp["text"] if isinstance(sp, dict) else str(sp)
            _idx = text.find(space_name)
            _seg = text[_idx:] if _idx != -1 else text
            _m = re.search(r"ID:\s*([0-9a-fA-F\-]+)", _seg) or re.search(
                r"ID:\s*([0-9a-fA-F\-]+)", text
            )
            if not _m:
                raise ValueError(
                    f"Showcase couldn't be located. Space '{space_name}' not found."
                )
            space_id = _m.group(1)

        # --- 2. Find the target folder -> folder_id (SKIP if caller passed folder_id) ---
        if not folder_id:
            root = list_folder_members(spaceId=space_id, memberType="FOLDER")
            folder_id = next(
                (
                    mem["memberId"]
                    for mem in root.get("members", [])
                    if mem["resourceName"] == folder_name
                ),
                None,
            )
            if not folder_id:
                raise ValueError(
                    f"Showcase couldn't be located. Folder '{folder_name}' not found in space."
                )

        # --- 3. Enumerate ALL documents in the folder (paginated) ---
        docs, token = [], None
        while True:
            kwargs = {
                "spaceId": space_id,
                "folderId": folder_id,
                "memberType": "DOCUMENT",
            }
            if token:
                kwargs["nextToken"] = token
            resp = list_folder_members(**kwargs)
            docs.extend(resp.get("members", []))
            token = resp.get("nextToken")
            if not token:
                break

        if not docs:
            raise ValueError(
                "Showcase folder contents couldn't be copied over. Folder is empty."
            )

        # --- 3b. Name check BEFORE downloading anything (see header) ---
        plan, msg = _check_member_names(
            [d["resourceName"] for d in docs], space_name, folder_name
        )
        if plan:
            result["rename_required"] = plan
            result["user_message"] = msg
            result["error"] = (
                f"{len(plan)} file(s) in folder '{folder_name}' don't include "
                f"the folder name; rename required"
            )
            print(f"FAILED: {result['error']}")
            return result

        # --- 4. Download each document; branch on content type ---
        for d in docs:
            name = d["resourceName"]
            ctype = (d.get("documentDetails", {}).get("contentType") or "").lower()
            out = read_quick_suite_file(space_name=space_name, document_name=name)
            dest = os.path.join(dest_dir, name)

            if ctype in TEXT_TYPES:
                if isinstance(out, (dict, list)):
                    with open(dest, "w", encoding="utf-8") as f:
                        # indent=2 so the written file closely matches the original
                        # pretty-printed size — a minified dump looks much smaller on
                        # disk and triggers a spurious "file shrank, verify" detour.
                        json.dump(
                            out, f, ensure_ascii=False, indent=2
                        )  # parsed JSON -> write real JSON
                else:
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(out)
            else:
                pm = re.search(r"Path:\s*(\S+)", str(out))
                if not pm:
                    print(f"  ERROR  {name:34s} -> no path in binary response")
                    continue
                shutil.copy(_resolve_ws_path(pm.group(1)), dest)

            result["files"].append(name)
            print(f"  OK  {name:34s} -> {os.path.getsize(dest):,} bytes")

        result["dest_dir"] = dest_dir
        result["count"] = len(result["files"])
        result["status"] = "success"
        print(f"Downloaded {result['count']} file(s) to attached_files/")

    except Exception as e:
        result["error"] = str(e)
        print(f"FAILED: {e}")

    return result
