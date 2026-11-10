"""Agent loop with tool calling: the model proposes tool calls, the executor runs them, results go back."""
from __future__ import annotations

import json
from typing import Any, Callable

from .. import config
from ..models import ToolResult
from ..tools.executor import ToolExecutor
from ..tools.registry import TOOLS
from .prompts import SYSTEM_PROMPT
from .providers import Provider, ProviderError
from .types import ActionRecord, EngineReply, ToolCall


class CapReached(Exception):
    """The daily LLM call cap was hit before (or during) the conversation turn."""


class LLMEngine:
    def __init__(self, executor: ToolExecutor, provider: Provider) -> None:
        self.executor = executor
        self.provider = provider

    def respond(self, history: list[dict[str, Any]], model: str, context: str,
                allow_call: Callable[[], bool] = lambda: True, source: str = "chat_llm") -> EngineReply:
        """Run up to LLM_MAX_ROUNDS rounds of model call -> tool execution.

        ``history`` is the neutral message list ending with the student's message.
        ``allow_call`` is asked before every model call; False raises CapReached.
        Raises ProviderError on network/auth/rate errors (the coach then falls back to offline)."""
        messages = list(history)
        reply = EngineReply("", engine="llm")
        text = ""
        for _round in range(config.LLM_MAX_ROUNDS):
            if not allow_call():
                if reply.actions:
                    break                       # keep what was already done; summarise below
                raise CapReached()
            try:
                response = self.provider.complete(SYSTEM_PROMPT, context, messages, model, tools=True)
            except ProviderError:
                if reply.actions:
                    break                       # tools already ran: report them instead of redoing offline
                raise
            text = response.text
            if not response.tool_calls:
                break
            messages.append({"role": "assistant", "content": response.text, "tool_calls": response.tool_calls,
                             "provider": self.provider.name, "provider_content": response.provider_content})
            for call in response.tool_calls:
                result = self._execute(call, source)
                spec = TOOLS.get(call.name)
                if result.needs_confirmation:
                    reply.confirmations.append({"tool": call.name, "args": result.data["args"],
                                                "preview": result.preview})
                else:
                    reply.actions.append(ActionRecord.from_result(call, result, bool(spec and spec.write)))
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                 "content": json.dumps(result.to_dict(), ensure_ascii=False, default=str),
                                 "ok": result.ok})
            text = ""
        if not text:
            text = self._fallback_text(reply)
        reply.text = text
        return reply

    def _execute(self, call: ToolCall, source: str) -> ToolResult:
        spec = TOOLS.get(call.name)
        if spec is None or not spec.llm_visible:
            return ToolResult(False, f"Unknown tool '{call.name}'", tool=call.name)
        args = dict(call.args)
        if spec.requires_confirmation:
            args.pop("confirmed", None)         # only the student's click on the card may confirm
        return self.executor.execute(call.name, args, source)

    @staticmethod
    def _fallback_text(reply: EngineReply) -> str:
        done = [a.summary for a in reply.actions if a.write and a.ok]
        failed = [a.summary for a in reply.actions if not a.ok]
        parts: list[str] = []
        if done:
            parts.append("Done: " + "; ".join(done) + ".")
        if failed:
            parts.append("I couldn't do this: " + "; ".join(failed))
        if reply.confirmations:
            parts.append("Please confirm below.")
        return " ".join(parts) or "I looked at your plan but have nothing to add yet — what would you like to change?"
