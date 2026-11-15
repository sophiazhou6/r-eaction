# Reddit Narrative Agent

HackPrinceton · Arlequin AI Track 2: Social Media Analysis (content + connectivity)

## What it does

A user types a news event ("jensen huang scandal"). The system finds Reddit discussion of it, groups the comments into the **narratives** people are telling, measures **stance** on the event's main question, and shows **how communities connect**: which subreddits tell the same story, which share people, and which narratives argue with each other.

**One-line pitch:** Talk to the City–style opinion mapping plus Waller & Anderson–style community structure, run live for any news event.

## Team

| Part | Owner | Type |
|---|---|---|
| Orchestrator (deep agent, plans and delegates) | Poshitha | Agent |
| Search strategist + Reddit tools | Kent | Agent + Python tools |
| Clustering analyst + narrative analyst | Sophia | 2 agents + Python tools (`sophia_tools.py`) |
| Reporter + UI / graph view | — | Agent + frontend |

Rule of thumb: **Python tools do the work; agents only make the judgment calls.**

## Pipeline

```
user query
  → search-strategist   queries → posts → all comments          /data/comments.jsonl, /data/posts.jsonl
  → clustering-analyst  stance axis → claims + stance → clusters /analysis/stance_axis.json, claims.jsonl, clusters.jsonl
  → narrative-analyst   labels + merges → community/connectivity /analysis/narratives.json, connectivity.json
  → reporter            briefing                                /report/briefing.json
  → UI                  reads the files above
```

Agents pass **file paths**, never raw data.

## The two clustering tasks

### Task 1: clustering-analyst

**Goal:** turn raw comments into clustered claims with an event-specific stance.

| Step | Tool | What it does | Judgment? |
|---|---|---|---|
| 1 | `preview_comments_tool` | ~40 top comments across subreddits | — |
| 2 | `set_stance_axis_tool` | Defines what −1 and +1 mean **for this event**, plus short UI labels | **Yes**: picks the question most comments take sides on |
| 3 | `extract_claims_tool` | One batched LLM pass per comment: neutral `claim`, `stance` (−1..1, or `null` if off-axis), `emotion`, `endorse_only` | — |
| 4 | `cluster_tool` | Embed claims → UMAP (8D) → HDBSCAN → 2D UMAP for the plot → c-TF-IDF keywords → quality report | **Yes**: retries with a new `min_cluster_size` if quality is bad (max 2 retries) |

Example stance axes:
- Anthropic IPO: "Is the IPO good for ordinary investors?" → *Bad for retail* ↔ *Good for retail*
- FAA closure: "Was the closure justified?" → *Overreach* ↔ *Necessary*
- Jensen Huang scandal: "Did he do something wrong?" → *Condemns* ↔ *Defends*

**Design choices**
- **Embed the claim, not the raw comment.** Generic embeddings group comments by topic. Rewriting each comment into its claim makes them group by *position*.
- **Sentiment is not clustered.** Stance is a separate per-comment score, so "agree on the story, feel differently" stays visible.
- **No amplification from replies.** Parent text is used only to *interpret* a reply and never goes into its embedding. Agree-only replies ("this") add a capped bonus to the parent's weight instead of counting as new voices. Each author counts roughly once per thread. A community × narrative cell can take at most 20% of its weight from one thread.
- **Upvotes** are a percentile rank within each thread (removes subreddit-size and post-age bias) and affect weight only, never the embedding.

**Quality checks** (returned by `cluster_tool`): noise share, largest cluster share, number of clusters, and per cluster: threads, subreddits, `localized` (more than 50% from one thread, or fewer than 3 threads).

### Task 2: narrative-analyst

**Goal:** name the narratives and build every table the UI needs.

| Step | Tool | What it does | Judgment? |
|---|---|---|---|
| 1 | `sample_cluster_tool` | Representative comments per cluster (near the centroid, high weight, max 3 per thread) | — |
| 2 | `set_narratives_tool` | Saves a neutral label (≤6 words) and a one-sentence summary per cluster; merges duplicates | **Yes**: labeling and merging |
| 3 | `connectivity_tool` | Builds all community and connectivity tables | — |

**Connectivity layers**
- **Narrative distance** (content): √(Jensen-Shannon divergence) between communities' narrative mixes → MDS layout. Nearby circles frame the event the same way.
- **Shared commenters** (connectivity): Jaccard overlap of authors per subreddit pair. Shown as undirected lines.
- **Reply network** (connectivity): narrative × narrative reply matrix plus an echo score (share of replies within the same narrative).
- **Crossposts** (connectivity): directed flows, counted separately in each direction.

**Key insight to show:** where content and connectivity disagree. Close together but no shared users means the communities reached the same narrative independently. Far apart with many shared users means the same people frame the event differently in different places.

## Output data shape

**`clusters.jsonl`**: one row per comment
```json
{"id": "c1", "body": "...", "claim": "...", "subreddit": "stocks", "author": "u130",
 "score": 42, "weight": 1.3, "narrative": 3, "x": 4.32, "y": 8.79, "is_noise": false,
 "stance": -0.6, "emotion": "resentment", "thread": "abc12", "parent_id": "t1_..."}
```

**`narratives.json`**
```json
[{"id": 3, "label": "Insiders win, retail loses", "summary": "...", "size": 155,
  "weighted_share": 0.14, "mean_stance": -0.5, "on_axis_share": 0.8, "top_emotion": "resentment",
  "n_threads": 24, "n_subreddits": 6}]
```

**`connectivity.json`**: `stance_axis`, `communities` (with MDS x/y), `shares`, `stance`, `js_distance`, `shared_commenters`, `reply_matrix_pct`, `echo_score`, `crosspost_flows`.

Join on `narrative` ↔ `id` and on `subreddit`. The x/y values are raw UMAP/MDS units, so rescale them in the frontend.

## Ground truth and evaluation

Fresh news events have no labeled data, so we combine several cheap checks, from most to least important.

### 1. Expert-consensus narrative recall (primary)
For each demo event:
1. Collect 3–5 trusted pieces of coverage (major outlets, explainers, op-eds from different sides).
2. Have an LLM list the distinct perspectives those articles report ("critics argue X, supporters say Y"). That list is the **reference narrative set**.
3. Match our narratives against it, using an LLM judge plus a human check:
   - **Recall:** share of reference narratives we found. *Did we miss a major side?*
   - **Novel narratives:** ours with no match. These are either Reddit-specific angles (interesting, so report them) or noise (check by reading samples).

### 2. Small hand-labeled set (optional)
Two teammates label the same 100 comments into the final narratives (~30 min).
- **Inter-annotator agreement** shows how ambiguous the task is.
- **Purity / adjusted Rand index** between the labels and our clusters gives the clustering accuracy.
- **Stance accuracy:** compare hand stance (−/0/+) with the model's stance.

### 3. Pew Research / polling (only where it exists)
Fresh events rarely have polls. Where a related one exists (e.g. public attitudes toward AI companies), compare direction only ("Reddit is more negative than the general public"). Never claim Reddit represents the public.

### 4. Internal checks (automatic, every run)
- **Stability:** adjusted Rand index between runs with different random seeds. Low means the clusters aren't trustworthy.
- **Coherence:** an LLM judge sees 5 comments from a cluster and answers "same claim? yes/no". Report % coherent.
- **Coverage:** noise share and `on_axis_share`.
- **Localization:** share of narratives that come from one thread.

### 5. Ablation (demo slide)
One event, four configurations:

| | Local embeddings (free) | OpenAI embeddings |
|---|---|---|
| Raw comment text | A | B |
| LLM claim | C | D |

Compare coherence, stability, noise, and expert recall. Expected result: claims (C, D) beat raw text (A, B), and the embedding model matters less, which supports the claim-rewrite design.

### Search-side metric (Kent)
recall@k per query, with an LLM judging whether each retrieved post is relevant to the event.

## Cost and latency (per event, ~3,000 comments)

| Step | Time | Cost |
|---|---|---|
| Reddit fetch | 30–90 s | free (rate-limited) |
| Claim + stance (batched, ~20 concurrent) | 20–40 s | ~$0.25 (gpt-4o-mini) |
| Embeddings | seconds | <$0.01 (OpenAI) or free (local) |
| UMAP + HDBSCAN | ~3 s warm (~14 s first run) | free |
| Labeling + agents | ~10 s | cents |

Target: about 1–2 minutes for a new event, instant for cached ones. **Precompute all demo events.**

## Open items
- [ ] Confirm `comments.jsonl` fields with Kent: `id, body, author, subreddit, score, parent_id, link_id, post_title`; posts need `crosspost_parent`
- [ ] Deep agents filesystem backend points to real disk so the Python tools can read `/data`
- [ ] Choose the agent model and API keys (deepagents defaults to Claude)
- [ ] First real run on one event; tune `min_cluster_size`
- [ ] Optional: Polymarket odds vs. community stance panel
