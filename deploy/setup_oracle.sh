#!/usr/bin/env bash
# إعداد خادم Ubuntu لنشر المنظومة (Django + بوت الواتساب + PostgreSQL + nginx)
# التشغيل: bash deploy/setup_oracle.sh
set -euo pipefail

APP_DIR="/home/ubuntu/webapp3"
ENV_FILE="$APP_DIR/deploy/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "!! أنشئ $ENV_FILE أولًا (انسخ من deploy/.env.example واملأ القيم)."
  exit 1
fi
set -a; source "$ENV_FILE"; set +a

echo "== 1) حزم النظام =="
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git nginx \
    postgresql postgresql-contrib build-essential libpq-dev curl

echo "== 2) تثبيت Node.js 20 =="
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

echo "== 3) إعداد قاعدة بيانات PostgreSQL =="
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='webapp'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER webapp WITH PASSWORD '${DB_PASSWORD}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='webapp3'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE webapp3 OWNER webapp;"
sudo -u postgres psql -c "ALTER USER webapp CREATEDB;"

echo "== 4) بيئة Python الافتراضية + الحزم =="
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "== 5) ترحيل قاعدة البيانات + الملفات الثابتة =="
export DATABASE_URL="postgres://webapp:${DB_PASSWORD}@127.0.0.1:5432/webapp3"
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py collectstatic --noinput

echo "== 6) تثبيت حزم بوت الواتساب =="
cd "$APP_DIR/whatsapp-bot"
npm install

echo "== 7) خدمات systemd (Django + البوت) =="
sudo cp "$APP_DIR/deploy/gunicorn.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/whatsapp-bot.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gunicorn whatsapp-bot

echo "== 8) إعداد nginx =="
sudo cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/webapp3
sudo ln -sf /etc/nginx/sites-available/webapp3 /etc/nginx/sites-enabled/webapp3
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "تم الإعداد بنجاح. تحقق من:"
echo "  systemctl status gunicorn whatsapp-bot"
echo "  journalctl -u whatsapp-bot -f   (لمتابعة رمز QR عند الربط)"
echo "ثم أنشئ مستخدم المدير:"
echo "  cd $APP_DIR && export DATABASE_URL='postgres://webapp:${DB_PASSWORD}@127.0.0.1:5432/webapp3' && ./venv/bin/python manage.py createsuperuser"
