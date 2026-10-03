@echo off
chcp 65001 >nul
set NGINX_VERSION=1.26.2
set NGINX_DIR=nginx-%NGINX_VERSION%

if not exist %NGINX_DIR%\nginx.exe (
    echo [!] Khong tim thay thu muc %NGINX_DIR%
    exit /b 1
)

echo Đang tải lại cấu hình Nginx...
cd %NGINX_DIR%
nginx.exe -s reload
cd ..
echo [OK] Nginx đã được tải lại thành công.
