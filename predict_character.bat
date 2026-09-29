@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found.
    echo Please run install.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

if not exist "character_descriptions.txt" (
    echo [INFO] Creating template file...
    python predict_character.py
    echo.
    echo [INFO] Opening template file for editing...
    notepad character_descriptions.txt
    echo.
    echo Please fill in the descriptions, save, and run this script again.
    pause
    exit /b 0
)

python predict_character.py --save

pause