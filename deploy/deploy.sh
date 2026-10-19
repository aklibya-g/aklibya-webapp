#!/usr/bin/env bash
# =====================================================================
#  deploy.sh — نشر مشروع اليمامة المالية كاملاً على VPS (Ubuntu 24.04)
#
#  الاستخدام (مرة واحدة بعد رفع المشروع):
#      cd /var/www/webapp
#      chmod +x deploy/deploy.sh
#      sudo deploy/deploy.sh
#
#  ماذا يفعل:
#    1) يثبّت المتطلبات (python3.11, node, nginx, pm2) إن لم تكن موجودة
#    2) يبني venv ويثبت requirements.txt
#    3) يجهّز قاعدة البيانات (migrate + collectstatic) إذا لم تكن موجودة
#    4) يركّب خدمة gunicorn (systemd) + nginx
#    5) يثبت وتثبّت خدمة البوت (pm2 + systemd resurrect)
#    6) يضبط الجدار الناري (ufw)
#
#  ملفات يجب رفعها يدوياً قبل التشغيل (غير موجودة في GitHub):
#      - db.sqlite3               (إذا أردت بياناتك الحالية، وإلا يُنشأ جديد)
#      - whatsapp-bot/.env         (GEMINI_API_KEY + GROUP_JID ...)
#      - whatsapp-bot/auth_session/  (جلسة واتساب — يغني عن مسح QR)
# =====================================================================
set -euo pipefail

APP_DIR="/var/www/webapp"
SVC_NAME="webapp"
NGINX_SITE="/etc/nginx/sites-available/webapp"
RUN_AS="www-data"

echo "==== [1/6] تثبيت الحزم إن لزم ===="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq python3.11 python3.11-venv python3-pip nginx git ufw curl

if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
fi
if ! command -v pm2 >/dev/null 2>&1; then
    npm install -g pm2
fi
echo "  node=$(node -v 2>/dev/null || echo '?')  pm2=$(pm2 -v 2>/dev/null || echo '?')"

echo "==== [2/6] بيئة بايثون + الحزم ===="
cd "$APP_DIR"
if [ ! -d venv ]; then
    python3.11 -m venv venv
fi
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q

echo "==== [3/6] تجهيز قاعدة البيانات والمجلدات ===="
mkdir -p "$APP_DIR" && chown -R "$RUN_AS:$RUN_AS" "$APP_DIR"
if [ ! -f db.sqlite3 ]; then
    echo "  db.sqlite3 غير موجود — إنشاء قاعدة جديدة عبر migrate"
fi
sudo -u "$RUN_AS" venv/bin/python manage.py migrate --noinput
sudo -u "$RUN_AS" venv/bin/python manage.py collectstatic --noinput
# مجلد للكتابة (سجلات/مرفوعات)
sudo -u "$RUN_AS" mkdir -p "$APP_DIR/media" "$APP_DIR/logs"

echo "==== [4/6] خدمة gunicorn (systemd) ===="
cp "$APP_DIR/deploy/webapp.service" "/etc/systemd/system/${SVC_NAME}.service"
systemctl daemon-reload
systemctl enable "$SVC_NAME" >/dev/null 2>&1
systemctl restart "$SVC_NAME"
echo "  status: $(systemctl is-active "$SVC_NAME")"

echo "==== [5/6] nginx ===="
ln -sf "$NGINX_SITE" "/etc/nginx/sites-enabled/webapp" 2>/dev/null || true
if [ ! -f "$NGINX_SITE" ]; then
    cp "$APP_DIR/deploy/nginx.conf" "$NGINX_SITE"
fi
# أزل الموقع الافتراضي المتعارض إن وجد
rm -f /etc/nginx/sites-enabled/default
nginx -t -q && systemctl reload nginx
echo "  nginx: $(systemctl is-active nginx)"

echo "  ---- بوت الواتساب (pm2) ----"
cd "$APP_DIR/whatsapp-bot"
if [ ! -d node_modules ]; then
    npm install --omit=dev -s
fi
# ملكية النُص لمستخدم الخدمة
chown -R "$RUN_AS:$RUN_AS" "$APP_DIR/whatsapp-bot" 2>/dev/null || true
sudo -u "$RUN_AS" bash -c "cd $APP_DIR/whatsapp-bot && pm2 start index.js --name wabot 2>/dev/null || pm2 restart wabot"
sudo -u "$RUN_AS" pm2 save -s || true
cp "$APP_DIR/deploy/whatsapp-bot.service" /etc/systemd/system/whatsapp-bot.service
systemctl daemon-reload
systemctl enable whatsapp-bot >/dev/null 2>&1
systemctl start whatsapp-bot
echo "  pm2: $(pm2 jlist 2>/dev/null | grep -o '"name":"wabot"' | head -1 || echo 'غير معروف')"

echo "==== [6/6] الجدار الناري ===="
if command -v ufw >/dev/null 2>&1; then
    ufw allow OpenSSH >/dev/null 2>&1 || true
    ufw allow 80/tcp >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ufw --force enable >/dev/null 2>&1 || true
    echo "  ufw: $(ufw status | head -1)"
fi

echo ""
echo "=================================================="
echo " ✅ اكتمل النشر"
echo "    الموقع:      http://<IP_السيرفر>/"
echo "    دخول لوحة:   admin / shaban"
echo "    حالة الخدمة: systemctl status webapp"
echo "    حالة البوت:  pm2 list  (wabot)"
echo "    سجل البوت:   pm2 logs wabot"
echo ""
echo " ⚠️  تذكّر:"
echo "    - رفع whatsapp-bot/.env و auth_session/ قبل تشغيل البوت"
echo "    - لا تشغّل نسخة البوت على جهازك المحلي بالتوازي"
echo "=================================================="
