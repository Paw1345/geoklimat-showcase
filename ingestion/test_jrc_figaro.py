import hashlib
import io
import json
from importlib import import_module
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ingestion.downloads import configuration
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError

HEADER = "category,codeIndicator,refArea,colPi,timePeriod,unit,obsValue\n"
LINES = [
    "Air emissions,CO2,PL,P3_S13 ,2015,kt,-0",
    "Air emissions,CH4,PL,A01_A,2015,kt,-2.50",
    "Air emissions,F-gases,FIGW1,P5M ,2015,kt CO2e,0.001",
]


def fixture(lines=None):
    return (HEADER + "\n".join(LINES if lines is None else lines) + "\n").encode()


class FigaroParserTests(SimpleTestCase):
    def parse(self, body=None):
        return import_module("ingestion.jrc_figaro").parse_figaro(
            fixture() if body is None else body
        )

    def test_raw_codes_signed_values_and_units_are_preserved(self):
        rows, summary = self.parse()
        self.assertEqual(rows[0]["colPi"], "P3_S13 ")
        self.assertEqual(rows[0]["obsValue"], "-0")
        self.assertEqual(rows[1]["obsValue"], "-2.50")
        self.assertEqual(rows[2]["unit"], "kt CO2e")
        self.assertEqual(rows[2]["refArea"], "FIGW1")
        self.assertEqual(summary["records"], 3)
        self.assertEqual(summary["negative_values"], 1)
        self.assertEqual(summary["negative_zero_raw"], 1)
        self.assertEqual(summary["source_sha256"], hashlib.sha256(fixture()).hexdigest())
        self.assertNotIn("emission_total", summary)

    def test_wrong_missing_reordered_or_extra_header_is_refused(self):
        for header in [
            HEADER.replace("category", "Category"),
            HEADER.replace("colPi,", ""),
            HEADER.replace("refArea,colPi", "colPi,refArea"),
            HEADER.replace("obsValue", "obsValue,extra"),
        ]:
            with self.subTest(header=header), self.assertRaises(FetchError):
                self.parse(fixture().replace(HEADER.encode(), header.encode()))

    def test_empty_extra_and_missing_fields_are_refused(self):
        for body in [
            b"",
            HEADER.encode(),
            fixture([""]),
            fixture([LINES[0] + ",extra"]),
            fixture([LINES[0].rsplit(",", 1)[0]]),
        ]:
            with self.subTest(body=body), self.assertRaises(FetchError):
                self.parse(body)

    def test_bad_encoding_bom_nul_and_malformed_csv_are_refused(self):
        for body in [
            b"\xef\xbb\xbf" + fixture(),
            fixture().replace(b"CO2", b"\xff"),
            fixture().replace(b"CO2", b"\x00"),
            fixture(['"Air emissions,CO2,PL,A,2015,kt,1']),
        ]:
            with self.subTest(body=body), self.assertRaises(FetchError):
                self.parse(body)

    def test_wrong_year_category_area_indicator_and_unit_are_refused(self):
        for old, new in [
            (b"2015", b"2026"),
            (b"Air emissions", b"Other"),
            (b",PL,", b",XX,"),
            (b"CO2", b"NO2"),
            (b",kt,", b",kg,"),
            (b"kt CO2e", b"kt"),
        ]:
            with self.subTest(new=new), self.assertRaises(FetchError):
                self.parse(fixture().replace(old, new))

    def test_nonfinite_blank_and_non_decimal_values_are_refused(self):
        for value in ["", "NaN", "Infinity", "1e3", "+1", " 1", "1 ", ".2", "1,2", "--1"]:
            with self.subTest(value=value), self.assertRaises(FetchError):
                self.parse(fixture([LINES[0].rsplit(",", 1)[0] + "," + value]))

    def test_duplicate_identity_is_refused_even_when_value_differs(self):
        with self.assertRaises(FetchError):
            self.parse(fixture([LINES[0], LINES[0].rsplit(",", 1)[0] + ",100"]))

    def test_control_blank_and_overlong_codes_are_refused(self):
        for code in ["", " ", "\tA", "A\rB", "A" * 65]:
            with self.subTest(code=code), self.assertRaises(FetchError):
                self.parse(fixture().replace(b"P3_S13 ", code.encode()))

    def test_byte_row_and_cell_budgets_are_enforced(self):
        module = import_module("ingestion.jrc_figaro")
        for name, value in [("MAX_BYTES", 10), ("MAX_ROWS", 2), ("MAX_CELL", 3)]:
            with patch.object(module, name, value), self.assertRaises(FetchError):
                self.parse()


class FigaroCommandTests(TransactionTestCase):
    def setUp(self):
        self.module = import_module("ingestion.jrc_figaro")
        release = RegistryRelease.objects.create(filename="test", sha256="1" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=181, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug=self.module.SLUG,
            kind="file",
            region="EU",
            url=self.module.URL,
            config=self.module.CONFIG,
            access_approved=True,
            attribution="European Commission, Joint Research Centre (JRC), CC BY 4.0.",
        )
        self.body = fixture()
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.job = DownloadJob.objects.create(
            channel=self.channel,
            url=self.channel.url,
            config_hash=configuration(self.channel),
            status="complete",
            offset=len(self.body),
            total_bytes=len(self.body),
            sha256=self.digest,
            expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        self.chunk = DataSnapshot.objects.create(
            download_job=self.job,
            channel=self.channel,
            payload=self.body,
            size_bytes=len(self.body),
            sha256=self.digest,
            media_type="application/octet-stream",
            summary={"kind": "file_chunk", "scope": "EU", "offset": 0},
            expires_at=self.job.expires_at,
        )

    def run_command(self, **options):
        output = io.StringIO()
        with (
            patch.object(self.module, "SIZE", len(self.body)),
            patch.object(self.module, "SHA256", self.digest),
        ):
            call_command("read_jrc_figaro", job=self.job.pk, stdout=output, **options)
        return json.loads(output.getvalue())

    def test_whole_copy_is_checked_then_bounded_raw_sample_is_returned(self):
        before = (
            list(DownloadJob.objects.values()),
            list(DataSnapshot.objects.values()),
            list(DataChannel.objects.values()),
        )
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("HTTP forbidden")):
            result = self.run_command(area="PL", limit=1)
        self.assertEqual(result["records"], 3)
        self.assertEqual(result["matching_records"], 2)
        self.assertEqual(len(result["sample"]), 1)
        self.assertEqual(result["sample"][0]["obsValue"], "-0")
        self.assertEqual(result["job_id"], self.job.pk)
        self.assertEqual(result["source_sha256"], self.digest)
        self.assertEqual(result["license"], self.module.CONFIG["license"])
        self.assertEqual(
            before,
            (
                list(DownloadJob.objects.values()),
                list(DataSnapshot.objects.values()),
                list(DataChannel.objects.values()),
            ),
        )

    def test_indicator_filter_does_not_convert_or_aggregate(self):
        result = self.run_command(indicator="F-gases")
        self.assertEqual(result["matching_records"], 1)
        self.assertEqual(result["sample"][0]["unit"], "kt CO2e")
        self.assertNotIn("emission_total", result)

    def test_bad_row_outside_filter_refuses_whole_copy(self):
        self.body = fixture().replace(b"F-gases", b"BAD")
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.job.sha256 = self.digest
        self.job.save()
        self.chunk.payload = self.body
        self.chunk.sha256 = self.digest
        self.chunk.size_bytes = len(self.body)
        self.chunk.save()
        self.job.total_bytes = self.job.offset = len(self.body)
        self.job.save()
        with self.assertRaises(CommandError):
            self.run_command(area="PL")

    def test_unreviewed_sha_and_unknown_job_are_refused(self):
        with self.assertRaises(CommandError):
            call_command("read_jrc_figaro", job=self.job.pk, stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("read_jrc_figaro", job=999999, stdout=io.StringIO())

    def test_expired_job_and_chunk_are_refused(self):
        for obj in [self.job, self.chunk]:
            previous = obj.expires_at
            obj.expires_at = timezone.now() - timezone.timedelta(seconds=1)
            obj.save()
            with self.assertRaises(CommandError):
                self.run_command()
            obj.expires_at = previous
            obj.save()

    def test_revoked_or_changed_profile_is_refused(self):
        for field, changed in [
            ("slug", "other"),
            ("access_approved", False),
            ("region", "PL"),
            ("kind", "metadata"),
            ("url", "https://example.invalid/file.csv"),
            ("config", {**self.module.CONFIG, "license": "unknown"}),
        ]:
            old = getattr(self.channel, field)
            setattr(self.channel, field, changed)
            self.channel.save()
            with self.assertRaises(CommandError):
                self.run_command()
            setattr(self.channel, field, old)
            self.channel.save()

    def test_actual_payload_size_is_checked_before_materializing_or_export(self):
        self.chunk.payload = self.body + b"x"
        self.chunk.save()
        with CaptureQueriesContext(connection) as queries, self.assertRaises(CommandError):
            self.run_command()
        for query in queries:
            sql = query["sql"].replace('OCTET_LENGTH("ingestion_datasnapshot"."payload")', "")
            self.assertNotIn('"payload"', sql)

    def test_corrupt_same_length_chunk_is_refused(self):
        self.chunk.payload = self.body.replace(b"CO2", b"BAD")
        self.chunk.save()
        with self.assertRaises(CommandError):
            self.run_command()

    def test_invalid_limit_or_filter_is_refused_before_output(self):
        for options in [{"limit": 0}, {"limit": 51}, {"area": "XX"}, {"indicator": "NO2"}]:
            with self.subTest(options=options), self.assertRaises(CommandError):
                self.run_command(**options)

    def test_database_write_attempt_is_blocked_by_read_only_transaction(self):
        def forbidden_write(_body):
            self.channel.__class__.objects.filter(pk=self.channel.pk).update(enabled=True)

        with (
            patch.object(self.module, "parse_figaro", side_effect=forbidden_write),
            self.assertRaises(DatabaseError),
        ):
            self.run_command()
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.enabled)

    def test_changed_job_state_offset_url_configuration_and_size_are_refused(self):
        for field, changed in [
            ("status", "queued"),
            ("offset", len(self.body) - 1),
            ("url", "https://example.invalid/other.csv"),
            ("config_hash", "0" * 64),
            ("total_bytes", len(self.body) + 1),
            ("sha256", "0" * 64),
        ]:
            old = getattr(self.job, field)
            setattr(self.job, field, changed)
            self.job.save()
            with self.subTest(field=field), self.assertRaises(CommandError):
                self.run_command()
            setattr(self.job, field, old)
            self.job.save()

    def test_changed_chunk_receipt_size_and_checksum_are_refused(self):
        for field, changed in [
            ("summary", {"kind": "file_chunk", "scope": "EU", "offset": 1}),
            ("summary", {"kind": "file_chunk", "scope": "PL", "offset": 0}),
            ("size_bytes", len(self.body) - 1),
            ("sha256", "0" * 64),
        ]:
            old = getattr(self.chunk, field)
            setattr(self.chunk, field, changed)
            self.chunk.save()
            with self.subTest(field=field), self.assertRaises(CommandError):
                self.run_command()
            setattr(self.chunk, field, old)
            self.chunk.save()

    def test_extra_chunk_is_refused_before_payload_loading(self):
        self.chunk.pk = None
        self.chunk.save()
        with self.assertRaises(CommandError):
            self.run_command()

    def test_reader_refuses_without_repeatable_read_read_only_boundary(self):
        with self.assertRaises(FetchError):
            self.module.read_verified_figaro(self.job, timezone.now())
        from django.db import transaction

        with transaction.atomic(), self.assertRaises(FetchError):
            self.module.read_verified_figaro(self.job, timezone.now())

    def test_no_match_is_empty_sample_with_whole_copy_counts(self):
        result = self.run_command(area="AR")
        self.assertEqual(result["sample"], [])
        self.assertEqual(result["matching_records"], 0)
        self.assertEqual(result["records"], 3)
