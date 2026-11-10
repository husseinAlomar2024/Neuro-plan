# Working rules for this project

You own this codebase end to end. Build NeuroPlan exactly as specified in `SPEC.md`.

## Autonomy
- You are running with permissions bypassed and nobody is watching. **Do not ask questions.** Every product decision is already made in SPEC.md. If something is truly unspecified, pick the simplest option that passes the acceptance tests, write one line in `DECISIONS.md`, and continue.
- Do not end your turn while any phase in SPEC.md §10 is unfinished, any test fails, or any `TODO`, `pass` placeholder, or stub remains. Do not stop to summarize remaining work or offer to continue later. Keep going.
- If your context gets long, keep `PROGRESS.md` current (phase, what's done, what's next, known issues) so work can resume from it alone.

## Safety boundaries (important because permissions are bypassed)
- Read and write only inside this project folder. Never touch anything outside it, the registry, system settings, or global Python.
- Use a local virtual environment with any installed Python 3.11 or newer: run `py --list`, use py -3.13 -m venv .venv, then `.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt`. Install nothing beyond the packages listed in SPEC.md §1.
- No `git push`, no remote repos, no network calls except pip installs. Tests must never call real LLM APIs (use FakeProvider).
- Never delete `neuroplan.db` belonging to the user. Tests use temp DBs.

## Token discipline
- Read SPEC.md once at the start; re-open only the section you need after that.
- Don't paste file contents or long diffs into chat. Make targeted edits, not full-file rewrites, once a file exists.
- Use tests as your verification loop: `.venv\Scripts\python -m pytest -q`. Don't take screenshots of the GUI. Use the GUI smoke test.
- Keep your messages between tool calls minimal.

## Workflow per phase
1. Implement the phase.
2. Write or extend its tests from SPEC.md §9.
3. Run the full test suite until it's green.
4. `git add -A && git commit -m "P<n>: <summary>"` (run `git init` first if needed).
5. Update PROGRESS.md.

## Code standards
- Type hints everywhere, dataclasses for models, no global mutable state except the app container.
- Every constant from SPEC.md §4 lives in `config.py`.
- UI code never runs SQL and never calls providers directly. It goes through the ToolExecutor and the Coach.
- Background work on threads; UI updates only via `after()`.

## Done means
`python main.py` launches with seed data and a populated plan, the coach greets the user and works offline, all tests pass, `build_exe.bat` exists and the PyInstaller build succeeds, and README.md explains setup, run, test, and build.
