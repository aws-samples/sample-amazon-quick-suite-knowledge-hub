"""
description: Clear the current Quick account's demo state before an Amazon Quick Showcase Studio run: deletes all Knowledge Graph entities and memories, and resolves activity-feed items. Conversation deletion is handled by the orchestrator with direct delete_chat calls, not in this script, because delete_chat can hang when called from run_python. Runs via run_python with injected tool callables; prints a JSON status per category (kg, memories, feed). DESTRUCTIVE. The wipe is gated: it only runs when cleanup() is called with confirmation="DELETE" (passed via run_python's code= argument). Importing or executing this file on its own defines the function but deletes nothing.
last_updated: 2026-10-07
origin: original
"""

# ruff: noqa: F821 - tool callables and WORKSPACE_DIR are injected by the Quick runtime at execution time, not imported.

import json
import re

# Identity of the first-run marker entity. Cleanup never deletes this entity, so
# the marker survives the wipe and a later run can detect that this account has
# already been set up by the skill.
#
# The marker is matched by its exact NAME, not its category: the Knowledge Graph
# normalizes a custom category (e.g. "QuickShowcaseStudioConfig") into one of its
# own ontology types (observed: it was stored as "DefinedTerm"), so the category
# we wrote cannot be relied on. The name is preserved verbatim. MARKER_CATEGORY is
# kept only as a secondary match in case a future Quick version preserves it.
MARKER_NAME = "Quick Showcase Studio Config"
MARKER_CATEGORY = "QuickShowcaseStudioConfig"

# The destructive wipe only runs when the caller passes exactly this token via
# run_python's code= argument: cleanup(confirmation="DELETE"). This is a code-level
# interlock against accidental or standalone execution (a misfire, a future
# refactor, or running the file directly) — NOT a user-consent check. User consent
# is enforced by the orchestrator's typed-DELETE confirmation cards in SKILL.md
# <Workflow - Confirm and Clear>; the model is what passes this token after those
# cards, so the token proves the post-confirmation step was reached, not that a
# human typed it.
REQUIRED_CONFIRMATION = "DELETE"


def cleanup(confirmation=None):
    """Clear KG entities, memories, and feed items. Destructive; gated on confirmation.

    Call as cleanup(confirmation="DELETE") to run the wipe. Any other value (or
    none) refuses: it deletes nothing and prints a refusal status whose kg value
    does not start with "success", so the orchestrator's pre-import check stops
    the run. Returns the same dict it prints.
    """
    # --- Gate: refuse unless the exact confirmation token was passed ---
    if confirmation != REQUIRED_CONFIRMATION:
        refused = {
            "status": "refused",
            "kg": "refused: confirmation token not provided",
            "memories": "refused: confirmation token not provided",
            "feed": "refused: confirmation token not provided",
        }
        print(json.dumps(refused, indent=2))
        return refused

    results = {
        "kg": "skipped",
        "memories": "skipped",
        "feed": "skipped",
    }

    # --- 1. Clear Knowledge Graph (delete all entities EXCEPT the first-run marker) ---
    # IMPORTANT: do NOT use mode="pagerank" for enumeration. PageRank ordering can omit
    # disconnected nodes once their edges are deleted, leaving orphan entities behind
    # (observed: a prior showcase's stragglers surviving cleanup). A plain kg_search
    # enumerates every entity regardless of connectivity. Loop until a full sweep returns
    # nothing; a guard prevents an infinite loop if a delete ever fails to take effect.
    try:
        deleted_count = 0
        guard = 0
        while guard < 500:
            guard += 1
            search = kg_search(limit=100)
            entities = search.get("entities", []) if isinstance(search, dict) else []
            to_delete = [
                e["id"]
                for e in entities
                if e.get("id")
                and e.get("name") != MARKER_NAME
                and e.get("category") != MARKER_CATEGORY
            ]
            if not to_delete:
                break
            kg_edit(delete=",".join(to_delete))
            deleted_count += len(to_delete)
        results["kg"] = f"success: deleted {deleted_count} entities"
    except Exception as e:
        results["kg"] = f"FAILED: {str(e)}"

    # --- 2. Clear Memories ---
    try:
        mem_list = memory_list_all()
        mem_text = str(mem_list)
        if "No memories found" in mem_text or mem_text.strip() == "":
            results["memories"] = "success: no memories to delete"
        else:
            ids = re.findall(r"`([a-f0-9]{6,})`", mem_text)
            if ids:
                memory_delete_by_id(
                    memory_ids=",".join(ids), reason="Custom demo cleanup"
                )
                results["memories"] = f"success: deleted {len(ids)} memories"
            else:
                results["memories"] = "success: no memory IDs found to delete"
    except Exception as e:
        results["memories"] = f"FAILED: {str(e)}"

    # --- 3. Resolve Activity Feed ---
    try:
        feed = list_recent_feed_items(limit=100)
        items = feed.get("items", []) if isinstance(feed, dict) else []
        resolved_count = 0
        for item in items:
            eid = item.get("event_id", "")
            if eid:
                try:
                    resolve_feed_item(
                        event_id=eid,
                        status="done",
                        reason="Cleared as part of custom demo setup",
                    )
                    resolved_count += 1
                except Exception:  # noqa: S110  # nosec B110 - best-effort per-item cleanup; ignore a single item's failure and continue
                    pass
        results["feed"] = f"success: resolved {resolved_count} items"
    except Exception as e:
        results["feed"] = f"FAILED: {str(e)}"

    # Conversations ARE deleted on a run, but by the orchestrator (direct delete_chat
    # calls), NOT by this script. delete_chat can hang when called from run_python
    # (it waits on an approval the sandbox cannot surface), which would stall this
    # whole script until the sandbox timeout, so the orchestrator deletes them with
    # direct delete_chat calls instead (see SKILL.md <Workflow - Confirm and Clear>).

    print(json.dumps(results, indent=2))
    return results


# No module-level call: executing this file only DEFINES cleanup(). The wipe is
# triggered solely by run_python's code='cleanup(confirmation="DELETE")'.
