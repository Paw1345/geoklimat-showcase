from unittest.mock import patch

from django.test import TestCase

from ingestion.gios_catalogue import DATASETS, catalogue_for_registry
from ingestion.models import RegistryEntry, RegistryRelease, Source


class GiosCatalogueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        release = RegistryRelease.objects.create(filename="test", sha256="d" * 64, sheets={})
        for key in (6, 46, 47, 48, 49, 50, 170):
            RegistryEntry.objects.create(
                registry_id=key,
                release=release,
                priority="P1",
                integration_state="needs_connector",
                master={},
                technical={},
            )

    def test_mapping_is_shared_with_discovery_and_filtered(self):
        self.assertEqual({x["dataset_id"] for x in catalogue_for_registry(6)}, set(DATASETS))
        for registry, expected in ((46, 1), (47, 2), (48, 1), (49, 1), (50, 1)):
            rows = catalogue_for_registry(registry)
            self.assertEqual(len(rows), expected)
            self.assertTrue(all(row["registry_id"] == registry for row in rows))
        self.assertEqual(catalogue_for_registry(170), [])

    def test_pages_are_read_only_without_remote_requests(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("Unexpected HTTP")):
            for key in (6, 46, 47, 48, 49, 50):
                response = self.client.get(f"/registry/{key}/")
                self.assertContains(response, "Rozpoznane katalogi API GIOŚ")
                self.assertContains(response, "nie działające kanały pomiarowe")
                self.assertContains(response, "2026-09-30")
                self.assertContains(response, "nie bieżący test dostępności")
                self.assertContains(response, "needs_connector")
        self.assertFalse(Source.objects.exists())
        self.assertEqual(
            RegistryEntry.objects.filter(integration_state="needs_connector").count(), 7
        )

    def test_other_registry_does_not_show_gios_notice_and_post_rejected(self):
        self.assertNotContains(self.client.get("/registry/170/"), "Rozpoznane katalogi API GIOŚ")
        self.assertEqual(self.client.post("/registry/46/").status_code, 405)
