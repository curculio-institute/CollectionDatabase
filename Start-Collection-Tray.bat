@echo off
REM ===========================================================================
REM  Collection Database - Windows tray launcher (no console window)
REM
REM  Normally invoked by Collection.vbs, which runs this fully hidden. It:
REM    1. activates the 'collection' conda environment,
REM    2. starts the tray launcher with pythonw (no console), which supervises
REM       the server and shows a tray icon with Open Collection / Quit.
REM
REM  Exit code 1 = the 'collection' env could not be activated; Collection.vbs
REM  shows an error box in that case. Server start-up failures are reported by
REM  the tray launcher itself (an error dialog + the log).
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

REM Start the tray launcher detached and with no console (pythonw). The tray
REM process outlives this script, which then exits.
start "" pythonw "%~dp0collection_tray.py"
exit /b 0
