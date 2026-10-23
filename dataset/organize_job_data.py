"""Join the source job-posting, summary, and skill CSVs into one builder-ready CSV.

Usage:
  python -m dataset.organize_job_data --input-dir dataset/raw
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd


SOURCE_FILES = ("job_postings.csv", "job_summary.csv", "job_skills.csv")


def read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Required source CSV was not found: {path}")
    return pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8",
        encoding_errors="replace",
        low_memory=False,
    )


def require_columns(frame: pd.DataFrame, required: set[str], path: Path) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{path} is missing required column(s): {', '.join(missing)}")


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def aggregate_skills(values: pd.Series) -> str:
    skills = []
    seen = set()
    for value in values:
        for skill in clean_text(value).split(","):
            normalized = clean_text(skill)
            key = normalized.casefold()
            if key and key not in seen:
                seen.add(key)
                skills.append(normalized)
    return ", ".join(skills)


def organize(input_dir: Path, output_path: Path) -> dict:
    paths = {name: input_dir / name for name in SOURCE_FILES}
    postings = read_csv(paths["job_postings.csv"])
    summaries = read_csv(paths["job_summary.csv"])
    skills = read_csv(paths["job_skills.csv"])

    require_columns(postings, {"job_link", "job_title"}, paths["job_postings.csv"])
    require_columns(summaries, {"job_link", "job_summary"}, paths["job_summary.csv"])
    require_columns(skills, {"job_link", "job_skills"}, paths["job_skills.csv"])

    for frame in (postings, summaries, skills):
        frame["job_link"] = frame["job_link"].map(clean_text)

    summaries["job_summary"] = summaries["job_summary"].map(clean_text)
    summaries = (
        summaries.loc[summaries["job_link"].ne("")]
        .groupby("job_link", as_index=False, sort=False)["job_summary"]
        .agg(lambda values: next((value for value in values if value), ""))
    )

    skills["job_skills"] = skills["job_skills"].map(clean_text)
    skills = (
        skills.loc[skills["job_link"].ne("")]
        .groupby("job_link", as_index=False, sort=False)["job_skills"]
        .agg(aggregate_skills)
    )

    postings_source_rows = len(postings)
    postings["job_title"] = postings["job_title"].map(clean_text)
    if "company" not in postings.columns:
        postings["company"] = ""
    postings["company"] = postings["company"].map(clean_text)
    postings = (
        postings.loc[postings["job_link"].ne("")]
        .drop_duplicates(subset=["job_link"], keep="first")
    )
    unique_job_links = len(postings)

    postings = postings.merge(
        summaries, on="job_link", how="left", validate="one_to_one"
    ).merge(
        skills, on="job_link", how="left", validate="one_to_one"
    )
    postings["job_summary"] = postings["job_summary"].fillna("").map(clean_text)
    postings["job_skills"] = postings["job_skills"].fillna("").map(clean_text)

    result = pd.DataFrame({
        "title": postings["job_title"],
        "description": postings["job_summary"],
        "company": postings["company"],
        "id": postings["job_link"],
        "job_skills": postings["job_skills"],
    })

    empty_title = result["title"].eq("")
    empty_description = result["description"].eq("")
    rows_missing_title = int(empty_title.sum())
    rows_missing_description = int(empty_description.sum())
    result = result.loc[~empty_title & ~empty_description].copy()
    result = result.drop_duplicates(subset=["id"], keep="first")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8")

    report = {
        "source_files": {name: str(path) for name, path in paths.items()},
        "postings_source_rows": postings_source_rows,
        "unique_job_links_after_deduplication": unique_job_links,
        "rows_missing_title_before_filter": rows_missing_title,
        "rows_missing_description_before_filter": rows_missing_description,
        "output_rows": int(len(result)),
        "output_rows_with_skill_labels": int(result["job_skills"].str.strip().ne("").sum()),
        "output_rows_without_skill_labels": int(result["job_skills"].str.strip().eq("").sum()),
        "output_columns": list(result.columns),
        "output_path": str(output_path),
        "note": (
            "job_skills contains source-provided labels. Inspect them for noise before "
            "training. This script does not infer or invent skill labels."
        ),
    }
    report_path = output_path.parent / "postings_organize_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("\nNext step:")
    print(
        "python -m dataset.build_postings "
        f"--input {output_path} "
        "--out dataset/processed/postings.jsonl"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="dataset/raw", help="directory with the three source CSVs")
    parser.add_argument(
        "--output",
        default="dataset/processed/organized_postings.csv",
        help="combined builder-ready CSV path",
    )
    args = parser.parse_args()
    try:
        organize(Path(args.input_dir), Path(args.output))
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
