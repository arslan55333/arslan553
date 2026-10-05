import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from leadengine.llm import ClaudeLLM, GeminiLLM, LLMError, OllamaLLM, build_llm

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def run(coro):
    return asyncio.run(coro)


def test_claude_request_shape_and_refusal(monkeypatch):
    llm = ClaudeLLM("claude-opus-5-5", {"effort": "low"}, api_key="sk-test")
    captured = {}

    async def fake_create(**params):
        captured.update(params)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text='{"ok": true}')])

    monkeypatch.setattr(llm.client.beta.messages, "create", fake_create)
    assert run(llm.complete_json("rate this", system="be brief", images=[PNG])) == {"ok": True}
    assert captured["model"] == "claude-opus-5-5" and captured["output_config"] == {"effort": "low"}
    assert captured["betas"] == ["server-side-fallback-2026-07-01"] and captured["extra_body"] == {"fallbacks": "default"}
    blocks = captured["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[-1] == {"type": "text", "text": "rate this"} and "JSON" in captured["system"]
    assert "thinking" not in captured                                  # Opus 5.5: thinking is on by default

    async def refused(**params):
        return SimpleNamespace(stop_reason="refusal", content=[])

    monkeypatch.setattr(llm.client.beta.messages, "create", refused)
    with pytest.raises(LLMError, match="declined"):
        run(llm.complete("x"))


def test_gemini_and_ollama_requests():
    def gemini(request: httpx.Request):
        body = json.loads(request.content)
        assert request.headers["x-goog-api-key"] == "g-key" and "gemini-2.5-flash:generateContent" in str(request.url)
        assert body["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "image/png"
        assert body["systemInstruction"]["parts"][0]["text"] == "sys"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hello"}]}}]})

    g = GeminiLLM("gemini-2.5-flash", {}, "g-key", transport=httpx.MockTransport(gemini))
    assert run(g.complete("hi", system="sys", images=[PNG])) == "hello"

    def ollama(request: httpx.Request):
        body = json.loads(request.content)
        assert request.url.path == "/api/generate" and body["images"] and body["stream"] is False
        return httpx.Response(200, json={"response": "local answer"})

    o = OllamaLLM("llama3.2-vision", {}, "http://ollama.local:11434", transport=httpx.MockTransport(ollama))
    assert run(o.complete("hi", images=[PNG])) == "local answer"
    with pytest.raises(LLMError):
        GeminiLLM("m", {}, "")


def test_build_llm_from_config(settings):
    from dataclasses import replace

    s = replace(settings, sections={**settings.sections, "llm": {"provider": "claude"},
                                    "preview": {"provider": "ollama", "model": "qwen2.5"}},
                llm_keys={"anthropic": "sk-x", "ollama_url": "http://box:11434"})
    assert isinstance(build_llm(s), ClaudeLLM) and build_llm(s).model == "claude-opus-5-5"
    local = build_llm(s, "preview")
    assert isinstance(local, OllamaLLM) and local.model == "qwen2.5" and local.base_url == "http://box:11434"
    with pytest.raises(LLMError):
        build_llm(replace(s, sections={"llm": {"provider": "nope"}}))
