import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase

from ingestion.adapters import Entry
from ingestion.marine import CONFIG, PARTIAL_CONFIG, URL
from ingestion.marine_history import MarineAuditRecord, MarineProfileState
from ingestion.models import IngestionRun, RegistryEntry, RegistryRelease, Source
from ingestion.transport import Response
from ingestion.worker_presence import WORKER_LOCK, WorkerPresence, workers_stopped

WORKER = "ingestion.management.commands.ingestion_worker."


class OtherSession:
    """A second PostgreSQL session, standing in for a running worker process."""

    def __init__(self):
        self.connection = connection.copy()

    def lock(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [WORKER_LOCK])
            return cursor.fetchone()[0]

    def close(self):
        self.connection.close()


class MarineProfileApplyTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="d" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=195, release=release, master={}, technical={}
        )
        self.source = Source.objects.create(
            slug="registry-195",
            name="Marine",
            adapter="stac",
            url=URL,
            config=CONFIG.copy(),
            enabled=False,
            activation_approved=False,
        )
        registry.sources.add(self.source)
        self.result = ([Entry("key", "Sea", URL)], 0, Response(200, b"", {}, URL), "a" * 64)

    def configure(self, **options):
        output = io.StringIO()
        call_command("configure_marine_partial", stdout=output, **options)
        return output.getvalue()

    def audit(self):
        with patch(
            "ingestion.management.commands.audit_sources.fetch_entries", return_value=self.result
        ):
            call_command("audit_sources", source=self.source.slug, stdout=io.StringIO())

    def activate(self):
        call_command(
            "activate_registry", source=[self.source.slug], apply=True, stdout=io.StringIO()
        )
        self.source.refresh_from_db()
        return self.source.enabled and self.source.activation_approved

    def snapshot(self):
        return (
            Source.objects.values().get(pk=self.source.pk),
            list(MarineProfileState.objects.values()),
        )

    def test_dry_run_changes_nothing_and_reports_worker_state(self):
        before = self.snapshot()
        self.assertIn("no worker running", self.configure())
        self.assertEqual(self.snapshot(), before)
        other = OtherSession()
        self.addCleanup(other.close)
        self.assertTrue(other.lock())
        self.assertIn("apply would refuse", self.configure())
        self.assertEqual(self.snapshot(), before)

    def test_apply_refuses_while_worker_holds_lock(self):
        other = OtherSession()
        self.addCleanup(other.close)
        self.assertTrue(other.lock())
        before = self.snapshot()
        with self.assertRaisesMessage(CommandError, "stop the worker first"):
            self.configure(apply=True)
        self.assertEqual(self.snapshot(), before)
        other.close()
        self.configure(apply=True)
        self.source.refresh_from_db()
        self.assertEqual(self.source.config, PARTIAL_CONFIG)

    def test_apply_switches_profile_and_raises_generation(self):
        self.assertIn("generation 0 -> 1", self.configure(apply=True))
        self.source.refresh_from_db()
        state = MarineProfileState.objects.get(source=self.source)
        self.assertEqual(self.source.config, PARTIAL_CONFIG)
        self.assertEqual((state.profile, state.generation), ("partial", 1))
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)
        self.configure(apply=True, profile="strict")
        self.source.refresh_from_db()
        state.refresh_from_db()
        self.assertEqual(self.source.config, CONFIG)
        self.assertEqual((state.profile, state.generation), ("strict", 2))

    def test_apply_invalidates_earlier_audit_even_for_same_profile(self):
        self.audit()
        self.configure(apply=True, profile="strict")
        self.assertFalse(self.activate())
        self.audit()
        record = MarineAuditRecord.objects.order_by("-pk").first()
        self.assertEqual(record.generation, 1)
        self.assertTrue(self.activate())

    def test_returning_to_old_profile_does_not_revive_old_audit(self):
        self.audit()
        self.configure(apply=True)
        self.configure(apply=True, profile="strict")
        self.assertFalse(self.activate())

    def test_apply_keeps_existing_guards(self):
        Source.objects.filter(pk=self.source.pk).update(enabled=True)
        with self.assertRaises(CommandError):
            self.configure(apply=True)
        Source.objects.filter(pk=self.source.pk).update(enabled=False, config={"mode": "x"})
        with self.assertRaises(CommandError):
            self.configure(apply=True)
        Source.objects.filter(pk=self.source.pk).update(config=CONFIG.copy())
        IngestionRun.objects.create(source=self.source, status="running")
        with self.assertRaises(CommandError):
            self.configure(apply=True)
        self.assertFalse(MarineProfileState.objects.exists())
        self.source.refresh_from_db()
        self.assertEqual(self.source.config, CONFIG)


class WorkerPresenceTests(TransactionTestCase):
    def test_presence_blocks_proof_until_released(self):
        worker = WorkerPresence()
        self.addCleanup(worker.release)
        self.assertTrue(worker.ensure())
        self.assertTrue(worker.ensure())
        with workers_stopped() as idle:
            self.assertFalse(idle)
        second = WorkerPresence()
        self.addCleanup(second.release)
        self.assertFalse(second.ensure())
        worker.release()
        with workers_stopped() as idle:
            self.assertTrue(idle)
            self.assertFalse(second.ensure())
        self.assertTrue(second.ensure())

    def test_lost_connection_reacquires_or_reports_loss(self):
        worker = WorkerPresence()
        self.addCleanup(worker.release)
        self.assertTrue(worker.ensure())
        worker.connection.close()
        self.assertTrue(worker.ensure())
        worker.connection.close()
        other = OtherSession()
        self.addCleanup(other.close)
        self.assertTrue(other.lock())
        self.assertFalse(worker.ensure())

    def run_worker(self, **options):
        with (
            patch(WORKER + "call_command"),
            patch(WORKER + "run_downloads", return_value=[]),
            patch(WORKER + "run_data", return_value=[]) as data,
            patch(WORKER + "run_batch", return_value=[]),
        ):
            call_command("ingestion_worker", **options)
        return data

    def test_worker_once_refuses_while_lock_is_held_and_releases_after(self):
        other = OtherSession()
        self.addCleanup(other.close)
        self.assertTrue(other.lock())
        with self.assertRaises(CommandError):
            self.run_worker(once=True)
        other.close()
        self.run_worker(once=True).assert_called_once()
        with workers_stopped() as idle:
            self.assertTrue(idle)
