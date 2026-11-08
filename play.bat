@echo off
rem Play with the glove and the front camera, using the project's own Python (nothing to activate).
rem   play.bat                  dry run: prints the keys it would press, presses nothing
rem   play.bat --live           really presses keys in whichever window has focus
rem   play.bat --simulate       fake glove, to try it with no hardware
rem This runs punch_live.py. It needs a trained model: record with run.bat punch_capture.py, then run.bat punch_train.py.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :novenv

".venv\Scripts\python.exe" punch_live.py %*
goto :done

:novenv
echo Cannot find .venv\Scripts\python.exe. Set it up first: see "Setup" in README.md.

:done
echo.
pause
