@echo off
chcp 65001 >nul
echo ===================================================
echo   TIẾN TRÌNH CẬP NHẬT CODE TỰ ĐỘNG TRÊN VPS (NATIVE)
echo ===================================================

echo.
echo [1/4] Đang tải code mới nhất từ GitHub...
git pull origin main

echo.
echo [2/4] Đang kiểm tra và cài đặt các thư viện mới (nếu có)...
if exist .venv\Scripts\pip.exe (
    call .venv\Scripts\pip.exe install -r requirements.txt
) else (
    python -m venv .venv
    call .venv\Scripts\pip.exe install -r requirements.txt
)

echo.
echo [3/4] Cập nhật cấu hình Nginx...
set NGINX_VERSION=1.26.2
set NGINX_DIR=nginx-%NGINX_VERSION%
if exist "%NGINX_DIR%\conf" (
    if exist "nginx_config\certs\suttasearch.net-chain.pem" (
        copy /Y nginx_config\nginx_ssl.conf %NGINX_DIR%\conf\nginx.conf >nul
    ) else (
        copy /Y nginx_config\nginx.conf %NGINX_DIR%\conf\nginx.conf >nul
    )
)

echo.
echo [4/4] Khởi động lại toàn bộ hệ thống (Python + Nginx)...
:: Dừng tiến trình Python cũ để giải phóng cổng 8000 sạch sẽ
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM uvicorn.exe /T >nul 2>&1
timeout /t 1 /nobreak >nul

:: Bật lại toàn bộ dịch vụ (Python app + Nginx + Firewall)
call start_all.bat

echo.
echo ===================================================
echo   HOÀN TẤT! ỨNG DỤNG ĐÃ ĐƯỢC CẬP NHẬT VÀ ĐANG CHẠY.
echo ===================================================
pause
