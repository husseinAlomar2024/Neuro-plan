# PROGRESS

## Current phase
P2 — scheduler + feasibility + tests (in progress)

## Done
- P1: `config.py`, `models.py`, `db/schema.sql`, `db/repository.py`, `seed.py`, `tests/test_repository.py` (12 tests green).

## Next
- P2 scheduler acceptance tests (`tests/test_scheduler.py`)
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
