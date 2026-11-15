"""Builds the orchestrator deep agent from Kent's and Sophia's pieces."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scrapetools"))  # Kent's modules import each other by plain name

import config  # noqa: E402  Kent: WORKSPACE, Supabase client
import tools as kent  # noqa: E402  Kent: scrapetools/tools.py
from deepagents import create_deep_agent  # noqa: E402
from deepagents.backends import FilesystemBackend  # noqa: E402
from langchain.agents.middleware import TodoListMiddleware  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from agent import glue, prompts  # noqa: E402
from narrative.sophia_tools import subagent_specs  # noqa: E402

# Dev: cheap model. Demo: MODEL=anthropic:claude-sonnet-5-5 (or openai:gpt-4.1)
MODEL = os.getenv("MODEL", "anthropic:claude-haiku-5-5")

# How much to download. run.py sets these: live runs stay fast, --demo runs go deep.
MAX_POSTS = os.getenv("MAX_POSTS", "10")
MAX_COMMENTS = os.getenv("MAX_COMMENTS", "1000")
LIVE = os.getenv("LIVE_MODE") == "1"  # set by agent/server.py: fast path for website searches


@tool
def cluster_summaries_tool(path: str = "/data/comments.jsonl") -> str:
    """Call FIRST, before labeling. For each cluster: size, keywords, subreddits, mean stance,
    stance_split, localized flag, and ~8 representative comments (claim + original text).
    If stance_split.polarized is true, label the TOPIC neutrally, not one side."""
    return glue.cluster_summaries(path)


@tool
def report_context_tool(path: str = "/data/comments.jsonl") -> str:
    """Everything needed for the briefing: narratives with quotes, stance splits, reply
    patterns (echo_score, argues_with), communities, groups, shared commenters,
    content-vs-connectivity contrasts, and the timeline story if it exists. Small JSON."""
    return glue.report_context(path)


@tool
def timeline_tool(path: str = "/data/comments.jsonl") -> str:
    """Evidence for the story over time: origin, windows (narrative mix, stance split,
    communities, quotes, threads that started then), turning points, spread order."""
    return glue.timeline_summary(path)


@tool
def search_many_live(queries: list[str], subreddits: list[str], limit: int = 50,
                     time_filter: str = "month") -> str:
    """LIVE MODE search: runs the 2 best queries x 4 best subreddits in parallel (extra ones are dropped).
    Returns a count per search plus the total number of unique posts found."""
    return kent.search_many(queries[:2], subreddits[:4], limit=min(limit, 50), time_filter=time_filter)


def timeline_subagent():
    return {
        "name": "timeline-analyst",
        "description": "Tells how the conversation evolved over time: origin, themed phases, "
                       "turning points with likely triggers, and spread. Writes /data/timeline.json.",
        "system_prompt": prompts.TIMELINE_ANALYST,
        "tools": [timeline_tool],
    }


def reporter_subagent():
    return {
        "name": "reporter",
        "description": "Writes the final briefing to /data/briefing.json from report_context_tool.",
        "system_prompt": prompts.REPORTER,
        "tools": [report_context_tool],
    }


def build_subagents():
    clustering, narrative = subagent_specs()
    clustering = {**clustering, "system_prompt": prompts.NO_FILES_NOTE + clustering["system_prompt"]}
    narrative = {
        **narrative,
        "tools": [cluster_summaries_tool, *narrative["tools"]],
        "system_prompt": prompts.NO_FILES_NOTE
        + "Step 0: call cluster_summaries_tool to see what each cluster says.\n"
          "set_narratives_tool must FINISH before you call connectivity_tool.\n\n"
        + narrative["system_prompt"],
    }
    search = {
        "name": "search-strategist",
        "description": "Turns the event into Reddit searches, scores them by recall@k, loops until "
                       "coverage is good, then downloads the threads to /data/comments.jsonl.",
        "system_prompt": prompts.SEARCH_STRATEGIST.replace("__MAX_POSTS__", MAX_POSTS)
                                                  .replace("__MAX_COMMENTS__", MAX_COMMENTS),
        "tools": [kent.plan_queries, kent.set_event_description, kent.search_many,
                  kent.search_reddit, kent.score_recall, kent.fetch_threads],
    }
    if LIVE:
        search["system_prompt"] = prompts.LIVE_SEARCH + search["system_prompt"]
        search["tools"] = [kent.plan_queries, kent.set_event_description, search_many_live,
                           kent.score_recall, kent.fetch_threads]
    return [search, clustering, narrative, timeline_subagent(), reporter_subagent()]


def build_agent():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return create_deep_agent(
        model=MODEL,
        tools=[],  # the orchestrator only plans and delegates
        system_prompt=prompts.ORCHESTRATOR + (prompts.LIVE_ORCHESTRATOR if LIVE else ""),
        middleware=[TodoListMiddleware()],  # adds write_todos
        subagents=build_subagents(),
        # agents see WORKSPACE as "/", so /data/comments.jsonl = workspace/data/comments.jsonl
        backend=FilesystemBackend(root_dir=config.WORKSPACE, virtual_mode=True),
    )


ENRICH_PROMPT = """Cached results for one event are in /data. Do exactly two steps, one at a time:
1. task timeline-analyst: "narratives are labeled; write /data/timeline.json".
2. task reporter: "write /data/briefing.json".
Then reply with the reporter's headline."""


def build_enrich_agent():
    """Small agent for runs that already finished: adds the timeline and rewrites the briefing."""
    return create_deep_agent(
        model=MODEL, tools=[], system_prompt=ENRICH_PROMPT,
        middleware=[TodoListMiddleware()],
        subagents=[timeline_subagent(), reporter_subagent()],
        backend=FilesystemBackend(root_dir=config.WORKSPACE, virtual_mode=True),
    )
