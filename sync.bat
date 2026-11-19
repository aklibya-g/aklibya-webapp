@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===============================================
echo  إصدار الكود فقط - بدون نقل أي بيانات
echo ===============================================
echo.
echo [1/3] تجهيز ملفات البرنامج والقوالب فقط...
git add access_app config deploy whatsapp-bot/index.js whatsapp-bot/package.json whatsapp-bot/package-lock.json manage.py requirements.txt Procfile runtime.txt start.bat start.sh sync.bat release.bat RELEASE_WORKFLOW.md
rem حماية إضافية: لا تسمح بإضافة قاعدة البيانات المحلية.
git reset -- db.sqlite3 2>nul
git reset -- media backups whatsapp-bot/auth_session 2>nul
echo.
echo [2/3] الملفات المرحّلة:
git diff --cached --name-only
echo.
git commit -m "release: code only %date% %time%"
if errorlevel 1 (
    echo لا يوجد تعديل برمجي جديد أو حدث خطأ في الالتزام.
    pause
    exit /b 1
)
echo.
echo [3/3] رفع إصدار البرنامج فقط إلى GitHub...
git push origin main
echo.
echo اكتمل إصدار الكود. لم يتم نقل db.sqlite3 أو أي بيانات محلية.
echo بعد ذلك نفّذ تحديث السيرفر من خلال deploy/online_update.sh ثم أعد تشغيل الموقع.
pause
