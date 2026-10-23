"""Skill matrix: how often postings mention each skill vs. whether the repo shows evidence of it.

Fully deterministic. To track a new skill, add an entry to data/skills.json — no code changes needed.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from . import config
from .github_client import RepoSnapshot
from .postings import MIN_MARKET_POSTINGS, get_role
from .signals import Signal

GAP_MIN_PCT = 0.25  # a skill is a "gap" if >=25% of postings mention it and the repo shows nothing
_CONFIG_BASENAMES = {
    "requirements.txt", "pyproject.toml", "package.json", "pipfile", "environment.yml",
    "go.mod", "cargo.toml", "pom.xml", "setup.py",
}
_MARKER_KEYS = {"deps", "paths", "keywords", "signals"}
_DEPENDENCY_ALIASES = {
    "faiss": {"faiss-cpu", "faiss-gpu"},
    "kafka": {"kafka-go", "kafka-python"},
    "psycopg2": {"psycopg2-binary"},
    "sklearn": {"scikit-learn"},
}


def load_taxonomy() -> list[dict]:
    path = config.DATA_DIR / "skills.json"
    try:
        taxonomy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Could not load data/skills.json.") from exc
    if not isinstance(taxonomy, list):
        raise ValueError("The skill taxonomy must be a JSON array.")

    ids: set[str] = set()
    for index, skill in enumerate(taxonomy):
        entry = f"Skill entry {index + 1}"
        if not isinstance(skill, dict):
            raise ValueError(f"{entry} must be a JSON object.")
        skill_id, label = skill.get("id"), skill.get("label")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError(f"{entry} must have a non-empty string id.")
        if skill_id in ids:
            raise ValueError(f"Skill id '{skill_id}' is duplicated.")
        ids.add(skill_id)
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"Skill '{skill_id}' must have a non-empty string label.")
        synonyms = skill.get("synonyms")
        if not isinstance(synonyms, list) or not synonyms or any(
            not isinstance(term, str) or not term.strip() for term in synonyms
        ):
            raise ValueError(f"Skill '{skill_id}' must have a non-empty list of string synonyms.")
        markers = skill.get("markers")
        if not isinstance(markers, dict) or set(markers) - _MARKER_KEYS:
            raise ValueError(f"Skill '{skill_id}' has invalid markers.")
        for marker_name, marker_values in markers.items():
            if not isinstance(marker_values, list) or any(
                not isinstance(value, str) or not value.strip() for value in marker_values
            ):
                raise ValueError(f"Skill '{skill_id}' marker '{marker_name}' must be a list of strings.")
            if marker_name == "paths":
                for pattern in marker_values:
                    try:
                        re.compile(pattern, re.IGNORECASE)
                    except re.error as exc:
                        raise ValueError(
                            f"Skill '{skill_id}' has an invalid path regex: {pattern!r}."
                        ) from exc
        if "detectable" in skill and not isinstance(skill["detectable"], bool):
            raise ValueError(f"Skill '{skill_id}' detectable must be a boolean.")
        fix_id = skill.get("fix_id")
        if fix_id is not None and (not isinstance(fix_id, str) or not fix_id.strip()):
            raise ValueError(f"Skill '{skill_id}' fix_id must be null or a non-empty string.")
    return taxonomy


def _word_pattern(term: str) -> re.Pattern:
    # custom boundaries so "c++" / "ci/cd" behave and "ml" doesn't match inside "html"
    return re.compile(r"(?<![a-z0-9+#])" + re.escape(term.lower()) + r"(?![a-z0-9+#])")


def count_mentions(taxonomy: list[dict], posting_texts: list[str]) -> dict[str, int]:
    lowered = [t.lower() for t in posting_texts]
    counts = {}
    for sk in taxonomy:
        pats = [_word_pattern(s) for s in sk["synonyms"]]
        counts[sk["id"]] = sum(1 for t in lowered if any(p.search(t) for p in pats))
    return counts


class _Corpus:
    """Repository paths, parsed dependencies, and searchable source text."""

    def __init__(self, snap: RepoSnapshot):
        self.tree = snap.tree
        self.dependencies = {
            path: _parse_manifest(path, text)
            for path, text in snap.files.items()
            if path.rsplit("/", 1)[-1].lower() in _CONFIG_BASENAMES
        }
        self.text_files = {
            path: text.lower() for path, text in snap.files.items() if path not in self.dependencies
        }


def _normalize_dependency(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _add_requirement(names: set[str], value: str) -> None:
    value = value.strip().strip("'\"")
    if not value or value.startswith(("-", "#")):
        return
    match = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)", value)
    if match:
        names.add(_normalize_dependency(match.group(1)))


def _toml_dependency_names(payload: dict) -> set[str]:
    names: set[str] = set()
    project = payload.get("project", {})
    if isinstance(project, dict):
        for item in project.get("dependencies", []):
            if isinstance(item, str):
                _add_requirement(names, item)
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for values in optional.values():
                if isinstance(values, list):
                    for item in values:
                        if isinstance(item, str):
                            _add_requirement(names, item)

    tool = payload.get("tool", {})
    poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
    if isinstance(poetry, dict):
        for section in (poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})):
            if isinstance(section, dict):
                names.update(_normalize_dependency(name) for name in section if name != "python")
        groups = poetry.get("group", {})
        if isinstance(groups, dict):
            for group in groups.values():
                if isinstance(group, dict) and isinstance(group.get("dependencies"), dict):
                    names.update(
                        _normalize_dependency(name)
                        for name in group["dependencies"] if name != "python"
                    )

    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        values = payload.get(section, {})
        if isinstance(values, dict):
            names.update(_normalize_dependency(name) for name in values)

    pipfile = payload.get("packages", {})
    if isinstance(pipfile, dict):
        names.update(_normalize_dependency(name) for name in pipfile)
    pipfile_dev = payload.get("dev-packages", {})
    if isinstance(pipfile_dev, dict):
        names.update(_normalize_dependency(name) for name in pipfile_dev)
    return names


def _parse_toml_without_tomllib(text: str) -> set[str]:
    names: set[str] = set()
    section = ""
    in_array = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.strip("[]").split(".")[-1].strip('"')
            in_array = False
            continue
        if section in {"packages", "dev-packages", "dependencies", "dev-dependencies", "build-dependencies"}:
            assignment = re.match(r'^([A-Za-z0-9_.-]+)\s*=', stripped)
            if assignment and not in_array:
                name = assignment.group(1)
                if name != "python":
                    names.add(_normalize_dependency(name))
            if re.search(r"=\s*\[", stripped):
                in_array = "]" not in stripped
            elif in_array and "]" in stripped:
                in_array = False
        if section in {"project", "optional-dependencies"} or in_array:
            for requirement in re.findall(r"""["']([^"']+)["']""", stripped):
                _add_requirement(names, requirement)
    return names


def _parse_manifest(path: str, text: str) -> set[str]:
    """Extract declared package names using the manifest's own structure."""
    basename = path.rsplit("/", 1)[-1].lower()
    names: set[str] = set()
    if basename == "package.json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return names
        if isinstance(payload, dict):
            for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                values = payload.get(section, {})
                if isinstance(values, dict):
                    names.update(_normalize_dependency(name) for name in values)
    elif basename in {"pyproject.toml", "pipfile", "cargo.toml"}:
        try:
            import tomllib

            payload = tomllib.loads(text)
        except ModuleNotFoundError:
            names.update(_parse_toml_without_tomllib(text))
        except ValueError:
            return names
        else:
            names.update(_toml_dependency_names(payload))
    elif basename == "requirements.txt":
        for line in text.splitlines():
            _add_requirement(names, line.split("#", 1)[0].strip().lstrip("- "))
    elif basename == "setup.py":
        requirements = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.DOTALL)
        if requirements:
            for value in re.findall(r"""["']([^"']+)["']""", requirements.group(1)):
                _add_requirement(names, value)
    elif basename == "environment.yml":
        in_dependencies = False
        for line in text.splitlines():
            if re.match(r"^\s*dependencies\s*:", line):
                in_dependencies = True
                continue
            if in_dependencies:
                item = line.strip().lstrip("- ").strip()
                if item and not item.startswith("#") and not item.startswith("pip:"):
                    _add_requirement(names, re.split(r"[=<>!~\s]", item, maxsplit=1)[0])
    elif basename == "go.mod":
        in_require_block = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("require ("):
                in_require_block = True
                continue
            if in_require_block and stripped == ")":
                in_require_block = False
                continue
            if stripped.startswith("require ") or in_require_block:
                module = stripped.removeprefix("require ").split()[0] if stripped else ""
                if module:
                    names.add(_normalize_dependency(module.rsplit("/", 1)[-1]))
    elif basename == "pom.xml":
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return names
        for dependency in root.iter():
            if dependency.tag.endswith("dependency"):
                artifact = next(
                    (child.text for child in dependency if child.tag.endswith("artifactId") and child.text),
                    None,
                )
                if artifact:
                    names.add(_normalize_dependency(artifact))
    return names


def _dependency_matches(marker: str, dependencies: set[str]) -> bool:
    normalized = _normalize_dependency(marker)
    candidates = {normalized, *_DEPENDENCY_ALIASES.get(normalized, set())}
    return any(
        dependency in candidates
        or (normalized.startswith("@") and dependency.startswith(normalized + "/"))
        for dependency in dependencies
    )


def repo_evidence(skill: dict, corpus: _Corpus, signals: dict[str, Signal]) -> list[str]:
    markers = skill.get("markers", {})
    ev: list[str] = []

    for sig in markers.get("signals", []):
        s = signals.get(sig)
        if s and s.value:
            ev.append(f"signal:{sig}")

    for dep in markers.get("deps", []):
        ev += [path for path, dependencies in corpus.dependencies.items()
               if _dependency_matches(dep, dependencies)]

    for rx in markers.get("paths", []):
        pat = re.compile(rx, re.I)
        ev += [p for p in corpus.tree if pat.search(p)][:3]

    for kw in markers.get("keywords", []):
        k = kw.lower()
        ev += [path for path, text in corpus.text_files.items() if k in text][:3]

    seen, out = set(), []
    for e in ev:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out[:4]


_STATUS_ORDER = {
    "gap": 0,
    "minor_gap": 1,
    "strength": 2,
    "not_detectable": 3,
    "market_insufficient": 4,
    "market_unavailable": 4,
    "not_mentioned": 5,
    "extra": 6,
}


def build_skill_matrix(
    snap: RepoSnapshot,
    signals: dict[str, Signal],
    posting_texts: list[str],
    role_id: str,
) -> list[dict]:
    role = get_role(role_id)
    skill_weights = role.get("skill_weights")
    if not isinstance(skill_weights, dict) or not skill_weights:
        raise ValueError(f"Role '{role_id}' has no configured skill weights.")

    taxonomy_by_id = {skill["id"]: skill for skill in load_taxonomy()}
    unknown_skills = set(skill_weights) - taxonomy_by_id.keys()
    if unknown_skills:
        raise ValueError(
            f"Role '{role_id}' references unknown skill(s): {', '.join(sorted(unknown_skills))}."
        )
    for skill_id, weight in skill_weights.items():
        if (
            not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not 0 < weight <= 1
        ):
            raise ValueError(f"Role '{role_id}' has an invalid weight for skill '{skill_id}'.")

    taxonomy = [taxonomy_by_id[skill_id] for skill_id in skill_weights]
    total = len(posting_texts)
    mentions = count_mentions(taxonomy, posting_texts)
    corpus = _Corpus(snap)

    rows = []
    for sk in taxonomy:
        n = mentions[sk["id"]]
        pct = n / total if total >= MIN_MARKET_POSTINGS else None
        detectable = sk.get("detectable", True)
        evidence = repo_evidence(sk, corpus, signals) if detectable else []
        in_repo = bool(evidence)
        if not detectable:
            evidence_status = "not_assessed"
        elif in_repo:
            evidence_status = "evidence_found"
        else:
            evidence_status = "no_evidence_found"
        market_status = (
            "insufficient_market_data"
            if total < MIN_MARKET_POSTINGS
            else "assessed"
        )

        if not detectable:
            status = "not_detectable"
        elif total == 0:
            status = "market_unavailable"
        elif total < MIN_MARKET_POSTINGS:
            status = "market_insufficient"
        elif n > 0 and in_repo:
            status = "strength"
        elif n > 0 and pct >= GAP_MIN_PCT and not in_repo:
            status = "gap"
        elif n > 0:
            status = "minor_gap"
        elif in_repo:
            status = "extra"
        else:
            status = "not_mentioned"

        rows.append({
            "id": sk["id"], "label": sk["label"], "postings_mentioning": n, "total_postings": total,
            "pct": round(pct, 3) if pct is not None else None,
            "role_weight": skill_weights[sk["id"]],
            "in_repo": in_repo, "evidence": evidence,
            "detectable": detectable, "evidence_status": evidence_status,
            "market_status": market_status,
            "minimum_postings_for_market_assessment": MIN_MARKET_POSTINGS,
            "status": status, "fix_id": sk.get("fix_id"),
        })

    rows.sort(key=lambda r: (_STATUS_ORDER[r["status"]], -(r["pct"] or 0), r["label"]))
    return rows
