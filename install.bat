@echo off
chcp 65001 >nul
title تثبيت النظام - شركة اليمامة المالية

echo ========================================
echo    تثبيت نظام شركة اليمامة المالية
echo ========================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Python غير مثبت
    echo.
    echo جاري فتح صفحة تثبيت Python...
    start https://www.python.org/downloads/
    echo.
    echo يرجى تثبيت Python أولاً ثم تشغيل هذا الملف مرة أخرى
    pause
    exit /b 1
)

echo [✓] Python مثبت
python --version

:: Install requirements
echo.
echo جاري تثبيت المتطلبات...
pip install -r requirements.txt

echo.
echo ========================================
echo   التثبيت بنجاح!
echo   اضغط على start.bat لتشغيل النظام
echo ========================================
pause
