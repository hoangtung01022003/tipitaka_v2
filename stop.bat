@echo off
chcp 65001 >nul
echo ===================================================
echo   TIẾN TRÌNH TẮT WEB CHẠY NGẦM
echo ===================================================
echo.
echo Đang tìm và tắt các tiến trình Python/Uvicorn chạy ẩn...
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM uvicorn.exe /T >nul 2>&1

echo.
echo ===================================================
echo   ĐÃ TẮT THÀNH CÔNG! CỔNG ĐÃ ĐƯỢC GIẢI PHÓNG.
echo ===================================================
pause
