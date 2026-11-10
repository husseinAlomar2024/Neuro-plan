# NeuroPlan

A Windows desktop study planner with an always-on study coach. A deterministic scheduling engine does the
math (what to study, when, for how long); the coach plans *with* you in a chat panel, reacts to what you do,
and changes the plan only through the same validated, undoable tools the buttons use. It works fully offline;
with an API key the coach becomes an LLM agent.

## Setup

Requires Python 3.11 or newer on Windows.

```bat
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
```

(`requirements.txt` alone is enough to run the app; `requirements-dev.txt` adds pytest and PyInstaller.)

## Run

```bat
.venv\Scripts\python main.py
```

The first launch creates `neuroplan.db` in the project folder with a demo semester (four subjects, their
assessments, a 14-day plan) and the coach greets you with today's briefing. Everything works without a key:
the coach then runs in offline mode and understands commands such as

- `what should I study now?`
- `today I only have 90 minutes`
- `I'm 2 lectures behind in ML`
- `I finished 2 units of Deep Learning` / `I studied ML for 40 min`
- `my ML midterm is on 2026-11-24` / `deep learning exam is next thursday`
- `I have 3 hours a day on weekdays and 6 hours on friday and saturday`
- `I couldn't study today, rebalance` · `why this order?` · `am I on track?` · `undo`

### Turning on the AI coach

Open **Settings**:

1. Choose a provider: `anthropic` (Claude) or `openai_compat` (OpenAI, DeepSeek, Ollama — set the base URL,
   e.g. `http://localhost:11434/v1`, and the model names).
2. Paste the API key and press **Save key**. The key is stored in the Windows Credential Manager through
   `keyring` (service `NeuroPlan`), never in the database or the log. Alternatively set the environment
   variable `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` before starting the app.
3. Press **Test connection**.

Default models are `claude-sonnet-5-5` for chat and `claude-haiku-5-5` for proactive background messages;
both are plain settings. If a call fails (network, key, rate limit, daily call cap) the coach says so in one
line and answers the same message in offline mode.

## Using it

- **Today** — KPI cards, today's blocks, and the pomodoro. *Start* runs the focus timer; when it ends you
  choose **Done**, **Partial** (minutes actually studied) or **Didn't really study**. Nothing is logged
  before you answer.
- **Week** — weekly availability, a "today only" override, the 7-day board, a risk banner when an assessment
  cannot be fully prepared, and *Why this order?*.
- **Subjects** — subjects, units, expected vs. done progress (backlog bar), assessments.
- **Reviews** — spaced-repetition reviews (1, 3, 7, 16, 35 days) with Again / Hard / Good / Easy.
- **Settings** — provider, models, key, proactive level, daily AI call cap, end-of-day time, theme,
  week start, plan horizon, export / import (JSON), reset to demo data.
- **Coach panel** (right) — chat, proactive messages, "Actions executed" badges with **Undo**, confirm cards
  for destructive actions, option buttons for risks and skipped blocks. It collapses to an icon with an
  unread badge.

## Test

```bat
.venv\Scripts\python -m pytest -q
```

The suite uses temporary databases and a fixed date. No test calls a real LLM API: the coach is driven by a
scripted `FakeProvider`, and the two real providers are checked against an in-process mock HTTP transport.
The GUI smoke tests are skipped automatically when no display is available.

## Build the exe

```bat
build_exe.bat
```

Produces `dist\NeuroPlan.exe` (PyInstaller, `--noconsole --onefile`, CustomTkinter data files and the SQL
schema bundled). The exe keeps `neuroplan.db` and `neuroplan.log` next to itself.

## How it is put together

```
GUI (CustomTkinter)  ──user actions──►  ToolExecutor  ◄──tool calls──  Coach (LLMEngine | OfflineEngine)
                                            │  validated, transactional, logged, undoable
                                            ▼
                                        Repository (SQLite)  ◄──►  Scheduler (pure functions)
                                            │
                                            └─ every change emits an event ─► EventBus ─► Coach / views
```

| Path | What it holds |
|---|---|
| `neuroplan/config.py` | every constant (scheduler numbers from SPEC §4, defaults, parser vocabulary) |
| `neuroplan/db/` | `schema.sql`, `repository.py` (all SQL, returns dataclasses) |
| `neuroplan/scheduler/` | `engine.py` (`generate_plan`), `priority.py`, `feasibility.py` (risk report), `service.py` (regenerate) |
| `neuroplan/tools/` | `registry.py` (JSON-Schema tool definitions, Anthropic + OpenAI formats), `executor.py` (execution, undo, events) |
| `neuroplan/coach/` | `coach.py`, `llm_engine.py`, `offline_engine.py`, `providers.py`, `prompts.py` |
| `neuroplan/timer.py` | pomodoro state machine (no UI code) |
| `neuroplan/ui/` | app shell, theme, views, coach panel, widgets |
| `tests/` | acceptance tests from SPEC §9 plus extras |

Design rules that are enforced in code and tests:

- The LLM never computes the schedule and never writes to the database; it only calls tools.
- Buttons and chat share one code path (`ToolExecutor.execute`); every write is logged in `action_log`
  with a snapshot, so **Undo** restores the exact previous state.
- The UI never blocks: LLM calls run on a worker thread and results reach the widgets through `after()`.
- A corrupted database is renamed to `neuroplan.db.bak` (after asking) and recreated; older schemas are
  migrated forward on startup.

See `DECISIONS.md` for every choice that SPEC.md left open, and `PROGRESS.md` for the build log.
