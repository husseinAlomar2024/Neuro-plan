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
- Look and layout (2026-11-12): DPI awareness before Tk starts (`ui/dpi.py`), shared Segoe UI fonts (`ui/fonts.py`, chat 15 / UI 13), Text size Small/Medium/Large with Ctrl+Plus/Minus/0, three live themes as tokens in `ui/theme.py` (Midnight, Ivory & Gold, Mist; selector in Settings and a sidebar button), draggable dividers (coach panel, Subjects list) saved via `set_ui_prefs`, bubble text with looser line spacing (`widgets/chat_text.py`); `tests/test_theme.py`, `tests/test_gui_look.py`.

- Smooth dragging (2026-11-12): ghost-line divider drag (resize once on release), debounced re-wrap and pane fit after window resize / maximise; `tests/test_gui_look.py` (50 motion events → one resize, one re-wrap).
- Flicker-free layout (2026-11-12): snapshot cover during divider apply and maximise/restore (`ui/win32.py`), ghost line as its own popup, Escape/focus-loss cancel, chat scroll anchor, centre minimum width per text size, `tools/drag_check.py` and `tools/centre_min.py`; tests for scroll (D) and limits (F) in `tests/test_gui_look.py`. On-screen result: 0 broken frames for every drag (baseline 14–25), list never blank, scroll kept; maximise 1 / restore 2 distinct cover frames during the Windows animation; release-to-final 540–775 ms (target 300 ms not reached, see DECISIONS).
## Verification
- `.venv\Scripts\python -m pytest -q` → 391 passed (2026-11-12).
- `build_exe.bat` (2026-11-11, after the provider fix) → `dist\NeuroPlan.exe` (about 29 MB) installed to `%LOCALAPPDATA%\NeuroPlan\NeuroPlan.exe` while the app was running: the running exe was set aside as `NeuroPlan.exe.old`, the database and log were untouched, the shortcut was rewritten. The new exe has not been started yet (the open app is still the old build). The earlier build was smoke-tested from a scratch folder with a redirected `LOCALAPPDATA` (data folder created, old database copied once).
- Live diagnosis (2026-11-11, 6 requests, allowed for this once): with the installed app's settings and keyring key, Gemini's OpenAI-compatible endpoint answers every chat call, plain or with tools, with HTTP 403 `PERMISSION_DENIED` "The Gemini API free tier is not available for this project" (also for another model). The stored Base URL ended in `/models` (HTTP 404 before the clean-up). The fixed code reports exactly that reason.
- The app was launched from source on a temp DB, every view shown, an offline chat message answered.

## Next
- Nothing open. Optional ideas: drag-and-drop on the week board, per-unit titles in the Subjects view.

## Known issues
- No successful live chat round-trip has been seen yet: the only real endpoint tried (Gemini, see Verification) refuses the project with HTTP 403, so a tool call against a real model, and whether Gemini accepts the full tool list, are still unverified. Everything else about the providers is checked against a mock HTTP transport and a scripted FakeProvider.
- GUI tests were slow on 2026-11-11 (suite 128 s instead of about 40 s) and one full run was stopped after 400 s without output; the rerun passed.
- Creating many Tk windows in one test run occasionally fails inside Tk's own start-up on this machine ("couldn't read file …/ttk/entry.tcl", "invalid command name tcl_findLibrary" — Tcl library files that exist); a rerun passes. Not caused by app code. The GUI test fixtures retry the window creation for exactly these errors (`make_app` in `tests/test_gui_smoke.py`).
- GUI tests need a display; they are skipped automatically without one.

## Environment
- venv: `.venv` (Python 3.13). Tests: `.venv\Scripts\python -m pytest -q`. Run: `.venv\Scripts\python main.py`. Build: `build_exe.bat`.
