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
