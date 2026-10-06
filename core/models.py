from django.db import models


class LoginWindow(models.Model):
    """One pseudonymous client key per fixed window, shared by all web workers."""

    key = models.CharField(max_length=64, primary_key=True)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
