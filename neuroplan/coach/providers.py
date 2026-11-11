"""LLM providers behind one small interface: Anthropic, OpenAI-compatible endpoints, and a scripted fake.

Messages use a neutral format so the engine does not care which provider is active:
    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall], "provider_content": raw blocks}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str, "ok": bool}
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from .. import config
from ..tools import registry
from .types import ToolCall

log = logging.getLogger(__name__)
ENV_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai_compat": "OPENAI_API_KEY"}


class ProviderError(Exception):
    """A failed LLM call.

    ``kind``: auth (the key is wrong), permission (the key is fine but the account/project may not use the
    service), bad_url, bad_model, rate_limit (rate limit or quota), bad_request, network, refusal, api.
    ``status`` is the HTTP status if there was one, ``detail`` the service's own explanation (never the key),
    ``target`` the model or address the error is about."""

    PERSISTENT = ("auth", "permission")     # will fail the same way until the settings change

    def __init__(self, kind: str, message: str, status: Optional[int] = None, detail: str = "",
                 target: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.detail = detail
        self.target = target

    def reason(self) -> str:
        """What went wrong, on one line: headline, HTTP status, and the service's own words."""
        head = {
            "auth": "The API key was rejected",
            "permission": "The AI service accepted the key but denied access",
            "bad_url": f"Nothing answers at the Base URL {self.target}".rstrip(),
            "bad_model": f"The AI service does not know the model '{self.target}'" if self.target
                         else "The AI service does not know this model",
            "rate_limit": "The rate limit or quota of the AI service is used up",
            "bad_request": "The AI service rejected the request",
            "network": f"I can't reach the AI service at {self.target}" if self.target
                       else "I can't reach the AI service",
            "refusal": "The AI model declined that request",
        }.get(self.kind, "The AI service returned an error")
        detail = self.detail or ("" if self.status or self.kind != "api" else self.message)
        if self.status:
            head += f" (HTTP {self.status})"
        return f"{head}: {detail}" if detail else head

    def hint(self) -> str:
        """What to do about it (Settings → Test connection)."""
        return {
            "auth": "Check the key in Settings.",
            "permission": "The key itself works; the account or project behind it is not allowed to use this "
                          "model (billing, plan or region).",
            "bad_url": "Check the Base URL: it ends like …/v1, without /chat/completions.",
            "bad_model": "Check the model names in Settings.",
            "rate_limit": "Wait a little, or check the plan and billing of the account.",
            "bad_request": "The service does not accept this kind of request; try another model.",
            "network": "Check the Base URL and the internet connection.",
        }.get(self.kind, "")

    def explain(self) -> str:
        """Reason plus hint, for the Settings view."""
        reason = self.reason()
        if reason[-1:] not in ".!?":
            reason += "."
        return f"{reason} {self.hint()}".strip()

    def notice(self) -> str:
        """One line for the chat."""
        return f"{self.reason().rstrip('.')} — answering in offline mode."


_BAD_KEY_RE = re.compile(r"api[ _-]?key[ _]?(is )?(not valid|invalid|expired|not found)|"
                         r"(invalid|incorrect|expired|unknown|missing) (x-)?api[ _-]?key", re.IGNORECASE)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def error_detail(body: str, secret: str = "") -> str:
    """The human-readable message inside an error body ({"error": {"message": …}}, Gemini's list form,
    Ollama's {"error": "…"}, or plain text), on one line, shortened, with the key removed."""
    text = (body or "").strip()
    try:
        data: Any = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        error = data.get("error", data)
        if isinstance(error, dict):
            text = str(error.get("message") or error.get("detail") or error.get("status") or error.get("type")
                       or "")
        else:
            text = str(error or "")
    elif data is not None or text[:1] == "<":           # a bare JSON value or an HTML error page says nothing
        text = ""
    text = _redact(" ".join(text.split()), secret)
    limit = config.ERROR_DETAIL_MAX_CHARS
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def status_error(status: int, body: str, secret: str = "", model: str = "", base_url: str = "") -> ProviderError:
    """Map an HTTP error answer to a ProviderError that says what is really wrong."""
    detail = error_detail(body, secret)
    target = ""
    if status == 401 or (status in (400, 403) and _BAD_KEY_RE.search(detail)):
        kind = "auth"
    elif status == 403:
        kind = "permission"
    elif status == 404 and "model" in detail.lower():
        kind, target = "bad_model", model
    elif status == 404 and base_url:
        kind, target = "bad_url", base_url
    elif status == 429:
        kind = "rate_limit"
    elif status in (400, 422):
        kind = "bad_request"
    else:
        kind = "api"
    return ProviderError(kind, f"HTTP {status}", status, detail, target)


def connection_error(exc: BaseException, secret: str = "", host: str = "", timed_out: bool = False) -> ProviderError:
    """A call that never got an HTTP answer: say whether it timed out or could not connect, and why."""
    cause = exc.__cause__ or exc
    if timed_out:
        detail = f"no answer within {config.LLM_TIMEOUT_S} s"
    else:
        detail = _redact(" ".join(f"{type(cause).__name__}: {cause}".split()), secret)[:160].rstrip(": ")
    return ProviderError("network", "connection failed or timed out", None, detail, host)


def _body_of(exc: Any) -> str:
    """The raw error body of an SDK status error."""
    try:
        return str(exc.response.text)
    except Exception:
        body = getattr(exc, "body", None)
        return json.dumps(body) if isinstance(body, (dict, list)) else str(body or "")


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
            if value and config.clean_api_key(value):
                return config.clean_api_key(value)
        except Exception:
            log.warning("keyring read failed; falling back to the environment")
    return config.clean_api_key(os.environ.get(ENV_KEYS.get(provider, ""), "")) or None


def set_api_key(provider: str, key: str) -> bool:
    """Store (or, with an empty key, delete) the key in the OS keyring. Returns False if unavailable."""
    key = config.clean_api_key(key)
    kr = _keyring()
    if kr is None:
        return False
    try:
        if key:
            kr.set_password(config.KEYRING_SERVICE, provider, key)
        else:
            with contextlib.suppress(Exception):       # nothing stored: nothing to delete
                kr.delete_password(config.KEYRING_SERVICE, provider)
        return True
    except Exception:
        log.warning("keyring write failed")
        return False


# ---------------------------------------------------------------- Anthropic
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, http_client: Any = None) -> None:
        """``http_client`` lets tests plug in a mock transport; production uses the SDK default."""
        import anthropic
        self._anthropic = anthropic
        self._key = api_key
        self._client = anthropic.Anthropic(api_key=api_key, timeout=float(config.LLM_TIMEOUT_S),
                                           max_retries=config.LLM_MAX_RETRIES, http_client=http_client)
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
        except anthropic.APIConnectionError as exc:      # includes APITimeoutError
            raise connection_error(exc, self._key, "api.anthropic.com",
                                   isinstance(exc, anthropic.APITimeoutError)) from exc
        except anthropic.APIStatusError as exc:
            raise status_error(exc.status_code, _body_of(exc), self._key, model) from exc
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

    def __init__(self, api_key: str, base_url: str = "", http_client: Any = None) -> None:
        import openai
        self._openai = openai
        self._key = api_key
        base_url = config.clean_base_url(base_url)
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url or None,
                                     timeout=float(config.LLM_TIMEOUT_S), max_retries=config.LLM_MAX_RETRIES,
                                     http_client=http_client)

    @property
    def base_url(self) -> Any:
        """The address the client really uses (after clean-up and the SDK's own defaults)."""
        return self._client.base_url

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
        except openai.APIConnectionError as exc:         # includes APITimeoutError
            raise connection_error(exc, self._key, self.base_url.host,
                                   isinstance(exc, openai.APITimeoutError)) from exc
        except openai.APIStatusError as exc:
            raise status_error(exc.status_code, _body_of(exc), self._key, model, str(self.base_url)) from exc
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
        base_url = config.clean_base_url(settings.get("base_url") or "")
        key = get_api_key("openai_compat")
        if not key:
            if not base_url:
                return None, "no_key"
            key = "not-needed"           # local endpoints such as Ollama ignore the key
        return OpenAICompatProvider(key, base_url), ""
    return None, "offline"
