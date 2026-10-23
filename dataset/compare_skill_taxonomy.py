"""Compare baseline skill labels with Redmargin's canonical skill taxonomy.

This is an offline audit only. It does not change taxonomy entries, app behavior,
or market-demand calculations.

Usage:
  python -m dataset.compare_skill_taxonomy
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from .common import PROCESSED

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAXONOMY = ROOT / "data" / "skills.json"


def normalize_skill_name(value: str) -> str:
    """Normalize punctuation and spacing while retaining exact-token matching."""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def taxonomy_index(taxonomy: list[dict]) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    aliases: dict[str, dict[str, dict]] = defaultdict(dict)
    skills_by_id = {}
    for skill in taxonomy:
        skill_id = skill.get("id")
        label = skill.get("label")
        if not isinstance(skill_id, str) or not isinstance(label, str):
            raise ValueError("Every taxonomy skill must have string 'id' and 'label' fields.")
        if skill_id in skills_by_id:
            raise ValueError(f"Duplicate taxonomy skill id: {skill_id}")
        skills_by_id[skill_id] = {"id": skill_id, "label": label}

        synonyms = skill.get("synonyms")
        if not isinstance(synonyms, list) or any(not isinstance(term, str) for term in synonyms):
            raise ValueError(f"Taxonomy skill {skill_id!r} must have a list of string synonyms.")
        for term in [skill_id, label, *synonyms]:
            normalized = normalize_skill_name(term.strip())
            if normalized:
                aliases[normalized][skill_id] = skills_by_id[skill_id]

    return {key: list(value.values()) for key, value in aliases.items()}, skills_by_id


def compare_labels(report: dict, taxonomy: list[dict]) -> dict:
    per_label = report.get("per_label")
    if not isinstance(per_label, dict):
        raise ValueError("Baseline report must contain a 'per_label' object.")

    aliases, _ = taxonomy_index(taxonomy)
    matched = []
    ambiguous = []
    unmatched = []

    for source_label, metrics in sorted(per_label.items(), key=lambda item: item[0].casefold()):
        if not isinstance(source_label, str):
            continue
        candidates = aliases.get(normalize_skill_name(source_label), [])
        item = {"source_label": source_label, "metrics": metrics}
        if len(candidates) == 1:
            matched.append({**item, "canonical_skill": candidates[0]})
        elif candidates:
            ambiguous.append({**item, "candidates": candidates})
        else:
            unmatched.append(item)

    canonical_matches = defaultdict(list)
    canonical_labels = {}
    for item in matched:
        skill = item["canonical_skill"]
        canonical_matches[skill["id"]].append(item["source_label"])
        canonical_labels[skill["id"]] = skill["label"]

    return {
        "baseline_report": report.get("note", ""),
        "model_label_count": len(per_label),
        "matched_count": len(matched),
        "ambiguous_count": len(ambiguous),
        "unmatched_count": len(unmatched),
        "matched": matched,
        "ambiguous_for_manual_review": ambiguous,
        "unmatched_for_manual_review": unmatched,
        "taxonomy_alias_collisions_for_manual_review": [
            {"normalized_alias": alias, "candidates": candidates}
            for alias, candidates in sorted(aliases.items())
            if len(candidates) > 1
        ],
        "canonical_skills_matched": [
            {
                "id": skill_id,
                "label": canonical_labels[skill_id],
                "source_labels": sorted(labels, key=str.casefold),
            }
            for skill_id, labels in sorted(canonical_matches.items())
        ],
        "note": (
            "Exact normalized alias matching only. Ambiguous and unmatched labels "
            "are not assigned automatically. This audit does not provide market data."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=str(PROCESSED / "baseline_report.json"))
    parser.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    parser.add_argument("--out", default=str(PROCESSED / "taxonomy_mapping_report.json"))
    args = parser.parse_args()

    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        taxonomy = json.loads(Path(args.taxonomy).read_text(encoding="utf-8"))
        if not isinstance(taxonomy, list):
            raise ValueError("Taxonomy JSON must be a list of skill objects.")
        result = compare_labels(report, taxonomy)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"Could not compare taxonomy labels: {exc}") from exc

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nWrote taxonomy comparison to {output_path}")


if __name__ == "__main__":
    main()
