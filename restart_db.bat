@echo off
chcp 65001 >nul
echo ===================================================
echo   KHỞI ĐỘNG / CẬP NHẬT DATABASE (DOCKER)
echo ===================================================

echo.
echo Đang chạy docker-compose...
docker-compose up -d

echo.
echo ===================================================
echo   HOÀN TẤT! DATABASE ĐÃ ĐƯỢC BẬT.
echo ===================================================
pause
