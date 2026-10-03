@echo off
chcp 65001 >nul
echo ===================================================
echo   TIẾN TRÌNH CÀI ĐẶT & GIA HẠN SSL (HTTPS) CHO NGINX
echo ===================================================

set WACS_URL=https://github.com/win-acme/win-acme/releases/download/v2.2.8.1/win-acme.v2.2.8.1.x64.trimmed.zip
set WACS_DIR=win-acme
set CERTS_DIR=%~dp0nginx_config\certs
set NGINX_DIR=nginx-1.26.2

mkdir "%CERTS_DIR%" 2>nul
mkdir "%WACS_DIR%" 2>nul
mkdir "%~dp0%NGINX_DIR%\html" 2>nul

if exist "%WACS_DIR%\wacs.exe" (
    echo [!] Cong cu Win-Acme da ton tai, bo qua buoc tai ve.
) else (
    echo.
    echo [1/4] Đang tải Win-Acme - Cong cu lay SSL mien phi...
    python download_wacs.py
)

echo.
echo [2/4] Đang chuẩn bị cấu hình Nginx Webroot để xác thực...
if exist "%CERTS_DIR%\suttasearch.net-chain.pem" (
    copy /Y nginx_config\nginx_ssl.conf %NGINX_DIR%\conf\nginx.conf >nul
) else (
    copy /Y nginx_config\nginx.conf %NGINX_DIR%\conf\nginx.conf >nul
)

:: Kiểm tra và khởi động Nginx nếu chưa chạy để phục vụ acme-challenge
tasklist /fi "imagename eq nginx.exe" | findstr /i "nginx.exe" >nul
if errorlevel 1 (
    echo Khởi động Nginx...
    call start_nginx.bat
) else (
    echo Tải lại cấu hình Nginx...
    call reload_nginx.bat
)

echo.
echo [3/4] Đang lấy/gia hạn chứng chỉ SSL (suttasearch.net & www.suttasearch.net)...
cd %WACS_DIR%
:: Sử dụng webroot filesystem qua Nginx để không bị xung đột cổng 80 và hỗ trợ tự động gia hạn vĩnh viễn
wacs.exe --source manual --host suttasearch.net,www.suttasearch.net --validation filesystem --webrootpath "%~dp0%NGINX_DIR%\html" --store pemfiles --pemfilespath "%CERTS_DIR%" --installation none --accepttos --emailaddress admin@suttasearch.net
cd ..

echo.
echo [4/4] Đang áp dụng cấu hình Nginx HTTPS và bảo mật mới...
copy /Y nginx_config\nginx_ssl.conf %NGINX_DIR%\conf\nginx.conf >nul
call reload_nginx.bat

echo.
echo ===================================================
echo   HOÀN TẤT CÀI ĐẶT & GIA HẠN SSL!
echo   Tên miền hỗ trợ: https://suttasearch.net và https://www.suttasearch.net
echo   Cơ chế Auto-renew: Sử dụng Webroot, không xung đột cổng 80.
echo ===================================================
pause
