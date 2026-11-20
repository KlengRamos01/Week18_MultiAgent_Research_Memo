"""Minimal OpenAI-compatible chat client shared by the writer and reviewer.

Uses the same endpoint, model and temperature as the supplied baseline. Provider
usage is returned untouched so token counts are reported, never invented.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from config import require_model_config

DEFAULT_TIMEOUT = 120
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Raised when the provider call fails; never includes response bodies."""


def _post(payload: dict, config: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        config["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + config["api_key"],
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def chat(
    messages: list[dict],
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 2,
) -> dict:
    """Send a chat completion request and return text plus provider usage."""
    config = require_model_config()
    payload = {"model": config["model"], "messages": messages, "temperature": temperature}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = _post(payload, config, timeout)
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUS or attempt == retries:
                raise LLMError(f"Provider HTTP error {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == retries:
                raise LLMError("Provider connection failed") from None
        time.sleep(1.5 * (attempt + 1))
    else:  # pragma: no cover - loop always breaks or raises
        raise LLMError("Provider call failed") from last_error

    try:
        text = result["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError):
        raise LLMError("Provider returned an unexpected response shape") from None
    if not isinstance(text, str) or not text.strip():
        raise LLMError("Provider returned no text")

    usage = result.get("usage") or {}
    return {
        "text": text.strip(),
        "usage": usage,
        "total_tokens": usage.get("total_tokens"),
        "model": result.get("model", config["model"]),
    }


def extract_json(text: str) -> dict:
    """Return the first complete JSON object in text, tolerating code fences."""
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object in response")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])
    raise ValueError("Unbalanced JSON object in response")


def chat_json(messages: list[dict], *, retries: int = 1, **kwargs) -> dict:
    """Ask for JSON and parse it, retrying once with a stricter reminder."""
    result = chat(messages, **kwargs)
    try:
        result["data"] = extract_json(result["text"])
        return result
    except (ValueError, json.JSONDecodeError):
        if retries <= 0:
            raise LLMError("Provider did not return valid JSON") from None
    reminder = list(messages) + [
        {"role": "user", "content": "Return only one valid JSON object, with no prose or code fences."}
    ]
    result = chat(reminder, **kwargs)
    try:
        result["data"] = extract_json(result["text"])
    except (ValueError, json.JSONDecodeError):
        raise LLMError("Provider did not return valid JSON") from None
    return result
