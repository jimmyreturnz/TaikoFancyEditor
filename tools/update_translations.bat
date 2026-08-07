@echo off
setlocal
cd /d "%~dp0\.."
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM translations\taiko_ja.ts is the reviewed catalog and the single source of
REM truth. Edit it by hand or in Qt Linguist. Nothing in this script rewrites
REM application source or machine-translates entries.

call "%~dp0compile_translations.bat"
if errorlevel 1 exit /b 1

set "QT_QPA_PLATFORM=offscreen"
"%PY%" -m unittest tests.test_i18n -v
if errorlevel 1 (
  echo.
  echo Localization checks failed. Nothing was written to the source tree.
  echo Fix translations\taiko_ja.ts, then run this script again.
  exit /b 1
)

echo.
echo Japanese catalog compiled and localization checks passed.
