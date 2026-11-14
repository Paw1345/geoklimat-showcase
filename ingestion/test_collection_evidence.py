import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, models, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ingestion.collection_evidence import begin_attempt, finish_attempt, record_attempt
from ingestion.data_service import run_data
from ingestion.models import (
    CollectionAttempt,
    DataChannel,
    DataSnapshot,
    RegistryEntry,
    RegistryRelease,
)
from ingestion.transport import FetchError, Response

START = datetime(2026, 10, 12, 9, tzinfo=UTC)


def metadata(channel_id=1, **changes):
    values = {
        "channel_id": channel_id,
        "attempt_id": uuid4(),
        "batch_id": None,
        "origin": "unknown",
        "started_at": START,
        "finished_at": START + timedelta(seconds=2),
        "due_at": None,
        "interval_minutes": 60,
        "profile_sha256": "a" * 64,
        "outcome": "stored",
        "reason_code": "",
    }
    return values | changes


class DisabledEvidenceTests(SimpleTestCase):
    def test_default_off_before_schema_or_connection_access(self):
        self.assertIs(settings.COLLECTION_EVIDENCE_ENABLED, False)
        with patch("ingestion.collection_evidence.connection") as db:
            with patch.object(CollectionAttempt, "full_clean", side_effect=AssertionError):
                self.assertEqual(record_attempt(**metadata()), "disabled")
            self.assertEqual(db.mock_calls, [])

    def test_only_boolean_true_enables_recording(self):
        for value in (False, None, 0, 1, "true", "1"):
            with self.subTest(value=value), override_settings(COLLECTION_EVIDENCE_ENABLED=value):
                self.assertEqual(record_attempt(**metadata()), "disabled")

    def test_payload_and_config_are_not_part_of_recording_interface(self):
        for field in ("payload", "config", "url", "error", "user_id"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                record_attempt(**metadata(), **{field: "private-fixture"})


@override_settings(COLLECTION_EVIDENCE_ENABLED=True)
class CollectionEvidenceTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="a" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=32, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=registry,
            slug="imgw-synop",
            name="IMGW",
            kind="imgw",
            region="PL",
            url="https://example.org/",
            config={"secret": "private-fixture"},
        )

    def record(self, **changes):
        return record_attempt(**metadata(self.channel.pk, **changes))

    def test_terminal_results_do_not_require_or_create_a_snapshot(self):
        for outcome, reason in (
            ("stored", ""),
            ("unchanged", ""),
            ("failed", "collection_failed"),
            ("busy", "lock_busy"),
        ):
            with self.subTest(outcome=outcome):
                self.assertEqual(self.record(outcome=outcome, reason_code=reason), "recorded")
        self.assertEqual(CollectionAttempt.objects.count(), 4)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_duplicate_attempt_never_updates_an_existing_record(self):
        values = metadata(self.channel.pk)
        self.assertEqual(record_attempt(**values), "recorded")
        before = list(CollectionAttempt.objects.values())
        with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
            self.assertEqual(record_attempt(**(values | {"outcome": "unchanged"})), "unavailable")
        self.assertEqual(list(CollectionAttempt.objects.values()), before)

    def test_metadata_only_and_no_redundant_foreign_key_index(self):
        names = {field.name for field in CollectionAttempt._meta.fields}
        self.assertEqual(names, {"id", *metadata().keys()} - {"channel_id"} | {"channel"})
        self.assertFalse(CollectionAttempt._meta.get_field("channel").db_index)
        self.assertEqual(CollectionAttempt._meta.indexes[0].fields, ["channel", "started_at"])
        self.assertFalse(
            any(
                isinstance(field, (models.JSONField, models.BinaryField, models.TextField))
                for field in CollectionAttempt._meta.fields
            )
        )

    def test_database_guards_cannot_be_bypassed_by_skipping_full_clean(self):
        invalid = (
            {"origin": "http"},
            {"outcome": "running"},
            {"interval_minutes": 0},
            {"interval_minutes": 14},
            {"profile_sha256": "not-a-hash"},
            {"profile_sha256": "A" * 64},
            {"finished_at": START - timedelta(seconds=1)},
            {"origin": "worker", "batch_id": uuid4()},
            {"origin": "worker", "due_at": START},
            {"outcome": "failed", "reason_code": ""},
            {"outcome": "stored", "reason_code": "collection_failed"},
            {"reason_code": "private-fixture"},
            {"finished_at": None},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    CollectionAttempt.objects.create(**metadata(self.channel.pk, **changes))
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_worker_requires_batch_and_due_but_manual_does_not_claim_worker(self):
        self.assertEqual(self.record(origin="worker", batch_id=uuid4(), due_at=START), "recorded")
        self.assertEqual(self.record(origin="manual"), "recorded")
        self.assertEqual(self.record(), "recorded")
        self.assertEqual(
            set(CollectionAttempt.objects.values_list("origin", flat=True)),
            {"worker", "manual", "unknown"},
        )

    def test_captured_context_is_not_replaced_by_current_channel_settings(self):
        before = DataChannel.objects.values().get(pk=self.channel.pk)
        self.assertEqual(self.record(interval_minutes=30, profile_sha256="b" * 64), "recorded")
        row = CollectionAttempt.objects.get()
        self.assertEqual((row.interval_minutes, row.profile_sha256), (30, "b" * 64))
        self.assertEqual(DataChannel.objects.values().get(pk=self.channel.pk), before)

    def test_timestamps_are_normalized_to_utc_without_inventing_dates(self):
        local = START.astimezone(ZoneInfo("Europe/Warsaw"))
        self.assertEqual(
            self.record(started_at=local, finished_at=local + timedelta(seconds=2), due_at=local),
            "recorded",
        )
        row = CollectionAttempt.objects.get()
        self.assertEqual(
            (row.started_at, row.finished_at, row.due_at),
            (START, START + timedelta(seconds=2), START),
        )
        self.assertEqual(row.started_at.utcoffset(), timedelta(0))

    def test_naive_or_untyped_dates_are_refused_before_insert(self):
        for field in ("started_at", "finished_at", "due_at"):
            for value in (START.replace(tzinfo=None), "2026-10-12T09:00:00"):
                with self.subTest(field=field, value=value):
                    with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
                        with self.assertNumQueries(0):
                            self.assertEqual(self.record(**{field: value}), "invalid")
        with self.assertRaises(ValidationError):
            CollectionAttempt(
                **metadata(self.channel.pk, started_at=START.replace(tzinfo=None))
            ).full_clean()
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_invalid_field_values_log_only_safe_codes(self):
        for changes in (
            {"attempt_id": "private-fixture"},
            {"origin": "private-fixture"},
            {"reason_code": "private-fixture"},
            {"profile_sha256": "private-fixture"},
            {"finished_at": START - timedelta(seconds=1)},
        ):
            with self.subTest(changes=changes):
                with self.assertLogs("ingestion.collection_evidence", level="WARNING") as logs:
                    self.assertEqual(self.record(**changes), "invalid")
                self.assertEqual(
                    logs.output,
                    ["WARNING:ingestion.collection_evidence:collection_evidence_invalid_metadata"],
                )
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_outer_transaction_cannot_couple_metadata_to_collector_data(self):
        with transaction.atomic():
            with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
                with self.assertNumQueries(0):
                    self.assertEqual(self.record(), "transaction_active")
        with patch("ingestion.collection_evidence.connection.get_autocommit", return_value=False):
            with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
                self.assertEqual(self.record(), "transaction_active")
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_writer_failure_does_not_change_existing_data_or_expose_exception(self):
        DataSnapshot.objects.create(
            channel=self.channel,
            payload=b"[]",
            sha256="a" * 64,
            size_bytes=2,
            media_type="application/json",
            fetched_at=START,
            expires_at=START + timedelta(days=30),
            summary={},
        )
        before = list(DataSnapshot.objects.values())
        with patch.object(CollectionAttempt, "save", side_effect=DatabaseError("private-fixture")):
            with self.assertLogs("ingestion.collection_evidence", level="WARNING") as logs:
                self.assertEqual(self.record(outcome="unchanged"), "unavailable")
        self.assertEqual(
            logs.output, ["WARNING:ingestion.collection_evidence:collection_evidence_unavailable"]
        )
        self.assertEqual(list(DataSnapshot.objects.values()), before)
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_missing_table_is_safe_when_disabled_and_not_a_success_when_enabled(self):
        # TestPG only: expose the actual pre-C7 schema condition, restore in finally.
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE ingestion_collectionattempt RENAME TO test_absent_attempt")
        try:
            with override_settings(COLLECTION_EVIDENCE_ENABLED=False), self.assertNumQueries(0):
                self.assertEqual(self.record(), "disabled")
            with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
                self.assertEqual(self.record(), "unavailable")
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE test_absent_attempt RENAME TO ingestion_collectionattempt"
                )
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_foreign_key_and_uuid_uniqueness_are_enforced_in_postgres(self):
        values = metadata(self.channel.pk)
        CollectionAttempt.objects.create(**values)
        for invalid in (values, metadata(self.channel.pk + 999)):
            with self.assertRaises(IntegrityError), transaction.atomic():
                CollectionAttempt.objects.create(**invalid)
        self.assertEqual(CollectionAttempt.objects.count(), 1)

    def test_postgres_indexes_cover_channel_prefix_and_global_retention(self):
        with connection.cursor() as cursor:
            indexes = connection.introspection.get_constraints(
                cursor, CollectionAttempt._meta.db_table
            )
        channel_indexes = [
            row["columns"]
            for row in indexes.values()
            if row["index"] and row["columns"][0] == "channel_id"
        ]
        self.assertEqual(channel_indexes, [["channel_id", "started_at"]])
        self.assertTrue(
            any(row["index"] and row["columns"] == ["finished_at"] for row in indexes.values())
        )

    def test_valid_write_is_one_insert_without_ledger_reads(self):
        # BEGIN + INSERT + COMMIT; FK/check/unique validation belongs to PG.
        with self.assertNumQueries(3):
            self.assertEqual(self.record(), "recorded")

    def test_connection_failure_is_safe_and_does_not_claim_a_record(self):
        with patch(
            "ingestion.collection_evidence.connection.get_autocommit",
            side_effect=DatabaseError("private-fixture"),
        ):
            with self.assertLogs("ingestion.collection_evidence", level="WARNING") as logs:
                self.assertEqual(self.record(), "unavailable")
        self.assertEqual(
            logs.output, ["WARNING:ingestion.collection_evidence:collection_evidence_unavailable"]
        )
        self.assertFalse(CollectionAttempt.objects.exists())


@override_settings(COLLECTION_EVIDENCE_ENABLED=True)
class CollectorEvidenceHookTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="hook", sha256="b" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=32, release=release, master={}, technical={}
        )
        self.channel = self.channel_for("synop")

    def channel_for(self, profile):
        return DataChannel.objects.create(
            registry=self.registry,
            slug=f"imgw-{profile}",
            name=profile,
            kind="imgw",
            region="PL",
            url=f"https://danepubliczne.imgw.pl/api/data/{profile}",
            enabled=True,
            access_approved=True,
            next_fetch_at=START - timedelta(minutes=1),
            config={"timezone": "UTC", "secret": "private-fixture"},
        )

    def run_channel(self, channel=None, **options):
        channel = channel or self.channel
        profile = channel.url.rsplit("/", 1)[-1]
        if profile == "synop":
            stamp = {"data_pomiaru": "2026-10-12", "godzina_pomiaru": "9"}
        else:
            field = "temperatura_powietrza_data" if profile == "meteo" else "stan_wody_data_pomiaru"
            stamp = {field: START.isoformat()}
        response = Response(200, json.dumps([stamp]).encode(), {}, channel.url)
        client = Mock()
        client.get.return_value = response
        with (
            patch("ingestion.data_service.Client", return_value=client),
            patch("ingestion.data_service.stop_requested", return_value=False),
            patch("ingestion.collection_evidence.timezone.now", return_value=START),
        ):
            return run_data(slug=channel.slug, force=True, **options)

    def test_three_imgw_profiles_have_terminal_metadata_but_not_worker_origin(self):
        for profile in ("synop", "meteo", "hydro"):
            channel = self.channel if profile == "synop" else self.channel_for(profile)
            result = self.run_channel(channel)
            self.assertEqual(result[0]["status"], "stored")
            self.assertEqual(CollectionAttempt.objects.filter(channel=channel).count(), 1)
            row = CollectionAttempt.objects.get(channel=channel)
            self.assertEqual(
                (row.outcome, row.origin, row.due_at),
                ("stored", "unknown", START - timedelta(minutes=1)),
            )
            self.assertEqual(row.interval_minutes, 60)
            self.assertEqual(result[0]["evidence_status"], "recorded")

    def test_unchanged_is_an_attempt_without_refreshing_the_existing_copy(self):
        self.run_channel()
        before = list(DataSnapshot.objects.values())
        result = self.run_channel()
        self.assertEqual(result[0]["status"], "unchanged")
        self.assertEqual(list(DataSnapshot.objects.values()), before)
        self.assertEqual(CollectionAttempt.objects.count(), 2)
        self.assertEqual(
            set(CollectionAttempt.objects.values_list("outcome", flat=True)),
            {"stored", "unchanged"},
        )

    def test_manual_cli_does_not_claim_worker_and_has_no_origin_flag(self):
        with patch("ingestion.management.commands.collect_data.run_data", return_value=[]) as run:
            call_command("collect_data", source=self.channel.slug, apply=True, stdout=io.StringIO())
        self.assertIs(run.call_args.kwargs.get("manual"), True)
        for extra in ({"origin": "worker"}, {"worker": True}):
            with self.assertRaises(TypeError):
                call_command("collect_data", **extra, stdout=io.StringIO())

    def test_manual_apply_records_manual_but_untyped_origin_cannot_claim_worker(self):
        self.run_channel(manual=True)
        self.assertEqual(CollectionAttempt.objects.get().origin, "manual")
        with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
            result = self.run_channel(manual="worker")
        self.assertEqual(
            (result[0]["status"], result[0]["evidence_status"]), ("unchanged", "unavailable")
        )
        self.assertEqual(CollectionAttempt.objects.count(), 1)

    def test_failed_fetch_is_an_attempt_without_payload_or_secret_error(self):
        with patch("ingestion.data_service.Client") as client:
            client.return_value.get.side_effect = FetchError("private-fixture")
            result = run_data(slug=self.channel.slug, force=True)
        self.assertEqual(result[0]["status"], "failed")
        row = CollectionAttempt.objects.get()
        self.assertEqual((row.outcome, row.reason_code), ("failed", "collection_failed"))
        self.assertNotIn("private-fixture", str(list(CollectionAttempt.objects.values())))
        self.assertFalse(DataSnapshot.objects.exists())

    def test_off_has_no_ledger_sql_no_capture_hash_and_compatible_output(self):
        with (
            override_settings(COLLECTION_EVIDENCE_ENABLED=False),
            patch("ingestion.collection_evidence.record_attempt", side_effect=AssertionError),
            patch("ingestion.downloads.configuration", side_effect=AssertionError),
        ):
            with CaptureQueriesContext(connection) as queries:
                result = self.run_channel()
        self.assertEqual(result[0]["status"], "stored")
        self.assertNotIn("evidence_status", result[0])
        self.assertTrue(all("ingestion_collectionattempt" not in item["sql"] for item in queries))
        self.assertEqual(DataSnapshot.objects.count(), 1)
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_dry_run_has_no_attempt_or_copy_even_with_flag_on(self):
        result = self.run_channel(dry_run=True)
        self.assertEqual(result[0]["status"], "dry_run")
        self.assertNotIn("evidence_status", result[0])
        self.assertFalse(CollectionAttempt.objects.exists())
        self.assertFalse(DataSnapshot.objects.exists())

    def test_global_busy_does_not_fabricate_per_channel_attempts(self):
        with (
            patch("ingestion.data_service.batch_lock") as lock,
            patch("ingestion.data_service.Client") as client,
        ):
            lock.return_value.__enter__.return_value = False
            self.assertEqual(run_data(force=True), [{"status": "busy"}])
            client.assert_not_called()
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_stop_before_channel_does_not_create_an_attempt(self):
        with (
            patch("ingestion.data_service.stop_requested", return_value=True),
            patch("ingestion.data_service.Client") as client,
        ):
            self.assertEqual(run_data(force=True), [])
            client.return_value.get.assert_not_called()
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_storage_failure_does_not_change_data_schedule_or_expiry(self):
        self.run_channel()
        before = list(DataSnapshot.objects.values())
        with patch(
            "ingestion.collection_evidence.record_attempt",
            side_effect=RuntimeError("private-fixture"),
        ):
            with self.assertLogs("ingestion.collection_evidence", level="WARNING") as logs:
                result = self.run_channel()
        self.assertEqual(
            (result[0]["status"], result[0]["evidence_status"]), ("unchanged", "unavailable")
        )
        self.assertEqual(list(DataSnapshot.objects.values()), before)
        self.channel.refresh_from_db()
        self.assertEqual((self.channel.last_status, self.channel.last_error), ("unchanged", ""))
        self.assertEqual(self.channel.next_fetch_at, START + timedelta(minutes=60))
        self.assertEqual(CollectionAttempt.objects.count(), 1)
        self.assertEqual(
            logs.output, ["WARNING:ingestion.collection_evidence:collection_evidence_unavailable"]
        )

    def test_capture_failure_is_visible_but_does_not_prevent_data_collection(self):
        with patch("ingestion.downloads.configuration", side_effect=ValueError("private-fixture")):
            with self.assertLogs("ingestion.collection_evidence", level="WARNING") as logs:
                result = self.run_channel()
        self.assertEqual(
            (result[0]["status"], result[0]["evidence_status"]), ("stored", "unavailable")
        )
        self.assertEqual(DataSnapshot.objects.count(), 1)
        self.assertFalse(CollectionAttempt.objects.exists())
        self.assertEqual(
            logs.output,
            ["WARNING:ingestion.collection_evidence:collection_evidence_capture_unavailable"],
        )

    def test_capture_is_immutable_and_keeps_initial_profile_interval_and_due(self):
        from ingestion.downloads import configuration

        expected = configuration(self.channel)
        capture = begin_attempt(self.channel, dry_run=False)
        self.channel.config["changed"] = True
        self.channel.interval_minutes = 30
        self.channel.next_fetch_at = START + timedelta(hours=5)
        with self.assertRaises(TypeError):
            capture.values["origin"] = "worker"
        self.assertEqual(finish_attempt(capture, "stored"), "recorded")
        row = CollectionAttempt.objects.get()
        self.assertEqual(
            (row.profile_sha256, row.interval_minutes, row.due_at),
            (expected, 60, START - timedelta(minutes=1)),
        )

    def test_only_reviewed_imgw_api_profiles_are_eligible(self):
        for changes in (
            {"kind": "imgw_daily"},
            {"region": "EU"},
            {"url": self.channel.url + "/format/json"},
            {"config": {"mode": "crfop"}},
            {"config": {"pagination": True}},
        ):
            channel = DataChannel.objects.get(pk=self.channel.pk)
            for field, value in changes.items():
                setattr(channel, field, value)
            with self.subTest(changes=changes), self.assertNumQueries(0):
                self.assertIsNone(begin_attempt(channel, dry_run=False))

    def test_nonterminal_status_is_not_recorded_as_a_success(self):
        capture = begin_attempt(self.channel, dry_run=False)
        with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
            self.assertEqual(finish_attempt(capture, "collecting"), "not_terminal")
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_provider_get_and_ledger_write_are_outside_data_transaction(self):
        from ingestion.collection_evidence import record_attempt as writer

        def store_evidence(**values):
            self.assertFalse(connection.in_atomic_block)
            return writer(**values)

        def fake_collect(*args):
            self.assertFalse(connection.in_atomic_block)
            return b"[]", "application/json", {"kind": "imgw"}

        with (
            patch("ingestion.data_service.collect", side_effect=fake_collect),
            patch("ingestion.collection_evidence.record_attempt", side_effect=store_evidence),
        ):
            self.assertEqual(self.run_channel()[0]["status"], "stored")
        self.assertEqual(CollectionAttempt.objects.count(), 1)

    def test_unapproved_or_not_due_channels_do_not_create_attempts(self):
        self.channel.access_approved = False
        self.channel.save()
        self.assertEqual(self.run_channel(), [])
        self.channel.access_approved = True
        self.channel.next_fetch_at = timezone.now() + timedelta(days=1)
        self.channel.save()
        with patch("ingestion.data_service.Client") as client:
            self.assertEqual(run_data(slug=self.channel.slug), [])
            client.return_value.get.assert_not_called()
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_off_collector_works_without_the_ledger_table(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE ingestion_collectionattempt RENAME TO test_absent_hook_attempt"
            )
        try:
            with override_settings(COLLECTION_EVIDENCE_ENABLED=False):
                result = self.run_channel()
            self.assertEqual(result[0]["status"], "stored")
            self.assertNotIn("evidence_status", result[0])
            self.assertEqual(DataSnapshot.objects.count(), 1)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE test_absent_hook_attempt RENAME TO ingestion_collectionattempt"
                )

    def test_real_manual_command_records_manual_metadata_with_mocked_http(self):
        out = io.StringIO()
        response = Response(
            200, b'[{"data_pomiaru":"2026-10-12","godzina_pomiaru":"9"}]', {}, self.channel.url
        )
        # A fixed clock keeps the fixture inside the evidence window on any calendar day.
        with (
            patch("ingestion.data_service.Client") as client,
            patch("ingestion.collection_evidence.timezone.now", return_value=START),
        ):
            client.return_value.get.return_value = response
            call_command(
                "collect_data", source=self.channel.slug, apply=True, force=True, stdout=out
            )
        self.assertEqual(CollectionAttempt.objects.get().origin, "manual")
        self.assertEqual(json.loads(out.getvalue())[0]["evidence_status"], "recorded")
        self.assertNotIn("private-fixture", out.getvalue())

    def test_missing_ledger_schema_does_not_rollback_a_collected_snapshot(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE ingestion_collectionattempt RENAME TO test_absent_enabled_hook"
            )
        try:
            with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
                result = self.run_channel()
            self.assertEqual(
                (result[0]["status"], result[0]["evidence_status"]), ("stored", "unavailable")
            )
            self.assertEqual(DataSnapshot.objects.count(), 1)
            self.channel.refresh_from_db()
            self.assertEqual(self.channel.last_status, "stored")
            self.assertEqual(self.channel.next_fetch_at, START + timedelta(minutes=60))
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE test_absent_enabled_hook RENAME TO ingestion_collectionattempt"
                )

    def run_mock_worker(self, collect):
        worker = "ingestion.management.commands.ingestion_worker."
        with (
            patch(worker + "call_command"),
            patch(worker + "run_downloads", return_value=[]),
            patch(worker + "run_data", collect),
            patch(worker + "run_batch", return_value=[]),
        ):
            call_command("ingestion_worker", once=True)

    def test_worker_attempts_share_batch_but_manual_override_does_not(self):
        def collect():
            first = self.run_channel()
            self.run_channel(self.channel_for("meteo"))
            self.run_channel(self.channel_for("hydro"), manual=True)
            return first

        self.run_mock_worker(collect)
        rows = list(CollectionAttempt.objects.order_by("id"))
        self.assertEqual([row.origin for row in rows], ["worker", "worker", "manual"])
        self.assertIsNotNone(rows[0].batch_id)
        self.assertEqual(rows[0].batch_id, rows[1].batch_id)
        self.assertIsNone(rows[2].batch_id)
        self.assertEqual(rows[0].due_at, START - timedelta(minutes=1))
        self.run_channel()
        self.assertEqual(CollectionAttempt.objects.latest("id").origin, "unknown")

    def test_partial_worker_stop_records_only_the_finished_channel(self):
        self.channel_for("meteo")
        client = Mock()
        client.get.return_value = Response(
            200, b'[{"data_pomiaru":"2026-10-12","godzina_pomiaru":"9"}]', {}, self.channel.url
        )
        results = []

        def collect():
            with (
                patch("ingestion.data_service.Client", return_value=client),
                patch("ingestion.data_service.stop_requested", side_effect=[False, True]),
                patch("ingestion.collection_evidence.timezone.now", return_value=START),
            ):
                results.extend(run_data(force=True))
            return results

        self.run_mock_worker(collect)
        self.assertEqual(len(results), 1)
        self.assertEqual(client.get.call_count, 1)
        row = CollectionAttempt.objects.get()
        self.assertEqual(
            (row.channel_id, row.origin, row.outcome), (self.channel.pk, "worker", "stored")
        )

    def test_stopped_or_busy_worker_has_no_per_channel_success(self):
        def stopped():
            with patch("ingestion.data_service.stop_requested", return_value=True):
                return run_data(force=True)

        self.run_mock_worker(stopped)
        self.assertFalse(CollectionAttempt.objects.exists())

        def busy():
            with patch("ingestion.data_service.batch_lock") as lock:
                lock.return_value.__enter__.return_value = False
                return run_data(force=True)

        self.run_mock_worker(busy)
        self.assertFalse(CollectionAttempt.objects.exists())

    def test_worker_failed_fetch_is_failure_and_dry_run_has_no_evidence(self):
        def failed():
            with patch("ingestion.data_service.Client") as client:
                client.return_value.get.side_effect = FetchError("private-fixture")
                return run_data(force=True)

        self.run_mock_worker(failed)
        row = CollectionAttempt.objects.get()
        self.assertEqual(
            (row.origin, row.outcome, row.reason_code), ("worker", "failed", "collection_failed")
        )
        self.run_mock_worker(lambda: self.run_channel(dry_run=True))
        self.assertEqual(CollectionAttempt.objects.count(), 1)


@override_settings(COLLECTION_EVIDENCE_ENABLED=True)
class CollectionEvidenceReadTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="reader", sha256="c" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=32, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=registry,
            slug="imgw-synop",
            name="synop",
            kind="imgw",
            region="PL",
            url="https://danepubliczne.imgw.pl/api/data/synop",
            config={"secret": "private-fixture"},
        )
        from ingestion.downloads import configuration

        self.profile = configuration(self.channel)
        self.now = START + timedelta(hours=2)

    def channels(self):
        return [
            {
                key: getattr(self.channel, key)
                for key in ("id", "url", "kind", "region", "config", "interval_minutes")
            }
        ]

    def read(self, channels=None, now=None):
        from ingestion.collection_evidence import read_attempts

        return read_attempts(channels or self.channels(), now=now or self.now)

    def add(self, **changes):
        return CollectionAttempt.objects.create(
            **metadata(self.channel.pk, profile_sha256=self.profile, **changes)
        )

    @override_settings(COLLECTION_EVIDENCE_ENABLED=False)
    def test_off_reader_has_zero_sql_and_no_fingerprint(self):
        with (
            self.assertNumQueries(0),
            patch("ingestion.downloads.configuration", side_effect=AssertionError("OFF hash")),
        ):
            self.assertEqual(self.read(), {"status": "disabled", "by_channel": {}})

    def test_reader_enable_flag_is_literal_true_and_naive_on_time_is_refused(self):
        for value in (False, None, 0, 1, "true"):
            with override_settings(COLLECTION_EVIDENCE_ENABLED=value), self.assertNumQueries(0):
                self.assertEqual(self.read()["status"], "disabled")
        with self.assertNumQueries(0), self.assertRaises(ValueError):
            self.read(now=self.now.replace(tzinfo=None))

    def test_out_of_scope_profiles_are_not_read(self):
        for changes in (
            {"kind": "imgw_daily"},
            {"region": "EU"},
            {"url": "https://example.org/"},
            {"config": {"mode": "other"}},
            {"config": {"pagination": True}},
        ):
            with self.assertNumQueries(0):
                self.assertEqual(
                    self.read([{**self.channels()[0], **changes}]),
                    {"status": "not_applicable", "by_channel": {}},
                )

    def test_counts_origins_outcomes_lateness_and_start_gaps_without_pass(self):
        batch = uuid4()
        self.add(origin="worker", batch_id=batch, due_at=START - timedelta(minutes=5))
        self.add(
            origin="worker",
            batch_id=batch,
            due_at=START + timedelta(minutes=55),
            started_at=START + timedelta(hours=1),
            finished_at=START + timedelta(hours=1, seconds=2),
            outcome="unchanged",
        )
        self.add(origin="manual")
        self.add(outcome="failed", reason_code="collection_failed")
        result = self.read()
        self.assertEqual(result["status"], "available")
        detail = result["by_channel"][self.channel.pk]
        self.assertFalse(detail["continuity_confirmed"])
        worker = detail["by_origin"]["worker"]
        self.assertEqual(worker["attempts"], 2)
        self.assertEqual(worker["outcomes"], {"stored": 1, "unchanged": 1, "failed": 0, "busy": 0})
        self.assertEqual(worker["max_lateness_seconds"], 300)
        self.assertEqual(worker["max_start_gap_seconds"], 3600)
        self.assertEqual(detail["by_origin"]["manual"]["attempts"], 1)
        self.assertEqual(detail["by_origin"]["unknown"]["outcomes"]["failed"], 1)
        self.assertIn("tolerance_not_agreed", detail["reasons"])
        exported = json.dumps(result, default=str)
        for private in ("private-fixture", self.channel.url, self.profile):
            self.assertNotIn(private, exported)

    def test_cap_refuses_all_partial_aggregates(self):
        self.add()
        self.add()
        with patch("ingestion.collection_evidence.READ_ATTEMPT_LIMIT", 1):
            result = self.read()
        self.assertEqual(result["status"], "capped")
        detail = result["by_channel"][self.channel.pk]
        self.assertEqual(detail["status"], "capped")
        self.assertNotIn("by_origin", detail)
        self.assertFalse(detail["continuity_confirmed"])

    def test_window_boundaries_and_not_finished_as_of_are_explicit(self):
        start = self.now - timedelta(days=30)
        self.add(started_at=start, finished_at=start + timedelta(seconds=1))
        self.add(started_at=self.now, finished_at=self.now)
        self.add(
            started_at=self.now + timedelta(seconds=1), finished_at=self.now + timedelta(seconds=2)
        )
        self.add(
            started_at=self.now - timedelta(minutes=1), finished_at=self.now + timedelta(minutes=1)
        )
        result = self.read()
        self.assertEqual(result["status"], "available")
        detail = result["by_channel"][self.channel.pk]
        self.assertEqual(detail["by_origin"]["unknown"]["attempts"], 1)
        self.assertEqual(detail["not_finished_as_of_count"], 1)
        self.assertIn("not_finished_as_of", detail["reasons"])

    def test_profile_and_interval_changes_are_reported_not_regular(self):
        self.add()
        changes = metadata(
            self.channel.pk,
            profile_sha256="a" * 64,
            interval_minutes=30,
            started_at=START + timedelta(hours=1),
            finished_at=START + timedelta(hours=1, seconds=2),
        )
        CollectionAttempt.objects.create(**changes)
        result = self.read()
        self.assertEqual(result["status"], "available")
        detail = result["by_channel"][self.channel.pk]
        self.assertEqual(
            (detail["distinct_profile_count"], detail["profile_mismatch_count"]), (2, 1)
        )
        self.assertEqual(
            (detail["distinct_interval_count"], detail["interval_mismatch_count"]), (2, 1)
        )
        self.assertIn("profile_changed", detail["reasons"])
        self.assertIn("interval_changed", detail["reasons"])
        self.assertFalse(detail["continuity_confirmed"])

    def test_one_bounded_select_for_multiple_channels_and_no_writes(self):
        channels = self.channels()
        for profile in ("meteo", "hydro"):
            channel = DataChannel.objects.create(
                registry=self.channel.registry,
                slug=profile,
                name=profile,
                kind="imgw",
                region="PL",
                url=f"https://danepubliczne.imgw.pl/api/data/{profile}",
                config={},
            )
            channels.append({key: getattr(channel, key) for key in channels[0]})
        with CaptureQueriesContext(connection) as queries:
            result = self.read(channels)
        self.assertEqual(result["status"], "available")
        sql = [row["sql"] for row in queries if "ingestion_collectionattempt" in row["sql"]]
        self.assertEqual(len(sql), 1)
        self.assertTrue(sql[0].lstrip().startswith("SELECT"))
        self.assertIn("LIMIT 10001", sql[0])
        self.assertNotIn("JOIN", sql[0])
        self.assertEqual(len(result["by_channel"]), 3)

    def test_dst_uses_local_days_but_elapsed_utc_seconds(self):
        first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        self.add(started_at=first, finished_at=first + timedelta(seconds=1))
        self.add(
            started_at=first + timedelta(hours=1), finished_at=first + timedelta(hours=1, seconds=1)
        )
        result = self.read(now=first + timedelta(hours=2))
        self.assertEqual(result["status"], "available")
        origin = result["by_channel"][self.channel.pk]["by_origin"]["unknown"]
        self.assertEqual(origin["attempts_by_day"], {"2026-10-25": 2})
        self.assertEqual(origin["max_start_gap_seconds"], 3600)

    def test_missing_table_does_not_poison_the_outer_read_only_report(self):
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE ingestion_collectionattempt RENAME TO test_absent_reader")
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                with self.assertLogs("ingestion.collection_evidence", level="WARNING") as logs:
                    self.assertEqual(self.read()["status"], "unavailable")
                self.assertEqual(DataChannel.objects.count(), 1)
            self.assertEqual(
                logs.output,
                ["WARNING:ingestion.collection_evidence:collection_evidence_read_unavailable"],
            )
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE test_absent_reader RENAME TO ingestion_collectionattempt"
                )

    def test_nonmapping_profile_is_unavailable_without_sql_or_raw_exception(self):
        with (
            self.assertNumQueries(0),
            self.assertLogs("ingestion.collection_evidence", level="WARNING") as logs,
        ):
            result = self.read([{**self.channels()[0], "config": ["private-fixture"]}])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["by_channel"][self.channel.pk]["status"], "unavailable")
        self.assertNotIn("by_origin", result["by_channel"][self.channel.pk])
        self.assertEqual(
            logs.output,
            ["WARNING:ingestion.collection_evidence:collection_evidence_read_unavailable"],
        )
