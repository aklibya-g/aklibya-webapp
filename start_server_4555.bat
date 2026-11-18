@echo off
setlocal
cd /d "%~dp0"

start "شركة العربي للحوالات المالية - Server" /min cmd /k "cd /d "%~dp0" && python manage.py runserver 0.0.0.0:4555"

timeout /t 3 /nobreak >nul
start "" "msedge.exe" "http://127.0.0.1:4555/login/"

endlocal
