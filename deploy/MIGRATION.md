# سياق النشر — نقل الموقع إلى VPS

> **الحالة:** جاهز للتنفيذ. احفظ المستودع، وعند الطلب ننفّذ هذه الخطوات مباشرة.

## القرار المعتمد
- **بنية موحّدة:** Django + SQLite + بوت الواتساب **كلها على VPS واحد** (بايثون أني وير يُلغى)
- **المزوّد المختار:** Oracle Cloud **Always Free** (مجاني دائماً — لا بطاقة تُخصم بعد التحقق)
- ملفات النشر جاهزة في `deploy/` (deploy.sh + systemd + nginx + README.md)

## خطوات أوراكل (تُنفَّذ مرة واحدة يدوياً من المستخدم)
1. التسجيل: https://signup.cloud.oracle.com (بريد + هاتف + بطاقة تحقق فقط)
2. **Home Region:** `Germany Frankfurt` أو `Netherlands Amsterdam` (لا يتغير بعدها)
3. إنشاء Instance:
   - Image: **Ubuntu 24.04 aarch64** (Always Free eligible)
   - Shape: **Ampere VM.Standard.A1.Flex → 1 OCPU + 4 GB**
   - Boot volume: 50 GB
   - Public IPv4: مفعّل
4. فتح المنافذ (Security List): 22 + 80 + 443 من 0.0.0.0/0
5. عند «Out of capacity»: تغيير المنطقة/إعادة المحاولة لاحقاً

## مفاتيح SSH
- على جهاز المستخدم (Windows):
  `ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\oci_key -N '""'`
- المفتاح العام يُلصق عند إنشاء الـ Instance
- الاتصال: `ssh -i $env:USERPROFILE\.ssh\oci_key ubuntu@<IP>`

## ما يجب رفعه يدوياً (غير في GitHub — سرّي)
| الملف | المصدر | ملاحظة |
|---|---|---|
| `whatsapp-bot/.env` | جهاز المستخدم | GEMINI_API_KEY + GROUP_JID |
| `whatsapp-bot/auth_session/` | جهاز المستخدم | جلسة واتساب — تغني عن مسح QR |
| `db.sqlite3` | جهاز المستخدم أو GitHub | موجود في git أصلاً |

## أمر النشر على السيرفر
```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/aklibya-g/aklibya-webapp.git /var/www/webapp
# رفع الملفات اليدوية عبر scp من جهاز المستخدم ثم:
cd /var/www/webapp && chmod +x deploy/deploy.sh && sudo deploy/deploy.sh
```
- سكربت النشر: Python تلقائي (3.11 أو 3.12)، Node 20، nginx، pm2، ufw
- الموقع: `http://<IP>/` — دخول: `admin` / `shaban`

## بعد النقل
1. **إيقاف البوت على جهاز المستخدم المحلي** (لا تشغيل نسختين)
2. التحقق: `pm2 list` (wabot متصل) + فتح الموقع
3. بايثون أني وير يُوقف/يُلغى

## أوامر يومية على السيرفر
```bash
systemctl status webapp      # الموقع
pm2 logs wabot               # سجل البوت
pm2 restart wabot            # إعادة البوت
systemctl restart webapp     # إعادة الموقع
```

## ملاحظات لاحقة
- نطاق + HTTPS: `certbot` + `server_name` في `deploy/nginx.conf`
- احتياطي `db.sqlite3` بـ cron خارج السيرفر
- البوت يعمل 24/7 ⇒ حفظ حوالات الصادرة عند أي انقطاع نت محلي
