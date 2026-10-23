"""The pipeline: snapshot -> signals -> skill matrix -> plan -> critique.

The numbers (signals, percentages, hours) are deterministic. The LLM only writes the critique, and every claim it
makes must cite evidence that we can check. Unsupported claims are dropped and counted.
"""
from __future__ import annotations

from dataclasses import asdict

from . import llm, prompts
from .github_client import RepoSnapshot, fetch_snapshot
from .planner import build_plan
from .postings import MIN_MARKET_POSTINGS, gather_postings, get_role
from .schemas import ReviewRequest
from .signals import Signal, compute_signals
from .skills import build_skill_matrix


def is_valid_evidence(ev: str, tree: set[str], signal_names: set[str], skill_ids: set[str]) -> bool:
    ev = (ev or "").strip()
    if ev.startswith("signal:"):
        return ev[7:] in signal_names
    if ev.startswith("skill:"):
        return ev[6:] in skill_ids
    return ev in tree


def calculate_repository_score(skill_matrix: list[dict]) -> float | None:
    """Score detectable repository skill evidence independently of job-market data."""
    assessed = [item for item in skill_matrix if item.get("detectable") is True]
    evidence_found = sum(
        item.get("evidence_status") == "evidence_found"
        or (item.get("evidence_status") is None and bool(item.get("in_repo")))
        for item in assessed
    )
    return round(10 * evidence_found / len(assessed), 1) if assessed else None


def _validate_items(items, tree, signal_names, skill_ids) -> tuple[list[dict], int]:
    """Keep only items with at least one checkable piece of evidence. Returns (kept, dropped_count)."""
    kept, dropped = [], 0
    if items is None:
        return kept, dropped
    if not isinstance(items, list):
        return kept, 1
    for item in items or []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, list):
            dropped += 1
            continue
        good = [
            evidence_item for evidence_item in evidence
            if isinstance(evidence_item, str)
            and is_valid_evidence(evidence_item, tree, signal_names, skill_ids)
        ]
        if good:
            kept.append({**item, "evidence": good})
        else:
            dropped += 1
    return kept, dropped


def rule_based_critique(role_label: str, signals: dict[str, Signal], matrix: list[dict], plan: dict) -> dict:
    """Used when no LLM is configured or the LLM fails. Plain facts, each tied to evidence."""
    findings, strengths = [], []
    for row in [r for r in matrix if r["status"] == "gap"][:4]:
        findings.append({
            "title": f"No repository evidence found for {row['label']}",
            "severity": "high" if row["pct"] >= 0.5 else "medium",
            "detail": (
                f"{row['postings_mentioning']} of {row['total_postings']} postings mention it. "
                "No matching evidence was found in the inspected repository; this does not establish "
                "whether the candidate has the skill."
            ),
            "evidence": [f"skill:{row['id']}"],
        })
    for sig_name, msg in [
        ("committed_env_file", "A .env file is committed. Remove it and rotate any keys."),
        ("has_tests", "No automated tests were found."),
        ("readme_has_metrics", "The README shows no measured results."),
        ("has_live_demo", "No live demo URL was found."),
    ]:
        s = signals.get(sig_name)
        if s and s.value != s.good_when_true:
            findings.append({"title": msg, "severity": "high" if sig_name == "committed_env_file" else "medium",
                             "detail": "", "evidence": [f"signal:{sig_name}"]})
    for row in [r for r in matrix if r["status"] == "strength"][:3]:
        strengths.append({"title": f"{row['label']} is shown in the repo and asked for in postings",
                          "detail": f"Mentioned in {row['postings_mentioning']} of {row['total_postings']} postings.",
                          "evidence": row["evidence"][:2]})
    gaps = sum(1 for r in matrix if r["status"] == "gap")
    market_available = any(
        r.get("total_postings", 0) > 0
        and r.get("pct") is not None
        and r.get("postings_mentioning", 0) > 0
        for r in matrix
    )
    total_postings = max((r.get("total_postings", 0) for r in matrix), default=0)
    if total_postings < MIN_MARKET_POSTINGS:
        summary = (
            f"For {role_label}, market demand could not be assessed confidently: only "
            f"{total_postings} real job posting(s) were available; at least "
            f"{MIN_MARKET_POSTINGS} are needed for a market comparison. "
            "Repository evidence is reported separately, and market-based skill gaps and demand "
            "estimates are not assessed."
        )
    elif market_available:
        summary = (
            f"For {role_label}, {gaps} in-demand skill(s) have no matching "
            f"evidence in this repo. Closing the listed fixes is roughly "
            f"{plan['total_hours_min']}-{plan['total_hours_max']} hours of work."
        )
    elif any(r.get("total_postings", 0) > 0 for r in matrix):
        summary = (
            f"For {role_label}, real job postings were available, but none "
            "matched the configured role skills. Market-based skill gaps "
            "and demand estimates could not be assessed."
        )
    else:
        summary = (
            f"For {role_label}, market demand could not be assessed because "
            "no real job postings were available. Repository evidence can "
            "still support technical findings, but market-based skill gaps "
            "and demand estimates are unavailable."
        )
    return {"summary": summary, "findings": findings, "strengths": strengths}


async def generate_critique(snap: RepoSnapshot, role_label: str, signals, matrix, plan, total_postings: int) -> dict:
    tree = set(snap.tree)
    signal_names = set(signals)
    skill_ids = {r["id"] for r in matrix}

    market_skill_data_available = total_postings >= MIN_MARKET_POSTINGS and any(
        row.get("postings_mentioning", 0) > 0 and row.get("pct") is not None
        for row in matrix
    )
    if not market_skill_data_available:
        base = rule_based_critique(role_label, signals, matrix, plan)
        return {
            **base,
            "source": (
                "rule-based (no real postings)"
                if total_postings == 0
                else "rule-based (insufficient market data)"
                if total_postings < MIN_MARKET_POSTINGS
                else "rule-based (no matched role skills)"
            ),
            "llm_error": None,
            "evidence_check": {
                "findings_total": len(base["findings"]),
                "findings_kept": len(base["findings"]),
                "strengths_total": len(base["strengths"]),
                "strengths_kept": len(base["strengths"]),
                "dropped": 0,
            },
        }

    source, llm_error, raw = "llm", None, None
    try:
        raw = await llm.complete_json(prompts.CRITIQUE_SYSTEM,
                                      prompts.build_critique_user(snap, role_label, signals, matrix, total_postings))
    except llm.LLMError as e:
        llm_error = str(e)

    if raw is None:
        source = "rule-based (no LLM configured)" if llm_error is None else "rule-based (LLM failed)"
        base = rule_based_critique(role_label, signals, matrix, plan)
        return {**base, "source": source, "llm_error": llm_error,
                "evidence_check": {
                    "findings_total": len(base["findings"]),
                    "findings_kept": len(base["findings"]),
                    "strengths_total": len(base["strengths"]),
                    "strengths_kept": len(base["strengths"]),
                    "dropped": 0,
                }}

    raw_findings = raw.get("findings")
    raw_strengths = raw.get("strengths")
    findings, dropped_f = _validate_items(raw_findings, tree, signal_names, skill_ids)
    strengths, dropped_s = _validate_items(raw_strengths, tree, signal_names, skill_ids)
    return {
        "source": source,
        "summary": str(raw.get("summary", "")),
        "findings": findings,
        "strengths": strengths,
        "llm_error": None,
        "evidence_check": {
            "findings_total": len(raw_findings) if isinstance(raw_findings, list) else int(raw_findings is not None),
            "findings_kept": len(findings),
            "strengths_total": len(raw_strengths) if isinstance(raw_strengths, list) else int(raw_strengths is not None),
            "strengths_kept": len(strengths),
            "dropped": dropped_f + dropped_s,
        },
    }


async def prepare_analysis(repo_url: str, role_id: str, pasted_postings: list[str] | None = None) -> dict:
    role = get_role(role_id)
    postings, postings_meta = gather_postings(role_id, pasted_postings or [])
    snap = await fetch_snapshot(repo_url)
    signals = compute_signals(snap)
    real_postings = [posting for posting in postings if not posting.is_sample]
    matrix = build_skill_matrix(
        snap, signals, [posting.text for posting in real_postings], role_id
    )
    return {
        "role": role,
        "postings": postings,
        "postings_meta": postings_meta,
        "real_postings": real_postings,
        "snapshot": snap,
        "signals": signals,
        "matrix": matrix,
    }


async def run_review(req: ReviewRequest) -> dict:
    context = await prepare_analysis(req.repo_url, req.role, req.pasted_postings)
    role = context["role"]
    postings_meta = context["postings_meta"]
    snap = context["snapshot"]
    signals = context["signals"]
    matrix = context["matrix"]
    real_postings = context["real_postings"]
    plan = build_plan(matrix, signals, req.hours_per_day)
    critique = await generate_critique(snap, role["label"], signals, matrix, plan, len(real_postings))
    market_comparison_available = len(real_postings) >= MIN_MARKET_POSTINGS and any(
        row.get("postings_mentioning", 0) > 0 and row.get("pct") is not None
        for row in matrix
    )

    return {
        "repo": {
            "full_name": snap.full_name, "url": f"https://github.com/{snap.full_name}", "description": snap.description,
            "stars": snap.stars, "primary_language": snap.primary_language, "pushed_at": snap.pushed_at,
            "branch": snap.branch, "files_read": list(snap.files), "tree_truncated": snap.tree_truncated,
        },
        "role": role,
        "postings": postings_meta,
        "signals": [asdict(s) for s in signals.values()],
        "skill_matrix": matrix,
        "repository_score": calculate_repository_score(matrix),
        "market_comparison_available": market_comparison_available,
        "real_posting_count": len(real_postings),
        "plan": plan,
        "critique": critique,
    }
