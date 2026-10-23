
from app.agent import (
    build_help_response,
    classify_goal,
    load_resources,
    select_resources,
)


def test_llm_goal_returns_learning_resources_and_roadmap():
    result = build_help_response(
        message="I want to learn LLMs and build an NLP project",
        role_id="nlp",
        available_hours_per_day=2,
        missing_skills=["llm_apis"],
        evidence_summary="No LLM integration was found in inspected files.",
    )

    assert result["recommended_skill"]["id"] == "llm_apis"
    assert result["goal"] == "I want to learn LLMs and build an NLP project"
    assert result["repository_context"]["missing_skills"] == ["llm_apis"]
    assert "inspected files" in result["repository_context"]["evidence_summary"]
    assert result["project"]["tasks"]
    assert result["resources"]
    assert result["resume_evidence"]
    assert result["project"]["estimated_days_at_available_pace"] > 0


def test_agent_goal_uses_agent_template():
    result = build_help_response(
        message="I want to build an AI agent with tools",
        role_id="fde",
    )

    assert result["recommended_skill"]["id"] == "agents"
    assert "agent" in result["project"]["title"].lower()


def test_api_goal_uses_api_template():
    assert classify_goal("Help me build a FastAPI backend") == "rest_apis"


def test_review_context_does_not_override_explicit_goal():
    result = build_help_response(
        message="Help me build a FastAPI backend",
        role_id="swe",
        missing_skills=["testing"],
        evidence_summary="No tests were found in inspected files.",
    )

    assert result["goal"] == "Help me build a FastAPI backend"
    assert result["recommended_skill"]["id"] == "rest_apis"
    assert result["repository_context"]["missing_skills"] == ["testing"]
    assert result["repository_context"]["evidence_summary"] == "No tests were found in inspected files."


def test_resources_have_https_urls_and_skill_tags():
    resources = load_resources()

    assert resources
    assert all(item["url"].startswith("https://") for item in resources)
    assert all(item["skills"] for item in resources)


def test_llm_resources_match_requested_skill():
    resources = select_resources("llm_apis")

    assert resources
    assert any("Hugging Face" in item["title"] for item in resources)


def test_invalid_role_falls_back_safely():
    result = build_help_response(
        message="I want to learn LLMs",
        role_id="unknown_role",
    )

    assert result["role"]["id"] == "nlp"


def test_blank_message_is_rejected():
    try:
        build_help_response("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("Blank messages should be rejected.")
