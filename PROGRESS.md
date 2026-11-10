# PROGRESS

## Current phase
P4 — offline engine (in progress)

## Done
- P1: `config.py`, `models.py`, `db/schema.sql`, `db/repository.py`, `seed.py`, `tests/test_repository.py` (12 tests green).
- P2: `scheduler/engine.py`, `priority.py`, `feasibility.py`, `service.py` (build_state / regenerate), `tests/test_scheduler.py` (acceptance 1-11 except 8b; 38 tests green). See DECISIONS.md for scheduler choices.
- P3: `events.py`, `tools/registry.py` (schemas + validation, Anthropic/OpenAI export), `tools/executor.py` (transactional, snapshot undo, events, risk detection, plan diff), `tools/matching.py`; `tests/test_tools.py`, `tests/test_reviews.py` (150 tests green). Regeneration keeps ids of unchanged blocks.

## Next
- P4 offline engine
- P5 providers, LLM engine, coach, events
- P6 GUI + timer
- P7 proactive event wiring
- P8 polish, README, build_exe.bat, PyInstaller dry run

## Known issues
- none

## Environment
- venv: `.venv` (Python 3.13). Tests: `.venv\Scripts\python -m pytest -q`
