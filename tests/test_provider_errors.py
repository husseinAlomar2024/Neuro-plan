"""What a failed provider call tells the student: the real reason (bad key, no access, wrong URL, unknown
model, quota, rejected request, no connection), checked against a mock HTTP transport. No network."""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

import anthropic
import httpx2
import openai
import pytest

from neuroplan import config
from neuroplan.coach import coach as coach_module
from neuroplan.coach import providers
from neuroplan.coach.coach import Coach
from neuroplan.coach.providers import (AnthropicProvider, FakeProvider, OpenAICompatProvider, ProviderError,
                                       build_provider, error_detail, get_api_key, set_api_key)
from neuroplan.db.repository import Repository
from neuroplan.events import EventBus
from neuroplan.tools import registry
from neuroplan.tools.executor import ToolExecutor

from .conftest import TODAY

Handler = Callable[[httpx2.Request], httpx2.Response]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
SECRET = "sk-test-SECRET-123"
USER = [{"role": "user", "content": "ping"}]
FREE_TIER = ("The Gemini API free tier is not available for this project. To use the Gemini API, please set up "
             "billing to upgrade to the paid tier at https://aistudio.google.com/billing.")


@pytest.fixture(autouse=True)
def no_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 0)


def gemini_error(code: int, message: str, status: str) -> str:
    """Gemini's OpenAI-compatible endpoint wraps its error object in a list."""
    return json.dumps([{"error": {"code": code, "message": message, "status": status}}], indent=2)


def openai_error(message: str, kind: str = "invalid_request_error") -> str:
    return json.dumps({"error": {"message": message, "type": kind, "param": None, "code": None}})


def provider_for(handler: Handler, base_url: str = GEMINI_URL) -> OpenAICompatProvider:
    client = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return OpenAICompatProvider(SECRET, base_url, http_client=client)


def answering(status: int, body: str) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, text=body)
    return handler


def completion(text: str = "OK") -> dict[str, Any]:
    return {"id": "c1", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def failure(handler: Handler, model: str = "gemini-flash-latest", base_url: str = GEMINI_URL) -> ProviderError:
    with pytest.raises(ProviderError) as info:
        provider_for(handler, base_url).complete("S", "C", USER, model, tools=False)
    return info.value


# (label, HTTP status, body, expected kind, text that must appear in the reason)
CASES: list[tuple[str, int, str, str, str]] = [
    ("bad key, OpenAI", 401, openai_error("Incorrect API key provided."), "auth", "API key was rejected (HTTP 401)"),
    ("bad key, Gemini", 400, gemini_error(400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT"),
     "auth", "API key was rejected (HTTP 400): API key not valid"),
    ("no access, Gemini", 403, gemini_error(403, FREE_TIER, "PERMISSION_DENIED"), "permission",
     "accepted the key but denied access (HTTP 403): The Gemini API free tier is not available for this project"),
    ("wrong URL, empty body", 404, "", "bad_url", "Nothing answers at the Base URL https://generativelanguage"),
    ("wrong URL, HTML page", 404, "<html><body><h1>Not Found</h1></body></html>", "bad_url", "(HTTP 404)"),
    ("wrong URL, Go server", 404, "404 page not found", "bad_url", "(HTTP 404): 404 page not found"),
    ("unknown model, Gemini", 404, gemini_error(404, "models/gemini-nope is not found for API version v1main, or "
                                                     "is not supported for generateContent.", "NOT_FOUND"),
     "bad_model", "does not know the model 'gemini-nope' (HTTP 404): models/gemini-nope is not found"),
    ("unknown model, Ollama", 404, json.dumps({"error": "model 'gemini-nope' not found"}), "bad_model",
     "'gemini-nope' (HTTP 404): model 'gemini-nope' not found"),
    ("quota", 429, gemini_error(429, "You exceeded your current quota, please check your plan and billing details.",
                                "RESOURCE_EXHAUSTED"),
     "rate_limit", "rate limit or quota of the AI service is used up (HTTP 429): You exceeded your current quota"),
    ("schema", 400, gemini_error(400, "Invalid JSON payload received. Unknown name \"additionalProperties\" at "
                                      "'tools[0].function_declarations[0].parameters': Cannot find field.",
                                 "INVALID_ARGUMENT"),
     "bad_request", "rejected the request (HTTP 400): Invalid JSON payload received. Unknown name"),
    ("server error", 503, openai_error("The model is overloaded."), "api",
     "returned an error (HTTP 503): The model is overloaded."),
]


@pytest.mark.parametrize("label,status,body,kind,expected", CASES, ids=[c[0] for c in CASES])
def test_http_errors_say_what_is_really_wrong(label: str, status: int, body: str, kind: str, expected: str) -> None:
    error = failure(answering(status, body), model="gemini-nope" if "model" in label else "gemini-flash-latest")
    assert error.kind == kind and error.status == status
    assert expected in error.reason()
    for line in (error.reason(), error.notice(), error.explain()):
        assert "\n" not in line and "<html" not in line and SECRET not in line
    assert error.notice().endswith("— answering in offline mode.")
    if kind != "api":
        assert error.hint() and error.explain().endswith(error.hint())


def test_only_a_rejected_key_is_called_a_rejected_key() -> None:
    """The reported bug: a 403 for a project without access was shown as 'The API key was rejected'."""
    for label, status, body, kind, _ in CASES:
        error = failure(answering(status, body))
        assert ("API key was rejected" in error.reason()) == (kind == "auth"), label
    denied = failure(answering(403, gemini_error(403, FREE_TIER, "PERMISSION_DENIED")))
    assert "The key itself works" in denied.explain()
    assert failure(answering(403, openai_error("Invalid API key."))).kind == "auth"


def test_the_key_never_appears_in_an_error() -> None:
    error = failure(answering(401, openai_error(f"Incorrect API key provided: {SECRET}.")))
    assert error.kind == "auth" and SECRET not in error.reason() and "***" in error.reason()

    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"could not send header Bearer {SECRET}", request=request)
    assert SECRET not in failure(broken).reason()


def test_long_error_messages_are_shortened() -> None:
    error = failure(answering(400, openai_error("x" * 5000)))
    assert len(error.detail) == config.ERROR_DETAIL_MAX_CHARS and error.detail.endswith("…")
    assert error_detail("") == "" and error_detail("12") == "" and error_detail('{"error": {}}') == ""


def test_connection_failure_and_timeout_are_told_apart() -> None:
    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    error = failure(refused, base_url="http://localhost:11434/v1")
    assert error.kind == "network" and error.status is None
    assert "I can't reach the AI service at localhost" in error.reason() and "connection refused" in error.reason()
    error = failure(slow)
    assert error.kind == "network"
    assert f"generativelanguage.googleapis.com: no answer within {config.LLM_TIMEOUT_S} s" in error.reason()


def test_anthropic_errors_carry_the_reason_too() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"type": "error", "error": {"type": "not_found_error",
                                                                     "message": "model: claude-nope"}})
    client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    with pytest.raises(ProviderError) as info:
        AnthropicProvider("test-key", http_client=client).complete("S", "C", USER, "claude-nope")
    assert info.value.kind == "bad_model"
    assert info.value.reason() == "The AI service does not know the model 'claude-nope' (HTTP 404): model: claude-nope"


# ---------------------------------------------------------------- base URL and key clean-up
@pytest.mark.parametrize("typed,stored", [
    ("https://generativelanguage.googleapis.com/v1beta/openai/", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("https://generativelanguage.googleapis.com/v1beta/openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("  \"https://generativelanguage.googleapis.com/v1beta/openai/\"\n", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("'https://generativelanguage.googleapis.com/v1beta/openai/models'", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("https://api.deepseek.com/v1/chat/completions/", "https://api.deepseek.com/v1"),
    ("<http://localhost:11434/v1>", "http://localhost:11434/v1"),
    ("localhost:11434/v1", "http://localhost:11434/v1"),
    ("127.0.0.1:1234/v1/", "http://127.0.0.1:1234/v1"),
    ("api.openai.com/v1", "https://api.openai.com/v1"),
    ("﻿https://api.openai.com/v1​", "https://api.openai.com/v1"),
    ("https://models.example.com/v1", "https://models.example.com/v1"),
    ("", ""), ("  ''  ", ""),
])
def test_clean_base_url(typed: str, stored: str) -> None:
    assert config.clean_base_url(typed) == stored
    assert config.clean_base_url(stored) == stored, "cleaning twice changes nothing"


@pytest.mark.parametrize("typed,stored", [
    ("abc123", "abc123"), ("  abc123\r\n", "abc123"), ("\"abc123\"", "abc123"), ("'abc123'", "abc123"),
    ("abc\n123", "abc123"), ("“abc123”", "abc123"), ("​abc123﻿", "abc123"), ("   ", ""),
])
def test_clean_api_key(typed: str, stored: str) -> None:
    assert config.clean_api_key(typed) == stored


@pytest.mark.parametrize("base_url", [
    GEMINI_URL, GEMINI_URL.rstrip("/"), f'"{GEMINI_URL}"', f"  {GEMINI_URL}models ", f"{GEMINI_URL}chat/completions",
])
def test_base_url_and_key_reach_the_request_however_the_url_was_pasted(base_url: str) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=completion())

    assert provider_for(handler, base_url).complete("S", "C", USER, "gemini-flash-latest", tools=False).text == "OK"
    assert str(seen[0].url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert seen[0].headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(seen[0].content)["model"] == "gemini-flash-latest"


def test_tool_list_for_openai_compatible_servers_has_no_unsupported_keywords() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=completion())

    provider_for(handler).complete("S", "C", USER, "gemini-flash-latest")
    allowed = {"type", "description", "properties", "required", "items", "enum", "minimum", "maximum",
               "minItems", "minLength", "maxLength", "pattern"}

    def walk(node: Any, names: bool = False) -> None:
        if isinstance(node, dict):
            if not names:
                assert set(node) <= allowed, set(node) - allowed
                assert isinstance(node.get("type"), str)
            for key, value in node.items():
                walk(value, names=(key == "properties" and not names))
        elif isinstance(node, list):
            for item in node:
                walk(item)

    assert len(seen[0]["tools"]) == len(registry.llm_specs())
    for tool in seen[0]["tools"]:
        assert set(tool) == {"type", "function"} and tool["function"]["parameters"]["type"] == "object"
        walk(tool["function"]["parameters"])
    # an argument that happens to be called like a keyword is kept
    schema = {"type": "object", "properties": {"format": {"type": "string", "format": "date"}},
              "additionalProperties": False}
    assert registry.export_schema(schema, config.OPENAI_SCHEMA_DROP) == {
        "type": "object", "properties": {"format": {"type": "string"}}}
    assert "additionalProperties" in registry.anthropic_tools()[0]["input_schema"], "Anthropic's list is unchanged"


# ---------------------------------------------------------------- keyring
class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> Optional[str]:
        return self.store.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.store[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        del self.store[(service, name)]


def test_key_is_cleaned_when_saved_and_when_read(monkeypatch: pytest.MonkeyPatch) -> None:
    ring = FakeKeyring()
    monkeypatch.setattr(providers, "_keyring", lambda: ring)
    assert set_api_key("openai_compat", '  "abc 123"\r\n')
    assert ring.store == {(config.KEYRING_SERVICE, "openai_compat"): "abc123"}
    ring.store[(config.KEYRING_SERVICE, "openai_compat")] = " 'older-build-key'\n"      # saved by an older build
    assert get_api_key("openai_compat") == "older-build-key"
    assert set_api_key("openai_compat", ' "" ') and ring.store == {}, "an empty key removes the entry"
    monkeypatch.setenv("OPENAI_API_KEY", " env-key\n")
    assert get_api_key("openai_compat") == "env-key"


# ---------------------------------------------------------------- settings → provider → Test connection
def offline_coach(repo: Repository) -> tuple[Coach, ToolExecutor]:
    bus = EventBus()
    executor = ToolExecutor(repo, bus, today_fn=lambda: TODAY)
    return Coach(repo, executor, bus, synchronous=True), executor


def test_settings_are_cleaned_on_save_and_apply_without_restart(seeded: Repository,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "  \"env-key\" ")
    coach, executor = offline_coach(seeded)
    assert coach.provider is None and coach.status_text() == "Offline coach"
    saved = executor.execute("update_settings", {"settings": {
        "provider": "openai_compat", "base_url": f' "{GEMINI_URL}models" ', "chat_model": " 'gemini-flash-latest' ",
        "background_model": "\"gemini-flash-latest\""}}, "ui")
    assert saved.ok
    assert seeded.get_setting("base_url") == GEMINI_URL.rstrip("/")
    assert seeded.get_setting("chat_model") == seeded.get_setting("background_model") == "gemini-flash-latest"
    coach.reload_provider()                              # what the Settings view does after every save
    assert isinstance(coach.provider, OpenAICompatProvider)
    assert str(coach.provider.base_url) == GEMINI_URL
    assert coach.provider._client.api_key == "env-key"
    assert coach.status_text() == "AI coach · gemini-flash-latest"
    executor.execute("update_settings", {"settings": {"base_url": "localhost:11434/v1/"}}, "ui")
    coach.reload_provider()
    assert str(coach.provider.base_url) == "http://localhost:11434/v1/"
    # a value stored by an older build (no clean-up on save) is cleaned when the provider is built
    seeded.set_setting("base_url", f' "{GEMINI_URL}models"')
    provider, _ = build_provider(seeded.all_settings())
    assert isinstance(provider, OpenAICompatProvider) and str(provider.base_url) == GEMINI_URL


TEST_CONNECTION_CASES = [
    (401, openai_error("Incorrect API key provided."), ["API key was rejected (HTTP 401)", "Check the key"]),
    (403, gemini_error(403, FREE_TIER, "PERMISSION_DENIED"),
     ["denied access (HTTP 403)", "free tier is not available for this project", "The key itself works"]),
    (404, "", ["Nothing answers at the Base URL", "Check the Base URL"]),
    (404, gemini_error(404, "models/gemini-nope is not found", "NOT_FOUND"),
     ["does not know the model 'gemini-nope'", "Check the model names"]),
    (429, gemini_error(429, "You exceeded your current quota.", "RESOURCE_EXHAUSTED"),
     ["quota", "(HTTP 429)", "plan and billing"]),
    (400, gemini_error(400, "Unknown name \"foo\": Cannot find field.", "INVALID_ARGUMENT"),
     ["rejected the request (HTTP 400)", "Cannot find field"]),
]


@pytest.mark.parametrize("status,body,expected", TEST_CONNECTION_CASES,
                         ids=["bad key", "no access", "wrong url", "unknown model", "quota", "bad request"])
def test_test_connection_shows_the_real_reason(seeded: Repository, monkeypatch: pytest.MonkeyPatch,
                                               status: int, body: str, expected: list[str]) -> None:
    seeded.set_setting("provider", "openai_compat")
    seeded.set_setting("chat_model", "gemini-nope")
    coach, _ = offline_coach(seeded)
    monkeypatch.setattr(coach_module, "build_provider", lambda settings: (provider_for(answering(status, body)), ""))
    ok, message = coach.test_connection()
    assert not ok and "\n" not in message
    for part in expected:
        assert part in message, message
    if status != 401:
        assert "API key was rejected" not in message


def test_test_connection_success_and_network_failure(seeded: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    seeded.set_setting("chat_model", "gemini-flash-latest")
    coach, _ = offline_coach(seeded)
    seen: list[dict[str, Any]] = []

    def fine(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=completion())

    monkeypatch.setattr(coach_module, "build_provider", lambda settings: (provider_for(fine), ""))
    assert coach.test_connection() == (True, "Connected: gemini-flash-latest answered.")
    assert seen[0]["model"] == "gemini-flash-latest" and "tools" not in seen[0]

    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("getaddrinfo failed", request=request)

    monkeypatch.setattr(coach_module, "build_provider", lambda settings: (provider_for(refused), ""))
    ok, message = coach.test_connection()
    assert not ok and "I can't reach the AI service at generativelanguage.googleapis.com" in message
    assert "getaddrinfo failed" in message and "Check the Base URL and the internet connection." in message


# ---------------------------------------------------------------- chat
def chat_coach(repo: Repository, script: list[Any]) -> tuple[Coach, FakeProvider]:
    bus = EventBus()
    executor = ToolExecutor(repo, bus, today_fn=lambda: TODAY)
    fake = FakeProvider(script)
    return Coach(repo, executor, bus, fake, synchronous=True), fake


def test_denied_access_goes_offline_once_with_the_real_reason(seeded: Repository) -> None:
    denied = providers.status_error(403, gemini_error(403, FREE_TIER, "PERMISSION_DENIED"))
    coach, fake = chat_coach(seeded, [denied])
    reply = coach.handle_user_message("today I only have 90 minutes")
    coach.handle_user_message("what now?")
    assert reply is not None and "Set today to 90 min" in reply.text
    notices = [m.text for m in seeded.list_messages() if m.role == "system_event"]
    assert len(notices) == 1 and "free tier is not available for this project" in notices[0]
    assert "API key was rejected" not in notices[0] and notices[0].endswith("— answering in offline mode.")
    assert len(fake.calls) == 1 and not coach.llm_enabled(), "no further calls until the settings change"


def test_wrong_url_is_reported_in_chat_and_tried_again_next_time(seeded: Repository) -> None:
    wrong = providers.status_error(404, "", base_url=f"{GEMINI_URL}models/")
    coach, fake = chat_coach(seeded, [wrong])
    coach.handle_user_message("what now?")
    notices = [m.text for m in seeded.list_messages() if m.role == "system_event"]
    assert notices == [f"Nothing answers at the Base URL {GEMINI_URL}models/ (HTTP 404) — answering in offline mode."]
    assert coach.handle_user_message("thanks").text == "OK." and len(fake.calls) == 2      # type: ignore[union-attr]
