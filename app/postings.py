"""Load stored and pasted job postings with explicit origin and sample metadata."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from . import config

SAMPLE_MARKER = "SAMPLE"
MIN_MARKET_POSTINGS = 10


@dataclass
class Posting:
    name: str
    text: str
    source: str
    role: str
    retrieved_at: str
    is_sample: bool = False


def _validate_posting_text(text: str, description: str) -> str:
    normalized = text.strip()
    if not normalized:
        raise ValueError(f"{description} must not be empty.")
    if len(normalized) > config.MAX_PASTED_POSTINGS_CHARS:
        raise ValueError(
            f"{description} exceeds the {config.MAX_PASTED_POSTINGS_CHARS}-character limit."
        )
    return normalized


def load_roles() -> list[dict]:
    return json.loads((config.DATA_DIR / "roles.json").read_text(encoding="utf-8"))


def get_role(role_id: str) -> dict:
    roles = load_roles()
    for role in roles:
        if role["id"] == role_id:
            return role
    raise ValueError(f"Unknown role '{role_id}'. Available: {', '.join(r['id'] for r in roles)}")


def load_stored_postings(role_id: str) -> list[Posting]:
    get_role(role_id)
    folder = config.DATA_DIR / "postings" / role_id
    if not folder.exists():
        return []
    postings = []
    for file_path in sorted(folder.glob("*.txt")):
        with file_path.open(encoding="utf-8", errors="ignore") as source_file:
            raw_text = source_file.read(config.MAX_PASTED_POSTINGS_CHARS + 1)
            if len(raw_text) > config.MAX_PASTED_POSTINGS_CHARS or source_file.read(1):
                raise ValueError(
                    f"Stored posting {file_path.name} exceeds the "
                    f"{config.MAX_PASTED_POSTINGS_CHARS}-character limit."
                )
        if not raw_text.strip():
            continue
        text = _validate_posting_text(raw_text, f"Stored posting {file_path.name}")
        retrieved_at = datetime.fromtimestamp(file_path.stat().st_mtime, timezone.utc).isoformat()
        postings.append(Posting(
            name=file_path.name,
            text=text,
            source="stored",
            role=role_id,
            retrieved_at=retrieved_at,
            is_sample=text.upper().startswith(SAMPLE_MARKER),
        ))
    return postings


def gather_postings(role_id: str, pasted: list[str]) -> tuple[list[Posting], dict]:
    """Use real stored and user-pasted postings; use samples only when real data is absent."""
    get_role(role_id)
    stored = load_stored_postings(role_id)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    pasted_postings = [
        Posting(
            name=f"pasted-{i + 1}",
            text=_validate_posting_text(text, f"Pasted posting {i + 1}"),
            source="user_pasted",
            role=role_id,
            retrieved_at=retrieved_at,
        )
        for i, text in enumerate(pasted)
    ]

    real_stored = [posting for posting in stored if not posting.is_sample]
    postings = real_stored + pasted_postings
    if not postings:
        postings = stored
    if len(postings) > config.MAX_POSTINGS:
        raise ValueError(f"No more than {config.MAX_POSTINGS} postings may be used in one review.")
    total_chars = sum(len(posting.text) for posting in postings)
    if total_chars > config.MAX_PASTED_POSTINGS_CHARS:
        raise ValueError(
            f"Combined posting text exceeds the {config.MAX_PASTED_POSTINGS_CHARS}-character limit."
        )

    sample_count = sum(posting.is_sample for posting in postings)
    warning = ""
    if not postings:
        raise ValueError(
            f"No job postings found for this role. Paste some, or add .txt files to data/postings/{role_id}/."
        )
    if sample_count:
        warning = (
            "Synthetic sample postings are shown for testing but excluded from market percentages. "
            "Paste 10–15 real postings (separate them with a line containing ---) or add .txt files to "
            f"data/postings/{role_id}/."
        )
    elif len(postings) < MIN_MARKET_POSTINGS:
        warning = (
            f"Only {len(postings)} real posting(s) used. At least {MIN_MARKET_POSTINGS} are needed "
            "for a market comparison; 10–15 gives more reliable percentages."
        )

    metadata = {
        "count": len(postings),
        "evidence_count": sum(not posting.is_sample for posting in postings),
        "sample_count": sample_count,
        "using_sample_postings": sample_count > 0,
        "sources": [
            {
                "name": posting.name,
                "source": posting.source,
                "role": posting.role,
                "retrieved_at": posting.retrieved_at,
                "is_sample": posting.is_sample,
            }
            for posting in postings
        ],
        "warning": warning,
    }
    return postings, metadata
