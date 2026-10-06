import io
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from core.data_views import download, export_payload
from ingestion.bdl_probe import LICENSE, plan
from ingestion.bdl_samples import run_sample
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease


class BdlActivationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture", sha256="bdlactivation", sheets={}
        )
        RegistryEntry.objects.create(
            registry_id=39,
            release=release,
            master={},
            technical={},
            integration_state="needs_connector",
        )
        call_command("configure_bdl_samples", apply=True, stdout=io.StringIO())
        self.probe = patch(
            "ingestion.management.commands.activate_bdl_samples.run_sample",
            return_value={
                "status": "dry_run",
                "declared_api_license": LICENSE,
                "records": 1,
                "bytes": 100,
                "original_response_preserved": True,
            },
        ).start()
        self.addCleanup(patch.stopall)

    def test_dry_apply_repeat_no_snapshots_no_repeat_http(self):
        call_command("activate_bdl_samples", stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.filter(enabled=True).exists())
        call_command("activate_bdl_samples", apply=True, stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.filter(enabled=True, access_approved=True).count(), 3)
        before = list(DataChannel.objects.values_list("pk", "next_fetch_at"))
        self.probe.reset_mock()
        call_command("activate_bdl_samples", apply=True, stdout=io.StringIO())
        self.probe.assert_not_called()
        self.assertEqual(list(DataChannel.objects.values_list("pk", "next_fetch_at")), before)
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_profile_drift_after_get_prevents_all_activation(self):
        def change(*args, **kwargs):
            DataChannel.objects.filter(slug="bdl-rdlp-raw-sample").update(
                next_fetch_at=timezone.now() + timedelta(days=2)
            )
            return {
                "status": "dry_run",
                "declared_api_license": LICENSE,
                "records": 1,
                "bytes": 100,
                "original_response_preserved": True,
            }

        self.probe.side_effect = change
        with self.assertRaises(CommandError):
            call_command("activate_bdl_samples", apply=True, stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.filter(enabled=True).exists())

    def test_empty_probe_and_generic_enable_refused(self):
        self.probe.return_value["records"] = 0
        with self.assertRaises(CommandError):
            call_command("activate_bdl_samples", apply=True, stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command(
                "audit_data", source="bdl-rdlp-raw-sample", enable=True, stdout=io.StringIO()
            )
        self.assertFalse(DataChannel.objects.filter(enabled=True).exists())

    def test_download_keeps_raw_bytes_and_attribution(self):
        channel = DataChannel.objects.get(slug="bdl-rdlp-raw-sample")
        channel.enabled = channel.access_approved = True
        channel.save()
        raw = b'{ "type": "FeatureCollection", "features": [] }\n'
        report = {
            "number_returned": 0,
            "declared_api_license": LICENSE,
            "collection": "rdlp",
            "url": plan("rdlp")["url"],
            "original_response_preserved": True,
        }
        with patch("ingestion.bdl_samples.fetch_sample", return_value=(raw, report)):
            result = run_sample(channel, Mock())
        snapshot = DataSnapshot.objects.get(pk=result["snapshot_id"])
        self.assertEqual(export_payload(snapshot), raw)
        response = download(RequestFactory().get("/"), snapshot.pk)
        self.assertEqual(response.content, raw)
        self.assertIn(".geojson", response["Content-Disposition"])
        self.assertIn('rel="license"', response["Link"])
        self.assertEqual(response["X-Data-Acquired-At"], snapshot.fetched_at.isoformat())
        snapshot.sha256 = "wrong"
        self.assertIsNone(export_payload(snapshot))
