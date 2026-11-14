import hashlib
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from ingestion.adapters import Entry
from ingestion.marine import CONFIG, URL, product_url
from ingestion.marine_models import MarineCatalogueReport
from ingestion.models import IngestionRun, Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import _ingest_source, ingest_source, run_batch
from ingestion.source_policy import SourcePolicyChanged
from ingestion.transport import FetchError, Response


class MarineAtomicImportTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="b" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=195, release=release, master={}, technical={}
        )
        self.old_success = timezone.now() - timedelta(days=3)
        self.source = Source.objects.create(
            slug="registry-195",
            name="Marine",
            adapter="stac",
            url=URL,
            config=CONFIG.copy(),
            enabled=True,
            activation_approved=True,
            failures=7,
            last_success_at=self.old_success,
            etag="old-validator",
        )
        self.registry.sources.add(self.source)
        self.root = json.dumps(
            {
                "type": "Catalog",
                "id": "MDS",
                "links": [
                    {"rel": "child", "href": f"{key}/product.stac.json"} for key in ("P1", "P2")
                ],
            }
        ).encode()
        self.p1 = {
            "type": "Collection",
            "id": "P1",
            "title": "Ocean",
            "license": "CC-BY",
            "properties": {},
        }
        self.p2 = {
            "type": "Collection",
            "id": "P2",
            "title": "Ocean 2",
            "license": None,
            "properties": {},
        }
        self.http = Mock()

    def replies(self):
        return [Response(200, self.root, {}, URL)] + [
            Response(
                200, json.dumps(row).encode(), {}, product_url(f"{row['id']}/product.stac.json")
            )
            for row in (self.p1, self.p2)
        ]

    def partial(self):
        self.http.get.side_effect = self.replies()
        return _ingest_source(self.source, self.http, marine_partial=True)

    def test_partial_commits_report_and_preserves_full_success_and_rejected_item(self):
        old = Item.objects.create(
            source=self.source,
            external_id=hashlib.sha256(b"P2").hexdigest(),
            title="old",
            url=product_url("P2/product.stac.json"),
            content_hash="a" * 64,
        )
        before = (old.last_seen_at, old.content_hash, old.title)
        run = self.partial()
        self.assertEqual((run.status, run.fetched, run.rejected, run.created), ("partial", 1, 1, 1))
        self.source.refresh_from_db()
        self.assertEqual((self.source.last_success_at, self.source.failures), (self.old_success, 7))
        self.assertEqual(self.source.etag, "old-validator")
        old.refresh_from_db()
        self.assertEqual((old.last_seen_at, old.content_hash, old.title), before)
        report = MarineCatalogueReport.objects.get(run=run).payload
        self.assertEqual(report["manifest"], ["P1", "P2"])
        self.assertEqual(report["root_sha256"], hashlib.sha256(self.root).hexdigest())
        self.assertEqual(report["issues"][0]["product_id"], "P2")
        self.assertTrue(report["scan_complete"])
        self.assertFalse(report["metadata_complete"])
        self.assertEqual(self.http.get.call_count, 3)

    def test_all_valid_partial_path_records_full_success(self):
        self.p2["license"] = "CC-BY"
        run = self.partial()
        self.assertEqual((run.status, run.rejected, run.created), ("success", 0, 2))
        self.source.refresh_from_db()
        self.assertEqual(self.source.failures, 0)
        self.assertGreater(self.source.last_success_at, self.old_success)
        self.assertTrue(MarineCatalogueReport.objects.get(run=run).payload["metadata_complete"])

    def test_repeat_is_idempotent_and_each_run_has_own_report(self):
        first = self.partial()
        self.source.refresh_from_db()
        second = self.partial()
        self.assertEqual((first.created, second.created, second.unchanged), (1, 0, 1))
        self.assertEqual(Item.objects.filter(source=self.source).count(), 1)
        self.assertEqual(MarineCatalogueReport.objects.count(), 2)

    def test_report_write_failure_rolls_back_all_items(self):
        self.http.get.side_effect = self.replies()
        with patch.object(MarineCatalogueReport, "save", side_effect=RuntimeError("failure")):
            run = _ingest_source(self.source, self.http, marine_partial=True)
        self.assertEqual((run.status, run.created, run.updated, run.unchanged), ("failed", 0, 0, 0))
        self.assertFalse(Item.objects.filter(source=self.source).exists())
        self.assertFalse(MarineCatalogueReport.objects.filter(run=run).exists())

    def test_report_write_failure_preserves_an_existing_updated_item(self):
        old = Item.objects.create(
            source=self.source,
            external_id=hashlib.sha256(b"P1").hexdigest(),
            title="Older title",
            url=product_url("P1/product.stac.json"),
            content_hash="a" * 64,
        )
        before = (old.title, old.content_hash, old.last_seen_at)
        self.http.get.side_effect = self.replies()
        with patch.object(MarineCatalogueReport, "save", side_effect=RuntimeError("failure")):
            run = _ingest_source(self.source, self.http, marine_partial=True)
        self.assertEqual(run.status, "failed")
        old.refresh_from_db()
        self.assertEqual((old.title, old.content_hash, old.last_seen_at), before)
        self.assertEqual(old.versions.count(), 0)

    def test_no_valid_product_is_failure_not_partial_success(self):
        self.p1["title"] = None
        run = self.partial()
        self.assertEqual(run.status, "failed")
        self.assertFalse(Item.objects.exists())
        self.assertFalse(MarineCatalogueReport.objects.exists())

    def test_revocation_during_fetch_rejects_all(self):
        responses = iter(self.replies())

        def fetch(_url):
            response = next(responses)
            if response.url.endswith("P2/product.stac.json"):
                Source.objects.filter(pk=self.source.pk).update(activation_approved=False)
            return response

        self.http.get.side_effect = fetch
        run = _ingest_source(self.source, self.http, marine_partial=True)
        self.assertEqual(run.status, "failed")
        self.assertFalse(Item.objects.exists())
        self.assertFalse(MarineCatalogueReport.objects.exists())
        self.source.refresh_from_db()
        self.assertFalse(self.source.activation_approved)
        self.assertEqual(self.source.failures, 7)

    def test_linkage_removed_during_fetch_rejects_all(self):
        responses = iter(self.replies())

        def fetch(_url):
            response = next(responses)
            if response.url.endswith("P2/product.stac.json"):
                self.registry.sources.remove(self.source)
            return response

        self.http.get.side_effect = fetch
        run = _ingest_source(self.source, self.http, marine_partial=True)
        self.assertEqual(run.status, "failed")
        self.assertFalse(Item.objects.exists())
        self.assertFalse(MarineCatalogueReport.objects.exists())

    def test_config_changed_during_fetch_rejects_all_without_overwriting_admin(self):
        responses = iter(self.replies())
        new_config = {"mode": "different"}

        def fetch(_url):
            response = next(responses)
            if response.url.endswith("P2/product.stac.json"):
                Source.objects.filter(pk=self.source.pk).update(config=new_config)
            return response

        self.http.get.side_effect = fetch
        run = _ingest_source(self.source, self.http, marine_partial=True)
        self.assertEqual(run.status, "failed")
        self.assertFalse(Item.objects.exists())
        self.assertFalse(MarineCatalogueReport.objects.exists())
        self.source.refresh_from_db()
        self.assertEqual((self.source.config, self.source.failures), (new_config, 7))

    def test_fatal_error_after_first_product_writes_nothing(self):
        responses = self.replies()
        self.http.get.side_effect = [responses[0], responses[1], FetchError("timeout")]
        run = _ingest_source(self.source, self.http, marine_partial=True)
        self.assertEqual(run.status, "failed")
        self.assertFalse(Item.objects.exists())
        self.assertFalse(MarineCatalogueReport.objects.exists())

    def test_public_entrypoint_remains_strict(self):
        self.http.get.side_effect = self.replies()
        run = ingest_source(self.source, self.http)
        self.assertEqual(run.status, "failed")
        self.assertFalse(Item.objects.exists())
        self.assertFalse(MarineCatalogueReport.objects.exists())

    def test_scheduler_entrypoint_stays_strict(self):
        self.http.get.side_effect = self.replies()
        with (
            patch("ingestion.service.Client", return_value=self.http),
            patch("ingestion.service.stop_requested", return_value=False),
        ):
            result = run_batch(source_slug=self.source.slug, force=True)
        self.assertEqual(result[0]["status"], "failed")
        self.assertFalse(Item.objects.exists())
        self.assertFalse(MarineCatalogueReport.objects.exists())

    def test_unapproved_source_preflight_preserves_running_history_and_never_fetches(self):
        old_run = IngestionRun.objects.create(source=self.source, status="running")
        Source.objects.filter(pk=self.source.pk).update(activation_approved=False)
        with self.assertRaises(SourcePolicyChanged):
            _ingest_source(self.source, self.http, marine_partial=True)
        old_run.refresh_from_db()
        self.assertEqual(old_run.status, "running")
        self.assertEqual(IngestionRun.objects.count(), 1)
        self.http.get.assert_not_called()

    def test_unrelated_source_preflight_preserves_history(self):
        other = Source.objects.create(
            slug="other",
            name="Other",
            adapter="stac",
            url=URL,
            config=CONFIG.copy(),
            enabled=True,
            activation_approved=True,
        )
        old_run = IngestionRun.objects.create(source=other, status="running")
        with self.assertRaises(SourcePolicyChanged):
            _ingest_source(other, self.http, marine_partial=True)
        old_run.refresh_from_db()
        self.assertEqual(old_run.status, "running")
        self.assertEqual(IngestionRun.objects.filter(source=other).count(), 1)
        self.http.get.assert_not_called()


class NonMarinePartialCompatibilityTests(TestCase):
    def test_existing_partial_behavior_is_unchanged_for_other_adapters(self):
        old_success = timezone.now() - timedelta(days=2)
        source = Source.objects.create(
            slug="other-partial",
            name="Other",
            adapter="feed",
            url="https://example.org/feed",
            config={},
            enabled=True,
            activation_approved=True,
            failures=3,
            last_success_at=old_success,
        )
        result = (
            [Entry("key", "Example", "https://example.org/article")],
            1,
            Response(200, b"", {}, source.url),
            "a" * 64,
        )
        with patch("ingestion.service.fetch_entries", return_value=result):
            run = ingest_source(source, Mock())
        self.assertEqual(run.status, "partial")
        source.refresh_from_db()
        self.assertEqual(source.failures, 0)
        self.assertGreater(source.last_success_at, old_success)
