@echo off
REM ===========================================================================
REM  Collection Database - Windows launcher
REM
REM  Double-click this file to start the app. It:
REM    1. activates the 'collection' conda environment,
REM    2. launches the server (run.py), which fetches the Chromium print
REM       browser on first launch,
REM    3. opens the app in your default web browser.
REM
REM  Close this console window to stop the app.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

title Collection Database

REM --- Locate and activate the 'collection' conda environment ----------------
set "CONDA_OK="

REM 1) conda already on PATH (i.e. 'conda init cmd.exe' has been run)
where conda >nul 2>nul
if not errorlevel 1 (
    call conda activate collection >nul 2>nul && set "CONDA_OK=1"
)

REM 2) otherwise probe the common install locations
if not defined CONDA_OK (
    for %%D in (
        "%USERPROFILE%\miniconda3"
        "%USERPROFILE%\anaconda3"
        "%USERPROFILE%\miniforge3"
        "%LOCALAPPDATA%\miniconda3"
        "%LOCALAPPDATA%\anaconda3"
        "C:\ProgramData\miniconda3"
        "C:\ProgramData\Anaconda3"
        "C:\ProgramData\miniforge3"
    ) do (
        if not defined CONDA_OK if exist "%%~D\Scripts\activate.bat" (
            call "%%~D\Scripts\activate.bat" collection >nul 2>nul && set "CONDA_OK=1"
        )
    )
)

if not defined CONDA_OK (
    echo.
    echo   ERROR: Could not activate the 'collection' conda environment.
    echo.
    echo   Open an Anaconda / Miniconda prompt and run once:
    echo       conda env create -f environment.yml    ^(first time only^)
    echo       conda activate collection
    echo       python run.py
    echo.
    pause
    exit /b 1
)

REM --- Open the browser a few seconds after the server starts -----------------
REM  Done in a detached child so the app (below) keeps this window's output.
start "" /b cmd /c "timeout /t 5 >nul & start http://127.0.0.1:8080"

REM --- Start the app (blocks; close this window to stop it) -------------------
echo.
echo   Starting Collection Database...
echo   Your browser will open at http://127.0.0.1:8080
echo   (Close this window to stop the app.)
echo.
python run.py

REM  If run.py exits (e.g. an error), keep the window open so you can read it.
echo.
echo   The app has stopped.
pause
endlocal
