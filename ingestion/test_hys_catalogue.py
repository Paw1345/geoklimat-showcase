import copy
import io
import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.hys_catalogue import CONFIG, LEGACY, URL, fetch_catalogue
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


def row(identity=1, codes=("ENV", "CLIMA")):
    return {
        "id": float(identity),
        "topics": [{"code": code, "label": code} for code in codes],
        "shortTitle": "Source title",
        "reference": "COM(2026)1",
        "foreseenActType": "PROP_REG",
        "initiativeStatus": "ACTIVE",
        "initiativeTranslations": [
            {"language": "PL", "field": "SHORT_TITLE", "value": "Tytuł PL"},
            {"language": "EN", "field": "SHORT_TITLE", "value": "Title EN"},
        ],
        "currentStatuses": [
            {
                "isCurrent": True,
                "frontEndStage": "ADOPTION_WORKFLOW",
                "receivingFeedbackStatus": "OPEN",
                "feedbackStartDate": "2026/01/01 12:00:00",
                "feedbackEndDate": "2026/02/01 23:59:59",
            }
        ],
    }


class HysCatalogueTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="1" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=171,
            release=release,
            master={},
            technical={},
            integration_state="configured",
        )
        self.source = Source.objects.create(
            slug="registry-171",
            adapter="web",
            url=LEGACY,
            enabled=False,
            activation_approved=False,
            interval_minutes=60,
            scope=(
                "Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
                "Bez pełnego archiwum."
            ),
        )
        self.registry.sources.add(self.source)
        self.paged = Source(adapter="json", url=URL, config=CONFIG)
        self.client_api = Mock()
        self.rows = [row()]
        self.mutate = lambda data, theme, number: data
        self.client_api.get.side_effect = self.serve

    def serve(self, url):
        params = parse_qs(urlsplit(url).query)
        theme, number = params["topic"][0], int(params["page"][0])
        size = int(params["size"][0])
        rows = copy.deepcopy(self.rows[number * size : (number + 1) * size])
        pages = max(1, (len(self.rows) + size - 1) // size)
        data = {
            "exactMatch": True,
            "initiativeResultDtoPage": {
                "content": rows,
                "size": size,
                "number": number,
                "totalElements": len(self.rows),
                "totalPages": pages,
                "numberOfElements": len(rows),
                "first": number == 0,
                "last": number == pages - 1,
            },
        }
        data = self.mutate(data, theme, number)
        return Response(200, json.dumps(data).encode(), {}, url)

    def test_all_pages_cross_theme_dedup_polish_title_no_legal_inference(self):
        self.rows = [row(1), row(2)]
        with patch("ingestion.hys_catalogue.SIZE", 1):
            entries, rejected, response, digest = fetch_catalogue(self.paged, self.client_api)
        self.assertEqual(len(entries), 2)
        self.assertEqual(self.client_api.get.call_count, 4)
        self.assertEqual(rejected, 0)
        self.assertEqual(entries[0].title, "Tytuł PL")
        self.assertEqual(entries[0].published, "")
        self.assertEqual(entries[0].updated, "")
        self.assertFalse(entries[0].metadata["consultation_open_verified"])
        self.assertFalse(entries[0].metadata["feedback_dates_timezone_verified"])
        self.assertEqual(len(digest), 64)
        self.assertEqual(response.status, 200)

    def test_optional_translations_source_title_fallback(self):
        del self.rows[0]["initiativeTranslations"]
        del self.rows[0]["currentStatuses"]
        self.rows[0]["reference"] = None
        self.rows[0]["foreseenActType"] = None
        entry = fetch_catalogue(self.paged, self.client_api)[0][0]
        self.assertEqual(entry.title, "Source title")
        self.assertEqual(entry.metadata["display_language"], "source")
        self.assertFalse(entry.metadata["current_statuses_field_present"])
        self.assertEqual(entry.metadata["current_statuses_raw"], [])
        self.assertIsNone(entry.metadata["reference"])

    def test_wrong_theme_identity_duplicate_and_bad_dates_refuse(self):
        for mutate in (
            lambda r: r.update(id=True),
            lambda r: r.update(topics=[{"code": "DIGITAL", "label": "Digital"}]),
            lambda r: r["currentStatuses"][0].update(feedbackEndDate="2026/02/30 12:00:00"),
        ):
            self.rows = [row()]
            mutate(self.rows[0])
            with self.assertRaises(FetchError):
                fetch_catalogue(self.paged, self.client_api)
        self.rows = [row(), row()]
        with self.assertRaises(FetchError):
            fetch_catalogue(self.paged, self.client_api)

    def test_cross_theme_changed_record_refuses_not_arbitrary_version(self):
        def mutation(data, theme, number):
            if theme == "CLIMA":
                data["initiativeResultDtoPage"]["content"][0]["shortTitle"] = "Changed"
            return data

        self.mutate = mutation
        with self.assertRaises(FetchError):
            fetch_catalogue(self.paged, self.client_api)

    def test_pagination_total_offset_flags_and_budgets_refuse(self):
        for key, value in (("number", 1), ("totalElements", 2), ("last", False), ("size", True)):

            def mutate(data, theme, number, key=key, value=value):
                data["initiativeResultDtoPage"][key] = value
                return data

            self.mutate = mutate
            with self.assertRaises(FetchError):
                fetch_catalogue(self.paged, self.client_api)
        self.mutate = lambda data, theme, number: data
        with patch("ingestion.hys_catalogue.MAX_BYTES", 1), self.assertRaises(FetchError):
            fetch_catalogue(self.paged, self.client_api)
        self.rows = [row(1), row(2)]
        with (
            patch("ingestion.hys_catalogue.SIZE", 1),
            patch("ingestion.hys_catalogue.MAX_PAGES", 1),
            self.assertRaises(FetchError),
        ):
            fetch_catalogue(self.paged, self.client_api)

    def test_redirect_json_and_profile_guards(self):
        for response in (
            Response(200, b"{}" + b"\xff", {}, URL),
            Response(200, b'{"exactMatch":true,"exactMatch":true}', {}, URL),
            Response(304, b"", {}, URL),
        ):
            self.client_api.get.side_effect = lambda url, response=response: Response(
                response.status, response.body, {}, url
            )
            with self.assertRaises(FetchError):
                fetch_catalogue(self.paged, self.client_api)
        self.client_api.get.side_effect = lambda url: Response(
            200, b"{}", {}, "https://example.com"
        )
        with self.assertRaises(FetchError):
            fetch_catalogue(self.paged, self.client_api)
        self.paged.config = {"mode": "other"}
        self.client_api.reset_mock()
        with self.assertRaises(FetchError):
            fetch_catalogue(self.paged, self.client_api)
        self.client_api.get.assert_not_called()

    def test_empty_filters_valid(self):
        self.rows = []
        self.assertEqual(fetch_catalogue(self.paged, self.client_api)[0], [])

    def test_configure_dry_apply_repeat_history_and_pk_preserved_no_http(self):
        pk = self.source.pk
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            call_command("configure_hys", stdout=io.StringIO())
            self.source.refresh_from_db()
            self.assertEqual(self.source.url, LEGACY)
            call_command("configure_hys", apply=True, stdout=io.StringIO())
            self.source.refresh_from_db()
            self.source.last_audit = {"status": "verified", "count": 1}
            self.source.save()
            call_command("configure_hys", apply=True, stdout=io.StringIO())
        self.source.refresh_from_db()
        self.assertEqual(self.source.pk, pk)
        self.assertEqual(Source.objects.count(), 1)
        self.assertEqual(self.source.last_audit["status"], "verified")
        self.assertFalse(self.source.enabled)
        self.assertEqual(self.source.interval_minutes, 60)

    def test_admin_configuration_refuses(self):
        self.source.config = {"admin": True}
        self.source.save()
        with self.assertRaises(CommandError):
            call_command("configure_hys", apply=True, stdout=io.StringIO())
        self.source.refresh_from_db()
        self.assertEqual(self.source.url, LEGACY)

    def test_ui_raw_status_dates_and_escaping(self):
        value = fetch_catalogue(self.paged, self.client_api)[0][0]
        item = Item.objects.create(
            source=self.source,
            external_id=value.key,
            title=value.title,
            url=value.url,
            content_hash=value.digest(),
            metadata={**value.metadata, "reference": "<script>bad</script>"},
        )
        response = self.client.get(f"/items/{item.pk}/")
        self.assertContains(response, "nie potwierdzają")
        self.assertContains(response, "2026/02/01 23:59:59")
        self.assertContains(response, "&lt;script&gt;bad&lt;/script&gt;")
        self.assertNotContains(response, "<script>bad</script>")
