"""Run one investigation end to end.

From the repo root:
    uv run python -m agent.run "jensen huang scandal"            # live: 10 threads x 1000 comments
    uv run python -m agent.run "anthropic IPO" --demo            # deep: 15 threads x 5000 comments
    uv run python -m agent.run "FAA closure" --demo --archive    # also save to Supabase narrative_runs

Every run copies its outputs to workspace/cache/<event-slug>/ so demo events are kept.
"""
import json
import os
import re
import shutil
import sys

from dotenv import load_dotenv

load_dotenv()

FLAGS = {a for a in sys.argv[1:] if a.startswith("--")}
if "--demo" in FLAGS:  # must be set before agent.agent is imported
    os.environ.setdefault("MAX_POSTS", "15")
    os.environ.setdefault("MAX_COMMENTS", "5000")

from agent.agent import MAX_COMMENTS, MAX_POSTS, build_agent, config, kent  # noqa: E402

OUTPUTS = ("queries.json", "stance_axis.json", "subreddit_aliases.json", "clusters.json", "connectivity.json",
           "briefing.json")


def main():
    event = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or "jensen huang scandal"

    # fresh workspace per event: Sophia's tools cache embeddings/UMAP next to the data
    if config.DATA_DIR.exists():
        shutil.rmtree(config.DATA_DIR)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    event_id = kent.start_event(event)  # Supabase events row + resets /data files
    print(f"Investigating: {event}  (event_id={event_id}, up to {MAX_POSTS} threads x {MAX_COMMENTS} comments)\n")

    agent = build_agent()
    final = None
    for update in agent.stream(
        {"messages": [{"role": "user", "content": event}]},
        stream_mode="updates",
        config={"recursion_limit": 150},
    ):
        for _node, data in update.items():
            msgs = data.get("messages", []) if isinstance(data, dict) else []
            for m in msgs if isinstance(msgs, list) else []:
                for call in getattr(m, "tool_calls", None) or []:
                    print(f"-> {call['name']} {call['args'].get('subagent_type', '')}")
                if getattr(m, "type", "") == "ai" and m.content:
                    final = m.content

    print("\n=== Final ===")
    print(final)

    print(f"\nFiles in {config.DATA_DIR}:")
    for name in OUTPUTS:
        print(f"  {'OK     ' if (config.DATA_DIR / name).exists() else 'MISSING'} {name}")

    # add a deterministic sentiment section (stance split + emotions) to the briefing
    briefing = config.DATA_DIR / "briefing.json"
    if briefing.exists() and (config.DATA_DIR / "clusters.json").exists():
        from agent.glue import sentiment_summary
        try:
            b = json.loads(briefing.read_text())
            b["sentiment"] = sentiment_summary()
            briefing.write_text(json.dumps(b, indent=2))
            print("Added sentiment section to briefing.json")
        except Exception as e:
            print(f"(sentiment section skipped: {e})")

    # keep this event's outputs (the next run wipes workspace/data)
    slug = re.sub(r"[^a-z0-9]+", "-", event.lower()).strip("-")[:60]
    cache = config.WORKSPACE / "cache" / slug
    cache.mkdir(parents=True, exist_ok=True)
    for name in OUTPUTS:
        if (config.DATA_DIR / name).exists():
            shutil.copy2(config.DATA_DIR / name, cache / name)
    (cache / "meta.json").write_text(json.dumps({"event": event, "event_id": event_id,
                                                 "max_posts": MAX_POSTS, "max_comments": MAX_COMMENTS}))
    print(f"Cached to {cache}")

    if "--archive" in FLAGS:  # Sophia's archiver; never deletes comments from here
        from narrative.sophia_tools import archive_and_cleanup
        print("Archive:", archive_and_cleanup(event_id, delete_comments=False))


if __name__ == "__main__":
    main()
