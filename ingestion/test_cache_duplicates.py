import io
import json
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease


class CacheDuplicatesTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=1, release=release, name="Test", master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry, slug="stac-test", kind="stac", name="Test", region="PL"
        )

    def add(self, *, link="a", collection="sentinel", value=1, expired=False, raw=None):
        payload = (
            raw
            or json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "collection": collection,
                            "id": "scene",
                            "properties": {"value": value},
                        }
                    ],
                    "links": [{"href": link}],
                }
            ).encode()
        )
        return DataSnapshot.objects.create(
            channel=self.channel,
            payload=payload,
            size_bytes=len(payload),
            expires_at=timezone.now() + timedelta(days=-1 if expired else 1),
            summary={"harvest_id": 1},
        )

    def report(self, **kwargs):
        output = io.StringIO()
        call_command("cache_duplicates", source=self.channel.slug, stdout=output, **kwargs)
        return json.loads(output.getvalue())

    def test_links_are_separate_from_content_and_report_does_not_write(self):
        self.add()
        self.add(link="b")
        self.add(link="b")
        before = list(DataSnapshot.objects.values())
        result = self.report()
        self.assertEqual(result["exact_duplicate_pages"], 1)
        self.assertEqual(result["pages_equal_except_links"], 2)
        self.assertEqual(result["identical_scene_occurrences"], 2)
        self.assertEqual(result["unique_scene_ids"], 1)
        self.assertEqual(list(DataSnapshot.objects.values()), before)

    def test_changed_scene_and_collection_are_not_duplicates(self):
        self.add()
        self.add(value=2)
        self.add(collection="other")
        result = self.report()
        self.assertEqual(result["pages_equal_except_links"], 0)
        self.assertEqual(result["identical_scene_occurrences"], 0)
        self.assertEqual(result["additional_scene_variants"], 1)
        self.assertEqual(result["unique_scene_ids"], 2)

    def test_limit_expiry_and_invalid_payload(self):
        self.add(expired=True)
        self.add()
        self.add(raw=b"not JSON")
        result = self.report(limit=1)
        self.assertEqual(result["available_pages"], 2)
        self.assertEqual(result["scanned_pages"], 1)
        self.assertEqual(result["invalid_pages"], 1)
        self.assertTrue(result["truncated"])

    def test_invalid_arguments(self):
        for limit in (0, -1, 10001):
            with self.assertRaises(CommandError):
                self.report(limit=limit)
        self.channel.kind = "file"
        self.channel.save()
        with self.assertRaises(CommandError):
            self.report()
