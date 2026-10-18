@echo off
title WhatsApp Bot - شركة اليمامة المالية
echo ========================================
echo   تشغيل بوت الواتساب...
echo   http://127.0.0.1:1500
echo   اضغط Ctrl+C لإيقاف البوت
echo ========================================
cd /d "%~dp0"
start "" http://127.0.0.1:1500
py -3.11 whatsapp_bot.py
pause
