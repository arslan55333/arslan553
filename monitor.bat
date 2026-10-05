@echo off
title LeadEngine - Weekly watch
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
python -m leadengine monitor
