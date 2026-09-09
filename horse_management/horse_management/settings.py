"""
Django settings for horse_management project.
"""

import os
from datetime import timedelta
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
    'axes',

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
    # Compress responses: the map pages carry field geometry as JSON and
    # gzip cuts them to a fifth. Django masks the CSRF token per request,
    # which is what makes compressing pages with forms safe (BREACH).
    'django.middleware.gzip.GZipMiddleware',
    'core.middleware.ServerTimingMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    # Content-Security-Policy. Report-only for now (see CSP settings below),
    # so it adds a header and never blocks a response.
    'csp.middleware.CSPMiddleware',
    # django-axes must be LAST: it wraps the response to record the outcome
    # of a sign-in attempt, so every other middleware has to have run first.
    'axes.middleware.AxesMiddleware',
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
                'core.analytics.template_context',
                'core.context_processors.location_maps',
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
    if not DEBUG:
        # A missing/typo'd DATABASE_URL on a real host must fail loudly.
        # The old silent SQLite fallback booted "successfully" against a
        # fresh ephemeral file — the app accepted data and lost all of it
        # on the next redeploy.
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured(
            'DATABASE_URL is not set. Production runs (DEBUG=False) must '
            'point at a real database — the SQLite fallback is dev-only.'
        )
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

# Media files (uploads)
# ---------------------
# Two ways to hold uploads, chosen by whether MEDIA_S3_BUCKET is set.
#
# Object store (preferred in production). Uploads go straight to an
# S3-compatible bucket, so no service holds the only copy on a local disk.
# That matters here for a specific reason: a host volume attaches to ONE
# service, so with uploads on a volume the backup job — which runs on the
# worker — cannot read files written by the web service, and quietly backs
# up nothing. An object store is reachable from every service.
#
# Local disk (development, and any deployment that has not moved yet).
# MEDIA_ROOT can point at a mounted volume, e.g. MEDIA_ROOT=/data/media.
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(env('MEDIA_ROOT', default=str(BASE_DIR / 'media')))

# Uploads include passports, insurance documents and vet records. The
# bucket MUST be private: no public development URL, no custom domain.
# Access is through short-lived signed links generated per request, which
# is what querystring_auth below turns on.
MEDIA_S3_BUCKET = env('MEDIA_S3_BUCKET', default='')
MEDIA_S3_ENDPOINT = env('MEDIA_S3_ENDPOINT', default='')
MEDIA_S3_ACCESS_KEY = env('MEDIA_S3_ACCESS_KEY', default='')
MEDIA_S3_SECRET_KEY = env('MEDIA_S3_SECRET_KEY', default='')
MEDIA_S3_REGION = env('MEDIA_S3_REGION', default='auto')
# Seconds a signed media link stays valid. Long enough for a page to load
# and a document to open, short enough that a copied link is not a lasting
# handout of someone's passport.
MEDIA_S3_SIGNED_URL_TTL = env.int('MEDIA_S3_SIGNED_URL_TTL', default=900)

MEDIA_ON_S3 = bool(MEDIA_S3_BUCKET and MEDIA_S3_ACCESS_KEY and MEDIA_S3_SECRET_KEY)

if MEDIA_ON_S3:
    _media_storage = {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'bucket_name': MEDIA_S3_BUCKET,
            # Cloudflare R2 and Backblaze B2 need an endpoint; plain AWS S3
            # works it out from the region.
            'endpoint_url': MEDIA_S3_ENDPOINT or None,
            'access_key': MEDIA_S3_ACCESS_KEY,
            'secret_key': MEDIA_S3_SECRET_KEY,
            'region_name': MEDIA_S3_REGION,
            # R2 has no ACL support, and an ACL is the wrong tool anyway —
            # the bucket is private and links are signed. Sending one makes
            # R2 reject the upload outright.
            'default_acl': None,
            'querystring_auth': True,
            'querystring_expire': MEDIA_S3_SIGNED_URL_TTL,
            # Keep Django's collision handling: a second upload of the same
            # name gets a suffix rather than overwriting the first.
            'file_overwrite': False,
            'signature_version': 's3v4',
            'addressing_style': 'path',
        },
    }
else:
    _media_storage = {'BACKEND': 'django.core.files.storage.FileSystemStorage'}

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    # Defining STORAGES replaces Django's built-in dict entirely, so the
    # 'default' media storage must be declared too — without it every
    # file upload save raises InvalidStorageError.
    'default': _media_storage,
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}
WHITENOISE_USE_FINDERS = True
WHITENOISE_MAX_AGE = 31536000 if not DEBUG else 0  # 1 year in production, no cache in dev

# Serve media files through Django even when DEBUG=False. WhiteNoise only
# handles static files, so on hosts without an object store / CDN in front
# (e.g. Railway with a volume) set SERVE_MEDIA=True. Off by default; never
# needed on Vercel.
#
# Forced off once uploads are on S3: there is no local file to serve, and
# leaving the route wired would answer every media URL with a 404 that
# looks like a missing document rather than a misconfiguration.
SERVE_MEDIA = env.bool('SERVE_MEDIA', default=False) and not MEDIA_ON_S3

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
if not DEBUG and EMAIL_BACKEND == 'django.core.mail.backends.console.EmailBackend':
    # The console backend "succeeds" into worker logs: invoices get marked
    # SENT and reminders count as delivered while nobody receives anything.
    # Keep booting (some deploys genuinely run email-less) but say so loudly.
    import logging as _logging
    _logging.getLogger(__name__).warning(
        'EMAIL_BACKEND is the console backend with DEBUG=False — outgoing '
        'email (invoices, reminders) is NOT being delivered. Set '
        'EMAIL_BACKEND/EMAIL_HOST for real delivery.'
    )
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
# Without a timeout a wedged SMTP connection blocks the reminder loop (and
# its Celery worker slot) until the task time limit kills it.
EMAIL_TIMEOUT = env.int('EMAIL_TIMEOUT', default=30)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='noreply@yardway.local')

# Off-site backups (core/backup/)
# ------------------------------
# A database backup does NOT include the uploaded media — horse photos,
# passports, insurance documents and receipts live on a volume. Both are
# backed up here, and both are needed for a restore that works.
#
# Use an object store from a DIFFERENT provider than the database. One
# provider is one suspended account away from losing both copies.
BACKUP_ENABLED = env.bool('BACKUP_ENABLED', default=False)
BACKUP_INCLUDE_MEDIA = env.bool('BACKUP_INCLUDE_MEDIA', default=True)

# S3-compatible destination. Cloudflare R2 and Backblaze B2 both work.
# Leave BACKUP_S3_ENDPOINT empty for plain AWS S3.
BACKUP_S3_ENDPOINT = env('BACKUP_S3_ENDPOINT', default='')
BACKUP_S3_BUCKET = env('BACKUP_S3_BUCKET', default='')
BACKUP_S3_ACCESS_KEY = env('BACKUP_S3_ACCESS_KEY', default='')
BACKUP_S3_SECRET_KEY = env('BACKUP_S3_SECRET_KEY', default='')
BACKUP_S3_REGION = env('BACKUP_S3_REGION', default='auto')

# pg_dump connects with this if set. Useful when the app talks to the
# database through a connection pooler (Supabase's pgbouncer) that
# pg_dump cannot use — point this at the direct connection instead.
BACKUP_DATABASE_URL = env('BACKUP_DATABASE_URL', default='')

# The restore test (core/backup/restore.py). Both must name somewhere
# scratch: the module refuses to run if the database matches DATABASE_URL
# or BACKUP_DATABASE_URL by host and name, or if the bucket is the live
# media or backup one. There is no default and no fallback — a restore
# overwrites whatever it is aimed at, so it must be aimed deliberately.
RESTORE_TEST_DATABASE_URL = env('RESTORE_TEST_DATABASE_URL', default='')
RESTORE_TEST_MEDIA_BUCKET = env('RESTORE_TEST_MEDIA_BUCKET', default='')

# Seconds. A yard-sized database dumps in seconds; this is the backstop
# for a wedged connection, not a target.
BACKUP_PG_DUMP_TIMEOUT = env.int('BACKUP_PG_DUMP_TIMEOUT', default=1800)

# Grandfather-father-son retention: every backup from the last week, then
# one a week, then one a month. Answers both "undo yesterday" and "we only
# noticed two months later" without keeping 365 copies.
BACKUP_KEEP_DAILY = env.int('BACKUP_KEEP_DAILY', default=7)
BACKUP_KEEP_WEEKLY = env.int('BACKUP_KEEP_WEEKLY', default=4)
BACKUP_KEEP_MONTHLY = env.int('BACKUP_KEEP_MONTHLY', default=12)

# When the nightly backup runs (Europe/London). Before the 05:00 Xero
# sweep and the 06:00-07:00 invoice and reminder window, so the copy is of
# a quiet database.
BACKUP_HOUR = env.int('BACKUP_HOUR', default=2)

# Error monitoring (Sentry)
# ------------------------
# Off entirely until SENTRY_DSN is set, so local runs and the test suite
# report nothing. See core/monitoring.py for what is scrubbed before an
# event leaves this process — the app holds personal data and the default
# Sentry configuration would send more of it than is acceptable.
SENTRY_DSN = env('SENTRY_DSN', default='')

# Which deployment an error came from. Without this, a staging stack trace
# and a production one land in the same list and read the same.
SENTRY_ENVIRONMENT = env(
    'SENTRY_ENVIRONMENT',
    default='production' if not DEBUG else 'development',
)

# Ties an error to the commit that caused it. Railway injects the SHA, so
# this needs no configuration there.
SENTRY_RELEASE = env(
    'SENTRY_RELEASE',
    default=os.environ.get('RAILWAY_GIT_COMMIT_SHA', ''),
)

# Performance tracing. 0.0 = errors only, which is what a yard-sized app
# needs; tracing every request burns the free-tier quota in days and the
# ServerTimingMiddleware already reports slow requests.
SENTRY_TRACES_SAMPLE_RATE = env.float('SENTRY_TRACES_SAMPLE_RATE', default=0.0)

# Encryption of secrets stored in the database (core/encryption.py).
# Comma-separated Fernet keys, newest first. The first key encrypts; all of
# them are tried when decrypting, which is what allows rotation. Leave it
# empty to derive a key from SECRET_KEY instead.
FIELD_ENCRYPTION_KEYS = env.list('FIELD_ENCRYPTION_KEYS', default=[])

# Xero OAuth2
XERO_CLIENT_ID = env('XERO_CLIENT_ID', default='')
XERO_CLIENT_SECRET = env('XERO_CLIENT_SECRET', default='')
XERO_REDIRECT_URI = env('XERO_REDIRECT_URI', default='')
XERO_SCOPES = 'openid profile email accounting.invoices accounting.contacts offline_access'

# Product analytics (PostHog)
# --------------------------
# Off entirely until POSTHOG_API_KEY is set, so local runs and the test suite
# send nothing. The key is a *public* project key (phc_...) and is embedded in
# the page on purpose — it can only write events, never read them.
POSTHOG_API_KEY = env('POSTHOG_API_KEY', default='')

# EU cloud by default. A UK livery business keeps personal data inside the EEA
# with far less paperwork than the US region. Switch to https://us.i.posthog.com
# (assets https://us-assets.i.posthog.com, UI https://us.posthog.com) if the
# project was created in the US region.
POSTHOG_HOST = env('POSTHOG_HOST', default='https://eu.i.posthog.com')
POSTHOG_ASSET_HOST = env('POSTHOG_ASSET_HOST', default='https://eu-assets.i.posthog.com')
POSTHOG_UI_HOST = env('POSTHOG_UI_HOST', default='https://eu.posthog.com')

# Session replay. Inputs and page text are always masked (see
# templates/includes/posthog.html); this switch turns recording off outright.
POSTHOG_SESSION_RECORDING = env.bool('POSTHOG_SESSION_RECORDING', default=True)

# Usernames in person profiles. Off by default because people can sign in with
# an email address, which is personal data.
POSTHOG_SEND_USERNAMES = env.bool('POSTHOG_SEND_USERNAMES', default=False)

# Send server-side events on the request thread. Needed on serverless hosts,
# where the function freezes before a background flush thread can run.
POSTHOG_SYNC_MODE = env.bool(
    'POSTHOG_SYNC_MODE', default=bool(os.environ.get('VERCEL'))
)

# Verbose posthog-js logging in the browser console.
POSTHOG_DEBUG = env.bool('POSTHOG_DEBUG', default=False)

# Route browser traffic through this app at /ingest/ instead of straight to
# posthog.com. Ad blockers block posthog.com by default, so without this anyone
# running one is invisible. The proxy view is core.views.posthog_proxy; the
# path here must match the route in urls.py.
POSTHOG_PROXY = env.bool('POSTHOG_PROXY', default=True)
POSTHOG_PROXY_PATH = '/ingest'

# Bulk invoice sending: queue one Celery task per invoice instead of
# rendering every PDF and blocking on SMTP inside the request (a 40-owner
# yard could hit the worker timeout part-way through). Off on Vercel, which
# has no worker process — there the bulk action sends inline as before.
INVOICE_SEND_ASYNC = env.bool(
    'INVOICE_SEND_ASYNC', default=not bool(os.environ.get('VERCEL'))
)

# Location mapping (coordinates, the nearest-location chip, the map tab
# and the Near you dashboard card). Off by default in every environment;
# the edit form's coordinate picker and the backfill command work
# regardless, so coordinates can be entered before anything is shown.
LOCATION_MAPS_ENABLED = env.bool('LOCATION_MAPS_ENABLED', default=False)
# Inside this distance of a location's point the nearest-location chip
# names it. Tune per yard once real readings are in.
LOCATION_NEAR_RADIUS_M = env.int('LOCATION_NEAR_RADIUS_M', default=150)

# Celery Configuration
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='django-db')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
# Runaway-task backstop: the longest legitimate task is the nightly Xero
# sweep, which paces itself at ~1 call/second plus up to three 60s
# rate-limit waits — 30 minutes is generous headroom for a large yard while
# still killing a genuinely hung task (e.g. stuck SMTP/HTTP socket) the
# same night instead of stalling the worker indefinitely.
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60
CELERY_TASK_TIME_LIMIT = 30 * 60

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


def _staggered(offset_minutes):
    """Reminder-task stagger with hour carry.

    A plain (minute + offset) % 60 wraps: REMINDER_MINUTE=56 would run the
    'later' tasks up to 55 minutes *earlier* than the vaccination task.
    """
    total = REMINDER_HOUR * 60 + REMINDER_MINUTE + offset_minutes
    return {'hour': (total // 60) % 24, 'minute': total % 60}
REMINDER_DAYS_OF_WEEK = env('REMINDER_DAYS_OF_WEEK', default='mon-fri')
INVOICE_STATUS_HOUR = env.int('INVOICE_STATUS_HOUR', default=6)
XERO_SYNC_HOUR = env.int('XERO_SYNC_HOUR', default=5)
MONTHLY_INVOICE_HOUR = env.int('MONTHLY_INVOICE_HOUR', default=5)

CELERY_BEAT_SCHEDULE = {
    # Off-site backup of the database and the uploaded media. First in the
    # night's run so it copies a quiet database, before the Xero sweep and
    # the invoice and reminder window touch anything. A no-op unless
    # BACKUP_ENABLED is set.
    'nightly-backup': {
        'task': 'core.tasks.run_nightly_backup',
        'schedule': crontab(hour=BACKUP_HOUR, minute=0),
    },
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
            **_staggered(5),
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
    'send-overdue-invoice-reminders': {
        'task': 'notifications.tasks.send_overdue_invoice_reminders',
        'schedule': crontab(
            **_staggered(10),
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
    'send-ehv-reminders': {
        'task': 'notifications.tasks.send_ehv_reminders',
        'schedule': crontab(
            **_staggered(15),
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
    'send-breeding-reminders': {
        'task': 'notifications.tasks.send_breeding_reminders',
        'schedule': crontab(
            **_staggered(17),
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
    # Documents (passports, insurance) expiring within 30 days — one summary
    # email to the business address, one reminder per document.
    'send-document-expiry-reminders': {
        'task': 'notifications.tasks.send_document_expiry_reminders',
        'schedule': crontab(
            **_staggered(20),
            day_of_week=REMINDER_DAYS_OF_WEEK,
        ),
    },
}

# Login settings
# Users can sign in with their email address (or legacy username).
# AxesStandaloneBackend must come first. It refuses a locked-out identity
# before any password is checked, so a lockout cannot be bypassed by a
# backend further down the list.
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'core.auth_backends.EmailOrUsernameBackend',
]
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'
LOGIN_URL = '/accounts/login/'

# Where the Django admin is mounted.
# ----------------------------------
# The admin is the one place a single compromised account reaches every
# table at once, and at the default path every scanner on the internet
# already knows to try it. Moving it does not make the admin safe — the
# password and the role checks still do that — but it takes the site out
# of the untargeted sweeps that fill the django-axes lockout table.
#
# Set ADMIN_URL in production to something unguessable, e.g.
# ADMIN_URL=yard-office-7f3a/. The default keeps existing links working.
# A trailing slash is added if it is missing; a leading one is stripped,
# because Django's path() wants neither.
ADMIN_URL = env('ADMIN_URL', default='admin/').strip().lstrip('/')
if not ADMIN_URL:
    # An empty value would mount the admin at the site root, shadowing the
    # dashboard. Refuse it rather than serve the admin from '/'.
    ADMIN_URL = 'admin/'
if not ADMIN_URL.endswith('/'):
    ADMIN_URL += '/'

# Session security
# ----------------
# Seven days of inactivity, not thirty. A session cookie is a bearer
# credential: whoever holds it is signed in, with no password prompt. The
# window in which a cookie copied from a shared or lost device still works
# is what this setting controls, and a month of it is more than a yard
# needs — staff who use the app weekly are not signed out.
#
# Rolling expiry stays on, so the seven days count from the last request
# rather than from sign-in. That means an attacker actively using a stolen
# cookie keeps it alive; what this closes is the far more likely case of a
# cookie sitting unused on a device somebody else now has.
SESSION_COOKIE_AGE = env.int('SESSION_COOKIE_AGE', default=7 * 24 * 60 * 60)
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = True  # rolling expiry — each request resets the window
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

# CSRF cookie
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'

# Clickjacking protection
X_FRAME_OPTIONS = 'DENY'

# Brute-force protection (django-axes)
# -----------------------------------
# Before this, sign-in accepted unlimited password guesses. A yard's owner
# email addresses are on its invoices, so the username half of a guess is
# not secret and only the password stands in the way.
#
# Locking on the username (not the IP address) is the deliberate choice
# here. The app sits behind Railway and, usually, Cloudflare, so the IP
# address Django sees is a proxy's unless the forwarded-header chain is
# configured exactly right — a lockout keyed on it is either useless (every
# request looks like one IP) or wrong (it locks out a whole office). The
# username is always accurate.
#
# The cost of that choice: someone who knows a colleague's email address can
# lock them out on purpose. AXES_COOLOFF_TIME bounds the damage — the lock
# lifts by itself, and an administrator can clear it sooner with
# `python manage.py axes_reset_username <email>`.
AXES_ENABLED = env.bool('AXES_ENABLED', default=True)
AXES_FAILURE_LIMIT = env.int('AXES_FAILURE_LIMIT', default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int('AXES_COOLOFF_MINUTES', default=30))
AXES_LOCKOUT_PARAMETERS = ['username']
# A correct password clears the counter, so five typos spread over a month
# never add up to a lockout.
AXES_RESET_ON_SUCCESS = True
# Sign-in accepts an email address or a username, case-insensitively (see
# core.auth_backends). Without this, 'Sam@yard.co' and 'sam@yard.co' would
# count as two separate identities and each get its own five attempts.
AXES_USERNAME_FORM_FIELD = 'username'
AXES_USERNAME_CALLABLE = 'core.axes_username.normalise_username'
AXES_LOCKOUT_TEMPLATE = 'registration/lockout.html'
# 429 is the honest status for "too many attempts, try later". The default
# is 403, which reads as "forbidden forever".
AXES_HTTP_RESPONSE_CODE = 429
# axes.W006 warns that AXES_LOCKOUT_PARAMETERS has no 'ip_address', on the
# grounds that an attacker could rotate identifiers to dodge the limit.
# That warning does not apply to a username-only lockout: rotating IP
# address, user agent or cookies changes nothing, because the counter keys
# on the account being attacked. The real trade-off (deliberate lockout of
# a known colleague) is documented above and bounded by AXES_COOLOFF_TIME.
SILENCED_SYSTEM_CHECKS = ['axes.W006']

# Content-Security-Policy (django-csp)
# -----------------------------------
# REPORT-ONLY on purpose. The browser reports what a policy WOULD block and
# blocks nothing, so this cannot break a page. Watch the browser console for
# a week, then move this dict to CONTENT_SECURITY_POLICY to enforce it.
#
# Every script this app serves is self-hosted (htmx, Alpine and Leaflet are
# vendored under static/js), and PostHog is proxied through /ingest/ on this
# origin, so 'self' covers almost everything.
#
# KNOWN WEAK POINT: script-src still allows 'unsafe-inline' and
# 'unsafe-eval'. base.html and six other templates carry inline <script>
# blocks and Django's json_script filter emits more, and Alpine.js evaluates
# its x- attributes with new Function(). Removing these two needs per-tag
# nonces and Alpine's CSP build. Until then the real protection comes from
# the other directives: connect-src and img-src stop an injected script
# sending data anywhere off this origin, and form-action stops it retargeting
# a form post.
CSP_POLICY = {
    'DIRECTIVES': {
        'default-src': ["'self'"],
        'script-src': ["'self'", "'unsafe-inline'", "'unsafe-eval'"],
        # Inline style="..." attributes appear across the templates.
        'style-src': ["'self'", "'unsafe-inline'"],
        # data: for inline SVG icons, blob: for the client-side image
        # preview on photo upload, and the OpenStreetMap tile server for
        # the optional map layer (see static/js/location_map.js).
        'img-src': ["'self'", 'data:', 'blob:', 'https://tile.openstreetmap.org'],
        'font-src': ["'self'"],
        # This is the directive that matters most against an injected
        # script: it decides where the page may send data.
        'connect-src': ["'self'"],
        'frame-src': ["'none'"],
        'object-src': ["'none'"],
        'base-uri': ["'self'"],
        # Stops an injected <form> posting credentials to another host.
        'form-action': ["'self'"],
        # Same intent as X_FRAME_OPTIONS above, in the modern header.
        'frame-ancestors': ["'none'"],
    },
}

# When the PostHog proxy is off, browsers talk to posthog.com directly and
# need those hosts allowed. With the proxy on (the default) they never do.
if POSTHOG_API_KEY and not POSTHOG_PROXY:
    CSP_POLICY['DIRECTIVES']['connect-src'] += [POSTHOG_HOST, POSTHOG_ASSET_HOST]
    CSP_POLICY['DIRECTIVES']['script-src'] += [POSTHOG_ASSET_HOST]

# Set CSP_ENFORCE=True to switch from reporting to blocking. Do that only
# after the report-only console is quiet.
if env.bool('CSP_ENFORCE', default=False):
    CONTENT_SECURITY_POLICY = CSP_POLICY
else:
    CONTENT_SECURITY_POLICY_REPORT_ONLY = CSP_POLICY

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
    # Root logger: every app module (invoicing, notifications, xero_integration,
    # health, billing...) logs through it. Without this only 'django' and
    # 'core' had a handler, so a logger.exception() in a Celery task or a
    # view outside core fell through to Python's last-resort handler —
    # WARNING and above only, and with no formatting.
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'performance': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'core': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# Security settings for production (applied when DEBUG=False)
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Both Railway and Vercel terminate TLS at the edge and forward the
    # original scheme in X-Forwarded-Proto, so this header is what makes
    # request.is_secure() correct behind them. It must be set *before*
    # SECURE_SSL_REDIRECT, or every HTTPS request looks like plain HTTP
    # and redirects to itself forever.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # Redirect plain HTTP to HTTPS. On by default; set SECURE_SSL_REDIRECT=False
    # in the environment to switch it off without a deploy if a proxy in front
    # ever stops sending X-Forwarded-Proto.
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
    # Railway's platform healthcheck reaches the container over plain HTTP
    # with no X-Forwarded-Proto. Without this exemption it gets a 301 and
    # the deploy is marked unhealthy. Paths are regexes without the leading
    # slash. The view returns no data beyond {"status": "ok"}.
    SECURE_REDIRECT_EXEMPT = [r'^_health/$']
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    # Trim the referer sent to third parties (the PostHog CDN, any external
    # link) to the origin. Full URLs here carry record IDs.
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# Debug toolbar (only in DEBUG mode)
if DEBUG:
    INSTALLED_APPS += ['debug_toolbar']
    # After GZipMiddleware, or the toolbar cannot read compressed responses.
    MIDDLEWARE.insert(
        MIDDLEWARE.index('django.middleware.gzip.GZipMiddleware') + 1,
        'debug_toolbar.middleware.DebugToolbarMiddleware',
    )
    INTERNAL_IPS = ['127.0.0.1']
# QA runs: keep DEBUG on but drop the toolbar so it can't intercept clicks.
if DEBUG and os.environ.get('QA_NO_TOOLBAR'):
    INSTALLED_APPS = [a for a in INSTALLED_APPS if a != 'debug_toolbar']
    MIDDLEWARE = [m for m in MIDDLEWARE if 'debug_toolbar' not in m]


# Start Sentry last, so every setting it reads is already defined. A no-op
# when SENTRY_DSN is empty.
from core import monitoring as _monitoring  # noqa: E402

_monitoring.init(
    dsn=SENTRY_DSN,
    environment=SENTRY_ENVIRONMENT,
    release=SENTRY_RELEASE,
    traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
)
