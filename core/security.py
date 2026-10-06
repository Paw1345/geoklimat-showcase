from datetime import timedelta
from ipaddress import ip_address

from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.deprecation import MiddlewareMixin

from core.models import LoginWindow

WINDOW_SECONDS = 300
MAX_ATTEMPTS = 30


@transaction.atomic
def reserve_attempt(address, now=None):
    now = now or timezone.now()
    try:
        address = str(ip_address(address))
    except ValueError:
        address = "unknown"
    bucket = int(now.timestamp()) // WINDOW_SECONDS
    key = salted_hmac("geoklimat-login", f"{address}:{bucket}", algorithm="sha256").hexdigest()
    expiry = now + timedelta(seconds=WINDOW_SECONDS - int(now.timestamp()) % WINDOW_SECONDS)
    row, _ = LoginWindow.objects.get_or_create(key=key, defaults={"expires_at": expiry})
    row = LoginWindow.objects.select_for_update().get(pk=row.pk)
    if row.attempts >= MAX_ATTEMPTS:
        return max(1, int((row.expires_at - now).total_seconds()))
    row.attempts += 1
    row.save(update_fields=["attempts"])
    return 0


class BrowserProtectionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; object-src 'none'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response


class LoginRateMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method != "POST" or request.path_info not in {
            "/accounts/login/",
            "/admin/login/",
        }:
            return None
        # Never trust user-supplied forwarding headers. Proxy deployment needs edge limits.
        try:
            wait = reserve_attempt(request.META.get("REMOTE_ADDR", ""))
        except DatabaseError:
            response = HttpResponse("Logowanie chwilowo niedostępne.", status=503)
        else:
            if not wait:
                return None
            response = HttpResponse(
                "Zbyt wiele prób logowania. Spróbuj ponownie później.", status=429
            )
            response["Retry-After"] = str(wait)
        response["Cache-Control"] = "no-store"
        return response
