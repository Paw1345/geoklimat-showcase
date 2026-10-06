import io
import json
from datetime import date, timedelta
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ingestion.legislation import current_events, fetch_details, observations, sync_item
from ingestion.models import Item, LegalEvent, LegalLink, LegalProcess, LegalRelation, Source
from ingestion.transport import FetchError, Response


class LegislationTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug="eli-test",
            name="ELI",
            adapter="eli",
            url="https://api.sejm.gov.pl/eli/acts/DU/2026",
            enabled=True,
            activation_approved=True,
        )
        self.item = Item.objects.create(
            source=self.source,
            external_id="x",
            title="Akt testowy",
            url="https://eli.gov.pl/eli/DU/2026/1/ogl",
            content_hash="a" * 64,
            metadata={
                "ELI": "DU/2026/1",
                "announcementDate": "2026-09-01",
                "publication_date_raw": "2026-09-02",
            },
        )

    def test_exact_identity_two_dates_not_effective_and_idempotence(self):
        process = sync_item(self.item)
        self.assertEqual(process.identifier, "ELI:DU/2026/1")
        self.assertEqual(
            set(LegalEvent.objects.values_list("kind", flat=True)), {"issued", "published"}
        )
        self.assertFalse(LegalEvent.objects.filter(kind="effective").exists())
        sync_item(self.item)
        self.assertEqual(LegalEvent.objects.count(), 2)
        other = Item.objects.create(
            source=self.source,
            external_id="y",
            title=self.item.title,
            url="https://eli.gov.pl/eli/DU/2026/2/ogl",
            content_hash="b" * 64,
            metadata={"ELI": "DU/2026/2"},
        )
        self.assertNotEqual(sync_item(other).pk, process.pk)

    def test_source_change_hides_old_dates_and_keeps_revision_history(self):
        sync_item(self.item)
        self.item.content_hash = "b" * 64
        self.item.metadata["publication_date_raw"] = "2026-09-03"
        self.item.save()
        self.assertEqual(current_events(LegalEvent.objects.all()).count(), 0)
        sync_item(self.item)
        self.assertEqual(LegalEvent.objects.count(), 4)
        self.assertEqual(current_events(LegalEvent.objects.all()).count(), 2)
        self.assertEqual(LegalEvent.objects.filter(superseded_at__isnull=False).count(), 2)

    def test_detail_api_dates_replace_listing_and_references_link_exactly(self):
        sync_item(self.item)
        sync_item(
            self.item,
            {
                "ELI": "DU/2026/1",
                "promulgation": "2026-09-02",
                "entryIntoForce": "2026-10-01",
                "references": {"Akty zmienione": [{"id": "DU/2025/42"}]},
            },
        )
        self.assertEqual(current_events(LegalEvent.objects.filter(kind="published")).count(), 1)
        self.assertEqual(LegalRelation.objects.get().target.identifier, "ELI:DU/2025/42")
        self.assertEqual(LegalEvent.objects.get(kind="effective").date, date(2026, 10, 1))

    def test_no_legal_status_from_title_and_wrong_metadata_not_trusted(self):
        self.item.metadata["ELI"] = "DU/2026/2"
        self.assertIsNone(sync_item(self.item))
        self.assertEqual(LegalProcess.objects.count(), 0)
        for raw in ("", "2026-02-31", "2026-9-1", "2026-09-01T01:00:00Z"):
            if raw:
                with self.assertRaises(FetchError):
                    observations({"entryIntoForce": raw}, "eli")

    def test_sejm_process_is_not_act_until_explicit_link_exists(self):
        source = Source.objects.create(slug="sejm-test", adapter="json", config={"mode": "sejm"})
        item = Item.objects.create(
            source=source,
            external_id="p",
            title="Projekt",
            url="https://api.sejm.gov.pl/sejm/term10/processes/1",
            content_hash="c" * 64,
            metadata={"legislation": {"term": 10, "number": "1", "passed": True}},
        )
        process = sync_item(item)
        self.assertFalse(process.documents.first().events.exists())
        sync_item(
            item,
            {
                "ELI": "DU/2026/1",
                "processStartDate": "2026-09-01",
                "stages": [{"date": "2026-09-03", "stageName": "Pierwsze czytanie"}],
            },
        )
        self.assertEqual(LegalRelation.objects.get().target.identifier, "ELI:DU/2026/1")
        self.assertEqual(LegalEvent.objects.get(kind="stage").date, date(2026, 9, 3))

    def test_ambiguous_provider_eli_is_preserved_and_flagged_without_guessing(self):
        source = Source.objects.create(
            slug="ambiguous-sejm", adapter="json", config={"mode": "sejm"}
        )
        item = Item.objects.create(
            source=source,
            external_id="z",
            title="Proces",
            url="https://api.sejm.gov.pl/sejm/term10/processes/17964-z",
            content_hash="c" * 64,
            metadata={
                "legislation": {"ELI": "MP/2026/752 753 75", "processStartDate": "2026-09-01"}
            },
        )
        process = sync_item(item)
        self.assertFalse(LegalRelation.objects.exists())
        self.assertEqual(LegalEvent.objects.count(), 1)
        link = process.documents.get()
        self.assertIn("MP/2026/752 753 75", link.evidence)
        self.assertIn("Niejednoznaczny", link.reference_warning)
        self.assertContains(self.client.get(f"/legislation/{process.pk}/"), "Niejednoznaczny")

    def test_detail_fetch_pins_identity_and_url_no_redirect_or_extra_http(self):
        client = Mock()
        url = "https://api.sejm.gov.pl/eli/acts/DU/2026/1"
        client.get.return_value = Response(
            200, json.dumps({"ELI": "DU/2026/1", "entryIntoForce": "2026-10-01"}).encode(), {}, url
        )
        self.assertEqual(fetch_details(self.item, client)["ELI"], "DU/2026/1")
        client.get.assert_called_once_with(url)
        client.get.return_value.url = "https://api.sejm.gov.pl/"
        with self.assertRaises(FetchError):
            fetch_details(self.item, client)
        client.get.return_value = Response(200, b'{"ELI":"DU/2026/2"}', {}, url)
        with self.assertRaises(FetchError):
            fetch_details(self.item, client)

    def record(self, **extra):
        options = dict(
            item=self.item.pk,
            process="EU:2025_393",
            title="Konsultacje testowe",
            jurisdiction="EU",
            kind="consultation_end",
            key="consultations",
            date=(timezone.localdate() + timedelta(days=5)).isoformat(),
            evidence_url="https://eur-lex.europa.eu/procedure/EN/2025_393",
            evidence="Sekcja konsultacji, podana data dzienna",
            reviewer="Redaktor",
            stdout=io.StringIO(),
        )
        options.update(extra)
        call_command("record_legal_event", **options)

    def test_editorial_dry_run_idempotence_correction_and_staleness(self):
        self.record()
        self.assertFalse(LegalProcess.objects.exists())
        self.record(apply=True)
        self.record(apply=True)
        self.assertEqual(LegalEvent.objects.count(), 1)
        self.record(apply=True, date=(timezone.localdate() + timedelta(days=6)).isoformat())
        self.assertEqual(LegalEvent.objects.count(), 2)
        self.assertEqual(current_events(LegalEvent.objects.all()).count(), 1)
        self.item.content_hash = "f" * 64
        self.item.save()
        self.assertEqual(current_events(LegalEvent.objects.all()).count(), 0)
        self.assertContains(self.client.get("/deadlines/"), "Brak potwierdzonych dat")

    def test_editorial_required_evidence_and_https_no_writes_on_error(self):
        for extra in (
            {"evidence": ""},
            {"reviewer": " "},
            {"date": "bad"},
            {"evidence_url": "javascript:alert(1)"},
            {"process": ""},
        ):
            with self.subTest(extra=extra), self.assertRaises(CommandError):
                self.record(apply=True, **extra)
        self.assertFalse(LegalLink.objects.exists())

    def test_catalog_detail_deadlines_get_only_escaped_and_history_visible(self):
        self.record(apply=True, title="<script>alert(1)</script>")
        process = LegalProcess.objects.get()
        for url in ("/legislation/", f"/legislation/{process.pk}/", "/deadlines/"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "<script>")
            self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(self.client.get("/legislation/99999/").status_code, 404)
        self.assertContains(self.client.get(f"/items/{self.item.pk}/"), process.identifier)
        self.assertEqual(
            self.client.get("/legislation/?jurisdiction=PL").context["processes"].paginator.count, 0
        )

    def test_backfill_dry_apply_no_http_and_no_unbounded_details(self):
        call_command("sync_legislation", stdout=io.StringIO())
        self.assertFalse(LegalProcess.objects.exists())
        call_command("sync_legislation", apply=True, stdout=io.StringIO())
        self.assertEqual(LegalProcess.objects.count(), 1)
        with self.assertRaises(CommandError):
            call_command("sync_legislation", details=True, stdout=io.StringIO())

    def test_withdrawal_preserves_evidence_and_can_be_repeated(self):
        self.record(apply=True)
        event = LegalEvent.objects.get()
        original = event.evidence
        options = {
            "reviewer": "Redaktor",
            "note": "Błędny zakres cytowanego przepisu",
            "stdout": io.StringIO(),
        }
        call_command("withdraw_legal_event", event.pk, **options)
        event.refresh_from_db()
        self.assertIsNone(event.superseded_at)
        call_command("withdraw_legal_event", event.pk, apply=True, **options)
        event.refresh_from_db()
        stamp = event.superseded_at
        self.assertIsNotNone(stamp)
        self.assertEqual(event.evidence, original)
        self.assertIn("Redaktor", event.withdrawal_note)
        self.assertFalse(current_events(LegalEvent.objects.all()).exists())
        call_command("withdraw_legal_event", event.pk, apply=True, **options)
        event.refresh_from_db()
        self.assertEqual(event.superseded_at, stamp)

    def test_conflicting_dates_are_visible_not_resolved_automatically(self):
        self.record(apply=True)
        self.record(
            apply=True,
            key="second-source",
            date=(timezone.localdate() + timedelta(days=6)).isoformat(),
        )
        process = LegalProcess.objects.get()
        response = self.client.get(f"/legislation/{process.pk}/")
        self.assertContains(response, "Różne daty tego samego rodzaju")
        self.assertEqual(len(response.context["events"]), 2)

    def test_link_only_does_not_require_or_create_a_date(self):
        self.record(apply=True, link_only=True, kind=None, key=None, date=None)
        self.assertEqual(LegalLink.objects.count(), 1)
        self.assertFalse(LegalEvent.objects.exists())
        with self.assertRaises(CommandError):
            self.record(apply=True, link_only=True)
        with self.assertRaises(CommandError):
            self.record(
                apply=True,
                link_only=True,
                kind=None,
                key=None,
                date=None,
                evidence="Different source passage; previous preserved",
            )

    def test_deadline_window_boundaries_and_catalog_page_size(self):
        today = timezone.localdate()
        for offset in (-31, -30, -1, 0, 90, 91):
            self.record(
                apply=True, key=f"day{offset}", date=(today + timedelta(days=offset)).isoformat()
            )
        self.assertEqual(self.client.get("/deadlines/").context["events"].paginator.count, 2)
        self.assertEqual(
            self.client.get("/deadlines/?window=past").context["events"].paginator.count, 2
        )
        LegalProcess.objects.bulk_create(
            [LegalProcess(identifier=f"EU:{i}", title="Test", jurisdiction="EU") for i in range(31)]
        )
        response = self.client.get("/legislation/?jurisdiction=EU&page=2")
        self.assertEqual(response.context["processes"].paginator.count, 32)
        self.assertEqual(len(response.context["processes"]), 2)

    def test_ingestion_updates_legislative_index_in_same_transaction(self):
        from ingestion.service import ingest_source

        http = Mock()
        http.get.return_value = Response(
            200,
            json.dumps(
                {
                    "items": [
                        {
                            "ELI": "DU/2026/42",
                            "title": "Test source act",
                            "promulgation": "2026-09-01",
                        }
                    ],
                    "count": 1,
                }
            ).encode(),
            {},
            self.source.url,
        )
        # ELI fetcher requests current and previous year; isolate to one parsed batch.
        from unittest.mock import patch

        from ingestion.adapters import parse

        entries, rejected = parse(self.source, http.get.return_value)
        with patch(
            "ingestion.service.fetch_entries",
            return_value=(entries, rejected, http.get.return_value, "d" * 64),
        ):
            run = ingest_source(self.source, http)
        self.assertEqual(run.status, "success")
        self.assertTrue(LegalProcess.objects.filter(identifier="ELI:DU/2026/42").exists())

    def test_ingestion_dry_run_validates_legal_dates_without_writes(self):
        from unittest.mock import patch

        from ingestion.adapters import Entry
        from ingestion.service import run_batch

        entry = Entry(
            "key",
            "Act",
            self.item.url,
            metadata={"ELI": "DU/2026/1", "announcementDate": "2026-02-31"},
        )
        with patch(
            "ingestion.service.fetch_entries",
            return_value=([entry], 0, Response(200, b"", {}, self.source.url), "d" * 64),
        ):
            result = run_batch(self.source.slug, dry_run=True)
        self.assertEqual(result[0]["status"], "failed")
        self.assertIn("Invalid legislative date", result[0]["error"])
        self.assertFalse(LegalProcess.objects.exists())
