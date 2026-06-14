"""
Django settings for gim_connect project.
Django 5.2 · Python 3.11+
"""

import os
import dj_database_url
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths & Core
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "fallback-local-development-key-change-me-in-production-12345!@#$%",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

_BASE_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]
_EXTRA_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", ".vercel.app,.railway.app").split(",")
    if h.strip()
]
ALLOWED_HOSTS = _BASE_ALLOWED_HOSTS + _EXTRA_HOSTS

_VERCEL_URL = os.environ.get("VERCEL_URL")
if _VERCEL_URL:
    ALLOWED_HOSTS.append(_VERCEL_URL)

# ---------------------------------------------------------------------------
# Applications & Middleware
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "cloudinary_storage",          # <--- CLOUDINARY STORAGE
    "django.contrib.staticfiles",
    "cloudinary",                  # <--- CLOUDINARY API
    "connect",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # <--- WhiteNoise serves the CSS
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gim_connect.urls"
WSGI_APPLICATION = "gim_connect.wsgi.application"

# ---------------------------------------------------------------------------
# Templates & Database
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

# conn_max_age=0 prevents Vercel Serverless from exhausting DB connections
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=0,
        ssl_require=not DEBUG,
    )
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
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = False

# ---------------------------------------------------------------------------
# Internationalisation & Static Files
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"

_static_src = BASE_DIR / "static"
STATICFILES_DIRS = [_static_src] if _static_src.exists() else []

STATIC_ROOT = BASE_DIR / "staticfiles"

# Forces WhiteNoise to dynamically scan your code instead of looking for a missing folder
WHITENOISE_USE_FINDERS = True

# ---------------------------------------------------------------------------
# Media & Storage (Cloudinary config)
# ---------------------------------------------------------------------------

STORAGES = {
    "default": {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage", # <--- THIS WAS THE MISSING PIECE
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Cloudinary settings for saving user selfies
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME'),
    'API_KEY': os.environ.get('CLOUDINARY_API_KEY'),
    'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET'),
}

# ---------------------------------------------------------------------------
# Email Configuration
# ---------------------------------------------------------------------------

EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND") or "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST") or "smtp.gmail.com"

raw_port = os.environ.get("EMAIL_PORT")
EMAIL_PORT = int(raw_port) if raw_port and raw_port.isdigit() else 587

EMAIL_USE_TLS = (os.environ.get("EMAIL_USE_TLS") or "True").lower() == "true"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER") or "gimconnect4@gmail.com"
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD") or ""
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL") or "GIM Connect <gimconnect4@gmail.com>"
SERVER_EMAIL = DEFAULT_FROM_EMAIL

GIM_ALLOWED_EMAIL_DOMAINS = [
    value.strip().lower()
    for value in os.environ.get("GIM_ALLOWED_EMAIL_DOMAINS", "gim.ac.in").split(",")
    if value.strip()
]

AI_BOOTSTRAP_ENABLED = False

# ---------------------------------------------------------------------------
# CSRF Trusted Origins (Crucial for Vercel Forms)
# ---------------------------------------------------------------------------

CSRF_TRUSTED_ORIGINS = [
    "https://*.vercel.app",
]
if _VERCEL_URL:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_VERCEL_URL}")

_custom_domain = os.environ.get("CUSTOM_DOMAIN")
if _custom_domain:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_custom_domain}")

# ---------------------------------------------------------------------------
# Security Hardening (Production Only)
# ---------------------------------------------------------------------------

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
