"""Add the timeline story to runs that already finished, rewrite their briefing, republish.

    uv run python -m agent.enrich                        # every cached event missing timeline.json
    uv run python -m agent.enrich jensen-huang-scandal   # one event
    WORKSPACE=./workspace2 uv run python -m agent.enrich # events cached in workspace2

Runs a small deep agent (timeline-analyst -> reporter) on a COPY of each cached event, so
the original files are only replaced once it succeeds. Does not touch Reddit or Supabase
tables; only republishes to Storage.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _one(src: Path):
    """Child process: WORKSPACE points at a scratch copy; run the enrich agent there."""
    from agent.agent import build_enrich_agent, config
    from agent.glue import sentiment_summary

    agent = build_enrich_agent()
    out = agent.invoke({"messages": [{"role": "user", "content": "enrich this event"}]},
                       config={"recursion_limit": 60})
    print("  agent:", str(out["messages"][-1].content)[:200])
    data = config.DATA_DIR
    if (data / "briefing.json").exists():
        b = json.loads((data / "briefing.json").read_text())
        b["sentiment"] = sentiment_summary()
        (data / "briefing.json").write_text(json.dumps(b, indent=2))
    for name in ("timeline.json", "briefing.json"):
        if (data / name).exists():
            shutil.copy2(data / name, src / name)
            print(f"  updated {name}")


def main():
    if "--one" in sys.argv:
        _one(Path(sys.argv[sys.argv.index("--one") + 1]))
        return

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scrapetools"))
    import config  # noqa: E402

    cache = config.WORKSPACE / "cache"
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")]
    slugs = wanted or sorted(p.name for p in cache.iterdir()
                             if p.is_dir() and not (p / "timeline.json").exists())
    if not slugs:
        print("Nothing to enrich (every cached event already has timeline.json).")
        return

    for slug in slugs:
        src = cache / slug
        if not (src / "clusters.json").exists():
            print(f"skip {slug}: no clusters.json")
            continue
        print(f"Enriching {slug} ...")
        scratch = config.WORKSPACE / "enrich" / slug
        if scratch.exists():
            shutil.rmtree(scratch)
        (scratch / "data").mkdir(parents=True)
        for f in src.glob("*.json"):
            shutil.copy2(f, scratch / "data" / f.name)
        # raw comments give thread titles for triggers; only if they belong to this event
        meta = json.loads((src / "meta.json").read_text()) if (src / "meta.json").exists() else {}
        for raw_dir in (config.DATA_DIR,):
            q = raw_dir / "queries.json"
            if (raw_dir / "comments.jsonl").exists() and q.exists():
                try:
                    if json.loads(q.read_text()).get("user_query") == meta.get("event"):
                        shutil.copy2(raw_dir / "comments.jsonl", scratch / "data" / "comments.jsonl")
                except ValueError:
                    pass
        env = {**os.environ, "WORKSPACE": str(scratch)}
        r = subprocess.run([sys.executable, "-m", "agent.enrich", "--one", str(src)], env=env)
        if r.returncode != 0:
            print(f"  enrich failed for {slug} (originals untouched)")
            continue
        try:
            from agent.publish import publish
            publish([slug])
        except Exception as e:
            print(f"  (publish skipped: {str(e)[:120]})")


if __name__ == "__main__":
    main()
