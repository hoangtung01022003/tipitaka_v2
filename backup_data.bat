@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo =====================================================================
echo   CÔNG CỤ TỰ ĐỘNG SAO LƯU DỮ LIỆU SUTTASEARCH (WINDOWS VPS)
echo =====================================================================
echo.

if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe backup_vps.py
) else (
    python backup_vps.py
)

echo.
pause
