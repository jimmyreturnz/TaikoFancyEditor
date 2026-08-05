@echo off
setlocal
cd /d "%~dp0\.."
pyside6-lupdate gui.py settings_dialog.py settings.py -ts translations\taiko_ja.ts
if errorlevel 1 exit /b 1
echo Updated translations\taiko_ja.ts
