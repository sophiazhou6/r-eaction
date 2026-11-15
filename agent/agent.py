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


@tool
def cluster_summaries_tool(path: str = "/data/comments.jsonl") -> str:
    """Call FIRST, before labeling. For each cluster: size, keywords, subreddits, mean stance,
    localized flag, and ~8 representative comments (claim + original text)."""
    return glue.cluster_summaries(path)


@tool
def report_context_tool(path: str = "/data/comments.jsonl") -> str:
    """Everything needed for the briefing: narratives with quotes, communities, groups,
    shared commenters, and content-vs-connectivity contrasts. Small JSON."""
    return glue.report_context(path)


def build_subagents():
    clustering, narrative = subagent_specs()
    narrative = {
        **narrative,
        "tools": [cluster_summaries_tool, *narrative["tools"]],
        "system_prompt": "Step 0: call cluster_summaries_tool to see what each cluster says.\n\n"
                         + narrative["system_prompt"],
    }
    search = {
        "name": "search-strategist",
        "description": "Turns the event into Reddit searches, scores them by recall@k, loops until "
                       "coverage is good, then downloads the threads to /data/comments.jsonl.",
        "system_prompt": prompts.SEARCH_STRATEGIST,
        "tools": [kent.plan_queries, kent.set_event_description, kent.search_reddit,
                  kent.score_recall, kent.fetch_threads],
    }
    reporter = {
        "name": "reporter",
        "description": "Writes the final briefing to /data/briefing.json from report_context.",
        "system_prompt": prompts.REPORTER,
        "tools": [report_context_tool],
    }
    return [search, clustering, narrative, reporter]


def build_agent():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return create_deep_agent(
        model=MODEL,
        tools=[],  # the orchestrator only plans and delegates
        system_prompt=prompts.ORCHESTRATOR,
        middleware=[TodoListMiddleware()],  # adds write_todos
        subagents=build_subagents(),
        # agents see WORKSPACE as "/", so /data/comments.jsonl = workspace/data/comments.jsonl
        backend=FilesystemBackend(root_dir=config.WORKSPACE, virtual_mode=True),
    )
