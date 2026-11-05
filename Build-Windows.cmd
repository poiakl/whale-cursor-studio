@echo off
cd /d "%~dp0"
py -3 -m pip install -r requirements-dev.txt
if errorlevel 1 goto fail
py -3 -m pytest -q
if errorlevel 1 goto fail
py -3 -m PyInstaller --noconfirm --clean --windowed --onedir --name WhaleCursorStudio --add-data "assets;assets" --collect-all PySide6.QtMultimedia main.py
if errorlevel 1 goto fail
py -3 -m PyInstaller --noconfirm --clean --console --onefile --name WhaleCursorMCP --copy-metadata mcp --collect-submodules mcp.server.fastmcp mcp_entry.py
if errorlevel 1 goto fail
copy /y dist\WhaleCursorMCP.exe dist\WhaleCursorStudio\WhaleCursorMCP.exe
if errorlevel 1 goto fail
py -3 tests/mcp_protocol_probe.py --server dist/WhaleCursorStudio/WhaleCursorMCP.exe --editor dist/WhaleCursorStudio/WhaleCursorStudio.exe --workspace build/mcp-check
if errorlevel 1 goto fail
copy /y docs\mcp.md dist\WhaleCursorStudio\AI接入说明.md
py -3 -c "from whale_cursor.project import Project; Project.defaults().save('dist/WhaleCursorStudio/Schemes/Whale-Maid.whalepack')"
if errorlevel 1 goto fail
explorer dist\WhaleCursorStudio
exit /b 0
:fail
pause
exit /b 1
