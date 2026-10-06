import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

if len(SECRET_KEY) < 50 or SECRET_KEY.startswith(("REPLACE", "development")):  # noqa: F405
    raise ImproperlyConfigured("Set a random DJANGO_SECRET_KEY of at least 50 characters.")
if not os.environ.get("DJANGO_ALLOWED_HOSTS") or "*" in ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("Set explicit DJANGO_ALLOWED_HOSTS.")
if not os.environ.get("POSTGRES_PASSWORD") or os.environ["POSTGRES_PASSWORD"].startswith(
    ("REPLACE", "local-development")
):
    raise ImproperlyConfigured("Set a production POSTGRES_PASSWORD.")
CSRF_TRUSTED_ORIGINS = [
    value for value in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if value
]
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^health/live/$"]
# The trusted reverse proxy must overwrite, never append, this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
