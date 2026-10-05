@echo off
title LeadEngine - Setup
cd /d "%~dp0"
echo.
echo ============================================
echo   LeadEngine setup (sirf pehli dafa)
echo   Is mein 5-10 minute lag sakte hain.
echo ============================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY set "PY=python"
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [X] Python 3.11 ya naya nahi mila.
  echo     python.org se Python install karo aur "Add Python to PATH" zaroor tick karo.
  echo     Phir ye file dobara double-click karo.
  goto fail
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Python environment bana raha hoon...
  %PY% -m venv .venv
  if errorlevel 1 goto fail
)
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto fail

echo [2/5] Zaroori packages install ho rahe hain...
python -m pip install --upgrade pip >nul
python -m pip install -e ".[dev]"
if errorlevel 1 goto fail

echo [3/5] Browser (Chromium) download ho raha hai...
python -m playwright install chromium
if errorlevel 1 goto fail

echo [4/5] Settings files...
if not exist ".env" copy ".env.example" ".env" >nul

echo [5/5] Database aur check...
python -m leadengine init
python -m leadengine doctor

echo.
echo ============================================
echo   Setup mukammal!
echo   Ab "start.bat" par double-click karo.
echo   (config.toml mein apna naam/address bhi likh dena)
echo ============================================
if not defined LEADENGINE_NO_PAUSE pause
exit /b 0

:fail
echo.
echo [X] Kuch ghalat ho gaya. Upar wala text copy kar ke Claude ko bhej do.
if not defined LEADENGINE_NO_PAUSE pause
exit /b 1
