@echo off
chcp 65001 >nul
echo =================================================================
echo   CÔNG CỤ TỰ ĐỘNG SỬA DỨT ĐIỂM SSL & CẤU HÌNH NGINX (WINDOWS VPS)
echo =================================================================

set NGINX_VERSION=1.26.2
set NGINX_DIR=nginx-%NGINX_VERSION%
set WACS_DIR=win-acme
set CERTS_DIR=%~dp0nginx_config\certs

echo.
echo [1/4] Cập nhật file cấu hình Nginx tối ưu bảo mật & WebSocket...
copy /Y nginx_config\nginx_ssl.conf %NGINX_DIR%\conf\nginx.conf >nul
mkdir "%~dp0%NGINX_DIR%\html" 2>nul
mkdir "%CERTS_DIR%" 2>nul

echo.
echo [2/4] Kiểm tra cú pháp Nginx...
cd %NGINX_DIR%
nginx.exe -t
if errorlevel 1 (
    echo [LOI] Cau hinh Nginx khong hop le! Vui long kiem tra lai.
    cd ..
    pause
    exit /b 1
)
cd ..
echo [OK] Cú pháp Nginx hoàn toàn hợp lệ.

echo.
echo [3/4] Cấp/Gia hạn chứng chỉ SSL cho suttasearch.net và www.suttasearch.net...
echo       (Sử dụng chế độ Webroot - không cần tắt Nginx, không xung đột cổng 80)
if exist "%WACS_DIR%\wacs.exe" (
    cd %WACS_DIR%
    wacs.exe --source manual --host suttasearch.net,www.suttasearch.net --validation filesystem --webrootpath "%~dp0%NGINX_DIR%\html" --store pemfiles --pemfilespath "%CERTS_DIR%" --installation none --accepttos --emailaddress admin@suttasearch.net
    cd ..
) else (
    echo [!] Chưa tìm thấy win-acme, đang chuyển sang chạy setup_ssl.bat...
    call setup_ssl.bat
    exit /b 0
)

echo.
echo [4/4] Tải lại Nginx để áp dụng chứng chỉ và cấu hình mới...
cd %NGINX_DIR%
nginx.exe -s reload
cd ..

echo.
echo =================================================================
echo   HOÀN TẤT KHẮC PHỤC!
echo   - Chứng chỉ SSL mới đã bao gồm cả suttasearch.net và www.suttasearch.net
echo   - Chế độ tự động gia hạn đã chuyển sang Webroot an toàn
echo   - Đã kích hoạt đầy đủ Security Headers và WebSocket streaming
echo =================================================================
pause
