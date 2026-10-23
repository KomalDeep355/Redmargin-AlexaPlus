"""Turn gaps + repo hygiene into a ranked fix list and a day-by-day timetable.

Hours come from data/fix_library.json (rough ranges), NOT from the LLM, because LLM time estimates are unreliable.
Priority = (demand weight) / (average hours): high-demand, cheap fixes come first.
"""
from __future__ import annotations

import json
import math
from decimal import Decimal

from . import config
from .signals import Signal
from .skills import load_taxonomy

# (signal name, bad value, fix id, weight 0-1, reason)
HYGIENE = [
    ("committed_env_file", True, "remove_committed_env", 1.0, "A .env file is committed to the repo"),
    ("has_readme", False, "write_readme", 0.9, "The repo has no README"),
    ("readme_substantial", False, "improve_readme", 0.7, "The existing README is minimal"),
    ("readme_has_metrics", False, "readme_metrics", 0.5, "The README has no meaningful measured results"),
    ("has_tests", False, "add_tests", 0.5, "No automated tests were found"),
    ("has_live_demo", False, "deploy_live", 0.5, "No live demo URL was found"),
    ("has_ci", False, "add_ci", 0.3, "No CI workflow was found"),
    ("has_license", False, "add_license", 0.3, "No license file"),
    ("has_dockerfile", False, "add_dockerfile", 0.2, "No Dockerfile"),
]


class _DuplicateJsonKeyError(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"Duplicate key '{key}'.")
        result[key] = value
    return result


def load_fix_library() -> dict:
    try:
        library = json.loads(
            (config.DATA_DIR / "fix_library.json").read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
        raise ValueError("Could not load data/fix_library.json.") from exc
    if not isinstance(library, dict):
        raise ValueError("The fix library must be a JSON object.")
    for fix_id, fix in library.items():
        if not isinstance(fix_id, str) or not fix_id.strip():
            raise ValueError("Every fix library entry must have a non-empty string ID.")
        if not isinstance(fix, dict) or not isinstance(fix.get("title"), str) or not fix["title"].strip():
            raise ValueError(f"Fix library entry '{fix_id}' must have a title.")
        steps = fix.get("steps")
        if not isinstance(steps, list) or not steps or any(
            not isinstance(step, str) or not step.strip() for step in steps
        ):
            raise ValueError(f"Fix library entry '{fix_id}' must have a non-empty list of steps.")
        minimum, maximum = fix.get("hours_min"), fix.get("hours_max")
        try:
            minimum_number = float(minimum)
            maximum_number = float(maximum)
        except (OverflowError, TypeError, ValueError):
            raise ValueError(f"Fix library entry '{fix_id}' must have valid positive hour bounds.")
        if (
            isinstance(minimum, bool) or isinstance(maximum, bool)
            or not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float))
            or not math.isfinite(minimum_number) or not math.isfinite(maximum_number)
            or minimum_number <= 0 or maximum_number < minimum_number or maximum_number > 1000
        ):
            raise ValueError(f"Fix library entry '{fix_id}' must have valid positive hour bounds.")

    referenced_ids = {fix_id for _, _, fix_id, _, _ in HYGIENE} | {"add_env_example"}
    skill_fix_ids = [skill.get("fix_id") for skill in load_taxonomy() if skill.get("fix_id")]
    referenced_ids.update(skill_fix_ids)
    missing = sorted(referenced_ids - library.keys())
    if missing:
        raise ValueError(f"Fix library is missing referenced fix_id(s): {', '.join(missing)}.")
    return library


def build_plan(matrix: list[dict], signals: dict[str, Signal], hours_per_day: float = 2.0) -> dict:
    library = load_fix_library()
    if not math.isfinite(hours_per_day) or hours_per_day <= 0:
        raise ValueError("hours_per_day must be a finite positive number.")
    hours_per_day = max(0.5, float(hours_per_day))
    daily_capacity = Decimal(str(hours_per_day))
    fixes: dict[str, dict] = {}

    def _bump(fix_id: str, weight: float, reason: str):
        lib = library.get(fix_id)
        if not lib:
            raise ValueError(f"Unknown fix_id '{fix_id}' referenced by a planning gap.")
        f = fixes.setdefault(fix_id, {"fix_id": fix_id, "weight": 0.0, "reasons": [], **lib})
        f["weight"] = max(f["weight"], weight)
        if reason not in f["reasons"]:
            f["reasons"].append(reason)

    unaddressed = []
    for row in matrix:
        fix_id = row.get("fix_id")
        if fix_id is not None and (not isinstance(fix_id, str) or fix_id not in library):
            raise ValueError(f"Unknown fix_id '{fix_id}' referenced by a skill matrix row.")
        if row["status"] != "gap":
            continue
        reason = f"{row['postings_mentioning']} of {row['total_postings']} postings mention {row['label']}; the repo shows no evidence"
        if row["fix_id"]:
            _bump(row["fix_id"], (row["pct"] or 0) * row.get("role_weight", 1.0), reason)
        else:
            unaddressed.append({"label": row["label"], "pct": row["pct"], "reason": reason})

    for sig_name, bad_value, fix_id, weight, reason in HYGIENE:
        s = signals.get(sig_name)
        if s is not None and s.value == bad_value:
            if sig_name in {"readme_substantial", "readme_has_metrics"}:
                has_readme = signals.get("has_readme")
                if has_readme is None or not has_readme.value:
                    continue
            _bump(fix_id, weight, reason)

    env_required = signals.get("requires_env_config")
    env_example_exists = signals.get("has_env_example")
    env_example_complete = signals.get("env_example_complete")
    if (
        env_required is not None and env_required.value
        and (env_example_exists is None or not env_example_exists.value
             or env_example_complete is None or not env_example_complete.value)
    ):
        _bump("add_env_example", 0.2, "The repo uses environment variables but has no complete example file")

    ranked = []
    for f in fixes.values():
        avg = (Decimal(str(f["hours_min"])) + Decimal(str(f["hours_max"]))) / 2
        f["hours_avg"] = float(avg)
        f["_hours_avg_decimal"] = avg
        f["score"] = round(f["weight"] * 100 / float(avg), 1)
        ranked.append(f)
    ranked.sort(key=lambda f: -f["score"])

    days, cur = [], {"day": 1, "tasks": [], "hours": Decimal("0")}
    for f in ranked:
        remaining = f.pop("_hours_avg_decimal")
        while remaining > 0:
            space = daily_capacity - cur["hours"]
            if space <= 0:
                days.append(cur)
                cur = {"day": len(days) + 1, "tasks": [], "hours": Decimal("0")}
            chunk = min(space, remaining)
            cur["tasks"].append({
                "fix_id": f["fix_id"],
                "title": f["title"],
                "hours": float(chunk),
            })
            cur["hours"] += chunk
            remaining -= chunk
    if cur["tasks"]:
        days.append(cur)
    for day in days:
        day["hours"] = float(day["hours"])

    return {
        "hours_per_day": hours_per_day,
        "fixes": ranked,
        "timetable": days,
        "total_hours_min": round(sum(f["hours_min"] for f in ranked), 2),
        "total_hours_max": round(sum(f["hours_max"] for f in ranked), 2),
        "unaddressed_gaps": unaddressed,
        "note": "Hour ranges are rough defaults for someone comfortable with the stack, not predictions.",
    }
