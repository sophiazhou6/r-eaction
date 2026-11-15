"""Small read-only tools that connect the subagents.

Sophia's clusters.json holds every comment (thousands of rows), so no agent should
read_file it. These tools hand agents a SMALL view of it instead:
  cluster_summaries -> narrative-analyst (what each cluster says, to label it)
  report_context    -> reporter (everything needed to write the briefing)
"""
import json
from collections import Counter

from narrative.sophia_tools import DEFAULT_COMMENTS, _clusters_path, _resolve, _short


def _split(rows) -> dict | None:
    """Stance split for a set of comments: share at each end of the axis (0 vs 1).
    Means hide polarization (half 0 + half 1 averages to 0.5), so always report this."""
    v = [r["stance"] for r in rows if r.get("stance") is not None]
    if not v:
        return None
    n = len(v)
    lo, hi = sum(x < 0.3 for x in v) / n, sum(x > 0.7 for x in v) / n
    return {"n_on_axis": n, "pct_side_0": round(lo, 2), "pct_mixed": round(1 - lo - hi, 2),
            "pct_side_1": round(hi, 2), "polarized": bool(lo >= 0.35 and hi >= 0.35)}


def _load(path):
    p = _resolve(path)
    return json.loads(p.read_text()) if p.exists() else None


def cluster_summaries(path: str = DEFAULT_COMMENTS, per_cluster: int = 8) -> str:
    """Read this BEFORE labeling. For each cluster: size, keywords, subreddits, mean stance,
    stance_split (share at each end of the axis), whether it is localized (mostly one thread),
    and its most representative comments (claim + original text).
    If stance_split.polarized is true, the cluster is a TOPIC both sides argue about: label the
    topic neutrally (e.g. "Huang's affairs and the ouster"), not one side's position.
    Small output; never read /data/clusters.json directly (it is huge)."""
    data = json.loads(_clusters_path(path).read_text())
    by_c = {}
    for c in data["comments"]:
        by_c.setdefault(c["cluster"], []).append(c)
    out = []
    for meta in data["clusters"]:
        cid = meta["cluster"]
        rows = [r for r in by_c.get(cid, []) if not r.get("is_noise")] or by_c.get(cid, [])
        rows = sorted(rows, key=lambda r: -(r.get("weight") or 0))
        seen_threads, picked = Counter(), []
        for r in rows:  # max 3 per thread so one thread can't dominate the sample
            if seen_threads[r["post_id"]] >= 3:
                continue
            seen_threads[r["post_id"]] += 1
            picked.append({"subreddit": r["subreddit"], "stance": r.get("stance"),
                           "claim": _short(r.get("claim"), 200), "text": _short(r.get("body"), 220)})
            if len(picked) >= per_cluster:
                break
        out.append({k: meta.get(k) for k in ("cluster", "size", "keywords", "subreddit_counts",
                                              "mean_stance", "n_threads", "localized")}
                   | {"stance_split": _split(by_c.get(cid, [])), "examples": picked})
    return json.dumps({"stance_axis": _load("/data/stance_axis.json"), "clusters": out})


def _top(d: dict, n=3):
    return {k: v for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n] if v}


def report_context(path: str = DEFAULT_COMMENTS) -> str:
    """Everything the reporter needs, in one small JSON: event description, search coverage,
    stance axis, stance_split per community and narrative (use it, not mean_stance: a 0.5 mean
    can be a 50/50 split), labeled narratives with 2 example quotes each, communities with their top
    narratives, community groups, strongest shared-commenter links, echo scores, and the
    content-vs-connectivity contrasts (the key Arlequin insight)."""
    q = _load("/data/queries.json") or {}
    clusters = json.loads(_clusters_path(path).read_text())
    conn = _load("/data/connectivity.json") or {}

    by_n = {}
    for c in clusters["comments"]:
        by_n.setdefault(c.get("narrative", c["cluster"]), []).append(c)
    narratives = []
    for n in clusters.get("narratives", []):
        rows = sorted(by_n.get(n["id"], []), key=lambda r: -(r.get("weight") or 0))
        emo = Counter(r.get("emotion") for r in rows if r.get("emotion") not in (None, "unknown"))
        narratives.append({k: n.get(k) for k in ("id", "label", "summary", "size", "weighted_share",
                                                   "mean_stance", "on_axis_share", "n_subreddits")}
                          | {"stance_split": _split(rows),
                             "top_emotion": emo.most_common(1)[0][0] if emo else None,
                             "echo_score": (conn.get("echo_score") or {}).get(str(n["id"])),
                             "quotes": [{"subreddit": r["subreddit"], "text": _short(r["body"], 200)}
                                        for r in rows[:2]]})

    labels = {n["id"]: n["label"] for n in narratives}
    # reply patterns: where each narrative's replies go (row -> column, % of row)
    rm = conn.get("reply_matrix_pct") or {}
    for n in narratives:
        row = {int(k): v for k, v in (rm.get(str(n["id"])) or {}).items() if int(k) != n["id"] and int(k) in labels}
        if row:
            tgt, pct = max(row.items(), key=lambda kv: kv[1])
            n["argues_with"] = {"narrative": labels[tgt], "pct_of_replies": pct}
    shares = conn.get("shares", {})
    by_sub = {}
    for c in clusters["comments"]:
        by_sub.setdefault(c["subreddit"], []).append(c)
    communities = [{"subreddit": c["subreddit"], "n_comments": c["n_comments"], "mean_stance": c.get("mean_stance"),
                    "stance_split": _split(by_sub.get(c["subreddit"], [])),
                    "top_emotions": dict(Counter(r.get("emotion") for r in by_sub.get(c["subreddit"], [])
                                                 if r.get("emotion") not in (None, "unknown", "neutral")).most_common(3)),
                    "top_narratives": {labels.get(int(k), k): v for k, v in _top(shares.get(c["subreddit"], {})).items()}}
                   for c in conn.get("communities", [])]

    # content vs connectivity: where narrative distance and shared users disagree
    js = conn.get("js_distance", {})
    jac = {tuple(sorted((e["a"], e["b"]))): e["jaccard"] for e in conn.get("shared_commenters", [])}
    pairs = []
    for key, d in js.items():
        a, b = key.split("|")
        pairs.append({"a": a, "b": b, "narrative_distance": d, "shared_users": jac.get(tuple(sorted((a, b))), 0.0)})
    contrasts = []
    if pairs:
        ds = sorted(p["narrative_distance"] for p in pairs)
        js_ = sorted(p["shared_users"] for p in pairs)
        d_lo, d_hi = ds[len(ds) // 4], ds[(3 * len(ds)) // 4]
        j_hi = js_[(3 * len(js_)) // 4]
        for p in pairs:
            if p["narrative_distance"] <= d_lo and p["shared_users"] <= 0.01:
                contrasts.append(p | {"pattern": "same story, no shared people (reached it independently)"})
            elif p["narrative_distance"] >= d_hi and p["shared_users"] >= j_hi and p["shared_users"] > 0:
                contrasts.append(p | {"pattern": "shared people, different story"})

    return json.dumps({
        "event": q.get("user_query"), "description": q.get("description"),
        "n_queries": len(q.get("queries", {})),
        "stance_axis": conn.get("stance_axis") or _load("/data/stance_axis.json"),
        "clustering": clusters.get("params"),
        "narratives": narratives,
        "communities": communities,
        "groups": [{k: g.get(k) for k in ("id", "label", "members", "n_comments", "mean_stance")}
                   for g in (conn.get("community_groups") or {}).get("groups", [])][:8],
        "strongest_shared_commenters": sorted(conn.get("shared_commenters", []), key=lambda e: -e["jaccard"])[:6],
        "content_vs_connectivity": contrasts[:6],
        "timeline": _load("/data/timeline.json"),
    })


def sentiment_summary(path: str = DEFAULT_COMMENTS) -> dict:
    """Deterministic sentiment block for briefing.json (no LLM): stance split and top emotions
    per community and per narrative, straight from the scored comments."""
    clusters = json.loads(_clusters_path(path).read_text())
    cm = clusters["comments"]
    labels = {n["id"]: n["label"] for n in clusters.get("narratives", [])}

    def block(rows):
        emo = Counter(r.get("emotion") for r in rows if r.get("emotion") not in (None, "unknown"))
        return {"n_comments": len(rows), "stance_split": _split(rows),
                "top_emotions": {e: round(k / len(rows), 2) for e, k in emo.most_common(4)}}

    by_sub, by_n = {}, {}
    for c in cm:
        by_sub.setdefault(c["subreddit"], []).append(c)
        by_n.setdefault(c.get("narrative", c["cluster"]), []).append(c)
    return {
        "stance_axis": _load("/data/stance_axis.json"),
        "overall": block(cm),
        "by_community": [{"subreddit": s} | block(rows)
                         for s, rows in sorted(by_sub.items(), key=lambda kv: -len(kv[1]))],
        "by_narrative": [{"id": n, "label": labels.get(n, f"cluster {n}")} | block(rows)
                         for n, rows in sorted(by_n.items(), key=lambda kv: -len(kv[1]))],
    }


# ---------------------------------------------------------------- timeline (story over time)
def _js_div(p: dict, q: dict) -> float:
    import math
    keys = set(p) | set(q)
    P = [p.get(k, 0) + 1e-9 for k in keys]
    Q = [q.get(k, 0) + 1e-9 for k in keys]
    sp, sq = sum(P), sum(Q)
    P, Q = [x / sp for x in P], [x / sq for x in Q]
    M = [(a + b) / 2 for a, b in zip(P, Q)]
    kl = lambda A, B: sum(a * math.log2(a / b) for a, b in zip(A, B))
    return round(math.sqrt((kl(P, M) + kl(Q, M)) / 2), 3)


def _when(t) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%b %d %Y %H:%M UTC")


def timeline_summary(path: str = DEFAULT_COMMENTS, n_windows: int = 6) -> str:
    """Evidence for the story of the conversation over time (no interpretation):
    origin (first comments/threads), windows of equal comment count with their narrative mix,
    stance split, communities and top quotes, the threads that STARTED in each window (likely
    triggers), turning points (big change in narrative mix or stance between windows), and the
    order communities joined. Sampling is by whole threads, so a shift can partly reflect which
    threads were collected; say so when interpreting."""
    clusters = json.loads(_clusters_path(path).read_text())
    cm = sorted((c for c in clusters["comments"] if c.get("created_utc")), key=lambda c: c["created_utc"])
    if len(cm) < 30:
        return json.dumps({"error": "too few timestamped comments"})
    labels = {n["id"]: n["label"] for n in clusters.get("narratives", [])}
    lab = lambda c: labels.get(c.get("narrative", c["cluster"]), f"cluster {c['cluster']}")

    titles = dict(_load("/data/threads.json") or {})  # saved by an earlier timeline call
    q = _load("/data/queries.json") or {}
    for qq in q.get("queries", {}).values():
        for r in qq.get("results", []):
            titles[r["post_id"]] = (r.get("title") or "")[:140]
    raw = _resolve(path)
    if raw.exists():  # top-level comments carry the post title as parent_body (Kent's format)
        with raw.open() as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("parent_type") == "post" and r.get("post_id") not in titles and r.get("parent_body"):
                    titles[r["post_id"]] = r["parent_body"].split("\n")[0][:140]
        try:  # keep titles with the cached results (comments.jsonl is not cached)
            _resolve("/data/threads.json").write_text(json.dumps(titles))
        except OSError:
            pass
    thread_start, thread_sub, thread_n = {}, {}, Counter()
    for c in cm:
        thread_start.setdefault(c["post_id"], c["created_utc"])
        thread_sub.setdefault(c["post_id"], c["subreddit"])
        thread_n[c["post_id"]] += 1

    size = max(30, len(cm) // n_windows)
    windows, prev = [], None
    for i in range(0, len(cm), size):
        rows = cm[i:i + size]
        if len(rows) < 15 and windows:  # fold a tiny tail into the last window
            break
        t0, t1 = rows[0]["created_utc"], rows[-1]["created_utc"]
        mix = Counter(lab(r) for r in rows)
        mix = {k: round(v / len(rows), 2) for k, v in mix.most_common()}
        sp = _split(rows)
        started = sorted((p for p, t in thread_start.items() if t0 <= t <= t1), key=lambda p: -thread_n[p])[:3]
        w = {"window": len(windows), "from": _when(t0), "to": _when(t1), "n_comments": len(rows),
             "communities": dict(Counter(r["subreddit"] for r in rows).most_common(3)),
             "narrative_mix": dict(list(mix.items())[:4]), "stance_split": sp,
             "threads_started": [{"subreddit": thread_sub[p], "title": titles.get(p, "(title unknown)"),
                                  "comments": thread_n[p]} for p in started],
             "top_quotes": [{"subreddit": r["subreddit"], "narrative": lab(r), "text": _short(r["body"], 180)}
                            for r in sorted(rows, key=lambda r: -(r.get("weight") or 0))[:3]]}
        if prev:
            shift = _js_div(prev["narrative_mix"], w["narrative_mix"])
            ds = None
            if prev["stance_split"] and sp:
                ds = round(sp["pct_side_0"] - prev["stance_split"]["pct_side_0"], 2)
            w["change_from_previous"] = {"narrative_shift": shift, "side_0_change": ds,
                                         "turning_point": bool(shift >= 0.25 or (ds is not None and abs(ds) >= 0.15))}
        windows.append(w)
        prev = w

    first_by_sub = {}
    for c in cm:
        first_by_sub.setdefault(c["subreddit"], []).append(c)
    spread = []
    for s, rows in sorted(first_by_sub.items(), key=lambda kv: kv[1][0]["created_utc"]):
        if len(rows) < 15:
            continue
        early = rows[:50]
        spread.append({"subreddit": s, "joined": _when(rows[0]["created_utc"]), "n_comments": len(rows),
                       "brought": Counter(lab(r) for r in early).most_common(1)[0][0],
                       "early_stance_split": _split(early)})

    origin_rows = cm[:max(20, len(cm) // 20)]
    first_threads = sorted(thread_start, key=thread_start.get)[:3]
    return json.dumps({
        "stance_axis": _load("/data/stance_axis.json"),
        "span": {"from": _when(cm[0]["created_utc"]), "to": _when(cm[-1]["created_utc"]), "n_comments": len(cm)},
        "origin": {"communities": dict(Counter(r["subreddit"] for r in origin_rows).most_common(3)),
                   "narrative_mix": {k: round(v / len(origin_rows), 2)
                                     for k, v in Counter(lab(r) for r in origin_rows).most_common(3)},
                   "first_threads": [{"subreddit": thread_sub[p], "title": titles.get(p, "(title unknown)"),
                                      "started": _when(thread_start[p])} for p in first_threads]},
        "windows": windows,
        "spread_order": spread,
        "caveat": "Collected by whole threads: shifts can partly reflect which threads were sampled.",
    })
