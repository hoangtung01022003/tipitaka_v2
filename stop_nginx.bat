@echo off
chcp 65001 >nul
set NGINX_VERSION=1.26.2
set NGINX_DIR=nginx-%NGINX_VERSION%

echo Đang tắt Nginx...
if exist %NGINX_DIR%\nginx.exe (
    cd %NGINX_DIR%
    nginx.exe -s quit >nul 2>&1
    cd ..
)
timeout /t 1 /nobreak >nul
taskkill /F /IM nginx.exe >nul 2>&1
echo [OK] Nginx đã được tắt hoàn toàn.
