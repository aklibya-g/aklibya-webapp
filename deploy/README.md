# نشر اليمامة المالية على VPS

بنية واحدة: **Django + SQLite + بوت الواتساب كلها على سيرفر واحد** — بايثون أني وير غير مطلوب.

## قبل النشر (رفع 3 ملفات يدوياً)

| الملف | من أين | لماذا |
|---|---|---|
| `db.sqlite3` | من جهازك المحلي | بياناتك الحالية (موجود في GitHub أصلاً) |
| `whatsapp-bot/.env` | من جهازك | `GEMINI_API_KEY` + `GROUP_JID` (غير محفوظ في GitHub) |
| `whatsapp-bot/auth_session/` | من جهازك | جلسة واتساب — تغني عن مسح QR من جديد |

> بدون `auth_session` ستحتاج مسح QR مرة واحدة من هاتفك بعد النشر (يظهر في صفحة الواتساب).

## خطوات التشغيل

```bash
# 1) على جهازك: ارفع المشروع
scp -r webapp3/* user@SERVER_IP:/var/www/webapp/
# أو الأفضل: git clone من GitHub على السيرفر ثم انسخ الملفات اليدوية

# 2) على السيرفر
cd /var/www/webapp
chmod +x deploy/deploy.sh
sudo deploy/deploy.sh
```

السكربت يثبّت كل شيء ويشغّل الخدمتين.

## الأوامر اليومية

```bash
systemctl status webapp        # حالة الموقع
systemctl restart webapp       # إعادة تشغيل الموقع
pm2 list                       # حالة البوت
pm2 logs wabot                 # سجل البوت
pm2 restart wabot              # إعادة تشغيل البوت
```

## ملاحظات

- **لا تشغّل** نسخة البوت على جهازك المحلي بعد النقل (تضارب جلسات)
- بايثون أني وير **يُلغى** — الموقع يُفتح على `http://IP_السيرفر`
- لو أردت نطاقاً + HTTPS: ثبّت `certbot` وأضف `server_name` في `deploy/nginx.conf`
- احتياطي القاعدة: `cron` يومي لنسخ `db.sqlite3` خارج السيرفر
