"""Reddit access. All backends return raw dicts; tools.py cleans them.

- Arctic Shift (default): public Reddit archive, no keys. Reddit itself now blocks
  requests that aren't logged in (403) and has stopped approving new API apps.
- PRAW (official API) when REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set.
- Public .json endpoints with REDDIT_BACKEND=json (blocked by Reddit as of Nov 2026).
"""
import os
import threading
import time

import requests

USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "hackprinceton-narrative-map/0.1")


def _post(d: dict) -> dict:
    p = {k: d.get(k) for k in ("id", "subreddit", "author", "title", "selftext", "url",
                               "permalink", "score", "upvote_ratio", "num_comments",
                               "created_utc")}
    if not p["permalink"]:
        p["permalink"] = f"/r/{p['subreddit']}/comments/{p['id']}/"
    return p


def _comment(d: dict, n_replies: int) -> dict:
    return {k: d.get(k) for k in ("id", "parent_id", "author", "body", "score", "depth",
                                  "created_utc")} | {"n_replies": n_replies}


class PublicJSONClient:
    BASE = "https://www.reddit.com"

    def __init__(self, delay: float | None = None):
        self.delay = float(os.environ.get("REDDIT_DELAY", "6")) if delay is None else delay
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._last = 0.0

    def _get(self, path: str, params: dict):
        for attempt in range(4):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            r = self.s.get(self.BASE + path, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Reddit kept rate-limiting {path}")

    def search(self, query, time_filter="month", limit=50, subreddit=None) -> list[dict]:
        path = f"/r/{subreddit}/search.json" if subreddit else "/search.json"
        params = {"q": query, "sort": "relevance", "t": time_filter,
                  "limit": min(limit, 100), "type": "link", "raw_json": 1}
        if subreddit:
            params["restrict_sr"] = 1
        data = self._get(path, params)
        return [_post(c["data"]) for c in data["data"]["children"] if c["kind"] == "t3"]

    def thread(self, post_id, limit=500, post=None) -> tuple[dict, list[dict]]:
        """Post + its comments in tree order (a parent always comes before its replies)."""
        data = self._get(f"/comments/{post_id}.json",
                         {"limit": limit, "depth": 10, "sort": "top", "raw_json": 1})
        post = _post(data[0]["data"]["children"][0]["data"])
        out = []

        def walk(children):
            for ch in children:
                if ch["kind"] != "t1":
                    continue
                d = ch["data"]
                rep = d.get("replies")
                kids = rep["data"]["children"] if isinstance(rep, dict) else []
                out.append(_comment(d, sum(k["kind"] == "t1" for k in kids)))
                walk(kids)

        walk(data[1]["data"]["children"])
        return post, out


class PRAWClient:
    def __init__(self):
        import praw
        self.r = praw.Reddit(client_id=os.environ["REDDIT_CLIENT_ID"],
                             client_secret=os.environ["REDDIT_CLIENT_SECRET"],
                             user_agent=USER_AGENT)
        self.replace_more = int(os.environ.get("REPLACE_MORE", "8"))

    @staticmethod
    def _p(s) -> dict:
        return {"id": s.id, "subreddit": s.subreddit.display_name,
                "author": s.author.name if s.author else None, "title": s.title,
                "selftext": s.selftext, "url": s.url, "permalink": s.permalink,
                "score": s.score, "upvote_ratio": s.upvote_ratio,
                "num_comments": s.num_comments, "created_utc": s.created_utc}

    def search(self, query, time_filter="month", limit=50, subreddit=None):
        sr = self.r.subreddit(subreddit or "all")
        return [self._p(s) for s in sr.search(query, sort="relevance",
                                              time_filter=time_filter, limit=limit)]

    def thread(self, post_id, limit=500, post=None):
        s = self.r.submission(id=post_id)
        s.comment_sort = "top"
        s.comments.replace_more(limit=self.replace_more)
        out = [{"id": c.id, "parent_id": c.parent_id, "body": c.body,
                "author": c.author.name if c.author else None, "score": c.score,
                "depth": c.depth, "created_utc": c.created_utc, "n_replies": len(c.replies)}
               for c in s.comments.list()[:limit]]   # list() is breadth-first: parents first
        return self._p(s), out


class ArcticShiftClient:
    """Arctic Shift: a public archive of Reddit (arctic-shift.photon-reddit.com).

    No keys. Title search must be scoped to a subreddit, so a search with no
    subreddit runs over DEFAULT_SUBS and merges the results.

    Thread-safe: up to ARCTIC_WORKERS requests in flight at once, and request starts
    are spaced at least ARCTIC_DELAY seconds apart across all threads.
    """
    BASE = "https://arctic-shift.photon-reddit.com"
    DEFAULT_SUBS = os.environ.get(
        "DEFAULT_SUBS", "news,worldnews,politics,technology,business,stocks,AskReddit,nottheonion"
    ).split(",")
    WINDOW_DAYS = {"hour": 1, "day": 1, "week": 7, "month": 31, "year": 365, "all": 3650}

    def __init__(self, delay: float | None = None, workers: int | None = None):
        self.delay = float(os.environ.get("ARCTIC_DELAY", "1.5")) if delay is None else delay
        self.workers = int(os.environ.get("ARCTIC_WORKERS", "3")) if workers is None else workers
        self._sem = threading.Semaphore(self.workers)
        self._lock = threading.Lock()
        self._next_start = 0.0
        self._local = threading.local()

    def _session(self):
        if not hasattr(self._local, "s"):
            self._local.s = requests.Session()
            self._local.s.headers["User-Agent"] = USER_AGENT
        return self._local.s

    def _throttle(self):
        with self._lock:
            now = time.time()
            wait = max(0.0, self._next_start - now)
            self._next_start = max(now, self._next_start) + self.delay
        if wait:
            time.sleep(wait)

    def _get(self, path: str, params: dict):
        last_err = None
        with self._sem:
            for attempt in range(6):
                self._throttle()
                try:
                    r = self._session().get(self.BASE + path, params=params, timeout=90)
                    body = r.json()
                except Exception as e:
                    last_err = e
                    time.sleep(5 * (attempt + 1))
                    continue
                if r.status_code == 200 and body.get("error") is None:
                    return body.get("data")
                last_err = body.get("error") or f"HTTP {r.status_code}"
                # "Timeout. Maybe slow down a bit": back off hard (10s, 20s, 30s, ...)
                time.sleep(10 * (attempt + 1))
        raise RuntimeError(f"Arctic Shift {path} failed: {last_err}")

    def _search_one(self, query, sub, after, limit):
        data = self._get("/api/posts/search", {
            "subreddit": sub, "title": query, "after": after, "limit": min(limit, 100),
            "sort": "desc"})
        return [_post(p) for p in (data or [])]

    def search(self, query, time_filter="month", limit=100, subreddit=None) -> list[dict]:
        from datetime import date, timedelta
        after = (date.today() - timedelta(days=self.WINDOW_DAYS.get(time_filter, 31))).isoformat()
        subs = [subreddit] if subreddit else self.DEFAULT_SUBS
        posts, errors = [], []
        for sub in subs:
            try:
                posts += self._search_one(query, sub, after, limit)
            except Exception as e:
                errors.append(f"r/{sub}: {e}")
        if errors and not posts:
            raise RuntimeError("; ".join(errors))
        posts = list({p["id"]: p for p in posts}.values())
        # most-discussed first, so rank (for recall@k) favours big threads
        posts.sort(key=lambda p: -(p.get("num_comments") or 0))
        return posts[:limit]

    def thread(self, post_id, limit=5000, post: dict | None = None) -> tuple[dict, list[dict]]:
        """Post + up to `limit` comments. Pass `post` (from search) to skip re-fetching it.
        Collapsed "more" branches are expanded with /api/comments/ids."""
        if post is None:
            posts = self._get("/api/posts/ids", {"ids": post_id}) or []
            if not posts:
                raise RuntimeError(f"post {post_id} not in archive")
            post = _post(posts[0])
        tree = self._get("/api/comments/tree",
                         {"link_id": f"t3_{post_id}", "limit": min(limit, 25000)}) or []
        out, seen, more_ids = [], set(), []

        def node_data(n):
            if not isinstance(n, dict):
                return None
            if n.get("kind") == "more":
                ids = n.get("children") or n.get("data", {}).get("children") or []
                more_ids.extend(str(i).removeprefix("t1_") for i in ids)
                return None
            return n.get("data", n)

        def kids_of(d):
            rep = d.get("replies") or d.get("children") or []
            if isinstance(rep, dict):                      # Reddit Listing
                rep = rep.get("data", {}).get("children", [])
            return [k for k in rep if isinstance(k, dict)] if isinstance(rep, list) else []

        def add(d, depth):
            if d["id"] in seen:
                return
            seen.add(d["id"])
            out.append(_comment({**d, "depth": d.get("depth", depth)}, 0))

        def walk(nodes, depth):
            for n in nodes:
                d = node_data(n)
                if not d or "body" not in d:
                    continue
                add(d, depth)
                walk(kids_of(d), depth + 1)

        walk(tree if isinstance(tree, list) else [tree], 0)

        # expand collapsed branches, 500 ids per request, until we reach the limit
        todo = [i for i in dict.fromkeys(more_ids) if i not in seen]
        while todo and len(out) < limit:
            batch, todo = todo[:500], todo[500:]
            for d in self._get("/api/comments/ids", {"ids": ",".join(batch)}) or []:
                d = d.get("data", d)
                if "body" in d:
                    add(d, None)

        # reply counts and depths from the full set (expanded comments arrive flat)
        out = out[:limit]
        by_id = {c["id"]: c for c in out}
        counts: dict[str, int] = {}
        for c in out:
            par = (c.get("parent_id") or "")
            if par.startswith("t1_"):
                counts[par[3:]] = counts.get(par[3:], 0) + 1
        for c in out:
            c["n_replies"] = counts.get(c["id"], 0)
            if c.get("depth") is None:
                d, cur = 0, c
                while (cur.get("parent_id") or "").startswith("t1_") and cur["parent_id"][3:] in by_id and d < 50:
                    cur, d = by_id[cur["parent_id"][3:]], d + 1
                c["depth"] = d
        return post, out


def get_client():
    if os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET"):
        return PRAWClient()
    if os.environ.get("REDDIT_BACKEND") == "json":
        return PublicJSONClient()
    return ArcticShiftClient()
