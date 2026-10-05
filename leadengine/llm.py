"""Pluggable LLM providers: Claude (default), Gemini, Groq, local Ollama.

Every provider exposes the same small interface::

    text = await llm.complete(prompt, system=..., images=[png_bytes], max_tokens=...)
    data = await llm.complete_json(prompt, ...)

Configure in config.toml ``[llm]`` (provider, model) and keys in .env
(ANTHROPIC_API_KEY / GEMINI_API_KEY / GROQ_API_KEY / OLLAMA_URL).
"""

from __future__ import annotations

import base64
import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from leadengine.config import Settings
from leadengine.errors import LeadEngineError
from leadengine.log import get_logger

log = get_logger("llm")

DEFAULT_MODELS = {
    "claude": "claude-opus-5-5",
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "ollama": "llama3.2-vision",
}


class LLMError(LeadEngineError):
    pass


def _media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def extract_json(text: str) -> Any:
    """First JSON object/array in a model reply (tolerates ```json fences and prose)."""
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                continue
    raise LLMError("model did not return valid JSON")


class LLM(ABC):
    name: str = "base"

    def __init__(self, model: str, cfg: dict[str, Any]) -> None:
        self.model = model
        self.cfg = cfg

    @abstractmethod
    async def complete(self, prompt: str, *, system: str | None = None, images: list[bytes] | None = None,
                       max_tokens: int = 2000) -> str: ...

    async def complete_json(self, prompt: str, **kw) -> Any:
        system = (kw.pop("system", None) or "") + "\nReply with valid JSON only, no other text."
        return extract_json(await self.complete(prompt, system=system.strip(), **kw))


class ClaudeLLM(LLM):
    """Anthropic Messages API via the official SDK. Refusals fall back server-side
    (``fallbacks: "default"``) so a declined request is re-run on Anthropic's recommended model."""

    name = "claude"

    def __init__(self, model: str, cfg: dict[str, Any], api_key: str = "") -> None:
        super().__init__(model, cfg)
        import anthropic

        self._anthropic = anthropic
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY / `ant auth login` profiles itself.
        self.client = anthropic.AsyncAnthropic(api_key=api_key or None, max_retries=3)

    async def complete(self, prompt, *, system=None, images=None, max_tokens=2000) -> str:
        content: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": _media_type(img),
                                         "data": base64.standard_b64encode(img).decode()}}
            for img in images or []
        ]
        content.append({"type": "text", "text": prompt})
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": self.cfg.get("effort", "medium")},
        }
        if system:
            params["system"] = system
        if self.cfg.get("fallbacks", True):
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["extra_body"] = {"fallbacks": "default"}
        try:
            response = await self.client.beta.messages.create(**params)
        except self._anthropic.AuthenticationError as exc:
            raise LLMError("Claude: API key rejected (set ANTHROPIC_API_KEY)") from exc
        except self._anthropic.RateLimitError as exc:
            raise LLMError("Claude: rate limited, try again later") from exc
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError("Claude: network error") from exc
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined this request")
        return "".join(b.text for b in response.content if b.type == "text")


class GeminiLLM(LLM):
    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, model, cfg, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(model, cfg)
        if not api_key:
            raise LLMError("GEMINI_API_KEY missing in .env")
        self.api_key = api_key
        self.transport = transport

    async def complete(self, prompt, *, system=None, images=None, max_tokens=2000) -> str:
        parts: list[dict[str, Any]] = [
            {"inline_data": {"mime_type": _media_type(img), "data": base64.b64encode(img).decode()}}
            for img in images or []
        ]
        parts.append({"text": prompt})
        body: dict[str, Any] = {"contents": [{"parts": parts}],
                                "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            r = await client.post(self.URL.format(model=self.model), json=body,
                                  headers={"x-goog-api-key": self.api_key})
        if r.status_code >= 400:
            raise LLMError(f"Gemini error {r.status_code}: {r.text[:200]}")
        try:
            return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("Gemini returned no text") from exc


class GroqLLM(LLM):
    """Groq's own chat-completions endpoint (text only)."""

    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model, cfg, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(model, cfg)
        if not api_key:
            raise LLMError("GROQ_API_KEY missing in .env")
        self.api_key = api_key
        self.transport = transport

    async def complete(self, prompt, *, system=None, images=None, max_tokens=2000) -> str:
        if images:
            raise LLMError("Groq provider here is text-only; use claude, gemini or ollama for screenshots")
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            r = await client.post(self.URL, json={"model": self.model, "messages": messages, "max_tokens": max_tokens},
                                  headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code >= 400:
            raise LLMError(f"Groq error {r.status_code}: {r.text[:200]}")
        return r.json()["choices"][0]["message"]["content"]


class OllamaLLM(LLM):
    """Local model via Ollama (free, private). Vision needs a vision model, e.g. llama3.2-vision."""

    name = "ollama"

    def __init__(self, model, cfg, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(model, cfg)
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.transport = transport

    async def complete(self, prompt, *, system=None, images=None, max_tokens=2000) -> str:
        body: dict[str, Any] = {"model": self.model, "prompt": prompt, "stream": False,
                                "options": {"num_predict": max_tokens}}
        if system:
            body["system"] = system
        if images:
            body["images"] = [base64.b64encode(i).decode() for i in images]
        try:
            async with httpx.AsyncClient(timeout=300, transport=self.transport) as client:
                r = await client.post(f"{self.base_url}/api/generate", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama not reachable at {self.base_url}") from exc
        if r.status_code >= 400:
            raise LLMError(f"Ollama error {r.status_code}: {r.text[:200]}")
        return r.json().get("response", "")


def build_llm(settings: Settings, section: str = "llm") -> LLM:
    """Provider from ``[llm]`` (or a feature section that overrides provider/model)."""
    base = settings.section("llm")
    cfg = {**base, **settings.section(section)} if section != "llm" else base
    provider = str(cfg.get("provider", "claude")).lower()
    model = cfg.get("model") or DEFAULT_MODELS.get(provider, "")
    env = os.environ
    keys = settings.llm_keys
    if provider == "claude":
        return ClaudeLLM(model, cfg, keys.get("anthropic", ""))
    if provider == "gemini":
        return GeminiLLM(model, cfg, keys.get("gemini", ""))
    if provider == "groq":
        return GroqLLM(model, cfg, keys.get("groq", ""))
    if provider == "ollama":
        return OllamaLLM(model, cfg, keys.get("ollama_url", "") or env.get("OLLAMA_URL", ""))
    raise LLMError(f"Unknown LLM provider {provider!r} (claude | gemini | groq | ollama)")
