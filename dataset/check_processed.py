"""Validate and summarize the normalized postings JSONL dataset.

Usage:
  python -m dataset.check_processed
  python -m dataset.check_processed --postings path/to/postings.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .common import PROCESSED


FIELDS = ("title", "text", "skills", "role", "split")


def inspect_postings(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(
            f"Processed dataset not found: {path}\n"
            "Run dataset.build_postings first."
        )

    rows = 0
    populated = Counter()
    roles = Counter()
    splits = Counter()
    labels: set[str] = set()
    labelled_rows = 0
    sample = None

    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise SystemExit(f"Expected a JSON object on line {line_number}.")

            rows += 1
            if sample is None:
                sample = row
            for field in FIELDS:
                if row.get(field) not in (None, "", []):
                    populated[field] += 1

            skills = row.get("skills", [])
            if not isinstance(skills, list):
                raise SystemExit(f"Expected skills to be a list on line {line_number}.")
            if skills:
                labelled_rows += 1
                labels.update(str(skill).strip().casefold() for skill in skills if str(skill).strip())

            roles[str(row.get("role", "missing"))] += 1
            splits[str(row.get("split", "missing"))] += 1

    if rows == 0:
        raise SystemExit(f"Processed dataset is empty: {path}")

    return {
        "path": str(path),
        "total_rows": rows,
        "populated_fields": {field: populated[field] for field in FIELDS},
        "split_distribution": dict(sorted(splits.items())),
        "role_distribution": dict(sorted(roles.items())),
        "rows_with_skill_labels": labelled_rows,
        "distinct_skill_labels": len(labels),
        "sample": {
            "title": sample.get("title"),
            "role": sample.get("role"),
            "split": sample.get("split"),
            "skills": sample.get("skills", [])[:10],
            "description_preview": str(sample.get("text", ""))[:250],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postings", default=str(PROCESSED / "postings.jsonl"))
    args = parser.parse_args()
    print(json.dumps(inspect_postings(Path(args.postings)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
