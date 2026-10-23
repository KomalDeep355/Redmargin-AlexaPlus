
"""Optional, shadow-mode predictions from job-posting text.

This module predicts skills mentioned or implied by job descriptions.
It does NOT establish whether a GitHub repository demonstrates those skills.
It does NOT change Redmargin scores.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import logging
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = PROJECT_ROOT / "models" / "skill_extractor.joblib"


@lru_cache(maxsize=2)
def _load_artifact(model_path: str) -> dict[str, Any] | None:
    """Load the local model once. Return None if it is not available."""
    path = Path(model_path)

    if not path.is_file():
        logger.info("Skill prediction model is not installed: %s", path)
        return None

    try:
        import joblib

        artifact = joblib.load(path)
        required = {"vectorizer", "classifier", "vocabulary", "threshold"}
        if not isinstance(artifact, dict) or not required.issubset(artifact):
            logger.warning("Skill prediction artifact has an invalid format: %s", path)
            return None

        return artifact
    except (ImportError, OSError, ValueError, EOFError) as exc:
        logger.warning("Could not load skill prediction model: %s", exc)
        return None


def model_status(model_path: str | Path | None = None) -> dict[str, Any]:
    """Report availability without making predictions."""
    path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
    artifact = _load_artifact(str(path.resolve()))

    if artifact is None:
        return {
            "available": False,
            "model_path": str(path),
            "reason": "Model artifact missing, invalid, or dependencies unavailable.",
        }

    metadata = artifact.get("metadata", {})
    return {
        "available": True,
        "model_path": str(path),
        "label_count": len(artifact["vocabulary"]),
        "threshold": float(artifact["threshold"]),
        "metadata": metadata,
    }


def predict_job_skills(
    text: str,
    *,
    model_path: str | Path | None = None,
    threshold: float | None = None,
    top_k: int = 15,
) -> dict[str, Any]:
    """Predict skills from job-posting text; never scores repository evidence."""
    if not isinstance(text, str) or not text.strip():
        return {
            "available": False,
            "skills": [],
            "reason": "No job-posting text was provided.",
        }

    path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
    artifact = _load_artifact(str(path.resolve()))

    if artifact is None:
        return {
            "available": False,
            "skills": [],
            "reason": "Trained skill model is unavailable.",
        }

    if top_k < 1:
        raise ValueError("top_k must be at least 1.")

    cutoff = (
        float(artifact["threshold"])
        if threshold is None
        else float(threshold)
    )
    if not 0.0 <= cutoff <= 1.0:
        raise ValueError("threshold must be between 0 and 1.")

    vector = artifact["vectorizer"].transform([text])
    classifier = artifact["classifier"]
    probabilities = classifier.predict_proba(vector)[0]
    vocabulary = artifact["vocabulary"]

    ranked = sorted(
        (
            {"skill": str(skill), "probability": float(probability)}
            for skill, probability in zip(vocabulary, probabilities)
            if float(probability) >= cutoff
        ),
        key=lambda item: item["probability"],
        reverse=True,
    )

    return {
        "available": True,
        "threshold": cutoff,
        "skills": ranked[:top_k],
        "purpose": "job_posting_skill_prediction_only",
    }


def compare_with_deterministic_skills(
    predicted: list[dict[str, Any]],
    deterministic_skills: list[str] | set[str],
) -> dict[str, list[str]]:
    """Compare skill names only. This is diagnostic, not a scoring function."""
    normalize = lambda value: str(value).strip().casefold()

    predicted_names = {
        normalize(item.get("skill", ""))
        for item in predicted
        if item.get("skill")
    }
    deterministic_names = {
        normalize(skill)
        for skill in deterministic_skills
        if str(skill).strip()
    }

    return {
        "agreement": sorted(predicted_names & deterministic_names),
        "model_only": sorted(predicted_names - deterministic_names),
        "deterministic_only": sorted(deterministic_names - predicted_names),
    }
