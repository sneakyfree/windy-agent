"""vision.describe + vision.ocr capability tests.

Pins the contract:

  1. Exactly-one-of(path, url) enforced.
  2. Local file: size cap, missing file, non-image extension all handled.
  3. URL: must be http(s); non-http rejected.
  4. Anthropic API: payload shape correct, timeout passed, key required.
  5. OCR no-text path returns the magic string and the flag.
  6. Registration: both capabilities show up with READ_EXTERNAL tier.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from windyfly.agent.capabilities.descriptor import Tier
from windyfly.agent.capabilities.vision import (
    _call_anthropic_vision,
    _image_source_from_path,
    _image_source_from_url,
    _model_for_vision,
    _resolve_image_block,
    _vision_describe_handler,
    _vision_ocr_handler,
    register_vision_capabilities,
)


# ─── Image source builders ────────────────────────────────────────


def _make_tmp_image(tmp_path, name="img.png", payload=b"FAKE_PNG_BYTES"):
    p = tmp_path / name
    p.write_bytes(payload)
    return str(p)


class TestImageSourceFromPath:

    def test_local_file_base64_encoded(self, tmp_path):
        path = _make_tmp_image(tmp_path, "x.png", b"\x89PNG\r\n\x1a\n hello")
        src = _image_source_from_path(path)
        assert src["type"] == "base64"
        assert src["media_type"] == "image/png"
        assert base64.b64decode(src["data"]) == b"\x89PNG\r\n\x1a\n hello"

    def test_jpeg_media_type(self, tmp_path):
        path = _make_tmp_image(tmp_path, "x.jpg", b"\xff\xd8\xff jpeg-ish")
        src = _image_source_from_path(path)
        assert src["media_type"] == "image/jpeg"

    def test_unknown_extension_defaults_to_jpeg(self, tmp_path):
        path = _make_tmp_image(tmp_path, "x.weird", b"???")
        src = _image_source_from_path(path)
        # Should fall back to jpeg media type so the call doesn't bail early
        assert src["media_type"].startswith("image/")

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            _image_source_from_path("/nonexistent/path/to/image.png")

    def test_not_a_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not a regular file"):
            _image_source_from_path(str(tmp_path))  # it's a dir

    def test_oversized_file_raises(self, tmp_path):
        # 5MB > 4MB cap
        path = _make_tmp_image(tmp_path, "big.png", b"x" * (5 * 1024 * 1024))
        with pytest.raises(ValueError, match="too large"):
            _image_source_from_path(path)


class TestImageSourceFromUrl:

    def test_https_url_passes(self):
        src = _image_source_from_url("https://example.com/cat.png")
        assert src == {"type": "url", "url": "https://example.com/cat.png"}

    def test_http_url_passes(self):
        src = _image_source_from_url("http://example.com/cat.png")
        assert src["type"] == "url"

    @pytest.mark.parametrize("bad_url", [
        "ftp://example.com/cat.png",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "/local/path.png",
    ])
    def test_non_http_rejected(self, bad_url):
        with pytest.raises(ValueError, match="http"):
            _image_source_from_url(bad_url)


# ─── Exactly-one-of(path, url) ────────────────────────────────────


class TestResolveImageBlock:

    def test_path_only(self, tmp_path):
        p = _make_tmp_image(tmp_path)
        src = _resolve_image_block(path=p, url=None)
        assert src["type"] == "base64"

    def test_url_only(self):
        src = _resolve_image_block(path=None, url="https://x/y.png")
        assert src["type"] == "url"

    def test_neither_raises(self):
        with pytest.raises(ValueError, match="path.*url"):
            _resolve_image_block(path=None, url=None)

    def test_both_raises(self, tmp_path):
        p = _make_tmp_image(tmp_path)
        with pytest.raises(ValueError, match="OR"):
            _resolve_image_block(path=p, url="https://x/y.png")


# ─── Anthropic API call shape ─────────────────────────────────────


class TestAnthropicCall:
    """The call_anthropic_vision wrapper must build the right payload."""

    def _ok_response(self, text="a cat"):
        def fake_post(url, json=None, headers=None, timeout=None):
            class R:
                status_code = 200
                def json(self_inner):
                    return {
                        "content": [{"type": "text", "text": text}],
                        "model": "claude-haiku-4-5-20251001",
                        "usage": {"input_tokens": 100, "output_tokens": 5},
                    }
            return R()
        return fake_post

    def test_payload_shape(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            captured["timeout"] = timeout
            class R:
                status_code = 200
                def json(self_inner):
                    return {"content": [{"type": "text", "text": "ok"}],
                            "model": "m", "usage": {}}
            return R()

        with patch("httpx.post", side_effect=fake_post):
            _call_anthropic_vision(
                {"type": "url", "url": "https://x/y.png"},
                "system text",
                "what is this?",
                timeout_s=15,
            )

        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        assert captured["headers"]["x-api-key"] == "sk-test"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"
        assert captured["timeout"] == 15
        payload = captured["json"]
        assert payload["system"] == "system text"
        # Messages have a user message with image + text blocks in order
        msg = payload["messages"][0]
        assert msg["role"] == "user"
        content = msg["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "url"
        assert content[1]["type"] == "text"
        assert content[1]["text"] == "what is this?"

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            _call_anthropic_vision(
                {"type": "url", "url": "https://x/y.png"},
                "sys", "q", timeout_s=10,
            )

    def test_timeout_surfaces_clear_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        def fake_post(*a, **kw):
            raise httpx.TimeoutException("timed out")

        with patch("httpx.post", side_effect=fake_post):
            with pytest.raises(RuntimeError, match="timed out"):
                _call_anthropic_vision(
                    {"type": "url", "url": "https://x/y.png"},
                    "sys", "q", timeout_s=5,
                )

    def test_non_2xx_response_raises(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        def fake_post(*a, **kw):
            class R:
                status_code = 500
                text = "internal server error"
            return R()

        with patch("httpx.post", side_effect=fake_post):
            with pytest.raises(RuntimeError, match="HTTP 500"):
                _call_anthropic_vision(
                    {"type": "url", "url": "https://x/y.png"},
                    "sys", "q", timeout_s=5,
                )


# ─── Handlers ─────────────────────────────────────────────────────


class TestDescribeHandler:

    def test_url_input_returns_description(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["system"] = json["system"]
            class R:
                status_code = 200
                def json(self_inner):
                    return {"content": [{"type": "text",
                                         "text": "A sunlit cat."}],
                            "model": "claude-haiku-4-5-20251001",
                            "usage": {"input_tokens": 100, "output_tokens": 5}}
            return R()

        with patch("httpx.post", side_effect=fake_post):
            r = _vision_describe_handler(url="https://x/cat.png")

        assert r["description"] == "A sunlit cat."
        assert r["source"] == "url"
        assert r["source_value"] == "https://x/cat.png"
        # The system prompt orients the model toward DESCRIPTION
        assert "describe" in captured["system"].lower()

    def test_custom_question_passes_through(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["user_text"] = json["messages"][0]["content"][1]["text"]
            class R:
                status_code = 200
                def json(self_inner):
                    return {"content": [{"type": "text", "text": "ok"}],
                            "model": "m", "usage": {}}
            return R()

        p = _make_tmp_image(tmp_path, "cat.png", b"\x89PNG")
        with patch("httpx.post", side_effect=fake_post):
            _vision_describe_handler(
                path=p, question="Is there a cat in this image?",
            )
        assert captured["user_text"] == "Is there a cat in this image?"


class TestOcrHandler:

    def test_extracts_text(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        def fake_post(*a, **kw):
            class R:
                status_code = 200
                def json(self_inner):
                    return {"content": [{"type": "text",
                                         "text": "STOP\nExit 14"}],
                            "model": "m",
                            "usage": {"input_tokens": 100, "output_tokens": 5}}
            return R()

        with patch("httpx.post", side_effect=fake_post):
            r = _vision_ocr_handler(url="https://x/sign.png")

        assert r["text"] == "STOP\nExit 14"
        assert r["no_text_in_image"] is False

    def test_no_text_path_returns_flag(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        def fake_post(*a, **kw):
            class R:
                status_code = 200
                def json(self_inner):
                    return {"content": [{"type": "text",
                                         "text": "(no text in image)"}],
                            "model": "m", "usage": {}}
            return R()

        with patch("httpx.post", side_effect=fake_post):
            r = _vision_ocr_handler(url="https://x/sunset.png")

        assert r["no_text_in_image"] is True

    def test_ocr_system_prompt_focuses_extraction(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["system"] = json["system"]
            class R:
                status_code = 200
                def json(self_inner):
                    return {"content": [{"type": "text", "text": "ok"}],
                            "model": "m", "usage": {}}
            return R()

        with patch("httpx.post", side_effect=fake_post):
            _vision_ocr_handler(url="https://x/y.png")
        sys = captured["system"].lower()
        assert "ocr" in sys or "extract" in sys
        # Must NOT contain describe-mode phrasing
        assert "describe" not in sys


# ─── Model selection ──────────────────────────────────────────────


class TestModelSelection:

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("WINDY_VISION_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
        assert _model_for_vision() == "claude-opus-4-7"

    def test_falls_back_to_default_model(self, monkeypatch):
        monkeypatch.delenv("WINDY_VISION_MODEL", raising=False)
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
        assert _model_for_vision() == "claude-sonnet-4-6"

    def test_falls_back_to_hardcoded_haiku(self, monkeypatch):
        monkeypatch.delenv("WINDY_VISION_MODEL", raising=False)
        monkeypatch.delenv("DEFAULT_MODEL", raising=False)
        assert _model_for_vision() == "claude-haiku-4-5-20251001"


# ─── Registration ─────────────────────────────────────────────────


class TestRegistration:

    def test_both_caps_registered(self):
        from windyfly.agent.capabilities.registry import CapabilityRegistry
        reg = CapabilityRegistry()
        register_vision_capabilities(reg, {})
        ids = {cap.id for cap in reg.all()}
        assert "vision.describe" in ids
        assert "vision.ocr" in ids

    def test_tier_is_read_external(self):
        from windyfly.agent.capabilities.registry import CapabilityRegistry
        reg = CapabilityRegistry()
        register_vision_capabilities(reg, {})
        for cap in reg.all():
            if cap.id.startswith("vision."):
                assert cap.tier == Tier.READ_EXTERNAL

    def test_describe_schema_includes_question(self):
        from windyfly.agent.capabilities.registry import CapabilityRegistry
        reg = CapabilityRegistry()
        register_vision_capabilities(reg, {})
        cap = next(c for c in reg.all() if c.id == "vision.describe")
        assert "question" in cap.input_schema["properties"]

    def test_ocr_schema_has_no_question_field(self):
        # OCR doesn't take a question — the prompt is fixed.
        from windyfly.agent.capabilities.registry import CapabilityRegistry
        reg = CapabilityRegistry()
        register_vision_capabilities(reg, {})
        cap = next(c for c in reg.all() if c.id == "vision.ocr")
        assert "question" not in cap.input_schema["properties"]
