import os
from pathlib import Path

from ingestion.registry_hosts import HOSTS as REGISTRY_HOSTS

# Logical raw-data cache; PostgreSQL indexes, WAL and backups require extra space.
DATA_RETENTION_DAYS = 30
DATA_STORAGE_MAX_BYTES = 15_000_000_000
DATA_AUTO_MAX_BYTES = 5 * 1024 * 1024
DATA_MANUAL_MAX_BYTES = 50 * 1024 * 1024
LOCAL_AI_ENABLED = os.environ.get("LOCAL_AI_ENABLED") == "1"

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = False
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "ingestion",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.security.BrowserProtectionMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "core.security.LoginRateMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "geoklimat"),
        "USER": os.environ.get("POSTGRES_USER", "geoklimat"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 5},
    }
}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True
LOGIN_REDIRECT_URL = "/changes/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 8 * 60 * 60
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
INGEST_USER_AGENT = os.environ.get("INGEST_USER_AGENT", "GeoKlimatMonitor/0.2 (metadata)")
INGEST_ALLOWED_HOSTS = [
    "www.gov.pl",
    "api.sejm.gov.pl",
    "www.eea.europa.eu",
    "environment.ec.europa.eu",
    "climate.ec.europa.eu",
    "eur-lex.europa.eu",
    "legislacja.rcl.gov.pl",
    "www.geoportal.gov.pl",
    "data.europa.eu",
    "api.tech.ec.europa.eu",  # User-approved public LIFE search; POST remains fixed and scoped.
]
INGEST_ALLOWED_HOSTS = list(set(INGEST_ALLOWED_HOSTS + REGISTRY_HOSTS))
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
