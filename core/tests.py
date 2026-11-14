from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import TestCase


class FoundationTests(TestCase):
    def test_home(self):
        self.assertContains(self.client.get("/"), "GeoKlimat")

    def test_readiness_with_postgres(self):
        self.assertEqual(self.client.get("/health/ready/").json(), {"status": "ok"})

    def test_database_outage_does_not_leak_details(self):
        with patch("core.views.connection.cursor", side_effect=OperationalError("secret")):
            response = self.client.get("/health/ready/")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b"secret", response.content)
            self.assertEqual(self.client.get("/health/live/").status_code, 200)

    def test_anonymous_admin_redirects_to_login(self):
        self.assertRedirects(self.client.get("/admin/"), "/admin/login/?next=/admin/")

    def test_nonstaff_cannot_access_admin(self):
        user = get_user_model().objects.create_user(username="reader")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_superuser_can_access_admin(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.org", password="test-only-passphrase"
        )
        self.client.force_login(user)
        self.assertContains(self.client.get("/admin/"), "GeoKlimat")

    def test_csrf_protection(self):
        from django.test import Client

        self.assertEqual(Client(enforce_csrf_checks=True).post("/admin/login/").status_code, 403)


class AccessibilityLayoutTests(TestCase):
    """S8: shared navigation, skip link and keyboard-reachable regions."""

    def test_pages_share_skip_link_main_target_and_navigation(self):
        for path in ["/", "/registry/", "/search/", "/accounts/login/"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(response, '<a class="skip-link" href="#tresc">', html=False)
                self.assertContains(response, '<main id="tresc" tabindex="-1">', html=False)
                self.assertContains(response, 'aria-label="Nawigacja główna"', count=1)
                self.assertContains(response, 'name="viewport"')
                self.assertContains(response, "@media (max-width:600px)")

    def test_current_page_is_marked_in_navigation(self):
        response = self.client.get("/registry/")
        self.assertContains(
            response, '<a href="/registry/" aria-current="page">Rejestr</a>', html=False
        )
        self.assertNotContains(response, '<a href="/" aria-current="page">', html=False)

    def test_login_link_for_anonymous_but_not_on_login_page(self):
        self.assertContains(self.client.get("/registry/"), "/accounts/login/?next=/registry/")
        self.assertNotContains(self.client.get("/accounts/login/"), "?next=/accounts/login/")

    def test_staff_links_stay_hidden_from_readers(self):
        user = get_user_model().objects.create_user(username="reader")
        self.client.force_login(user)
        response = self.client.get("/registry/")
        self.assertContains(response, "Wyloguj się", count=1)
        self.assertNotContains(response, 'href="/collections/"')
        self.assertNotContains(response, "Gotowość rejestru")

    def test_registry_page_has_title_and_focusable_table_region(self):
        response = self.client.get("/registry/")
        self.assertContains(response, "<title>Rejestr źródeł — GeoKlimat</title>", html=False)
        self.assertContains(response, 'role="region" aria-label="Pozycje rejestru', html=False)
        self.assertContains(response, '<th scope="col">ID</th>', html=False)

    def test_registry_filters_have_polish_labels(self):
        response = self.client.get("/registry/")
        self.assertContains(response, '<label>Priorytet <select name="priority">', html=False)
        self.assertContains(
            response, '<label>Stan integracji <select name="integration_state">', html=False
        )
