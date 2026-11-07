@echo off
rem Play with the project's own Python, so there is nothing to activate.
rem   play.bat                        dry run of the 45 degree camera: prints the keys, presses nothing
rem   play.bat --live                 same, but really presses keys in the focused window
rem   play.bat <variant> [--live]     another variant (python play.py lists them)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :novenv

if "%~1"=="" goto :default
set "FIRST=%~1"
if "%FIRST:~0,1%"=="-" goto :default
".venv\Scripts\python.exe" play.py %*
goto :done

:default
".venv\Scripts\python.exe" play.py angled_camera %*
goto :done

:novenv
echo Cannot find .venv\Scripts\python.exe. Set it up first: see "Setup" in README.md.

:done
echo.
pause
