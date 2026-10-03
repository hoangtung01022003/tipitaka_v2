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
echo [1/5] Cập nhật file cấu hình Nginx tối ưu bảo mật & WebSocket...
copy /Y nginx_config\nginx_ssl.conf %NGINX_DIR%\conf\nginx.conf >nul
mkdir "%~dp0%NGINX_DIR%\html" 2>nul
mkdir "%CERTS_DIR%" 2>nul

echo.
echo [2/5] Kiểm tra cú pháp Nginx...
cd %NGINX_DIR%
nginx.exe -t
if errorlevel 1 (
    echo [LOI] Cấu hình Nginx không hợp lệ! Vui lòng kiểm tra lại.
    cd ..
    pause
    exit /b 1
)
cd ..
echo [OK] Cú pháp Nginx hoàn toàn hợp lệ.

echo.
echo [3/5] Cấp/Gia hạn chứng chỉ SSL cho suttasearch.net và www.suttasearch.net...
echo       - Phương thức: Webroot filesystem (không xung đột cổng 80 khi Nginx đang chạy)
echo       - Tự động nạp lại Nginx khi gia hạn: Đã kích hoạt hook reload_nginx.bat
if exist "%WACS_DIR%\wacs.exe" (
    cd %WACS_DIR%
    wacs.exe --source manual --host suttasearch.net,www.suttasearch.net --validation filesystem --webrootpath "%~dp0%NGINX_DIR%\html" --store pemfiles --pemfilespath "%CERTS_DIR%" --installation script --script "%~dp0reload_nginx.bat" --accepttos --emailaddress admin@suttasearch.net
    cd ..
) else (
    echo [!] Chưa tìm thấy win-acme, đang chuyển sang chạy setup_ssl.bat...
    call setup_ssl.bat
    exit /b 0
)

echo.
echo [4/5] Tải lại Nginx để áp dụng chứng chỉ và cấu hình mới...
cd %NGINX_DIR%
nginx.exe -s reload
cd ..

echo.
echo [5/5] Kiểm tra tác vụ tự động gia hạn (Windows Task Scheduler)...
schtasks /query /tn "win-acme renew (wacs)" >nul 2>&1
if errorlevel 1 (
    echo [!] Đang kích hoạt lịch trình tự động gia hạn ngầm hàng ngày...
    cd %WACS_DIR%
    wacs.exe --registertask
    cd ..
) else (
    echo [OK] Tác vụ tự động gia hạn hàng ngày "win-acme renew (wacs)" đã sẵn sàng!
)

echo.
echo =================================================================
echo   HOÀN TẤT KHẮC PHỤC!
echo   - Chứng chỉ SSL mới đã bao gồm cả suttasearch.net và www.suttasearch.net
echo   - Chế độ tự động gia hạn đã chuyển sang Webroot an toàn (100% Auto-renew)
echo   - Nginx sẽ tự động tải lại chứng chỉ mới mỗi khi Win-Acme gia hạn
echo   - Đã kích hoạt đầy đủ Security Headers và WebSocket streaming
echo =================================================================
pause
