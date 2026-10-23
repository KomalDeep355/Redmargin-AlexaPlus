"""Make a CSV for HAND-labelling a gold set (aim for 50 repos). This is the evidence that your system works.

For each repo, open it on GitHub and fill the human_* columns with y or n. Then run eval_gold.py.

Usage: python -m dataset.make_gold_sheet --n 50
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path
from collections import defaultdict

from .common import PROCESSED, read_jsonl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--silver", default=str(PROCESSED / "repos_silver.jsonl"))
    ap.add_argument("--out", default=str(PROCESSED / "gold_labeling.csv"))
    args = ap.parse_args()
    if args.n < 1:
        ap.error("--n must be positive.")

    rows = list(read_jsonl(Path(args.silver)))
    if not rows:
        raise SystemExit("No silver rows. Run collect_repos and label_silver first.")
    by_role = defaultdict(list)
    for r in rows:
        by_role[r["role_hint"]].append(r)
    rng = random.Random(args.seed)
    role_rows = {}
    for role, rs in sorted(by_role.items()):
        rng.shuffle(rs)
        role_rows[role] = rs
    picked = []
    positions = {role: 0 for role in role_rows}
    while len(picked) < min(args.n, len(rows)):
        added_this_round = False
        for role, role_items in role_rows.items():
            position = positions[role]
            if position < len(role_items) and len(picked) < args.n:
                picked.append(role_items[position])
                positions[role] += 1
                added_this_round = True
        if not added_this_round:
            break

    signal_names = list(picked[0]["signals"])
    header = ["full_name", "url", "role_hint", "split"]
    for s in signal_names:
        header += [f"pred_{s}", f"human_{s}"]
    header += ["human_biggest_gap", "human_resume_worthy(y/n/maybe)"]

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in picked:
            row = [r["full_name"], f"https://github.com/{r['full_name']}", r["role_hint"], r["split"]]
            for s in signal_names:
                row += ["y" if r["signals"][s] else "n", ""]
            row += ["", ""]
            w.writerow(row)
    print(f"Wrote {len(picked)} repos to {output_path}. Fill the human_* columns (y/n), then run eval_gold.")


if __name__ == "__main__":
    main()
