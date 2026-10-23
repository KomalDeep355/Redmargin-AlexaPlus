"""Create SILVER labels: what the deterministic pipeline says about each collected repo.

Honest warning: silver labels are the rules' own output, so they can NOT prove the system is correct. Use them for
consistency checks, weak supervision and error analysis. Only hand-made GOLD labels (make_gold_sheet.py) can
prove accuracy.

Usage: python -m dataset.label_silver
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from app.github_client import RepoSnapshot
from app.planner import build_plan
from app.signals import compute_signals
from app.skills import build_skill_matrix

from .common import PROCESSED, read_jsonl, write_jsonl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", default=str(PROCESSED / "repos.jsonl"))
    ap.add_argument("--postings", default=str(PROCESSED / "postings.jsonl"))
    ap.add_argument("--out", default=str(PROCESSED / "repos_silver.jsonl"))
    ap.add_argument("--max-postings", type=int, default=300)
    args = ap.parse_args()

    by_role = defaultdict(list)
    for p in read_jsonl(Path(args.postings)):
        if p["split"] == "train":  # reference postings come from the train split only
            by_role[p["role"]].append(p["text"])
    for role, texts in by_role.items():
        by_role[role] = texts[: args.max_postings]
        if len(texts) < 10:
            print(f"! only {len(texts)} train postings for {role}; percentages will be noisy")

    rows = []
    for r in read_jsonl(Path(args.repos)):
        texts = by_role.get(r["role_hint"])
        if not texts:
            continue
        snap = RepoSnapshot(r["full_name"].split("/")[0], r["full_name"].split("/")[1], r.get("branch", "main"),
                            r.get("description", ""), "", r.get("stars", 0), r.get("pushed_at", ""),
                            r.get("primary_language", ""), r["tree"], r.get("tree_truncated", False), r["files"])
        sigs = compute_signals(snap)
        matrix = build_skill_matrix(snap, sigs, texts, r["role_hint"])
        plan = build_plan(matrix, sigs)
        rows.append({
            "full_name": r["full_name"], "split": r["split"], "role_hint": r["role_hint"],
            "signals": {k: s.value for k, s in sigs.items()},
            "gaps": [m["id"] for m in matrix if m["status"] == "gap"],
            "strengths": [m["id"] for m in matrix if m["status"] == "strength"],
            "top_fixes": [f["fix_id"] for f in plan["fixes"][:5]],
        })
    n = write_jsonl(Path(args.out), rows)
    print(f"Wrote {n} silver-labelled repos to {args.out}")


if __name__ == "__main__":
    main()
