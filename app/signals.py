"""Deterministic repo checks. No LLM here: every signal is a fact we can point to.

Each Signal carries `evidence` (file paths) so the UI and the LLM can cite it.
Add a new check by writing one more `_add(...)` call in compute_signals().
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .github_client import RepoSnapshot


@dataclass
class Signal:
    name: str
    label: str
    value: bool
    evidence: list[str] = field(default_factory=list)
    good_when_true: bool = True  # False for things like "committed .env file"
    note: str = ""


_TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.(js|jsx|ts|tsx)$",
    re.I,
)
_METRIC_RE = re.compile(
    r"\b\d+(\.\d+)?\s?%|\b(accuracy|f1|precision|recall|auc|roc-auc|rmse|mae|latency|p95|throughput)\b[^.\n]{0,40}\d",
    re.I,
)
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)|<img\s", re.I)
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.I)
_IGNORED_URL_PARTS = (
    "github.com", "shields.io", "githubusercontent.com", "badge", "docs.python.org",
    "pypi.org", "creativecommons.org", "opensource.org", "img.",
)
_DEP_FILES = {
    "requirements.txt", "pyproject.toml", "package.json", "pipfile", "environment.yml",
    "go.mod", "cargo.toml", "pom.xml",
}
_DEPLOY_FILES = {"render.yaml", "vercel.json", "procfile", "fly.toml", "netlify.toml", "dockerfile"}
_ENV_EXAMPLE_BASENAMES = {".env.example", ".env.sample", ".env.template"}
_ENV_KEY_PATTERNS = (
    re.compile(r"""os\.getenv\s*\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
    re.compile(r"""os\.environ(?:\.get)?\s*\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
    re.compile(r"""os\.environ\.get\s*\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
    re.compile(r"""getenv\s*\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
    re.compile(r"\bprocess\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
)
_ENV_CONFIGURATION_RE = re.compile(
    r"\b(?:BaseSettings|load_dotenv|dotenv_values)\b|"
    r"\bos\.environ\b|\bos\.getenv\b|\bprocess\.env\b|\bgetenv\s*\(",
    re.I,
)
_ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:#\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)
_CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".rb", ".php", ".kt", ".swift",
}


def _match(tree: list[str], pred, limit: int = 3) -> list[str]:
    return [p for p in tree if pred(p)][:limit]


def _base(p: str) -> str:
    return p.split("/")[-1].lower()


def compute_signals(snap: RepoSnapshot) -> dict[str, Signal]:
    tree = snap.tree
    readme = snap.readme_text
    readme_path = snap.readme_path
    out: dict[str, Signal] = {}

    def _add(name, label, value, evidence=None, good=True, note=""):
        out[name] = Signal(name, label, bool(value), evidence or [], good, note)

    _add("has_readme", "Has a README", bool(readme_path), [readme_path] if readme_path else [])
    _add("readme_substantial", "README is more than a stub (1,200+ characters)", len(readme) >= 1200,
         [readme_path] if readme_path else [])
    metric_hit = bool(_METRIC_RE.search(readme))
    _add("readme_has_metrics", "README shows measured results", metric_hit,
         [readme_path] if metric_hit else [],
         note="Heuristic only: looks for metric names near numbers or percentage values; it does not verify methodology.")
    _add("readme_has_screenshot", "README has a screenshot or demo image", bool(_IMG_RE.search(readme)),
         [readme_path] if _IMG_RE.search(readme) else [])

    tests = _match(tree, lambda p: bool(_TEST_RE.search(p)))
    _add("has_tests", "Has automated tests", bool(tests), tests)

    ci = _match(tree, lambda p: p.startswith(".github/workflows/"))
    _add("has_ci", "Runs CI (GitHub Actions)", bool(ci), ci)

    docker = _match(tree, lambda p: _base(p) == "dockerfile")
    _add("has_dockerfile", "Has a Dockerfile", bool(docker), docker)

    deploy = _match(tree, lambda p: _base(p) in _DEPLOY_FILES)
    urls = [u for u in _URL_RE.findall(readme) if not any(x in u.lower() for x in _IGNORED_URL_PARTS)]
    live = bool(snap.homepage) or bool(urls)
    live_evidence = []
    if snap.homepage or urls:
        live_evidence.append(readme_path or "repo homepage field")
    _add("has_live_demo", "Has a detectable live demo URL", live, live_evidence,
         note="Heuristic: repo homepage field or a non-GitHub link in the README; URL availability is not checked.")
    _add(
        "has_deployment_config",
        "Has a deployment configuration",
        bool(deploy),
        deploy,
    )

    lic = _match(tree, lambda p: "/" not in p and _base(p).startswith(("license", "licence", "copying")))
    _add("has_license", "Has a license", bool(lic), lic)

    envex = _match(tree, lambda p: _base(p) in _ENV_EXAMPLE_BASENAMES)
    _add("has_env_example", "Has a .env.example", bool(envex), envex)
    env_example_text = "\n".join(
        text for path, text in snap.files.items()
        if _base(path) in _ENV_EXAMPLE_BASENAMES
    )
    declared_env_keys = set(_ENV_ASSIGNMENT_RE.findall(env_example_text))
    env_references: set[str] = set()
    env_evidence: list[str] = []
    for path, text in snap.files.items():
        suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        if suffix not in _CODE_EXTENSIONS or not _ENV_CONFIGURATION_RE.search(text):
            continue
        env_evidence.append(path)
        for pattern in _ENV_KEY_PATTERNS:
            env_references.update(pattern.findall(text))
    requires_env = bool(env_evidence)
    missing_env_keys = sorted(env_references - declared_env_keys)
    _add(
        "requires_env_config",
        "Uses environment-based configuration",
        requires_env,
        env_evidence[:3],
    )
    complete_env_example = bool(envex) and (
        not env_references or not missing_env_keys
    )
    _add(
        "env_example_complete",
        "The environment example covers detected environment variables",
        complete_env_example,
        envex if complete_env_example else [],
        note=(
            f"Detected environment variables missing from the example: {', '.join(missing_env_keys)}."
            if missing_env_keys else ""
        ),
    )

    committed = _match(
        tree,
        lambda p: _base(p) == ".env" or (_base(p).endswith(".env") and not _base(p).endswith((".example.env", ".sample.env"))),
    )
    _add("committed_env_file", "A .env file is committed (secrets risk)", bool(committed), committed, good=False)

    deps = _match(tree, lambda p: _base(p) in _DEP_FILES)
    _add("has_dependency_file", "Lists its dependencies", bool(deps), deps)

    has_nb = any(p.lower().endswith(".ipynb") for p in tree)
    has_code = any(p.lower().endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go")) for p in tree)
    nb = _match(tree, lambda p: p.lower().endswith(".ipynb"))
    _add("notebooks_only", "Only notebooks, no importable code", has_nb and not has_code, nb, good=False)

    return out
