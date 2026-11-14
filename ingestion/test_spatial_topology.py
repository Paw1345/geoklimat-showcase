import json
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TransactionTestCase, override_settings

from ingestion import test_spatial_geometry as geometry_tests
from ingestion import test_spatial_query as query_tests
from ingestion.models import DataSnapshot
from ingestion.spatial_topology import report

INVALID, polygon = geometry_tests.INVALID, geometry_tests.polygon


class TopologyReportTests(TransactionTestCase):
    def setUp(self):
        query_tests.SpatialTests.setUp(self)

    replace_geometry = geometry_tests.GeometryQueryTests.replace_geometry

    def test_clean_scan_reports_no_defects_without_http_or_writes(self):
        before = list(DataSnapshot.objects.values_list("pk", "sha256"))
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            result = report(self.channel.pk)
        self.assertEqual(result["features_checked"], 2)
        self.assertEqual(result["invalid_features"], 0)
        self.assertTrue(result["complete_scope_checked"])
        self.assertFalse(result["repaired"])
        self.assertFalse(result["data_modified"])
        self.assertEqual(before, list(DataSnapshot.objects.values_list("pk", "sha256")))

    def test_invalid_geometry_listed_with_id_page_and_evidence(self):
        self.replace_geometry(2, polygon([INVALID]))
        snapshot = DataSnapshot.objects.get(summary__page=2)
        before = bytes(snapshot.payload)
        result = report(self.channel.pk)
        self.assertEqual(result["invalid_features"], 1)
        defect = result["defects"][0]
        self.assertEqual(defect["object_id"], 2)
        self.assertEqual(defect["page"], 2)
        self.assertEqual(defect["snapshot_id"], snapshot.pk)
        self.assertEqual(defect["sha256"], snapshot.sha256)
        self.assertEqual(defect["reason"], "topology")
        self.assertIn("Self-intersection", defect["detail"])
        snapshot.refresh_from_db()
        self.assertEqual(bytes(snapshot.payload), before)

    def test_tampered_copy_gives_no_partial_evidence(self):
        self.replace_geometry(2, polygon([INVALID]))
        DataSnapshot.objects.filter(summary__page=2).update(sha256="0" * 64)
        result = report(self.channel.pk)
        self.assertEqual(result["features_checked"], 0)
        self.assertEqual(result["defects"], [])
        self.assertFalse(result["complete_scope_checked"])
        self.assertEqual(result["batches"][0]["payload_check"], "failed")

    def test_batches_cover_every_page(self):
        with patch("ingestion.spatial_topology.COPY_BATCH", 1):
            result = report(self.channel.pk)
        self.assertEqual([b["pages"] for b in result["batches"]], [[1, 1], [2, 2]])
        self.assertEqual(result["verified_batches"], 2)
        self.assertTrue(result["complete_scope_checked"])

    def test_batches_split_by_byte_budget_and_oversize_copy_is_refused(self):
        sizes = list(
            DataSnapshot.objects.order_by("summary__page").values_list("size_bytes", flat=True)
        )
        with override_settings(DATA_MANUAL_MAX_BYTES=max(sizes)):
            result = report(self.channel.pk)
        self.assertEqual([b["pages"] for b in result["batches"]], [[1, 1], [2, 2]])
        self.assertTrue(result["complete_scope_checked"])
        with override_settings(DATA_MANUAL_MAX_BYTES=min(sizes) - 1):
            result = report(self.channel.pk)
        self.assertEqual(len(result["batches"]), 2)
        self.assertFalse(result["complete_scope_checked"])
        self.assertTrue(all(b["payload_check"].startswith("refused") for b in result["batches"]))
        self.assertEqual(result["features_checked"], 0)

    def test_command_outputs_json_and_refuses_unknown_channel(self):
        self.replace_geometry(1, polygon([INVALID]))
        out = StringIO()
        call_command("spatial_topology_report", source=self.channel.slug, stdout=out)
        self.assertEqual(json.loads(out.getvalue())["defects"][0]["object_id"], 1)
        with self.assertRaises(CommandError):
            call_command("spatial_topology_report", source="missing")
