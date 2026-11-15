"""
tools/sophia_tools.py — clustering + narrative tools for the Reddit Narrative Agent.

Team-spec tools (call in this order):
  1. community_summary(path)            -> per subreddit {n_comments, total_score}
  2. score_stance(path, axis)           -> adds stance (0-1 or null), emotion, claim to each comment
  3. cluster_narratives(path, k=None)   -> writes data/clusters.json, returns per-cluster summaries

Extras (not in the spec yet; needed for labels + the connectivity view):
  preview_comments(path)                -> sample to help whoever writes the stance axis
  set_narratives(labels)                -> saves labels/summaries/merges into data/clusters.json
  build_connectivity(path)              -> data/connectivity.json (distances, shared commenters, replies)

Input format (data/comments.jsonl, one object per line):
  {id, post_id, parent_id, parent_body, subreddit, author, body, score, created_utc}
  parent_body == "" for top-level comments.

All tools return small summaries, never raw comment lists.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

BOTS = {"AutoModerator", "[deleted]", "VisualMod", "RemindMeBot", None, ""}
MIN_WORDS = 5
THREAD_CAP = 0.20          # max share of a (community, narrative) cell from one thread
AXIS_MIN_ON_AXIS = 0.40    # a cluster "has the axis" if > 40% of its comments take a stance on it
AXIS_MIN_CLUSTERS = 0.40   # the axis is good if > 40% of clusters have it
CHAT_MODEL = os.environ.get("CLAIM_MODEL", "gpt-4.1-mini")   # same family as Kent's JUDGE_MODEL
EMBED_BACKEND = os.environ.get("EMBED_BACKEND", "auto")   # auto | supabase | openai | local | tfidf
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
LOCAL_MODEL = os.environ.get("LOCAL_EMBED_MODEL", "all-MiniLM-L6-v2")


# ================================================================ paths
# Kent's config: WORKSPACE/data/comments.jsonl on disk, seen by agents as /data/comments.jsonl
# (deepagents FilesystemBackend(root_dir=WORKSPACE, virtual_mode=True)).
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scrapetools"))
    import config as _config
    DATA_DIR = _config.DATA_DIR
except Exception:          # running standalone
    _config = None
    DATA_DIR = Path(os.environ.get("WORKSPACE", "./workspace")).resolve() / "data"
DEFAULT_COMMENTS = "/data/comments.jsonl"


def _resolve(path) -> Path:
    """Map an agent-visible path (/data/x) to the real file; leave real paths alone."""
    p = str(path or DEFAULT_COMMENTS)
    if p.startswith("/data/"):
        return DATA_DIR / p[len("/data/"):]
    return Path(p)


# ================================================================ helpers
def _strip(x) -> str:
    return re.sub(r"^t\d_", "", str(x or ""))


def _read_jsonl(path) -> pd.DataFrame:
    return pd.read_json(_resolve(path), lines=True, dtype={"id": str, "post_id": str, "parent_id": str})


def _write_jsonl(df: pd.DataFrame, path):
    path = _resolve(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_json(path, orient="records", lines=True)


def _clusters_path(path) -> Path:
    return _resolve(path).parent / "clusters.json"


def _short(text, n=160) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def load_comments(path) -> pd.DataFrame:
    """Read + clean + add derived columns. Safe to call on raw or already-scored files."""
    df = _read_jsonl(path)
    # accept Kent's / the Supabase `comments` column names too; remember them so we write back his format
    renamed = {k: v for k, v in {"comment_id": "id", "author_hash": "author"}.items() if k in df}
    df = df.rename(columns=renamed)
    df.attrs["renamed"] = renamed
    df.attrs["orig_cols"] = [renamed.get(c, c) for c in pd.read_json(_resolve(path), lines=True, nrows=1).columns] \
        if _resolve(path).stat().st_size else list(df.columns)
    df["id"] = df["id"].astype(str)
    if "parent_type" in df:   # Supabase: parent_body holds the POST text for top-level comments
        df.loc[df["parent_type"] == "post", "parent_body"] = ""
    df["parent_id"] = df["parent_id"].fillna("").astype(str).replace({"None": "", "nan": ""})
    df["parent_body"] = df.get("parent_body", "").fillna("").replace("[not loaded]", "")
    df = df[~df["author"].isin(BOTS)]
    df = df[~df["body"].isin(["[deleted]", "[removed]"])].drop_duplicates("id")
    df["id"] = df["id"].map(_strip)
    df["thread"] = df["post_id"].map(_strip)
    df["parent"] = df["parent_id"].map(_strip)
    df["is_top_level"] = (df["parent_body"] == "") | (df["parent"] == df["thread"])
    df["n_words"] = df["body"].str.split().str.len()
    df["score_pct"] = df.groupby("thread")["score"].rank(pct=True)   # removes subreddit-size/age bias
    # controversy: many direct replies but low score within its thread
    n_replies = df["id"].map(df["parent"].value_counts()).fillna(0).astype(int)
    df["n_replies"] = np.maximum(n_replies, df["n_replies"].fillna(0).astype(int)) if "n_replies" in df else n_replies
    df["controversy"] = (np.log1p(df["n_replies"]) * (1 - df["score_pct"])).round(3)
    return df.reset_index(drop=True)


# ================================================================ 1. community_summary
def community_summary(path: str = DEFAULT_COMMENTS) -> dict:
    """Per subreddit: n_comments, total_score. Run first so the agent sees where the discussion is."""
    df = load_comments(path)
    g = df.groupby("subreddit").agg(n_comments=("id", "count"), total_score=("score", "sum"),
                                    n_threads=("thread", "nunique"), n_authors=("author", "nunique"))
    g = g.sort_values("n_comments", ascending=False)
    return {"path": path, "n_comments": len(df), "n_subreddits": len(g),
            "subreddits": {s: {k: int(v) for k, v in r.items()} for s, r in g.head(20).iterrows()}}


def preview_comments(path: str = DEFAULT_COMMENTS, n: int = 40) -> dict:
    """Top comments spread across subreddits, to help write the stance axis."""
    df = load_comments(path)
    df = df[df["n_words"] >= MIN_WORDS]
    per = max(2, n // max(1, df["subreddit"].nunique()))
    s = df.sort_values("score", ascending=False).groupby("subreddit").head(per).head(n)
    return {"samples": [{"subreddit": r.subreddit, "score": int(r.score), "comment": _short(r.body, 250)}
                        for r in s.itertuples()]}


# ================================================================ 2. score_stance
STANCE_PROMPT = """You analyze Reddit reactions to a news event.
Stance question: {question}
  0 = {pole0}
  1 = {pole1}
For EACH comment below, judge what THAT commenter asserts (use the parent only to interpret replies).
Return a JSON object {{"items": [...]}} with one item per comment, in the same order:
  "id": comment id
  "stance": number from 0 to 1 on the question above (0.5 = takes a position but mixed);
            null if the comment does not address the question (jokes, side topics, questions)
  "emotion": one word (anger, excitement, fear, humor, skepticism, sympathy, neutral, ...)
  "claim": one neutral sentence stating what the comment itself says or argues, as a plain statement
           (e.g. "The director's stock sale was pre-scheduled and legal."). Write the claim for EVERY comment,
           including jokes and off-topic ones (e.g. "Joke that a fourth mistress leaked the affair.").
           NEVER mention the stance question, whether the comment addresses it, or the words "the commenter"
           / "this comment". If the comment only agrees with its parent ("this", "exactly"), restate the
           parent's claim.
  "endorse_only": true if it adds nothing beyond agreeing with the parent

Comments:
{block}"""


def _block(rows):
    return "\n".join(json.dumps({"id": r["id"], "subreddit": r["subreddit"],
                                 "parent": _short(r["parent_body"], 300), "comment": _short(r["body"], 800)})
                     for r in rows)


async def _stance_batch(client, sem, rows, axis):
    prompt = STANCE_PROMPT.format(question=axis["question"], pole0=axis["0"], pole1=axis["1"], block=_block(rows))
    async with sem:
        for attempt in range(3):
            try:
                r = await client.chat.completions.create(
                    model=CHAT_MODEL, temperature=0, response_format={"type": "json_object"},
                    messages=[{"role": "user", "content": prompt}])
                return json.loads(r.choices[0].message.content)["items"]
            except Exception:
                await asyncio.sleep(1.5 * (attempt + 1))
    return [{"id": r["id"], "stance": None, "emotion": "unknown", "claim": None, "endorse_only": False} for r in rows]


def score_stance(path: str, axis: dict, batch_size: int = 25, concurrency: int = 20,
                 fast_mode: bool = False) -> dict:
    """Adds stance (0-1, null = off-axis), emotion, claim, endorse_only to every comment in `path`
    (rewrites the file in place). axis = {"question": "...", "0": "condemns", "1": "defends"}.
    One batched LLM pass. fast_mode=True skips the LLM (no stance, claim = raw text)."""
    df = load_comments(path)
    if fast_mode:
        res = pd.DataFrame({"id": df["id"], "stance": np.nan, "emotion": "unknown",
                            "claim": df["body"].map(lambda t: _short(t, 400)),
                            "endorse_only": df["n_words"] < MIN_WORDS})
    else:
        from openai import AsyncOpenAI
        client, sem = AsyncOpenAI(), asyncio.Semaphore(concurrency)
        rows = df.to_dict("records")
        batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]

        async def run():
            return await asyncio.gather(*[_stance_batch(client, sem, b, axis) for b in batches])
        res = pd.DataFrame([it for b in asyncio.run(run()) for it in b])
        res["id"] = res["id"].astype(str).map(_strip)

    renamed, orig_cols = df.attrs.get("renamed", {}), df.attrs.get("orig_cols", [])
    drop = [c for c in ["stance", "emotion", "claim", "endorse_only"] if c in df]
    out = df.drop(columns=drop).merge(res, on="id", how="left")
    out["stance"] = pd.to_numeric(out["stance"], errors="coerce").clip(0, 1)
    out["claim"] = out["claim"].fillna(out["body"].map(lambda t: _short(t, 400)))
    out["claim"] = [_clean_claim(c, b) for c, b in zip(out["claim"], out["body"])]
    out["endorse_only"] = out["endorse_only"].fillna(False).astype(bool)
    out["emotion"] = out["emotion"].fillna("unknown")
    # write back the ORIGINAL columns (Kent's format, untouched values) + the 4 new ones
    raw = _read_jsonl(path).rename(columns=renamed)
    raw["id"] = raw["id"].astype(str).map(_strip)
    new = out[["id", "stance", "emotion", "claim", "endorse_only"]]
    base_cols = [c for c in (orig_cols or raw.columns) if c in raw and c not in new.columns.drop("id")]
    merged = raw[base_cols].merge(new, on="id", how="left").rename(columns={v: k for k, v in renamed.items()})
    _write_jsonl(merged, path)
    _resolve(path).with_name("stance_axis.json").write_text(json.dumps(axis, indent=2))

    by_sub = out.groupby("subreddit")["stance"].agg(["mean", "count"]).sort_values("count", ascending=False)
    return {"path": path, "axis": axis, "n_comments": len(out),
            "n_on_axis": int(out["stance"].notna().sum()),
            "avg_stance_by_subreddit": {s: (None if pd.isna(r["mean"]) else round(float(r["mean"]), 2))
                                        for s, r in by_sub.head(15).iterrows()}}


_BOILER = re.compile(r"^(the |this )?(commenter|comment|user|author|poster)\s+(\w+\s+){0,2}?"
                     r"(that|states|claims|believes|asserts|argues|says|suggests|questions|thinks|discusses|mentions|notes|"
                     r"expresses|points out|wonders|asks|jokes|criticizes|implies|speculates)\s*(that\s+|about\s+)?",
                     re.I)
_AXIS_LEAK = re.compile(r"\b(does not|doesn't|without) (directly )?address(ing)?\b.*$", re.I)


def _clean_claim(c, body=""):
    c = str(c or "").strip()
    c = _BOILER.sub("", c)
    c = _AXIS_LEAK.sub("", c).strip(" ,;.")
    if len(c.split()) < 3 or c.lower() in ("this comment", "the comment", "the commenter"):
        return _short(body, 300)            # nothing left: fall back to the raw comment
    return c[:1].upper() + c[1:] + "."


# ================================================================ weights
def compute_weights(df: pd.DataFrame) -> pd.Series:
    """Upvote percentile + capped bonus from agree-only replies; ~1 vote per author per thread.
    Used for sizes/shares/sampling only, never for embeddings or clustering."""
    w = 0.5 + df["score_pct"].fillna(0.5)
    endorsed = df[df["endorse_only"]].groupby("parent").size()
    w = w + np.minimum(np.log1p(df["id"].map(endorsed).fillna(0)), 2.0)
    per_author = df.groupby(["thread", "author"])["id"].transform("count")
    return (w / np.sqrt(per_author)).round(3)


# ================================================================ 3. cluster_narratives
def _stored_embeddings(ids: list[str]) -> np.ndarray | None:
    """Kent's embed.py writes 384-d all-MiniLM-L6-v2 vectors (of the RAW body) to comments.embedding."""
    if _config is None:
        return None
    try:
        sb, got = _config.supabase(), {}
        for i in range(0, len(ids), 200):
            rows = sb.table("comments").select("comment_id,embedding").in_("comment_id", ids[i:i + 200]).execute().data
            for r in rows:
                if r.get("embedding"):
                    v = r["embedding"]
                    got[r["comment_id"]] = json.loads(v) if isinstance(v, str) else v
        if len(got) < 0.95 * len(ids):
            return None
        dim = len(next(iter(got.values())))
        return np.array([got.get(i, [0.0] * dim) for i in ids], dtype=np.float32)
    except Exception:
        return None


def _embed(texts, cache: Path, ids: list[str] | None = None, claims_rewritten: bool = False) -> tuple[np.ndarray, str]:
    if cache.exists():
        arr = np.load(cache)
        if len(arr) == len(texts):
            return arr, "cache"
    backend = EMBED_BACKEND
    # claims were not rewritten (fast mode / no LLM) -> Kent's stored raw-body vectors are equivalent and free
    if backend in ("auto", "supabase") and ids is not None and (backend == "supabase" or not claims_rewritten):
        arr = _stored_embeddings(ids)
        if arr is not None:
            np.save(cache, arr)
            return arr, "supabase-stored"
    if backend == "auto":
        if os.environ.get("OPENAI_API_KEY"):
            backend = "openai"
        else:
            try:
                import sentence_transformers  # noqa: F401
                backend = "local"
            except ImportError:
                backend = "tfidf"
    if backend == "openai":
        from openai import OpenAI
        client, vecs = OpenAI(), []
        for i in range(0, len(texts), 1000):
            vecs += [d.embedding for d in client.embeddings.create(model=EMBED_MODEL, input=texts[i:i + 1000]).data]
        arr = np.array(vecs, dtype=np.float32)
    elif backend == "local":
        from sentence_transformers import SentenceTransformer
        arr = SentenceTransformer(LOCAL_MODEL).encode(texts, batch_size=64, normalize_embeddings=True)
    else:  # offline test fallback
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        X = TfidfVectorizer(stop_words="english", min_df=2, ngram_range=(1, 2)).fit_transform(texts)
        arr = TruncatedSVD(n_components=min(100, X.shape[1] - 1), random_state=0).fit_transform(X)
    np.save(cache, arr)
    return arr, backend


def _keywords(texts: pd.Series, labels: np.ndarray, k=8) -> dict:
    from sklearn.feature_extraction.text import CountVectorizer
    docs = texts.groupby(labels).apply(" ".join)
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    stop = list(ENGLISH_STOP_WORDS | {"commenter", "comment", "comments", "address", "addresses", "does",
                                      "jensen", "huang", "suggests", "states", "claims", "believes", "argues"})
    cv = CountVectorizer(stop_words=stop, ngram_range=(1, 2))
    X = cv.fit_transform(docs).toarray().astype(float)
    tf = X / X.sum(1, keepdims=True).clip(1)
    idf = np.log(1 + X.sum(1).mean() / X.sum(0).clip(1))
    vocab = np.array(cv.get_feature_names_out())
    return {int(c): vocab[np.argsort(-(tf[i] * idf))[:k]].tolist() for i, c in enumerate(docs.index)}


def _score_option(labels, n):
    """Rule-based quality: prefer 5-12 clusters, low noise, no giant cluster."""
    ids = [c for c in set(labels) if c >= 0]
    if not ids:
        return -1.0, {"n_clusters": 0, "noise": 1.0, "largest": 1.0}
    noise = float((labels == -1).mean())
    largest = float(max((labels == c).sum() for c in ids) / n)
    k = len(ids)
    s = 1.0 - noise - max(0, largest - 0.35) - (0 if 5 <= k <= 12 else 0.08 * min(abs(k - 5), abs(k - 12)))
    return round(s, 3), {"n_clusters": k, "noise": round(noise, 2), "largest": round(largest, 2)}


def cluster_narratives(path: str = DEFAULT_COMMENTS, k: int | None = None, seed: int = 42) -> dict:
    """Cluster ALL kept comments together (not per subreddit), on their claims.
    k=None: HDBSCAN, sweeping 4 min_cluster_size values and keeping the best.
    k=int : force exactly k clusters (agglomerative on the reduced space).
    Writes data/clusters.json; returns per-cluster summaries."""
    import umap
    from sklearn.cluster import HDBSCAN, AgglomerativeClustering

    df = load_comments(path)
    if "claim" not in df:
        df["claim"], df["stance"], df["emotion"], df["endorse_only"] = df["body"], np.nan, "unknown", df["n_words"] < MIN_WORDS
    df["endorse_only"] = df["endorse_only"].fillna(False).astype(bool)
    df["weight"] = compute_weights(df)
    work = df[~df["endorse_only"] & (df["n_words"] >= 3)].reset_index(drop=True)
    n = len(work)
    cpath = _clusters_path(path)
    rewritten = bool((work["claim"] != work["body"].map(lambda t: _short(t, 400))).mean() > 0.5)
    inline = work["embedding"] if "embedding" in work else None
    if inline is not None and not rewritten and inline.notna().all():
        E = np.array([json.loads(v) if isinstance(v, str) else v for v in inline], dtype=np.float32)
        backend = "inline-stored (all-MiniLM-L6-v2, raw body)"
    else:
      E, backend = _embed(work["claim"].tolist(), cpath.with_suffix(".emb.npy"),
                        ids=work["id"].tolist(), claims_rewritten=rewritten)
    work = work.drop(columns=["embedding"], errors="ignore")

    zcache = cpath.with_suffix(".umap8.npy")
    if zcache.exists() and len(np.load(zcache)) == n:
        Z = np.load(zcache)
    else:
        Z = umap.UMAP(n_components=8, n_neighbors=15, min_dist=0.0, metric="cosine", random_state=seed).fit_transform(E)
        np.save(zcache, Z)

    sweep = []
    if k:
        labels = AgglomerativeClustering(n_clusters=int(k), linkage="ward").fit_predict(Z)
        chosen = {"method": "agglomerative", "k": int(k)}
    else:
        best = None
        for m in sorted({max(10, n // d) for d in (80, 40, 20, 10)}):
            lab = HDBSCAN(min_cluster_size=m, min_samples=max(3, m // 3)).fit_predict(Z)
            score, info = _score_option(lab, n)
            sweep.append({"min_cluster_size": m, "score": score, **info})
            if best is None or score > best[0]:
                best = (score, m, lab)
        labels, chosen = best[2], {"method": "hdbscan", "min_cluster_size": best[1]}

    work["is_noise"] = labels == -1
    if (labels == -1).any() and (labels >= 0).any():          # attach noise to nearest cluster, keep flag
        ids = sorted(c for c in set(labels) if c >= 0)
        C = np.stack([Z[labels == c].mean(0) for c in ids])
        labels = labels.copy()
        labels[labels == -1] = np.array(ids)[np.argmin(((Z[labels == -1, None] - C[None]) ** 2).sum(-1), 1)]
    # renumber clusters by size: 0 = largest
    order = {c: i for i, c in enumerate(pd.Series(labels).value_counts().index)}
    work["cluster"] = [order[c] for c in labels]

    xy = umap.UMAP(n_components=2, n_neighbors=15, min_dist=0.1, metric="cosine", random_state=seed).fit_transform(E)
    work["x"], work["y"] = xy[:, 0].round(3), xy[:, 1].round(3)

    kw = _keywords(work["claim"], work["cluster"].to_numpy())
    summaries = []
    for c, g in work.groupby("cluster"):
        top_thread = g["thread"].value_counts(normalize=True).iloc[0]
        top = g.sort_values("score", ascending=False).head(10)
        contested = g[g["n_replies"] >= 2].sort_values("controversy", ascending=False).head(3)
        summaries.append({
            "cluster": int(c), "size": len(g), "weighted_size": round(float(g["weight"].sum()), 1),
            "subreddit_counts": g["subreddit"].value_counts().head(8).to_dict(),
            "keywords": kw[int(c)],
            "mean_stance": None if g["stance"].isna().all() else round(float(g["stance"].mean()), 2),
            "on_axis_share": round(float(g["stance"].notna().mean()), 2),
            "n_threads": int(g["thread"].nunique()),
            "localized": bool(work["thread"].nunique() >= 4 and (top_thread > 0.5 or g["thread"].nunique() < 3)),
            "top_comments": [{"id": r.id, "subreddit": r.subreddit, "score": int(r.score), "text": _short(r.body)}
                             for r in top.itertuples()],
            "most_contested": [{"id": r.id, "replies": int(r.n_replies), "score": int(r.score), "text": _short(r.body)}
                               for r in contested.itertuples()],
        })

    axis_check = check_axis(summaries)
    cols = ["id", "post_id", "parent_id", "subreddit", "author", "body", "claim", "score", "weight",
            "n_replies", "controversy", "stance", "emotion", "cluster", "is_noise", "x", "y", "created_utc"]
    out = {"params": {**chosen, "embed_backend": backend, "n_clustered": n,
                      "n_endorse_folded": int(df["endorse_only"].sum())},
           "sweep": sweep, "axis_check": axis_check,
           "clusters": [{k2: v for k2, v in s.items() if k2 not in ("top_comments", "most_contested")} for s in summaries],
           "comments": json.loads(work[[c for c in cols if c in work]].to_json(orient="records"))}
    cpath.write_text(json.dumps(out))
    np.save(cpath.with_suffix(".final8.npy"), Z)
    return {"path": str(cpath), **out["params"], "sweep": sweep, "axis_check": axis_check, "clusters": summaries}


def check_axis(cluster_summaries: list[dict], min_on_axis: float = AXIS_MIN_ON_AXIS,
               min_clusters: float = AXIS_MIN_CLUSTERS) -> dict:
    """Is the stance axis about what people actually argue? OK when more than `min_clusters` of the
    clusters have more than `min_on_axis` of their comments taking a stance (stance not null)."""
    shares = [c.get("on_axis_share", 0) or 0 for c in cluster_summaries]
    if not shares or max(shares) == 0:
        return {"ok": None, "reason": "no stance scores yet (fast mode or score_stance not run)"}
    with_axis = [c["cluster"] for c, sh in zip(cluster_summaries, shares) if sh > min_on_axis]
    frac = len(with_axis) / len(shares)
    ok = frac > min_clusters
    return {"ok": ok, "clusters_with_axis": with_axis, "fraction": round(frac, 2),
            "rule": f"> {int(min_clusters * 100)}% of clusters need > {int(min_on_axis * 100)}% of comments on the axis",
            "advice": None if ok else ("The axis misses most narratives. Look at the off-axis clusters' top comments, "
                                       "write a new axis they take sides on, call score_stance_tool again, then "
                                       "cluster_narratives_tool again. Do this at most once.")}


# ================================================================ extras: labels + connectivity
def set_narratives(narratives: list[dict], path: str = DEFAULT_COMMENTS) -> dict:
    """narratives = [{"cluster": 0, "label": "...", "summary": "...", "merge_into": null|int}, ...].
    Writes label/summary into clusters.json and adds a `narrative` id to each comment (after merges)."""
    cpath = _clusters_path(path)
    data = json.loads(cpath.read_text())
    remap = {n["cluster"]: n["merge_into"] for n in narratives if n.get("merge_into") is not None}
    meta = {n["cluster"]: n for n in narratives if n.get("merge_into") is None}
    for c in data["comments"]:
        c["narrative"] = remap.get(c["cluster"], c["cluster"])
    cm = pd.DataFrame(data["comments"])
    tot = cm["weight"].sum()
    data["narratives"] = []
    for nid, g in cm.groupby("narrative"):
        m = meta.get(nid, {"label": f"cluster {nid}", "summary": ""})
        data["narratives"].append({
            "id": int(nid), "label": m["label"], "summary": m.get("summary", ""), "size": len(g),
            "weighted_share": round(float(g["weight"].sum() / tot), 3),
            "mean_stance": None if g["stance"].isna().all() else round(float(g["stance"].mean()), 2),
            "on_axis_share": round(float(g["stance"].notna().mean()), 2),
            "mean_controversy": round(float(g["controversy"].mean()), 3),
            "n_threads": int(g["post_id"].nunique()), "n_subreddits": int(g["subreddit"].nunique())})
    cpath.write_text(json.dumps(data))
    return {"path": str(cpath), "narratives": [(n["id"], n["label"]) for n in data["narratives"]]}


def _js(p, q):
    p, q = np.asarray(p, float) + 1e-12, np.asarray(q, float) + 1e-12
    p, q = p / p.sum(), q / q.sum()
    m = (p + q) / 2
    return float(((p * np.log2(p / m)).sum() + (q * np.log2(q / m)).sum()) / 2)


def _mds(D):
    n = len(D)
    J = np.eye(n) - 1 / n
    vals, vecs = np.linalg.eigh(-0.5 * J @ (D ** 2) @ J)
    idx = np.argsort(vals)[::-1][:2]
    return vecs[:, idx] * np.sqrt(np.clip(vals[idx], 0, None))


def _unit_tables(d: pd.DataFrame, unit: str, units: list, col: str, narrs: list):
    """Thread-capped narrative shares, mean stance, sqrt-JS distance and MDS layout for any unit
    (subreddit or community group)."""
    cell = d.groupby([unit, col, "thread"])["weight"].sum().reset_index()
    grp = cell.groupby([unit, col])
    tot, nthr = grp["weight"].transform("sum"), grp["thread"].transform("count")
    cap = np.maximum(THREAD_CAP, 1.0 / nthr)          # with few threads, an equal split is the floor
    cell["capped"] = np.minimum(cell["weight"], cap * tot)
    shares = cell.groupby([unit, col])["capped"].sum().unstack(fill_value=0).reindex(index=units, columns=narrs, fill_value=0)
    shares = shares.div(shares.sum(1), axis=0).fillna(0)
    stance = d.groupby([unit, col])["stance"].mean().unstack().reindex(index=units, columns=narrs)
    D = np.array([[np.sqrt(_js(shares.loc[a], shares.loc[b])) for b in units] for a in units])
    xy = _mds(D) if len(units) > 2 else np.zeros((len(units), 2))
    return shares, stance, D, xy


def _jaccard_matrix(users: dict, units: list) -> np.ndarray:
    J = np.eye(len(units))
    for i, a in enumerate(units):
        for j in range(i + 1, len(units)):
            A, B = users.get(a, set()), users.get(units[j], set())
            J[i, j] = J[j, i] = len(A & B) / len(A | B) if (A or B) else 0.0
    return J


def build_connectivity(path: str = DEFAULT_COMMENTS, top_k: int = 8, min_comments: int = 50,
                       group_by: str = "people", group_threshold: float | None = None,
                       min_group_comments: int = 10) -> dict:
    """Community x narrative shares (thread-capped), stance, sqrt-JS distance + MDS layout,
    shared commenters (Jaccard), narrative x narrative reply matrix, and COMMUNITY GROUPS
    -> data/connectivity.json.

    Community groups merge near-identical subreddits (hierarchical clustering, average linkage):
      group_by="people"   distance = 1 - Jaccard(shared commenters)          [recommended]
      group_by="both"     distance = mean(1 - Jaccard, sqrt-JS narrative distance)
    Small subreddits (>= min_group_comments) are kept inside groups instead of being dropped."""
    from sklearn.cluster import AgglomerativeClustering

    cpath = _clusters_path(path)
    data = json.loads(cpath.read_text())
    cm = pd.DataFrame(data["comments"])
    cm["thread"] = cm["post_id"].map(_strip)
    col = "narrative" if "narrative" in cm else "cluster"
    narrs = sorted(cm[col].unique().tolist())
    counts = cm["subreddit"].value_counts()
    raw = load_comments(path)                                   # all authors, incl. agree-only repliers
    users_all = raw.groupby("subreddit")["author"].apply(set).to_dict()
    sd = lambda v: None if pd.isna(v) else round(float(v), 2)

    # ---------- per-subreddit view (top_k biggest)
    subs = counts[counts >= min_comments].head(top_k).index.tolist() or counts.head(top_k).index.tolist()
    d = cm[cm["subreddit"].isin(subs)]
    shares, stance, D, xy = _unit_tables(d, "subreddit", subs, col, narrs)
    Js = _jaccard_matrix(users_all, subs)
    overlap = [{"a": a, "b": b, "shared": len(users_all.get(a, set()) & users_all.get(b, set())),
                "jaccard": round(float(Js[i, j]), 3)}
               for i, a in enumerate(subs) for j, b in enumerate(subs) if i < j and Js[i, j] > 0]

    # ---------- community groups (all subreddits with >= min_group_comments)
    gsubs = counts[counts >= min_group_comments].index.tolist()
    if len(gsubs) >= 3:
        Jg = _jaccard_matrix(users_all, gsubs)
        dist = 1 - Jg
        if group_by == "both":
            gd = cm[cm["subreddit"].isin(gsubs)]
            _, _, Dn, _ = _unit_tables(gd, "subreddit", gsubs, col, narrs)
            dist = (dist + Dn) / 2
        np.fill_diagonal(dist, 0)
        # default threshold: merge only clearly overlapping subreddits
        thr = group_threshold if group_threshold is not None else (0.85 if group_by == "people" else 0.6)
        lab = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                      distance_threshold=thr).fit_predict(dist)
    else:
        lab = np.arange(len(gsubs))
    sub2grp = dict(zip(gsubs, lab))
    # order groups by size; label = biggest member (+N); agent can rename via set_group_labels
    gsize = pd.Series({g: counts[[s for s in gsubs if sub2grp[s] == g]].sum() for g in set(lab)}).sort_values(ascending=False)
    gid = {g: i for i, g in enumerate(gsize.index)}
    groups = []
    for g in gsize.index:
        members = sorted([s for s in gsubs if sub2grp[s] == g], key=lambda s: -counts[s])
        groups.append({"id": gid[g], "members": members, "n_comments": int(gsize[g]),
                       "label": members[0] + (f" +{len(members) - 1}" if len(members) > 1 else "")})
    cm["group"] = cm["subreddit"].map(lambda s: gid.get(sub2grp.get(s), -1) if s in sub2grp else -1)
    gd = cm[cm["group"] >= 0]
    gunits = [g["id"] for g in groups][:max(top_k, 8)]
    gd = gd[gd["group"].isin(gunits)]
    gshares, gstance, gD, gxy = _unit_tables(gd, "group", gunits, col, narrs)
    gusers = {g["id"]: set().union(*[users_all.get(s, set()) for s in g["members"]]) for g in groups}
    Jgg = _jaccard_matrix(gusers, gunits)
    for i, g in enumerate(groups[:len(gunits)]):
        g.update({"x": round(float(gxy[i, 0]), 4), "y": round(float(gxy[i, 1]), 4),
                  "mean_stance": sd(gd[gd["group"] == g["id"]]["stance"].mean()),
                  "shares": {int(k): round(float(v), 3) for k, v in gshares.loc[g["id"]].items()}})

    # ---------- reply matrix
    nmap = dict(zip(cm["id"], cm[col]))
    cm["parent_narr"] = cm["parent_id"].map(lambda p: nmap.get(_strip(p)))
    rp = cm.dropna(subset=["parent_narr"])
    rm = pd.crosstab(rp[col], rp["parent_narr"].astype(int)).reindex(index=narrs, columns=narrs, fill_value=0)
    rm_pct = (rm.div(rm.sum(1).replace(0, np.nan), axis=0) * 100).round(1).fillna(0)

    axis_file = _resolve(path).with_name("stance_axis.json")
    out = {
        "stance_axis": json.loads(axis_file.read_text()) if axis_file.exists() else None,
        "communities": [{"subreddit": s, "n_comments": int(counts[s]), "group": gid.get(sub2grp.get(s), None),
                         "x": round(float(xy[i, 0]), 4), "y": round(float(xy[i, 1]), 4),
                         "mean_stance": sd(d[d.subreddit == s]["stance"].mean())} for i, s in enumerate(subs)],
        "narratives": narrs,
        "shares": {s: {int(k): round(float(v), 3) for k, v in shares.loc[s].items()} for s in subs},
        "stance": {s: {int(k): sd(v) for k, v in stance.loc[s].items()} for s in subs},
        "js_distance": {f"{a}|{b}": round(float(D[i, j]), 3) for i, a in enumerate(subs) for j, b in enumerate(subs) if i < j},
        "shared_commenters": overlap,
        "community_groups": {"group_by": group_by, "groups": groups,
                             "shared_commenters": [{"a": a, "b": b, "jaccard": round(float(Jgg[i, j]), 3)}
                                                   for i, a in enumerate(gunits) for j, b in enumerate(gunits)
                                                   if i < j and Jgg[i, j] > 0],
                             "js_distance": {f"{a}|{b}": round(float(gD[i, j]), 3) for i, a in enumerate(gunits)
                                             for j, b in enumerate(gunits) if i < j}},
        "reply_matrix_pct": {int(r): {int(c): float(v) for c, v in rm_pct.loc[r].items()} for r in rm_pct.index},
        "echo_score": {int(r): float(rm_pct.loc[r, r]) for r in rm_pct.index},
    }
    opath = _resolve(path).with_name("connectivity.json")
    opath.write_text(json.dumps(out, indent=2))
    return {"path": str(opath), "communities": subs, "n_overlap_edges": len(overlap),
            "groups": [{"id": g["id"], "members": g["members"][:8], "n_comments": g["n_comments"]} for g in groups],
            "n_reply_links": int(rm.values.sum())}


def set_group_labels(labels: dict, path: str = DEFAULT_COMMENTS) -> dict:
    """labels = {group_id: "Retail investors", ...}. Renames community groups in connectivity.json."""
    opath = _resolve(path).with_name("connectivity.json")
    data = json.loads(opath.read_text())
    for g in data["community_groups"]["groups"]:
        if str(g["id"]) in {str(k) for k in labels}:
            g["label"] = labels.get(g["id"], labels.get(str(g["id"])))
    opath.write_text(json.dumps(data, indent=2))
    return {"groups": [(g["id"], g["label"], g["members"][:5]) for g in data["community_groups"]["groups"]]}


# ================================================================ LangChain wrappers
def make_tools():
    from langchain_core.tools import tool

    @tool
    def community_summary_tool(path: str = DEFAULT_COMMENTS) -> str:
        """Step 1. Per-subreddit comment counts and total score. Call first."""
        return json.dumps(community_summary(path))

    @tool
    def preview_comments_tool(path: str = DEFAULT_COMMENTS) -> str:
        """~40 top comments across subreddits. Read before writing the stance axis."""
        return json.dumps(preview_comments(path))

    @tool
    def score_stance_tool(axis: dict, path: str = DEFAULT_COMMENTS) -> str:
        """Step 2. axis = {"question": "...", "0": "<one end>", "1": "<other end>"}, written for THIS event
        (e.g. {"question": "Did Jensen Huang do something wrong?", "0": "condemns", "1": "defends"}).
        Adds stance (0-1, null if off-topic), emotion, claim to every comment. Returns avg stance per subreddit."""
        return json.dumps(score_stance(path, axis))

    @tool
    def cluster_narratives_tool(path: str = DEFAULT_COMMENTS, k: int | None = None) -> str:
        """Step 3. Clusters ALL comments together into narratives. Leave k empty (auto: tries 4 granularities,
        keeps the best). Only set k if the auto result is clearly too coarse or fine. Returns per-cluster
        size, top 10 comments, subreddit counts, keywords, most-contested comments."""
        return json.dumps(cluster_narratives(path, k))

    @tool
    def set_narratives_tool(narratives: list[dict], path: str = DEFAULT_COMMENTS) -> str:
        """Save labels. Each item: {"cluster": int, "label": "<=6 words", "summary": "one sentence",
        "merge_into": int or null}. Use merge_into for clusters that make the same claim."""
        return json.dumps(set_narratives(narratives, path))

    @tool
    def connectivity_tool(path: str = DEFAULT_COMMENTS) -> str:
        """Build community distances, shared commenters, community groups (near-identical subreddits merged
        by shared commenters) and the reply matrix. Call after set_narratives_tool."""
        return json.dumps(build_connectivity(path))

    @tool
    def set_group_labels_tool(labels: dict, path: str = DEFAULT_COMMENTS) -> str:
        """Name the community groups from connectivity_tool, e.g. {0: "Retail investors", 1: "AI enthusiasts"}.
        Name each group by what its member subreddits have in common (2-3 words)."""
        return json.dumps(set_group_labels(labels, path))

    return [community_summary_tool, preview_comments_tool, score_stance_tool,
            cluster_narratives_tool, set_narratives_tool, connectivity_tool, set_group_labels_tool]


def subagent_specs():
    """For create_deep_agent(subagents=[...]). Prompt key is "system_prompt" (older deepagents: "prompt")."""
    t = {f.name: f for f in make_tools()}
    return [
        {"name": "clustering-analyst",
         "description": "Scores stance and clusters data/comments.jsonl into narratives. Call after fetch_threads.",
         "system_prompt": (
             "You analyze Reddit reactions to one news event.\n"
             "1. Call community_summary_tool.\n"
             "2. If no stance axis was given, call preview_comments_tool and write one: the single question most\n"
             "   comments take a side on, with short labels for each end. Use positions real comments argue,\n"
             "   not generic positive/negative. Call score_stance_tool once.\n"
             "3. Call cluster_narratives_tool with k empty. Only rerun with k if the result is clearly unusable\n"
             "   (fewer than 3 or more than 15 clusters). At most one rerun.\n"
             "   If axis_check.ok is false (40% rule), follow its advice: write ONE new axis from the off-axis\n"
             "   clusters' comments, call score_stance_tool and cluster_narratives_tool again. Only once.\n"
             "4. Report briefly: axis, n_on_axis, number of clusters, which are localized. Do not label clusters."),
         "tools": [t["community_summary_tool"], t["preview_comments_tool"], t["score_stance_tool"], t["cluster_narratives_tool"]]},
        {"name": "narrative-analyst",
         "description": "Labels narrative clusters, merges duplicates, builds connectivity tables.",
         "system_prompt": (
             "You name the narratives in clustered Reddit comments, using the cluster summaries you are given\n"
             "(top comments, keywords, subreddits).\n"
             "1. For each cluster write a neutral label (<=6 words) and a one-sentence summary of the shared claim.\n"
             "   Describe the position; do not judge it.\n"
             "2. If two clusters make the same claim, merge the smaller into the larger.\n"
             "3. Call set_narratives_tool once with all clusters, then connectivity_tool.\n"
             "   Then name each multi-member community group with set_group_labels_tool (2-3 words).\n"
             "4. Report the narratives, flag localized ones (mostly one thread), and note any cluster whose\n"
             "   most-contested comments show a real disagreement."),
         "tools": [t["set_narratives_tool"], t["connectivity_tool"], t["set_group_labels_tool"]]},
    ]


# ================================================================ archive + cleanup (NOT an agent tool)
def archive_and_cleanup(event_id: int, path: str = DEFAULT_COMMENTS, delete_comments: bool = True,
                        sb=None) -> dict:
    """Run AFTER the agents finish. Destructive, so it is plain Python, never an LLM-callable tool.

    1. Checks clusters.json + connectivity.json exist and cover the comments.
    2. Saves both into Supabase `narrative_runs` (one row per run) so the UI no longer needs `comments`.
       clusters.json already carries every comment's body/score/etc. for the click-through.
    3. Deletes this event's rows from `comments` (incl. embeddings) and marks its posts
       comments_fetched=false in every event that shares them. `posts`/`events` are kept (small).
    Refuses to delete if step 1 or 2 fails."""
    cdir = _resolve(path).parent
    cfile, kfile = cdir / "clusters.json", cdir / "connectivity.json"
    if not (cfile.exists() and kfile.exists()):
        return {"ok": False, "reason": "outputs missing; run cluster_narratives + build_connectivity first"}
    clusters, conn = json.loads(cfile.read_text()), json.loads(kfile.read_text())
    n_out = len(clusters.get("comments", []))
    if n_out == 0 or "narratives" not in clusters:
        return {"ok": False, "reason": "clusters.json has no comments or no narrative labels yet"}

    if sb is None:
        if _config is None:
            return {"ok": False, "reason": "no Supabase client (scrapetools/config.py not importable)"}
        sb = _config.supabase()

    # 2. archive (must succeed before anything is deleted)
    try:
        run = sb.table("narrative_runs").insert({"event_id": event_id, "n_comments": n_out,
                                                 "clusters": clusters, "connectivity": conn}).execute().data
        run_id = run[0]["run_id"] if run else None
    except Exception as e:
        return {"ok": False, "reason": f"could not save to narrative_runs ({e}); nothing deleted. "
                                       "Run narrative/schema_narrative_runs.sql once in the SQL editor."}
    if not delete_comments:
        return {"ok": True, "run_id": run_id, "deleted": 0}

    # 3. delete this event's comments
    pids = [r["post_id"] for r in sb.table("event_posts").select("post_id")
            .eq("event_id", event_id).eq("comments_fetched", True).execute().data]
    deleted = 0
    for i in range(0, len(pids), 20):
        res = sb.table("comments").delete(count="exact").in_("post_id", pids[i:i + 20]).execute()
        deleted += res.count or 0
        sb.table("event_posts").update({"comments_fetched": False}).in_("post_id", pids[i:i + 20]).execute()
    return {"ok": True, "run_id": run_id, "archived_comments": n_out, "deleted_comments": deleted,
            "posts_cleared": len(pids)}
