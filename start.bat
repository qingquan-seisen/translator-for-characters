@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
start /b "" pythonw gui_main.py
exit