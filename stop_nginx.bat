@echo off
chcp 65001 >nul
set NGINX_VERSION=1.26.2
set NGINX_DIR=nginx-%NGINX_VERSION%

echo Đang tắt Nginx...
cd %NGINX_DIR%
nginx.exe -s quit
cd ..
echo Nginx đã được tắt.
