# PROGRESS

## Current phase
All phases of SPEC.md §10 (P1–P8) are complete.

## Done
- P1: `config.py`, `models.py`, `db/schema.sql`, `db/repository.py`, `seed.py`, `tests/test_repository.py`.
- P2: `scheduler/engine.py`, `priority.py`, `feasibility.py`, `service.py` (build_state / regenerate), `tests/test_scheduler.py` (acceptance 1–11 except 8b).
- P3: `events.py`, `tools/registry.py`, `tools/executor.py` (transactional, snapshot undo, events, risk detection, plan diff), `tools/matching.py`; `tests/test_tools.py`, `tests/test_reviews.py`.
- P4: `coach/offline_engine.py` (intent parser, templates, offline onboarding interview), `coach/types.py`; `tests/test_offline_parser.py`.
- P5: `coach/providers.py` (Anthropic, OpenAI-compatible, Fake; keyring keys), `coach/prompts.py`, `coach/llm_engine.py`, `coach/coach.py`; `tests/test_coach.py`, `tests/test_providers_http.py` (mock HTTP transport, no network).
- P6: `timer.py`, `ui/` (theme, widgets, five views, coach panel, app shell), `main.py`; `tests/test_timer.py` (acceptance 8b), `tests/test_gui_smoke.py`.
- P7: proactive wiring: start-up sequence (missed blocks, re-plan, onboarding or briefing, review nudge), plan-change debounce, block finished/skipped, risk options, end-of-day (clock + close), break tips, onboarding after UI edits.
- P8: `README.md`, `DECISIONS.md`, `build_exe.bat` (PyInstaller onefile/noconsole), bare `pass` clean-up, plain exported tool schemas, undo skips housekeeping actions.

- Post-P8 fixes (2026-11-11): archive/restore/delete buttons and "Show archived" in the Subjects view, "archive X" in the offline parser; multi-intent messages (clause splitting) in the offline parser; chat bubbles wrap to the panel width and re-wrap on resize.
- Install (2026-11-11): exe data in `%LOCALAPPDATA%\NeuroPlan` with one-time copy of an old database next to the exe, app icon (`assets\neuroplan.ico`), `build_exe.bat` installs the exe and creates the Desktop shortcut; `tests/test_paths.py`.
- Live provider fix (2026-11-11): provider errors carry the HTTP status and the service's message and are classified (rejected key / access denied / wrong Base URL / unknown model / rate limit or quota / rejected request / no connection); Test connection, chat notice and log show that reason; Base URL, model names and key are cleaned on save and on use; `build_exe.bat` can install while the app is running; `tests/test_provider_errors.py`.

## Verification
- `.venv\Scripts\python -m pytest -q` → 359 passed.
- `build_exe.bat` (2026-11-11, current code) → `dist\NeuroPlan.exe` (about 29 MB), copied to `%LOCALAPPDATA%\NeuroPlan\NeuroPlan.exe`, shortcut `Desktop\NeuroPlan.lnk` created. The exe was started from a scratch folder with `LOCALAPPDATA` pointing at a scratch folder and an old `neuroplan.db` beside it: it stayed up, created `NeuroPlan\neuroplan.db` and `neuroplan.log` there, the old subjects were present, the old file was left in place, and the coach posted its start-up messages. The installed copy itself has not been started, so the real data folder holds only the exe until the first launch.
- The app was launched from source on a temp DB, every view shown, an offline chat message answered.

## Next
- Nothing open. Optional ideas: app icon, drag-and-drop on the week board, per-unit titles in the Subjects view.

## Known issues
- The LLM providers are verified against a mock HTTP transport and a scripted FakeProvider only; no live API call was made during the build (by design: no network, no keys).
- GUI tests need a display; they are skipped automatically without one.

## Environment
- venv: `.venv` (Python 3.13). Tests: `.venv\Scripts\python -m pytest -q`. Run: `.venv\Scripts\python main.py`. Build: `build_exe.bat`.
