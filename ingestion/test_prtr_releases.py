import hashlib
import io
import json
from importlib import import_module
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from ingestion import gios_prtr_file as profile
from ingestion.downloads import advance_download, enqueue
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

NS = "urn:eu:com:env:prtr:data:standard:2"
RELEASE = """<p:PollutantRelease><p:MediumCode>AIR</p:MediumCode>
<p:PollutantCode>CO2</p:PollutantCode><p:MethodBasisCode>M</p:MethodBasisCode>
<p:TotalQuantity unitCode="KGM">100.00</p:TotalQuantity>
<p:AccidentalQuantity unitCode="KGM">2.50</p:AccidentalQuantity>
<p:ConfidentialIndicator>0</p:ConfidentialIndicator></p:PollutantRelease>"""


def fixture(release=RELEASE, facility_extra="", root_extra="", year=2017):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<p:PollutantReleaseAndTransferReport xmlns:p="{NS}">'
        f"<p:ReportingYear>{year}</p:ReportingYear><p:CountryID>PL</p:CountryID>"
        f"<p:CoordinateSystemID>EPSG:4326</p:CoordinateSystemID>{root_extra}"
        "<p:FacilityReport><p:NationalID>PL-1</p:NationalID>"
        "<p:FacilityName>Public facility</p:FacilityName><p:ReportingYear>null</p:ReportingYear>"
        "<p:ConfidentialIndicator>0</p:ConfidentialIndicator>"
        f"<p:ProtectVoluntaryData>0</p:ProtectVoluntaryData>{facility_extra}{release}"
        "</p:FacilityReport></p:PollutantReleaseAndTransferReport>"
    ).encode()


class PrtrReleaseParserTests(SimpleTestCase):
    def parse(self, body=None):
        return import_module("ingestion.prtr_releases").parse_releases(body or fixture())

    def test_preserves_decimal_text_codes_and_header_year_without_adding_accidents(self):
        rows, summary = self.parse()
        self.assertEqual(rows[0]["total_quantity"], "100.00")
        self.assertEqual(rows[0]["accidental_quantity"], "2.50")
        self.assertEqual(rows[0]["unit"], "kg/year")
        self.assertEqual(rows[0]["pollutant_code"], "CO2")
        self.assertEqual(rows[0]["reporting_year_source"], "report_header")
        self.assertEqual(summary["records"], 1)
        self.assertEqual(summary["source_sha256"], hashlib.sha256(fixture()).hexdigest())

    def test_reviewed_2016_original_uses_its_own_header_year(self):
        parse = import_module("ingestion.prtr_releases").parse_releases
        rows, summary = parse(fixture(year=2016), 2016)
        self.assertEqual(rows[0]["reporting_year"], 2016)
        self.assertEqual(summary["scope"], "PL2016_pollutant_releases_only")
        for body, year in ((fixture(year=2016), 2017), (fixture(), 2016), (fixture(), 2015)):
            with self.subTest(year=year), self.assertRaises(FetchError):
                parse(body, year)

    def test_missing_quantities_remain_unknown_not_zero(self):
        body = fixture(
            RELEASE.replace('<p:TotalQuantity unitCode="KGM">100.00</p:TotalQuantity>', "")
        )
        rows, summary = self.parse(body)
        self.assertIsNone(rows[0]["total_quantity"])
        self.assertEqual(summary["missing_total"], 1)
        rows, summary = self.parse(fixture(RELEASE.replace("2.50", "null")))
        self.assertIsNone(rows[0]["accidental_quantity"])

    def test_confidential_release_is_counted_but_not_disclosed(self):
        rows, summary = self.parse(fixture(RELEASE.replace(">0<", ">true<")))
        self.assertEqual(rows, [])
        self.assertEqual(summary["excluded_releases"], 1)
        self.assertNotIn("CO2", json.dumps(summary))

    def test_protected_or_confidential_facility_is_not_disclosed(self):
        for field in ["ConfidentialIndicator", "ProtectVoluntaryData"]:
            body = fixture().replace(f"<p:{field}>0".encode(), f"<p:{field}>1".encode(), 1)
            rows, summary = self.parse(body)
            self.assertEqual(rows, [])
            self.assertEqual(summary["excluded_facilities"], 1)
            self.assertEqual(summary["excluded_releases"], 1)

    def test_missing_or_unknown_confidentiality_refuses_whole_result(self):
        for replacement in ["", "<p:ConfidentialIndicator>unknown</p:ConfidentialIndicator>"]:
            with self.assertRaises(FetchError):
                self.parse(
                    fixture(
                        RELEASE.replace(
                            "<p:ConfidentialIndicator>0</p:ConfidentialIndicator>", replacement
                        )
                    )
                )

    def test_unit_numeric_medium_and_method_changes_refuse_whole_result(self):
        for original, changed in [
            ("KGM", "TNE"),
            ("100.00", "NaN"),
            ("100.00", "-1"),
            ("100.00", "1e999999"),
            ("100.00", "INF"),
            (">AIR<", ">UNKNOWN<"),
            (">M<", ">X<"),
        ]:
            with self.subTest(changed=changed), self.assertRaises(FetchError):
                self.parse(fixture(RELEASE.replace(original, changed)))

    def test_scientific_notation_is_preserved_without_float_rounding(self):
        rows, _ = self.parse(fixture(RELEASE.replace("100.00", "1.000e2")))
        self.assertEqual(rows[0]["total_quantity"], "1.000e2")

    def test_duplicate_fields_and_release_identities_are_refused(self):
        for body in [
            fixture(RELEASE + RELEASE),
            fixture(root_extra="<p:CountryID>PL</p:CountryID>"),
            fixture(
                RELEASE.replace(
                    "<p:MediumCode>AIR</p:MediumCode>", "<p:MediumCode>AIR</p:MediumCode>" * 2
                )
            ),
        ]:
            with self.assertRaises(FetchError):
                self.parse(body)

    def test_country_namespace_root_and_year_are_exact(self):
        for old, new in [
            (b">PL<", b">DE<"),
            (b">2017<", b">2016<"),
            (NS.encode(), b"other"),
            (b">null<", b">2016<"),
        ]:
            with self.assertRaises(FetchError):
                self.parse(fixture().replace(old, new))

    def test_transfer_counts_do_not_enter_release_records(self):
        rows, summary = self.parse(
            fixture(facility_extra="<p:PollutantTransfer/><p:WasteTransfer/>")
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(summary["skipped_pollutant_transfers"], 1)
        self.assertEqual(summary["skipped_waste_transfers"], 1)

    def test_contacts_addresses_and_descriptions_never_enter_output(self):
        extra = (
            "<p:ContactPersonName>PRIVATE_CONTACT</p:ContactPersonName>"
            "<p:RemarkText>PRIVATE_NOTE</p:RemarkText>"
        )
        rows, summary = self.parse(fixture(facility_extra=extra))
        self.assertNotIn("PRIVATE_", json.dumps([rows, summary]))

    def test_dtd_entity_encoding_and_malformed_xml_are_refused(self):
        for body in [
            b'<!DOCTYPE x [<!ENTITY y "value">]>' + fixture(),
            fixture().replace(b"UTF-8", b"UTF-16"),
            fixture() + b"<extra>",
            fixture().replace(b"CO2", b"\xff"),
            fixture().replace(b"CO2", b"\x00"),
        ]:
            with self.assertRaises(FetchError):
                self.parse(body)

    def test_byte_node_depth_facility_and_release_budgets_fail_closed(self):
        module = import_module("ingestion.prtr_releases")
        for field, limit in [
            ("MAX_BYTES", 10),
            ("MAX_NODES", 5),
            ("MAX_DEPTH", 2),
            ("MAX_FACILITIES", 0),
            ("MAX_RELEASES", 0),
        ]:
            with patch.object(module, field, limit), self.assertRaises(FetchError):
                self.parse()

    def test_empty_root_missing_facility_flags_and_duplicate_facilities_are_refused(self):
        body = fixture()
        start = body.index(b"<p:FacilityReport>")
        end = body.index(b"</p:FacilityReport>") + len(b"</p:FacilityReport>")
        for changed in [
            body[:start] + body[end:],
            body[:end] + body[start:end] + body[end:],
            body.replace(b"<p:ProtectVoluntaryData>0</p:ProtectVoluntaryData>", b""),
        ]:
            with self.assertRaises(FetchError):
                self.parse(changed)

    def test_scalar_children_and_missing_quantity_units_are_refused(self):
        for old, new in [(b">CO2<", b"><p:Other>CO2</p:Other><"), (b' unitCode="KGM"', b"")]:
            with self.assertRaises(FetchError):
                self.parse(fixture().replace(old, new))


class PrtrReleaseCommandTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="1" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=49, release=release, master={}, technical={}
        )
        self.body = fixture()
        self.config = {
            **profile.CONFIG,
            "expected_sha256": hashlib.sha256(self.body).hexdigest(),
            "max_file_bytes": len(self.body),
        }
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug=profile.SLUG,
            kind="file",
            url=profile.URL,
            region="PL",
            config=self.config,
            access_approved=True,
        )
        with patch.dict(profile.CONFIG, self.config):
            self.job, _ = enqueue(self.channel)
            client = Mock()
            client.get.return_value = Response(200, self.body, {}, profile.URL)
            advance_download(self.job, client)

    def run_command(self):
        with (
            patch.dict(profile.CONFIG, self.config),
            patch.object(profile, "SIZE", len(self.body)),
            patch.object(profile, "SHA256", self.job.sha256),
        ):
            output = io.StringIO()
            call_command("read_prtr_releases", job=self.job.pk, stdout=output)
            return json.loads(output.getvalue())

    def test_reads_verified_copy_without_http_or_database_writes(self):
        before = (
            DownloadJob.objects.count(),
            DataSnapshot.objects.count(),
            self.channel.next_fetch_at,
        )
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("HTTP forbidden")):
            result = self.run_command()
        self.assertEqual(result["records"], 1)
        self.assertNotIn("Public facility", json.dumps(result))
        self.channel.refresh_from_db()
        self.assertEqual(
            before,
            (DownloadJob.objects.count(), DataSnapshot.objects.count(), self.channel.next_fetch_at),
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "complete")

    def test_expired_revoked_wrong_profile_and_corrupt_copy_are_refused(self):
        self.channel.access_approved = False
        self.channel.save()
        with self.assertRaises(CommandError):
            self.run_command()
        self.channel.access_approved = True
        self.channel.slug = "other-file"
        self.channel.save()
        with self.assertRaises(CommandError):
            self.run_command()
        self.channel.slug = profile.SLUG
        self.channel.save()
        self.job.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        self.job.save()
        with self.assertRaises(CommandError):
            self.run_command()
        self.job.expires_at = timezone.now() + timezone.timedelta(days=1)
        self.job.save()
        self.job.chunks.update(payload=b"corrupt")
        with self.assertRaises(CommandError):
            self.run_command()

    def test_unknown_job_and_unreviewed_original_are_refused(self):
        with self.assertRaises(CommandError):
            call_command("read_prtr_releases", job=999999, stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("read_prtr_releases", job=self.job.pk, stdout=io.StringIO())
