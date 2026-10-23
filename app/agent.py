
"""Repository-aware career and project planning agent.

V1 uses curated resources and deterministic planning.
It does not claim to browse the live web or modify repositories.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESOURCE_FILE = ROOT / "data" / "agent_resources.json"

ROLE_LABELS = {
    "fde": "Forward Deployed Engineer",
    "data_scientist": "Data Scientist",
    "swe": "Software Engineer",
    "nlp": "NLP / ML Engineer",
}

SKILL_LABELS = {
    "llm_apis": "LLM application development",
    "rag": "Retrieval-augmented generation",
    "agents": "AI agents and tool calling",
    "ml_frameworks": "Machine learning frameworks",
    "nlp": "Natural language processing",
    "data_analysis": "Data analysis",
    "data_viz": "Data visualization",
    "sql_databases": "SQL and databases",
    "rest_apis": "REST API development",
    "testing": "Automated testing",
    "evals": "Model evaluation",
    "statistics_experimentation": "Statistics and experimentation",
    "ml_deployment": "ML deployment",
    "ci_cd": "CI/CD",
    "docker": "Docker and containers",
    "documentation": "Technical documentation",
}

# Estimates are planning estimates, not guarantees.
PROJECT_TEMPLATES = {
    "llm_apis": {
        "title": "Document question-answering assistant",
        "total_hours": "14–22",
        "why": "Demonstrates practical LLM integration and grounded responses.",
        "tasks": [
            {
                "name": "Learn model inference and prompting",
                "hours": "2–4",
                "required": True,
                "deliverable": "A small working example using a model API or local model."
            },
            {
                "name": "Load documents and split them into chunks",
                "hours": "2–3",
                "required": True,
                "deliverable": "A repeatable document ingestion module."
            },
            {
                "name": "Add embeddings and retrieval",
                "hours": "3–4",
                "required": True,
                "deliverable": "A retriever that returns relevant source passages."
            },
            {
                "name": "Generate answers with source references",
                "hours": "2–4",
                "required": True,
                "deliverable": "Answers grounded in retrieved passages, with an abstention path."
            },
            {
                "name": "Write tests and evaluate failure cases",
                "hours": "2–3",
                "required": True,
                "deliverable": "A test set covering correct answers, missing answers, and citations."
            },
            {
                "name": "Add a polished UI, Docker, or deployment",
                "hours": "3–4",
                "required": False,
                "deliverable": "A reproducible demo with setup instructions."
            }
        ]
    },
    "agents": {
        "title": "Tool-Using Research Agent",
        "total_hours": "10–16",
        "why": "Demonstrates structured planning, tool use, and verification.",
        "tasks": [
            {
                "name": "Define a narrow user goal and allowed tools",
                "hours": "1–2",
                "required": True,
                "deliverable": "Documented inputs, outputs, and tool boundaries."
            },
            {
                "name": "Implement resource search and retrieval",
                "hours": "2–3",
                "required": True,
                "deliverable": "A tool that returns relevant resources with URLs."
            },
            {
                "name": "Create a plan and execute approved steps",
                "hours": "3–4",
                "required": True,
                "deliverable": "A traceable plan with tool results."
            },
            {
                "name": "Add error handling and evaluation tests",
                "hours": "2–3",
                "required": True,
                "deliverable": "Tests for missing resources and failed tool calls."
            },
            {
                "name": "Add UI polish and observability",
                "hours": "2–4",
                "required": False,
                "deliverable": "Visible progress, logs, and a clear final summary."
            }
        ]
    },
    "rest_apis": {
        "title": "Production-style REST API",
        "total_hours": "8–14",
        "why": "Demonstrates API design, validation, tests, and maintainability.",
        "tasks": [
            {
                "name": "Design validated request and response schemas",
                "hours": "1–2",
                "required": True,
                "deliverable": "Typed API contracts and documented error responses."
            },
            {
                "name": "Implement endpoints and input validation",
                "hours": "2–4",
                "required": True,
                "deliverable": "Working endpoints with consistent status codes."
            },
            {
                "name": "Add automated endpoint tests",
                "hours": "2–3",
                "required": True,
                "deliverable": "Tests for success, invalid input, and failure cases."
            },
            {
                "name": "Add CI, logging, and deployment instructions",
                "hours": "3–5",
                "required": False,
                "deliverable": "A reproducible development and release workflow."
            }
        ]
    },
    "ml_frameworks": {
        "title": "Evaluated machine-learning prediction service",
        "total_hours": "12–18",
        "why": "Demonstrates a complete ML workflow rather than only importing a model.",
        "tasks": [
            {
                "name": "Define a baseline and evaluation split",
                "hours": "2–3",
                "required": True,
                "deliverable": "A reproducible baseline and leakage-aware data split."
            },
            {
                "name": "Train and compare candidate models",
                "hours": "3–5",
                "required": True,
                "deliverable": "Saved metrics and a documented model-selection decision."
            },
            {
                "name": "Build a prediction interface",
                "hours": "2–3",
                "required": True,
                "deliverable": "Validated inputs and model predictions."
            },
            {
                "name": "Test, document, and package the model",
                "hours": "3–4",
                "required": True,
                "deliverable": "Tests, reproducible setup, and model limitations."
            },
            {
                "name": "Add monitoring or a demo dashboard",
                "hours": "2–3",
                "required": False,
                "deliverable": "A simple view of prediction quality or model behavior."
            }
        ]
    }
}


def load_resources() -> list[dict[str, Any]]:
    """Load curated resources; fail clearly if the catalog is invalid."""
    with RESOURCE_FILE.open(encoding="utf-8") as file:
        resources = json.load(file)

    if not isinstance(resources, list):
        raise ValueError("Agent resource catalog must be a JSON list.")

    for resource in resources:
        url = resource.get("url", "")
        if not (
            isinstance(url, str)
            and url.startswith("https://")
            and resource.get("title")
            and isinstance(resource.get("skills"), list)
        ):
            raise ValueError(f"Invalid resource entry: {resource!r}")

    return resources


def classify_goal(message: str) -> str:
    """Map a user request to a supported planning template."""
    text = message.lower()

    if any(word in text for word in (
        "agent", "tool calling", "tool-calling", "agentic"
    )):
        return "agents"

    if any(word in text for word in (
        "llm", "large language model", "rag",
        "retrieval", "embeddings", "chatbot", "generative ai"
    )):
        return "llm_apis"

    if any(word in text for word in (
        "machine learning", "train a model", "classification",
        "prediction model", "scikit-learn", "ml model"
    )):
        return "ml_frameworks"

    if any(word in text for word in (
        "api", "fastapi", "backend", "rest endpoint"
    )):
        return "rest_apis"

    return "llm_apis"


def select_resources(
    skill_id: str,
    limit: int = 5,
) -> list[dict[str, str]]:
    """Return curated links that explicitly match the requested skill."""
    resources = load_resources()
    matches = [
        resource for resource in resources
        if skill_id in resource.get("skills", [])
    ]

    return [
        {
            "title": resource["title"],
            "url": resource["url"],
            "type": resource["type"],
            "level": resource["level"],
            "description": resource["description"],
        }
        for resource in matches[:max(1, min(limit, 10))]
    ]


def build_help_response(
    message: str,
    role_id: str = "nlp",
    available_hours_per_day: float = 2,
    missing_skills: list[str] | None = None,
    evidence_summary: str = "",
) -> dict[str, Any]:
    """Create a structured career/project plan from user and review context."""
    message = message.strip()

    if not message:
        raise ValueError("Please enter a question or project goal.")

    if role_id not in ROLE_LABELS:
        role_id = "nlp"

    hours_per_day = max(0.5, min(float(available_hours_per_day), 16))
    skill_id = classify_goal(message)

    if missing_skills:
        normalized = {
            re.sub(r"[^a-z0-9]+", "_", skill.lower()).strip("_")
            for skill in missing_skills
        }
        if skill_id not in normalized:
            # Preserve the user's explicit goal. Do not silently change
            # the requested project just because another gap exists.
            pass

    template = PROJECT_TEMPLATES[skill_id]
    resources = select_resources(skill_id)

    if not resources:
        resources = select_resources("llm_apis")

    total_hours = {
        "llm_apis": 18,
        "agents": 13,
        "rest_apis": 11,
        "ml_frameworks": 15,
    }[skill_id]

    days = max(1, int(total_hours / hours_per_day + 0.999))

    return {
        "agent_name": "Redmargin Project Mentor",
        "intent": "project_roadmap",
        "role": {
            "id": role_id,
            "label": ROLE_LABELS[role_id],
        },
        "goal": message,
        "recommended_skill": {
            "id": skill_id,
            "label": SKILL_LABELS.get(skill_id, skill_id),
        },
        "project": {
            "title": template["title"],
            "why": template["why"],
            "estimated_hours": template["total_hours"],
            "estimated_days_at_available_pace": days,
            "tasks": template["tasks"],
        },
        "resources": resources,
        "resume_evidence": [
            "A working, reproducible implementation",
            "Tests and measurable evaluation results",
            "A README explaining architecture and trade-offs",
            "A demo or example inputs and outputs",
        ],
        "repository_context": {
            "evidence_summary": evidence_summary,
            "missing_skills": missing_skills or [],
            "note": (
                "Recommendations use the supplied review context. "
                "A skill not found in inspected files is not proof that "
                "the developer lacks that skill."
            ),
        },
        "limitations": [
            "Time estimates are approximate.",
            "This version uses curated resources, not live web search.",
            "It proposes changes; it does not edit repository files.",
        ],
    }
