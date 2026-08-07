@echo off
setlocal
cd /d "%~dp0\.."
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" tools\build_i18n.py || exit /b 1
"%PY%" tools\audit_i18n.py || (
  echo Coverage failed. Open translations\coverage_report.txt
  exit /b 1
)
echo Japanese translation build and coverage audit passed.