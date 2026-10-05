@echo off
title LeadEngine - Dashboard
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo Pehle "setup.bat" par double-click karo.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
echo.
echo ============================================
echo   LeadEngine dashboard khul raha hai...
echo   Browser mein: http://127.0.0.1:8765
echo   Jab tak kaam karna hai, ye window KHULI rakho.
echo   Band karna ho to ye window close kar do.
echo ============================================
echo.
python -m leadengine ui
pause
