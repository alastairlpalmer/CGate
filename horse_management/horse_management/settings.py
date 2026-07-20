"""
Django settings for horse_management project.
"""

import os
from pathlib import Path

import environ
from celery.schedules import crontab

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize environment variables
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ['localhost', '127.0.0.1']),
)

# Read .env file if it exists
env_file = BASE_DIR / '.env'
if env_file.exists():
    environ.Env.read_env(str(env_file))

# SECURITY WARNING: keep the secret key used in production secret!
# No default — the app will refuse to start without a real SECRET_KEY.
SECRET_KEY = env('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DEBUG', default=False)
# Hard-off on Vercel regardless of env: DEBUG=True serves static files through
# Django, renders full tracebacks, and accumulates query logs in memory.
if os.environ.get('VERCEL'):
    DEBUG = False

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1', '.vercel.app'])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[
    'http://localhost:8000',
    'https://*.vercel.app',
    'https://c-gate-ten.vercel.app',
])

# Auto-add Vercel deployment URLs
VERCEL_URL = os.environ.get('VERCEL_URL')
if VERCEL_URL:
    if VERCEL_URL not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(VERCEL_URL)
    CSRF_TRUSTED_ORIGINS.append(f'https://{VERCEL_URL}')

VERCEL_PRODUCTION_URL = os.environ.get('VERCEL_PROJECT_PRODUCTION_URL')
if VERCEL_PRODUCTION_URL:
    if VERCEL_PRODUCTION_URL not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(VERCEL_PRODUCTION_URL)
    CSRF_TRUSTED_ORIGINS.append(f'https://{VERCEL_PRODUCTION_URL}')

# When running on Vercel, always allow any *.vercel.app hostname so branch
# preview deployments work (their auto-generated URLs are not in the env's
# ALLOWED_HOSTS list and VERCEL_URL doesn't always match the browser URL).
if os.environ.get('VERCEL'):
    if '.vercel.app' not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append('.vercel.app')
    if 'https://*.vercel.app' not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append('https://*.vercel.app')

# Auto-add Railway deployment URLs (RAILWAY_* vars are injected by Railway)
RAILWAY_PUBLIC_DOMAIN = os.environ.get('RAILWAY_PUBLIC_DOMAIN')
if RAILWAY_PUBLIC_DOMAIN:
    if RAILWAY_PUBLIC_DOMAIN not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(RAILWAY_PUBLIC_DOMAIN)
    CSRF_TRUSTED_ORIGINS.append(f'https://{RAILWAY_PUBLIC_DOMAIN}')

# Railway's deployment healthcheck sends Host: healthcheck.railway.app
if os.environ.get('RAILWAY_ENVIRONMENT'):
    if 'healthcheck.railway.app' not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append('healthcheck.railway.app')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # Third party
    'django_htmx',
    'crispy_forms',
    'crispy_tailwind',

    # Local apps
    'core.apps.CoreConfig',
    'invoicing.apps.InvoicingConfig',
    'health.apps.HealthConfig',
    'billing.apps.BillingConfig',
    'notifications.apps.NotificationsConfig',
    'xero_integration.apps.XeroIntegrationConfig',
]

# Only add celery apps when not on Vercel (no worker process there)
if not os.environ.get('VERCEL'):
    INSTALLED_APPS += [
        'django_celery_beat',
        'django_celery_results',
    ]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'core.middleware.ServerTimingMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
]

ROOT_URLCONF = 'horse_management.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.permissions.feature_access_context',
            ],
            'loaders': [
                ('django.template.loaders.cached.Loader', [
                    'django.template.loaders.filesystem.Loader',
                    'django.template.loaders.app_directories.Loader',
                ]),
            ] if not DEBUG else [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
        },
    },
]

WSGI_APPLICATION = 'horse_management.wsgi.application'

# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases
DATABASE_URL = env('DATABASE_URL', default=None)

if DATABASE_URL:
    DATABASES = {
        'default': env.db()
    }
    # Persistent connections are safe on always-on hosts (Railway) and save a
    # TLS handshake per request. On serverless (Vercel) they must be 0 —
    # connections can't be reused across invocations and would leak through
    # the Supabase pooler.
    CONN_MAX_AGE = env.int('CONN_MAX_AGE', default=600)
    if os.environ.get('VERCEL'):
        CONN_MAX_AGE = 0
    DATABASES['default']['CONN_MAX_AGE'] = CONN_MAX_AGE
    DATABASES['default']['CONN_HEALTH_CHECKS'] = True
    DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True  # Required for Supabase pooler (pgbouncer transaction mode)
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-gb'
TIME_ZONE = 'Europe/London'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    # Defining STORAGES replaces Django's built-in dict entirely, so the
    # 'default' media storage must be declared too — without it every
    # file upload save raises InvalidStorageError.
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}
WHITENOISE_USE_FINDERS = True
WHITENOISE_MAX_AGE = 31536000 if not DEBUG else 0  # 1 year in production, no cache in dev

# Media files (uploads)
# Point MEDIA_ROOT at a persistent disk in production (e.g. a Railway volume
# mounted at /data — set MEDIA_ROOT=/data/media). Defaults to the local
# media/ folder for development.
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(env('MEDIA_ROOT', default=str(BASE_DIR / 'media')))

# Serve media files through Django even when DEBUG=False. WhiteNoise only
# handles static files, so on hosts without an object store / CDN in front
# (e.g. Railway with a volume) set SERVE_MEDIA=True. Off by default; never
# needed on Vercel.
SERVE_MEDIA = env.bool('SERVE_MEDIA', default=False)

# Upload limits — 10MB max per file, 12MB max request body
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024   # 12MB

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Crispy forms
CRISPY_ALLOWED_TEMPLATE_PACKS = 'tailwind'
CRISPY_TEMPLATE_PACK = 'tailwind'

# Email settings
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='noreply@yardway.local')

# Xero OAuth2
XERO_CLIENT_ID = env('XERO_CLIENT_ID', default='')
XERO_CLIENT_SECRET = env('XERO_CLIENT_SECRET', default='')
XERO_REDIRECT_URI = env('XERO_REDIRECT_URI', default='')
XERO_SCOPES = 'openid profile email accounting.invoices accounting.contacts offline_access'

# Celery Configuration
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='django-db')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Celery Beat schedule
# ---------------------
# Times are in CELERY_TIMEZONE (Europe/London) and configurable via env vars
# so they can be changed in the Railway dashboard without a deploy:
#   REMINDER_HOUR / REMINDER_MINUTE   — when the reminder emails go out
#   REMINDER_DAYS_OF_WEEK             — crontab day spec, e.g. 'mon-fri' or '*'
#   INVOICE_STATUS_HOUR               — daily invoice status promotion
#   XERO_SYNC_HOUR                    — nightly Xero payment-status sweep
#   MONTHLY_INVOICE_HOUR              — draft generation on the 1st (at :30)
# The reminder tasks are staggered 5 minutes apart from the base time.
#
# NOTE: django-celery-beat's DatabaseScheduler syncs these entries into the
# database when beat starts. Entries named below are re-written from this
# definition on every beat restart, so change the env vars (or this dict)
# rather than editing these rows in the Django admin. Extra schedules added
# in the admin under different names are untouched.
REMINDER_HOUR = env.int('REMINDER_HOUR', default=7)
REMINDER_MINUTE = env.int('REMINDER_MINUTE', default=0)
REMINDER_DAYS_OF_WEEK = env('REMINDER_DAYS_OF_WEEK', default='mon-fri')
INVOICE_STATUS_HOUR = env.int('INVOICE_STATUS_HOUR', default=6)
XERO_SYNC_HOUR = env.int('XERO_SYNC_HOUR', default=5)
MONTHLY_INVOICE_HOUR = env.int('MONTHLY_INVOICE_HOUR', default=5)

CELERY_BEAT_SCHEDULE = {
    # Poll Xero for payments on pushed invoices. Runs daily before the
    # invoice-status promotion and reminder windows, so freshly-paid
    # invoices are marked paid before any overdue email could go out.
    'sync-xero-invoice-statuses': {
        'task': 'xero_integration.tasks.sync_xero_invoice_statuses',
        'schedule': crontab(hour=XERO_SYNC_HOUR, minute=0),
    },
    # Create draft invoices for the month just ended, on the 1st.
    # Duplicate-safe (already-invoiced owners are skipped) and drafts-only;
    # can be disabled in Settings (auto_generate_invoices).
    'generate-monthly-invoices': {
        'task': 'invoicing.tasks.generate_monthly_draft_invoices',
        'schedule': crontab(day_of_month='1', hour=MONTHLY_INVOICE_HOUR, minute=30),
    },
    # Promote SENT invoices past their due date to OVERDUE. Runs every day
    # (including weekends) before the reminder window.
    'check-invoice-status': {
        'task': 'notifications.tasks.check_invoice_status',
        'schedule': crontab(hour=INVOICE_STATUS_HOUR, minute=0),
    },
    'send-vaccination-reminders': {
        'task': 'notifications.tasks.send_vaccination_reminders',
        'schedule': crontab(
            hour=REMINDER_HOUR, minute=REMINDER_MINUTE,
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
    'send-farrier-reminders': {
        'task': 'notifications.tasks.send_farrier_reminders',
        'schedule': crontab(
            hour=REMINDER_HOUR, minute=(REMINDER_MINUTE + 5) % 60,
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
    'send-overdue-invoice-reminders': {
        'task': 'notifications.tasks.send_overdue_invoice_reminders',
        'schedule': crontab(
            hour=REMINDER_HOUR, minute=(REMINDER_MINUTE + 10) % 60,
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
    'send-ehv-reminders': {
        'task': 'notifications.tasks.send_ehv_reminders',
        'schedule': crontab(
            hour=REMINDER_HOUR, minute=(REMINDER_MINUTE + 15) % 60,
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
    # Documents (passports, insurance) expiring within 30 days — one summary
    # email to the business address, one reminder per document.
    'send-document-expiry-reminders': {
        'task': 'notifications.tasks.send_document_expiry_reminders',
        'schedule': crontab(
            hour=REMINDER_HOUR, minute=(REMINDER_MINUTE + 20) % 60,
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
}

# Login settings
# Users can sign in with their email address (or legacy username).
AUTHENTICATION_BACKENDS = ['core.auth_backends.EmailOrUsernameBackend']
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'
LOGIN_URL = '/accounts/login/'

# Session security
SESSION_COOKIE_AGE = 2592000  # 30 days
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = True  # rolling expiry — each request resets the 30-day window
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

# CSRF cookie
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'

# Clickjacking protection
X_FRAME_OPTIONS = 'DENY'

# Logging — surface slow-request warnings AND unhandled-exception
# tracebacks in the host's console logs. Django's default console handler
# only fires when DEBUG=True, which leaves production 500s invisible.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'performance': {
            'handlers': ['console'],
            'level': 'WARNING',
        },
        'core': {
            'handlers': ['console'],
            'level': 'INFO',
        },
    },
}

# Security settings for production (applied when DEBUG=False)
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # SSL redirect disabled - Vercel handles HTTPS at the edge
    SECURE_SSL_REDIRECT = False
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Debug toolbar (only in DEBUG mode)
if DEBUG:
    INSTALLED_APPS += ['debug_toolbar']
    MIDDLEWARE.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')
    INTERNAL_IPS = ['127.0.0.1']
# QA runs: keep DEBUG on but drop the toolbar so it can't intercept clicks.
if DEBUG and os.environ.get('QA_NO_TOOLBAR'):
    INSTALLED_APPS = [a for a in INSTALLED_APPS if a != 'debug_toolbar']
    MIDDLEWARE = [m for m in MIDDLEWARE if 'debug_toolbar' not in m]
