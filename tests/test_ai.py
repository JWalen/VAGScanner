"""Tests for the multi-provider AI chat client (mocked, no network)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

pytest.importorskip("PySide6")  # ai ships in the gui package
from vcds_gui import ai  # noqa: E402


class FakeResp:
    def __init__(self, data: dict):
        self._d = json.dumps(data).encode("utf-8")

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _msgs():
    return [{"role": "user", "content": "Why is my boost low?"}]


def test_anthropic_request_and_parse():
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["body"] = json.loads(req.data)
        return FakeResp({"content": [{"type": "text", "text": "Likely a boost leak."}]})

    out = ai.chat("anthropic", "KEY", "claude-x", "sys-context", _msgs(), opener=opener)
    assert out == "Likely a boost leak."
    assert "api.anthropic.com" in seen["url"]
    assert seen["headers"]["x-api-key"] == "KEY"
    assert seen["body"]["system"] == "sys-context"


def test_openai_request_and_parse():
    def opener(req, timeout):
        body = json.loads(req.data)
        assert body["messages"][0] == {"role": "system", "content": "sys"}
        assert "Bearer K" in dict((k.lower(), v) for k, v in req.header_items())["authorization"]
        return FakeResp({"choices": [{"message": {"content": "GPT reply"}}]})

    assert ai.chat("openai", "K", "gpt-4o", "sys", _msgs(), opener=opener) == "GPT reply"


def test_gemini_request_and_parse():
    def opener(req, timeout):
        assert "generativelanguage.googleapis.com" in req.full_url
        assert "key=K" in req.full_url
        body = json.loads(req.data)
        assert body["systemInstruction"]["parts"][0]["text"] == "sys"
        return FakeResp({"candidates": [{"content": {"parts": [{"text": "Gemini reply"}]}}]})

    assert ai.chat("gemini", "K", "gemini-2.0-flash", "sys", _msgs(), opener=opener) == "Gemini reply"


def test_missing_key_raises():
    with pytest.raises(RuntimeError, match="API key"):
        ai.chat("openai", "", "m", "", _msgs())


def test_http_error_is_surfaced():
    def opener(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"invalid key"}')
        )

    with pytest.raises(RuntimeError, match="401"):
        ai.chat("anthropic", "K", "m", "", _msgs(), opener=opener)


def test_providers_have_defaults():
    for prov in ai.PROVIDERS.values():
        assert prov.default_model in prov.models
        assert prov.key_url.startswith("https://")
