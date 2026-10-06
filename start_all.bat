@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ===================================================
echo   TIẾN TRÌNH KHỞI ĐỘNG HỆ THỐNG SUTTASEARCH (FULL)
echo ===================================================

:: 1. Khởi động Python App (Uvicorn) nếu chưa chạy
tasklist /fi "imagename eq python.exe" | findstr /i "python.exe" >nul
if errorlevel 1 (
    echo [1/3] Đang khởi động Python/Uvicorn backend ngầm...
    call wscript.exe run_hidden.vbs
) else (
    echo [1/3] Python backend đang chạy.
)

:: 2. Khởi động Nginx nếu chưa chạy
set NGINX_VERSION=1.26.2
set NGINX_DIR=nginx-%NGINX_VERSION%
tasklist /fi "imagename eq nginx.exe" | findstr /i "nginx.exe" >nul
if errorlevel 1 (
    echo [2/3] Đang khởi động Nginx web server...
    if exist %NGINX_DIR%\nginx.exe (
        cd %NGINX_DIR%
        start nginx.exe
        cd ..
        echo [OK] Nginx đã khởi động.
    ) else (
        echo [!] Không tìm thấy %NGINX_DIR%\nginx.exe!
    )
) else (
    echo [2/3] Nginx đang chạy, đang tải lại cấu hình mới...
    call reload_nginx.bat >nul 2>&1
)

:: 3. Đảm bảo tường lửa mở cổng 80 & 443
echo [3/3] Đảm bảo cổng tường lửa 80 và 443 mở...
netsh advfirewall firewall add rule name="Allow Web (Port 80)" dir=in action=allow protocol=TCP localport=80 >nul 2>&1
netsh advfirewall firewall add rule name="Allow Secure Web (Port 443)" dir=in action=allow protocol=TCP localport=443 >nul 2>&1

echo.
echo ===================================================
echo   HỆ THỐNG ĐÃ SẴN SÀNG VÀ ĐANG CHẠY!
echo ===================================================
