
from app.skill_predictor import (
    compare_with_deterministic_skills,
    model_status,
    predict_job_skills,
)


def test_missing_model_fails_gracefully(tmp_path):
    missing = tmp_path / "does-not-exist.joblib"

    status = model_status(missing)
    result = predict_job_skills(
        "Python, SQL, and machine learning experience required.",
        model_path=missing,
    )

    assert status["available"] is False
    assert result["available"] is False
    assert result["skills"] == []


def test_empty_job_text_returns_no_predictions(tmp_path):
    missing_model = tmp_path / "does-not-exist.joblib"

    result = predict_job_skills("", model_path=missing_model)

    assert result == {
        "available": False,
        "skills": [],
        "reason": "No job-posting text was provided.",
    }
    assert result["skills"] == []


def test_skill_comparison_does_not_change_scores():
    result = compare_with_deterministic_skills(
        [
            {"skill": "python", "probability": 0.9},
            {"skill": "docker", "probability": 0.8},
        ],
        {"Python", "SQL"},
    )

    assert result["agreement"] == ["python"]
    assert result["model_only"] == ["docker"]
    assert result["deterministic_only"] == ["sql"]
