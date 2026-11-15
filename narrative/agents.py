"""Sophia's two subagents for the deep agent orchestrator.

    from narrative.agents import clustering_analyst, narrative_analyst
    agent = create_deep_agent(
        model="openai:gpt-4.1-mini",
        subagents=[search_strategist, clustering_analyst, narrative_analyst, ...],
        backend=FilesystemBackend(root_dir=config.WORKSPACE, virtual_mode=True),
    )

Order the orchestrator should follow:
    search-strategist  -> /data/comments.jsonl
    clustering-analyst -> /data/stance_axis.json, stance+claims added to comments, /data/clusters.json
    narrative-analyst  -> labels in /data/clusters.json, /data/connectivity.json   (the UI reads these two)
"""
from narrative.sophia_tools import subagent_specs

clustering_analyst, narrative_analyst = subagent_specs()

__all__ = ["clustering_analyst", "narrative_analyst"]
