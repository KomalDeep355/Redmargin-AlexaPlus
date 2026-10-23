"""Three-run, evidence-grounded hackathon judging estimates."""
from __future__ import annotations

import asyncio
import math
import re
from statistics import median

from . import llm, prompts
from .github_client import RepoSnapshot

_TOKEN_RE = re.compile(r"[a-z0-9+#.-]{3,}")
_STOP_WORDS = {
    "and", "are", "for", "from", "has", "into", "its", "project", "repo", "repository",
    "that", "the", "their", "this", "with",
}


def _criterion_terms(criterion: dict) -> set[str]:
    text = f"{criterion.get('name', '')} {criterion.get('description', '')}".casefold()
    return {term for term in _TOKEN_RE.findall(text) if term not in _STOP_WORDS}


def _deterministic_scores(snap: RepoSnapshot, criteria: list[dict]) -> list[dict]:
    scores = []
    for criterion in criteria:
        terms = _criterion_terms(criterion)
        matching_paths = []
        matched_terms = set()
        for path, text in snap.files.items():
            corpus = f"{path}\n{text}".casefold()
            hits = terms.intersection(_TOKEN_RE.findall(corpus))
            if hits:
                matching_paths.append(path)
                matched_terms.update(hits)
        score = round(10 * len(matched_terms) / len(terms), 1) if terms else 0.0
        scores.append({
            "name": criterion["name"],
            "score": min(10.0, score),
            "evidence": matching_paths[:5],
        })
    return scores


def _validated_run(raw: dict, criteria: list[dict], snap: RepoSnapshot) -> list[dict] | None:
    raw_criteria = raw.get("criteria")
    if not isinstance(raw_criteria, list) or len(raw_criteria) != len(criteria):
        return None
    by_name = {}
    for item in raw_criteria:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return None
        name = item["name"].casefold()
        if name in by_name:
            return None
        score = item.get("score")
        evidence = item.get("evidence")
        if (
            isinstance(score, bool) or not isinstance(score, (int, float))
            or not math.isfinite(score) or score < 0 or score > 10
            or not isinstance(evidence, list)
        ):
            return None
        valid_evidence = list(dict.fromkeys(
            path for path in evidence if isinstance(path, str) and path in snap.files and path in snap.tree
        ))
        if score > 0 and not valid_evidence:
            return None
        by_name[name] = {"score": float(score), "evidence": valid_evidence}

    validated = []
    for criterion in criteria:
        result = by_name.get(criterion["name"].casefold())
        if result is None:
            return None
        validated.append({"name": criterion["name"], **result})
    return validated


async def judge_project(snap: RepoSnapshot, criteria: list[dict]) -> dict:
    """Return criterion-level estimates and an interval based on three model runs."""
    prompt_criteria = [
        {"name": criterion["name"], "description": criterion["description"], "weight": criterion["weight"]}
        for criterion in criteria
    ]
    user_prompt = prompts.build_judge_user(snap, prompt_criteria)
    run_results = None
    llm_error = None
    try:
        raw_runs = await asyncio.gather(*(
            llm.complete_json(prompts.JUDGE_SYSTEM, user_prompt) for _ in range(3)
        ))
        if all(isinstance(raw, dict) for raw in raw_runs):
            parsed_runs = [_validated_run(raw, criteria, snap) for raw in raw_runs]
            if all(run is not None for run in parsed_runs):
                run_results = parsed_runs
        if run_results is None and any(raw is not None for raw in raw_runs):
            llm_error = "The model returned scores that did not match the required criteria or evidence format."
    except llm.LLMError as exc:
        llm_error = str(exc)

    if run_results is None:
        source = "deterministic evidence heuristic"
        base_scores = _deterministic_scores(snap, criteria)
        run_results = [base_scores, base_scores, base_scores]
    else:
        source = "llm (3 runs)"

    results = []
    weighted_min = weighted_median = weighted_max = 0.0
    for index, criterion in enumerate(criteria):
        criterion_runs = [run[index] for run in run_results]
        values = [run["score"] for run in criterion_runs]
        minimum, middle, maximum = min(values), float(median(values)), max(values)
        weight = criterion["weight"]
        weighted_min += minimum * weight / 100
        weighted_median += middle * weight / 100
        weighted_max += maximum * weight / 100
        evidence = list(dict.fromkeys(
            path for run in criterion_runs for path in run["evidence"]
        ))
        results.append({
            "name": criterion["name"],
            "weight": weight,
            "score": round(middle, 2),
            "minimum": round(minimum, 2),
            "median": round(middle, 2),
            "maximum": round(maximum, 2),
            "evidence": evidence,
        })

    return {
        "full_name": snap.full_name,
        "estimate": True,
        "criteria": results,
        "weighted_score": round(weighted_median, 2),
        "weighted_range": {
            "minimum": round(weighted_min, 2),
            "median": round(weighted_median, 2),
            "maximum": round(weighted_max, 2),
        },
        "source": source,
        "llm_error": llm_error,
    }
