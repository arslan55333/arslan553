@echo off
title LeadEngine - Schedule weekly watch
cd /d "%~dp0"
echo Har roz subah 9 baje LeadEngine apni saved watches check karega (sirf jo due hon).
schtasks /Create /SC DAILY /TN "LeadEngine watch" /TR "\"%~dp0monitor.bat\"" /ST 09:00 /F
if errorlevel 1 (
  echo [X] Schedule nahi bana. Is file par right-click karke "Run as administrator" try karo.
) else (
  echo Ho gaya! Band karna ho to: schtasks /Delete /TN "LeadEngine watch" /F
)
pause
