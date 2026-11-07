@echo off
rem Run any project script with the project's own Python, so there is nothing to activate.
rem   run.bat glove_check.py
rem   run.bat punch_tune.py --simulate
rem   run.bat punch_capture.py --person kadir
rem   run.bat punch_train.py
rem   run.bat -m unittest discover tests
rem Use this instead of "python ...": on some machines "python" is a different install without OpenCV.
cd /d "%~dp0"
if "%~1"=="" goto :usage
if not exist ".venv\Scripts\python.exe" goto :novenv

".venv\Scripts\python.exe" %*
goto :done

:usage
echo Usage: run.bat ^<script.py^> [options]     for example: run.bat glove_check.py
goto :done

:novenv
echo Cannot find .venv\Scripts\python.exe. Set it up first: see "Setup" in README.md.

:done
