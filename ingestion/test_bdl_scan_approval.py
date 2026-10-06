import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from ingestion.bdl_probe import LICENSE
from ingestion.bdl_samples import RESERVE
from ingestion.bdl_scan import SLUG, defaults
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease

RESULT = {
    "status": "dry_run",
    "records": 0,
    "source_count": 17,
    "bytes": 100,
    "identity_scope_hash": "a" * 64,
    "declared_api_license": LICENSE,
    "original_response_preserved": True,
}


class BdlScanApprovalTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="bdlapprove", sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=39,
            release=release,
            master={},
            technical={},
            integration_state="needs_connector",
        )
        self.channel = DataChannel.objects.create(
            slug=SLUG, name="RDLP", enabled=False, access_approved=False, **defaults(registry)
        )
        self.probe = patch(
            "ingestion.management.commands.approve_bdl_scan.advance", return_value=RESULT
        ).start()
        self.addCleanup(patch.stopall)

    def command(self, **kwargs):
        call_command("approve_bdl_scan", stdout=io.StringIO(), **kwargs)

    def test_dry_apply_repeat_keeps_disabled_schedule_and_history(self):
        schedule = self.channel.next_fetch_at
        self.command()
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.access_approved)
        self.command(apply=True)
        self.channel.refresh_from_db()
        self.assertTrue(self.channel.access_approved)
        self.assertFalse(self.channel.enabled)
        checked = self.channel.checked_at
        self.probe.reset_mock()
        self.command(apply=True)
        self.probe.assert_not_called()
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.checked_at, checked)
        self.assertEqual(self.channel.next_fetch_at, schedule)
        self.assertFalse(DataHarvest.objects.exists())
        self.assertFalse(DataSnapshot.objects.exists())

    def test_drift_during_probe_refuses_without_overwriting(self):
        def change(*args, **kwargs):
            DataChannel.objects.filter(pk=self.channel.pk).update(last_status="admin_review")
            return RESULT

        self.probe.side_effect = change
        with self.assertRaises(CommandError):
            self.command(apply=True)
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.access_approved)
        self.assertEqual(self.channel.last_status, "admin_review")

    def test_incomplete_or_unlicensed_probe_refused(self):
        for changes in (
            {"bytes": 0},
            {"source_count": True},
            {"source_count": 101},
            {"declared_api_license": "unknown"},
            {"identity_scope_hash": ""},
            {"original_response_preserved": False},
        ):
            with self.subTest(changes=changes):
                self.probe.return_value = {**RESULT, **changes}
                with self.assertRaises(CommandError):
                    self.command(apply=True)
                self.channel.refresh_from_db()
                self.assertFalse(self.channel.access_approved)

    def test_prior_unapproved_history_is_preserved(self):
        h = DataHarvest.objects.create(
            channel=self.channel, config_hash="old", start_url="old", next_url="old"
        )
        with self.assertRaises(CommandError):
            self.command(apply=True)
        self.probe.assert_not_called()
        h.refresh_from_db()
        self.assertEqual(h.config_hash, "old")

    @override_settings(DATA_STORAGE_MAX_BYTES=RESERVE)
    def test_budget_refuses_before_http(self):
        with self.assertRaises(CommandError):
            self.command(apply=True)
        self.probe.assert_not_called()

    @override_settings(DATA_STORAGE_MAX_BYTES=RESERVE + 60 * 1024 * 1024)
    def test_full_manifest_scenario_refuses_approval(self):
        with self.assertRaises(CommandError):
            self.command(apply=True)
        self.probe.assert_called_once()
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.access_approved)
