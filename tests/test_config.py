import importlib

import pytest
from pydantic import ValidationError

from app import config
from app.schemas import ReviewRequest


def test_invalid_numeric_environment_values_warn_and_use_defaults(monkeypatch, caplog):
    values = {
        "LLM_TIMEOUT_SECONDS": "nan",
        "MAX_SOURCE_FILES": "0",
        "MAX_CHARS_PER_FILE": "not-a-number",
        "MAX_PASTED_POSTINGS_CHARS": "-1",
        "MAX_REPO_URL_CHARS": "inf",
    }
    try:
        with monkeypatch.context() as patch:
            for name, value in values.items():
                patch.setenv(name, value)
            loaded = importlib.reload(config)

            assert loaded.LLM_TIMEOUT_SECONDS == 90.0
            assert loaded.MAX_SOURCE_FILES == 12
            assert loaded.MAX_CHARS_PER_FILE == 6000
            assert loaded.MAX_PASTED_POSTINGS_CHARS == 50000
            assert loaded.MAX_REPO_URL_CHARS == 2048
            assert "using default" in caplog.text
    finally:
        importlib.reload(config)


def test_review_request_limits_repository_url_and_combined_postings():
    ReviewRequest(
        repo_url="https://github.com/a/b",
        pasted_postings=["x" * config.MAX_PASTED_POSTINGS_CHARS],
    )

    with pytest.raises(ValidationError, match="repo_url"):
        ReviewRequest(repo_url="x" * (config.MAX_REPO_URL_CHARS + 1))

    with pytest.raises(ValidationError, match="pasted_postings"):
        ReviewRequest(repo_url="https://github.com/a/b", pasted_postings=[" "])

    with pytest.raises(ValidationError, match="Combined pasted job descriptions"):
        ReviewRequest(
            repo_url="https://github.com/a/b",
            pasted_postings=["x" * config.MAX_PASTED_POSTINGS_CHARS, "x"],
        )
