"""Generate interview questions that cite files present in the fetched snapshot."""
from __future__ import annotations

from . import llm, prompts
from .github_client import RepoSnapshot


def _fallback_questions(snap: RepoSnapshot, role_label: str) -> list[dict]:
    questions = []
    for path in list(snap.files)[:8]:
        questions.append({
            "question": f"What responsibilities does `{path}` have in this {role_label} project?",
            "why_they_ask": "To check that you understand the code you submitted and its role in the project.",
            "evidence": [path],
            "follow_up": f"How would you test an important edge case in `{path}`?",
        })
    return questions


async def generate_interview_questions(
    snap: RepoSnapshot,
    role_label: str,
    matrix: list[dict],
) -> dict:
    """Return questions with evidence restricted to readable repository files."""
    source = "llm"
    llm_error = None
    try:
        raw = await llm.complete_json(
            prompts.INTERVIEW_SYSTEM,
            prompts.build_interview_user(snap, role_label, matrix),
        )
    except llm.LLMError as exc:
        raw = None
        llm_error = str(exc)

    if raw is None:
        source = "rule-based (no LLM configured)" if llm_error is None else "rule-based (LLM failed)"
        questions = _fallback_questions(snap, role_label)
    else:
        questions = []
        raw_questions = raw.get("questions")
        if isinstance(raw_questions, list):
            readable_paths = set(snap.files)
            for item in raw_questions:
                if not isinstance(item, dict):
                    continue
                path = item.get("file")
                if not isinstance(path, str) or path not in readable_paths:
                    continue
                question = item.get("question")
                why = item.get("why_they_ask")
                follow_up = item.get("follow_up")
                if not all(isinstance(value, str) and value.strip() for value in (question, why, follow_up)):
                    continue
                questions.append({
                    "question": question.strip(),
                    "why_they_ask": why.strip(),
                    "evidence": [path],
                    "follow_up": follow_up.strip(),
                })
        if not questions:
            questions = _fallback_questions(snap, role_label)
            source = "rule-based (LLM returned no valid questions)"

    return {"questions": questions, "source": source, "llm_error": llm_error}
