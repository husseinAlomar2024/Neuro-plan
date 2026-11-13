@echo off
rem Build dist\NeuroPlan\ (PyInstaller onedir: starts faster than a single exe), install it for the current
rem user and point the "NeuroPlan" Desktop shortcut at it.
rem   Program:  %LOCALAPPDATA%\NeuroPlan\app\NeuroPlan.exe   (the app folder is replaced on every build)
rem   Data:     %LOCALAPPDATA%\NeuroPlan\neuroplan.db and neuroplan.log   (never touched by this script)
setlocal
cd /d "%~dp0"

rem ---- NeuroPlan must not be running: its program files would be locked
tasklist /FI "IMAGENAME eq NeuroPlan.exe" /NH 2>nul | find /I "NeuroPlan.exe" >nul
if not errorlevel 1 (
    echo NeuroPlan is running. Close NeuroPlan, then run build_exe.bat again.
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.13 -m venv .venv || py -3 -m venv .venv || goto :fail
    .venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt || goto :fail
)

if not exist "assets\neuroplan.ico" (
    .venv\Scripts\python assets\make_icon.py || goto :fail
)

rem Qt modules the app does not use are left out (it needs QtCore, QtGui and QtWidgets only).
.venv\Scripts\python -m PyInstaller --noconfirm --clean --noconsole --onedir --name NeuroPlan ^
    --icon "assets\neuroplan.ico" ^
    --add-data "neuroplan\db\schema.sql;neuroplan\db" ^
    --add-data "assets\neuroplan.ico;assets" ^
    --collect-submodules keyring.backends ^
    --copy-metadata keyring ^
    --exclude-module tkinter --exclude-module _tkinter --exclude-module customtkinter ^
    --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtQuickWidgets --exclude-module PySide6.QtOpenGL --exclude-module PySide6.QtOpenGLWidgets ^
    --exclude-module PySide6.QtSql --exclude-module PySide6.QtTest --exclude-module PySide6.QtXml ^
    --exclude-module PySide6.QtDBus --exclude-module PySide6.QtSvg --exclude-module PySide6.QtSvgWidgets ^
    --exclude-module PySide6.QtPdf --exclude-module PySide6.QtPdfWidgets --exclude-module PySide6.QtConcurrent ^
    --exclude-module PySide6.QtPrintSupport --exclude-module PySide6.QtHelp --exclude-module PySide6.QtDesigner ^
    --exclude-module PySide6.QtUiTools ^
    main.py || goto :fail

rem not needed by this Qt Widgets app: the software OpenGL fallback (20 MB) and Qt's own translations (7 MB)
if exist "dist\NeuroPlan\_internal\PySide6\opengl32sw.dll" del /q "dist\NeuroPlan\_internal\PySide6\opengl32sw.dll"
if exist "dist\NeuroPlan\_internal\PySide6\translations" rmdir /s /q "dist\NeuroPlan\_internal\PySide6\translations"

echo.
echo Built dist\NeuroPlan\NeuroPlan.exe

rem ---- install: only the program folder is replaced; the data files beside it are never touched
if not defined LOCALAPPDATA (
    echo LOCALAPPDATA is not set, so NeuroPlan was not installed. dist\NeuroPlan\ is ready to use.
    goto :fail
)
set "NP_DIR=%LOCALAPPDATA%\NeuroPlan"
set "NP_APP=%NP_DIR%\app"
set "NP_EXE=%NP_APP%\NeuroPlan.exe"
if not exist "%NP_DIR%" mkdir "%NP_DIR%"
if not exist "%NP_DIR%" goto :fail
if exist "%NP_APP%" rmdir /s /q "%NP_APP%"
if exist "%NP_APP%" (
    echo Could not replace "%NP_APP%". Close NeuroPlan and anything using that folder, then try again.
    goto :fail
)
robocopy "dist\NeuroPlan" "%NP_APP%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo Copying the program to "%NP_APP%" failed.
    goto :fail
)
rem the single-file exe of older builds (a program file, not data) is removed so only one NeuroPlan exists
if exist "%NP_DIR%\NeuroPlan.exe" del /q "%NP_DIR%\NeuroPlan.exe" >nul 2>&1
if exist "%NP_DIR%\NeuroPlan.exe.old" del /q "%NP_DIR%\NeuroPlan.exe.old" >nul 2>&1
echo Installed %NP_EXE%

rem ---- Desktop shortcut (the Desktop folder is asked from Windows, so a OneDrive desktop works too)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $desktop = [Environment]::GetFolderPath('Desktop'); $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'NeuroPlan.lnk')); $link.TargetPath = $env:NP_EXE; $link.WorkingDirectory = $env:NP_DIR; $link.IconLocation = $env:NP_EXE + ',0'; $link.Description = 'NeuroPlan study planner'; $link.Save(); Write-Host ('Shortcut   ' + $link.FullName)"
if errorlevel 1 (
    echo NeuroPlan is installed, but the Desktop shortcut could not be created.
    goto :fail
)

echo.
echo Done. Start NeuroPlan from the Desktop icon. Your data stays in %NP_DIR%
exit /b 0

:fail
echo.
echo Build failed.
exit /b 1
