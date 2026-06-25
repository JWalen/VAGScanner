"""Multi-provider AI chat client (Anthropic / OpenAI / Google Gemini).

Thin REST clients over stdlib ``urllib`` — no provider SDKs — so it stays light
and bundles cleanly into the PyInstaller app. Qt-free and dependency-free, with
an injectable opener so it can be unit-tested without network access.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

_SSL_CTX: Optional[ssl.SSLContext] = None


def _ssl_context() -> Optional[ssl.SSLContext]:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        try:
            return ssl.create_default_context()
        except Exception:  # noqa: BLE001
            return None


def _default_opener(req, timeout):
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = _ssl_context()
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)


@dataclass
class Provider:
    id: str
    label: str
    models: List[str]
    default_model: str
    key_url: str


PROVIDERS: Dict[str, Provider] = {
    "anthropic": Provider(
        "anthropic", "Anthropic (Claude)",
        ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        "claude-sonnet-4-6", "https://console.anthropic.com/settings/keys",
    ),
    "openai": Provider(
        "openai", "OpenAI (GPT)",
        ["gpt-4o", "gpt-4o-mini", "o4-mini"],
        "gpt-4o", "https://platform.openai.com/api-keys",
    ),
    "gemini": Provider(
        "gemini", "Google (Gemini)",
        ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
        "gemini-2.0-flash", "https://aistudio.google.com/apikey",
    ),
}

Opener = Callable[..., object]
Message = Dict[str, str]  # {"role": "user"|"assistant", "content": str}


def _post_json(url: str, headers: dict, payload: dict, opener: Opener, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with opener(req, timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"{exc.code} {exc.reason}: {body[:400]}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from None


def chat(
    provider_id: str,
    api_key: str,
    model: str,
    system: str,
    messages: List[Message],
    max_tokens: int = 1024,
    timeout: float = 90,
    opener: Optional[Opener] = None,
) -> str:
    """Send a chat conversation to a provider and return the assistant's reply.

    Args:
        provider_id: One of ``PROVIDERS``.
        api_key: The provider API key.
        model: Model id (see ``Provider.models`` for suggestions).
        system: System prompt (vehicle context).
        messages: Prior turns as ``{"role", "content"}`` dicts.
    """
    if not api_key:
        raise RuntimeError("No API key set for this provider.")
    op = opener or _default_opener
    if provider_id == "anthropic":
        return _anthropic(api_key, model, system, messages, max_tokens, timeout, op)
    if provider_id == "openai":
        return _openai(api_key, model, system, messages, max_tokens, timeout, op)
    if provider_id == "gemini":
        return _gemini(api_key, model, system, messages, max_tokens, timeout, op)
    raise ValueError(f"Unknown provider: {provider_id}")


def _anthropic(api_key, model, system, messages, max_tokens, timeout, opener) -> str:
    payload = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        payload["system"] = system
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        payload, opener, timeout,
    )
    return "".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    ).strip() or "(no response)"


def _openai(api_key, model, system, messages, max_tokens, timeout, opener) -> str:
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    data = _post_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {api_key}"},
        {"model": model, "messages": msgs, "max_tokens": max_tokens},
        opener, timeout,
    )
    return data["choices"][0]["message"]["content"].strip()


def _gemini(api_key, model, system, messages, max_tokens, timeout, opener) -> str:
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    payload = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    data = _post_json(url, {}, payload, opener, timeout)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


SYSTEM_PREAMBLE = (
    "You are an expert VAG/Audi (Volkswagen Auto Group) diagnostic assistant "
    "embedded in the VCDS Toolkit app. Help the user diagnose their vehicle from "
    "the data below. Be specific and practical: name the most likely causes, the "
    "checks to confirm them, and typical fixes, ordered by likelihood. If the data "
    "is insufficient, say what to log next. Keep safety in mind."
)


def vehicle_system_prompt(context: str) -> str:
    """Wrap a diagnostic-context string in the assistant system prompt."""
    if not context:
        return SYSTEM_PREAMBLE + "\n\n(No vehicle data has been loaded yet.)"
    return SYSTEM_PREAMBLE + "\n\n--- CURRENT VEHICLE DATA ---\n" + context
