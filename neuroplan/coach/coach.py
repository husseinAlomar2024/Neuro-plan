"""The live coach: routes chat to the LLM or offline engine, keeps history, reacts to events."""
from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from .. import config, events
from ..db.repository import Repository
from ..events import Event, EventBus
from ..models import ChatMessage
from ..tools.executor import ToolExecutor, describe_diff
from ..tools.registry import TOOLS
from .llm_engine import CapReached, LLMEngine
from .offline_engine import OfflineEngine
from .prompts import SUMMARY_INSTRUCTION, SYSTEM_PROMPT, build_context, event_message
from .providers import Provider, ProviderError, build_provider, get_api_key, set_api_key
from .types import ActionRecord, EngineReply, ToolCall

log = logging.getLogger(__name__)
_QUIET_PLAN_TOOLS = {"complete_block", "skip_block", "complete_review", "lock_block", "undo_last"}
_ONBOARDING_TOOLS = {"upsert_subject", "import_data", "reset_demo_data"}


class Coach:
    def __init__(self, repo: Repository, executor: ToolExecutor, bus: Optional[EventBus] = None,
                 provider: Optional[Provider] = None, *, synchronous: bool = False,
                 debounce_s: float = config.PLAN_CHANGED_DEBOUNCE_S,
                 now_fn: Callable[[], datetime] = datetime.now) -> None:
        self.repo = repo
        self.executor = executor
        self.bus = bus or executor.bus
        self.now = now_fn
        self.synchronous = synchronous
        self.debounce_s = debounce_s
        self.offline = OfflineEngine(executor)
        self._forced_provider = provider is not None
        self.provider: Optional[Provider] = provider
        self._offline_reason = "" if provider is not None else "offline"
        self._auth_failed = False
        self._notices_shown: set[str] = set()
        self._lock = threading.RLock()
        self._plan_timer: Optional[threading.Timer] = None
        self._plan_pending: list[dict[str, Any]] = []
        self._tip_index = 0
        self._confirm_seq = 0
        self._last_onboarding_key = ""
        self._pending_confirmations: dict[str, dict[str, Any]] = {}
        self._queue: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._closed = False
        if not self._forced_provider:
            self.reload_provider()
        self._handlers: dict[str, Callable[[Event], None]] = {
            events.PLAN_CHANGED: self._on_plan_changed,
            events.BLOCK_FINISHED: self._on_block_finished,
            events.BLOCK_SKIPPED: self._on_block_skipped,
            events.MISSED_YESTERDAY: self._on_missed_yesterday,
            events.RISK_DETECTED: self._on_risk_detected,
            events.ONBOARDING_NEEDED: self._on_onboarding_needed,
            events.DAILY_BRIEFING: self._on_daily_briefing,
            events.END_OF_DAY: self._on_end_of_day,
            events.REVIEWS_DUE: self._on_reviews_due,
            events.BREAK_STARTED: self._on_break_started,
            events.ERROR: self._on_error,
        }
        for name in self._handlers:
            self.bus.subscribe(name, self.on_event)
        self.bus.subscribe(events.STATE_CHANGED, self._on_state_changed)

    # ------------------------------------------------------------ infrastructure
    def on_event(self, event: Event) -> None:
        """EventBus entry point: decide (on the worker thread) whether and how to speak."""
        handler = self._handlers.get(event.name)
        if handler is not None:
            self._dispatch(lambda: handler(event))

    def _dispatch(self, fn: Callable[[], None]) -> None:
        if self.synchronous:
            self._safely(fn)
            return
        with self._lock:
            if self._closed:
                return
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="coach-worker", daemon=True)
                self._worker.start()
        self._queue.put(fn)

    def _work(self) -> None:
        while True:
            fn = self._queue.get()
            if fn is None:
                return
            self._safely(fn)

    def _safely(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # never let the worker die; tell the student instead
            log.exception("coach task failed")
            try:
                self._post("system_event", f"Something went wrong in the coach: {exc}")
            except Exception:
                log.exception("could not report coach failure")

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            if self._plan_timer is not None:
                self._plan_timer.cancel()
                self._plan_timer = None
        self._queue.put(None)

    def _post(self, role: str, text: str, actions: Optional[dict[str, Any]] = None,
              proactive: bool = False) -> ChatMessage:
        message = self.repo.add_message(role, text, actions, proactive)
        self.bus.emit(events.CHAT_MESSAGE, message=message)
        return message

    def _post_reply(self, reply: EngineReply, proactive: bool = False) -> ChatMessage:
        if reply.notice:
            self._post("system_event", reply.notice)
        return self._post("coach", reply.text, reply.actions_payload(), proactive)

    def _setting(self, key: str) -> str:
        return self.repo.get_setting(key, config.DEFAULT_SETTINGS.get(key, "")) or ""

    def _today_iso(self) -> str:
        return self.executor.today().isoformat()

    # ------------------------------------------------------------ provider management
    def reload_provider(self) -> None:
        """Rebuild the provider from settings + keyring (after Settings changed)."""
        if self._forced_provider:
            return
        try:
            self.provider, self._offline_reason = build_provider(self.repo.all_settings())
        except Exception:
            log.exception("could not initialise the provider")
            self.provider, self._offline_reason = None, "error"
        self._auth_failed = False
        self._notices_shown.discard("no_key")
        self._notices_shown.discard("auth")

    def llm_enabled(self) -> bool:
        return self.provider is not None and not self._auth_failed

    def status_text(self) -> str:
        if self.llm_enabled():
            return f"AI coach · {self._setting('chat_model')}"
        return "Offline coach"

    def has_api_key(self, provider: str) -> bool:
        return bool(get_api_key(provider))

    def save_api_key(self, provider: str, key: str) -> bool:
        ok = set_api_key(provider, key.strip())
        self.reload_provider()
        return ok

    def test_connection(self) -> tuple[bool, str]:
        try:
            provider, reason = build_provider(self.repo.all_settings())
        except Exception as exc:
            return False, f"Could not initialise the provider: {exc}"
        if provider is None:
            return False, ("Provider is set to offline." if reason == "offline"
                           else "No API key found (keyring or environment variable).")
        model = self._setting("chat_model")
        try:
            provider.complete("You are a connection test.", "Reply with the single word OK.",
                              [{"role": "user", "content": "ping"}], model, tools=False)
        except ProviderError as exc:
            return False, exc.notice().split(" — ")[0]
        return True, f"Connected: {model} answered."

    def test_connection_async(self, callback: Callable[[bool, str], None]) -> None:
        def task() -> None:
            callback(*self.test_connection())
        threading.Thread(target=lambda: self._safely(task), name="coach-test", daemon=True).start()

    # ------------------------------------------------------------ LLM budget
    def _allow_llm_call(self) -> bool:
        """Count one LLM call against daily_llm_call_cap; False when the cap is reached."""
        with self._lock:
            today = self._today_iso()
            try:
                cap = int(self._setting("daily_llm_call_cap") or 0)
            except ValueError:
                cap = int(config.DEFAULT_SETTINGS["daily_llm_call_cap"])
            count = int(self.repo.get_setting("llm_calls_count", "0") or 0)
            if self.repo.get_setting("llm_calls_date") != today:
                count = 0
            if count >= cap:
                return False
            self.repo.set_setting("llm_calls_date", today)
            self.repo.set_setting("llm_calls_count", str(count + 1))
            return True

    def llm_calls_today(self) -> int:
        if self.repo.get_setting("llm_calls_date") != self._today_iso():
            return 0
        return int(self.repo.get_setting("llm_calls_count", "0") or 0)

    def _notice_once(self, key: str, text: str) -> str:
        if key in self._notices_shown:
            return ""
        self._notices_shown.add(key)
        return text

    # ------------------------------------------------------------ chat
    def history(self, limit: int = 200) -> list[ChatMessage]:
        return self.repo.list_messages(limit)

    def submit(self, text: str) -> None:
        """Handle a chat message on the worker thread; results arrive as chat_message events."""
        self._busy_task(lambda: self.handle_user_message(text))

    def submit_option(self, payload: dict[str, Any], label: str = "") -> None:
        self._busy_task(lambda: self.select_option(payload, label))

    def _busy_task(self, fn: Callable[[], Any]) -> None:
        def task() -> None:
            self.bus.emit(events.COACH_BUSY, busy=True)
            try:
                fn()
            finally:
                self.bus.emit(events.COACH_BUSY, busy=False)
        self._dispatch(task)

    def handle_user_message(self, text: str) -> Optional[ChatMessage]:
        text = text.strip()
        if not text:
            return None
        self._post("user", text)
        return self._post_reply(self._reply_to(text))

    def _history_for_llm(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in self.repo.list_messages(config.HISTORY_WINDOW):
            if m.role == "user" and m.text:
                out.append({"role": "user", "content": m.text})
            elif m.role == "coach" and m.text:
                out.append({"role": "assistant", "content": m.text})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    def _context(self) -> str:
        state = self.executor.execute("get_state").data
        return build_context(state, self._setting("conversation_summary"))

    def _reply_to(self, text: str) -> EngineReply:
        notice = ""
        if self.llm_enabled():
            assert self.provider is not None
            try:
                reply = LLMEngine(self.executor, self.provider).respond(
                    self._history_for_llm(), self._setting("chat_model"), self._context(),
                    self._allow_llm_call)
                self._maybe_summarise()
                return reply
            except CapReached:
                notice = self._notice_once(
                    f"cap:{self._today_iso()}",
                    "Today's AI call limit is reached — I'm answering in offline mode until tomorrow.")
            except ProviderError as exc:
                log.warning("LLM call failed (%s): %s", exc.kind, exc.message)
                if exc.kind == "auth":
                    self._auth_failed = True
                    notice = self._notice_once("auth", exc.notice())
                    self._notices_shown.add("no_key")        # one notice is enough
                else:
                    notice = exc.notice()
        elif self._offline_reason == "no_key" or self._auth_failed:
            notice = self._notice_once(
                "no_key", "No working API key — I'm running in offline mode. Add a key in Settings to "
                          "enable the AI coach.")
        reply = self.offline.respond(text)
        reply.notice = notice
        return reply

    def _maybe_summarise(self) -> None:
        """When more than SUMMARY_TRIGGER messages are unsummarised, fold the older ones into the summary."""
        upto = int(self.repo.get_setting("summarised_upto_id", "0") or 0)
        if self.repo.count_messages(upto) <= config.SUMMARY_TRIGGER or not self.llm_enabled():
            return
        pending = [m for m in self.repo.list_messages(1000, upto) if m.role in ("user", "coach")]
        older = pending[:-config.HISTORY_WINDOW]
        if not older or not self._allow_llm_call():
            return
        assert self.provider is not None
        transcript = "\n".join(f"{'Student' if m.role == 'user' else 'Coach'}: {m.text}" for m in older)
        prompt = SUMMARY_INSTRUCTION.format(max_words=config.SUMMARY_MAX_WORDS,
                                            summary=self._setting("conversation_summary") or "(none)",
                                            messages=transcript)
        try:
            response = self.provider.complete(
                "You maintain a concise running summary of a coaching conversation.", "No extra context.",
                [{"role": "user", "content": prompt}], self._setting("background_model"), tools=False)
        except ProviderError as exc:
            log.warning("summary update failed: %s", exc.message)
            return
        words = response.text.split()
        if not words:
            return
        self.repo.set_setting("conversation_summary", " ".join(words[:config.SUMMARY_MAX_WORDS]))
        self.repo.set_setting("summarised_upto_id", str(older[-1].id))

    # ------------------------------------------------------------ option buttons / cards
    def request_confirmation(self, tool: str, args: dict[str, Any], preview: str) -> ChatMessage:
        """Show a Confirm/Cancel card for a tool the student triggered from the UI."""
        card: dict[str, Any] = {"tool": tool, "preview": preview}
        if len(json.dumps(args, default=str)) > 2000:      # e.g. import_data: keep the payload in memory
            self._confirm_seq += 1
            token = f"c{self._confirm_seq}"
            self._pending_confirmations[token] = args
            card["token"] = token
        else:
            card["args"] = args
        return self._post("coach", "This needs your confirmation.", {"confirmations": [card]})

    def select_option(self, payload: dict[str, Any], label: str = "") -> Optional[ChatMessage]:
        kind = payload.get("type")
        if kind == "say":
            return self.handle_user_message(str(payload.get("text", label)))
        if label:
            self._post("user", label)
        if kind == "confirm":
            args = payload.get("args")
            if args is None:
                args = self._pending_confirmations.pop(str(payload.get("token")), None)
            if args is None:
                return self._post("coach", "That confirmation has expired — please start the action again.")
            return self._post_reply(self._run([ToolCall(payload["tool"], dict(args))], "ui"))
        if kind == "cancel":
            return self._post("coach", "Okay — cancelled, nothing was changed.")
        if kind == "ack":
            return self._post("coach", "Great — everything is up to date. See you tomorrow.")
        if kind == "undo":
            return self._post_reply(self._run([ToolCall("undo_last", {})], "ui"))
        if kind == "risk_option":
            return self._post_reply(self._risk_choice(payload))
        if kind == "skip_reason":
            return self._post_reply(self._skip_reason(payload))
        return self._post("coach", "I don't know how to do that yet.")

    def _run(self, calls: list[ToolCall], source: str, intro: str = "") -> EngineReply:
        """Execute tool calls and build a short confirmation reply."""
        reply = EngineReply("", engine="offline")
        done: list[str] = []
        errors: list[str] = []
        moved = ""
        for call in calls:
            result = self.executor.execute(call.name, call.args, source)
            reply.actions.append(ActionRecord.from_result(call, result, TOOLS[call.name].write))
            if result.ok:
                done.append(result.summary)
                moved = result.plan_change or moved
            else:
                errors.append(result.summary)
        parts = [intro] if intro else ([("; ".join(done) + ".")] if done else [])
        if moved:
            parts.append(f"What moved — {moved}.")
        if errors:
            parts.append("I couldn't do that: " + " ".join(errors))
        reply.text = "\n".join(p for p in parts if p) or "Done."
        return reply

    def _risk_choice(self, payload: dict[str, Any]) -> EngineReply:
        actions = [ToolCall(a["tool"], dict(a["args"])) for a in payload.get("actions", [])]
        title = payload.get("title", "that assessment")
        if not actions:
            return EngineReply(f"Good choice for {title}. {payload.get('detail', '')} "
                               f"The plan itself stays as it is — follow that order inside your blocks.")
        reply = self._run(actions, "ui", intro=f"Done — {payload.get('label', 'option applied')} for {title}.")
        remaining = [r for r in self.executor.plan().risks if r.title == title]
        if remaining:
            reply.text += f"\nStill {remaining[0].shortfall_min} min short for {title}; another option can close the rest."
        else:
            reply.text += f"\n{title} is now fully covered by the plan."
        # one badge is enough for a multi-day change
        if len(reply.actions) > 1 and all(a.ok for a in reply.actions):
            last = reply.actions[-1]
            last.summary = f"{payload.get('label', 'Risk option')} ({len(reply.actions)} changes)"
        return reply

    def _skip_reason(self, payload: dict[str, Any]) -> EngineReply:
        reason = str(payload.get("reason", ""))
        subject = payload.get("subject")
        calls: list[ToolCall] = []
        intro = "Okay."
        if reason == "Too hard" and subject:
            subjects = self.executor.execute("list_subjects").data["subjects"]
            current = next((s for s in subjects if s["name"] == subject), None)
            if current and current["mastery"] > 1:
                calls.append(ToolCall("upsert_subject", {"name": subject, "mastery": current["mastery"] - 1}))
                intro = (f"Got it — I lowered {subject}'s mastery to {current['mastery'] - 1}. Its blocks switch "
                         f"to a gentler study mode and it gets more weight in the plan.")
            else:
                intro = (f"Got it. {subject} is already at the lowest mastery level, so its blocks are in "
                         f"concept-acquisition mode. Start with just the first 15 minutes.")
        elif reason == "No time":
            intro = ("No problem — the work is moved to the coming days. If today is shorter than planned, "
                     "tell me, e.g. \"today I only have 60 minutes\".")
        elif reason == "Didn't feel like it":
            intro = ("It happens. Try only the first 10 minutes of the next block — starting is the hard part. "
                     "The skipped work is already redistributed.")
        elif reason == "Sick":
            intro = "Rest first. If you can't study today at all, I can clear today and spread the work out."
        calls.append(ToolCall("regenerate_plan", {}))
        reply = self._run(calls, "chat_offline", intro=intro)
        reply.actions = [a for a in reply.actions if a.tool != "regenerate_plan" or not a.ok]
        if reason == "Sick":
            reply.options.append({"label": "Clear today",
                                  "payload": {"type": "say", "text": "I couldn't study today, rebalance"}})
        return reply

    # ------------------------------------------------------------ proactive behaviour
    def _allowed(self, event_name: str) -> bool:
        level = self._setting("proactive_level")
        if level == "off":
            return False
        return event_name in (config.PROACTIVE_LIGHT if level == "light" else config.PROACTIVE_FULL)

    def _background_text(self, prompt_name: str, template: str, **facts: Any) -> str:
        """Text for a proactive message: background model when available and under the cap, else template."""
        if not self.llm_enabled() or not self._allow_llm_call():
            return template
        assert self.provider is not None
        try:
            response = self.provider.complete(
                SYSTEM_PROMPT, self._context(),
                [{"role": "user", "content": event_message(prompt_name, **facts)}],
                self._setting("background_model"), tools=False)
        except ProviderError as exc:
            log.warning("proactive LLM call failed (%s): %s", exc.kind, exc.message)
            return template
        return response.text or template

    def _speak(self, text: str, options: Optional[list[dict[str, Any]]] = None) -> ChatMessage:
        payload: dict[str, Any] = {"options": options} if options else {}
        return self._post("coach", text, payload, proactive=True)

    def startup(self) -> None:
        """Run once after the UI is up: missed blocks, fresh plan, onboarding or briefing, review nudge."""
        today = self.executor.today()
        missed = self.repo.pending_blocks_before(today)
        if missed:
            names: list[str] = []
            for block in missed:
                subject = self.repo.get_subject(block.subject_id)
                if subject and subject.name not in names:
                    names.append(subject.name)
                self.executor.execute("skip_block", {"id": block.id, "reason": "missed"}, "system")
            self.bus.emit(events.MISSED_YESTERDAY, count=len(missed), subjects=names)
        self.executor.execute("regenerate_plan", {}, "system")
        if self.executor.missing_info():
            self.bus.emit(events.ONBOARDING_NEEDED, missing=self.executor.missing_info())
        else:
            self.bus.emit(events.DAILY_BRIEFING)
        due = self.executor.execute("get_state").data["kpis"]["reviews_due"]
        if due >= config.REVIEWS_NUDGE_MIN:
            self.bus.emit(events.REVIEWS_DUE, count=due)

    def check_end_of_day(self) -> bool:
        """Called periodically by the UI: emit end_of_day once the configured time has passed."""
        now = self.now()
        if self.repo.get_setting("last_checkin_date") == self._today_iso():
            return False
        if now.strftime("%H:%M") < self._setting("end_of_day_time"):
            return False
        self.bus.emit(events.END_OF_DAY, trigger="time")
        return True

    def on_close(self) -> bool:
        """Called when the window is closing. True = a check-in was posted, keep the app open once."""
        if self.repo.get_setting("last_checkin_date") == self._today_iso() or not self._allowed(events.END_OF_DAY):
            return False
        self.bus.emit(events.END_OF_DAY, trigger="close")
        return True

    def _on_state_changed(self, event: Event) -> None:
        """A subject was added/changed in the UI: if the plan now lacks information, ask for it."""
        if event.payload.get("source") == "ui" and event.payload.get("tool") in _ONBOARDING_TOOLS:
            self._dispatch(self._check_onboarding)

    def _check_onboarding(self) -> None:
        missing = self.executor.missing_info()
        if missing and json.dumps(missing[0], sort_keys=True) != self._last_onboarding_key:
            self.bus.emit(events.ONBOARDING_NEEDED, missing=missing)

    # ---- event handlers (run on the worker thread)
    def _on_error(self, event: Event) -> None:
        self._post("system_event", str(event.payload.get("message", "Unknown error")))

    def _on_plan_changed(self, event: Event) -> None:
        payload = event.payload
        if payload.get("source") != "ui" or payload.get("tool") in _QUIET_PLAN_TOOLS:
            return                                   # chat replies and block cards explain themselves
        if not self._allowed(events.PLAN_CHANGED):
            return
        with self._lock:
            self._plan_pending.append(payload)
            if self._plan_timer is not None:
                self._plan_timer.cancel()
            if self.debounce_s <= 0:
                self._plan_timer = None
            else:
                self._plan_timer = threading.Timer(self.debounce_s, lambda: self._dispatch(self._flush_plan_changed))
                self._plan_timer.daemon = True
                self._plan_timer.start()
        if self.debounce_s <= 0:
            self._flush_plan_changed()

    def _flush_plan_changed(self) -> None:
        with self._lock:
            pending, self._plan_pending = self._plan_pending, []
            self._plan_timer = None
        if not pending:
            return
        merged: dict[tuple[str, str], int] = {}
        for payload in pending:
            for entry in payload.get("diff", []):
                key = (entry["date"], entry["subject"])
                merged[key] = merged.get(key, 0) + entry["delta_min"]
        diff = [{"date": d, "subject": s, "delta_min": delta} for (d, s), delta in sorted(merged.items()) if delta]
        if not diff:
            return
        summary = describe_diff(diff, self.executor.today())
        tools = ", ".join(sorted({p.get("tool", "?") for p in pending}))
        template = f"Plan updated — {summary}."
        self._speak(self._background_text("plan_changed", template, diff=summary, tool=tools))

    def _on_block_finished(self, event: Event) -> None:
        p = event.payload
        if p.get("source") != "ui" or not self._allowed(events.BLOCK_FINISHED):
            return
        state = self.executor.execute("get_state").data
        kpis = state["kpis"]
        if p.get("last_of_day"):
            done = [b for b in state["today_blocks"] if b["status"] in ("done", "partial")]
            tomorrow = (self.executor.today() + timedelta(days=1)).isoformat()
            plan = self.executor.execute("get_plan", {"date_from": tomorrow}).data["days"][0]["blocks"]
            first = f" Tomorrow starts with {plan[0]['subject']} at {plan[0]['start']}." if plan else ""
            template = (f"That was today's last block — {len(done)} block(s), {kpis['done_min_today']} min done, "
                        f"streak {kpis['streak_days']} day(s).{first} Well done.")
            facts = {"blocks_done": len(done), "minutes_done": kpis["done_min_today"],
                     "streak_days": kpis["streak_days"],
                     "tomorrow_first": plan[0]["subject"] if plan else None}
            self._speak(self._background_text("session_recap", template, facts=facts))
            return
        units = p.get("units_done") or []
        note = f" Unit {', '.join(map(str, units))} finished — first review tomorrow." if units else ""
        pending = [b for b in state["today_blocks"] if b["status"] == "pending"]
        nxt = f" Next: {pending[0]['start']} {pending[0]['subject']}." if pending else ""
        label = "logged as partial" if p.get("status") == "partial" else "done"
        self._speak(f"✓ {p.get('subject', 'Block')} {label} ({p.get('actual_min', 0)} min).{note}{nxt}")

    def _skip_options(self, subject: Optional[str]) -> list[dict[str, Any]]:
        return [{"label": reason, "payload": {"type": "skip_reason", "reason": reason, "subject": subject}}
                for reason in config.SKIP_REASONS]

    def _on_block_skipped(self, event: Event) -> None:
        p = event.payload
        if p.get("source") != "ui" or not self._allowed(events.BLOCK_SKIPPED):
            return
        self._speak(f"You skipped the {p.get('subject', '')} block — its work is moved to the coming days. "
                    f"What happened?", self._skip_options(p.get("subject")))

    def _on_missed_yesterday(self, event: Event) -> None:
        if not self._allowed(events.MISSED_YESTERDAY):
            return
        subjects = event.payload.get("subjects") or []
        count = event.payload.get("count", 0)
        names = ", ".join(subjects) or "your subjects"
        self._speak(f"{count} planned block(s) from before today were left open ({names}). I marked them as "
                    f"skipped and re-planned the work. What happened?",
                    self._skip_options(subjects[0] if subjects else None))

    def _on_risk_detected(self, event: Event) -> None:
        if not self._allowed(events.RISK_DETECTED):
            return
        reply = EngineReply("", engine="offline")
        reply.text = OfflineEngine.describe_risks(event.payload.get("risks", []), reply)
        self._speak(reply.text, reply.options)

    def _on_onboarding_needed(self, event: Event) -> None:
        if not self._allowed(events.ONBOARDING_NEEDED):
            return
        question = self.offline.start_interview()
        if question is None:
            return
        self._last_onboarding_key = json.dumps(self.offline.pending, sort_keys=True)
        self._speak(self._background_text("onboarding_needed", question))

    def briefing_template(self) -> str:
        state = self.executor.execute("get_state").data
        blocks = [b for b in state["today_blocks"] if b["status"] == "pending"]
        hour = self.now().hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        if not blocks:
            lines = [f"{greeting}! Nothing is planned for today ({state['weekday']})."]
        else:
            total = sum(b["min"] for b in blocks)
            lines = [f"{greeting}! Today ({state['weekday']}): {len(blocks)} block(s), {total} min."]
            for b in blocks[:4]:
                lines.append(f"• {b['start']} {b['subject']} — {b['type'].lower()}, unit {b['unit']} ({b['min']} min)")
            if len(blocks) > 4:
                lines.append(f"• … and {len(blocks) - 4} more")
        priorities = self.executor.execute("explain_priority").data["priorities"]
        top = next((r for r in priorities if r.get("weight")), None)
        if top:
            lines.append(f"What matters most: {top['subject']} — {top['backlog_units']} unit(s) behind, "
                         f"{top['days_until_assessment']} day(s) to its next assessment.")
        if state["risks"]:
            r = state["risks"][0]
            lines.append(f"⚠ Risk: {r['assessment']} on {r['date']} is {r['shortfall_min']} min short — ask me "
                         f"\"am I on track?\" for options.")
        return "\n".join(lines)

    def _on_daily_briefing(self, event: Event) -> None:
        today = self._today_iso()
        with self._lock:
            if self.repo.get_setting("last_briefing_date") == today:
                return
            self.repo.set_setting("last_briefing_date", today)
        if not self._allowed(events.DAILY_BRIEFING):
            return
        self._speak(self._background_text("daily_briefing", self.briefing_template()))

    def _on_end_of_day(self, event: Event) -> None:
        today = self._today_iso()
        with self._lock:
            if self.repo.get_setting("last_checkin_date") == today:
                return
            self.repo.set_setting("last_checkin_date", today)
        if not self._allowed(events.END_OF_DAY):
            return
        state = self.executor.execute("get_state").data
        done = [b for b in state["today_blocks"] if b["status"] in ("done", "partial")]
        open_blocks = [b for b in state["today_blocks"] if b["status"] == "pending"]
        facts = {"blocks_done": len(done), "minutes_done": state["kpis"]["done_min_today"],
                 "blocks_open": len(open_blocks)}
        still = f", {len(open_blocks)} still open" if open_blocks else ""
        template = (f"End-of-day check-in: {len(done)} block(s) and {facts['minutes_done']} min done today{still}. "
                    f"Did you study anything I haven't logged? (e.g. \"I studied ML for 40 min\") "
                    f"And is tomorrow a normal day? (e.g. \"tomorrow I only have 1 hour\")")
        if event.payload.get("trigger") == "close":
            template += "\n(Close the window again to exit.)"
        options = [{"label": "All logged, normal day", "payload": {"type": "ack"}},
                   {"label": "Tomorrow only 1h", "payload": {"type": "say", "text": "tomorrow I only have 1 hour"}}]
        self._speak(self._background_text("end_of_day", template, facts=facts), options)

    def _on_reviews_due(self, event: Event) -> None:
        today = self._today_iso()
        if self.repo.get_setting("last_reviews_nudge_date") == today or not self._allowed(events.REVIEWS_DUE):
            return
        self.repo.set_setting("last_reviews_nudge_date", today)
        count = event.payload.get("count", 0)
        self._speak(f"{count} reviews are due today. They take a few minutes each and keep older units from "
                    f"fading — open Reviews and rate each one after recalling it.")

    def _on_break_started(self, event: Event) -> None:
        if not self._allowed(events.BREAK_STARTED):
            return
        tip = config.BREAK_TIPS[self._tip_index % len(config.BREAK_TIPS)]
        self._tip_index += 1
        self._speak(tip)
