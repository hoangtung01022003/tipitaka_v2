@echo off
chcp 65001 >nul
echo ===================================================
echo   TIẾN TRÌNH CÀI ĐẶT SSL (HTTPS) CHO NGINX
echo ===================================================

set WACS_URL=https://github.com/win-acme/win-acme/releases/download/v2.2.8.1/win-acme.v2.2.8.1.x64.trimmed.zip
set WACS_DIR=win-acme
set CERTS_DIR=%~dp0nginx_config\certs
set NGINX_DIR=nginx-1.26.2

echo.
echo [1/4] Tạm thời tắt Nginx để lấy SSL...
call stop_nginx.bat >nul 2>&1

mkdir %CERTS_DIR% 2>nul
mkdir %WACS_DIR% 2>nul

if exist %WACS_DIR%\wacs.exe (
    echo [!] Cong cu Win-Acme da ton tai, bo qua tai ve...
) else (
    echo.
    echo [2/4] Đang tải Win-Acme (Công cụ lấy SSL miễn phí)...
    powershell -Command "Invoke-WebRequest -Uri '%WACS_URL%' -OutFile 'wacs.zip'"
    powershell -Command "Expand-Archive -Path 'wacs.zip' -DestinationPath '%WACS_DIR%' -Force"
    del wacs.zip
)

echo.
echo [3/4] Đang lấy chứng chỉ SSL (Let's Encrypt)...
cd %WACS_DIR%
:: Dung self-hosting de chung thuc qua port 80
wacs.exe --source manual --host suttasearch.net,www.suttasearch.net --validation selfhosting --store pemfiles --pemfilespath "%CERTS_DIR%" --installation none --agree-tos --email admin@suttasearch.net
cd ..

echo.
echo [4/4] Đang áp dụng cấu hình Nginx mới có HTTPS...
copy /Y nginx_config\nginx_ssl.conf %NGINX_DIR%\conf\nginx.conf
call start_nginx.bat

echo.
echo ===================================================
echo   HOÀN TẤT CÀI ĐẶT SSL!
echo   Bây giờ bạn có thể vào: https://suttasearch.net
echo ===================================================
pause
