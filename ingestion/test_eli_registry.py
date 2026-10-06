import io
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ingestion.catalog import CATALOG
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source


class EliRegistryTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="e" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=23,
            release=release,
            master={},
            technical={},
            integration_state="configured",
            integration_note="Preserve note",
        )
        self.parent = RegistryEntry.objects.create(
            registry_id=22,
            release=release,
            master={},
            technical={},
        )
        self.legacy = Source.objects.create(
            slug="registry-023",
            adapter="web",
            url="https://eli.gov.pl/",
            enabled=False,
            activation_approved=False,
        )
        self.registry.sources.add(self.legacy)
        self.sources = []
        for profile in CATALOG:
            if profile["slug"] not in {"eli-du", "eli-mp"}:
                continue
            source = Source.objects.create(
                slug=profile["slug"],
                adapter="eli",
                url=profile["url"],
                scope=profile["scope"],
                enabled=True,
                activation_approved=True,
                audited_at=timezone.now(),
                last_audit={"status": "verified", "count": 1, "rejected": 0},
                interval_minutes=360,
                owner="Custom owner",
            )
            self.parent.sources.add(source)
            self.sources.append(source)

    def run_command(self, apply=False):
        output = io.StringIO()
        call_command("link_eli_registry", apply=apply, stdout=output)
        return output.getvalue()

    def test_dry_apply_repeat_no_http_or_source_mutation(self):
        before = list(Source.objects.order_by("pk").values())
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.assertIn("Dry-run", self.run_command())
            self.assertEqual(list(self.registry.sources.all()), [self.legacy])
            self.run_command(True)
            self.assertIn("unchanged", self.run_command(True))
        self.assertEqual(list(Source.objects.order_by("pk").values()), before)
        self.assertEqual(
            set(self.registry.sources.values_list("pk", flat=True)), {s.pk for s in self.sources}
        )
        self.assertEqual(self.parent.sources.count(), 2)
        self.registry.refresh_from_db()
        self.assertEqual(self.registry.integration_note, "Preserve note")
        self.assertEqual(self.registry.integration_state, "configured")
        self.assertTrue(Source.objects.filter(pk=self.legacy.pk).exists())

    def test_busy_refuses_without_changes(self):
        from contextlib import contextmanager

        @contextmanager
        def busy():
            yield False

        with patch("ingestion.management.commands.link_eli_registry.batch_lock", busy):
            with self.assertRaises(CommandError):
                self.run_command(True)
        self.assertEqual(self.registry.sources.count(), 1)

    def test_missing_collector_refuses(self):
        self.sources[0].delete()
        with self.assertRaises(CommandError):
            self.run_command(True)
        self.assertEqual(self.registry.sources.count(), 1)

    def test_admin_profile_or_linkage_refuses_atomically(self):
        source = self.sources[1]
        source.config = {"admin": True}
        source.save()
        with self.assertRaises(CommandError):
            self.run_command(True)
        self.assertEqual(self.registry.sources.count(), 1)
        source.config = {}
        source.save()
        self.registry.sources.add(source)
        with self.assertRaises(CommandError):
            self.run_command(True)
        self.assertTrue(self.registry.sources.filter(pk=self.legacy.pk).exists())

    def test_legacy_history_or_configuration_preserved(self):
        Item.objects.create(
            source=self.legacy,
            external_id="x",
            title="History",
            url="https://eli.gov.pl/",
            content_hash="a" * 64,
        )
        with self.assertRaises(CommandError):
            self.run_command(True)
        self.assertEqual(self.registry.sources.count(), 1)
        self.assertEqual(Item.objects.count(), 1)

    def test_stale_partial_disabled_audit_refuses(self):
        source = self.sources[0]
        for audit, stamp, enabled in (
            (
                {"status": "verified", "count": 1, "rejected": 0},
                timezone.now() - timedelta(minutes=16),
                True,
            ),
            ({"status": "verified", "count": 1, "rejected": 1}, timezone.now(), True),
            ({"status": "verified", "count": True, "rejected": 0}, timezone.now(), True),
            ({"status": "verified", "count": 1, "rejected": 0}, timezone.now(), False),
        ):
            source.last_audit, source.audited_at, source.enabled = audit, stamp, enabled
            source.save()
            with self.assertRaises(CommandError):
                self.run_command(True)
            self.assertEqual(self.registry.sources.count(), 1)
