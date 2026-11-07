@echo off
rem Record a tuning session, then score it. Uses the project's own Python, so there is nothing to activate.
rem   tune.bat                 the 45 degree camera
rem   tune.bat <variant>       another variant (python play.py lists them)
cd /d "%~dp0"
set "VARIANT=%~1"
if "%VARIANT%"=="" set "VARIANT=angled_camera"
if not exist ".venv\Scripts\python.exe" goto :novenv

".venv\Scripts\python.exe" tune_punches.py --variant %VARIANT%
if errorlevel 1 goto :done
echo.
echo ---- Scoring the recording ----
".venv\Scripts\python.exe" replay_punches.py --variant %VARIANT%
goto :done

:novenv
echo Cannot find .venv\Scripts\python.exe. Set it up first: see "Setup" in README.md.

:done
echo.
pause
