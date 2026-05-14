"""vision.describe + vision.ocr capabilities — Tier 0 Stock Toolkit.

Two ways for the agent to look at an image:

  - ``vision.describe`` — natural-language description ("a cat sitting on
    a windowsill, sunlit, mid-afternoon").
  - ``vision.ocr`` — extract visible text verbatim ("STOP", "Exit 14",
    or the contents of a screenshot).

Both routes hit Anthropic's vision-capable Claude API directly (no
agent-loop recursion) with different prompts. We deliberately don't
ship local Tesseract here — a grandma should never have to install a
system binary to OCR a screenshot. Tier 1 (`vision.ocr_local`) can
add it later via the install wizard for users who want offline.

Inputs:
  - ``path``: local file path to an image (read + base64-encode)
  - ``url``:  http(s) URL (passed through; Anthropic fetches it)
  - exactly one of the two must be provided

The two route the same underlying call (`_call_anthropic_vision`)
with task-specific system prompts. That keeps the codepath narrow
and makes both describe and OCR get the same retry/timeout/error
shape "for free."
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx

from windyfly.agent.capabilities.descriptor import (
    Capability,
    Reversibility,
    SandboxTier,
    Tier,
)
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

_ANTHROPIC_VISION_URL = "https://api.anthropic.com/v1/messages"
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # fast + vision-capable
_DEFAULT_TIMEOUT_S = 30
_DEFAULT_MAX_TOKENS = 1024

# Bytes-cap on local files we'll base64-encode — Anthropic accepts
# images up to ~5MB after b64; we cap a bit under that.
_LOCAL_FILE_BYTES_CAP = 4 * 1024 * 1024

_DESCRIBE_SYSTEM = (
    "You are looking at an image on behalf of a user. Describe what you "
    "see in clear, natural language. Be specific about subjects, setting, "
    "and any notable details. Two to four sentences."
)

_OCR_SYSTEM = (
    "You are an OCR engine. Extract ALL visible text from the image, "
    "verbatim, preserving line breaks where meaningful. Do not add "
    "commentary, interpretation, or markdown. If the image contains no "
    "text, reply exactly: (no text in image)."
)


def _model_for_vision() -> str:
    """Pick a vision-capable model from env, falling back to a sane default.

    Reads ``WINDY_VISION_MODEL`` first (lets the operator override), then
    ``DEFAULT_MODEL`` (the bot's primary model — also vision-capable on
    the Claude 4 family), then the hardcoded haiku default.
    """
    return (
        os.environ.get("WINDY_VISION_MODEL")
        or os.environ.get("DEFAULT_MODEL")
        or _DEFAULT_MODEL
    )


def _image_source_from_path(path: str) -> dict[str, Any]:
    """Build the Anthropic content-block source object from a local path."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"image not found: {path}")
    if not p.is_file():
        raise ValueError(f"not a regular file: {path}")
    size = p.stat().st_size
    if size > _LOCAL_FILE_BYTES_CAP:
        raise ValueError(
            f"image too large ({size} bytes > {_LOCAL_FILE_BYTES_CAP}); "
            "Anthropic accepts ~5MB after base64. Resize or screenshot a "
            "smaller region."
        )
    media_type, _ = mimetypes.guess_type(str(p))
    if not media_type or not media_type.startswith("image/"):
        # mimetypes can miss e.g. .heic; let the API reject if we're wrong.
        media_type = "image/jpeg"
    data_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return {
        "type": "base64",
        "media_type": media_type,
        "data": data_b64,
    }


def _image_source_from_url(url: str) -> dict[str, Any]:
    """URL pass-through — Anthropic fetches it server-side."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"image url must be http(s): {url!r}")
    return {"type": "url", "url": url}


def _call_anthropic_vision(
    image_block: dict[str, Any],
    system_prompt: str,
    user_question: str,
    timeout_s: int,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Single call to Anthropic with an image content block.

    Returns ``{"text": str, "model": str, "input_tokens": int,
              "output_tokens": int}``. Raises ``RuntimeError`` on
    transport failure or non-2xx API response.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "vision needs ANTHROPIC_API_KEY in the environment. The bot "
            "uses it for the rest of its LLM calls; vision shares the "
            "same key."
        )

    mdl = model or _model_for_vision()
    payload = {
        "model": mdl,
        "max_tokens": _DEFAULT_MAX_TOKENS,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": image_block},
                    {"type": "text", "text": user_question},
                ],
            }
        ],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        resp = httpx.post(
            _ANTHROPIC_VISION_URL,
            json=payload,
            headers=headers,
            timeout=timeout_s,
        )
    except httpx.TimeoutException as e:
        raise RuntimeError(f"vision request timed out after {timeout_s}s") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"vision transport error: {e}") from e

    if resp.status_code != 200:
        # Trim long error bodies for the LLM/caller
        body = resp.text[:1000]
        raise RuntimeError(
            f"vision API returned HTTP {resp.status_code}: {body}"
        )

    data = resp.json()
    blocks = data.get("content") or []
    text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    usage = data.get("usage") or {}
    return {
        "text": "".join(text_parts).strip(),
        "model": data.get("model", mdl),
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
    }


def _resolve_image_block(
    path: str | None, url: str | None
) -> dict[str, Any]:
    """Validate exactly-one-of and return the source block."""
    if not path and not url:
        raise ValueError(
            "vision requires either 'path' (local file) or 'url' (http(s))"
        )
    if path and url:
        raise ValueError(
            "vision accepts 'path' OR 'url', not both"
        )
    if path:
        return _image_source_from_path(path)
    return _image_source_from_url(url or "")


def _vision_describe_handler(
    *,
    path: str | None = None,
    url: str | None = None,
    question: str = "Describe this image.",
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Natural-language description of an image."""
    src = _resolve_image_block(path, url)
    result = _call_anthropic_vision(
        src, _DESCRIBE_SYSTEM, question, timeout_s,
    )
    return {
        "description": result["text"],
        "source": "path" if path else "url",
        "source_value": path or url,
        "model": result["model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }


def _vision_ocr_handler(
    *,
    path: str | None = None,
    url: str | None = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Extract visible text from an image."""
    src = _resolve_image_block(path, url)
    result = _call_anthropic_vision(
        src, _OCR_SYSTEM,
        "Extract all visible text from this image.",
        timeout_s,
    )
    text = result["text"]
    return {
        "text": text,
        "no_text_in_image": text.lower().strip() == "(no text in image)",
        "source": "path" if path else "url",
        "source_value": path or url,
        "model": result["model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }


def register_vision_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register ``vision.describe`` and ``vision.ocr`` on the registry."""
    del config  # unused at v1; would carry model/timeout overrides later
    logger.info(
        "Registering vision.{describe,ocr} — model=%s", _model_for_vision(),
    )

    def describe(
        *, path: str | None = None,
        url: str | None = None,
        question: str = "Describe this image.",
        timeout_s: int = _DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        return _vision_describe_handler(
            path=path, url=url, question=question, timeout_s=timeout_s,
        )

    def ocr(
        *, path: str | None = None,
        url: str | None = None,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        return _vision_ocr_handler(
            path=path, url=url, timeout_s=timeout_s,
        )

    image_input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Local file path to an image. Exactly one of 'path' "
                    "or 'url' must be provided. Capped at 4MB."
                ),
            },
            "url": {
                "type": "string",
                "description": (
                    "Public http(s) URL to an image. Exactly one of "
                    "'path' or 'url' must be provided."
                ),
            },
            "timeout_s": {
                "type": "integer",
                "description": (
                    "Seconds to wait for the vision API. Default 30."
                ),
            },
        },
    }

    describe_schema = {
        **image_input_schema,
        "properties": {
            **image_input_schema["properties"],
            "question": {
                "type": "string",
                "description": (
                    "Optional question to focus the description. Default: "
                    "'Describe this image.'"
                ),
            },
        },
    }

    registry.register(Capability(
        id="vision.describe",
        description=(
            "Describe an image in natural language. Accepts a local file "
            "path OR an http(s) URL (exactly one). Uses Anthropic's "
            "vision-capable Claude — no local Tesseract install needed. "
            "Returns a two-to-four-sentence description. For text "
            "extraction use vision.ocr instead."
        ),
        handler=describe,
        input_schema=describe_schema,
        tier=Tier.READ_EXTERNAL,
        sandbox_tier=SandboxTier.NONE,
        reversibility=Reversibility.READ,
        scope="vision_api",
    ))

    registry.register(Capability(
        id="vision.ocr",
        description=(
            "Extract visible text from an image. Accepts a local file "
            "path OR an http(s) URL (exactly one). Uses Anthropic's "
            "vision API — no local Tesseract needed. Returns the text "
            "verbatim (or '(no text in image)' if blank). For natural-"
            "language description use vision.describe instead."
        ),
        handler=ocr,
        input_schema=image_input_schema,
        tier=Tier.READ_EXTERNAL,
        sandbox_tier=SandboxTier.NONE,
        reversibility=Reversibility.READ,
        scope="vision_api",
    ))
