@echo off
title LeadEngine - Backup
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
python -m leadengine backup
pause
