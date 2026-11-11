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

## Verification
- `.venv\Scripts\python -m pytest -q` → 291 passed (after the post-P8 fixes; the exe below was built before them and has not been rebuilt).
- `build_exe.bat` → `dist\NeuroPlan.exe` (about 29 MB), built from the final code. The exe was started from a scratch folder: it stayed up, created `neuroplan.db` with the seed data and plan, the coach posted the welcome, the daily briefing and the review nudge, and the log stayed empty.
- The app was launched from source on a temp DB, every view shown, an offline chat message answered.

## Next
- Nothing open. Optional ideas: app icon, drag-and-drop on the week board, per-unit titles in the Subjects view.

## Known issues
- The LLM providers are verified against a mock HTTP transport and a scripted FakeProvider only; no live API call was made during the build (by design: no network, no keys).
- GUI tests need a display; they are skipped automatically without one.

## Environment
- venv: `.venv` (Python 3.13). Tests: `.venv\Scripts\python -m pytest -q`. Run: `.venv\Scripts\python main.py`. Build: `build_exe.bat`.
