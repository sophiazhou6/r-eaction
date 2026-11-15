"""Run one investigation end to end.

From the repo root:
    uv run python -m agent.run "jensen huang scandal"
    uv run python -m agent.run "anthropic IPO" --keep      # don't wipe the previous run's files
"""
import json
import shutil
import sys

from dotenv import load_dotenv

load_dotenv()

from agent.agent import build_agent, config, kent  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    event = " ".join(args) or "jensen huang scandal"

    # fresh workspace per event: Sophia's tools cache embeddings/UMAP next to the data
    if "--keep" not in sys.argv and config.DATA_DIR.exists():
        shutil.rmtree(config.DATA_DIR)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    event_id = kent.start_event(event)  # Supabase events row + resets /data files
    print(f"Investigating: {event}  (event_id={event_id})\n")

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

    briefing = config.DATA_DIR / "briefing.json"
    print(f"\nFiles in {config.DATA_DIR}:")
    for name in ("comments.jsonl", "clusters.json", "connectivity.json", "briefing.json"):
        print(f"  {'OK ' if (config.DATA_DIR / name).exists() else 'MISSING'} {name}")

    # Best effort: save the briefing on the event row (needs a jsonb `briefing` column)
    if briefing.exists():
        try:
            config.supabase().table("events").update(
                {"briefing": json.loads(briefing.read_text())}).eq("event_id", event_id).execute()
            print("Briefing saved to Supabase events row.")
        except Exception as e:
            print(f"(Supabase briefing save skipped: {str(e)[:120]})")


if __name__ == "__main__":
    main()
