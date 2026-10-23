import csv
import json
import sys

import pytest

from app import config
from dataset.baseline_skill_extractor import main as run_skill_baseline
from dataset.baseline_skill_extractor import select_training_vocabulary
from dataset import build_postings, check_processed, compare_skill_taxonomy
from dataset.build_postings import parse_skills
from dataset.common import assign_split
from dataset.eval_gold import evaluate_rows


def test_gold_evaluation_preserves_zero_scores_and_ignores_unlabelled_rows(capsys):
    report = evaluate_rows(
        [
            {"pred_has_tests": "y", "human_has_tests": "n"},
            {"pred_has_tests": "n", "human_has_tests": "y"},
            {"pred_has_tests": "n", "human_has_tests": ""},
        ],
        ["pred_has_tests", "human_has_tests"],
    )

    assert report["has_tests"] == {
        "n": 2,
        "accuracy": 0.0,
        "precision": 0.0,
        "recall": 0.0,
        "tp": 0,
        "fp": 1,
        "fn": 1,
        "tn": 0,
    }
    assert "0.00" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("rows", "fieldnames", "message"),
    [
        ([], None, "empty or has no header"),
        ([], ["pred_signal"], "matching pred_"),
        ([{"pred_signal": "maybe", "human_signal": "y"}],
         ["pred_signal", "human_signal"], "Invalid prediction"),
    ],
)
def test_gold_evaluation_rejects_invalid_input(rows, fieldnames, message):
    with pytest.raises(ValueError, match=message):
        evaluate_rows(rows, fieldnames)


def test_training_label_vocabulary_does_not_depend_on_test_labels():
    training = [
        {"skills": ["Python", "SQL"]},
        {"skills": ["python"]},
    ]

    assert select_training_vocabulary(training, top_k=10) == ["python", "sql"]


def test_skill_baseline_saves_fitted_artifact_with_validation_threshold(tmp_path, monkeypatch):
    postings_path = tmp_path / "postings.jsonl"
    rows = []
    for split, count in (("train", 70), ("val", 15), ("test", 15)):
        for index in range(count):
            skill = "python" if index % 2 == 0 else "sql"
            text = (
                "python application engineering"
                if skill == "python"
                else "sql database engineering"
            )
            rows.append({
                "id": f"{split}-{index}",
                "company": f"{split}-{index}",
                "role": "swe",
                "split": split,
                "text": text,
                "skills": [skill],
            })
    postings_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_path = tmp_path / "baseline_report.json"
    model_path = tmp_path / "nested" / "skill_extractor.joblib"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "baseline_skill_extractor",
            "--postings", str(postings_path),
            "--top-k", "2",
            "--min-df", "1",
            "--out", str(report_path),
            "--model-out", str(model_path),
        ],
    )

    run_skill_baseline()

    import joblib

    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifact = joblib.load(model_path)
    assert artifact["threshold"] == report["selected_threshold"]
    assert artifact["vocabulary"] == ["python", "sql"]
    assert artifact["metadata"] == {
        "method": "TF-IDF word/bigram + one-vs-rest logistic regression",
        "train_size": 70,
        "validation_size": 15,
        "labels": 2,
        "threshold_selection": "validation split",
        "purpose": "job-posting skill extraction",
        "version": 1,
    }
    assert artifact["vectorizer"].transform(["python application engineering"]).shape[1] > 0
    assert artifact["classifier"].predict(
        artifact["vectorizer"].transform(["python application engineering"])
    ).shape == (1, 2)


def test_taxonomy_comparison_normalizes_aliases_and_flags_ambiguous_labels():
    report = {
        "per_label": {
            "scikitlearn": {"support": 3, "f1-score": 0.5},
            "transformers": {"support": 7, "f1-score": 0.2},
            "unknown skill": {"support": 1, "f1-score": 0.0},
        }
    }
    taxonomy = [
        {"id": "ml_frameworks", "label": "ML frameworks", "synonyms": ["scikit-learn"]},
        {"id": "nlp", "label": "NLP", "synonyms": ["transformers"]},
        {"id": "llm_apis", "label": "LLM APIs", "synonyms": ["transformers"]},
    ]

    result = compare_skill_taxonomy.compare_labels(report, taxonomy)

    assert result["matched_count"] == 1
    assert result["matched"][0]["canonical_skill"]["id"] == "ml_frameworks"
    assert result["ambiguous_count"] == 1
    assert {
        item["id"] for item in result["ambiguous_for_manual_review"][0]["candidates"]
    } == {"nlp", "llm_apis"}
    assert any(
        item["normalized_alias"] == "transformers"
        for item in result["taxonomy_alias_collisions_for_manual_review"]
    )
    assert result["unmatched_count"] == 1
    assert result["unmatched_for_manual_review"][0]["source_label"] == "unknown skill"


def test_skill_parser_supports_json_and_delimited_source_labels():
    assert parse_skills('["Python", "SQL", "Python"]') == ["Python", "SQL"]
    assert parse_skills("Python; SQL | Docker") == ["Python", "SQL", "Docker"]


def test_split_is_stable_for_shared_company():
    assert assign_split("Example Corp") == assign_split(" example corp ")


def test_processed_postings_check_reports_schema_and_distributions(tmp_path):
    path = tmp_path / "postings.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({
                "title": "Software Engineer",
                "text": "Build dependable services.",
                "skills": ["Python", "SQL"],
                "role": "swe",
                "split": "train",
            }),
            json.dumps({
                "title": "Data Scientist",
                "text": "Analyze data.",
                "skills": [],
                "role": "data_scientist",
                "split": "test",
            }),
        ]),
        encoding="utf-8",
    )

    report = check_processed.inspect_postings(path)

    assert report["total_rows"] == 2
    assert report["populated_fields"] == {
        "title": 2,
        "text": 2,
        "skills": 1,
        "role": 2,
        "split": 2,
    }
    assert report["split_distribution"] == {"test": 1, "train": 1}
    assert report["role_distribution"] == {"data_scientist": 1, "swe": 1}
    assert report["rows_with_skill_labels"] == 1
    assert report["distinct_skill_labels"] == 2


@pytest.mark.parametrize(
    ("contents", "message"),
    [("{bad json}\n", "Invalid JSON on line 1"), ('["not", "an object"]\n', "Expected a JSON object")],
)
def test_processed_postings_check_rejects_malformed_records(tmp_path, contents, message):
    path = tmp_path / "postings.jsonl"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        check_processed.inspect_postings(path)


def test_job_data_organizer_joins_deduplicates_and_reports_exclusions(tmp_path):
    input_dir = tmp_path / "raw"
    input_dir.mkdir()
    pd = pytest.importorskip("pandas")
    from dataset.organize_job_data import organize

    pd.DataFrame([
        {"job_link": " job-1 ", "job_title": " Engineer ", "company": "Example"},
        {"job_link": "job-1", "job_title": "Duplicate title", "company": "Other"},
        {"job_link": "job-2", "job_title": "", "company": "Missing title"},
        {"job_link": "job-3", "job_title": "Analyst", "company": "Missing description"},
        {"job_link": "", "job_title": "No link", "company": "Other"},
    ]).to_csv(input_dir / "job_postings.csv", index=False)
    pd.DataFrame([
        {"job_link": "job-1", "job_summary": ""},
        {"job_link": "job-1", "job_summary": "First available description"},
        {"job_link": "job-1", "job_summary": "Later description"},
        {"job_link": "job-2", "job_summary": "Should be excluded because title is empty"},
    ]).to_csv(input_dir / "job_summary.csv", index=False)
    pd.DataFrame([
        {"job_link": "job-1", "job_skills": "Python, SQL"},
        {"job_link": "job-1", "job_skills": "python, Docker"},
    ]).to_csv(input_dir / "job_skills.csv", index=False)

    output_path = tmp_path / "processed" / "organized.csv"
    report = organize(input_dir, output_path)
    output = pd.read_csv(output_path, keep_default_na=False)

    assert report["postings_source_rows"] == 5
    assert report["unique_job_links_after_deduplication"] == 3
    assert report["rows_missing_title_before_filter"] == 1
    assert report["rows_missing_description_before_filter"] == 1
    assert report["output_rows"] == 1
    assert report["output_rows_with_skill_labels"] == 1
    assert output.to_dict("records") == [{
        "title": "Engineer",
        "description": "First available description",
        "company": "Example",
        "id": "job-1",
        "job_skills": "Python, SQL, Docker",
    }]
    assert (output_path.parent / "postings_organize_report.json").is_file()


def test_posting_builder_deduplicates_and_groups_company_splits(monkeypatch, tmp_path):
    source = tmp_path / "jobs.csv"
    with source.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["title", "description", "company", "job_skills"])
        writer.writeheader()
        writer.writerows([
            {
                "title": "Software Engineer",
                "description": "Build reliable services with Python and robust automated testing.",
                "company": "Example Corp",
                "job_skills": '["Python", "SQL"]',
            },
            {
                "title": "Software Engineer",
                "description": "Build reliable services with Python and robust automated testing.",
                "company": "Different Corp",
                "job_skills": "Python, SQL",
            },
            {
                "title": "Software Engineer",
                "description": "Design maintainable APIs and deploy cloud services for customers.",
                "company": "Example Corp",
                "job_skills": "FastAPI; Docker",
            },
        ])
    output_path = tmp_path / "processed" / "postings.jsonl"
    monkeypatch.setattr(build_postings, "PROCESSED", tmp_path / "processed")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "app-data")
    monkeypatch.setattr(
        "sys.argv",
        ["build_postings", "--input", str(source), "--min-chars", "10", "--out", str(output_path)],
    )

    build_postings.main()

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    same_company = [row for row in rows if row["company"] == "Example Corp"]
    assert len({row["split"] for row in same_company}) == 1
    assert rows[0]["skills"] == ["Python", "SQL"]
