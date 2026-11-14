import io
import json
from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from ingestion.collection_evidence import prune_attempts
from ingestion.models import (
    CollectionAttempt,
    DataChannel,
    DataHarvest,
    DataSnapshot,
    IngestionRun,
    Item,
    RegistryEntry,
    RegistryRelease,
    Source,
)
from ingestion.test_collection_evidence import START, metadata
from ingestion.worker_stop import STOP


class DisabledRetentionTests(SimpleTestCase):
    def test_help_names_the_approved_metadata_window(self):
        from ingestion.management.commands.prune_collection_attempts import Command

        self.assertIn("35-day collection-metadata", Command.help)

    def test_off_apply_command_is_explicitly_disabled_and_zero_sql(self):
        out = io.StringIO()
        # SimpleTestCase itself prohibits all database queries.
        with override_settings(COLLECTION_EVIDENCE_ENABLED=False):
            call_command("prune_collection_attempts", apply=True, stdout=out)
        self.assertEqual(
            json.loads(out.getvalue()), {"status": "disabled", "deleted": 0, "applied": False}
        )

    def test_off_including_apply_never_accesses_connection_or_clock(self):
        with (
            patch("ingestion.collection_evidence.connection", side_effect=AssertionError("OFF DB")),
            patch(
                "ingestion.collection_evidence.timezone.now",
                side_effect=AssertionError("OFF clock"),
            ),
        ):
            for value in (False, None, 0, 1, "true"):
                with override_settings(COLLECTION_EVIDENCE_ENABLED=value):
                    self.assertEqual(
                        prune_attempts(apply=True),
                        {"status": "disabled", "deleted": 0, "applied": False},
                    )


@override_settings(COLLECTION_EVIDENCE_ENABLED=True)
class EvidenceRetentionTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="retention", sha256="d" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=32, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=registry,
            slug="retention",
            name="retention",
            kind="imgw",
            url="https://example.org/",
            config={"secret": "private-fixture"},
        )
        self.now = START + timedelta(days=36)
        self.cutoff = self.now - timedelta(days=35)

    def add(self, finish):
        return CollectionAttempt.objects.create(
            **metadata(
                self.channel.pk, started_at=finish - timedelta(seconds=1), finished_at=finish
            )
        )

    def test_apply_cli_deletes_one_batch_and_reports_only_safe_metadata(self):
        self.add(START)
        out = io.StringIO()
        with patch("ingestion.collection_evidence.timezone.now", return_value=self.now):
            call_command("prune_collection_attempts", apply=True, limit=1, stdout=out)
        result = json.loads(out.getvalue())
        self.assertEqual((result["status"], result["deleted"]), ("applied", 1))
        self.assertNotIn("private-fixture", out.getvalue())
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_default_preview_reports_bounded_candidates_and_physical_size(self):
        self.add(self.cutoff - timedelta(seconds=1))
        before = list(CollectionAttempt.objects.values())
        result = prune_attempts(now=self.now)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(result["deleted"], 0)
        self.assertFalse(result["applied"])
        self.assertGreater(result["relation_bytes"], 0)
        self.assertEqual(before, list(CollectionAttempt.objects.values()))

    def test_thirty_to_thirty_five_day_metadata_is_retained_but_older_is_deleted(self):
        old = self.add(self.now - timedelta(days=35, microseconds=1))
        retained = [self.add(self.now - timedelta(days=age)) for age in (30, 34, 35)]
        result = prune_attempts(apply=True, now=self.now)
        self.assertEqual(result["cutoff"], self.now - timedelta(days=35))
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(CollectionAttempt.objects.filter(pk=old.pk).exists())
        self.assertEqual(
            set(CollectionAttempt.objects.values_list("pk", flat=True)),
            {row.pk for row in retained},
        )

    def test_apply_preserves_cutoff_equal_recent_and_all_unrelated_data(self):
        old = self.add(self.cutoff - timedelta(microseconds=1))
        retained = [self.add(self.cutoff), self.add(self.now)]
        DataSnapshot.objects.create(
            channel=self.channel,
            payload=b"[]",
            size_bytes=2,
            sha256="a" * 64,
            media_type="application/json",
            fetched_at=START,
            expires_at=START + timedelta(days=30),
            summary={"fixture": True},
        )
        source = Source.objects.create(
            slug="retention-news",
            name="news",
            organization="fixture",
            url="https://example.org/",
            adapter="feed",
        )
        Item.objects.create(
            source=source,
            external_id="retained",
            url="https://example.org/item",
            title="retained",
            content_hash="c" * 64,
        )
        IngestionRun.objects.create(source=source, started_at=START)
        DataHarvest.objects.create(
            channel=self.channel,
            config_hash="b" * 64,
            start_url="https://example.org/start",
            next_url="https://example.org/cursor",
            pages=3,
        )
        models = (
            DataChannel,
            DataSnapshot,
            RegistryEntry,
            RegistryRelease,
            Source,
            Item,
            IngestionRun,
            DataHarvest,
        )
        before = {model: list(model.objects.values()) for model in models}
        result = prune_attempts(apply=True, now=self.now)
        self.assertEqual(
            (result["status"], result["deleted"], result["applied"]), ("applied", 1, True)
        )
        self.assertFalse(CollectionAttempt.objects.filter(pk=old.pk).exists())
        self.assertEqual(
            set(CollectionAttempt.objects.values_list("pk", flat=True)),
            {row.pk for row in retained},
        )
        self.assertEqual(before, {model: list(model.objects.values()) for model in models})

    def test_batch_has_sentinel_and_repeated_runs_eventually_finish(self):
        for seconds in (3, 2, 1):
            self.add(self.cutoff - timedelta(seconds=seconds))
        with CaptureQueriesContext(connection) as queries:
            result = prune_attempts(apply=True, now=self.now, limit=2)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            (result["candidates"], result["deleted"], result["more_expired"]), (2, 2, True)
        )
        self.assertTrue(any("LIMIT 3" in row["sql"] for row in queries))
        self.assertEqual(CollectionAttempt.objects.count(), 1)
        second = prune_attempts(apply=True, now=self.now, limit=2)
        self.assertEqual((second["deleted"], second["more_expired"]), (1, False))
        self.assertEqual(prune_attempts(apply=True, now=self.now)["deleted"], 0)

    def test_dst_and_shorter_cache_policy_do_not_shorten_metadata_retention(self):
        self.add(self.cutoff)
        with override_settings(DATA_RETENTION_DAYS=1):
            result = prune_attempts(apply=True, now=self.now.astimezone(ZoneInfo("Europe/Warsaw")))
        self.assertIn("cutoff", result)
        self.assertEqual(result["cutoff"], self.cutoff)
        self.assertEqual(result["deleted"], 0)

    def test_invalid_limits_dates_and_apply_types_have_no_sql(self):
        for options in (
            {"limit": 0},
            {"limit": 1001},
            {"limit": True},
            {"limit": "1"},
            {"apply": "yes"},
            {"now": START.replace(tzinfo=None)},
        ):
            with (
                self.assertNumQueries(0),
                self.assertLogs("ingestion.collection_evidence", level="WARNING"),
            ):
                self.assertEqual(prune_attempts(**options)["status"], "invalid")

    def test_outer_transaction_is_refused_without_mutation(self):
        self.add(self.cutoff - timedelta(seconds=1))
        with transaction.atomic(), self.assertNumQueries(0):
            self.assertEqual(
                prune_attempts(apply=True, now=self.now)["status"], "transaction_active"
            )
        self.assertEqual(CollectionAttempt.objects.count(), 1)

    def test_delete_failure_rolls_back_with_safe_status_and_log(self):
        self.add(self.cutoff - timedelta(seconds=1))
        with (
            patch.object(QuerySet, "delete", side_effect=DatabaseError("private-fixture")),
            self.assertLogs("ingestion.collection_evidence", level="WARNING") as logs,
        ):
            result = prune_attempts(apply=True, now=self.now)
        self.assertEqual(
            (result["status"], result["deleted"], result["applied"]), ("unavailable", 0, False)
        )
        self.assertEqual(CollectionAttempt.objects.count(), 1)
        self.assertEqual(
            logs.output,
            ["WARNING:ingestion.collection_evidence:collection_evidence_retention_unavailable"],
        )

    def test_age_is_rechecked_at_delete(self):
        row = self.add(self.cutoff - timedelta(seconds=1))
        original = QuerySet.delete

        def updated_before_delete(query):
            CollectionAttempt.objects.filter(pk=row.pk).update(finished_at=self.now)
            return original(query)

        with patch.object(QuerySet, "delete", updated_before_delete):
            result = prune_attempts(apply=True, now=self.now)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(CollectionAttempt.objects.filter(pk=row.pk).exists())

    def test_unexpected_cascade_count_rolls_back(self):
        self.add(self.cutoff - timedelta(seconds=1))
        original = QuerySet.delete

        def unexpected(query):
            total, counts = original(query)
            return total + 1, {**counts, "ingestion.Item": 1}

        with (
            patch.object(QuerySet, "delete", unexpected),
            self.assertLogs("ingestion.collection_evidence", level="WARNING"),
        ):
            result = prune_attempts(apply=True, now=self.now)
        self.assertEqual((result["status"], result["deleted"]), ("unavailable", 0))
        self.assertEqual(CollectionAttempt.objects.count(), 1)

    def test_missing_table_returns_unavailable_and_default_cli_does_not_delete(self):
        out = io.StringIO()
        self.add(START)
        with patch("ingestion.collection_evidence.timezone.now", return_value=self.now):
            call_command("prune_collection_attempts", stdout=out)
        self.assertEqual(json.loads(out.getvalue())["status"], "dry_run")
        self.assertEqual(CollectionAttempt.objects.count(), 1)
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE ingestion_collectionattempt RENAME TO test_absent_retention"
            )
        try:
            with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
                self.assertEqual(prune_attempts(apply=True, now=self.now)["status"], "unavailable")
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE test_absent_retention RENAME TO ingestion_collectionattempt"
                )


class RetentionWorkerTests(SimpleTestCase):
    def setUp(self):
        self.addCleanup(STOP.clear)
        # The advisory-lock proof needs PostgreSQL; see test_marine_profile_apply.
        presence = patch("ingestion.management.commands.ingestion_worker.WorkerPresence")
        presence.start().return_value.ensure.return_value = True
        self.addCleanup(presence.stop)

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_worker_calls_bounded_cleanup_outside_data_scope(self):
        worker = "ingestion.management.commands.ingestion_worker."
        commands = []
        with (
            patch(
                worker + "call_command",
                side_effect=lambda name, **kwargs: commands.append((name, kwargs)),
            ),
            patch(worker + "run_downloads", return_value=[]),
            patch(worker + "run_data", return_value=[]),
            patch(worker + "run_batch", return_value=[]),
        ):
            call_command("ingestion_worker", once=True)
        names = [name for name, _ in commands]
        self.assertEqual(names, ["collect_alerts", "prune_data", "prune_collection_attempts"])
        self.assertEqual(commands[-1][1], {"apply": True, "limit": 500, "verbosity": 0})

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_stop_after_cache_prune_skips_metadata_cleanup(self):
        worker = "ingestion.management.commands.ingestion_worker."
        commands = []

        def command(name, **kwargs):
            commands.append(name)
            if name == "prune_data":
                STOP.set()

        with (
            patch(worker + "call_command", command),
            patch(worker + "run_downloads", return_value=[]),
            patch(worker + "run_data", return_value=[]),
            patch(worker + "run_batch", return_value=[]),
        ):
            call_command("ingestion_worker", once=True)
        self.assertEqual(commands, ["collect_alerts", "prune_data"])
