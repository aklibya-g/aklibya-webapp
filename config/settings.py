import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-z+pn5wu%blgnw9x*4gj87l(ksli1_%)eg59rp2#uzaxhpoqw)y')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = [
    "https://web-production-5df54.up.railway.app",
    "https://webapp3.fly.dev",
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'access_app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'access_app.middleware.MaintenanceMiddleware',
    'access_app.middleware.CorsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'access_app.context_processors.carryover_reminder',
                'access_app.context_processors.current_user_processor',
                'access_app.context_processors.maintenance_processor',
                'access_app.context_processors.import_alerts_processor',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASE_PATH = os.environ.get('DATABASE_PATH', str(BASE_DIR / 'db.sqlite3'))
if os.path.exists('/data'):
    DATABASE_PATH = '/data/db.sqlite3'

# قاعدة البيانات: PostgreSQL عبر DATABASE_URL (للإنتاج/السيرفر)، وإلا SQLite افتراضيًا (تطوير محلي)
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()

if DATABASE_URL:
    import dj_database_url

    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': DATABASE_PATH,
            'OPTIONS': {'timeout': 20},
        }
    }

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'ar'
TIME_ZONE = 'Africa/Tripoli'
USE_I18N = True
USE_TZ = False

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
STATICFILES_STORAGE = 'whitenoise.storage.StaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
