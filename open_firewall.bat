@echo off
chcp 65001 >nul
echo ===================================================
echo   MỞ CỔNG TƯỜNG LỬA CHO WEB (PORT 80 VA 443)
echo ===================================================

echo Yeu cau quyen Administrator de chay lenh nay...
netsh advfirewall firewall add rule name="Allow Web (Port 80)" dir=in action=allow protocol=TCP localport=80
netsh advfirewall firewall add rule name="Allow Secure Web (Port 443)" dir=in action=allow protocol=TCP localport=443

echo.
echo ===================================================
echo   DA MO PORT XONG!
echo ===================================================
pause
