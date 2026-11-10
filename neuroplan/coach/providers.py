"""LLM providers behind one small interface: Anthropic, OpenAI-compatible endpoints, and a scripted fake.

Messages use a neutral format so the engine does not care which provider is active:
    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall], "provider_content": raw blocks}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str, "ok": bool}
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from .. import config
from ..tools import registry
from .types import ToolCall

log = logging.getLogger(__name__)
ENV_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai_compat": "OPENAI_API_KEY"}


class ProviderError(Exception):
    """A failed LLM call. ``kind`` is one of auth, rate_limit, network, refusal, api."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message

    def notice(self) -> str:
        """One line for the chat."""
        return {
            "auth": "The API key was rejected — I'm answering in offline mode. Check it in Settings.",
            "rate_limit": "The AI service is rate-limited right now — answering in offline mode.",
            "network": "I can't reach the AI service — answering in offline mode.",
            "refusal": "The AI model declined that request — answering in offline mode.",
        }.get(self.kind, f"The AI service returned an error ({self.message}) — answering in offline mode.")


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    provider_content: Any = None


class Provider(Protocol):
    name: str

    def complete(self, system: str, context: str, messages: list[dict[str, Any]], model: str,
                 tools: bool = True) -> LLMResponse: ...


# ---------------------------------------------------------------- API keys (keyring, never SQLite)
def _keyring() -> Any:
    if os.environ.get("NEUROPLAN_NO_KEYRING"):
        return None
    try:
        import keyring
        return keyring
    except Exception:  # keyring backend missing or broken: fall back to env vars
        return None


def get_api_key(provider: str) -> Optional[str]:
    """Key from the OS keyring (service NeuroPlan), else from the provider's environment variable."""
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(config.KEYRING_SERVICE, provider)
            if value:
                return str(value)
        except Exception:
            log.warning("keyring read failed; falling back to the environment")
    return os.environ.get(ENV_KEYS.get(provider, ""), "") or None


def set_api_key(provider: str, key: str) -> bool:
    """Store (or, with an empty key, delete) the key in the OS keyring. Returns False if unavailable."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        if key:
            kr.set_password(config.KEYRING_SERVICE, provider, key)
        else:
            try:
                kr.delete_password(config.KEYRING_SERVICE, provider)
            except Exception:
                pass
        return True
    except Exception:
        log.warning("keyring write failed")
        return False


# ---------------------------------------------------------------- Anthropic
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=float(config.LLM_TIMEOUT_S),
                                           max_retries=config.LLM_MAX_RETRIES)
        self._no_effort: set[str] = set()      # models that reject output_config.effort

    @staticmethod
    def convert(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "user":
                out.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                if m.get("provider") == "anthropic" and m.get("provider_content") is not None:
                    # pass the original blocks back unchanged (thinking blocks must not be edited)
                    out.append({"role": "assistant", "content": m["provider_content"]})
                    continue
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for call in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.args})
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"],
                         "is_error": not m.get("ok", True)}
                # every result of one assistant turn goes into a single user message
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    def complete(self, system: str, context: str, messages: list[dict[str, Any]], model: str,
                 tools: bool = True) -> LLMResponse:
        anthropic = self._anthropic
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": config.LLM_MAX_TOKENS,
            # stable persona first (cacheable prefix together with the tool list), volatile state after
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
                       {"type": "text", "text": context}],
            "messages": self.convert(messages),
        }
        if tools:
            kwargs["tools"] = registry.anthropic_tools()
        if model not in self._no_effort:
            kwargs["output_config"] = {"effort": config.LLM_EFFORT}
        try:
            try:
                response = self._client.messages.create(**kwargs)
            except anthropic.BadRequestError as exc:
                if "output_config" not in kwargs or "effort" not in str(exc).lower():
                    raise
                self._no_effort.add(model)          # older model: retry once without the effort setting
                kwargs.pop("output_config")
                response = self._client.messages.create(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ProviderError("auth", "authentication failed") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("rate_limit", "rate limited") from exc
        except anthropic.APIConnectionError as exc:      # includes APITimeoutError
            raise ProviderError("network", "connection failed or timed out") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError("api", f"HTTP {exc.status_code}") from exc
        except anthropic.APIError as exc:
            raise ProviderError("api", type(exc).__name__) from exc
        if response.stop_reason == "refusal":
            raise ProviderError("refusal", "the model declined the request")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        calls = [ToolCall(block.name, dict(block.input or {}), block.id)
                 for block in response.content if block.type == "tool_use"]
        return LLMResponse(text, calls, response.stop_reason or "end_turn", response.content)


# ---------------------------------------------------------------- OpenAI-compatible
class OpenAICompatProvider:
    """Chat Completions with tools: OpenAI, DeepSeek, Ollama (http://localhost:11434/v1), …"""
    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str = "") -> None:
        import openai
        self._openai = openai
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url or None,
                                     timeout=float(config.LLM_TIMEOUT_S), max_retries=config.LLM_MAX_RETRIES)

    @staticmethod
    def convert(system: str, context: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": f"{system}\n\n{context}"}]
        for m in messages:
            role = m["role"]
            if role == "user":
                out.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                calls = m.get("tool_calls") or []
                if calls:
                    entry["tool_calls"] = [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
                        for c in calls]
                if entry["content"] or calls:
                    out.append(entry)
            elif role == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        return out

    def complete(self, system: str, context: str, messages: list[dict[str, Any]], model: str,
                 tools: bool = True) -> LLMResponse:
        openai = self._openai
        kwargs: dict[str, Any] = {"model": model, "messages": self.convert(system, context, messages)}
        if tools:
            kwargs["tools"] = registry.openai_tools()
        try:
            response = self._client.chat.completions.create(**kwargs)
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise ProviderError("auth", "authentication failed") from exc
        except openai.RateLimitError as exc:
            raise ProviderError("rate_limit", "rate limited") from exc
        except openai.APIConnectionError as exc:         # includes APITimeoutError
            raise ProviderError("network", "connection failed or timed out") from exc
        except openai.APIStatusError as exc:
            raise ProviderError("api", f"HTTP {exc.status_code}") from exc
        except openai.APIError as exc:
            raise ProviderError("api", type(exc).__name__) from exc
        if not response.choices:
            raise ProviderError("api", "empty response")
        choice = response.choices[0]
        calls: list[ToolCall] = []
        for i, call in enumerate(choice.message.tool_calls or []):
            function = getattr(call, "function", None)
            if function is None:
                continue
            try:
                args = json.loads(function.arguments or "{}")
            except ValueError:
                args = {}
            calls.append(ToolCall(function.name, args if isinstance(args, dict) else {}, call.id or f"call_{i}"))
        return LLMResponse((choice.message.content or "").strip(), calls, choice.finish_reason or "stop")


# ---------------------------------------------------------------- Fake (tests)
class FakeProvider:
    """Scripted responses for tests. Items are LLMResponse objects or exceptions to raise."""
    name = "fake"

    def __init__(self, script: Optional[list[Any]] = None, default_text: str = "OK.") -> None:
        self.script: list[Any] = list(script or [])
        self.default_text = default_text
        self.calls: list[dict[str, Any]] = []

    def complete(self, system: str, context: str, messages: list[dict[str, Any]], model: str,
                 tools: bool = True) -> LLMResponse:
        self.calls.append({"system": system, "context": context, "messages": [dict(m) for m in messages],
                           "model": model, "tools": tools})
        if not self.script:
            return LLMResponse(self.default_text)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            return LLMResponse(item)
        assert isinstance(item, LLMResponse)
        return item


def build_provider(settings: dict[str, str]) -> tuple[Optional[Provider], str]:
    """(provider, reason). provider is None when the coach must run offline; reason says why."""
    kind = settings.get("provider", "offline")
    if kind == "anthropic":
        key = get_api_key("anthropic")
        if not key:
            return None, "no_key"
        return AnthropicProvider(key), ""
    if kind == "openai_compat":
        base_url = (settings.get("base_url") or "").strip()
        key = get_api_key("openai_compat")
        if not key:
            if not base_url:
                return None, "no_key"
            key = "not-needed"           # local endpoints such as Ollama ignore the key
        return OpenAICompatProvider(key, base_url), ""
    return None, "offline"
