@echo off
REM ===========================================================================
REM  Collection Database - Windows front door (no console window)
REM
REM  Normally invoked by Collection.vbs, which runs this fully hidden. It:
REM    1. activates the 'collection' conda environment,
REM    2. starts the server with pythonw (no console), passing --auto-shutdown so
REM       closing the app window quits the server (with a desktop notification) —
REM       no invisible server left running.
REM
REM  Exit code 1 = the 'collection' env could not be activated; Collection.vbs
REM  shows an error box in that case.
REM
REM  For a visible/debug run with logs in the window, use Start-Collection.bat.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "CONDA_OK="

REM 1) conda already on PATH
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

if not defined CONDA_OK exit /b 1

REM Start the server detached and with no console (pythonw). It outlives this
REM script, which then exits; closing the app window shuts the server down.
start "" pythonw "%~dp0run.py" --auto-shutdown
exit /b 0
