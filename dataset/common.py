"""Shared helpers for the dataset pipeline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"              # put downloaded CSVs here (never commit)
PROCESSED = ROOT / "processed"  # generated files (never commit)


def assign_split(key: str) -> str:
    """Deterministic 70/15/15 split. Split by company (postings) or owner (repos) so near-duplicates
    never land in both train and test."""
    h = int(hashlib.md5((key or "").strip().lower().encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if h < 70 else "val" if h < 85 else "test"


def read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
