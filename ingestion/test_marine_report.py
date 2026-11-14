import copy

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from ingestion.marine import URL
from ingestion.marine_models import MarineCatalogueReport, validate_report
from ingestion.models import IngestionRun, Source


def sample_report():
    return {
        "version": 1,
        "profile": "marine_missing_metadata_v1",
        "root_url": URL,
        "root_sha256": "a" * 64,
        "manifest": ["A", "B"],
        "inspected_count": 2,
        "valid_count": 1,
        "scan_complete": True,
        "metadata_complete": False,
        "issues": [
            {
                "product_id": "B",
                "url": "https://stac.marine.copernicus.eu/metadata/B/product.stac.json",
                "missing_fields": ["license"],
            }
        ],
    }


class MarineReportValidationTests(SimpleTestCase):
    def test_partial_and_complete_reports(self):
        report = sample_report()
        self.assertIsNone(validate_report(report))
        report.update(valid_count=2, issues=[], metadata_complete=True)
        self.assertIsNone(validate_report(report))

    def test_incomplete_scan_has_unknown_metadata_completeness(self):
        report = sample_report()
        report.update(
            inspected_count=1, valid_count=1, scan_complete=False, metadata_complete=None, issues=[]
        )
        self.assertIsNone(validate_report(report))
        report["metadata_complete"] = True
        with self.assertRaises(ValidationError):
            validate_report(report)

    def test_incomplete_scan_does_not_report_unread_id_as_inspected(self):
        report = sample_report()
        report.update(inspected_count=1, valid_count=0, scan_complete=False, metadata_complete=None)
        with self.assertRaises(ValidationError):
            validate_report(report)

    def test_rejects_invalid_shape_counts_ids_and_urls(self):
        changes = [
            {"version": 2},
            {"version": True},
            {"profile": "unreviewed"},
            {"root_sha256": "not-a-hash"},
            {"root_url": "https://example.org/"},
            {"manifest": ["A", "A"]},
            {"manifest": [f"P{i}" for i in range(501)]},
            {"inspected_count": True},
            {"inspected_count": 1},
            {"valid_count": 2},
            {"metadata_complete": True},
            {"issues": []},
            {"extra": "not permitted"},
            {
                "scan_complete": False,
                "metadata_complete": None,
                "inspected_count": 2,
                "valid_count": 0,
            },
        ]
        for change in changes:
            with self.subTest(change=change):
                report = sample_report()
                report.update(change)
                with self.assertRaises(ValidationError):
                    validate_report(report)

    def test_invalid_issues_are_not_accepted(self):
        for change in (
            {"product_id": "A"},
            {"url": "https://example.org/"},
            {"missing_fields": []},
            {"missing_fields": ["license", "license"]},
            {"missing_fields": ["contacts"]},
            {"contact": "private"},
        ):
            with self.subTest(change=change):
                report = sample_report()
                report["issues"][0].update(change)
                with self.assertRaises(ValidationError):
                    validate_report(report)

    def test_one_mib_limit_and_invalid_nested_values_fail_closed(self):
        for report in (
            {**sample_report(), "extra": "x" * (1024 * 1024)},
            {**sample_report(), "manifest": ["A", None]},
            {**sample_report(), "manifest": ["A", float("nan")]},
        ):
            with self.subTest():
                with self.assertRaises(ValidationError):
                    validate_report(report)

    def test_oversize_report_with_only_valid_fields_is_rejected(self):
        ids = [f"{i:03d}_" + "A" * 1396 for i in range(400)]
        report = sample_report()
        report.update(
            manifest=ids,
            inspected_count=len(ids),
            valid_count=0,
            issues=[
                {
                    "product_id": key,
                    "url": f"https://stac.marine.copernicus.eu/metadata/{key}/product.stac.json",
                    "missing_fields": ["license"],
                }
                for key in ids
            ],
        )
        with self.assertRaises(ValidationError):
            validate_report(report)


class MarineReportModelTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Marine", slug="registry-195", adapter="stac", url=URL
        )
        self.run = IngestionRun.objects.create(source=self.source)

    def test_historical_run_is_unknown_and_report_is_one_to_one(self):
        self.assertFalse(MarineCatalogueReport.objects.filter(run=self.run).exists())
        MarineCatalogueReport.objects.create(run=self.run, payload=sample_report())
        self.assertEqual(self.run.marine_report.payload["issues"][0]["product_id"], "B")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MarineCatalogueReport.objects.create(run=self.run, payload=sample_report())

    def test_save_rejects_invalid_payload_without_truncating(self):
        report = sample_report()
        report["version"] = 999
        with self.assertRaises(ValidationError):
            MarineCatalogueReport.objects.create(run=self.run, payload=report)
        self.assertFalse(MarineCatalogueReport.objects.filter(run=self.run).exists())

    def test_cannot_attach_report_to_an_unrelated_source(self):
        other = Source.objects.create(name="Other", slug="other", adapter="stac", url=URL)
        run = IngestionRun.objects.create(source=other)
        with self.assertRaises(ValidationError):
            MarineCatalogueReport.objects.create(run=run, payload=sample_report())
        self.assertFalse(MarineCatalogueReport.objects.filter(run=run).exists())

    def test_report_round_trip_keeps_complete_manifest_and_issues(self):
        payload = sample_report()
        saved = MarineCatalogueReport.objects.create(run=self.run, payload=copy.deepcopy(payload))
        saved.refresh_from_db()
        self.assertEqual(saved.payload, payload)

    def test_read_validation_rejects_bypassed_or_legacy_unknown_version(self):
        report = MarineCatalogueReport.objects.create(run=self.run, payload=sample_report())
        invalid_payload = sample_report()
        invalid_payload["version"] = 2
        MarineCatalogueReport.objects.filter(pk=report.pk).update(payload=invalid_payload)
        report.refresh_from_db()
        with self.assertRaises(ValidationError):
            validate_report(report.payload)
