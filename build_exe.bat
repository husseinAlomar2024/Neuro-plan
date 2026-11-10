@echo off
rem Build dist\NeuroPlan.exe (single file, no console window).
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.13 -m venv .venv || py -3 -m venv .venv || goto :fail
    .venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt || goto :fail
)

.venv\Scripts\python -m PyInstaller --noconfirm --clean --noconsole --onefile --name NeuroPlan ^
    --collect-data customtkinter ^
    --add-data "neuroplan\db\schema.sql;neuroplan\db" ^
    --collect-submodules keyring.backends ^
    --copy-metadata keyring ^
    main.py || goto :fail

echo.
echo Built dist\NeuroPlan.exe  (neuroplan.db and neuroplan.log are created next to the exe on first run)
exit /b 0

:fail
echo.
echo Build failed.
exit /b 1
