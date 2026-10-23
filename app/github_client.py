"""Fetch a public GitHub repo into an in-memory RepoSnapshot (no git clone needed)."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

import httpx

from . import config

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
class RepoError(Exception):
    """Raised with a message that is safe to show to the user."""


@dataclass
class RepoSnapshot:
    owner: str
    name: str
    branch: str
    description: str
    homepage: str
    stars: int
    pushed_at: str
    primary_language: str
    tree: list[str]  # every file path in the repo
    tree_truncated: bool
    files: dict[str, str] = field(default_factory=dict)  # path -> text (only files we read)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def readme_path(self) -> str:
        for p in self.files:
            if "/" not in p and p.lower().startswith("readme"):
                return p
        return ""

    @property
    def readme_text(self) -> str:
        return self.files.get(self.readme_path, "")


_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


def parse_repo_url(url: str) -> tuple[str, str]:
    """Parse exactly one GitHub repository URL (optionally ending in .git or /)."""
    if not isinstance(url, str) or not url.strip():
        raise RepoError("Enter a GitHub repository URL such as https://github.com/owner/repo.")
    if len(url) > config.MAX_REPO_URL_CHARS:
        raise RepoError(f"Repository URLs must be at most {config.MAX_REPO_URL_CHARS} characters.")
    try:
        parsed = urlsplit(url.strip())
    except ValueError as exc:
        raise RepoError("That GitHub repository URL is malformed.") from exc
    if parsed.scheme.lower() != "https" or parsed.netloc.lower() != "github.com":
        raise RepoError("Repository URLs must use https://github.com/owner/repo.")
    if parsed.query or parsed.fragment:
        raise RepoError("Repository URLs must not include a query string or fragment.")
    path = parsed.path
    if not path.startswith("/") or path.startswith("//"):
        raise RepoError("Use the repository path /owner/repo, with no extra path segments.")
    parts = path[1:].split("/")
    if parts and parts[-1] == "":
        parts.pop()
    if len(parts) != 2 or not all(parts) or "%" in path:
        raise RepoError("Use the repository path /owner/repo, with no extra path segments.")
    owner, name = parts
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not _OWNER_RE.fullmatch(owner) or not _REPO_RE.fullmatch(name) or name.endswith("."):
        raise RepoError("The GitHub owner or repository name is malformed.")
    return owner, name


def _valid_tree_path(path: str) -> bool:
    parts = path.split("/")
    return (
        bool(path)
        and len(path) <= 1024
        and not path.startswith("/")
        and "\\" not in path
        and all(part not in {"", ".", ".."} for part in parts)
    )


def _is_rate_limited(response: httpx.Response) -> bool:
    if (
        response.status_code == 429
        or response.headers.get("X-RateLimit-Remaining") == "0"
        or "Retry-After" in response.headers
    ):
        return True
    if response.status_code != 403:
        return False
    if not response.is_stream_consumed:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    message = payload.get("message", "") if isinstance(payload, dict) else ""
    return isinstance(message, str) and "rate limit" in message.casefold()


# ---- which files are worth reading -------------------------------------------------------
SKIP_DIRS = {
    "node_modules", "venv", ".venv", "env", "__pycache__", "dist", "build", ".git",
    "vendor", "site-packages", ".next", "target", "coverage", ".idea", ".vscode",
}
CONFIG_BASENAMES = {
    "requirements.txt", "pyproject.toml", "package.json", "dockerfile", "docker-compose.yml",
    "docker-compose.yaml", "render.yaml", "vercel.json", "procfile", "fly.toml", "netlify.toml",
    "environment.yml", "setup.py", "pipfile", "go.mod", "cargo.toml", "pom.xml",
    ".env.example", ".env.sample", ".env.template",
}
SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cpp", ".c",
    ".cs", ".rb", ".php", ".kt", ".swift",
}
ENTRY_STEMS = {"main", "app", "index", "server", "cli", "__main__", "api"}


def _in_skipped_dir(path: str) -> bool:
    return any(part in SKIP_DIRS for part in path.split("/")[:-1])


def choose_files(tree: list[str]) -> list[str]:
    """Pick the few files that tell us the most. Order = priority."""
    chosen: list[str] = []

    readmes = sorted((p for p in tree if "/" not in p and p.lower().startswith("readme")), key=len)
    chosen += readmes[:1]
    chosen += [
        p for p in tree
        if "/" not in p and p.lower() in {".env.example", ".env.sample", ".env.template"}
    ][:1]

    chosen += [
        p for p in tree
        if p.split("/")[-1].lower() in CONFIG_BASENAMES
        and p.count("/") <= 2 and not _in_skipped_dir(p)
    ][:8]

    chosen += [p for p in tree if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))][:2]

    def is_source(p: str) -> bool:
        base = p.split("/")[-1]
        ext = "." + base.rsplit(".", 1)[-1].lower() if "." in base else ""
        return ext in SOURCE_EXTS and not _in_skipped_dir(p)

    def rank(p: str):
        stem = p.split("/")[-1].rsplit(".", 1)[0].lower()
        return (0 if stem in ENTRY_STEMS else 1, p.count("/"), p)

    sources = sorted((p for p in tree if is_source(p)), key=rank)
    chosen += sources[: config.MAX_SOURCE_FILES]

    seen, out = set(), []
    for p in chosen:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:config.MAX_REPO_FILES_TO_FETCH]


# ---- HTTP -------------------------------------------------------------------------------
async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    try:
        async with client.stream("GET", url) as r:
            if r.status_code == 404:
                raise RepoError(
                    "Repo not found. It may be private, or the URL is wrong. Only public repos work for now."
                )
            if r.status_code == 409:
                raise RepoError("That repo is empty — nothing to review yet.")
            if _is_rate_limited(r):
                raise RepoError("GitHub's API rate limit was reached. Add a GITHUB_TOKEN or try again later.")
            if not r.is_success:
                raise RepoError(f"GitHub returned an unexpected error ({r.status_code}). Please try again.")
            content_length = r.headers.get("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > config.MAX_REPO_API_RESPONSE_BYTES:
                raise RepoError(
                    f"GitHub's response exceeded the {config.MAX_REPO_API_RESPONSE_BYTES}-byte limit."
                )
            body = bytearray()
            async for chunk in r.aiter_bytes(chunk_size=min(65536, config.MAX_REPO_API_RESPONSE_BYTES)):
                if len(body) + len(chunk) > config.MAX_REPO_API_RESPONSE_BYTES:
                    raise RepoError(
                        f"GitHub's response exceeded the {config.MAX_REPO_API_RESPONSE_BYTES}-byte limit."
                    )
                body.extend(chunk)
    except httpx.TimeoutException as e:
        raise RepoError("The GitHub request timed out. Please try again.") from e
    except httpx.RequestError as e:
        raise RepoError("Could not connect to GitHub. Check the server's network connection and try again.") from e
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RepoError("GitHub returned invalid JSON. Please try again later.") from e
    if not isinstance(payload, dict):
        raise RepoError("GitHub returned an unexpected response. Please try again later.")
    return payload


async def _get_text(client: httpx.AsyncClient, url: str, path: str) -> str:
    try:
        async with client.stream("GET", url, headers={"Authorization": ""}) as r:
            if r.status_code != 200:
                if _is_rate_limited(r):
                    raise RepoError("GitHub's rate limit was reached while reading repository files.")
                raise RepoError(f"GitHub could not return repository file {path} (HTTP {r.status_code}).")
            chunks = []
            total_chars = 0
            async for chunk in r.aiter_text(chunk_size=min(65536, config.MAX_CHARS_PER_FILE)):
                remaining_chars = config.MAX_CHARS_PER_FILE - total_chars
                chunks.append(chunk[:remaining_chars])
                total_chars += min(len(chunk), remaining_chars)
                if len(chunk) > remaining_chars or total_chars >= config.MAX_CHARS_PER_FILE:
                    break
    except httpx.TimeoutException as e:
        raise RepoError(f"Timed out while reading {path} from GitHub.") from e
    except httpx.RequestError as e:
        raise RepoError(f"Could not connect to GitHub while reading {path}.") from e
    return "".join(chunks)


async def fetch_snapshot(repo_url: str) -> RepoSnapshot:
    owner, name = parse_repo_url(repo_url)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "repo-reviewer"}
    repo_api_url = f"{API}/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"

    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
        meta = await _get_json(client, repo_api_url)
        branch = meta.get("default_branch")
        if (
            not isinstance(branch, str)
            or not branch
            or len(branch) > 255
            or any(ord(char) < 32 for char in branch)
        ):
            raise RepoError("GitHub returned repository metadata without a valid default branch.")
        tree_json = await _get_json(
            client, f"{repo_api_url}/git/trees/{quote(branch, safe='/')}?recursive=1"
        )
        tree_nodes = tree_json.get("tree")
        if not isinstance(tree_nodes, list):
            raise RepoError("GitHub returned an invalid repository tree.")
        if len(tree_nodes) > config.MAX_REPO_TREE_PATHS:
            raise RepoError(
                f"This repository has too many tree entries to review "
                f"(limit: {config.MAX_REPO_TREE_PATHS})."
            )
        tree = []
        for node in tree_nodes:
            if (
                not isinstance(node, dict)
                or not isinstance(node.get("path"), str)
                or node.get("type") not in {"blob", "tree", "commit"}
                or not _valid_tree_path(node["path"])
            ):
                raise RepoError("GitHub returned malformed paths in the repository tree.")
            if node["type"] == "blob":
                tree.append(node["path"])
        truncated = tree_json.get("truncated", False)
        if not isinstance(truncated, bool):
            raise RepoError("GitHub returned an invalid repository tree status.")

        for field, expected_type in (
            ("description", str),
            ("homepage", str),
            ("pushed_at", str),
            ("language", str),
        ):
            value = meta.get(field)
            if value is not None and not isinstance(value, expected_type):
                raise RepoError(f"GitHub returned invalid repository metadata for {field}.")
        stars = meta.get("stargazers_count", 0)
        if isinstance(stars, bool) or not isinstance(stars, int) or stars < 0:
            raise RepoError("GitHub returned an invalid repository star count.")

        paths = choose_files(tree)
        semaphore = asyncio.Semaphore(config.MAX_GITHUB_CONCURRENT_REQUESTS)

        async def read_file(path: str) -> str:
            raw_url = (
                f"{RAW}/{quote(owner, safe='')}/{quote(name, safe='')}/"
                f"{quote(branch, safe='/')}/{quote(path, safe='/')}"
            )
            async with semaphore:
                return await _get_text(client, raw_url, path)

        texts = await asyncio.gather(*(read_file(path) for path in paths))

    return RepoSnapshot(
        owner=owner,
        name=name,
        branch=branch,
        description=meta.get("description") if isinstance(meta.get("description"), str) else "",
        homepage=meta.get("homepage") if isinstance(meta.get("homepage"), str) else "",
        stars=stars,
        pushed_at=meta.get("pushed_at") if isinstance(meta.get("pushed_at"), str) else "",
        primary_language=meta.get("language") if isinstance(meta.get("language"), str) else "",
        tree=tree,
        tree_truncated=truncated,
        files={p: t for p, t in zip(paths, texts) if t},
    )
