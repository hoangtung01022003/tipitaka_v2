@echo off
chcp 65001 >nul
set NGINX_VERSION=1.26.2
set NGINX_DIR=nginx-%NGINX_VERSION%

if not exist %NGINX_DIR%\nginx.exe (
    echo [!] Không tìm thấy %NGINX_DIR%\nginx.exe
    exit /b 1
)

tasklist /fi "imagename eq nginx.exe" | findstr /i "nginx.exe" >nul
if errorlevel 1 (
    echo Đang khởi động Nginx...
    cd %NGINX_DIR%
    start nginx.exe
    cd ..
    echo [OK] Nginx đang chạy. Đã có thể truy cập qua cổng 80/443.
) else (
    echo [!] Nginx đã đang chạy rồi. Đang tải lại cấu hình (reload)...
    cd %NGINX_DIR%
    nginx.exe -s reload
    cd ..
    echo [OK] Nginx đã được tải lại thành công.
)
