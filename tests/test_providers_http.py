"""Provider request/response shapes, checked against a mock HTTP transport (no network, no real API)."""
from __future__ import annotations

import json
from typing import Any, Callable

import anthropic
import httpx2
import openai
import pytest

from neuroplan import config
from neuroplan.coach.providers import AnthropicProvider, OpenAICompatProvider, ProviderError
from neuroplan.coach.types import ToolCall
from neuroplan.tools import registry

Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture(autouse=True)
def no_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 0)


def anthropic_provider(handler: Handler) -> AnthropicProvider:
    client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return AnthropicProvider("test-key", http_client=client)


def openai_provider(handler: Handler) -> OpenAICompatProvider:
    client = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return OpenAICompatProvider("test-key", "http://localhost:11434/v1", http_client=client)


def message_json(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> dict[str, Any]:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_anthropic_request_and_tool_use_round_trip() -> None:
    seen: list[dict[str, Any]] = []
    replies = [
        message_json([{"type": "thinking", "thinking": "", "signature": "sig-abc"},
                      {"type": "text", "text": "Let me check."},
                      {"type": "tool_use", "id": "toolu_1", "name": "get_state", "input": {}}], "tool_use"),
        message_json([{"type": "text", "text": "You have 4 blocks today."}]),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages"
        credentials = request.headers.get("x-api-key") or request.headers.get("authorization", "")
        assert credentials.endswith("test-key")
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=replies[len(seen) - 1])

    provider = anthropic_provider(handler)
    history: list[dict[str, Any]] = [{"role": "user", "content": "what now?"}]
    first = provider.complete("SYSTEM", "CONTEXT", history, "claude-sonnet-5-5")
    assert first.text == "Let me check." and first.stop_reason == "tool_use"
    assert first.tool_calls == [ToolCall("get_state", {}, "toolu_1")]
    body = seen[0]
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == config.LLM_MAX_TOKENS
    assert body["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}},
                              {"type": "text", "text": "CONTEXT"}]
    assert [t["name"] for t in body["tools"]] == [t["name"] for t in registry.anthropic_tools()]
    assert body["output_config"] == {"effort": config.LLM_EFFORT}
    for removed in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
        assert removed not in body
    assert body["messages"] == [{"role": "user", "content": "what now?"}]

    history.append({"role": "assistant", "content": first.text, "tool_calls": first.tool_calls,
                    "provider": "anthropic", "provider_content": first.provider_content})
    history.append({"role": "tool", "tool_call_id": "toolu_1", "name": "get_state", "content": "{\"ok\": true}",
                    "ok": True})
    second = provider.complete("SYSTEM", "CONTEXT", history, "claude-sonnet-5-5")
    assert second.text == "You have 4 blocks today." and second.tool_calls == []
    sent = seen[1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}, \
        "thinking blocks are passed back unchanged"
    assert sent[1]["content"][2]["type"] == "tool_use" and sent[1]["content"][2]["id"] == "toolu_1"
    assert sent[2]["content"] == [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "{\"ok\": true}",
                                   "is_error": False}]


def test_anthropic_without_tools_sends_no_tool_list() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=message_json([{"type": "text", "text": "briefing"}]))

    assert anthropic_provider(handler).complete("S", "C", [{"role": "user", "content": "x"}],
                                                "claude-haiku-5-5", tools=False).text == "briefing"
    assert "tools" not in seen[0] and seen[0]["model"] == "claude-haiku-5-5"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "api"),
                                         (404, "api"), (529, "api")])
def test_anthropic_http_errors_map_to_provider_errors(status: int, kind: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "nope"}})

    with pytest.raises(ProviderError) as info:
        anthropic_provider(handler).complete("S", "C", [{"role": "user", "content": "x"}], "m")
    assert info.value.kind == kind
    assert "\n" not in info.value.notice() and "test-key" not in info.value.notice()


def test_anthropic_connection_error_and_refusal() -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(ProviderError) as info:
        anthropic_provider(broken).complete("S", "C", [{"role": "user", "content": "x"}], "m")
    assert info.value.kind == "network"

    def refuse(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message_json([], "refusal"))

    with pytest.raises(ProviderError) as info:
        anthropic_provider(refuse).complete("S", "C", [{"role": "user", "content": "x"}], "m")
    assert info.value.kind == "refusal"


def test_anthropic_retries_without_effort_for_models_that_reject_it() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "output_config" in body:
            return httpx2.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "output_config.effort is not supported on this model"}})
        return httpx2.Response(200, json=message_json([{"type": "text", "text": "ok"}]))

    provider = anthropic_provider(handler)
    assert provider.complete("S", "C", [{"role": "user", "content": "x"}], "old-model").text == "ok"
    assert provider.complete("S", "C", [{"role": "user", "content": "y"}], "old-model").text == "ok"
    assert ["output_config" in b for b in seen] == [True, False, False]


def test_openai_compat_request_and_tool_calls() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "llama3.1",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "set_day_override",
                                             "arguments": "{\"date\": \"today\", \"minutes\": 90}"}},
                               {"id": "call_2", "type": "function",
                                "function": {"name": "get_plan", "arguments": "not json"}}]}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    response = openai_provider(handler).complete("SYSTEM", "CONTEXT", [{"role": "user", "content": "90 min"}],
                                                 "llama3.1")
    assert response.text == "" and response.stop_reason == "tool_calls"
    assert response.tool_calls == [ToolCall("set_day_override", {"date": "today", "minutes": 90}, "call_1"),
                                   ToolCall("get_plan", {}, "call_2")]
    body = seen[0]
    assert body["model"] == "llama3.1"
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM\n\nCONTEXT"}
    assert body["tools"][0]["type"] == "function" and len(body["tools"]) == len(registry.openai_tools())
    assert "max_tokens" not in body and "temperature" not in body


@pytest.mark.parametrize("status,kind", [(401, "auth"), (429, "rate_limit"), (500, "api")])
def test_openai_compat_errors(status: int, kind: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"error": {"message": "nope", "type": "x"}})

    with pytest.raises(ProviderError) as info:
        openai_provider(handler).complete("S", "C", [{"role": "user", "content": "x"}], "m")
    assert info.value.kind == kind

    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(ProviderError) as info:
        openai_provider(broken).complete("S", "C", [{"role": "user", "content": "x"}], "m")
    assert info.value.kind == "network"
