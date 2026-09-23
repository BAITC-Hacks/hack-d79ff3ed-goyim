@echo off
cd /d "%~dp0"
py -3 app.py --open
if errorlevel 1 pause
