
"""Check processed job data for split leakage and data-quality issues.

Run from the repository root:
    python -m dataset.check_splits
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .common import PROCESSED, read_jsonl


def normalize_text(value: str) -> str:
    """Normalize text to detect exact duplicates despite punctuation/spacing."""
    return re.sub(r"\W+", "", (value or "").casefold())


def main() -> None:
    path = PROCESSED / "postings.jsonl"
    rows = list(read_jsonl(path))

    if not rows:
        raise SystemExit(f"No processed postings found at {path}")

    company_splits: dict[str, set[str]] = defaultdict(set)
    text_splits: dict[str, set[str]] = defaultdict(set)
    id_splits: dict[str, set[str]] = defaultdict(set)
    split_counts: dict[str, int] = defaultdict(int)

    for row in rows:
        split = str(row.get("split", "unknown"))
        split_counts[split] += 1

        company = str(row.get("company", "")).strip().casefold()
        if company:
            company_splits[company].add(split)

        text_key = normalize_text(str(row.get("text", "")))
        if text_key:
            text_splits[text_key].add(split)

        posting_id = str(row.get("id", "")).strip()
        if posting_id:
            id_splits[posting_id].add(split)

    company_leaks = sorted(
        company for company, splits in company_splits.items()
        if len(splits) > 1
    )
    duplicate_text_leaks = sum(
        1 for splits in text_splits.values() if len(splits) > 1
    )
    id_leaks = sum(
        1 for splits in id_splits.values() if len(splits) > 1
    )

    report = {
        "file": str(path),
        "total_rows": len(rows),
        "split_counts": dict(sorted(split_counts.items())),
        "distinct_companies": len(company_splits),
        "companies_across_multiple_splits": len(company_leaks),
        "duplicate_texts_across_multiple_splits": duplicate_text_leaks,
        "ids_across_multiple_splits": id_leaks,
        "company_examples": company_leaks[:10],
        "interpretation": (
            "Investigate any nonzero leakage counts before trusting test metrics. "
            "Repeated companies across splits are a warning because company-specific "
            "language may be shared; they are not automatically proof of duplicate jobs."
        ),
    }

    print(json.dumps(report, indent=2))

    if duplicate_text_leaks or id_leaks:
        raise SystemExit(
            "Potential train/test contamination detected. Review the report above."
        )


if __name__ == "__main__":
    main()
