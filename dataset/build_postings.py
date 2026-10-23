"""Turn job-posting CSVs you downloaded (e.g. from Kaggle) into one clean, labelled, split JSONL file.

Usage (from the project root):
  python -m dataset.build_postings --input dataset/raw/postings.csv --input dataset/raw/other.csv
  python -m dataset.build_postings --input dataset/raw/postings.csv --export-app 15

--export-app N also writes N real postings per role into data/postings/<role>/real-*.txt so the app stops
using the synthetic samples.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app import config

from .common import PROCESSED, assign_split, write_jsonl

csv.field_size_limit(2**31 - 1)  # job descriptions can be long

# first matching rule wins, so the order matters
ROLE_RULES = [
    ("fde", ["forward deployed", "forward-deployed", "solutions engineer", "customer engineer", "implementation engineer",
             "deployment engineer", "field engineer", "solutions architect", "applied ai engineer", "ai solutions"]),
    ("nlp", ["nlp", "natural language", "llm", "language model", "machine learning engineer", "ml engineer", "ai engineer"]),
    ("data_scientist", ["data scientist", "applied scientist", "research scientist", "machine learning scientist"]),
    ("swe", ["software engineer", "software developer", "backend engineer", "back-end engineer", "full stack",
             "full-stack", "python developer", "web developer"]),
]
COLUMN_CANDIDATES = {
    "id": ["job_id", "job_link", "id"],
    "title": ["title", "job_title", "job"],
    "text": ["description", "job_summary", "job_details", "job_description"],
    "skills": ["skills", "job_skills"],
    "company": ["company_name", "company"],
}


def pick_column(header: list[str], names: list[str]) -> str | None:
    lower = {h.lower().strip(): h for h in header}
    for n in names:
        if n in lower:
            return lower[n]
    return None


def classify_title(title: str) -> str | None:
    t = title.casefold()
    for role, keys in ROLE_RULES:
        if any(k in t for k in keys):
            return role
    return None


def clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\r", "", text)).strip()


def mostly_english(text: str) -> bool:
    sample = text[:2000]
    return bool(sample) and sum(c.isascii() for c in sample) / len(sample) > 0.92


def parse_skills(raw: str) -> list[str]:
    value = (raw or "").strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        values = [str(skill).strip() for skill in parsed]
    else:
        values = [skill.strip().strip("[]'\"") for skill in re.split(r"[,;|]", value)]
    return list(dict.fromkeys(skill for skill in values if skill))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", action="append", required=True, help="CSV file (repeatable)")
    ap.add_argument("--out", default=str(PROCESSED / "postings.jsonl"))
    ap.add_argument("--min-chars", type=int, default=300)
    ap.add_argument("--max-per-role", type=int, default=5000)
    ap.add_argument("--export-app", type=int, default=0, help="write N postings per role into data/postings/")
    args = ap.parse_args()
    if args.min_chars < 1:
        ap.error("--min-chars must be positive.")
    if args.max_per_role < 1:
        ap.error("--max-per-role must be positive.")
    if args.export_app < 0:
        ap.error("--export-app cannot be negative.")

    seen, rows, per_role = set(), [], Counter()
    for path in args.input:
        with open(path, encoding="utf-8", errors="ignore", newline="") as f:
            reader = csv.DictReader(f)
            cols = {k: pick_column(reader.fieldnames or [], v) for k, v in COLUMN_CANDIDATES.items()}
            if not cols["title"] or not cols["text"]:
                raise SystemExit(
                    f"{path}: could not find title/description columns in {reader.fieldnames}"
                )
            print(f"{path}: using columns {cols}")
            for i, r in enumerate(reader):
                title = clean(r.get(cols["title"]) or "")
                text = clean(r.get(cols["text"]) or "")
                role = classify_title(title)
                if not role or len(text) < args.min_chars or not mostly_english(text):
                    continue
                if per_role[role] >= args.max_per_role:
                    continue
                normalized_text = re.sub(r"\W+", "", text.casefold())
                digest = hashlib.sha256(f"{role}:{normalized_text}".encode("utf-8")).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                company = clean(r.get(cols["company"]) or "") if cols["company"] else ""
                pid = (r.get(cols["id"]) if cols["id"] else "") or f"{Path(path).stem}-{i}"
                rows.append({
                    "id": str(pid), "role": role, "title": title, "company": company, "text": text,
                    "skills": parse_skills(r.get(cols["skills"]) or "") if cols["skills"] else [],
                    "source_file": Path(path).name,
                    "split": assign_split(company or str(pid)),
                })
                per_role[role] += 1

    n = write_jsonl(Path(args.out), rows)
    print(f"\nWrote {n} postings to {args.out}")
    for role, c in per_role.most_common():
        print(f"  {role}: {c}")
    print("  with skill labels:", sum(1 for r in rows if r["skills"]))

    if args.export_app:
        for role in per_role:
            folder = config.DATA_DIR / "postings" / role
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob("real-*.txt"):
                old.unlink()
            pool = sorted((r for r in rows if r["role"] == role),
                          key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())[: args.export_app]
            for k, r in enumerate(pool, 1):
                (folder / f"real-{k:02d}.txt").write_text(f"{r['title']}\n\n{r['text']}", encoding="utf-8")
            print(f"  exported {len(pool)} postings to data/postings/{role}/")


if __name__ == "__main__":
    main()
