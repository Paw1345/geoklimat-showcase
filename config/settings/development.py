from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = SECRET_KEY or "development-only-not-for-production"  # noqa: F405
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# Django runserver serves development assets; WhiteNoise handles built production assets.
MIDDLEWARE = [
    item
    for item in MIDDLEWARE  # noqa: F405
    if item != "whitenoise.middleware.WhiteNoiseMiddleware"
]
