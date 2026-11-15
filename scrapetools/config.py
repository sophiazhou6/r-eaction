"""Shared settings: env vars, Supabase client, workspace paths, username hashing."""
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://dexgswmmjewpcwkzocax.supabase.co")
SALT = os.environ.get("USERNAME_SALT", "change-me")

# The deep agent's filesystem. Give the orchestrator
#   FilesystemBackend(root_dir=WORKSPACE, virtual_mode=True)
# and the agents will see these files as /data/queries.json, /data/comments.jsonl.
WORKSPACE = Path(os.environ.get("WORKSPACE", "./workspace")).resolve()
DATA_DIR = WORKSPACE / "data"
QUERIES_FILE = DATA_DIR / "queries.json"
COMMENTS_FILE = DATA_DIR / "comments.jsonl"

_client = None


def supabase():
    """Server-side client. Uses the SECRET key, so never ship this to the frontend."""
    global _client
    if _client is None:
        from supabase import create_client
        key = os.environ.get("SUPABASE_SECRET_KEY")
        if not key:
            raise RuntimeError("Set SUPABASE_SECRET_KEY in .env")
        _client = create_client(SUPABASE_URL, key)
    return _client


def hash_user(name: str | None) -> str | None:
    if not name or name in ("[deleted]", "[removed]"):
        return None
    return hashlib.sha256((SALT + name.lower()).encode()).hexdigest()[:16]


def chunks(rows: list, n: int = 500):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]
