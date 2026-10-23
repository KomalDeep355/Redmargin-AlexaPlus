import asyncio
import json
from datetime import datetime

import httpx
import pytest
from pydantic import ValidationError

from app import config, github_client, interview, judge, llm, main, postings, prompts, resume_verdict
from app.github_client import RepoError, RepoSnapshot
from app.planner import build_plan, load_fix_library
from app.reviewer import generate_critique, prepare_analysis
from app.schemas import InterviewRequest, JudgeRequest, ResumeVerdictRequest, ReviewRequest
from app.signals import compute_signals


def make_snapshot(tree=None, files=None):
    tree = tree or ["README.md", "src/app.py"]
    files = files or {
        "README.md": "A FastAPI project that provides repository reviews and async HTTP endpoints.",
        "src/app.py": "FastAPI asynchronous endpoint processes submitted reviews.",
    }
    return RepoSnapshot("owner", "repo", "main", "A test repo", "", 1, "", "Python", tree, False, files)


def test_request_urls_are_normalized_and_invalid_criteria_rejected():
    assert ReviewRequest(repo_url=" https://github.com/Owner/Repo.git ").repo_url == (
        "https://github.com/Owner/Repo"
    )
    assert InterviewRequest(repo_url="https://github.com/a/b/").repo_url == "https://github.com/a/b"
    assert ResumeVerdictRequest(repo_url="https://github.com/a/b").role == "fde"
    with pytest.raises(ValidationError, match="weights must add up to 100"):
        JudgeRequest(
            repo_url="https://github.com/a/b",
            criteria=[
                {"name": "Design", "weight": 40},
                {"name": "Execution", "weight": 50},
            ],
        )
    with pytest.raises(ValidationError, match="unique"):
        JudgeRequest(
            repo_url="https://github.com/a/b",
            criteria=[
                {"name": "Design", "weight": 50},
                {"name": "design", "weight": 50},
            ],
        )


def test_postings_keep_source_role_date_and_sample_separation():
    rows, metadata = postings.gather_postings("fde", [])
    assert rows and metadata["using_sample_postings"]
    assert all(row.is_sample and row.source == "stored" for row in rows)
    assert all(row.role == "fde" and row.retrieved_at for row in rows)
    assert metadata["evidence_count"] == 0
    assert all(source["is_sample"] for source in metadata["sources"])

    pasted, pasted_meta = postings.gather_postings("fde", [" A real posting "])
    assert pasted[0].text == "A real posting"
    assert pasted[0].source == "user_pasted" and not pasted[0].is_sample
    assert pasted_meta["evidence_count"] == 1
    assert datetime.fromisoformat(pasted[0].retrieved_at).tzinfo is not None
    with pytest.raises(ValueError, match="must not be empty"):
        postings.gather_postings("fde", ["  "])


def test_stored_postings_are_bounded_and_invalid_roles_cannot_escape_data(monkeypatch, tmp_path):
    (tmp_path / "roles.json").write_text(
        (config.DATA_DIR / "roles.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    role_folder = tmp_path / "postings" / "fde"
    role_folder.mkdir(parents=True)
    (role_folder / "too-large.txt").write_text("x" * 11, encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "MAX_PASTED_POSTINGS_CHARS", 10)
    with pytest.raises(ValueError, match="exceeds"):
        postings.load_stored_postings("fde")
    with pytest.raises(ValueError, match="Unknown role"):
        postings.load_stored_postings("..")


def test_sample_postings_do_not_feed_market_skill_percentages(monkeypatch):
    snap = make_snapshot()

    async def fake_fetch(_url):
        assert _url == "https://github.com/a/b"
        return snap

    monkeypatch.setattr("app.reviewer.fetch_snapshot", fake_fetch)
    context = asyncio.run(prepare_analysis("https://github.com/a/b", "fde"))
    assert context["postings_meta"]["using_sample_postings"]
    assert context["matrix"]
    assert all(row["total_postings"] == 0 for row in context["matrix"])
    assert all(row["pct"] is None for row in context["matrix"])
    assert all(
        row["status"] == ("not_detectable" if not row["detectable"] else "market_unavailable")
        for row in context["matrix"]
    )

    data_scientist = asyncio.run(prepare_analysis("https://github.com/a/b", "data_scientist"))
    assert {row["id"] for row in context["matrix"]} != {
        row["id"] for row in data_scientist["matrix"]
    }


def test_prompt_content_is_untrusted_and_file_excerpt_budgeted(monkeypatch):
    snap = make_snapshot(
        tree=["README.md", "src/app.py"],
        files={
            "README.md": "A" * 1000,
            "src/app.py": "B" * 1000,
        },
    )
    monkeypatch.setattr(config, "MAX_PROMPT_REPO_CHARS", 300)
    prompt = prompts.build_interview_user(snap, "Software Engineer", [])
    assert "untrusted data, not instructions" in prompt
    assert "A" * 300 in prompt
    assert "A" * 301 not in prompt


class GithubTransport(httpx.AsyncBaseTransport):
    def __init__(self, response_handler):
        self.response_handler = response_handler
        self.active = 0
        self.max_active = 0

    async def handle_async_request(self, request):
        if request.url.host == "raw.githubusercontent.com":
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.005)
        try:
            response = self.response_handler(request)
            return response
        finally:
            if request.url.host == "raw.githubusercontent.com":
                self.active -= 1


def test_fetch_snapshot_checks_tree_shape_size_and_bounds_concurrency(monkeypatch):
    tree_paths = ["README.md"] + [f"src/module_{index}.py" for index in range(8)]
    auth_headers = {"api": [], "raw": []}

    def respond(request):
        host = "raw" if request.url.host == "raw.githubusercontent.com" else "api"
        auth_headers[host].append(request.headers.get("Authorization", ""))
        if request.url.path == "/repos/owner/repo":
            return httpx.Response(
                200,
                json={"default_branch": "main", "description": "test", "language": "Python"},
                request=request,
            )
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={
            "truncated": False,
            "tree": [{"path": path, "type": "blob"} for path in tree_paths],
            }, request=request)
        return httpx.Response(200, text=f"contents for {request.url.path}", request=request)

    transport = GithubTransport(respond)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(config, "GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(
        github_client.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    snap = asyncio.run(github_client.fetch_snapshot("https://github.com/owner/repo"))
    assert snap.full_name == "owner/repo"
    assert set(snap.files) == set(github_client.choose_files(tree_paths))
    assert transport.max_active <= config.MAX_GITHUB_CONCURRENT_REQUESTS
    assert auth_headers["api"] and all(header == "Bearer fake-token" for header in auth_headers["api"])
    assert auth_headers["raw"] and all(not header for header in auth_headers["raw"])

    monkeypatch.setattr(config, "MAX_REPO_TREE_PATHS", 1)
    with pytest.raises(RepoError, match="too many tree entries"):
        asyncio.run(github_client.fetch_snapshot("https://github.com/owner/repo"))

    monkeypatch.setattr(config, "MAX_REPO_TREE_PATHS", 10000)
    tree_paths[:] = ["../outside.py"]
    with pytest.raises(RepoError, match="malformed paths"):
        asyncio.run(github_client.fetch_snapshot("https://github.com/owner/repo"))


def test_github_api_errors_and_raw_file_status_are_explicit(monkeypatch):
    async def check():
        rate_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    403, headers={"X-RateLimit-Remaining": "0"}, request=request
                )
            )
        )
        async with rate_client:
            with pytest.raises(RepoError, match="rate limit"):
                await github_client._get_json(rate_client, "https://api.github.com/test")

        raw_client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(404, request=request))
        )
        async with raw_client:
            with pytest.raises(RepoError, match="missing.py"):
                await github_client._get_text(
                    raw_client, "https://raw.githubusercontent.com/o/r/main/missing.py", "missing.py"
                )

        timeout_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    httpx.ConnectTimeout("timeout", request=request)
                )
            )
        )
        async with timeout_client:
            with pytest.raises(RepoError, match="timed out"):
                await github_client._get_json(timeout_client, "https://api.github.com/test")

        malformed_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="not-json", request=request)
            )
        )
        async with malformed_client:
            with pytest.raises(RepoError, match="invalid JSON"):
                await github_client._get_json(malformed_client, "https://api.github.com/test")

        bounded_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"payload": "long response"}, request=request)
            )
        )
        async with bounded_client:
            monkeypatch.setattr(config, "MAX_REPO_API_RESPONSE_BYTES", 8)
            with pytest.raises(RepoError, match="byte limit"):
                await github_client._get_json(bounded_client, "https://api.github.com/test")

            monkeypatch.setattr(config, "MAX_CHARS_PER_FILE", 5)
            assert await github_client._get_text(
                bounded_client, "https://raw.githubusercontent.com/o/r/main/file.py", "file.py"
            ) == '{"pay'

    asyncio.run(check())


def test_llm_mock_and_openai_compatible_wire_response(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    assert asyncio.run(llm.complete_json("system", "user")) is None

    received = []

    def handle(request):
        received.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    transport = httpx.MockTransport(handle)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://local.test/v1")
    monkeypatch.setattr(config, "LLM_MODEL", "test-model")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    assert asyncio.run(llm.complete_json("system", "user")) == {"ok": True}
    assert received[0]["response_format"] == {"type": "json_object"}


def test_llm_does_not_retry_unrelated_400_and_retries_format_compatibility(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return httpx.Response(400, json={"error": {"message": "invalid API key"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://local.test/v1")
    monkeypatch.setattr(config, "LLM_MODEL", "test-model")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    with pytest.raises(llm.LLMError, match="HTTP 400"):
        asyncio.run(llm.complete_json("system", "user"))
    assert len(attempts) == 1

    attempts.clear()

    def format_handler(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return httpx.Response(
                400, json={"error": {"message": "response_format json_object is not supported"}}
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(format_handler), **kwargs),
    )
    assert asyncio.run(llm.complete_json("system", "user")) == {"ok": True}
    assert len(attempts) == 2
    assert "response_format" not in attempts[1]


def test_interview_discards_invalid_file_citations(monkeypatch):
    async def fake_complete(system, user):
        assert system and user
        return {"questions": [
            {"question": "Valid question?", "why_they_ask": "Reason", "file": "src/app.py", "follow_up": "Follow up?"},
            {"question": "Invented?", "why_they_ask": "Reason", "file": "missing.py", "follow_up": "Follow up?"},
        ]}

    monkeypatch.setattr(interview.llm, "complete_json", fake_complete)
    result = asyncio.run(interview.generate_interview_questions(make_snapshot(), "Engineer", []))
    assert len(result["questions"]) == 1
    assert result["questions"][0]["evidence"] == ["src/app.py"]


def test_critique_counts_findings_separately_from_strengths_and_falls_back(monkeypatch):
    async def fake_complete(system, user):
        assert system and user
        return {
            "summary": "Summary.",
            "findings": [{"title": "Finding", "evidence": ["README.md"]}],
            "strengths": [{"title": "Strength", "evidence": ["src/app.py"]}],
        }

    monkeypatch.setattr("app.reviewer.llm.complete_json", fake_complete)
    snap = make_snapshot()
    matrix = [{
        "id": "testing",
        "label": "Automated testing",
        "total_postings": 10,
        "postings_mentioning": 10,
        "pct": 1.0,
        "evidence": [],
        "status": "gap",
    }]
    critique = asyncio.run(generate_critique(snap, "Engineer", {}, matrix, {}, 10))
    assert critique["evidence_check"]["findings_total"] == 1
    assert critique["evidence_check"]["findings_kept"] == 1
    assert critique["evidence_check"]["strengths_total"] == 1
    assert critique["evidence_check"]["strengths_kept"] == 1

    async def fail_complete(system, user):
        assert system and user
        raise llm.LLMError("The LLM request timed out.")

    monkeypatch.setattr("app.reviewer.llm.complete_json", fail_complete)
    fallback = asyncio.run(
        generate_critique(
            snap, "Engineer", {}, matrix, {"total_hours_min": 0, "total_hours_max": 0}, 10
        )
    )
    assert fallback["source"] == "rule-based (LLM failed)"
    assert fallback["llm_error"] == "The LLM request timed out."


def test_resume_bullets_reject_unsupported_metrics_and_achievements(monkeypatch):
    async def fake_complete(system, user):
        assert system and user
        return {"bullets": [
            {
                "text": "Implemented FastAPI asynchronous endpoint for submitted reviews.",
                "evidence": ["src/app.py"],
            },
            {
                "text": "Improved accuracy to 99% with FastAPI asynchronous endpoint.",
                "evidence": ["src/app.py"],
            },
        ]}

    monkeypatch.setattr(resume_verdict.llm, "complete_json", fake_complete)
    snap = make_snapshot()
    result = asyncio.run(resume_verdict.resume_verdict(snap, "Engineer", {}, []))
    assert len(result["bullets"]) == 1
    assert result["rejected_bullets"] == 1
    assert result["rejected_bullet_details"][0]["reason"]
    bullet = result["bullets"][0]
    assert bullet["claim_type"] == "implementation"
    assert bullet["verification"].endswith("runtime behavior was not verified.")
    assert bullet["evidence_confidence"] == "medium"
    assert bullet["metrics_verified"] is False
    assert result["verdict"] in {
        "strong_project_evidence", "developing_project_evidence", "limited_project_evidence"
    }


def test_resume_bullets_separate_source_and_test_evidence(monkeypatch):
    async def fake_complete(system, user):
        assert system and user
        return {"bullets": [
            {
                "text": "Implemented FastAPI asynchronous endpoint for submitted reviews.",
                "evidence": ["src/app.py"],
            },
            {
                "text": "Tested FastAPI asynchronous endpoint for submitted reviews.",
                "evidence": ["src/app.py", "tests/test_app.py"],
            },
            {
                "text": "Served thousands of users through the FastAPI endpoint.",
                "evidence": ["src/app.py"],
            },
            {
                "text": "Processed 100 reviews with the FastAPI endpoint.",
                "evidence": ["src/app.py"],
            },
        ]}

    monkeypatch.setattr(resume_verdict.llm, "complete_json", fake_complete)
    snap = make_snapshot(
        tree=["README.md", "src/app.py", "tests/test_app.py"],
        files={
            "README.md": "A FastAPI project that provides repository reviews.",
            "src/app.py": "FastAPI asynchronous endpoint processes submitted reviews.",
            "tests/test_app.py": (
                "def test_review_endpoint():\n"
                "    response = client.get('/review')\n"
                "    assert response.status_code == 200\n"
            ),
        },
    )
    result = asyncio.run(resume_verdict.resume_verdict(snap, "Engineer", {}, []))

    assert [bullet["claim_type"] for bullet in result["bullets"]] == [
        "implementation", "testing"
    ]
    assert result["bullets"][1]["evidence"] == ["tests/test_app.py"]
    assert "test execution was not verified" in result["bullets"][1]["verification"]
    assert result["rejected_bullets"] == 2
    assert all(detail["reason"] for detail in result["rejected_bullet_details"])


def test_resume_verdict_does_not_make_generic_fallback_bullets(monkeypatch):
    async def no_llm_response(system, user):
        assert system and user
        return None

    monkeypatch.setattr(resume_verdict.llm, "complete_json", no_llm_response)
    result = asyncio.run(resume_verdict.resume_verdict(make_snapshot(), "Engineer", {}, []))

    assert result["bullets"] == []
    assert "file presence alone" in result["bullet_generation_note"]


def test_judge_reports_three_run_estimate_and_deterministic_mock(monkeypatch):
    snap = make_snapshot()
    criteria = [
        {"name": "FastAPI endpoints", "description": "asynchronous reviews", "weight": 60},
        {"name": "Documentation", "description": "README", "weight": 40},
    ]
    result = asyncio.run(judge.judge_project(snap, criteria))
    assert result["estimate"] is True
    assert result["source"] == "deterministic evidence heuristic"
    assert len(result["criteria"]) == 2
    assert result["weighted_range"]["minimum"] == result["weighted_range"]["maximum"]

    run_number = 0

    async def fake_complete(system, user):
        assert system and user
        nonlocal run_number
        run_number += 1
        return {
            "criteria": [
                {"name": "FastAPI endpoints", "score": [3, 5, 7][run_number - 1], "evidence": ["src/app.py"]},
                {"name": "Documentation", "score": 6, "evidence": ["README.md"]},
            ]
        }

    monkeypatch.setattr(judge.llm, "complete_json", fake_complete)
    llm_result = asyncio.run(judge.judge_project(snap, criteria))
    assert llm_result["source"] == "llm (3 runs)"
    assert llm_result["criteria"][0]["minimum"] == 3
    assert llm_result["criteria"][0]["median"] == 5
    assert llm_result["criteria"][0]["maximum"] == 7


def test_planner_checks_fix_references_duplicates_and_daily_capacity():
    assert load_fix_library()
    duplicated_gaps = [
        {"status": "gap", "fix_id": "add_tests", "pct": 0.8, "postings_mentioning": 4,
         "total_postings": 5, "label": "Testing"},
        {"status": "gap", "fix_id": "add_tests", "pct": 0.6, "postings_mentioning": 3,
         "total_postings": 5, "label": "Quality"},
    ]
    plan = build_plan(duplicated_gaps, {}, hours_per_day=0.505)
    assert len([fix for fix in plan["fixes"] if fix["fix_id"] == "add_tests"]) == 1
    assert all(day["hours"] <= 0.505 for day in plan["timetable"])
    assert abs(sum(day["hours"] for day in plan["timetable"]) - sum(
        fix["hours_avg"] for fix in plan["fixes"]
    )) < 0.05
    assert build_plan([], {}, hours_per_day=2)["timetable"] == []


def test_planner_rejects_invalid_fix_hours_and_references(monkeypatch, tmp_path):
    fix_file = tmp_path / "fix_library.json"
    fix_file.write_text(
        json.dumps({"bad": {"title": "Broken", "hours_min": 2, "hours_max": 1, "steps": ["Fix it"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr("app.planner.load_taxonomy", lambda: [])
    with pytest.raises(ValueError, match="valid positive hour bounds"):
        load_fix_library()

    fix_file.write_text(
        json.dumps({"known": {"title": "Known", "hours_min": 1, "hours_max": 2, "steps": ["Do it"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.planner.HYGIENE", [])
    monkeypatch.setattr("app.planner.load_taxonomy", lambda: [{"fix_id": "missing"}])
    with pytest.raises(ValueError, match="missing referenced fix_id"):
        load_fix_library()


def test_fix_library_rejects_missing_steps_and_duplicate_ids(monkeypatch, tmp_path):
    fix_file = tmp_path / "fix_library.json"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr("app.planner.HYGIENE", [])
    monkeypatch.setattr("app.planner.load_taxonomy", lambda: [])

    fix_file.write_text(
        json.dumps({"known": {"title": "Known", "hours_min": 1, "hours_max": 2}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-empty list of steps"):
        load_fix_library()

    fix_file.write_text(
        '{"known":{"title":"Known","hours_min":1,"hours_max":2,"steps":["Do it"]},'
        '"known":{"title":"Duplicate","hours_min":1,"hours_max":2,"steps":["Do it"]}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Could not load"):
        load_fix_library()


def test_planner_rejects_unknown_fix_id_even_for_strength_rows():
    with pytest.raises(ValueError, match="Unknown fix_id"):
        build_plan([{
            "id": "unknown",
            "status": "strength",
            "fix_id": "missing_fix",
            "label": "Unknown",
            "pct": 0,
            "postings_mentioning": 0,
            "total_postings": 0,
        }], {}, hours_per_day=2)


def test_environment_example_fix_requires_usage_and_complete_values():
    no_environment_usage = make_snapshot(tree=["src/app.py"], files={"src/app.py": "print('hello')"})
    no_env_signals = compute_signals(no_environment_usage)
    assert not no_env_signals["requires_env_config"].value
    no_env_plan = build_plan([], no_env_signals, hours_per_day=2)
    assert "add_env_example" not in {item["fix_id"] for item in no_env_plan["fixes"]}

    incomplete = make_snapshot(
        tree=["src/app.py", ".env.example"],
        files={"src/app.py": 'import os\nkey = os.getenv("API_KEY")\n', ".env.example": "OTHER=value\n"},
    )
    incomplete_signals = compute_signals(incomplete)
    assert incomplete_signals["requires_env_config"].value
    assert not incomplete_signals["env_example_complete"].value
    incomplete_plan = build_plan([], incomplete_signals, hours_per_day=2)
    assert "add_env_example" in {item["fix_id"] for item in incomplete_plan["fixes"]}

    complete = make_snapshot(
        tree=["src/app.py", ".env.example"],
        files={"src/app.py": 'import os\nkey = os.getenv("API_KEY")\n', ".env.example": "API_KEY=placeholder\n"},
    )
    complete_signals = compute_signals(complete)
    assert complete_signals["env_example_complete"].value
    complete_plan = build_plan([], complete_signals, hours_per_day=2)
    assert "add_env_example" not in {item["fix_id"] for item in complete_plan["fixes"]}


def test_health_roles_and_review_work_in_mock_mode(monkeypatch):
    async def fake_fetch(_url):
        return make_snapshot()

    monkeypatch.setattr("app.reviewer.fetch_snapshot", fake_fetch)
    health = asyncio.run(main.health())
    assert health.status_code == 200
    assert asyncio.run(main.roles())

    result = asyncio.run(main.review(ReviewRequest(repo_url="https://github.com/owner/repo")))
    assert result["repo"]["full_name"] == "owner/repo"
    assert result["postings"]["using_sample_postings"]
    assert result["critique"]["source"] == "rule-based (no real postings)"
    assert "market demand could not be assessed" in result["critique"]["summary"]


def test_feature_endpoints_are_wired_with_typed_responses(monkeypatch):
    snap = make_snapshot()
    context = {"snapshot": snap, "role": {"label": "Engineer"}, "matrix": [], "signals": {}}

    async def fake_prepare(*args):
        assert args
        return context

    async def fake_interview(*args):
        assert args
        return {"questions": [], "source": "test"}

    async def fake_resume(*args):
        assert args
        return {
            "verdict": "developing_project_evidence",
            "explanation": "Repository evidence only.",
            "bullets": [],
            "rejected_bullets": 0,
            "source": "test",
        }

    async def fake_judge(*args):
        assert args
        return {
            "full_name": "owner/repo",
            "estimate": True,
            "criteria": [],
            "weighted_score": 5,
            "weighted_range": {"minimum": 5, "median": 5, "maximum": 5},
            "source": "test",
        }

    monkeypatch.setattr(main, "prepare_analysis", fake_prepare)
    monkeypatch.setattr(main, "generate_interview_questions", fake_interview)
    monkeypatch.setattr(main, "resume_verdict", fake_resume)

    async def fake_fetch_snapshot(*args):
        assert args
        return snap

    monkeypatch.setattr(main, "fetch_snapshot", fake_fetch_snapshot)
    monkeypatch.setattr(main, "judge_project", fake_judge)

    async def call_endpoints():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            interview_response = await client.post(
                "/api/interview", json={"repo_url": "https://github.com/a/b"}
            )
            resume_response = await client.post(
                "/api/resume-verdict", json={"repo_url": "https://github.com/a/b"}
            )
            judge_response = await client.post(
                "/api/judge",
                json={
                    "repo_url": "https://github.com/a/b",
                    "criteria": [{"name": "Design", "weight": 100}],
                },
            )
            help_response = await client.post(
                "/api/help",
                json={
                    "message": "Help me build an NLP evaluation project",
                    "role_id": "nlp",
                    "available_hours_per_day": 3,
                    "missing_skills": ["evals"],
                    "evidence_summary": "No evaluation code found.",
                },
            )
            invalid_help_response = await client.post("/api/help", json={"message": ""})
            return interview_response, resume_response, judge_response, help_response, invalid_help_response

    responses = asyncio.run(call_endpoints())
    assert all(response.status_code == 200 for response in responses[:4])
    help_result = responses[3].json()
    assert help_result["agent_name"] == "Redmargin Project Mentor"
    assert help_result["role"]["id"] == "nlp"
    assert help_result["repository_context"]["missing_skills"] == ["evals"]
    assert responses[4].status_code == 422
