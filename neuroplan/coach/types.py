"""Small data types shared by the coach engines."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..models import ToolResult


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass
class ActionRecord:
    """One executed tool call, as shown in the 'Actions executed' badge."""
    tool: str
    args: dict[str, Any]
    ok: bool
    summary: str
    action_id: Optional[int] = None
    write: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "ok": self.ok, "summary": self.summary, "action_id": self.action_id,
                "write": self.write}

    @classmethod
    def from_result(cls, call: ToolCall, result: ToolResult, write: bool) -> "ActionRecord":
        return cls(call.name, call.args, result.ok, result.summary, result.action_id, write)


@dataclass
class EngineReply:
    text: str
    actions: list[ActionRecord] = field(default_factory=list)
    confirmations: list[dict[str, Any]] = field(default_factory=list)   # {"tool","args","preview"}
    options: list[dict[str, Any]] = field(default_factory=list)         # {"label","payload"}
    engine: str = "offline"
    notice: str = ""

    def actions_payload(self) -> dict[str, Any]:
        """JSON stored in chat_messages.actions_json and rendered by the coach panel."""
        payload: dict[str, Any] = {}
        shown = [a.to_dict() for a in self.actions if a.write or not a.ok]
        if shown:
            payload["actions"] = shown
        if self.confirmations:
            payload["confirmations"] = self.confirmations
        if self.options:
            payload["options"] = self.options
        if self.engine:
            payload["engine"] = self.engine
        return payload
