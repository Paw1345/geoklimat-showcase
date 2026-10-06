"""Pilot acceptance: public discovery through editorial review, isolated test DB."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings

from ingestion.alerts import collect
from ingestion.local_ai import run_one
from ingestion.management.commands.pilot_sample import sample
from ingestion.models import AIDraft, ChangeThread, Classification, Item, Notification, Source


@override_settings(LOCAL_AI_ENABLED=True)
class PilotTests(TestCase):
    def setUp(self):
        source = Source.objects.create(
            slug="mkis", name="Pilot", adapter="html", url="https://www.gov.pl/web/klimat"
        )
        self.item = Item.objects.create(
            source=source,
            external_id="pilot",
            title="Pilotaż GIS",
            summary="Nowa usługa udostępnia dane przestrzenne administracji publicznej. "
            "Opis nie określa daty zmiany ani dodatkowych obowiązków.",
            url="https://www.gov.pl/web/klimat/pilot",
            content_hash="a" * 64,
        )
        self.editor = get_user_model().objects.create_user(username="pilot-editor")
        self.editor.user_permissions.add(Permission.objects.get(codename="edit_change_threads"))
        self.reader = get_user_model().objects.create_user(username="pilot-reader")
        self.url = f"/changes/new/?item={self.item.pk}"
        self.fields = {
            "title": self.item.title,
            "reason": "Osobny wątek do oceny zmiany usługi.",
            "expected_hash": self.item.content_hash,
        }

    def test_full_editorial_journey(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.editor)
        self.assertContains(client.get("/search/?q=Pilotaż"), self.item.title)
        self.assertContains(client.get(f"/items/{self.item.pk}/"), self.url, html=False)
        self.assertEqual(client.get(self.url).status_code, 200)
        self.assertFalse(ChangeThread.objects.exists())

        def post(url, values):
            return client.post(
                url, {**values, "csrfmiddlewaretoken": client.cookies["csrftoken"].value}
            )

        self.assertEqual(post(self.url, self.fields).status_code, 302)
        thread = ChangeThread.objects.get()
        self.assertEqual(
            post(f"/changes/{thread.pk}/watch/", {"action": "follow"}).status_code, 302
        )
        self.assertEqual(collect(), 0)
        self.assertEqual(post(f"/changes/{thread.pk}/ai/{self.item.pk}/", {}).status_code, 302)
        draft = AIDraft.objects.get()
        output = {
            "summary": "Nowa usługa danych przestrzennych.",
            "change": "Udostępniono nową usługę danych.",
            "audience": "Administracja publiczna.",
            "kinds": ["technical"],
            "quote": "Nowa usługa udostępnia dane przestrzenne administracji publicznej.",
        }
        with patch("ingestion.local_ai.request_model", return_value=output):
            self.assertEqual(run_one(), "ready")
        self.assertEqual(collect(), 0)  # Unpublished AI does not alert readers.
        self.assertEqual(client.get(f"/ai/{draft.pk}/").status_code, 200)
        self.assertEqual(
            post(
                f"/ai/{draft.pk}/",
                {
                    "action": "approve",
                    "change": output["change"],
                    "audience": output["audience"],
                    "kinds": output["kinds"],
                    "evidence": output["quote"],
                    "evidence_url": self.item.url,
                    "expected_hash": self.item.content_hash,
                    "previous_id": 0,
                },
            ).status_code,
            302,
        )
        self.assertEqual(collect(), 1)
        self.assertEqual(collect(), 0)
        notice = Notification.objects.get()
        self.assertContains(client.get("/alerts/"), notice.title)
        self.assertEqual(post(f"/alerts/{notice.pk}/read/", {}).status_code, 302)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.read_at)
        client.force_login(self.reader)
        self.assertEqual(client.get(f"/ai/{draft.pk}/").status_code, 403)
        self.assertEqual(client.get(f"/alerts/{notice.pk}/").status_code, 404)
        self.assertContains(client.get(f"/changes/{thread.pk}/"), "wsparciu lokalnego AI")

    def test_create_from_item_requires_editor_csrf_and_valid_hash(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(self.url, self.fields).status_code, 403)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.editor)
        self.assertEqual(secure.post(self.url, self.fields).status_code, 403)
        self.client.force_login(self.editor)
        response = self.client.post(self.url, {**self.fields, "expected_hash": "b" * 64})
        self.assertContains(response, "Dokument zmienił się")
        self.assertFalse(ChangeThread.objects.exists())
        self.assertEqual(self.client.get("/changes/new/?item=bad").status_code, 400)
        self.assertEqual(self.client.get("/changes/new/?item=9999999").status_code, 404)

    def test_watch_login_returns_to_requested_list(self):
        response = self.client.get("/changes/?watching=1")
        self.assertIn("next=/changes/", response.url)
        self.assertIn("watching", response.url)

    def test_navigation_and_duplicate_topic_labels(self):
        self.assertContains(self.client.get("/"), "Zaloguj się")
        self.assertContains(self.client.get("/"), 'href="/alerts/"')
        value = Classification(topics=["climate", "climate_adaptation", "climate"])
        self.assertEqual(len(value.topic_labels), len(set(value.topic_labels)))

    def test_sample_read_only_shortfalls_and_content(self):
        before = Item.objects.count()
        report = sample()
        self.assertEqual(len(report["groups"]), 5)
        self.assertEqual(report["groups"][0]["missing"], 5)
        self.assertEqual(report["groups"][0]["items"][0]["id"], self.item.pk)
        self.assertEqual(report["groups"][0]["items"][0]["threads"], [])
        self.assertEqual(Item.objects.count(), before)
        self.assertFalse(ChangeThread.objects.exists())
