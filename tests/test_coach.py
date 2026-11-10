"""P5: coach with FakeProvider — agent loop, fallbacks, caps, proactive events. No real LLM calls."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import pytest

from neuroplan import config, events
from neuroplan.coach.coach import Coach
from neuroplan.coach.llm_engine import LLMEngine
from neuroplan.coach.prompts import SYSTEM_PROMPT, approx_tokens, build_context
from neuroplan.coach.providers import (AnthropicProvider, FakeProvider, LLMResponse, OpenAICompatProvider,
                                       ProviderError, build_provider, get_api_key)
from neuroplan.coach.types import ToolCall
from neuroplan.db.repository import Repository
from neuroplan.events import EventBus
from neuroplan.tools.executor import ToolExecutor

from .conftest import TODAY

ISO = TODAY.isoformat()
NOW = datetime(2026, 11, 10, 19, 0)


def make(repo: Repository, script: Optional[list[Any]] = None, *, offline: bool = False,
         debounce_s: float = 0.0, now: datetime = NOW) -> tuple[Coach, FakeProvider, ToolExecutor]:
    bus = EventBus()
    executor = ToolExecutor(repo, bus, today_fn=lambda: TODAY)
    fake = FakeProvider(script)
    coach = Coach(repo, executor, bus, None if offline else fake, synchronous=True, debounce_s=debounce_s,
                  now_fn=lambda: now)
    return coach, fake, executor


def call(name: str, args: dict[str, Any], call_id: str) -> ToolCall:
    return ToolCall(name, args, call_id)


def coach_messages(repo: Repository) -> list[Any]:
    return [m for m in repo.list_messages() if m.role == "coach"]


# ---------------------------------------------------------------- agent loop
def test_scripted_multi_round_conversation(seeded: Repository) -> None:
    script = [
        LLMResponse("", [call("get_state", {}, "t1")], "tool_use"),
        LLMResponse("Updating your day.", [call("set_day_override", {"date": ISO, "minutes": 90}, "t2"),
                                           call("set_progress", {"subject": "ML", "expected_units_by_now": 7}, "t3")],
                    "tool_use"),
        LLMResponse("", [call("explain_priority", {"subject": "Machine Learning"}, "t4")], "tool_use"),
        LLMResponse("Done: today is 90 minutes and Machine Learning is now 3 units behind."),
    ]
    coach, fake, _ = make(seeded, script)
    reply = coach.handle_user_message("only 90 minutes today and I'm 3 behind in ML")
    assert reply is not None and reply.role == "coach"
    assert reply.text == "Done: today is 90 minutes and Machine Learning is now 3 units behind."
    # tools ran in order, through the executor, with the chat_llm source
    log = [(a.tool, a.source) for a in reversed(seeded.list_actions())]
    assert log == [("set_day_override", "chat_llm"), ("set_progress", "chat_llm")]
    assert seeded.get_overrides(TODAY)[TODAY].minutes == 90
    ml = seeded.get_subject_by_name("Machine Learning")
    assert ml is not None and ml.expected_units_by_now == 7
    # action badges: write tools only, with ids for undo
    badges = reply.actions["actions"]
    assert [b["tool"] for b in badges] == ["set_day_override", "set_progress"]
    assert all(b["ok"] and b["action_id"] for b in badges)
    assert "Set today to 90 min" in badges[0]["summary"]
    assert reply.actions["engine"] == "llm"
    # four model calls; tool results were fed back in the neutral format
    assert len(fake.calls) == 4 and all(c["tools"] for c in fake.calls)
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"
    last = fake.calls[-1]["messages"]
    assert [m["role"] for m in last[-7:]] == ["user", "assistant", "tool", "assistant", "tool", "tool", "assistant"] \
        or [m["role"] for m in last][-1] == "tool"
    tool_msgs = [m for m in last if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["t1", "t2", "t3", "t4"]
    assert json.loads(tool_msgs[1]["content"])["ok"] is True
    assert "plan_change" in json.loads(tool_msgs[1]["content"])
    assert coach.llm_calls_today() == 4
    assert "STATE SNAPSHOT" in fake.calls[0]["context"] and fake.calls[0]["system"] == SYSTEM_PROMPT


def test_loop_stops_after_six_rounds(seeded: Repository) -> None:
    script = [LLMResponse("", [call("get_plan", {}, f"t{i}")], "tool_use") for i in range(10)]
    coach, fake, _ = make(seeded, script)
    reply = coach.handle_user_message("loop forever")
    assert len(fake.calls) == config.LLM_MAX_ROUNDS and reply is not None and reply.text


def test_tool_error_is_fed_back_to_the_model(seeded: Repository) -> None:
    script = [LLMResponse("", [call("log_study", {"subject": "Deep Learning", "units": 40}, "t1")], "tool_use"),
              LLMResponse("Deep Learning only has 9 units left — how many did you finish?")]
    coach, fake, _ = make(seeded, script)
    reply = coach.handle_user_message("I finished 40 units of DL")
    assert reply is not None and "9 units left" in reply.text
    fed_back = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"][0]
    assert fed_back["ok"] is False and "only 9 unit(s) left" in fed_back["content"]
    assert reply.actions["actions"][0]["ok"] is False
    assert seeded.list_actions() == []


def test_llm_cannot_confirm_destructive_tools_or_use_hidden_ones(seeded: Repository) -> None:
    script = [LLMResponse("", [call("delete_subject", {"subject": "IELTS Prep", "confirmed": True}, "t1"),
                               call("reset_demo_data", {"confirmed": True}, "t2")], "tool_use"),
              LLMResponse("Please confirm the deletion below.")]
    coach, fake, _ = make(seeded, script)
    reply = coach.handle_user_message("delete IELTS")
    assert reply is not None
    assert seeded.get_subject_by_name("IELTS Prep") is not None, "nothing is deleted without the student's click"
    card = reply.actions["confirmations"][0]
    assert card["tool"] == "delete_subject" and card["args"] == {"subject": "IELTS Prep", "confirmed": True}
    results = [json.loads(m["content"]) for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert results[0]["needs_confirmation"] is True
    assert results[1]["ok"] is False and "Unknown tool" in results[1]["summary"]
    # the student confirms with the card
    confirmed = coach.select_option({"type": "confirm", **card}, "Confirm")
    assert confirmed is not None and "Deleted subject IELTS Prep" in confirmed.text
    assert seeded.get_subject_by_name("IELTS Prep") is None
    cancelled = coach.select_option({"type": "cancel"}, "Cancel")
    assert cancelled is not None and "nothing was changed" in cancelled.text


def test_history_window_and_roles(seeded: Repository) -> None:
    coach, fake, _ = make(seeded)
    for i in range(10):
        coach.handle_user_message(f"message {i}")
    sent = fake.calls[-1]["messages"]
    assert len(sent) <= config.HISTORY_WINDOW and sent[0]["role"] == "user"
    assert sent[-1] == {"role": "user", "content": "message 9"}
    assert {m["role"] for m in sent} == {"user", "assistant"}


def test_summary_updated_after_thirty_unsummarised_messages(seeded: Repository) -> None:
    coach, fake, _ = make(seeded)
    seeded.set_setting("daily_llm_call_cap", "500")
    for i in range(16):                       # 32 chat messages + the seeded welcome
        coach.handle_user_message(f"note {i}")
    summary_calls = [c for c in fake.calls if "running summary" in c["system"]]
    assert summary_calls, "the background model is asked to update the summary"
    assert summary_calls[0]["model"] == "claude-haiku-5-5" and summary_calls[0]["tools"] is False
    assert seeded.get_setting("conversation_summary") == "OK."
    upto = int(seeded.get_setting("summarised_upto_id") or 0)
    assert upto > 0 and seeded.count_messages(upto) <= config.SUMMARY_TRIGGER
    coach.handle_user_message("one more")
    chat_calls = [c for c in fake.calls if c["system"] == SYSTEM_PROMPT]
    assert "CONVERSATION SUMMARY SO FAR:\nOK." in chat_calls[-1]["context"]


# ---------------------------------------------------------------- fallbacks
def test_network_error_falls_back_to_offline(seeded: Repository) -> None:
    coach, fake, _ = make(seeded, [ProviderError("network", "connection failed")])
    reply = coach.handle_user_message("today I only have 90 minutes")
    assert reply is not None and "Set today to 90 min" in reply.text
    assert reply.actions["engine"] == "offline"
    assert seeded.get_overrides(TODAY)[TODAY].minutes == 90
    assert seeded.list_actions(1)[0].source == "chat_offline"
    notices = [m for m in seeded.list_messages() if m.role == "system_event"]
    assert len(notices) == 1 and "offline mode" in notices[0].text and "\n" not in notices[0].text
    # the next message tries the LLM again
    reply = coach.handle_user_message("thanks")
    assert reply is not None and reply.text == "OK." and len(fake.calls) == 2


def test_rate_limit_falls_back_to_offline(seeded: Repository) -> None:
    coach, _, _ = make(seeded, [ProviderError("rate_limit", "rate limited")])
    reply = coach.handle_user_message("what should I study now?")
    assert reply is not None and "Start with" in reply.text


def test_invalid_key_goes_offline_and_says_so_once(seeded: Repository) -> None:
    coach, fake, _ = make(seeded, [ProviderError("auth", "authentication failed")])
    coach.handle_user_message("what now?")
    coach.handle_user_message("what now?")
    coach.handle_user_message("undo")
    notices = [m for m in seeded.list_messages() if m.role == "system_event"]
    assert len(notices) == 1 and "API key was rejected" in notices[0].text
    assert len(fake.calls) == 1 and not coach.llm_enabled()
    assert coach.status_text() == "Offline coach"


def test_missing_key_runs_offline_and_says_so_once(seeded: Repository) -> None:
    seeded.set_setting("provider", "anthropic")
    bus = EventBus()
    executor = ToolExecutor(seeded, bus, today_fn=lambda: TODAY)
    coach = Coach(seeded, executor, bus, synchronous=True)
    assert not coach.llm_enabled()
    first = coach.handle_user_message("what should I study now?")
    coach.handle_user_message("what should I study now?")
    assert first is not None and "Start with" in first.text
    notices = [m for m in seeded.list_messages() if m.role == "system_event"]
    assert len(notices) == 1 and "No working API key" in notices[0].text


def test_offline_provider_setting_needs_no_notice(seeded: Repository) -> None:
    coach, fake, _ = make(seeded, offline=True)
    reply = coach.handle_user_message("what should I study now?")
    assert reply is not None and "Start with" in reply.text and fake.calls == []
    assert not [m for m in seeded.list_messages() if m.role == "system_event"]
    assert build_provider({"provider": "offline"}) == (None, "offline")
    assert build_provider({"provider": "anthropic"}) == (None, "no_key")
    assert get_api_key("anthropic") is None


def test_chat_respects_daily_cap(seeded: Repository) -> None:
    seeded.set_setting("daily_llm_call_cap", "1")
    coach, fake, _ = make(seeded)
    assert coach.handle_user_message("hello").text == "OK."            # type: ignore[union-attr]
    reply = coach.handle_user_message("today I only have 90 minutes")
    assert reply is not None and "Set today to 90 min" in reply.text and len(fake.calls) == 1
    coach.handle_user_message("undo")
    notices = [m for m in seeded.list_messages() if m.role == "system_event"]
    assert len(notices) == 1 and "call limit" in notices[0].text


# ---------------------------------------------------------------- proactive events
def test_daily_cap_switches_proactive_messages_to_templates(seeded: Repository) -> None:
    seeded.set_setting("daily_llm_call_cap", "1")
    coach, fake, _ = make(seeded, ["LLM briefing text"])
    coach.bus.emit(events.DAILY_BRIEFING)
    assert coach_messages(seeded)[-1].text == "LLM briefing text"
    assert fake.calls[0]["model"] == "claude-haiku-5-5" and fake.calls[0]["tools"] is False
    assert fake.calls[0]["messages"][0]["content"].startswith("[EVENT daily_briefing]")
    # cap reached: the end-of-day check-in now comes from the template, with no model call
    coach.bus.emit(events.END_OF_DAY)
    last = coach_messages(seeded)[-1]
    assert last.text.startswith("End-of-day check-in:") and last.proactive
    assert len(fake.calls) == 1 and coach.llm_calls_today() == 1


def test_daily_briefing_fires_once_per_day(seeded: Repository) -> None:
    coach, fake, _ = make(seeded, offline=True)
    before = len(coach_messages(seeded))
    coach.bus.emit(events.DAILY_BRIEFING)
    coach.bus.emit(events.DAILY_BRIEFING)
    coach.startup()
    briefings = coach_messages(seeded)[before:]
    texts = [m.text for m in briefings if "Today (Tuesday)" in m.text]
    assert len(texts) == 1
    assert texts[0].startswith("Good evening! Today (Tuesday): 4 block(s)")
    assert "What matters most: Deep Learning — 4 unit(s) behind, 10 day(s)" in texts[0]
    assert seeded.get_setting("last_briefing_date") == ISO
    # a new day briefs again
    seeded.set_setting("last_briefing_date", (TODAY - timedelta(days=1)).isoformat())
    coach.bus.emit(events.DAILY_BRIEFING)
    assert len([m for m in coach_messages(seeded) if "Today (Tuesday)" in m.text]) == 2


def test_plan_changed_is_debounced(seeded: Repository) -> None:
    coach, fake, executor = make(seeded, offline=True, debounce_s=0.15)
    before = len(coach_messages(seeded))
    assert executor.execute("set_day_override", {"date": ISO, "minutes": 120}).ok
    assert executor.execute("set_day_override", {"date": ISO, "minutes": 90}).ok
    assert executor.execute("set_day_override", {"date": ISO, "minutes": 60}).ok
    assert len(coach_messages(seeded)) == before, "nothing is said inside the debounce window"
    deadline = time.time() + 3
    while len(coach_messages(seeded)) == before and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.3)
    new = coach_messages(seeded)[before:]
    assert len(new) == 1, [m.text for m in new]
    assert new[0].text.startswith("Plan updated — Today:") and new[0].proactive
    coach.shutdown()


def test_plan_changed_quiet_for_chat_and_block_actions(seeded: Repository) -> None:
    coach, _, executor = make(seeded, offline=True)
    before = len(coach_messages(seeded))
    assert executor.execute("set_day_override", {"date": ISO, "minutes": 60}, "chat_offline").ok
    assert executor.execute("regenerate_plan", {}, "system").ok
    assert len(coach_messages(seeded)) == before
    assert executor.execute("set_day_override", {"date": ISO, "minutes": 120}, "ui").ok
    assert len(coach_messages(seeded)) == before + 1


def test_proactive_level_off_and_light(seeded: Repository) -> None:
    coach, _, executor = make(seeded, offline=True)
    seeded.set_setting("proactive_level", "off")
    before = len(coach_messages(seeded))
    coach.bus.emit(events.DAILY_BRIEFING)
    coach.bus.emit(events.BREAK_STARTED)
    coach.bus.emit(events.REVIEWS_DUE, count=5)
    assert executor.execute("set_day_override", {"date": ISO, "minutes": 60}).ok
    assert len(coach_messages(seeded)) == before
    seeded.set_setting("proactive_level", "light")
    coach.bus.emit(events.BREAK_STARTED)
    coach.bus.emit(events.REVIEWS_DUE, count=5)
    assert len(coach_messages(seeded)) == before
    coach.bus.emit(events.END_OF_DAY)
    assert len(coach_messages(seeded)) == before + 1


def test_block_finished_local_line_then_llm_recap(seeded: Repository) -> None:
    coach, fake, executor = make(seeded, ["Session recap from the model."])
    pending = [b for b in seeded.list_blocks(TODAY, TODAY) if b.status == "pending"]
    assert executor.execute("complete_block", {"id": pending[0].id}).ok
    line = coach_messages(seeded)[-1]
    assert line.text.startswith("✓ ") and "Next:" in line.text and fake.calls == [], "no LLM call per block"
    for _ in range(10):
        left = [b for b in seeded.list_blocks(TODAY, TODAY) if b.status == "pending"]
        if not left:
            break
        assert executor.execute("complete_block", {"id": left[0].id}).ok
    assert coach_messages(seeded)[-1].text == "Session recap from the model."
    assert len(fake.calls) == 1 and "[EVENT session_recap]" in fake.calls[0]["messages"][0]["content"]


def test_skip_asks_why_and_too_hard_lowers_mastery(seeded: Repository) -> None:
    coach, _, executor = make(seeded, offline=True)
    dl = seeded.get_subject_by_name("Deep Learning")
    assert dl is not None and dl.mastery == 2
    block = next(b for b in seeded.list_blocks(TODAY, TODAY) if b.subject_id == dl.id and b.block_type != "REVIEW")
    assert executor.execute("skip_block", {"id": block.id}).ok
    ask = coach_messages(seeded)[-1]
    assert "What happened?" in ask.text
    labels = [o["label"] for o in ask.actions["options"]]
    assert labels == ["No time", "Too hard", "Didn't feel like it", "Sick"]
    too_hard = ask.actions["options"][1]
    reply = coach.select_option(too_hard["payload"], too_hard["label"])
    fresh = seeded.get_subject(dl.id)
    assert fresh is not None and fresh.mastery == 1
    assert reply is not None and "lowered Deep Learning's mastery to 1" in reply.text
    assert [m.text for m in seeded.list_messages() if m.role == "user"][-1] == "Too hard"
    assert seeded.list_actions(2)[1].tool == "upsert_subject"
    sick = coach.select_option(ask.actions["options"][3]["payload"], "Sick")
    assert sick is not None and sick.actions["options"][0]["payload"]["type"] == "say"
    cleared = coach.select_option(sick.actions["options"][0]["payload"], "Clear today")
    assert cleared is not None and seeded.get_overrides(TODAY)[TODAY].minutes == 0


def test_risk_detected_presents_options_and_executes_choice(seeded: Repository) -> None:
    coach, _, executor = make(seeded, offline=True)
    dl = seeded.get_subject_by_name("Deep Learning")
    assert dl is not None
    midterm = seeded.list_assessments(dl.id)[0]
    assert executor.execute("update_assessment", {"id": midterm.id, "date": (TODAY + timedelta(days=1)).isoformat()},
                            "chat_offline").ok
    warning = coach_messages(seeded)[-1]
    assert "min short" in warning.text and warning.proactive
    options = warning.actions["options"]
    assert len(options) == 3 and all(o["payload"]["type"] == "risk_option" for o in options)
    shortfall_before = executor.plan().risks[0].shortfall_min
    reply = coach.select_option(options[0]["payload"], options[0]["label"])
    assert reply is not None and reply.text.startswith("Done — Add ")
    assert seeded.get_overrides(TODAY)[TODAY].minutes > 180
    risks = executor.plan().risks
    assert not risks or risks[0].shortfall_min < shortfall_before
    order = coach.select_option(options[1]["payload"], options[1]["label"])
    assert order is not None and "Prioritize units in this order: 4, 5, 6" in order.text
    cap = coach.select_option(options[2]["payload"], options[2]["label"])
    assert cap is not None and seeded.get_setting("cap_boosts") == f"[{dl.id}]"


def test_startup_handles_missed_blocks(seeded: Repository) -> None:
    yesterday = TODAY - timedelta(days=1)
    block = seeded.list_blocks(TODAY, TODAY)[1]
    seeded.update_block(block.id, plan_date=yesterday)
    coach, _, _ = make(seeded, offline=True)
    coach.startup()
    stale = seeded.get_block(block.id)
    assert stale is not None and stale.status == "skipped"
    texts = [m.text for m in coach_messages(seeded)]
    missed = next(t for t in texts if "left open" in t)
    assert "1 planned block(s)" in missed
    assert any("reviews are due today" in t for t in texts)
    assert any(t.startswith("Good evening!") for t in texts)
    coach.startup()
    assert len([t for t in (m.text for m in coach_messages(seeded)) if "reviews are due" in t]) == 1


def test_onboarding_starts_interview_on_empty_database(repo: Repository) -> None:
    coach, fake, executor = make(repo, offline=True)
    coach.startup()
    first = coach_messages(repo)[-1]
    assert "Which subjects are you taking" in first.text and first.proactive
    reply = coach.handle_user_message("Physics, Chemistry")
    assert reply is not None and "How much time can you study each day" in reply.text
    assert [s.name for s in repo.list_subjects()] == ["Physics", "Chemistry"]
    assert repo.get_setting("last_briefing_date") == "", "no briefing while onboarding is incomplete"


def test_onboarding_with_llm_uses_background_model(repo: Repository) -> None:
    coach, fake, _ = make(repo, ["Welcome! Which subjects are you taking this term?"])
    coach.startup()
    assert coach_messages(repo)[-1].text == "Welcome! Which subjects are you taking this term?"
    assert fake.calls[0]["model"] == "claude-haiku-5-5"
    assert "no subjects yet" in fake.calls[0]["context"]


def test_end_of_day_time_and_close(seeded: Repository) -> None:
    coach, _, _ = make(seeded, offline=True, now=datetime(2026, 11, 10, 21, 59))
    assert coach.check_end_of_day() is False
    coach.now = lambda: datetime(2026, 11, 10, 22, 0)
    assert coach.check_end_of_day() is True
    message = coach_messages(seeded)[-1]
    assert message.text.startswith("End-of-day check-in: 0 block(s)")
    assert coach.check_end_of_day() is False and coach.on_close() is False
    seeded.set_setting("last_checkin_date", "")
    assert coach.on_close() is True
    assert "Close the window again" in coach_messages(seeded)[-1].text
    assert coach.on_close() is False
    ack = coach.select_option(message.actions["options"][0]["payload"], "All logged, normal day")
    assert ack is not None and "See you tomorrow" in ack.text


def test_tool_errors_surface_as_system_messages(seeded: Repository) -> None:
    coach, _, _ = make(seeded, offline=True)
    coach.bus.emit(events.ERROR, message="Something went wrong in x: boom. Nothing was changed.")
    last = seeded.list_messages()[-1]
    assert last.role == "system_event" and "boom" in last.text


def test_async_submit_emits_busy_and_message_events(seeded: Repository) -> None:
    bus = EventBus()
    executor = ToolExecutor(seeded, bus, today_fn=lambda: TODAY)
    coach = Coach(seeded, executor, bus, FakeProvider(["hello from the worker"]))
    seen: list[Any] = []
    bus.subscribe(events.COACH_BUSY, lambda e: seen.append(("busy", e.payload["busy"])))
    bus.subscribe(events.CHAT_MESSAGE, lambda e: seen.append((e.payload["message"].role, e.payload["message"].text)))
    coach.submit("hi")
    deadline = time.time() + 5
    while ("busy", False) not in seen and time.time() < deadline:
        time.sleep(0.01)
    coach.shutdown()
    assert seen == [("busy", True), ("user", "hi"), ("coach", "hello from the worker"), ("busy", False)]


# ---------------------------------------------------------------- prompts & providers
def test_prompt_sizes_stay_small(seeded: Repository) -> None:
    assert approx_tokens(SYSTEM_PROMPT) <= 900
    _, _, executor = make(seeded, offline=True)
    context = build_context(executor.execute("get_state").data, "")
    assert approx_tokens(context) <= 1500
    for needle in ('"today":"2026-11-10"', '"weekday":"Tuesday"', '"backlog_units":4', '"streak_days"',
                   '"reviews_due":4', '"availability"', '"today_blocks"', '"risks"'):
        assert needle in context
    for phrase in ("one thing at a time", "ONE thing at a time"):
        if phrase in SYSTEM_PROMPT:
            break
    else:
        raise AssertionError("the system prompt must tell the coach to ask one thing at a time")
    assert "Arabic" in SYSTEM_PROMPT and "120 words" in SYSTEM_PROMPT


def test_anthropic_message_conversion() -> None:
    raw_blocks = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "tool_use", "id": "a",
                                                                             "name": "get_state", "input": {}}]
    neutral = [
        {"role": "assistant", "content": "leading coach message is dropped"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [ToolCall("get_state", {}, "a")],
         "provider": "anthropic", "provider_content": raw_blocks},
        {"role": "tool", "tool_call_id": "a", "name": "get_state", "content": "{}", "ok": True},
        {"role": "assistant", "content": "two calls", "provider": "fake",
         "tool_calls": [ToolCall("get_plan", {}, "b"), ToolCall("undo_last", {}, "c")]},
        {"role": "tool", "tool_call_id": "b", "name": "get_plan", "content": "{}", "ok": True},
        {"role": "tool", "tool_call_id": "c", "name": "undo_last", "content": "{}", "ok": False},
    ]
    out = AnthropicProvider.convert(neutral)
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant", "user"]
    assert out[1]["content"] is raw_blocks, "thinking blocks go back unchanged"
    assert out[2]["content"] == [{"type": "tool_result", "tool_use_id": "a", "content": "{}", "is_error": False}]
    assert [b["type"] for b in out[3]["content"]] == ["text", "tool_use", "tool_use"]
    results = out[4]["content"]
    assert [r["tool_use_id"] for r in results] == ["b", "c"], "all results of a turn share one user message"
    assert results[1]["is_error"] is True


def test_openai_message_conversion() -> None:
    neutral = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [ToolCall("get_plan", {"date_from": "today"}, "x")]},
        {"role": "tool", "tool_call_id": "x", "name": "get_plan", "content": "{\"ok\":true}", "ok": True},
    ]
    out = OpenAICompatProvider.convert("SYS", "CTX", neutral)
    assert out[0] == {"role": "system", "content": "SYS\n\nCTX"}
    assert out[2]["tool_calls"][0] == {"id": "x", "type": "function",
                                       "function": {"name": "get_plan", "arguments": "{\"date_from\": \"today\"}"}}
    assert out[2]["content"] is None
    assert out[3] == {"role": "tool", "tool_call_id": "x", "content": "{\"ok\":true}"}


def test_llm_engine_direct_use(seeded: Repository) -> None:
    executor = ToolExecutor(seeded, today_fn=lambda: TODAY)
    fake = FakeProvider([LLMResponse("", [call("regenerate_plan", {}, "r1")], "tool_use")])
    reply = LLMEngine(executor, fake).respond([{"role": "user", "content": "rebalance"}], "m", "ctx")
    assert reply.text == "OK." and reply.actions[0].tool == "regenerate_plan" and reply.engine == "llm"
    assert fake.calls[1]["messages"][1]["provider"] == "fake"


def test_api_keys_never_reach_sqlite_or_logs(seeded: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret-value")
    seeded.set_setting("provider", "anthropic")
    provider, reason = build_provider(seeded.all_settings())
    assert reason == "" and isinstance(provider, AnthropicProvider)
    dump = json.dumps(seeded.export_all())
    assert "sk-ant-test-secret-value" not in dump
    assert "api_key" not in config.DEFAULT_SETTINGS and "api_key" not in config.EDITABLE_SETTINGS


def test_subject_added_in_ui_triggers_one_onboarding_question(seeded: Repository) -> None:
    coach, _, executor = make(seeded, offline=True)
    before = len(coach_messages(seeded))
    assert executor.execute("upsert_subject", {"name": "Databases", "total_units": 8}, "ui").ok
    asked = [m.text for m in coach_messages(seeded)[before:]]
    assert any("next exam, quiz or deadline for Databases" in t for t in asked)
    count = len([t for t in (m.text for m in coach_messages(seeded)) if "deadline for Databases" in t])
    assert executor.execute("upsert_subject", {"name": "Databases", "mastery": 4}, "ui").ok
    again = len([t for t in (m.text for m in coach_messages(seeded)) if "deadline for Databases" in t])
    assert again == count == 1, "the same question is not repeated after every edit"
    # the answer in chat continues the interview
    reply = coach.handle_user_message("in 20 days")
    assert reply is not None and len(seeded.list_assessments()) == 6
    assert "how many units should you have covered" in reply.text
    # subjects added through chat do not trigger the extra proactive question
    quiet = len(coach_messages(seeded))
    assert executor.execute("upsert_subject", {"name": "Networks", "total_units": 5}, "chat_llm").ok
    later = [m.text for m in coach_messages(seeded)[quiet:]]
    assert not any("Networks" in t and "?" in t and "short" not in t for t in later), later
