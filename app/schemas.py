import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import config
from .github_client import RepoError, parse_repo_url


class RepositoryRoleRequest(BaseModel):
    repo_url: str = Field(..., max_length=config.MAX_REPO_URL_CHARS)
    role: str = Field("fde", min_length=1, max_length=64, description="id from data/roles.json")

    @field_validator("repo_url")
    @classmethod
    def normalize_repo_url(cls, value: str) -> str:
        try:
            owner, name = parse_repo_url(value)
        except RepoError as exc:
            raise ValueError(str(exc)) from exc
        return f"https://github.com/{owner}/{name}"

    @field_validator("role")
    @classmethod
    def normalize_role(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Role must not be empty.")
        return normalized


class ReviewRequest(RepositoryRoleRequest):
    pasted_postings: list[str] = Field(
        default_factory=list,
        max_length=config.MAX_POSTINGS,
        description="Full text of real job postings",
    )
    hours_per_day: float = Field(2.0, ge=0.5, le=12)

    @field_validator("pasted_postings")
    @classmethod
    def validate_individual_postings(cls, values: list[str]) -> list[str]:
        normalized = []
        for posting in values:
            text = posting.strip()
            if not text:
                raise ValueError("Pasted job descriptions must not be empty.")
            if len(text) > config.MAX_PASTED_POSTINGS_CHARS:
                raise ValueError(
                    f"Each pasted job description must be at most "
                    f"{config.MAX_PASTED_POSTINGS_CHARS} characters."
                )
            normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def validate_pasted_postings_size(self):
        total_chars = sum(len(posting) for posting in self.pasted_postings)
        if total_chars > config.MAX_PASTED_POSTINGS_CHARS:
            raise ValueError(
                "Combined pasted job descriptions exceed the "
                f"{config.MAX_PASTED_POSTINGS_CHARS}-character limit."
            )
        return self


class ReviewResponse(BaseModel):
    repository_score: float | None
    market_comparison_available: bool
    real_posting_count: int = Field(ge=0)

    model_config = ConfigDict(extra="allow")


class InterviewRequest(RepositoryRoleRequest):
    pass


class ResumeVerdictRequest(RepositoryRoleRequest):
    pass


class JudgeCriterion(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=1000)
    weight: float = Field(..., gt=0, le=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Criterion weights must be finite numbers.")
        return value


class JudgeRequest(BaseModel):
    repo_url: str = Field(..., max_length=config.MAX_REPO_URL_CHARS)
    criteria: list[JudgeCriterion] = Field(..., min_length=1, max_length=20)

    @field_validator("repo_url")
    @classmethod
    def normalize_repo_url(cls, value: str) -> str:
        try:
            owner, name = parse_repo_url(value)
        except RepoError as exc:
            raise ValueError(str(exc)) from exc
        return f"https://github.com/{owner}/{name}"

    @model_validator(mode="after")
    def validate_criteria(self):
        names = [criterion.name.casefold() for criterion in self.criteria]
        if any(not name for name in names):
            raise ValueError("Judging criteria names must not be blank.")
        if len(names) != len(set(names)):
            raise ValueError("Judging criteria names must be unique.")
        total_weight = sum(criterion.weight for criterion in self.criteria)
        if abs(total_weight - 100.0) > 0.01:
            raise ValueError("Judging criteria weights must add up to 100.")
        return self


class InterviewQuestion(BaseModel):
    question: str
    why_they_ask: str
    evidence: list[str]
    follow_up: str


class InterviewResponse(BaseModel):
    questions: list[InterviewQuestion]
    source: str
    llm_error: str | None = None


class ResumeBullet(BaseModel):
    text: str
    evidence: list[str]
    claim_type: str = "implementation"
    verification: str = "Supported by inspected source code; runtime behavior was not verified."
    metrics_verified: bool = False
    evidence_confidence: str = "medium"


class ResumeVerdictResponse(BaseModel):
    verdict: str
    explanation: str
    bullets: list[ResumeBullet]
    rejected_bullets: int
    source: str
    rejected_bullet_details: list[dict[str, str]] = Field(default_factory=list)
    bullet_generation_note: str | None = None
    llm_error: str | None = None


class JudgeCriterionResult(BaseModel):
    name: str
    weight: float
    score: float = Field(..., ge=0, le=10)
    minimum: float = Field(..., ge=0, le=10)
    median: float = Field(..., ge=0, le=10)
    maximum: float = Field(..., ge=0, le=10)
    evidence: list[str]


class JudgeResponse(BaseModel):
    full_name: str
    estimate: bool = True
    criteria: list[JudgeCriterionResult]
    weighted_score: float = Field(..., ge=0, le=10)
    weighted_range: dict[str, float]
    source: str
    llm_error: str | None = None


class AgentHelpRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    role_id: str = "nlp"
    available_hours_per_day: float = Field(default=2, ge=0.5, le=16)
    missing_skills: list[str] = Field(default_factory=list, max_length=100)
    evidence_summary: str = Field(default="", max_length=5000)


class AgentHelpResponse(BaseModel):
    agent_name: str
    intent: str
    role: dict
    goal: str
    recommended_skill: dict
    project: dict
    resources: list[dict]
    resume_evidence: list[str]
    repository_context: dict
    limitations: list[str]
