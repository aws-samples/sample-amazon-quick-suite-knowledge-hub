"""
description: Clear the current Quick account's demo state before an Amazon Quick Showcase Studio run: deletes all Knowledge Graph entities and memories, and resolves activity-feed items. Conversation deletion is handled by the orchestrator with direct delete_chat calls, not in this script, because delete_chat can hang when called from run_python. Runs via run_python with injected tool callables; prints a JSON status per category (kg, memories, feed). DESTRUCTIVE.
last_updated: 2026-10-07
origin: original
"""

# ruff: noqa: F821 - tool callables and WORKSPACE_DIR are injected by the Quick runtime at execution time, not imported.

import json
import re

results = {
    "kg": "skipped",
    "memories": "skipped",
    "feed": "skipped",
}

# --- 1. Clear Knowledge Graph (delete ALL entities, including disconnected/orphan nodes) ---
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
        to_delete = [e["id"] for e in entities if e.get("id")]
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
            memory_delete_by_id(memory_ids=",".join(ids), reason="Custom demo cleanup")
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

# Conversations are intentionally NOT deleted here. delete_chat can hang when
# called from run_python (it waits on an approval the sandbox cannot surface),
# which would stall this whole script until the sandbox timeout. The orchestrator
# deletes conversations with direct delete_chat calls instead
# (see SKILL.md <Workflow - Confirm and Clear>).

print(json.dumps(results, indent=2))
