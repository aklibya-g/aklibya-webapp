#!/usr/bin/env bash
# تحديث الكود على السيرفر فقط. لا ينقل أي قاعدة بيانات أو سجلات محلية.
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/aklibya-webapp}"
cd "$APP_DIR"

BACKUP=""
if [ -f db.sqlite3 ]; then
    BACKUP="$(mktemp /tmp/webapp-online-db.XXXXXX)"
    cp -p db.sqlite3 "$BACKUP"
    echo "تم حفظ نسخة مؤقتة من قاعدة بيانات الموقع الأونلاين."
fi

git pull --ff-only

# إذا كان الموقع يستخدم SQLite، أعد قاعدة بيانات الأونلاين كما كانت.
if [ -n "$BACKUP" ]; then
    cp -p "$BACKUP" db.sqlite3
    rm -f "$BACKUP"
fi

if [ -x venv/bin/pip ]; then
    venv/bin/pip install -r requirements.txt -q
fi
if [ -x venv/bin/python ]; then
    venv/bin/python manage.py migrate --noinput
    venv/bin/python manage.py collectstatic --noinput
fi

echo "تم تحديث الكود فقط. أعد تشغيل خدمة الموقع من لوحة AlwaysData."
