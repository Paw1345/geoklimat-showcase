import io
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError, close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from core.models import LoginWindow
from core.security import MAX_ATTEMPTS, reserve_attempt
from ingestion.models import Source


class SecurityTests(TestCase):
    def test_headers_on_public_private_and_admin(self):
        for path in ["/", "/information/", "/accounts/login/", "/alerts/", "/admin/login/"]:
            response = self.client.get(path)
            self.assertIn("script-src 'self'", response["Content-Security-Policy"])
            self.assertIn("object-src 'none'", response["Content-Security-Policy"])
            self.assertEqual(response["Referrer-Policy"], "same-origin")
            self.assertEqual(response["X-Frame-Options"], "DENY")
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertIn("camera=()", response["Permissions-Policy"])

    def test_login_and_admin_share_limit_ignore_forwarded_header(self):
        now = timezone.now()
        for _ in range(MAX_ATTEMPTS):
            reserve_attempt("127.0.0.1", now)
        with patch("core.security.timezone.now", return_value=now):
            for path in ["/accounts/login/", "/admin/login/"]:
                response = self.client.post(
                    path,
                    {"username": "secret-name", "password": "secret-pass"},
                    HTTP_X_FORWARDED_FOR="198.51.100.45",
                )
                self.assertEqual(response.status_code, 429)
                self.assertGreater(int(response["Retry-After"]), 0)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertNotContains(response, "secret-name", status_code=429)
        self.assertEqual(LoginWindow.objects.count(), 1)
        row = LoginWindow.objects.get()
        self.assertNotIn("127.0.0.1", row.key)
        self.assertEqual(row.attempts, MAX_ATTEMPTS)

    def test_window_expiry_and_other_client(self):
        now = timezone.now()
        for _ in range(MAX_ATTEMPTS):
            reserve_attempt("127.0.0.1", now)
        self.assertGreater(reserve_attempt("127.0.0.1", now), 0)
        self.assertEqual(reserve_attempt("127.0.0.2", now), 0)
        self.assertEqual(reserve_attempt("127.0.0.1", now + timedelta(minutes=5)), 0)

    def test_get_and_invalid_csrf_do_not_create_counter(self):
        self.client.get("/accounts/login/")
        response = Client(enforce_csrf_checks=True).post("/accounts/login/", {})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(LoginWindow.objects.exists())

    def test_login_outage_fails_closed_without_details(self):
        with patch("core.security.reserve_attempt", side_effect=OperationalError("secret-db")):
            response = self.client.post("/accounts/login/", {})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"secret-db", response.content)

    def test_valid_login_works_below_limit(self):
        get_user_model().objects.create_user(username="valid", password="test-only-passphrase")
        response = self.client.post(
            "/accounts/login/",
            {
                "username": "valid",
                "password": "test-only-passphrase",
                "next": "/alerts/",
            },
        )
        self.assertRedirects(response, "/alerts/")
        self.assertEqual(LoginWindow.objects.get().attempts, 1)

    def test_prune_is_dry_default_and_only_old_counters(self):
        old = LoginWindow.objects.create(key="old", expires_at=timezone.now() - timedelta(days=2))
        fresh = LoginWindow.objects.create(key="fresh", expires_at=timezone.now())
        call_command("prune_login_windows", stdout=io.StringIO())
        self.assertTrue(LoginWindow.objects.filter(pk=old.pk).exists())
        call_command("prune_login_windows", apply=True, stdout=io.StringIO())
        self.assertFalse(LoginWindow.objects.filter(pk=old.pk).exists())
        self.assertTrue(LoginWindow.objects.filter(pk=fresh.pk).exists())

    def test_quality_requires_staff_and_json_has_no_secrets(self):
        self.assertEqual(self.client.get("/quality/").status_code, 302)
        user = get_user_model().objects.create_user(username="reader")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/quality/").status_code, 302)
        user.is_staff = True
        user.save()
        Source.objects.create(
            slug="stale",
            name="Old",
            enabled=True,
            url="https://example.org/",
            adapter="feed",
            config={"token": "private-token"},
        )
        response = self.client.get("/quality/?format=json")
        self.assertEqual(response.json()["sources_stale"], 1)
        self.assertEqual(response.json()["sources_missing_reuse_notes"], 1)
        self.assertNotIn(b"private-token", response.content)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.get("/quality/").status_code, 200)
        self.assertEqual(self.client.post("/quality/").status_code, 405)

    def test_information_and_report_command(self):
        self.assertContains(self.client.get("/information/"), "nie zastępuje polityki")
        out = io.StringIO()
        call_command("quality_report", stdout=out)
        self.assertIn("registry_states", out.getvalue())
        self.assertEqual(Source.objects.count(), 0)


class ConcurrentLoginTests(TransactionTestCase):
    def test_concurrent_workers_cannot_exceed_limit(self):
        now = timezone.now()

        def attempt(_):
            close_old_connections()
            try:
                return reserve_attempt("192.0.2.42", now)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(40)))
        self.assertEqual(results.count(0), MAX_ATTEMPTS)
        self.assertEqual(LoginWindow.objects.get().attempts, MAX_ATTEMPTS)
