import csv
import hashlib
import io
import json
from importlib import import_module
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ingestion.downloads import configuration
from ingestion.eea_natura_file import ATTRIBUTION, CONFIG, SLUG, URL
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError

FIELDS = (
    "COUNTRY_CODE SITECODE SITENAME SITETYPE DATE_COMPILATION DATE_UPDATE DATE_SPA "
    "SPA_LEGAL_REFERENCE DATE_PROP_SCI DATE_CONF_SCI DATE_SAC SAC_LEGAL_REFERENCE "
    "EXPLANATIONS AREAHA LENGTHKM MARINE_AREA_PERCENTAGE DOCUMENTATION QUALITY "
    "DESIGNATION OTHERCHARACT LATITUDE LONGITUDE INSPIRE_ID"
).split()


def site(**changes):
    row = dict.fromkeys(FIELDS, "")
    row.update(
        COUNTRY_CODE="PL",
        SITECODE="PLH080074",
        SITENAME="Obszar",
        SITETYPE="B",
        DATE_COMPILATION="2000-01-01",
        AREAHA="12.50",
        LATITUDE="51",
        LONGITUDE="15",
    )
    row.update(changes)
    return [row[key] for key in FIELDS]


def fixture(rows=None, headers=None):
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(FIELDS if headers is None else headers)
    writer.writerows([site()] if rows is None else rows)
    return text.getvalue().encode()


class NaturaParserTests(SimpleTestCase):
    def parse(self, body=None):
        return import_module("ingestion.natura_sites").parse_sites(
            io.BytesIO(fixture() if body is None else body)
        )

    def test_full_file_large_quoted_multiline_and_raw_country_counts(self):
        old_limit = csv.field_size_limit()
        result = self.parse(
            fixture(
                [
                    site(QUALITY='Opis, cytat "GIS"\n' + "x" * 283763),
                    site(COUNTRY_CODE="de", SITECODE="DE0000001"),
                    site(COUNTRY_CODE="sk", SITECODE="SK0000001"),
                ]
            )
        )
        self.assertEqual(result["records"], 3)
        self.assertEqual(result["country_counts_raw"], {"PL": 1, "de": 1, "sk": 1})
        self.assertEqual(result["country_counts"], {"DE": 1, "PL": 1, "SK": 1})
        self.assertEqual(result["poland_records"], 1)
        self.assertGreater(result["max_field_chars"], 283763)
        self.assertEqual(csv.field_size_limit(), old_limit)
        self.assertFalse(result["current_registry_confirmed"])
        self.assertFalse(result["national_completeness_confirmed"])

    def test_quality_anomalies_are_reported_without_correcting_raw_values(self):
        result = self.parse(
            fixture(
                [
                    site(
                        DATE_CONF_SCI="2023-02-01",
                        DATE_SAC="2031-03-01",
                        LATITUDE="34961494",
                        LONGITUDE="0",
                        AREAHA="-99",
                        LENGTHKM="NaN",
                    )
                ]
            )
        )
        self.assertEqual(result["quality_status"], "warn")
        self.assertEqual(result["date_ranges"]["DATE_SAC"]["max_raw"], "2031-03-01")
        self.assertEqual(result["date_ranges"]["DATE_CONF_SCI"]["after_release"], 1)
        self.assertEqual(result["quality_counts"]["LATITUDE_out_of_range"], 1)
        self.assertEqual(result["quality_counts"]["LONGITUDE_zero"], 1)
        self.assertEqual(result["quality_counts"]["AREAHA_unknown_minus99"], 1)
        self.assertEqual(result["quality_counts"]["LENGTHKM_invalid_number"], 1)
        self.assertIn(
            {"sitecode": "PLH080074", "field": "LATITUDE", "raw": "34961494"},
            result["quality_examples"],
        )
        self.assertEqual(result["blank_counts"]["INSPIRE_ID"], 1)

    def test_header_empty_width_duplicate_and_late_bad_row_are_refused(self):
        for body in [
            b"",
            fixture([]),
            fixture(headers=FIELDS[::-1]),
            fixture([site()[:-1]]),
            fixture([site() + ["extra"]]),
            fixture([site(), site()]),
            fixture([site(), ["bad"]]),
        ]:
            with self.subTest(body_length=len(body)), self.assertRaises(FetchError):
                self.parse(body)

    def test_invalid_utf8_nul_and_unterminated_quote_are_refused(self):
        for body in [
            fixture().replace(b"Obszar", b"\xff"),
            fixture().replace(b"Obszar", b"\x00"),
            fixture([]) + b'"unterminated',
        ]:
            with self.subTest(body_length=len(body)), self.assertRaises(FetchError):
                self.parse(body)

    def test_country_and_identity_mismatch_are_refused(self):
        for row in [site(COUNTRY_CODE="XX"), site(SITECODE=""), site(SITECODE="DE0000001")]:
            with self.subTest(row=row[:2]), self.assertRaises(FetchError):
                self.parse(fixture([row]))

    def test_byte_row_and_field_limits_are_enforced_and_restored(self):
        old_limit = csv.field_size_limit()
        module = import_module("ingestion.natura_sites")
        for setting, value in [("MAX_BYTES", 20), ("MAX_ROWS", 0), ("MAX_FIELD_CHARS", 2)]:
            with patch.object(module, setting, value), self.assertRaises(FetchError):
                self.parse()
        self.assertEqual(csv.field_size_limit(), old_limit)

    def test_missing_dates_invalid_numbers_and_invalid_dates_are_quality_not_structure(self):
        result = self.parse(
            fixture(
                [
                    site(
                        DATE_UPDATE="not-a-date",
                        MARINE_AREA_PERCENTAGE="101",
                        LATITUDE="NaN",
                        LONGITUDE="",
                        DATE_SAC="1900-01-01",
                    )
                ]
            )
        )
        for key in [
            "DATE_UPDATE_invalid_date",
            "MARINE_AREA_PERCENTAGE_out_of_range",
            "LATITUDE_invalid_number",
            "DATE_SAC_early_date_unconfirmed",
        ]:
            self.assertEqual(result["quality_counts"][key], 1)
        self.assertEqual(result["blank_counts"]["LONGITUDE"], 1)
        self.assertTrue(result["structure_verified"])

    def test_bom_is_supported_and_binary_stream_remains_owned_by_caller(self):
        module = import_module("ingestion.natura_sites")
        stream = io.BytesIO(b"\xef\xbb\xbf" + fixture())
        self.assertEqual(module.parse_sites(stream)["records"], 1)
        self.assertFalse(stream.closed)

    def test_quality_examples_are_bounded_but_counts_cover_all_records(self):
        result = self.parse(
            fixture([site(SITECODE=f"PL{i:07d}", LATITUDE="999") for i in range(20)])
        )
        self.assertEqual(result["records"], 20)
        self.assertEqual(result["quality_counts"]["LATITUDE_out_of_range"], 20)
        self.assertEqual(len(result["quality_examples"]), 10)

    def test_overlong_invalid_value_example_has_explicit_truncation_marker(self):
        result = self.parse(fixture([site(LATITUDE="bad" * 100)]))
        example = next(e for e in result["quality_examples"] if e["field"] == "LATITUDE")
        self.assertTrue(example["raw_truncated"])
        self.assertEqual(len(example["raw"]), 200)


class NaturaCommandTests(TransactionTestCase):
    def setUp(self):
        self.module = import_module("ingestion.natura_sites")
        release = RegistryRelease.objects.create(filename="test", sha256="c" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=176, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug=SLUG,
            name="Natura",
            url=URL,
            config=CONFIG.copy(),
            kind="file",
            region="EU",
            attribution=ATTRIBUTION,
            access_approved=True,
        )
        self.body = fixture()
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.job = DownloadJob.objects.create(
            channel=self.channel,
            url=URL,
            config_hash=configuration(self.channel),
            status="complete",
            offset=len(self.body),
            total_bytes=len(self.body),
            sha256=self.digest,
            expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        self.chunk = DataSnapshot.objects.create(
            channel=self.channel,
            download_job=self.job,
            payload=self.body,
            size_bytes=len(self.body),
            sha256=self.digest,
            summary={"kind": "file_chunk", "scope": "EU", "offset": 0},
            expires_at=self.job.expires_at,
            media_type="application/octet-stream",
        )

    def run_report(self):
        output = io.StringIO()
        with (
            patch.object(self.module, "SIZE", len(self.body)),
            patch.object(self.module, "SHA256", self.digest),
        ):
            call_command("natura_sites_report", job=self.job.pk, stdout=output)
        return json.loads(output.getvalue())

    def test_default_plan_has_no_database_or_http_io(self):
        output = io.StringIO()
        with (
            CaptureQueriesContext(connection) as queries,
            patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")),
        ):
            call_command("natura_sites_report", stdout=output)
        self.assertEqual(len(queries), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "plan")

    def test_verified_report_preserves_all_models_and_provenance(self):
        before = [
            list(model.objects.values()) for model in (DownloadJob, DataChannel, DataSnapshot)
        ]
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            report = self.run_report()
        self.assertEqual(report["records"], 1)
        self.assertEqual(report["source_sha256"], self.digest)
        self.assertEqual(report["job_id"], self.job.pk)
        self.assertEqual(report["release"], "end2021 rev1")
        self.assertEqual(
            before,
            [list(model.objects.values()) for model in (DownloadJob, DataChannel, DataSnapshot)],
        )

    def test_profile_approval_and_state_changes_are_refused_without_output(self):
        for obj, field, value in [
            (self.channel, "access_approved", False),
            (self.channel, "config", {}),
            (self.channel, "region", "PL"),
            (self.job, "status", "running"),
            (self.job, "offset", 0),
            (self.job, "expires_at", timezone.now() - timezone.timedelta(days=1)),
        ]:
            old = getattr(obj, field)
            setattr(obj, field, value)
            obj.save()
            with self.subTest(field=field), self.assertRaises(CommandError):
                self.run_report()
            setattr(obj, field, old)
            obj.save()

    def test_unreviewed_job_and_actual_oversize_payload_are_refused_before_payload_read(self):
        with self.assertRaises(CommandError):
            call_command("natura_sites_report", job=self.job.pk, stdout=io.StringIO())
        self.chunk.payload = self.body + b"x"
        self.chunk.save()
        with CaptureQueriesContext(connection) as queries, self.assertRaises(CommandError):
            self.run_report()
        for query in queries:
            sql = query["sql"].replace('OCTET_LENGTH("ingestion_datasnapshot"."payload")', "")
            self.assertNotIn('"payload"', sql)

    def test_same_length_corruption_is_refused(self):
        self.chunk.payload = self.body.replace(b"Obszar", b"Broken")
        self.chunk.save()
        with self.assertRaises(CommandError):
            self.run_report()

    def test_read_only_snapshot_rejects_parser_write_attempt(self):
        def write(_stream):
            DataChannel.objects.filter(pk=self.channel.pk).update(enabled=True)

        with (
            patch.object(self.module, "parse_sites", side_effect=write),
            self.assertRaises(DatabaseError),
        ):
            self.run_report()
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.enabled)

    def test_unknown_nonpositive_and_nested_jobs_are_refused_without_json(self):
        for job_id in [0, -1, 999999]:
            output = io.StringIO()
            with self.subTest(job=job_id), self.assertRaises(CommandError):
                call_command("natura_sites_report", job=job_id, stdout=output)
            self.assertEqual(output.getvalue(), "")
        with transaction.atomic(), self.assertRaises(CommandError):
            self.run_report()

    def test_reader_rejects_missing_read_only_isolation_before_payload(self):
        with self.assertRaises(FetchError):
            self.module.read_verified_sites(self.job, timezone.now())
        with transaction.atomic(), self.assertRaises(FetchError):
            self.module.read_verified_sites(self.job, timezone.now())

    def test_expired_corrupt_or_missing_chunks_are_refused(self):
        for field, changed in [
            ("expires_at", timezone.now() - timezone.timedelta(seconds=1)),
            ("summary", {"kind": "file_chunk", "scope": "EU", "offset": 1}),
            ("size_bytes", len(self.body) - 1),
            ("sha256", "0" * 64),
        ]:
            old = getattr(self.chunk, field)
            setattr(self.chunk, field, changed)
            self.chunk.save()
            with self.subTest(field=field), self.assertRaises(CommandError):
                self.run_report()
            setattr(self.chunk, field, old)
            self.chunk.save()
        self.chunk.delete()
        with self.assertRaises(CommandError):
            self.run_report()

    def test_changed_slug_url_kind_job_url_hash_and_size_are_refused(self):
        for obj, field, value in [
            (self.channel, "slug", "other"),
            (self.channel, "url", "https://example.invalid/file"),
            (self.channel, "kind", "table"),
            (self.job, "url", "https://example.invalid/file"),
            (self.job, "config_hash", "0" * 64),
            (self.job, "sha256", "0" * 64),
            (self.job, "total_bytes", len(self.body) + 1),
        ]:
            old = getattr(obj, field)
            setattr(obj, field, value)
            obj.save()
            with self.subTest(field=field), self.assertRaises(CommandError):
                self.run_report()
            setattr(obj, field, old)
            obj.save()

    def test_late_malformed_record_does_not_emit_partial_report(self):
        self.body = fixture([site(), ["bad"]])
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.job.offset = self.job.total_bytes = len(self.body)
        self.job.sha256 = self.digest
        self.job.save()
        self.chunk.payload = self.body
        self.chunk.size_bytes = len(self.body)
        self.chunk.sha256 = self.digest
        self.chunk.save()
        output = io.StringIO()
        with (
            patch.object(self.module, "SIZE", len(self.body)),
            patch.object(self.module, "SHA256", self.digest),
        ):
            with self.assertRaises(CommandError):
                call_command("natura_sites_report", job=self.job.pk, stdout=output)
        self.assertEqual(output.getvalue(), "")
