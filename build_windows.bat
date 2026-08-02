@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo  Taiko Fancy Arranger v1.0.0 Builder
echo  Output: Portable folder with EXE
echo ==========================================
echo.

set "PYTHON_CMD="
py -3.12 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3.12"
if not defined PYTHON_CMD (
  py -3 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
  python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo ERROR: No suitable Python 3.10 or newer runtime was found.
  echo Install the official Python Install Manager, then install Python 3.12:
  echo   winget install 9NQ7512CXL7T
  echo   py install 3.12
  pause
  exit /b 1
)

echo Using:
%PYTHON_CMD% -c "import sys, platform; print(sys.executable); print(platform.python_version(), platform.architecture()[0])"

if not exist .venv (
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :failed
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :failed
pip install -r requirements-build.txt
if errorlevel 1 goto :failed
python check_release_clean.py
if errorlevel 1 goto :failed
python -m unittest discover -v
if errorlevel 1 goto :failed
pyinstaller --noconfirm --clean TaikoFancyArranger.spec
if errorlevel 1 goto :failed
if not exist dist\TaikoFancyArranger\TaikoFancyArranger.exe goto :missingexe
copy /Y README.md dist\TaikoFancyArranger\README.md >nul
copy /Y LICENSE dist\TaikoFancyArranger\LICENSE >nul
copy /Y VERSION dist\TaikoFancyArranger\VERSION >nul

echo.
echo BUILD SUCCEEDED
echo Run: dist\TaikoFancyArranger\TaikoFancyArranger.exe
echo Distribute the COMPLETE TaikoFancyArranger folder.
start "" dist\TaikoFancyArranger
pause
exit /b 0

:missingexe
echo ERROR: Expected EXE was not found.
pause
exit /b 1

:failed
echo BUILD FAILED. Review the error above.
pause
exit /b 1
