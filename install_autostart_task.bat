@echo off
chcp 65001 >nul
echo =====================================================================
echo   CÀI ĐẶT TỰ KHỞI ĐỘNG CÙNG WINDOWS & GIÁM SÁT SẬP WEB (TASK SCHEDULER)
echo =====================================================================
echo Yêu cầu chạy với quyền Administrator (Run as Administrator)...
echo.

set SCRIPT_DIR=%~dp0

:: 1. Tác vụ tự khởi động khi máy chủ VPS boot/restart (trước cả khi login)
echo [1/2] Đang tạo tác vụ tự khởi động khi máy chủ bật (On Boot)...
schtasks /create /tn "SuttaSearch_AutoStart_OnBoot" /tr "\"%SCRIPT_DIR%start_all.bat\"" /sc ONSTART /ru "SYSTEM" /f
if errorlevel 1 (
    echo [!] Không thể tạo bằng user SYSTEM, thử tạo bằng user Administrator...
    schtasks /create /tn "SuttaSearch_AutoStart_OnBoot" /tr "\"%SCRIPT_DIR%start_all.bat\"" /sc ONSTART /ru "Administrator" /f
)

:: 2. Tác vụ giám sát định kỳ mỗi 5 phút (nếu Nginx hoặc Python bị crash thì tự bật lại)
echo.
echo [2/2] Đang tạo tác vụ giám sát tự động mỗi 5 phút (Watchdog)...
schtasks /create /tn "SuttaSearch_Watchdog_5min" /tr "\"%SCRIPT_DIR%watchdog.bat\"" /sc MINUTE /mo 5 /ru "SYSTEM" /f
if errorlevel 1 (
    schtasks /create /tn "SuttaSearch_Watchdog_5min" /tr "\"%SCRIPT_DIR%watchdog.bat\"" /sc MINUTE /mo 5 /ru "Administrator" /f
)

echo.
echo =====================================================================
echo   HOÀN TẤT ĐĂNG KÝ TASK SCHEDULER!
echo   - Khi VPS bị restart ngầm, hệ thống sẽ tự động bật lại toàn bộ.
echo   - Cứ mỗi 5 phút, watchdog sẽ kiểm tra và tự dựng lại nếu Nginx/Python sập.
echo =====================================================================
pause
