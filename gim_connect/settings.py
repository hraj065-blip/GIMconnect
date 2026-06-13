"""
Django settings for gim_connect project.
Django 5.2 · Python 3.11+

Environment variables (set in .env for local, in Vercel/Railway dashboard for prod):

  DJANGO_SECRET_KEY          — required in production (long random string)
  DJANGO_DEBUG               — 'True' for local dev only, never in prod
  DJANGO_ALLOWED_HOSTS       — comma-separated hostnames, e.g. gimconnect.vercel.app
  DATABASE_URL               — full Postgres URL, e.g. postgres://user:pass@host/db
  DJANGO_SECURE_SSL_REDIRECT — '1' (default in prod) or '0' to disable
  DJANGO_SECURE_HSTS_SECONDS — integer seconds, default 3600; set 31536000 once stable
  DJANGO_CACHE_URL           — Redis URL for cache + rate-limiting (optional, falls back
                               to LocMemCache for single-process deployments)

  EMAIL_BACKEND              — full dotted path; defaults to console backend in dev
  EMAIL_HOST                 — SMTP host, default smtp.gmail.com
  EMAIL_PORT                 — SMTP port, default 587
  EMAIL_USE_TLS              — 'True'/'False', default True
  EMAIL_HOST_USER            — SMTP username / Gmail address
  EMAIL_HOST_PASSWORD        — SMTP password or App Password
  DEFAULT_FROM_EMAIL         — display name + address for outbound mail

  GIM_ALLOWED_EMAIL_DOMAINS  — comma-separated list, default 'gim.ac.in'

  CLOUDINARY_URL             — (optional) for cloud media storage on Cloudinary
  AWS_ACCESS_KEY_ID          — (optional) for S3 media storage
  AWS_SECRET_ACCESS_KEY      — (optional) for S3 media storage
  AWS_STORAGE_BUCKET_NAME    — (optional) S3 bucket name
  AWS_S3_REGION_NAME         — (optional) S3 region, default ap-south-1
"""

import os
import dj_database_url
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    # A long fallback is fine for local dev – never reaches production.
    "fallback-local-development-key-change-me-in-production-12345!@#$%",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

# Base hosts that are always valid.
_BASE_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

# Extra hosts injected via env (comma-separated) – used for Vercel / Railway /
# custom domains without changing this file.
_EXTRA_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", ".vercel.app,.railway.app").split(",")
    if h.strip()
]

ALLOWED_HOSTS = _BASE_ALLOWED_HOSTS + _EXTRA_HOSTS

# Allow the Vercel deployment URL automatically when running on Vercel.
_VERCEL_URL = os.environ.get("VERCEL_URL")  # injected by Vercel at build time
if _VERCEL_URL:
    ALLOWED_HOSTS.append(_VERCEL_URL)


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    # Django core
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    # "storages",   # uncomment when using django-storages for S3 / Cloudinary
    # Project app
    "connect",
]


# ---------------------------------------------------------------------------
# Middleware  (order matters – WhiteNoise must come right after SecurityMiddleware)
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",          # static files in prod
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ---------------------------------------------------------------------------
# URL / WSGI
# ---------------------------------------------------------------------------

ROOT_URLCONF = "gim_connect.urls"
WSGI_APPLICATION = "gim_connect.wsgi.application"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,       # keep connections alive for 10 min
        conn_health_checks=True,  # auto-discard stale connections
        ssl_require=not DEBUG,  # enforce SSL in production
    )
}


# ---------------------------------------------------------------------------
# Cache  (used by views.py for timer throttle + poll rate-limiting)
# ---------------------------------------------------------------------------

_CACHE_URL = os.environ.get("DJANGO_CACHE_URL", "")

if _CACHE_URL:
    # Redis (recommended for multi-process/multi-worker deployments):
    #   pip install django-redis
    #   DJANGO_CACHE_URL=redis://:<password>@<host>:6379/0
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": _CACHE_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "SOCKET_CONNECT_TIMEOUT": 5,
                "SOCKET_TIMEOUT": 5,
                "IGNORE_EXCEPTIONS": True,  # degrade gracefully if Redis is down
            },
            "KEY_PREFIX": "gimconnect",
            "TIMEOUT": 300,
        }
    }
else:
    # LocMemCache is process-local — fine for single-worker dev/Vercel serverless.
    # Note: timer throttle and rate limits won't be shared across workers with this.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "gimconnect-default",
        }
    }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "connect.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},   # bumped from default 8
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Session hardening
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14   # 14 days
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = False


# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static files  (WhiteNoise serves compressed + cached files in production)
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# ---------------------------------------------------------------------------
# Media files  (user-uploaded photos)
#
# For production you MUST use a persistent store – Vercel's filesystem is
# ephemeral and will lose uploads on redeploy.
#
# Option A – Cloudinary (easiest free tier):
#   pip install cloudinary django-cloudinary-storage
#   add 'cloudinary_storage' + 'cloudinary' to INSTALLED_APPS (before staticfiles)
#   set CLOUDINARY_URL env var
#
# Option B – AWS S3:
#   pip install boto3 django-storages
#   add 'storages' to INSTALLED_APPS
#   set AWS_* env vars below
# ---------------------------------------------------------------------------

_CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "")
_AWS_BUCKET = os.environ.get("AWS_STORAGE_BUCKET_NAME", "")

if _CLOUDINARY_URL:
    # Cloudinary media storage
    DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
    CLOUDINARY_STORAGE = {"CLOUDINARY_URL": _CLOUDINARY_URL}
    MEDIA_URL = "/media/"      # Cloudinary returns absolute URLs; this is a placeholder
    MEDIA_ROOT = BASE_DIR / "media"

elif _AWS_BUCKET:
    # S3 media storage via django-storages
    DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = _AWS_BUCKET
    AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "ap-south-1")
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = "private"
    AWS_S3_CUSTOM_DOMAIN = os.environ.get("AWS_S3_CUSTOM_DOMAIN", "")
    AWS_QUERYSTRING_AUTH = True      # signed URLs (photos are private)
    AWS_QUERYSTRING_EXPIRE = 600     # signed URL valid for 10 minutes
    MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN or f'{_AWS_BUCKET}.s3.amazonaws.com'}/"
    MEDIA_ROOT = ""

else:
    # Local filesystem – fine for dev, NOT suitable for Vercel production.
    MEDIA_URL = "media/"
    MEDIA_ROOT = BASE_DIR / "media"


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

# EMAIL_BACKEND is declared once here.  Duplicate declaration removed from
# the original file (the second assignment at the bottom was silently winning).
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    # Use console backend in dev so no real emails are sent.
    "django.core.mail.backends.console.EmailBackend" if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)

EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = 10   # seconds – prevents hung requests on SMTP failure

DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL",
    "GIM Connect <GIMconnect4@gmail.com>",
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL   # used for error emails to ADMINS


# ---------------------------------------------------------------------------
# GIM-specific app config
# ---------------------------------------------------------------------------

# Comma-separated list of allowed signup email domains.
# Override via env: GIM_ALLOWED_EMAIL_DOMAINS=gim.ac.in,student.gim.ac.in
GIM_ALLOWED_EMAIL_DOMAINS = [
    value.strip().lower()
    for value in os.environ.get("GIM_ALLOWED_EMAIL_DOMAINS", "gim.ac.in").split(",")
    if value.strip()
]

AI_BOOTSTRAP_ENABLED = False


# ---------------------------------------------------------------------------
# Logging  (structured, goes to stdout for log aggregators like Datadog/Logtail)
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
        "require_debug_true":  {"()": "django.utils.log.RequireDebugTrue"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "console_debug": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "filters": ["require_debug_true"],
        },
        "mail_admins": {
            "class": "django.utils.log.AdminEmailHandler",
            "level": "ERROR",
            "filters": ["require_debug_false"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "mail_admins"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console", "mail_admins"],
            "level": "ERROR",
            "propagate": False,
        },
        "connect": {
            # App-level logger used by views.py, services.py, etc.
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}


# ---------------------------------------------------------------------------
# Security hardening  (production only)
# ---------------------------------------------------------------------------

if not DEBUG:
    # Reverse-proxy headers (Vercel / Railway / nginx all set this)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # Redirect all HTTP → HTTPS.  Set to '0' only if your proxy handles it.
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"

    # Cookies must travel over HTTPS only
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # HSTS – start at 1 h, increase to 1 year once confirmed stable
    SECURE_HSTS_SECONDS = int(
        os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "3600")
    )
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False   # flip to True only after testing with HSTS Preload List

    # Additional hardening headers
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True    # legacy IE; harmless on modern browsers
    X_FRAME_OPTIONS = "DENY"

    # Referrer policy
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"


# ---------------------------------------------------------------------------
# Default primary key
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'connect',
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
]

ROOT_URLCONF = 'gim_connect.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'gim_connect.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# We use dj_database_url to automatically connect to our free cloud database later
DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get('DATABASE_URL'),
        conn_max_age=600
    )
}

# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

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
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Kolkata'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

AUTH_USER_MODEL = "connect.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "GIM Connect <noreply@gimconnect.local>")
GIM_ALLOWED_EMAIL_DOMAINS = [
    value.strip().lower()
    for value in os.environ.get("GIM_ALLOWED_EMAIL_DOMAINS", "gim.ac.in").split(",")
    if value.strip()
]
AI_BOOTSTRAP_ENABLED = False

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "3600"))

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "GIMconnect4@gmail.com")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "GIM Connect <GIMconnect4@gmail.com>")
