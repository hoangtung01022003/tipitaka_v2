@echo off
chcp 65001 >nul
echo ===================================================
echo   TIẾN TRÌNH CẬP NHẬT CODE TỰ ĐỘNG TRÊN VPS
echo ===================================================

echo.
echo [1/3] Đang tải code mới nhất từ GitHub...
git pull origin main

echo.
echo [2/3] Đang kiểm tra và cài đặt các thư viện mới (nếu có)...
if exist .venv\Scripts\pip.exe (
    call .venv\Scripts\pip.exe install -r requirements.txt
) else (
    python -m venv .venv
    call .venv\Scripts\pip.exe install -r requirements.txt
)

echo.
echo [3/3] Đang khởi động lại hệ thống ngầm...
where pm2 >nul 2>&1
if %errorlevel% equ 0 (
    echo Đang dùng PM2...
    call pm2 start run.bat --name "tipitaka-ai-backend" >nul 2>&1
    call pm2 restart tipitaka-ai-backend
    call pm2 save
) else (
    echo Đang khởi động ngầm qua VBScript (Không cần Node.js)...
    call wscript.exe run_hidden.vbs
)

echo.
echo ===================================================
echo   HOÀN TẤT! ỨNG DỤNG ĐÃ ĐƯỢC CẬP NHẬT VÀ ĐANG CHẠY.
echo ===================================================
pause
