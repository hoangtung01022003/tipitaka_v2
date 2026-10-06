@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: 1. Kiểm tra tiến trình Nginx
tasklist /fi "imagename eq nginx.exe" | findstr /i "nginx.exe" >nul
if errorlevel 1 (
    echo [%date% %time%] Watchdog: Phát hiện Nginx bị tắt, đang tự động bật lại... >> watchdog.log
    set NGINX_VERSION=1.26.2
    set NGINX_DIR=nginx-%NGINX_VERSION%
    if exist %NGINX_DIR%\nginx.exe (
        cd %NGINX_DIR%
        start nginx.exe
        cd ..
    )
)

:: 2. Kiểm tra tiến trình Python backend
tasklist /fi "imagename eq python.exe" | findstr /i "python.exe" >nul
if errorlevel 1 (
    echo [%date% %time%] Watchdog: Phát hiện Python backend bị tắt, đang tự động bật lại... >> watchdog.log
    call wscript.exe run_hidden.vbs
)
