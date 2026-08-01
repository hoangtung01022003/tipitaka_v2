@echo off
chcp 65001 >nul
set NGINX_VERSION=1.26.2
set NGINX_DIR=nginx-%NGINX_VERSION%

echo Đang khởi động Nginx...
cd %NGINX_DIR%
start nginx.exe
cd ..
echo Nginx đang chạy. Đã có thể truy cập qua cổng 80.
