@echo off
rem Build dist\NeuroPlan.exe (single file, no console window), install it for the current user and
rem put a "NeuroPlan" shortcut on the Desktop.
rem   Program:  %LOCALAPPDATA%\NeuroPlan\NeuroPlan.exe   (replaced on every build)
rem   Data:     %LOCALAPPDATA%\NeuroPlan\neuroplan.db and neuroplan.log   (never touched by this script)
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.13 -m venv .venv || py -3 -m venv .venv || goto :fail
    .venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt || goto :fail
)

if not exist "assets\neuroplan.ico" (
    .venv\Scripts\python assets\make_icon.py || goto :fail
)

.venv\Scripts\python -m PyInstaller --noconfirm --clean --noconsole --onefile --name NeuroPlan ^
    --icon "assets\neuroplan.ico" ^
    --collect-data customtkinter ^
    --add-data "neuroplan\db\schema.sql;neuroplan\db" ^
    --add-data "assets\neuroplan.ico;assets" ^
    --collect-submodules keyring.backends ^
    --copy-metadata keyring ^
    main.py || goto :fail

echo.
echo Built dist\NeuroPlan.exe

rem ---- install: only NeuroPlan.exe is copied. The folder is never emptied or removed, so the
rem      database and the log that live beside the exe survive every rebuild.
if not defined LOCALAPPDATA (
    echo LOCALAPPDATA is not set, so the exe was not installed. dist\NeuroPlan.exe is ready to use.
    goto :fail
)
set "NP_DIR=%LOCALAPPDATA%\NeuroPlan"
set "NP_EXE=%NP_DIR%\NeuroPlan.exe"
if not exist "%NP_DIR%" mkdir "%NP_DIR%"
if not exist "%NP_DIR%" goto :fail
set "NP_RUNNING="
rem an exe set aside by an earlier build (see below); a program file, not data
if exist "%NP_EXE%.old" del /q "%NP_EXE%.old" >nul 2>&1
copy /y "dist\NeuroPlan.exe" "%NP_EXE%" >nul 2>&1
if errorlevel 1 (
    rem NeuroPlan is running. Windows lets a running exe be renamed, so set it aside and copy again.
    move /y "%NP_EXE%" "%NP_EXE%.old" >nul 2>&1
    copy /y "dist\NeuroPlan.exe" "%NP_EXE%" >nul 2>&1
    if errorlevel 1 (
        if not exist "%NP_EXE%" move /y "%NP_EXE%.old" "%NP_EXE%" >nul 2>&1
        echo Could not replace "%NP_EXE%". Close NeuroPlan and run build_exe.bat again.
        goto :fail
    )
    set "NP_RUNNING=1"
)
echo Installed %NP_EXE%
if defined NP_RUNNING echo NeuroPlan is running: close it and open it again to use the new version.

rem ---- Desktop shortcut (the Desktop folder is asked from Windows, so a OneDrive desktop works too)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $desktop = [Environment]::GetFolderPath('Desktop'); $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'NeuroPlan.lnk')); $link.TargetPath = $env:NP_EXE; $link.WorkingDirectory = $env:NP_DIR; $link.IconLocation = $env:NP_EXE + ',0'; $link.Description = 'NeuroPlan study planner'; $link.Save(); Write-Host ('Shortcut   ' + $link.FullName)"
if errorlevel 1 (
    echo The exe is installed, but the Desktop shortcut could not be created.
    goto :fail
)

echo.
echo Done. Start NeuroPlan from the Desktop icon. Your data stays in %NP_DIR%
exit /b 0

:fail
echo.
echo Build failed.
exit /b 1
