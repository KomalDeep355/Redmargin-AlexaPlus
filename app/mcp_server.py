
import json

from mcp.server.fastmcp import FastMCP

from app.schemas import ReviewRequest
from app.reviewer import run_review
from app.postings import load_roles

mcp = FastMCP(
    "Redmargin Career Agent",
    json_response=True,
)


@mcp.tool()
async def review_repository(
    repo_url: str,
    role: str = "fde",
    hours_per_day: float = 2.0,
    pasted_postings: list[str] | None = None,
) -> str:
    """Review a public GitHub repository for a target career role.

    Return evidence-backed strengths, missing evidence, and prioritized
    improvements. Missing repository evidence does not prove missing ability.
    Pasted job descriptions must be real postings supplied by the user.
    """
    request = ReviewRequest(
        repo_url=repo_url,
        role=role,
        hours_per_day=hours_per_day,
        pasted_postings=pasted_postings or [],
    )

    result = await run_review(request)

    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    elif hasattr(result, "dict"):
        result = result.dict()

    return json.dumps(result, default=str)


@mcp.tool()
def get_supported_roles() -> str:
    """Return the career roles configured in Redmargin."""
    return json.dumps(load_roles(), default=str)


if __name__ == "__main__":
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = 8001
    mcp.settings.mount_path = "/"

    mcp.run(transport="streamable-http")
