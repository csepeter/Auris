@echo off
:: Auris - Windows launcher
:: The TTS model loads in the background; the app opens immediately.
:: Model must be present at: ..\model_backup\OmniVoice\

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The Auris virtual environment is missing: reader\.venv
    echo Run reader\setup.bat first, then start Auris again.
    pause
    exit /b 1
)
set "PYTHON=.venv\Scripts\python.exe"

echo Starting Auris...
echo Open your browser at: http://127.0.0.1:7860
echo.
echo Model status will appear in the top-right corner of the app.
echo (Model loads in the background; you can browse and read while it loads.)
echo.
echo Press Ctrl+C to stop.

"%PYTHON%" app.py
