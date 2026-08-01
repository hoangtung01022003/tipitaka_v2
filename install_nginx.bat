@echo off
chcp 65001 >nul
echo ===================================================
echo   TIẾN TRÌNH CÀI ĐẶT VÀ CẤU HÌNH NGINX TRÊN WINDOWS
echo ===================================================

set NGINX_VERSION=1.26.2
set NGINX_ZIP=nginx-%NGINX_VERSION%.zip
set NGINX_DIR=nginx-%NGINX_VERSION%

if exist %NGINX_DIR% (
    echo [!] Nginx da ton tai. Bo qua buoc tai ve...
    goto :config_nginx
)

echo.
echo [1/3] Đang tải Nginx %NGINX_VERSION%...
powershell -Command "Invoke-WebRequest -Uri 'http://nginx.org/download/%NGINX_ZIP%' -OutFile '%NGINX_ZIP%'"

echo.
echo [2/3] Đang giải nén...
powershell -Command "Expand-Archive -Path '%NGINX_ZIP%' -DestinationPath '.' -Force"
del %NGINX_ZIP%

:config_nginx
echo.
echo [3/3] Đang copy file cấu hình nginx.conf...
copy /Y nginx_config\nginx.conf %NGINX_DIR%\conf\nginx.conf

echo.
echo ===================================================
echo   HOÀN TẤT!
echo   Để khởi động Nginx, vui lòng chạy script: start_nginx.bat
echo ===================================================
pause
