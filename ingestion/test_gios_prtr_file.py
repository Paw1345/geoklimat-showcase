import hashlib
import io
import json
from importlib import import_module
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ingestion.data_service import collect
from ingestion.downloads import advance_download, configuration, enqueue
from ingestion.file_exports import export_descriptor, write_verified_file
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

URL = "https://api.dane.gov.pl/media/resources/20190930/E-PRTR_data_PL_2017.xml"
SIZE = 3964096
SHA = "3eebd25ebd5b06fe30b91f8847d3b886aca1f853f2813bf059430fe4d9e49519"
SLUG = "gios-prtr-2017-file"


class GiosPrtrFileTests(TestCase):
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
        return import_module("ingestion.gios_prtr_file")

    def mock_client(self, change=None):
        dataset = {
            "id": "425",
            "type": "dataset",
            "attributes": {"license_name": "CC BY 4.0", "license_condition_default_cc40": True},
            "relationships": {"institution": {"data": {"type": "institution", "id": "100"}}},
        }
        resource = {
            "id": "19734",
            "type": "resource",
            "attributes": {
                "link": URL,
                "file_size": SIZE,
                "format": "xml",
                "title": "E-PRTR 2017",
                "contains_protected_data": False,
            },
            "relationships": {"dataset": {"data": {"type": "dataset", "id": "425"}}},
        }
        sample = (
            b"<?xml version='1.0' encoding='UTF-8'?><a:PollutantReleaseAndTransferReport "
            b'xmlns:a="urn:eu:com:env:prtr:data:standard:2">'
            b"<a:ReportingYear>2017</a:ReportingYear><a:CountryID>PL</a:CountryID>"
        ).ljust(1024, b" ")
        response = Response(
            206, sample, {"content-range": f"bytes 0-1023/{SIZE}", "etag": '"5d91bcc3-3c7cc0"'}, URL
        )
        if change:
            change(dataset, resource, response)
        client = Mock()

        def get(url, **kwargs):
            if url == URL:
                return response
            row = dataset if "datasets/" in url else resource
            return Response(200, json.dumps({"data": row}).encode(), {}, url)

        client.get.side_effect = get
        return client

    def channel(self, config=None):
        return DataChannel.objects.create(
            registry=self.entry,
            slug=SLUG,
            kind="file",
            url=URL,
            region="PL",
            config=config or self.module().CONFIG.copy(),
            access_approved=True,
        )

    def test_audit_is_three_bounded_gets_and_does_not_claim_measurement_validation(self):
        client = self.mock_client()
        result = self.module().audit_file(client)
        self.assertEqual(result["total_bytes"], SIZE)
        self.assertEqual(result["reporting_year"], 2017)
        self.assertEqual(result["expected_sha256"], SHA)
        self.assertEqual(result["scope"], "file_access_only_not_measurement_validation")
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(client.get.call_args.kwargs["headers"], {"Range": "bytes=0-1023"})
        self.assertFalse(DataSnapshot.objects.exists())

    def test_changed_ownership_licence_resource_and_xml_prefix_fail_closed(self):
        changes = [
            lambda d, r, s: d["attributes"].update(license_condition_default_cc40="true"),
            lambda d, r, s: d["attributes"].update(license_condition_personal_data="restricted"),
            lambda d, r, s: d["relationships"]["institution"]["data"].update(id="1"),
            lambda d, r, s: r["relationships"]["dataset"]["data"].update(id="1"),
            lambda d, r, s: r["attributes"].update(link=URL + "?other=1"),
            lambda d, r, s: r["attributes"].update(file_size=SIZE + 1),
            lambda d, r, s: r["attributes"].update(title="E-PRTR 2016"),
            lambda d, r, s: r["attributes"].update(format="html"),
            lambda d, r, s: r["attributes"].update(contains_protected_data=True),
            lambda d, r, s: setattr(s, "status", 200),
            lambda d, r, s: setattr(s, "url", URL + "?redirect=1"),
            lambda d, r, s: setattr(s, "body", s.body.replace(b"2017", b"2016")),
            lambda d, r, s: setattr(s, "body", s.body.replace(b">PL<", b">DE<")),
            lambda d, r, s: s.headers.update(etag='W/"weak"'),
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(FetchError):
                self.module().audit_file(self.mock_client(change))

    def test_configuration_dry_run_repeat_and_admin_flags_preserved(self):
        module = self.module()
        with patch(
            "ingestion.management.commands.configure_gios_prtr_file.Client",
            return_value=self.mock_client(),
        ):
            call_command("configure_gios_prtr_file", stdout=io.StringIO())
            self.assertFalse(DataChannel.objects.exists())
            call_command("configure_gios_prtr_file", apply=True, stdout=io.StringIO())
            channel = DataChannel.objects.get(slug=SLUG)
            self.assertEqual(channel.config, module.CONFIG)
            self.assertTrue(channel.access_approved)
            self.assertFalse(channel.enabled)
            channel.access_approved = False
            channel.interval_minutes = 987
            channel.save()
            call_command("configure_gios_prtr_file", apply=True, stdout=io.StringIO())
            channel.refresh_from_db()
            self.assertFalse(channel.access_approved)
            self.assertEqual(channel.interval_minutes, 987)
            channel.config = {"admin": True}
            channel.save()
            with self.assertRaises(CommandError):
                call_command("configure_gios_prtr_file", apply=True, stdout=io.StringIO())
            channel.refresh_from_db()
            self.assertEqual(channel.config, {"admin": True})
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.integration_state, "needs_connector")
        self.assertFalse(DownloadJob.objects.exists())

    def test_invalid_configured_checksum_is_refused_before_fetch(self):
        channel = self.channel(config={"expected_sha256": "not-a-checksum"})
        with self.assertRaises(FetchError):
            enqueue(channel)
        self.assertFalse(DownloadJob.objects.exists())

    def complete_fixture(self):
        body = b"<historical-prtr-fixture/>"
        channel = self.channel(config={"expected_sha256": hashlib.sha256(body).hexdigest()})
        job, _ = enqueue(channel)
        client = Mock()
        client.get.return_value = Response(200, body, {}, URL)
        advance_download(job, client)
        return channel, job, body

    def test_matching_checksum_completes_and_repeat_does_not_queue_another_file(self):
        channel, job, body = self.complete_fixture()
        self.assertEqual(job.status, "complete")
        self.assertEqual(job.sha256, hashlib.sha256(body).hexdigest())
        repeated, created = enqueue(channel)
        self.assertFalse(created)
        self.assertEqual(repeated.pk, job.pk)

    def test_same_size_but_different_content_rolls_back_chunk(self):
        channel = self.channel(config={"expected_sha256": hashlib.sha256(b"original").hexdigest()})
        job, _ = enqueue(channel)
        client = Mock()
        client.get.return_value = Response(200, b"altered!", {}, URL)
        with self.assertRaisesMessage(FetchError, "reviewed checksum"):
            advance_download(job, client)
        job.refresh_from_db()
        self.assertEqual(job.offset, 0)
        self.assertEqual(job.status, "queued")
        self.assertFalse(job.chunks.exists())

    def test_export_rejects_consistent_payload_when_it_disagrees_with_reviewed_checksum(self):
        channel, job, body = self.complete_fixture()
        channel.config["expected_sha256"] = SHA
        channel.save()
        job.config_hash = configuration(channel)
        job.save()
        job.refresh_from_db()
        with self.assertRaises(FetchError):
            write_verified_file(job, timezone.now(), io.BytesIO())

    def test_failed_final_chunk_preserves_previous_parts_and_cursor(self):
        channel = self.channel(config={"expected_sha256": hashlib.sha256(b"original").hexdigest()})
        job, _ = enqueue(channel)
        client = Mock()
        client.get.return_value = Response(
            206, b"ori", {"content-range": "bytes 0-2/8", "etag": '"v1"'}, URL
        )
        advance_download(job, client)
        client.get.return_value = Response(
            206, b"other", {"content-range": "bytes 3-7/8", "etag": '"v1"'}, URL
        )
        with self.assertRaises(FetchError):
            advance_download(job, client)
        job.refresh_from_db()
        self.assertEqual(job.offset, 3)
        self.assertEqual(job.status, "running")
        self.assertEqual(job.chunks.count(), 1)
        self.assertEqual(bytes(job.chunks.get().payload), b"ori")

    def test_invalid_checksum_on_existing_job_is_refused_before_http(self):
        channel = self.channel(config={"expected_sha256": "invalid"})
        job = DownloadJob.objects.create(
            channel=channel,
            url=URL,
            config_hash=configuration(channel),
            expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        client = Mock()
        with self.assertRaises(FetchError):
            advance_download(job, client)
        client.get.assert_not_called()

    def test_direct_collection_cannot_bypass_reviewed_download_queue(self):
        channel = self.channel()
        client = Mock()
        client.get.return_value = Response(200, b"unreviewed-content", {}, URL)
        with self.assertRaisesMessage(FetchError, "download queue"):
            collect(channel, client, on_demand=True)
        client.get.assert_not_called()

    def test_staff_xml_export_preserves_bytes_and_headers(self):
        module = self.module()
        body = b"<historical-prtr-test/>"
        with patch.dict(module.CONFIG, {"expected_sha256": hashlib.sha256(body).hexdigest()}):
            channel = self.channel()
            job, _ = enqueue(channel)
            client = Mock()
            client.get.return_value = Response(200, body, {}, URL)
            advance_download(job, client)
            self.assertEqual(self.client.get(f"/data/files/{job.pk}/download/").status_code, 302)
            staff = get_user_model().objects.create_user("prtr-export", is_staff=True)
            self.client.force_login(staff)
            response = self.client.get(f"/data/files/{job.pk}/download/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/xml")
            self.assertEqual(
                response["Content-Disposition"], 'attachment; filename="GIOS-E-PRTR-PL-2017.xml"'
            )
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertEqual(b"".join(response.streaming_content), body)
            self.assertEqual(response["ETag"], '"' + hashlib.sha256(body).hexdigest() + '"')

    def test_exact_profile_has_xml_download_and_historical_scope_without_auth_bypass(self):
        channel = self.channel()
        descriptor = export_descriptor(channel)
        self.assertEqual(descriptor["media_type"], "application/xml")
        self.assertEqual(descriptor["filename"], "GIOS-E-PRTR-PL-2017.xml")
        self.assertIn("2017", descriptor["scope"])
        response = self.client.get(f"/data/{channel.pk}/")
        self.assertContains(response, "2017")
        self.assertNotContains(response, "Zleć pobranie w tle")
        self.assertEqual(self.client.post(f"/data/{channel.pk}/request/").status_code, 302)
        self.assertFalse(DownloadJob.objects.exists())
        staff = get_user_model().objects.create_user("prtr-reader", is_staff=True)
        self.client.force_login(staff)
        self.assertContains(self.client.get(f"/data/{channel.pk}/"), "Zleć pobranie w tle")
