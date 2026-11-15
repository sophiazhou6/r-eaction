"""Compare clustering on LLM claims vs. on Kent's stored raw-text embeddings, same comments, same run.

    .venv/bin/python narrative/compare.py            # reuses claims already in workspace/data/comments.jsonl
    .venv/bin/python narrative/compare.py --refetch  # pull the latest event from Supabase + LLM pass first

A = claims (LLM rewrite) embedded with all-MiniLM-L6-v2 locally (free; same model as Kent's, so the
    only difference from B is the text: claims vs raw comments). Falls back to OpenAI embeddings if
    sentence-transformers isn't installed.
B = raw comment text, Kent's stored all-MiniLM-L6-v2 vectors (no new embedding)
Writes workspace/data/compare_report.json and leaves A's outputs as clusters.json / connectivity.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from narrative import sophia_tools as st  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--refetch", action="store_true")
ap.add_argument("--no-judge", action="store_true", help="skip the LLM coherence judge (~$0.01)")
a = ap.parse_args()

path = st._resolve(st.DEFAULT_COMMENTS)
data = path.parent
if a.refetch or not path.exists():
    subprocess.run([sys.executable, str(ROOT / "narrative" / "run_real.py"), "--llm"], check=True)

rows = [json.loads(line) for line in path.open()]
if not rows or "claim" not in rows[0] or all(r.get("stance") is None for r in rows):
    sys.exit("comments.jsonl has no LLM claims yet: run ./run_pipeline.command (or --refetch) first")
if "embedding" not in rows[0]:
    sys.exit("comments.jsonl has no stored embeddings; rerun with --refetch")


def clear_cache():
    for f in data.glob("clusters.*.npy"):
        f.unlink()


def run(name, backend):
    clear_cache()
    st.EMBED_BACKEND = backend
    out = st.cluster_narratives()
    shutil.copy(data / "clusters.json", data / f"clusters_{name}.json")
    return out


try:
    import sentence_transformers  # noqa: F401
    A_BACKEND = "local"
except ImportError:
    A_BACKEND = os.environ.get("COMPARE_A_BACKEND", "openai")
A = run("claims", A_BACKEND)
B = run("stored", "supabase")


def metrics(out, name):
    from sklearn.metrics import normalized_mutual_info_score as nmi
    cm = json.loads((data / f"clusters_{name}.json").read_text())["comments"]
    lab = [c["cluster"] for c in cm]
    thr = [c["post_id"] for c in cm]
    sub = [c["subreddit"] for c in cm]
    cl = out["clusters"]
    top_thread = []
    for c in cl:
        members = [t for t, l in zip(thr, lab) if l == c["cluster"]]
        top_thread.append(max(members.count(t) for t in set(members)) / len(members))
    return {"n_clusters": len(cl), "noise": next((s["noise"] for s in out["sweep"]
                                                  if s.get("min_cluster_size") == out.get("min_cluster_size")), None),
            "largest_share": max(c["size"] for c in cl) / len(cm),
            "mean_top_thread_share": round(sum(top_thread) / len(top_thread), 3),
            "nmi_with_thread": round(nmi(thr, lab), 3),        # lower = clusters less tied to threads
            "nmi_with_subreddit": round(nmi(sub, lab), 3),
            "axis_check": out.get("axis_check", {}).get("fraction")}, dict(zip([c["id"] for c in cm], lab))


mA, labA = metrics(A, "claims")
mB, labB = metrics(B, "stored")
from sklearn.metrics import adjusted_rand_score  # noqa: E402
common = sorted(set(labA) & set(labB))
agree = adjusted_rand_score([labA[i] for i in common], [labB[i] for i in common])


async def judge(name, out):
    """LLM rates each cluster: do 6 random comments share one position? 1-5."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI()
    cm = json.loads((data / f"clusters_{name}.json").read_text())["comments"]
    random.seed(0)
    scores = {}

    async def one(c):
        mem = [x["body"][:300] for x in cm if x["cluster"] == c["cluster"] and not x["is_noise"]]
        sample = random.sample(mem, min(6, len(mem)))
        msg = ("Do these Reddit comments express the same position or narrative about the news? "
               "Reply with JSON {\"score\": 1-5} (5 = clearly one shared position, 1 = unrelated).\n\n"
               + "\n".join(f"- {s}" for s in sample))
        r = await client.chat.completions.create(model=st.CHAT_MODEL, temperature=0,
                                                 response_format={"type": "json_object"},
                                                 messages=[{"role": "user", "content": msg}])
        scores[c["cluster"]] = json.loads(r.choices[0].message.content).get("score")
    await asyncio.gather(*[one(c) for c in out["clusters"]])
    sizes = {c["cluster"]: c["size"] for c in out["clusters"]}
    w = sum(sizes.values())
    return round(sum((scores[k] or 0) * sizes[k] for k in scores) / w, 2), scores


if not a.no_judge and os.environ.get("OPENAI_API_KEY"):
    mA["coherence_1to5"], _ = asyncio.run(judge("claims", A))
    mB["coherence_1to5"], _ = asyncio.run(judge("stored", B))

print(f"\n=== A: LLM claims ({A['embed_backend']})  vs  B: raw text ({B['embed_backend']}) ===")
keys = list(mA)
print(f"{'metric':<24}{'A claims':>12}{'B stored':>12}")
for k in keys:
    print(f"{k:<24}{str(mA.get(k)):>12}{str(mB.get(k)):>12}")
print(f"{'agreement (ARI A vs B)':<24}{round(agree, 3):>12}")
print("\nlower nmi_with_thread / mean_top_thread_share = clusters are narratives, not just threads;"
      " higher coherence = clusters make sense to a reader.")
for name, out in (("A claims", A), ("B stored", B)):
    print(f"\n--- {name} ---")
    for c in out["clusters"]:
        print(f"[{c['cluster']}] n={c['size']} subs={c['subreddit_counts']} kw={', '.join(c['keywords'][:5])}")
        for t in c["top_comments"][:2]:
            print(f"     [{t['score']:>5}] {t['text'][:130]}")

(data / "compare_report.json").write_text(json.dumps({"A_claims": mA, "B_stored": mB, "ari": agree}, indent=1))
# leave A as the live output for the UI
shutil.copy(data / "clusters_claims.json", data / "clusters.json")
st.set_narratives([{"cluster": c["cluster"], "label": " / ".join(c["keywords"][:3]), "summary": "",
                    "merge_into": None} for c in A["clusters"]])
print("\n", json.dumps(st.build_connectivity(min_comments=20)))
print(f"\nreport: {data / 'compare_report.json'}")
