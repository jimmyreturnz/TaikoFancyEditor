@echo off
setlocal
cd /d "%~dp0\.."
pyside6-lrelease translations\taiko_ja.ts
if errorlevel 1 exit /b 1
echo Compiled translations\taiko_ja.qm
