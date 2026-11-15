"""Embed comments that don't have an embedding yet and store them in Supabase.

Model: sentence-transformers/all-MiniLM-L6-v2 (384 dims, runs locally on CPU, free).
Vectors are normalized, so cosine similarity = dot product.

    python embed.py                  # every comment without an embedding
    python embed.py --event 3        # only comments from event 3's posts
    python embed.py --with-context   # embed "comment + what it replies to"

Default embeds the comment text alone. With --with-context, a reply's vector is pulled
toward its parent, so replies tend to cluster with whatever they answered, even when
they disagree with it.

Also usable from Python (e.g. as a tool after fetch_threads):
    from embed import embed_pending; embed_pending(event_id=3)
"""
import argparse
import json
import os

from config import chunks, supabase

MODEL_NAME = os.environ.get("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _text(row: dict, with_context: bool) -> str:
    if with_context and row.get("parent_body"):
        return f"{row['body']}\n(in reply to: {row['parent_body'][:300]})"
    return row["body"]


def _pending(event_id: int | None, page: int = 1000) -> list[dict]:
    sb = supabase()
    post_ids = None
    if event_id is not None:
        post_ids = [r["post_id"] for r in sb.table("event_posts").select("post_id")
                    .eq("event_id", event_id).execute().data]
        if not post_ids:
            return []
    out = []
    for ids in (chunks(post_ids, 100) if post_ids else [None]):
        start = 0
        while True:
            q = sb.table("comments").select("comment_id, body, parent_body").is_("embedding", "null")
            if ids:
                q = q.in_("post_id", ids)
            rows = q.range(start, start + page - 1).execute().data
            out += rows
            if len(rows) < page:
                break
            start += page
    return out


def embed_pending(event_id: int | None = None, with_context: bool = False,
                  batch_size: int = 200) -> str:
    """Embed every comment (optionally only one event's) that has no embedding yet."""
    rows = _pending(event_id)
    if not rows:
        return "nothing to embed"
    model, sb, done = _get_model(), supabase(), 0
    for batch in chunks(rows, batch_size):
        vecs = model.encode([_text(r, with_context) for r in batch],
                            normalize_embeddings=True, batch_size=64)
        payload = [{"comment_id": r["comment_id"],
                    "embedding": json.dumps([round(float(x), 6) for x in v])}
                   for r, v in zip(batch, vecs)]
        sb.rpc("set_comment_embeddings", {"rows": payload}).execute()
        done += len(batch)
        print(f"embedded {done}/{len(rows)}")
    return f"embedded {done} comments with {MODEL_NAME}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", type=int)
    ap.add_argument("--with-context", action="store_true")
    a = ap.parse_args()
    print(embed_pending(a.event, a.with_context))
