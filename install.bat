@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo ========================================
echo Translator for Characters
echo Installation
echo ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.11 or newer.
    pause
    exit /b 1
)

python --version
echo.

:: ==================== Create virtual environment ====================
if exist "venv\Scripts\python.exe" (
    echo [INFO] Existing virtual environment found.
) else (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo.
echo [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

:: ==================== Upgrade pip ====================
echo.
echo [INFO] Upgrading pip...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

if errorlevel 1 (
    echo [WARN] Failed to upgrade pip. Continuing anyway...
)

:: ==================== Install uv in virtual environment ====================
echo.
echo [INFO] Installing uv in virtual environment...
pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple

if errorlevel 1 (
    echo [ERROR] Failed to install uv.
    pause
    exit /b 1
)

:: ==================== Install dependencies with uv ====================
echo.
echo [INFO] Installing dependencies with uv...
set UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
uv pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

:: ==================== Check .env file ====================
echo.
echo [INFO] Checking .env...

if not exist ".env" (
    echo [INFO] Creating .env...
    (
        echo OPENAI_API_KEY=
        echo OPENAI_API_BASE=https://api.deepseek.com/v1
        echo OPENAI_MODEL_NAME=deepseek-v4-flash
        echo EMBED_MODEL=paraphrase-multilingual-MiniLM-L12-v2
        echo CORPUS_SUFFIX=
        echo RAG_TOP_K=4
        echo RAG_THRESHOLD=0.48
        echo CHARACTER_TOP_K=5
        echo MAX_TERMS_IN_PROMPT=80
        echo TERM_BATCH_SIZE=30
    ) > ".env"
) else (
    echo [INFO] Existing .env found. It will NOT be overwritten.
)

echo.
echo ========================================
echo Installation completed.
echo ========================================
echo.
echo Please edit .env and set OPENAI_API_KEY.
echo Then run: start.bat
echo.
pause