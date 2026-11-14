import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ingestion.funding_calls import BASE, config, slug
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease


class FundingCopyContextTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="e" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            release=release, registry_id=189, master={}, technical={}
        )

    def channel(self, key="life"):
        return DataChannel.objects.create(
            registry=self.registry,
            slug=slug(key),
            name=key,
            kind="table",
            region="EU",
            url=BASE,
            config=config(key),
            enabled=True,
            access_approved=True,
        )

    def copy(self, channel, completeness=None):
        now = timezone.now()
        payload = json.dumps({"records": [{"identifier": "LIFE-2025-X", "state": "open"}]}).encode()
        summary = {
            "collector": "funding_calls",
            "kind": "funding_calls",
            "profile": channel.config["profile"],
            "scope": "EU",
            "as_of": (now - timedelta(days=2)).isoformat(),
            "records": 1,
            "state_open": 1,
            "state_forthcoming": 0,
            "state_later_deadline_only": 0,
            "state_deadline_passed": 0,
            "source_count": 1,
            "outside_scope": 0,
            "partial": False,
        }
        if completeness:
            summary["completeness"] = completeness
            summary["scan_passes"] = 4
        return DataSnapshot.objects.create(
            channel=channel,
            payload=payload,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type="application/json",
            fetched_at=now,
            expires_at=now + timedelta(days=30),
            summary=summary,
        )

    def test_life_copy_age_and_source_state_not_observation_window(self):
        channel = self.channel()
        snapshot = self.copy(channel)
        before = DataSnapshot.objects.values().get(pk=snapshot.pk)
        with patch(
            "ingestion.transport.Client.get", side_effect=AssertionError("No provider HTTP")
        ):
            response = self.client.get(f"/data/{channel.pk}/")
            exported = self.client.get(reverse("data_download", args=[snapshot.pk]))
        self.assertContains(response, "Lokalne kopie naborów UE")
        self.assertContains(response, "nie wieku terminów naborów")
        self.assertContains(response, "nie są przeliczane przy otwarciu strony")
        self.assertNotContains(response, "Pomiary IMGW/GIOŚ i sceny STAC")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.content, bytes(snapshot.payload))
        self.assertEqual(DataSnapshot.objects.values().get(pk=snapshot.pk), before)

    def test_horizon_convergence_is_not_completeness_even_when_partial_false(self):
        channel = self.channel("horizon")
        self.copy(channel, "converged_passes")
        response = self.client.get(f"/data/{channel.pk}/")
        self.assertContains(response, "Zbieżność przebiegów nie jest dowodem kompletności")
        self.assertContains(response, "Liczba przebiegów: 4")
        self.assertContains(response, "nie potwierdza aktualnej dostępności")

    def test_empty_funding_channel_still_explains_scope(self):
        channel = self.channel()
        response = self.client.get(f"/data/{channel.pk}/")
        self.assertContains(response, "Lokalne kopie naborów UE")
        self.assertContains(response, "Nie pobrano jeszcze danych")
        self.assertNotContains(response, "Zbieżność przebiegów nie jest dowodem kompletności")

    def test_other_channel_keeps_existing_observation_description(self):
        channel = self.channel()
        channel.config = {}
        channel.save(update_fields=["config"])
        response = self.client.get(f"/data/{channel.pk}/")
        self.assertContains(response, "Pomiary IMGW/GIOŚ i sceny STAC")
        self.assertNotContains(response, "Lokalne kopie naborów UE")
