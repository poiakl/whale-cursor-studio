@echo off
cd /d "%~dp0"
py -3 -m pip install -r requirements-dev.txt
if errorlevel 1 goto fail
py -3 -m pytest -q
if errorlevel 1 goto fail
py -3 -m PyInstaller --noconfirm --clean --windowed --onedir --name WhaleCursorStudio --add-data "assets;assets" --collect-all PySide6.QtMultimedia main.py
if errorlevel 1 goto fail
explorer dist\WhaleCursorStudio
exit /b 0
:fail
pause
exit /b 1
