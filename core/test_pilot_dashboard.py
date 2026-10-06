from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from ingestion.models import Item, Source


class PilotDashboardTests(TestCase):
    def setUp(self):
        self.editor = get_user_model().objects.create_user(username="editor")
        self.editor.user_permissions.add(Permission.objects.get(codename="edit_change_threads"))
        source = Source.objects.create(slug="pilot", name="Pilot", adapter="html")
        self.item = Item.objects.create(
            source=source,
            external_id="a",
            title="<script>x</script>",
            url="https://www.gov.pl/web/test/a",
            content_hash="a" * 64,
        )
        self.row = {
            "id": self.item.pk,
            "source": "pilot",
            "url": self.item.url,
            "hash": "b" * 64,
            "title": self.item.title,
            "summary": "",
            "topics": [],
        }

    def response(self):
        data = {"generated_at": "2026-09-25", "groups": [{"group": "Test", "items": [self.row]}]}
        with patch("core.pilot.baseline", return_value=data):
            return self.client.get("/pilot/")

    def test_permissions_and_get_only(self):
        self.assertEqual(self.response().status_code, 302)
        reader = get_user_model().objects.create_user(username="reader")
        self.client.force_login(reader)
        self.assertEqual(self.response().status_code, 403)
        self.client.force_login(self.editor)
        self.assertEqual(self.client.post("/pilot/", {}).status_code, 405)

    def test_comparison_escaped_and_no_writes(self):
        self.client.force_login(self.editor)
        with self.assertNumQueries(0):
            before = self.item.content_hash
        response = self.response()
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(response, "Treść zmieniła się")
        self.assertContains(response, "oczekuje na ocenę użytkownika")
        self.item.refresh_from_db()
        self.assertEqual(self.item.content_hash, before)
        self.assertFalse(self.item.versions.exists())
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_missing_or_reused_id_never_compares_other_content(self):
        self.client.force_login(self.editor)
        for key, value in (("url", "https://example.org/other"), ("id", 999999)):
            with self.subTest(key=key):
                old = self.row[key]
                self.row[key] = value
                self.assertContains(self.response(), "Brak zgodnego dokumentu")
                self.row[key] = old
