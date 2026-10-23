"""Central settings. Everything configurable lives in .env — never hard-code keys."""
import logging
import math
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"


logger = logging.getLogger(__name__)


def _positive_int(name: str, default: int, maximum: int) -> int:
    value = os.getenv(name)
    try:
        parsed = int(value) if value is not None else default
        if parsed <= 0 or parsed > maximum:
            raise ValueError
        return parsed
    except ValueError:
        logger.warning(
            "Invalid value for %s (expected 1-%d); using default %d.",
            name,
            maximum,
            default,
        )
        return default


def _positive_float(name: str, default: float, maximum: float) -> float:
    value = os.getenv(name)
    try:
        parsed = float(value) if value is not None else default
        if not math.isfinite(parsed) or parsed <= 0 or parsed > maximum:
            raise ValueError
        return parsed
    except ValueError:
        logger.warning(
            "Invalid value for %s (expected a positive number no greater than %s); using default %s.",
            name,
            maximum,
            default,
        )
        return default


# LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()  # "mock" | "openai_compat"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
LLM_TIMEOUT_SECONDS = _positive_float("LLM_TIMEOUT_SECONDS", 90.0, 300.0)

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Limits: keep prompts small and GitHub calls cheap
MAX_SOURCE_FILES = _positive_int("MAX_SOURCE_FILES", 12, 50)
MAX_CHARS_PER_FILE = _positive_int("MAX_CHARS_PER_FILE", 6000, 100000)
MAX_PASTED_POSTINGS_CHARS = _positive_int("MAX_PASTED_POSTINGS_CHARS", 50000, 500000)
MAX_POSTINGS = _positive_int("MAX_POSTINGS", 50, 200)
MAX_REPO_URL_CHARS = _positive_int("MAX_REPO_URL_CHARS", 2048, 4096)
MAX_REPO_TREE_PATHS = _positive_int("MAX_REPO_TREE_PATHS", 10000, 100000)
MAX_REPO_API_RESPONSE_BYTES = _positive_int("MAX_REPO_API_RESPONSE_BYTES", 20000000, 100000000)
MAX_REPO_FILES_TO_FETCH = _positive_int("MAX_REPO_FILES_TO_FETCH", 24, 64)
MAX_GITHUB_CONCURRENT_REQUESTS = _positive_int("MAX_GITHUB_CONCURRENT_REQUESTS", 5, 20)
MAX_PROMPT_REPO_CHARS = _positive_int("MAX_PROMPT_REPO_CHARS", 10000, 100000)
MAX_TREE_PATHS_IN_PROMPT = 150
MCP_SERVER_URL = os.getenv(
    "MCP_SERVER_URL",
    "http://127.0.0.1:8001/mcp",
)