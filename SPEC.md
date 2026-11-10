# NeuroPlan — Build Specification (v2)

A Windows desktop study planner with an **always-on LLM study coach** that plans *with* the student, backed by a **deterministic scheduling engine** that does the math. Every decision in this file is final. Where something is not specified, choose the simplest option that satisfies the acceptance tests, record it in `DECISIONS.md`, and keep going.

---

## 0. Core architecture (read this first)

```
            ┌──────────────────────── GUI (CustomTkinter) ────────────────────────┐
            │ Today | Week | Subjects | Reviews | Settings      ║  Coach panel   │
            └───────────────┬─────────────────────────────────────╨───────┬───────┘
                            │ user actions                                │ chat + proactive msgs
                            ▼                                             ▼
                    ┌──────────────┐   tool calls (same schema)   ┌──────────────────┐
                    │ ToolExecutor │◄─────────────────────────────│ Coach            │
                    │ (validated,  │                              │  LLMEngine       │
                    │  undoable)   │                              │  OfflineEngine   │
                    └──────┬───────┘                              └────────▲─────────┘
                           ▼                                               │ events
                    ┌──────────────┐     plan + risk report     ┌──────────┴───────┐
                    │ Repository   │◄──────────────────────────►│ Scheduler        │
                    │ (SQLite)     │                            │ (pure functions) │
                    └──────────────┘                            └──────────────────┘
                           │ every state change emits an event ──► EventBus ──► Coach
```

Non-negotiable rules:
1. **The LLM never computes the schedule and never writes to the DB directly.** It calls tools. The `Scheduler` is deterministic and pure (input state + date → plan + risk report). The LLM's job: understand the student, gather missing info, choose/adjust inputs, explain results, negotiate trade-offs, react to events.
2. **GUI buttons and the chat go through the same `ToolExecutor`.** One code path for every state change. Every executed tool writes to `action_log` and is undoable.
3. **The coach is live.** It reacts to events (Section 6) without being asked. Offline mode keeps the app fully usable with no API key.
4. **The UI never blocks.** All LLM/network calls run on a worker thread; results are marshalled to the UI with `widget.after(...)`.

---

## 1. Stack & layout

- Python 3.11+, `customtkinter`, `sqlite3`, `anthropic`, `openai` (for OpenAI-compatible endpoints: OpenAI, DeepSeek, Ollama), `keyring`, `python-dateutil`. Dev: `pytest`, `pyinstaller`. Nothing else.
- Timezone: use local system time. Default week display starts **Sunday** (setting `week_start`: `sun`|`mon`).

```
neuroplan/
  main.py                      # entry: python main.py
  neuroplan/
    __init__.py
    config.py                  # constants (Section 4 numbers live here, nowhere else)
    db/schema.sql
    db/repository.py           # all SQL; returns dataclasses
    models.py                  # dataclasses
    scheduler/engine.py        # generate_plan(state, start_date, days) -> PlanResult
    scheduler/priority.py
    scheduler/feasibility.py
    tools/registry.py          # tool definitions (JSON schema) + validators
    tools/executor.py          # execute(tool_name, args, source) -> ToolResult; undo
    coach/coach.py             # orchestrates engines, history, proactive events
    coach/llm_engine.py        # agent loop with tool calling
    coach/providers.py         # AnthropicProvider, OpenAICompatProvider, FakeProvider
    coach/offline_engine.py    # rule-based parser -> tool calls
    coach/prompts.py           # system prompt + state snapshot builder
    events.py                  # simple in-process pub/sub
    timer.py                   # pomodoro state machine (no UI code)
    ui/app.py, ui/theme.py, ui/views/{today,week,subjects,reviews,settings}.py, ui/coach_panel.py, ui/widgets/*.py
    seed.py
  tests/  (pytest)
  requirements.txt  requirements-dev.txt  build_exe.bat  README.md  DECISIONS.md  PROGRESS.md
```

---

## 2. Data model (SQLite, `neuroplan.db` next to the exe / in project dir)

Create from `schema.sql` on startup; track `schema_version` in `app_settings` and run forward migrations.

| Table | Columns |
|---|---|
| `subjects` | id PK, name UNIQUE, category (`HEAVY` = math/theory, `CODING` = practical, `MEMORY` = language/reading), difficulty 1–5, mastery 1–5, minutes_per_unit INT, expected_units_by_now INT, color_hex, active INT default 1, created_at |
| `units` | id PK, subject_id FK, number INT, title TEXT, minutes_logged INT default 0, status (`todo`,`in_progress`,`done`), done_date. UNIQUE(subject_id, number) |
| `assessments` | id PK, subject_id FK, kind (`quiz`,`midterm`,`final`,`assignment`,`project`), title, date, weight_pct INT, unit_from INT, unit_to INT |
| `availability` | weekday PK (0=Mon … 6=Sun, Python `weekday()`), minutes INT, start_time `HH:MM`, peak_window (`morning`,`afternoon`,`night`) |
| `availability_override` | date PK, minutes INT, start_time NULL |
| `study_blocks` | id PK, plan_date, start_time, subject_id NULL (NULL only for review blocks of multiple items—not used; always set), unit_number, block_type (`CATCHUP`,`PROGRESS`,`REVIEW`,`PRACTICE`), pomodoro (`50/10`,`25/5`), duration_min, task_text, status (`pending`,`done`,`partial`,`skipped`), actual_min INT, locked INT default 0, order_index, generated_at |
| `reviews` | id PK, subject_id, unit_number, stage_index INT, due_date, last_rating NULL, done INT default 0 |
| `chat_messages` | id PK, ts, role (`user`,`coach`,`system_event`), text, actions_json, proactive INT |
| `action_log` | id PK, ts, source (`ui`,`chat_llm`,`chat_offline`,`system`), tool, args_json, inverse_json, undone INT |
| `app_settings` | key PK, value |

Settings keys: `provider` (`anthropic`|`openai_compat`|`offline`), `base_url`, `chat_model`, `background_model`, `proactive_level` (`off`|`light`|`full`, default `full`), `daily_llm_call_cap` (default 60), `end_of_day_time` (default `22:00`), `theme`, `week_start`, `plan_horizon_days` (default 14), `conversation_summary`, `last_briefing_date`, `schema_version`.
**API keys are NOT stored in SQLite.** Use `keyring` (service `NeuroPlan`); fallback to env vars `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`.

**Backlog definition:** `backlog_units = max(0, expected_units_by_now − done_units)` where `done_units` = count of units with status `done`. `expected_units_by_now` is entered by the student (the coach asks for it). Units rows are auto-created 1..N when `total_units` is set via a tool.

---

## 3. Seed data (first launch only, and via "Reset to demo data")

Dates are relative to today.

| Subject | Cat | Diff | Mastery | Units | Expected | Done | min/unit | Assessment |
|---|---|---|---|---|---|---|---|---|
| Deep Learning | HEAVY | 5 | 2 | 12 | 7 | 3 | 75 | midterm in 10 days (units 1–6, 30%) |
| Machine Learning | HEAVY | 4 | 3 | 12 | 6 | 4 | 60 | quiz in 5 days (units 3–5, 10%), midterm in 14 days (1–7, 30%) |
| Internet Programming | CODING | 3 | 4 | 10 | 5 | 5 | 50 | project due in 18 days (1–8, 25%) |
| IELTS Prep | MEMORY | 3 | 3 | 30 | 8 | 6 | 45 | exam in 30 days (1–30, 100%) |

Availability: Sun–Thu 180 min starting 18:00, Fri–Sat 300 min starting 10:00, peak `night` on weekdays, `morning` on Fri/Sat. Seed done units get reviews at the stage matching their age (assume done 1 unit/2 days backwards). Generate the plan after seeding. Seed one coach welcome message.

---

## 4. Scheduler (pure, deterministic, fully unit-tested)

`generate_plan(state: State, start: date, days: int) -> PlanResult(blocks, reviews_projected, risks, explanations)`

All constants live in `config.py`:
```
REVIEW_STAGES_DAYS = [1, 3, 7, 16, 35]
REVIEW_BLOCK_MIN = 15          # one review block may cover up to 2 units of the same subject
REVIEW_CAP_SHARE = 0.20
DAILY_CAP_SHARE = 0.60         # max share of a day for one subject
DAILY_CAP_SHARE_EXAM = 0.85    # when that subject has an assessment within EXAM_CLOSE_DAYS
EXAM_CLOSE_DAYS = 3
QUANTUM = {HEAVY: 50, CODING: 50, MEMORY: 25}
POMODORO = {HEAVY: "50/10", CODING: "50/10", MEMORY: "25/5", REVIEW: "25/5"}
BREAK_MIN = {"50/10": 10, "25/5": 5}
MAX_CONSECUTIVE_HEAVY_MIN = 100
NO_ASSESSMENT_DAYS = 60
```

### 4.1 Per-day loop (for d in horizon; state is *simulated forward* so later days continue where earlier days stop)
1. **Capacity** `T_d` = override minutes if present, else weekday minutes. Breaks count inside `T_d` (a 50/10 block consumes 60 min of the day; the last block of the day has no trailing break).
2. **Reviews first**: collect reviews due ≤ d (real + projected), oldest first. Use up to `REVIEW_CAP_SHARE × T_d`. Overflow rolls to the next day (never dropped).
3. **Priority** for each active subject with remaining work, evaluated on day d:
   - `a` = nearest upcoming assessment (by date ≥ d); `days_until = max(1, (a.date − d).days)` or `NO_ASSESSMENT_DAYS` if none
   - `urgency = 1 + 14 / days_until`
   - `backlog_pressure = 1 + 0.45 × backlog_units_simulated`
   - `difficulty_factor = 1 + 0.15 × (difficulty − 3)`
   - `mastery_deficit = 1 + 0.12 × (5 − mastery)`
   - `exam_scope_boost = 1 + 0.5 × (undone units inside a's unit range) / max(1, range size)`, only when days_until ≤ 14, else 1
   - `W = urgency × backlog_pressure × difficulty_factor × mastery_deficit × exam_scope_boost`
4. **Allocation with cap (water-filling):** share remaining minutes ∝ W; clamp each subject to its cap; redistribute the excess to uncapped subjects; repeat until stable. If every subject is capped, the leftover is unscheduled and reported as `free_minutes`.
5. **Quantize with carry (deficit round-robin):** each subject has a `carry` (minutes, persists across days in the simulation). `budget = allocation + carry`. Emit `floor(budget / (quantum+break))` blocks; new `carry = remainder`. This guarantees low-priority subjects still appear every few days instead of vanishing. Fill any leftover day time with one extra block for the highest-W subject that fits (respecting the cap); otherwise leave it free.
6. **Block content:** each block targets the subject's next not-done unit (simulate minutes_logged so a 75-min unit spans blocks). Type: `CATCHUP` if the unit index ≤ expected_units_by_now, else `PROGRESS`; `PRACTICE` when all units in the upcoming assessment's range are done (practice past questions for that assessment). Task text by mastery:
   - 1–2: `Unit {n}: Concept acquisition — study actively, then write a 5-bullet Feynman summary from memory.`
   - 3: `Unit {n}: Guided application — solve 5 problems / build the example without looking at solutions.`
   - 4–5: `Unit {n}: Timed active recall — practice questions under time limit, then check mistakes.`
   - REVIEW: `Review {subject} unit {n}: close notes, recall everything you can in 5 min, then check and rate yourself.`
7. **Ordering:** (a) one review block first as warm-up, if any; (b) the highest-difficulty HEAVY block next if the session start time falls inside the day's peak window, otherwise the highest-W block; (c) then greedily choose the next block so the category differs from the previous one whenever an alternative exists, and consecutive HEAVY minutes never exceed `MAX_CONSECUTIVE_HEAVY_MIN`; (d) remaining reviews last. Compute `start_time` for every block from the session start + durations + breaks.
8. **Locked blocks** (`locked=1`) and `done`/`partial` blocks are kept as-is and their minutes are subtracted from `T_d` before allocation.

### 4.2 Feasibility & risk report
For each assessment: `needed_min` = remaining minutes of undone units in its range; `available_min` = the planned minutes for that subject before the assessment date. If `needed_min > available_min` → risk `{assessment, shortfall_min, suggestion_options}` where options are computed numerically: (1) extra minutes/day needed until the date, (2) a priority order for the remaining units: units most heavily covered by the assessment first, so the most important material is covered before time runs out. Never phrase this as "skip" or "drop"; call it "prioritize", (3) raising the cap to 0.85 for that subject and what that costs other subjects. The coach must present these options; it never silently ignores a risk.

### 4.3 Explanations
`explanations[subject_id]` = the W factor breakdown and allocated minutes per day, so the coach and the "Why this order?" button can quote real numbers.

### 4.4 Regeneration
`regenerate()` = delete `pending` & unlocked blocks with plan_date ≥ today, then `generate_plan(today, horizon)`. Called automatically after any tool that changes subjects, units, assessments, availability, or block status. Must run < 200 ms for 6 subjects × 14 days.

### 4.5 Completion & spaced repetition
- `complete_block(id, actual_min)` → adds actual_min to the unit's minutes_logged (overflow flows to the next unit); when minutes_logged ≥ minutes_per_unit the unit becomes `done` with done_date = today and a review is created at stage 0 (due today+1).
- `complete_review(id, rating)` ratings `again|hard|good|easy`: again → stage 0, due +1; hard → same stage, due +ceil(interval/2); good → next stage; easy → skip one stage. After the last stage → done.
- Skipped blocks just become `skipped`; the regeneration redistributes the work.

---

## 5. Tools (single schema for GUI, LLM, and offline parser)

Defined once in `tools/registry.py` as JSON Schema (exported in both Anthropic and OpenAI tool formats). The executor validates args, runs inside a DB transaction, writes `action_log` with an inverse for undo, emits events, triggers regeneration when needed, and returns `{ok, summary, data}`. `summary` is a short human string shown in the "Actions executed" badge.

Read tools: `get_state` (compact snapshot), `get_plan(date_from, date_to)`, `explain_priority(subject?)`, `get_risks`.
Write tools: `upsert_subject`, `delete_subject` (**requires_confirmation**), `set_progress(subject, expected_units_by_now?, done_units?)`, `log_study(subject, minutes | units)`, `add_assessment`, `update_assessment`, `delete_assessment`, `set_weekly_availability(weekday→minutes/start/peak)`, `set_day_override(date, minutes, start?)`, `complete_block`, `skip_block`, `lock_block`, `move_block(id, new_date)`, `complete_review(id, rating)`, `regenerate_plan`, `undo_last`.
Tools flagged `requires_confirmation` return `{needs_confirmation, preview}` instead of running; the UI shows a Confirm/Cancel card in the chat, and the confirm button calls the tool again with `confirmed=true`.

---

## 6. The live coach

### 6.1 Engines
- **LLMEngine**: agent loop — send system prompt + state snapshot + rolling summary + last 12 messages + tools; execute returned tool calls through the executor; feed results back; repeat up to 6 rounds; then show the final text plus the action badges. On any network/auth/rate error: show a one-line notice and route the same message through OfflineEngine.
- **Providers**: `AnthropicProvider` (Messages API with tools, default `chat_model=claude-sonnet-5-5`, `background_model=claude-haiku-5-5`), `OpenAICompatProvider` (base_url configurable; works for OpenAI, DeepSeek, Ollama `http://localhost:11434/v1`), `FakeProvider` (scripted responses for tests). Timeouts 30 s. Model names are settings, never hard-coded elsewhere.
- **OfflineEngine**: regex/keyword intent parser that emits the same tool calls, then responds with a template message. Must handle at least the utterances in tests/test_offline_parser.py (Section 9). Unknown input → helpful reply listing example commands.

### 6.2 System prompt (in `coach/prompts.py`; keep it under ~900 tokens)
Coach persona: concise, warm, direct study coach for a university student; replies ≤ 120 words unless asked; always grounded in tool results; never invents numbers — calls `explain_priority`/`get_plan` when quoting figures; when information needed for a good plan is missing (exam dates, units, expected progress, availability) it asks for **one thing at a time**, most impactful first; when a risk exists it presents the computed options and lets the student choose; it confirms what it changed; it may suggest but must not lecture. The student may write in English or Arabic or a mix — reply in the student's language.

State snapshot (JSON, compact, ≤ 1,500 tokens): today's date & weekday, subjects with backlog/remaining/next assessment, availability, today's blocks with status, due reviews count, active risks, streak.

### 6.3 Proactive events (this is what makes it "live")
`EventBus` events → `Coach.on_event` decides whether to speak. Use `background_model` for these. Respect `proactive_level` and `daily_llm_call_cap` (offline templates are used when the cap is reached or provider is offline).

| Event | When | Coach behaviour |
|---|---|---|
| `onboarding_needed` | no subjects, or a subject lacks units/expected/assessment | Starts an intake interview, one question at a time, filling data via tools until the plan is complete. |
| `daily_briefing` | first app open of the day | Today's plan in 3–5 lines, the one thing that matters most, any risk. |
| `plan_changed` | after a regeneration caused by a state change | 1–2 sentences: what moved and why (diff old vs new plan computed in code, passed in). Debounce 3 s. |
| `block_finished` | student confirms Done/Partial on the timer card, or marks a block done | No LLM call per block; local one-liner. After the **last** block of the day → short session recap (LLM). |
| `block_skipped` / `missed_yesterday` | skip pressed / pending blocks from yesterday found at startup | Asks what happened (offers quick chips: "No time", "Too hard", "Didn't feel like it", "Sick"); "Too hard" lowers mastery by 1 for that subject via tool; then regenerates and says what moved. |
| `risk_detected` | new risk in the risk report | Presents the computed options as buttons; executes the choice. |
| `end_of_day` | at `end_of_day_time` if app open, or on close if no check-in today | 30-second check-in: what got done, log anything not logged, set tomorrow's override if needed. |
| `reviews_due` | ≥ 3 reviews due today | Nudge. |
| `break_started` | pomodoro break begins | Optional one-line local tip (no LLM). |

Proactive messages appear in the coach panel with a small "coach" tag; a badge on the panel toggle shows unread count when collapsed.

### 6.4 History management
Store all messages. Send last 12 to the LLM; when more than 30 unsummarised exist, ask `background_model` to update `conversation_summary` (≤ 200 words) and mark them summarised.

---

## 7. GUI (CustomTkinter, 1280×800 default, min 1024×700, Dark default)

Left sidebar nav · centre view · right **Coach panel** (always visible, collapsible to an icon with unread badge, width ~380 px).

1. **Today** — KPI cards (backlog units total, planned vs done minutes today, streak days, reviews due) · timeline of today's blocks (time, subject color chip, category, pomodoro, task text, buttons: Start, Done, Skip, Lock) · Pomodoro widget (focus/break auto-switch, pause/reset). **Never auto-log.** When a block's focus time ends, show a confirmation card: **Done** → `complete_block(actual_min)`; **Partial** → ask minutes actually studied (prefilled with elapsed focus minutes) → `complete_block` with status `partial`; **Didn't really study** → `skip_block`. Only after the answer is the block logged and the plan regenerated.
2. **Week** — availability editor per weekday (minutes spinbox, start time, peak window) + "Today only" override · 7-column board (respect `week_start`) with block chips colored per subject and labelled by type · risk banner at top if any · "Why this order?" button → asks the coach.
3. **Subjects** — table + edit form (all subject fields, total units, expected, done) · assessments sub-table per subject · backlog bar (green 0, amber 1–2, red ≥3).
4. **Reviews** — due today / upcoming list with Again/Hard/Good/Easy buttons.
5. **Settings** — provider, base URL, chat/background model, API key (masked, saved to keyring, "Test connection" button), proactive level, daily call cap, end-of-day time, theme, week start, horizon, "Reset to demo data" (confirm), "Export data (JSON)" / "Import".

Coach panel: message bubbles (user / coach / system-event styles), "Actions executed" badges with an **Undo** link on the latest action, confirm cards, option buttons for risks/skips, quick chips ("What now?", "Only 2h today", "I'm behind in…", "Rebalance week", "Why this order?"), input box (Enter sends, Shift+Enter newline), typing indicator while the worker thread runs. All views refresh on the `state_changed` event.

---

## 8. Error handling & robustness
- Every DB write in a transaction; on exception roll back and show the error in the coach panel as a system message.
- Missing/invalid API key → coach runs offline and says so once.
- App must start with an empty, corrupted (rename to `.bak` and recreate after asking), or old-schema DB.
- Logging to `neuroplan.log` (rotating, 1 MB × 3). Never log API keys.

---

## 9. Acceptance tests (must all pass: `pytest -q`)

**Scheduler** (`tests/test_scheduler.py`)
1. Seed data, 14 days: no subject exceeds 60% of any day unless it has an assessment within 3 days (then ≤ 85%).
2. Every day's total scheduled minutes (incl. breaks) ≤ capacity.
3. Internet Programming (on track, low W) appears at least once in every 3-day window.
4. No more than 100 consecutive HEAVY minutes in any day when a non-HEAVY block exists that day.
5. Reviews ≤ 20% of each day; overflow appears on later days, none lost.
6. Unit numbers for a subject are non-decreasing across the horizon; a 75-min unit spans two blocks correctly.
7. Deterministic: same input → identical output.
8. Feasibility: set Deep Learning midterm to 2 days away with 3 h/day → a risk with positive shortfall and three options is reported.
8b. Pomodoro (`tests/test_timer.py`): when the timer's focus time ends, nothing is logged until the student confirms; Done, Partial (custom minutes), and Didn't-study each produce the correct tool call.
9. Override "today 90 min" → today's plan totals ≤ 90 and the regenerated week still covers backlog.
10. Locked and done blocks survive regeneration unchanged.
11. Performance: 6 subjects × 14 days < 200 ms.

**Spaced repetition** (`tests/test_reviews.py`): completion creates stage-0 review due +1; good/again/hard/easy transitions as specified.

**Tools** (`tests/test_tools.py`): each write tool validates bad input with a clear error; undo restores the exact previous state for upsert_subject, set_progress, set_day_override, complete_block; `delete_subject` requires confirmation.

**Offline parser** (`tests/test_offline_parser.py`) — each maps to the expected tool calls:
- "I have 3 subjects: Deep Learning, Machine Learning and Internet Programming"
- "I'm 4 lectures behind in deep learning and 2 behind in ML"  (alias: ML → Machine Learning by fuzzy/acronym match)
- "I have 3 hours a day on weekdays and 6 hours on friday and saturday"
- "today I only have 90 minutes" / "I only have 1.5h today"
- "I finished 2 units of deep learning today"
- "I studied machine learning for 40 min"
- "my ML midterm is on 2026-11-24" and "deep learning exam is next thursday"
- "I couldn't study today, rebalance" 
- "what should I study now?" → get_plan for today
- "undo"
- "عندي امتحان ديب ليرنينغ بعد ٥ أيام" (Arabic digits + "بعد N أيام" → add_assessment in 5 days; subject matched by transliteration alias list in config)

**Coach with FakeProvider** (`tests/test_coach.py`): a scripted multi-round tool-calling conversation executes tools in order and returns final text with action summaries; network error falls back to offline; daily cap switches proactive messages to templates; `daily_briefing` fires once per day only; `plan_changed` is debounced.

**GUI smoke** (`tests/test_gui_smoke.py`, skipped automatically when no display): build the app with a temp DB, switch to every view, send one offline chat message, destroy.

---

## 10. Build phases (commit after each; update PROGRESS.md)
P1 schema, repository, models, seed + tests → P2 scheduler + feasibility + tests → P3 tool registry/executor/undo + tests → P4 offline engine + tests → P5 providers, LLM engine, coach, events + FakeProvider tests → P6 GUI views + coach panel + timer → P7 proactive events wiring → P8 polish, README, build_exe.bat, full test run, `pyinstaller` dry run.

## 11. Deliverables
Runnable `python main.py`; all tests green; `README.md` with setup (venv, `pip install -r requirements.txt`, set API key in Settings), run, test, and build commands; `build_exe.bat` producing `dist/NeuroPlan.exe` (`--noconsole --onefile`, include customtkinter data files); `DECISIONS.md` listing every choice not covered by this spec.
