import io
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import OperationalError, close_old_connections, connection, connections
from django.test import Client, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import URLPattern, get_resolver, reverse
from django.utils import timezone

from core.models import LoginWindow
from core.security import MAX_ATTEMPTS, reserve_attempt
from ingestion.models import Classification, Item, RegistryEntry, RegistryRelease, Source


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
            self.assertIn("payment=()", response["Permissions-Policy"])
            self.assertEqual(response["Cross-Origin-Resource-Policy"], "same-origin")
            self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")

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


# S9: every named route has an expected access level. A new route fails the coverage
# test until it is added here, so a forgotten decorator cannot slip in unnoticed.
# public: anonymous allowed; login: any signed-in user; editor: edit_change_threads;
# staff: is_staff; health: probes. Method is the one the view accepts.
ACCESS = {
    "home": ("public", "GET"),
    "information": ("public", "GET"),
    "search": ("public", "GET"),
    "login": ("public", "GET"),
    "logout": ("public", "POST"),
    "legal_catalog": ("public", "GET"),
    "legal_detail": ("public", "GET"),
    "legal_deadlines": ("public", "GET"),
    "change_catalog": ("public", "GET"),
    "change_detail": ("public", "GET"),
    "data_catalog": ("public", "GET"),
    "data_coverage": ("public", "GET"),
    "data_detail": ("public", "GET"),
    "data_download": ("public", "GET"),
    "prtr_search": ("public", "GET"),
    "crfop_search": ("public", "GET"),
    "mwp_search": ("public", "GET"),
    "funding_local_search": ("public", "GET"),
    "registry": ("public", "GET"),
    "registry_sheet": ("public", "GET"),
    "registry_detail": ("public", "GET"),
    "item_history": ("public", "GET"),
    "live": ("health", "GET"),
    "ready": ("health", "GET"),
    "alert_inbox": ("login", "GET"),
    "alert_preferences": ("login", "GET"),
    "alert_detail": ("login", "GET"),
    "alert_read": ("login", "POST"),
    "change_watch": ("login", "POST"),
    "pilot": ("editor", "GET"),
    "ai_catalog": ("editor", "GET"),
    "ai_draft": ("editor", "GET"),
    "ai_create": ("editor", "POST"),
    "change_create": ("editor", "GET"),
    "change_choose": ("editor", "GET"),
    "change_document": ("editor", "GET"),
    "change_impact": ("editor", "GET"),
    "quality": ("staff", "GET"),
    "quality_gaps": ("staff", "GET"),
    "collection_catalog": ("staff", "GET"),
    "vocabulary_catalog": ("staff", "GET"),
    "data_storage": ("staff", "GET"),
    "stac_coverage": ("staff", "GET"),
    "stac_scenes": ("staff", "GET"),
    "spatial_search": ("staff", "GET"),
    "measurement_history": ("staff", "GET"),
    "harvest_download": ("staff", "GET"),
    "figaro_preview": ("staff", "GET"),
    "natura_report": ("staff", "GET"),
    "data_request": ("staff", "POST"),
    "file_download": ("staff", "GET"),
    "file_restart": ("staff", "POST"),
    "jrc_files": ("staff", "GET"),
    "jrc_file_request": ("staff", "POST"),
    "registry_readiness": ("staff", "GET"),
}


def project_routes():
    """Named routes of the project itself; the admin site has its own checks."""
    return {
        item.name: item
        for item in get_resolver().url_patterns
        if isinstance(item, URLPattern) and item.name
    }


def route_url(name):
    params = project_routes()[name].pattern.converters
    return reverse(name, kwargs={key: 0 if key == "index" else 999999 for key in params})


class AccessMatrixTests(TransactionTestCase):
    # Some staff reports open their own READ ONLY transaction, so no outer test atomic.
    def setUp(self):
        users = get_user_model().objects
        self.reader = users.create_user(username="reader")
        self.editor = users.create_user(username="editor")
        self.editor.user_permissions.add(Permission.objects.get(codename="edit_change_threads"))
        self.staff = users.create_user(username="staff", is_staff=True)
        self.inactive = users.create_user(username="gone", is_staff=True, is_active=False)

    def call(self, name, user=None):
        client = Client()
        if user is not None:
            client.force_login(user)
        method = ACCESS[name][1]
        return getattr(client, method.lower())(route_url(name))

    def assertLoginRedirect(self, response, name, who):
        self.assertEqual(response.status_code, 302, f"{who} {name}")
        self.assertIn("login", response["Location"], f"{who} {name}")

    def assertAllowed(self, response, name, who):
        # 404 is expected for a missing object, 400 for a missing parameter; the point
        # is that the guard let the request through and nothing crashed.
        self.assertIn(response.status_code, {200, 302, 400, 404}, f"{who} {name}")
        if response.status_code == 302:
            self.assertNotIn("login", response["Location"], f"{who} {name}")

    def test_every_route_is_classified(self):
        routes = set(project_routes())
        self.assertEqual(sorted(routes - set(ACCESS)), [], "add new routes to ACCESS")
        self.assertEqual(sorted(set(ACCESS) - routes), [], "remove stale ACCESS rows")

    def test_anonymous(self):
        for name, (level, _) in ACCESS.items():
            response = self.call(name)
            if level in {"public", "health"}:
                self.assertAllowed(response, name, "anonymous")
            else:
                self.assertLoginRedirect(response, name, "anonymous")

    def test_signed_in_reader_without_roles(self):
        for name, (level, _) in ACCESS.items():
            response = self.call(name, self.reader)
            if level == "editor":
                self.assertEqual(response.status_code, 403, f"reader {name}")
            elif level == "staff":
                self.assertLoginRedirect(response, name, "reader")
            else:
                self.assertAllowed(response, name, "reader")

    def test_editor_is_not_staff(self):
        for name, (level, _) in ACCESS.items():
            response = self.call(name, self.editor)
            if level == "staff":
                self.assertLoginRedirect(response, name, "editor")
            else:
                self.assertAllowed(response, name, "editor")

    def test_staff_is_not_editor(self):
        for name, (level, _) in ACCESS.items():
            response = self.call(name, self.staff)
            if level == "editor":
                self.assertEqual(response.status_code, 403, f"staff {name}")
            else:
                self.assertAllowed(response, name, "staff")

    def test_inactive_staff_session_is_rejected(self):
        for name, (level, _) in ACCESS.items():
            if level in {"staff", "login", "editor"}:
                self.assertLoginRedirect(self.call(name, self.inactive), name, "inactive")

    def test_admin_requires_staff(self):
        self.assertLoginRedirect(self.client.get("/admin/"), "admin", "anonymous")
        self.client.force_login(self.reader)
        self.assertLoginRedirect(self.client.get("/admin/"), "admin", "reader")
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_state_changing_routes_reject_get(self):
        for name, (level, method) in ACCESS.items():
            if method == "POST" and level != "public":
                client = Client()
                client.force_login(self.staff if level == "staff" else self.editor)
                self.assertEqual(client.get(route_url(name)).status_code, 405, name)

    def test_state_changing_routes_need_csrf(self):
        for name, (_level, method) in ACCESS.items():
            if method == "POST":
                client = Client(enforce_csrf_checks=True)
                client.force_login(self.staff)
                self.assertEqual(client.post(route_url(name)).status_code, 403, name)


class SessionAndPasswordTests(TestCase):
    def test_signed_in_pages_are_private(self):
        self.client.force_login(get_user_model().objects.create_user(username="reader"))
        response = self.client.get("/information/")
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", self.client.get("/alerts/")["Cache-Control"])

    def test_anonymous_public_page_has_no_private_marker(self):
        self.assertFalse(self.client.get("/health/live/").has_header("Cache-Control"))

    def test_short_passwords_are_rejected(self):
        with self.assertRaises(ValidationError):
            validate_password("Krotkie-h4s")
        validate_password("dluzsze-haslo-2026-x")


class QueryBudgetTests(TestCase):
    """Main pages must not run one extra query per listed row (N+1)."""

    def test_postgres_jit_is_off_for_app_connections(self):
        with connection.cursor() as cursor:
            cursor.execute("SHOW jit")
            self.assertEqual(cursor.fetchone()[0], "off")

    PAGES = ["/", "/search/?q=klimat", "/registry/", "/changes/", "/data/", "/legislation/"]

    def add_rows(self, count):
        release, _ = RegistryRelease.objects.get_or_create(
            sha256="b" * 64, defaults={"filename": "budget.xlsx", "sheets": {}}
        )
        start = Item.objects.count()
        for index in range(start, start + count):
            source = Source.objects.create(
                slug=f"budget-{index}",
                name=f"Źródło {index}",
                adapter="feed",
                url="https://example.org/feed",
                enabled=True,
            )
            item = Item.objects.create(
                source=source,
                external_id=str(index),
                title=f"Klimat i środowisko {index}",
                url=f"https://example.org/{index}",
                content_hash=f"{index:064d}",
            )
            Classification.objects.create(item=item, kind="Komunikat", auto_state="relevant")
            entry = RegistryEntry.objects.create(
                registry_id=900000 + index,
                release=release,
                organization=f"Org {index}",
                name=f"Rejestr {index}",
                master={},
                technical={},
            )
            entry.sources.add(source)

    def counts(self):
        result = {}
        for page in self.PAGES:
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(page)
            self.assertEqual(response.status_code, 200, page)
            result[page] = len(queries)
        return result

    def test_search_ranks_the_stored_vector(self):
        # S9b: rebuilding to_tsvector per hit cost ~1.4 s on 30k items; ranking must
        # read Item.search_document, which the GIN index also covers.
        self.add_rows(2)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get("/search/?q=klimat").status_code, 200)
        sql = " ".join(query["sql"] for query in queries)
        self.assertIn("search_document", sql)
        self.assertNotIn("to_tsvector", sql)

    def test_query_count_does_not_grow_with_rows(self):
        self.add_rows(3)
        small = self.counts()
        self.add_rows(12)
        self.assertEqual(self.counts(), small)
