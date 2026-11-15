# Sophia's agents: clustering-analyst + narrative-analyst

They take Kent's `/data/comments.jsonl` and produce the two files the UI reads:
**`/data/clusters.json`** and **`/data/connectivity.json`**.

## Plug into the orchestrator (Poshitha)
```python
from narrative.agents import clustering_analyst, narrative_analyst

agent = create_deep_agent(
    model="openai:gpt-4.1-mini",
    system_prompt="...",
    subagents=[search_strategist, clustering_analyst, narrative_analyst],
    backend=FilesystemBackend(root_dir=config.WORKSPACE, virtual_mode=True),
)
```
Order: `search-strategist` → `clustering-analyst` → `narrative-analyst` → reporter / UI.

After the agent finishes, save the outputs and drop the raw comments (plain Python, not an agent tool):
```python
event_id = tools.start_event(user_query)
agent.invoke({"messages": [{"role": "user", "content": user_query}]})
from narrative.sophia_tools import archive_and_cleanup
print(archive_and_cleanup(event_id))   # saves to narrative_runs, then deletes this event's comments
```
One-time setup: run `narrative/schema_narrative_runs.sql` in the Supabase SQL editor. The UI then reads
the latest `narrative_runs` row for an event (`clusters` + `connectivity` columns) instead of `comments`.
If saving fails, nothing is deleted.
Tools take agent paths (`/data/...`) and map them to `config.WORKSPACE/data/...` on disk.

## The two agents
| agent | tools (in order) | judgment it makes | writes |
|---|---|---|---|
| **clustering-analyst** | `community_summary` → `preview_comments` → `score_stance(axis)` → `cluster_narratives(k=None)` | the event's stance question (what 0 and 1 mean); whether the clustering is usable | `stance_axis.json`; adds `stance, emotion, claim, endorse_only` to `comments.jsonl` (Kent's columns kept as-is); `clusters.json` |
| **narrative-analyst** | `set_narratives(labels)` → `build_connectivity()` → `set_group_labels()` | names each narrative, merges duplicates, names community groups | labels in `clusters.json`; `connectivity.json` |

## What happens inside
- **Stance + claims:** one batched LLM pass (`CLAIM_MODEL`, default `gpt-4.1-mini`). Stance is 0–1 on an
  event-specific axis, or `null` when a comment doesn't address it. `claim` is a neutral one-line rewrite.
- **Clustering:** all comments together (not per subreddit). Embeddings → UMAP (8D) → HDBSCAN, trying 4
  `min_cluster_size` values and keeping the best. `k=` forces exactly k clusters instead.
  Agree-only replies ("this") are folded into their parent's weight, not clustered.
- **Embeddings** (`EMBED_BACKEND`, default `auto`): OpenAI `text-embedding-3-small` on the claims when
  `OPENAI_API_KEY` is set; otherwise Kent's stored 384-d `all-MiniLM-L6-v2` vectors from Supabase
  (raw body); otherwise local MiniLM; otherwise TF-IDF (tests only).
- **Weight** (sizes only, never clustering): upvote percentile within the thread + capped bonus from
  agree-only replies, ~1 vote per author per thread, max 20% of a cell from one thread.
- **Controversy** per comment: `log(1 + replies) × (1 − score percentile)`.
- **Connectivity:** narrative distance between subreddits (√JS + MDS layout), shared commenters
  (Jaccard), community groups (subreddits merged by shared commenters), narrative × narrative reply
  matrix + echo score.

## Run it without the agents
```bash
pip install -r scrapetools/requirements.txt -r narrative/requirements.txt
python - <<'PY'
from narrative import sophia_tools as st
print(st.community_summary())
print(st.score_stance(st.DEFAULT_COMMENTS, {"question": "Did Jensen Huang do something wrong?",
                                            "0": "condemns him", "1": "defends him"}))
out = st.cluster_narratives()
print(out["n_clusters"] if "n_clusters" in out else len(out["clusters"]), "clusters")
st.set_narratives([{"cluster": c["cluster"], "label": f"narrative {c['cluster']}", "summary": "", "merge_into": None}
                   for c in out["clusters"]])
print(st.build_connectivity())
PY
```
`score_stance(..., fast_mode=True)` skips the LLM (no stance; claims = raw text) for offline tests.

## Fallback: clustering inside Supabase
`kmeans_fallback.sql` runs k-means (k=7) on the stored embeddings directly in Postgres (pgvector) and
returns per-cluster size, subreddit/thread counts, central + top + most-contested comments. Read-only.
Use it if the Python side breaks during the demo.

## Output contract for the UI
See `docs/ui_visualization_spec.md`.
