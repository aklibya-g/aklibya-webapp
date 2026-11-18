@echo off
title Django Server - شركة اليمامة المالية
echo ========================================
echo   تشغيل السيرفر...
echo   http://127.0.0.1:1500
echo   اضغط Ctrl+C لإيقاف السيرفر
echo ========================================
cd /d "%~dp0"
start "" http://127.0.0.1:1500
py -3.11 manage.py runserver 127.0.0.1:1500
pause
