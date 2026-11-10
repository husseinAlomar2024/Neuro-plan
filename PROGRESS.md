# PROGRESS

## Current phase
P3 — tool registry / executor / undo (in progress)

## Done
- P1: `config.py`, `models.py`, `db/schema.sql`, `db/repository.py`, `seed.py`, `tests/test_repository.py` (12 tests green).
- P2: `scheduler/engine.py`, `priority.py`, `feasibility.py`, `service.py` (build_state / regenerate), `tests/test_scheduler.py` (acceptance 1-11 except 8b; 38 tests green). See DECISIONS.md for scheduler choices.

## Next
- P3 tool registry / executor / undo
- P4 offline engine
- P5 providers, LLM engine, coach, events
- P6 GUI + timer
- P7 proactive event wiring
- P8 polish, README, build_exe.bat, PyInstaller dry run

## Known issues
- none

## Environment
- venv: `.venv` (Python 3.13). Tests: `.venv\Scripts\python -m pytest -q`
