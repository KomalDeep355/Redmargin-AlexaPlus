
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, llm
from .agent import build_help_response
from .github_client import RepoError, fetch_snapshot
from .interview import generate_interview_questions
from .judge import judge_project
from .postings import load_roles, load_stored_postings
from .resume_verdict import resume_verdict
from .reviewer import prepare_analysis, run_review
from .schemas import (
    AgentHelpRequest,
    AgentHelpResponse,
    InterviewRequest,
    InterviewResponse,
    JudgeRequest,
    JudgeResponse,
    ResumeVerdictRequest,
    ResumeVerdictResponse,
    ReviewRequest,
    ReviewResponse,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Redmargin Career Agent", version="0.2.0")


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    logger.error(
        "Unexpected server error while handling %s %s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred."},
    )


@app.get("/api/health")
async def health():
    checks = {
        "static_directory": config.STATIC_DIR.is_dir(),
        "index_file": (config.STATIC_DIR / "index.html").is_file(),
        "career_file": (config.STATIC_DIR / "career.html").is_file(),
        "roles_file": (config.DATA_DIR / "roles.json").is_file(),
    }

    try:
        llm.validate_provider_settings()
        checks["llm_configuration"] = True
    except llm.LLMError:
        checks["llm_configuration"] = False

    ready = all(checks.values())

    payload = {
        "ok": ready,
        "ready": ready,
        "checks": checks,
        "llm_provider": config.LLM_PROVIDER,
        "llm_model": config.LLM_MODEL,
        "github_token_set": bool(config.GITHUB_TOKEN),
    }

    return JSONResponse(
        status_code=200 if ready else 503,
        content=payload,
    )


@app.get("/api/roles")
async def roles():
    output = []

    for role in load_roles():
        stored = load_stored_postings(role["id"])

        output.append({
            **role,
            "stored_postings": len(stored),
            "stored_are_samples": (
                bool(stored)
                and all(posting.is_sample for posting in stored)
            ),
        })

    return output


@app.post("/api/help", response_model=AgentHelpResponse)
def agent_help(request: AgentHelpRequest):
    try:
        return build_help_response(
            message=request.message,
            role_id=request.role_id,
            available_hours_per_day=request.available_hours_per_day,
            missing_skills=request.missing_skills,
            evidence_summary=request.evidence_summary,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@app.post("/api/review", response_model=ReviewResponse)
async def review(req: ReviewRequest):
    try:
        return await run_review(req)
    except (RepoError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@app.post("/api/agent/review", response_model=ReviewResponse)
async def agent_review(req: ReviewRequest):
    try:
        return await run_review(req)
    except (RepoError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@app.post("/api/interview", response_model=InterviewResponse)
async def interview(req: InterviewRequest):
    try:
        context = await prepare_analysis(
            req.repo_url,
            req.role,
        )

        return await generate_interview_questions(
            context["snapshot"],
            context["role"]["label"],
            context["matrix"],
        )
    except (RepoError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@app.post("/api/resume-verdict", response_model=ResumeVerdictResponse)
async def resume(req: ResumeVerdictRequest):
    try:
        context = await prepare_analysis(
            req.repo_url,
            req.role,
        )

        return await resume_verdict(
            context["snapshot"],
            context["role"]["label"],
            context["signals"],
            context["matrix"],
        )
    except (RepoError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@app.post("/api/judge", response_model=JudgeResponse)
async def judge(req: JudgeRequest):
    try:
        snapshot = await fetch_snapshot(req.repo_url)
        criteria = [
            criterion.model_dump()
            for criterion in req.criteria
        ]

        return await judge_project(snapshot, criteria)
    except (RepoError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@app.get("/career")
async def career_agent_page():
    career_file = config.STATIC_DIR / "career.html"

    if not career_file.is_file():
        raise HTTPException(
            status_code=503,
            detail="The Career Agent page is not available.",
        )

    return FileResponse(career_file)


@app.get("/")
async def index():
    index_file = config.STATIC_DIR / "index.html"

    if not config.STATIC_DIR.is_dir() or not index_file.is_file():
        raise HTTPException(
            status_code=503,
            detail="The static frontend is not available.",
        )

    return FileResponse(index_file)


app.mount(
    "/static",
    StaticFiles(directory=config.STATIC_DIR, check_dir=False),
    name="static",
)
