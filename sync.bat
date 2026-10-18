@echo off
chcp 65001 >nul
cd /d C:\Users\ak_ly\Desktop\webapp3
echo ===================================
echo  زامن البيانات مع الأونلاين
echo ===================================
echo.
echo [1/2] جاري الدفع إلى GitHub...
git add db.sqlite3 access_app/ access_app/templates/ access_app/management/
git commit -m "sync: auto update %date% %time%"
git push origin main
echo.
echo [2/2] تم الدفع بنجاح!
echo.
echo الآن اذهب إلى PythonAnywhere وافتح Bash واكتب:
echo   cd ~/aklibya-webapp ^&^& git pull
echo.
echo ثم اضغط Reload من صفحة Web.
echo.
pause
