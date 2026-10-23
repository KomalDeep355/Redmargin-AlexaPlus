"""One client for OpenAI-compatible endpoints, with an explicit no-model mode."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

from . import config


class LLMError(Exception):
    """A safe, user-displayable message for model configuration or provider failures."""


def validate_provider_settings() -> None:
    if config.LLM_PROVIDER == "mock":
        return
    if config.LLM_PROVIDER != "openai_compat":
        raise LLMError("Unsupported LLM_PROVIDER; use 'mock' or 'openai_compat'.")
    if not config.LLM_MODEL.strip():
        raise LLMError("LLM_MODEL must be set when LLM_PROVIDER is 'openai_compat'.")
    parsed = urlsplit(config.LLM_BASE_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise LLMError("LLM_BASE_URL must be a valid HTTP(S) base URL without embedded credentials.")
    if not config.LLM_API_KEY.strip():
        raise LLMError("LLM_API_KEY must be set when LLM_PROVIDER is 'openai_compat'.")


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise LLMError("The model did not return valid JSON.")
        try:
            result = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("The model did not return valid JSON.") from exc
    if not isinstance(result, dict):
        raise LLMError("The model response must be a JSON object.")
    return result


def _supports_response_format_retry(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False
    try:
        body = response.json()
    except ValueError:
        body = None
    message = ""
    if isinstance(body, dict):
        error = body.get("error")
        message = error.get("message", "") if isinstance(error, dict) else ""
    return "response_format" in str(message).lower() or "json_object" in str(message).lower()


async def complete_json(system: str, user: str) -> dict | None:
    """Return a JSON object, or None in mock mode; provider failures raise LLMError."""
    validate_provider_settings()
    if config.LLM_PROVIDER == "mock":
        return None

    payload = {
        "model": config.LLM_MODEL,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{config.LLM_BASE_URL}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url, headers=headers, json={**payload, "response_format": {"type": "json_object"}}
            )
            if _supports_response_format_retry(response):
                response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise LLMError("The LLM request timed out. Check the endpoint or increase LLM_TIMEOUT_SECONDS.") from exc
    except httpx.RequestError as exc:
        raise LLMError("Could not connect to the configured LLM endpoint. Check LLM_BASE_URL and network access.") from exc

    if response.status_code >= 400:
        raise LLMError(f"The LLM provider returned HTTP {response.status_code}. Check provider settings and quota.")
    try:
        response_data = response.json()
    except ValueError as exc:
        raise LLMError("The LLM provider returned invalid JSON.") from exc
    if not isinstance(response_data, dict):
        raise LLMError("The LLM provider returned an unexpected response.")
    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMError("The LLM response is missing its choices.")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise LLMError("The LLM response is missing message content.")
    return _extract_json(message["content"])
