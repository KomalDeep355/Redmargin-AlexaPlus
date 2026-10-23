"""A deterministic project-evidence verdict with validated, evidence-backed bullets."""
from __future__ import annotations

import re

from . import llm, prompts
from .github_client import RepoSnapshot
from .reviewer import is_valid_evidence

_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?")
_UNSUPPORTED_CLAIM_RE = re.compile(
    r"\b(?:years?\s+of\s+experience|managed|led|increased|improved|boosted|reduced|achieved|"
    r"grew|delivered|launched|optimized|streamlined|transformed|saved|scaled|deployed|"
    r"secured|productionized|accelerated|revenue|business\s+impact|customer\s+satisfaction|"
    r"business\s+value|cost\s+savings|profit|sales|conversion|retention|performance|latency|"
    r"throughput|response\s+time|faster|doubled|tripled|scalable|at\s+scale)\b",
    re.I,
)
_UNSUPPORTED_USER_CLAIM_RE = re.compile(
    r"\b(?:users?|customers?|clients?)\b.{0,35}\b(?:served|supported|reached|adopted|"
    r"retained|acquired|scaled|grew|used)\b|"
    r"\b(?:served|supported|reached|adopted|retained|acquired|scaled|grew|used)\b"
    r".{0,35}\b(?:users?|customers?|clients?)\b",
    re.I,
)
_TESTING_CLAIM_RE = re.compile(r"\b(?:tested|tests?|verified|validated|coverage)\b", re.I)
_TEST_PATH_RE = re.compile(r"(?:^|/)(?:tests?/|test_[^/]+|[^/]+_test\.[^/]+)", re.I)
_TEST_ASSERTION_RE = re.compile(r"\b(?:assert|expect|assertEqual|assertRaises)\b|toBe\(", re.I)
_STOP_WORDS = {
    "about", "across", "and", "based", "built", "by", "for", "from", "in", "into",
    "its", "project", "repo", "repository", "the", "through", "to", "using", "with",
    "developed", "implemented", "created", "engineered", "designed",
}


def _rejection(text: str, reason: str) -> dict[str, str]:
    return {"text": text, "reason": reason}


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def _validate_bullets(
    raw_bullets, snap: RepoSnapshot
) -> tuple[list[dict], list[dict[str, str]]]:
    if raw_bullets is None:
        return [], []
    if not isinstance(raw_bullets, list):
        return [], [_rejection("", "The advice service did not return a list of bullet objects.")]

    valid = []
    rejected = []
    for item in raw_bullets:
        if not isinstance(item, dict):
            rejected.append(_rejection("", "The bullet was not a JSON object."))
            continue
        text = item.get("text")
        evidence = item.get("evidence")
        if not isinstance(text, str) or not text.strip() or not isinstance(evidence, list):
            rejected.append(_rejection(
                text.strip() if isinstance(text, str) else "",
                "The bullet must contain non-empty text and an evidence-path list.",
            ))
            continue
        cited = list(dict.fromkeys(
            path for path in evidence
            if isinstance(path, str)
            and path in snap.files
            and is_valid_evidence(path, set(snap.tree), set(), set())
        ))
        if not cited:
            rejected.append(_rejection(
                text.strip(),
                "No cited path matched an inspected repository file.",
            ))
            continue

        cited_text = "\n".join(snap.files[path] for path in cited).casefold()
        normalized = text.strip()
        numbers = _NUMBER_RE.findall(normalized)
        if (
            _UNSUPPORTED_CLAIM_RE.search(normalized)
            or _UNSUPPORTED_USER_CLAIM_RE.search(normalized)
        ):
            rejected.append(_rejection(
                normalized,
                "The bullet makes an unsupported personal achievement or business-impact claim.",
            ))
            continue
        if numbers:
            rejected.append(_rejection(
                normalized,
                "Numeric metrics and counts cannot be verified by this static repository review.",
            ))
            continue

        terms = {
            token.casefold()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{3,}", normalized)
            if token.casefold() not in _STOP_WORDS
        }
        cited_terms = set(re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{3,}", cited_text))
        grounded_terms = terms.intersection(cited_terms)
        if (
            len(terms) < 2
            or len(grounded_terms) < 2
            or len(grounded_terms) / len(terms) < 0.6
        ):
            rejected.append(_rejection(
                normalized,
                "The cited files do not contain enough matching terms to substantiate the bullet.",
            ))
            continue

        is_testing_claim = bool(_TESTING_CLAIM_RE.search(normalized))
        if is_testing_claim:
            test_paths = [
                path for path in cited
                if _is_test_path(path) and _TEST_ASSERTION_RE.search(snap.files[path])
            ]
            if not test_paths:
                rejected.append(_rejection(
                    normalized,
                    "Testing claims require a cited test file containing an assertion.",
                ))
                continue
            valid.append({
                "text": normalized,
                "evidence": test_paths,
                "claim_type": "testing",
                "verification": "Supported by inspected test code; test execution was not verified.",
                "metrics_verified": False,
                "evidence_confidence": "medium",
            })
            continue

        implementation_paths = [path for path in cited if not _is_test_path(path)]
        if not implementation_paths:
            rejected.append(_rejection(
                normalized,
                "Implementation claims require a cited non-test source file.",
            ))
            continue
        valid.append({
            "text": normalized,
            "evidence": implementation_paths,
            "claim_type": "implementation",
            "verification": "Supported by inspected source code; runtime behavior was not verified.",
            "metrics_verified": False,
            "evidence_confidence": "medium",
        })

    if len(valid) > 3:
        rejected.extend(
            _rejection(
                bullet["text"],
                "Only the three most concise evidence-backed bullets are returned.",
            )
            for bullet in valid[3:]
        )
    return valid[:3], rejected


def _deterministic_verdict(signals: dict, matrix: list[dict], snap: RepoSnapshot) -> tuple[str, str]:
    strengths = sum(row["status"] == "strength" for row in matrix)
    gaps = sum(row["status"] == "gap" for row in matrix)
    insufficient_market = any(
        row.get("market_status") == "insufficient_market_data" for row in matrix
    )
    if strengths and strengths >= gaps and signals.get("has_readme") and signals["has_readme"].value:
        verdict = "strong_project_evidence"
    elif snap.files:
        verdict = "developing_project_evidence"
    else:
        verdict = "limited_project_evidence"
    if insufficient_market:
        evidence_count = sum(
            row.get("evidence_status") == "evidence_found" for row in matrix
        )
        explanation = (
            f"The inspected repository shows evidence for {evidence_count} detectable role skill(s). "
            "The market comparison is not assessed because fewer than 10 real postings were available. "
            "This describes repository evidence only; it is not a verdict on the candidate."
        )
    else:
        explanation = (
            f"The inspected repository shows evidence for {strengths} role skills and no matching repository evidence "
            f"for {gaps} high-demand skill(s). "
            "This describes repository evidence only; it is not a verdict on the candidate."
        )
    return verdict, explanation


async def resume_verdict(
    snap: RepoSnapshot,
    role_label: str,
    signals: dict,
    matrix: list[dict],
) -> dict:
    verdict, explanation = _deterministic_verdict(signals, matrix, snap)
    source = "llm"
    llm_error = None
    bullet_generation_note = None
    try:
        raw = await llm.complete_json(
            prompts.RESUME_SYSTEM,
            prompts.build_resume_user(snap, role_label, matrix),
        )
    except llm.LLMError as exc:
        raw = None
        llm_error = str(exc)

    if raw is None:
        source = "no bullets (LLM not configured)" if llm_error is None else "no bullets (LLM failed)"
        bullets = []
        rejected_details = []
        bullet_generation_note = (
            "No resume bullet was generated because inspected file presence alone is not enough "
            "to support a specific, useful claim."
        )
    else:
        bullets, rejected_details = _validate_bullets(raw.get("bullets"), snap)
        if not bullets:
            source = "no valid LLM bullets"
            bullet_generation_note = (
                "No resume bullet passed evidence validation. Review the rejected bullet reasons "
                "before making a claim."
            )
    return {
        "verdict": verdict,
        "explanation": explanation,
        "bullets": bullets,
        "rejected_bullets": len(rejected_details),
        "rejected_bullet_details": rejected_details,
        "bullet_generation_note": bullet_generation_note,
        "source": source,
        "llm_error": llm_error,
    }
