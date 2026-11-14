import hashlib
import io
import json
from datetime import timedelta
from importlib import import_module
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from ingestion.data_service import collect, run_data
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.test_prtr_releases import fixture
from ingestion.transport import FetchError, Response


class GiosPrtrTableTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="1" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            registry_id=49,
            release=release,
            master={},
            technical={},
            integration_state="needs_connector",
        )

    def module(self):
        return import_module("ingestion.gios_prtr_table")

    def channel(self, approved=True):
        module = self.module()
        return DataChannel.objects.create(
            registry=self.entry,
            slug=module.SLUG,
            name="PRTR 2017",
            kind="table",
            url=module.URL,
            config=module.CONFIG.copy(),
            region="PL",
            attribution=module.ATTRIBUTION,
            interval_minutes=1440,
            enabled=approved,
            access_approved=approved,
        )

    def client_for(self, change=None):
        module = self.module()
        client = Mock(max_bytes=5 * 1024 * 1024)

        def get(url, **kwargs):
            response = Response(200, fixture(), {}, module.URL)
            if change:
                change(response)
            return response

        client.get.side_effect = get
        self.enterContext(patch.object(module.original, "SIZE", len(fixture())))
        self.enterContext(
            patch.object(module.original, "SHA256", hashlib.sha256(fixture()).hexdigest())
        )
        self.audit = self.enterContext(patch.object(module.original, "audit_file"))
        return client

    def test_collect_audits_and_emits_annual_records_with_distinct_source_hash(self):
        channel = self.channel()
        client = self.client_for()
        payload, media, summary = collect(channel, client)
        self.audit.assert_called_once_with(client)
        client.get.assert_called_once_with(channel.url)
        self.assertEqual(client.max_bytes, 5 * 1024 * 1024)
        self.assertEqual(media, "application/json")
        self.assertEqual(json.loads(payload)["records"][0]["total_quantity"], "100.00")
        self.assertEqual(summary["scope"], "PL")
        self.assertEqual(summary["reporting_year"], 2017)
        self.assertEqual(summary["source_sha256"], hashlib.sha256(fixture()).hexdigest())
        self.assertNotEqual(summary["source_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertNotIn("oldest_observation", summary)

    def test_rejects_unreviewed_profile_and_custom_modes_before_http(self):
        channel = self.channel()
        client = Mock()
        for kwargs in ({"request_url": channel.url}, {"on_demand": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(FetchError):
                collect(channel, client, **kwargs)
        channel.config["year"] = 2018
        with self.assertRaises(FetchError):
            collect(channel, client)
        client.get.assert_not_called()

    def test_2016_profile_reads_its_own_pinned_original(self):
        module = self.module()
        body = fixture(year=2016)
        source = module.gios_prtr_2016
        self.enterContext(patch.object(source, "SIZE", len(body)))
        self.enterContext(patch.object(source, "SHA256", hashlib.sha256(body).hexdigest()))
        audit = self.enterContext(patch.object(source, "audit_file"))
        original_audit = self.enterContext(patch.object(module.original, "audit_file"))
        client = Mock(max_bytes=5 * 1024 * 1024)
        client.get.return_value = Response(200, body, {}, source.URL)
        call_command("configure_gios_prtr_table", year=2016, apply=True, stdout=io.StringIO())
        channel = DataChannel.objects.get(slug=module.SLUG_2016)
        self.assertEqual((channel.config["year"], channel.url), (2016, source.URL))
        self.assertFalse(channel.enabled or channel.access_approved)
        payload, _, summary = collect(channel, client)
        audit.assert_called_once_with(client)
        original_audit.assert_not_called()
        client.get.assert_called_once_with(source.URL)
        self.assertEqual(summary["reporting_year"], 2016)
        self.assertEqual(json.loads(payload)["records"][0]["reporting_year"], 2016)
        channel.config["year"] = 2017
        with self.assertRaises(FetchError):
            collect(channel, client)

    def test_unreviewed_checksum_refuses_before_http(self):
        module = self.module()
        self.enterContext(patch.object(module.gios_prtr_2016, "SHA256", "PENDING"))
        call_command("configure_gios_prtr_table", year=2016, apply=True, stdout=io.StringIO())
        channel = DataChannel.objects.get()
        client = Mock()
        with self.assertRaises(FetchError):
            collect(channel, client)
        client.get.assert_not_called()

    def test_rejects_changed_body_size_url_status_and_restores_cap(self):
        channel = self.channel()
        client = self.client_for()
        for field, value in (
            ("body", fixture().replace(b"100.00", b"100.01")),
            ("body", fixture() + b" "),
            ("url", channel.url + "?changed"),
            ("status", 206),
        ):
            with self.subTest(field=field, value=str(value)[:30]):
                client.get.side_effect = lambda url, f=field, v=value: Response(
                    **{
                        **{"status": 200, "body": fixture(), "headers": {}, "url": channel.url},
                        f: v,
                    }
                )
                with self.assertRaises(FetchError):
                    collect(channel, client)
                self.assertEqual(client.max_bytes, 5 * 1024 * 1024)

    @override_settings(DATA_AUTO_MAX_BYTES=1)
    def test_payload_budget_is_enforced(self):
        with self.assertRaises(FetchError):
            collect(self.channel(), self.client_for())

    def test_configure_dry_apply_repeat_preserves_operator_state(self):
        call_command("configure_gios_prtr_table", stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.exists())
        call_command("configure_gios_prtr_table", apply=True, stdout=io.StringIO())
        channel = DataChannel.objects.get()
        self.assertFalse(channel.enabled or channel.access_approved)
        channel.enabled = channel.access_approved = True
        channel.interval_minutes = 2880
        channel.last_status = "operator"
        channel.save()
        call_command("configure_gios_prtr_table", apply=True, stdout=io.StringIO())
        channel.refresh_from_db()
        self.assertEqual((channel.interval_minutes, channel.last_status), (2880, "operator"))
        channel.config["year"] = 2018
        channel.save()
        with self.assertRaises(CommandError):
            call_command("configure_gios_prtr_table", apply=True, stdout=io.StringIO())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.integration_state, "needs_connector")

    def test_dry_import_repeat_retains_historical_year_for_30_days(self):
        channel = self.channel()
        with patch("ingestion.data_service.Client", return_value=self.client_for()):
            self.assertEqual(run_data(channel.slug, dry_run=True)[0]["status"], "dry_run")
            self.assertFalse(DataSnapshot.objects.exists())
            self.assertEqual(run_data(channel.slug, force=True)[0]["status"], "stored")
            self.assertEqual(run_data(channel.slug, force=True)[0]["status"], "unchanged")
        snapshot = DataSnapshot.objects.get()
        self.assertAlmostEqual(
            (snapshot.expires_at - snapshot.fetched_at).total_seconds(), 30 * 86400, delta=2
        )
        self.assertEqual(json.loads(bytes(snapshot.payload))["records"][0]["reporting_year"], 2017)

    def test_force_does_not_bypass_disabled_or_unapproved(self):
        channel = self.channel(approved=False)
        with patch("ingestion.data_service.Client") as client:
            self.assertEqual(run_data(channel.slug, force=True), [])
            client.return_value.get.assert_not_called()

    def test_post_fetch_operator_changes_block_copy_and_diagnostic_overwrite(self):
        channel = self.channel()
        sentinel = timezone.now() + timedelta(days=4)
        for field, value in (
            ("access_approved", False),
            ("enabled", False),
            ("region", "EU"),
            ("config", {"mode": "gios_prtr_releases", "year": 2018}),
            ("interval_minutes", 2880),
        ):
            with self.subTest(field=field):
                DataChannel.objects.filter(pk=channel.pk).update(
                    enabled=True,
                    access_approved=True,
                    region="PL",
                    config=self.module().CONFIG,
                    interval_minutes=1440,
                    last_status="",
                    next_fetch_at=timezone.now(),
                )

                def change(response, field=field, value=value):
                    DataChannel.objects.filter(pk=channel.pk).update(
                        **{field: value},
                        last_status="operator",
                        next_fetch_at=sentinel,
                    )

                with patch("ingestion.data_service.Client", return_value=self.client_for(change)):
                    self.assertEqual(run_data(channel.slug, force=True)[0]["status"], "failed")
                self.assertFalse(DataSnapshot.objects.exists())
                channel.refresh_from_db()
                self.assertEqual(
                    (channel.last_status, channel.next_fetch_at), ("operator", sentinel)
                )
                self.assertEqual(getattr(channel, field), value)

    def test_audit_enable_does_not_overwrite_changed_profile(self):
        channel = self.channel(approved=False)

        def change_profile(response):
            DataChannel.objects.filter(pk=channel.pk).update(region="EU", last_status="operator")

        with patch("ingestion.data_service.Client", return_value=self.client_for(change_profile)):
            with self.assertRaises(CommandError):
                call_command("audit_data", source=channel.slug, enable=True, stdout=io.StringIO())
        channel.refresh_from_db()
        self.assertEqual((channel.region, channel.last_status), ("EU", "operator"))
        self.assertFalse(channel.enabled or channel.access_approved)

    def test_fresh_audit_enables_and_detail_explains_annual_scope(self):
        channel = self.channel(approved=False)
        with patch("ingestion.data_service.Client", return_value=self.client_for()):
            call_command("audit_data", source=channel.slug, enable=True, stdout=io.StringIO())
            self.assertEqual(run_data(channel.slug, force=True)[0]["status"], "stored")
        channel.refresh_from_db()
        self.assertTrue(channel.enabled and channel.access_approved)
        response = self.client.get(f"/data/{channel.pk}/")
        self.assertContains(response, "Lokalne kopie tabel rocznych")
        self.assertContains(response, "Uwolnienia PRTR za 2017")
        self.assertNotContains(response, "<h2>Kopie z ostatnich 30 dni</h2>")
        self.assertNotContains(response, "Próbka częściowa")
        self.assertContains(response, '<code class="checksum">', count=2)
        snapshot = DataSnapshot.objects.get()
        exported = self.client.get(f"/data/snapshots/{snapshot.pk}/download/")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported["ETag"], '"' + snapshot.sha256 + '"')
        self.assertEqual(exported.content, bytes(snapshot.payload))
        self.assertEqual(json.loads(exported.content)["records"][0]["reporting_year"], 2017)
