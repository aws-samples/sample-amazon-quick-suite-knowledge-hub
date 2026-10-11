"""
description: Import demo-scenario-data.json from workspace/attached_files/ into the current Quick account — populates Knowledge Graph nodes and edges, publishes the Activity Feed (including the day plan), and saves memories. Uses relative time offsets so the data always looks fresh. Runs via run_python with kg_add, update_feed, save_to_memory, publish_day_plan injected.
last_updated: 2026-10-01
origin: original
"""

# ruff: noqa: F821 - tool callables and WORKSPACE_DIR are injected by the Quick runtime at execution time, not imported.

import datetime
import glob
import json
import os

# Use the machine's local timezone. All times are relative offsets from load
# time, so absolute times render in the viewer's own timezone.
_NOW = datetime.datetime.now().astimezone()


def _abs_iso(offset_seconds):
    """Convert a relative *_offset_seconds (from load time) to an absolute ISO-8601 string."""
    return (_NOW + datetime.timedelta(seconds=offset_seconds or 0)).isoformat()


# publish_day_plan recommendation.type accepts only these values.
_REC_TYPE_MAP = {
    "prep": "prep_reminder",
    "focus": "block_time",
    "focus_time": "block_time",
    "conflict": "conflict",
    "reschedule": "reschedule",
}

# --- Locate the demo data JSON in attached_files ---
# Accept the canonical `demo-scenario-data.json` (bundled built-in) or a
# folder-PREFIXED name from create/load (e.g.
# `acme-corp-manufacturing-demo-scenario-data.json`). Prefix-only glob; the
# single-space design produces no suffix variants.
#
# IMPORTANT (multi-showcase safety): several showcases' files can linger in
# attached_files within one session (e.g. create A, then create B, or create
# then load). Picking alphabetically could import the WRONG showcase. The
# current operation always writes/downloads/copies ITS scenario file LAST, so
# select the MOST RECENTLY MODIFIED match — reliably this showcase's file.
_matches = glob.glob(f"{WORKSPACE_DIR}/attached_files/*demo-scenario-data.json")
if not _matches:
    print("ERROR: No *demo-scenario-data.json file found in attached_files/")
    print("Please upload the demo data JSON file first.")
    raise SystemExit(1)

json_path = max(_matches, key=os.path.getmtime)
if len(_matches) > 1:
    print(f"Note: {len(_matches)} scenario files present; using the most recent.")
print(f"Loading: {os.path.basename(json_path)}")

with open(json_path, encoding="utf-8") as f:
    data = json.load(f)

totals = {
    "kg_nodes": 0,
    "kg_node_upd": 0,
    "kg_edges": 0,
    "feed": 0,
    "mem": 0,
    "errors": [],
}

# ================================================================
# PHASE 1: KNOWLEDGE GRAPH — entities then edges
# ================================================================
print("PHASE 1: KNOWLEDGE GRAPH")
print("-" * 50)

kg_items = data.get("knowledge_graph", [])

# --- Prepare nodes ---
nodes = []
for item in kg_items:
    nodes.append(
        {
            "name": item["name"],
            "category": item["category"],
            "summary": item["summary"],
            "source_type": item.get("source_type", "other"),
        }
    )

# --- Prepare unique edges (deduplicate by canonical triple) ---
seen = set()
edges = []
for item in kg_items:
    ename = item["name"]
    for e in item.get("edges", []):
        if e["direction"] == "outgoing":
            fn, tn = ename, e["target_name"]
        else:
            fn, tn = e["target_name"], ename
        key = (fn, tn, e["relation"])
        if key not in seen:
            seen.add(key)
            edges.append(
                {
                    "from_name": fn,
                    "to_name": tn,
                    "relation": e["relation"],
                    "properties": {
                        "reason": e.get("reason", "Imported from demo data")
                    },
                }
            )

print(f"  Prepared {len(nodes)} nodes, {len(edges)} unique edges")

# --- Batch insert nodes (25 per batch) ---
NBATCH = 25
for i in range(0, len(nodes), NBATCH):
    batch = nodes[i : i + NBATCH]
    try:
        r = kg_add(nodes=json.dumps(batch), source_type="other")
        if isinstance(r, dict):
            totals["kg_nodes"] += r.get("added", 0)
            totals["kg_node_upd"] += r.get("updated", 0)
        lo, hi = i + 1, min(i + NBATCH, len(nodes))
        print(
            f"  Nodes [{lo}-{hi}]: added={r.get('added', 0)}, updated={r.get('updated', 0)}"
        )
    except Exception as ex:
        totals["errors"].append(f"nodes batch {i}: {ex}")
        print(f"  ERROR nodes batch {i}: {ex}")

# --- Batch insert edges (40 per batch) ---
EBATCH = 40
for i in range(0, len(edges), EBATCH):
    batch = edges[i : i + EBATCH]
    try:
        r = kg_add(edges=json.dumps(batch))
        ea = r.get("edges_added", 0) if isinstance(r, dict) else 0
        totals["kg_edges"] += ea
        lo, hi = i + 1, min(i + EBATCH, len(edges))
        print(f"  Edges [{lo}-{hi}]: added={ea}")
    except Exception as ex:
        totals["errors"].append(f"edges batch {i}: {ex}")
        print(f"  ERROR edges batch {i}: {ex}")

# ================================================================
# PHASE 2: ACTIVITY FEED — items
# ================================================================
print("\nPHASE 2: ACTIVITY FEED")
print("-" * 50)

feed_items = data.get("activity_feed", [])
print(f"  Inserting {len(feed_items)} feed items...")

for idx, item in enumerate(feed_items):
    try:
        det = item.get("details", {})

        # --- Day plan: must be published via publish_day_plan (populates the
        #     calendar surface), NOT update_feed (which makes a flat card). ---
        if item.get("event_type") == "day_plan":
            dp_events = []
            for j, ev in enumerate(det.get("events", [])):
                dp_events.append(
                    {
                        "ref_id": f"ev{j + 1}",
                        "title": ev.get("title", ""),
                        "start": _abs_iso(ev.get("start_offset_seconds")),
                        "end": _abs_iso(ev.get("end_offset_seconds")),
                        "organizer": ev.get("organizer", ""),
                        "status": ev.get("status", "confirmed"),
                        "meetingId": ev.get("meetingId", ""),
                    }
                )
            dp_recs = []
            for rec in det.get("recommendations", []):
                r_out = {
                    "type": _REC_TYPE_MAP.get(
                        rec.get("type"), rec.get("type", "prep_reminder")
                    ),
                    "priority": rec.get("priority", "medium"),
                    "title": rec.get("title", ""),
                    "summary": rec.get("summary", ""),
                    "context": rec.get("context", {}),
                }
                if rec.get("choices"):
                    r_out["choices"] = rec["choices"]
                dp_recs.append(r_out)
            publish_day_plan(events=dp_events, recommendations=dp_recs)
            totals["feed"] += 1
            print(
                f"  Published day plan: {len(dp_events)} events, {len(dp_recs)} recommendations"
            )
            continue

        title = det.get("title", item.get("summary", "")[:60])
        summary = det.get("full_message", item.get("summary", ""))
        ctx = det.get("context", {})
        raw_choices = det.get("choices", [])

        # Ensure each choice has prompt or url
        choices = []
        for c in raw_choices:
            if "prompt" in c or "url" in c:
                choices.append(c)
            else:
                choices.append(
                    {
                        "label": c.get("label", "View"),
                        "prompt": c.get("label", "View details"),
                    }
                )
        if not choices:
            choices = [{"label": "View", "prompt": f"Tell me about: {title[:80]}"}]

        # Use importance directly from the data (already tool-compatible values)
        importance = item.get("importance", det.get("importance", "fyi"))

        kwargs = {
            "summary": summary,
            "title": title,
            "context": ctx,
            "importance": importance,
            "event_type": item.get("event_type", "notification"),
            "choices": choices,
        }
        if det.get("icon"):
            kwargs["icon"] = det["icon"]
        if item.get("labels"):
            kwargs["labels"] = item["labels"]

        r = update_feed(**kwargs)
        totals["feed"] += 1

        if (idx + 1) % 10 == 0 or idx == len(feed_items) - 1:
            print(f"  Progress: {idx + 1}/{len(feed_items)}")
    except Exception as ex:
        totals["errors"].append(f"feed {item.get('id', '?')}: {ex}")
        print(f"  ERROR feed {item.get('id', '?')}: {ex}")

# ================================================================
# PHASE 3: MEMORIES — feed preferences + regular memories
# ================================================================
print("\nPHASE 3: MEMORIES")
print("-" * 50)

memories = data.get("memories", [])
for mem in memories:
    try:
        r = save_to_memory(hint=mem["text"])
        totals["mem"] += 1
        print(f"  \u2713 Memory: {mem['text'][:65]}...")
    except Exception as ex:
        totals["errors"].append(f"memory {mem.get('id', '?')}: {ex}")
        print(f"  ERROR memory {mem.get('id', '?')}: {ex}")

# ================================================================
# SUMMARY
# ================================================================
print("\n" + "=" * 50)
print("IMPORT COMPLETE")
print("=" * 50)
print(f"  KG Nodes added:   {totals['kg_nodes']}")
print(f"  KG Nodes updated: {totals['kg_node_upd']}")
print(f"  KG Edges added:   {totals['kg_edges']}")
print(f"  Feed items:       {totals['feed']}/{len(feed_items)}")
print(f"  Memories:         {totals['mem']}/{len(memories)}")
errs = totals["errors"]
if errs:
    print(f"  Errors ({len(errs)}):")
    for e in errs[:15]:
        print(f"    \u2022 {e}")
else:
    print("  Errors: 0 \U0001f389")
