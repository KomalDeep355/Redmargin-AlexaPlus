"""Collect public GitHub repos into dataset/processed/repos.jsonl (tree, key files, signals).

Needs GITHUB_TOKEN in .env (the search API is very limited without one). Resumable: run it again and it skips
repos it already has. Stops cleanly when GitHub's rate limit is reached.

Usage:
  python -m dataset.collect_repos --per-query 30 --stars 1..100
  python -m dataset.collect_repos --query "topic:rag language:python" --role fde
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from pathlib import Path

import httpx

from app import config
from app.github_client import API, RepoError, fetch_snapshot
from app.signals import compute_signals

from .common import PROCESSED, append_jsonl, assign_split, read_jsonl

DEFAULT_QUERIES = {
    "fde": ["topic:llm language:python", "topic:rag language:python", "topic:chatbot language:python"],
    "data_scientist": ["topic:data-science language:python", "topic:machine-learning language:python"],
    "nlp": ["topic:nlp language:python", "topic:transformers language:python"],
    "swe": ["topic:fastapi language:python", "topic:react language:typescript"],
}


async def search_repos(client: httpx.AsyncClient, query: str, want: int) -> list[str]:
    names: list[str] = []
    page = 1
    while len(names) < want and page <= 10:
        r = await client.get(f"{API}/search/repositories",
                             params={"q": query, "sort": "updated", "order": "desc", "per_page": 30, "page": page})
        if r.status_code in (403, 429):
            raise RepoError("GitHub rate limit reached during search")
        if r.status_code >= 400:
            print(f"  search error {r.status_code} for '{query}'")
            break
        items = r.json().get("items", [])
        if not items:
            break
        names += [it["full_name"] for it in items if not it.get("fork") and not it.get("archived")]
        page += 1
        await asyncio.sleep(2.5)  # the search API allows about 30 requests a minute with a token
    return names[:want]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-query", type=int, default=30)
    ap.add_argument("--stars", default="1..100", help="star range, e.g. 0..50 for student-style projects")
    ap.add_argument("--query", help="custom search query (use with --role)")
    ap.add_argument("--role", help="role hint for the custom query: fde|data_scientist|nlp|swe")
    ap.add_argument("--out", default=str(PROCESSED / "repos.jsonl"))
    args = ap.parse_args()

    if not config.GITHUB_TOKEN:
        raise SystemExit("Set GITHUB_TOKEN in .env first (github.com/settings/tokens, no scopes needed for public repos).")

    out = Path(args.out)
    done = {r["full_name"] for r in read_jsonl(out)} if out.exists() else set()
    plan = {args.role or "swe": [args.query]} if args.query else DEFAULT_QUERIES
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "repo-reviewer-dataset",
               "Authorization": f"Bearer {config.GITHUB_TOKEN}"}

    added = 0
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        for role, queries in plan.items():
            for q in queries:
                full_q = f"{q} stars:{args.stars} fork:false archived:false"
                print(f"[{role}] {full_q}")
                try:
                    names = await search_repos(client, full_q, args.per_query)
                except RepoError as e:
                    print("Stopping:", e, "- run again later; progress is saved.")
                    return
                for full in names:
                    if full in done:
                        continue
                    try:
                        snap = await fetch_snapshot(f"https://github.com/{full}")
                    except RepoError as e:
                        if "rate limit" in str(e).lower():
                            print("Stopping:", e, "- run again later; progress is saved.")
                            return
                        print(f"  skip {full}: {e}")
                        continue
                    sigs = compute_signals(snap)
                    append_jsonl(out, {
                        "full_name": snap.full_name, "owner": snap.owner, "role_hint": role,
                        "split": assign_split(snap.owner), "description": snap.description, "stars": snap.stars,
                        "primary_language": snap.primary_language, "pushed_at": snap.pushed_at, "branch": snap.branch,
                        "tree": snap.tree[:800], "tree_truncated": snap.tree_truncated or len(snap.tree) > 800,
                        "files": snap.files,
                        "signals": {k: {"value": s.value, "evidence": s.evidence} for k, s in sigs.items()},
                    })
                    done.add(full)
                    added += 1
                    print(f"  + {full} ({added} new)")
                    await asyncio.sleep(0.5)
    print(f"Done. {added} new repos. Total: {len(done)} in {out}")


if __name__ == "__main__":
    asyncio.run(main())
