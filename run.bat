@echo off
chcp 65001 >nul
cd /d "%~dp0"
start /min cmd /c "cd /d "%~dp0" && call venv\Scripts\activate.bat && python bot.py %*"
