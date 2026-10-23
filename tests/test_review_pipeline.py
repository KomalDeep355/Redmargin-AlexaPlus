import asyncio
import shutil

import pytest
from fastapi import HTTPException

from app import config, main
from app.github_client import RepoSnapshot
from app.reviewer import calculate_repository_score, prepare_analysis
from app.schemas import ReviewRequest, ReviewResponse
from app.postings import MIN_MARKET_POSTINGS


def make_snapshot():
    return RepoSnapshot(
        owner="example",
        name="project",
        branch="main",
        description="A small sample repository",
        homepage="",
        stars=0,
        pushed_at="",
        primary_language="Python",
        tree=["README.md", "main.py", "requirements.txt"],
        tree_truncated=False,
        files={
            "README.md": "A sample repository.",
            "main.py": "print('hello')",
            "requirements.txt": "fastapi\n",
        },
    )


@pytest.mark.parametrize("role_id", ["fde", "data_scientist", "swe", "nlp"])
def test_same_repository_uses_the_selected_role_skill_scope(monkeypatch, role_id):
    async def fake_fetch(_url):
        return make_snapshot()

    monkeypatch.setattr("app.reviewer.fetch_snapshot", fake_fetch)
    result = asyncio.run(prepare_analysis("https://github.com/example/project", role_id))
    configured_role = next(role for role in main.load_roles() if role["id"] == role_id)

    assert result["role"]["id"] == role_id
    assert {row["id"] for row in result["matrix"]} == set(configured_role["skill_weights"])
    assert all(row["total_postings"] == 0 and row["pct"] is None for row in result["matrix"])


def test_sample_only_review_has_no_market_based_score_inputs_or_findings(monkeypatch):
    async def fake_fetch(_url):
        return make_snapshot()

    async def unexpected_llm_call(*_args):
        raise AssertionError("LLM must not produce a market summary without real postings")

    monkeypatch.setattr("app.reviewer.fetch_snapshot", fake_fetch)
    monkeypatch.setattr("app.reviewer.llm.complete_json", unexpected_llm_call)
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    result = asyncio.run(
        main.review(ReviewRequest(repo_url="https://github.com/example/project", role="swe"))
    )
    assert result["postings"]["using_sample_postings"]
    assert result["postings"]["evidence_count"] == 0
    assert result["postings"]["sample_count"] > 0
    assert result["repository_score"] is not None
    assert result["market_comparison_available"] is False
    assert result["real_posting_count"] == 0
    response = ReviewResponse.model_validate(result).model_dump()
    assert response["repository_score"] == result["repository_score"]
    assert response["skill_matrix"] == result["skill_matrix"]
    assert all(
        row["market_status"] == "insufficient_market_data"
        for row in result["skill_matrix"]
    )
    assert all(
        row["status"] == ("not_detectable" if not row["detectable"] else "market_unavailable")
        for row in result["skill_matrix"]
    )
    assert all(row["pct"] is None for row in result["skill_matrix"])
    assert "market demand could not be assessed" in result["critique"]["summary"]
    assert not any(
        finding["title"].startswith("No repository evidence found")
        for finding in result["critique"]["findings"]
    )


def test_repository_score_uses_only_assessed_repository_evidence():
    score = calculate_repository_score([
        {"detectable": True, "evidence_status": "evidence_found"},
        {"detectable": True, "evidence_status": "no_evidence_found"},
        {"detectable": False, "evidence_status": "not_assessed"},
    ])

    assert score == 5.0


def test_repository_score_is_explicitly_unavailable_without_assessed_skills():
    assert calculate_repository_score([
        {"detectable": False, "evidence_status": "not_assessed"},
    ]) is None


def test_review_with_no_postings_returns_clear_client_error(monkeypatch, tmp_path):
    shutil.copyfile(config.DATA_DIR / "roles.json", tmp_path / "roles.json")
    (tmp_path / "postings" / "swe").mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.review(
            ReviewRequest(repo_url="https://github.com/example/project", role="swe")
        ))

    assert error.value.status_code == 400
    assert "No job postings found for this role" in error.value.detail


def test_review_with_real_posting_produces_market_data_and_supported_gap(monkeypatch):
    async def fake_fetch(_url):
        return make_snapshot()

    monkeypatch.setattr("app.reviewer.fetch_snapshot", fake_fetch)
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    posting = (
        "Software Engineer: Python, REST APIs, SQL, unit tests, automated testing, "
        "continuous integration, Docker, cloud deployment, and logging."
    )
    result = asyncio.run(main.review(ReviewRequest(
        repo_url="https://github.com/example/project",
        role="swe",
        pasted_postings=[posting] * MIN_MARKET_POSTINGS,
    )))

    assert result["postings"]["evidence_count"] == MIN_MARKET_POSTINGS
    assert result["postings"]["sample_count"] == 0
    assert result["real_posting_count"] == MIN_MARKET_POSTINGS
    assert result["market_comparison_available"] is True
    assert isinstance(result["repository_score"], float)
    assert all(row["total_postings"] == MIN_MARKET_POSTINGS for row in result["skill_matrix"])
    testing = next(row for row in result["skill_matrix"] if row["id"] == "testing")
    assert testing["postings_mentioning"] == MIN_MARKET_POSTINGS
    assert testing["pct"] == 1.0
    assert testing["status"] == "gap"
    assert any(
        finding["title"] == "No repository evidence found for Automated testing"
        for finding in result["critique"]["findings"]
    )
    expected_gap_count = sum(row["status"] == "gap" for row in result["skill_matrix"])
    assert f"{expected_gap_count} in-demand skill(s)" in result["critique"]["summary"]


def test_few_real_postings_are_disclosed_without_confident_gaps(monkeypatch):
    async def fake_fetch(_url):
        return make_snapshot()

    async def unexpected_llm_call(*_args):
        raise AssertionError("LLM must not produce market claims with insufficient postings")

    monkeypatch.setattr("app.reviewer.fetch_snapshot", fake_fetch)
    monkeypatch.setattr("app.reviewer.llm.complete_json", unexpected_llm_call)
    result = asyncio.run(main.review(ReviewRequest(
        repo_url="https://github.com/example/project",
        role="swe",
        pasted_postings=["Python, REST APIs, SQL, unit tests, and Docker."],
    )))

    assert result["postings"]["evidence_count"] == 1
    assert result["real_posting_count"] == 1
    assert result["market_comparison_available"] is False
    assert "at least 10 are needed for a market comparison" in result["critique"]["summary"]
    assert result["critique"]["source"] == "rule-based (insufficient market data)"
    assert all(row["market_status"] == "insufficient_market_data" for row in result["skill_matrix"])
    assert not any(row["status"] == "gap" for row in result["skill_matrix"])
    assert not any(
        finding["title"].startswith("No repository evidence found")
        for finding in result["critique"]["findings"]
    )


def test_real_postings_without_role_skill_matches_do_not_claim_zero_market_gaps(monkeypatch):
    async def fake_fetch(_url):
        return make_snapshot()

    async def unexpected_llm_call(*_args):
        raise AssertionError("LLM must not infer role demand when no configured skills matched")

    monkeypatch.setattr("app.reviewer.fetch_snapshot", fake_fetch)
    monkeypatch.setattr("app.reviewer.llm.complete_json", unexpected_llm_call)
    result = asyncio.run(main.review(ReviewRequest(
        repo_url="https://github.com/example/project",
        role="swe",
        pasted_postings=[
            "Seeking a curious person for a collaborative and exciting opportunity."
        ] * MIN_MARKET_POSTINGS,
    )))

    assert result["postings"]["evidence_count"] == MIN_MARKET_POSTINGS
    assert result["market_comparison_available"] is False
    assert all(row["postings_mentioning"] == 0 for row in result["skill_matrix"])
    assert "none matched the configured role skills" in result["critique"]["summary"]
    assert result["critique"]["source"] == "rule-based (no matched role skills)"
    assert not any(
        finding["title"].startswith("No repository evidence found")
        for finding in result["critique"]["findings"]
    )
