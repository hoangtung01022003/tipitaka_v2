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
call .\.venv\Scripts\pip.exe install -r requirements.txt

echo.
echo [3/3] Đang khởi động lại hệ thống ngầm bằng PM2...
:: Dùng start để tạo mới (nếu chưa có) và restart để áp dụng code mới (nếu đã có)
call pm2 start run.bat --name "tipitaka-ai-backend" >nul 2>&1
call pm2 restart tipitaka-ai-backend
call pm2 save

echo.
echo ===================================================
echo   HOÀN TẤT! ỨNG DỤNG ĐÃ ĐƯỢC CẬP NHẬT VÀ ĐANG CHẠY.
echo ===================================================
pause
