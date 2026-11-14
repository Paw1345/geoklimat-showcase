import io
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from ingestion.adapters import Entry
from ingestion.marine import CONFIG, PARTIAL_CONFIG, URL
from ingestion.marine_audit import marine_policy_hash
from ingestion.marine_history import MarineAuditRecord, MarineProfileState
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.transport import Response


class MarineAuditLedgerWiringTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="d" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=195, release=release, master={}, technical={}
        )
        self.source = Source.objects.create(
            slug="registry-195",
            name="Marine",
            adapter="stac",
            url=URL,
            config=CONFIG.copy(),
            enabled=False,
            activation_approved=False,
        )
        self.registry.sources.add(self.source)
        self.result = ([Entry("key", "Sea", URL)], 0, Response(200, b"", {}, URL), "a" * 64)

    def audit(self, result=None):
        with patch(
            "ingestion.management.commands.audit_sources.fetch_entries",
            return_value=result or self.result,
        ):
            call_command("audit_sources", source=self.source.slug, stdout=io.StringIO())

    def activate(self):
        call_command(
            "activate_registry", source=[self.source.slug], apply=True, stdout=io.StringIO()
        )
        self.source.refresh_from_db()
        return self.source.enabled and self.source.activation_approved

    def bump_generation(self, profile):
        state = MarineProfileState.objects.get(source=self.source)
        state.profile = profile
        state.generation += 1
        state.save()

    def test_audit_appends_exact_record_for_current_generation(self):
        self.audit()
        self.source.refresh_from_db()
        record = MarineAuditRecord.objects.get(source=self.source)
        self.assertEqual(record.audit, self.source.last_audit)
        self.assertEqual(record.audited_at, self.source.audited_at)
        self.assertEqual(record.profile, "strict")
        self.assertEqual(record.generation, 0)
        self.assertEqual(record.policy_hash, marine_policy_hash(self.source))
        self.assertTrue(self.activate())

    def test_each_audit_is_kept_in_history(self):
        self.audit()
        self.audit()
        self.assertEqual(MarineAuditRecord.objects.filter(source=self.source).count(), 2)

    def test_audit_without_ledger_entry_cannot_activate(self):
        self.source.last_audit = {
            "source": self.source.slug,
            "url": URL,
            "policy_hash": marine_policy_hash(self.source),
            "status": "verified",
            "count": 1,
            "rejected": 0,
        }
        self.source.audited_at = timezone.now()
        self.source.save()
        self.assertFalse(self.activate())

    def test_profile_round_trip_invalidates_previous_audit_until_reaudit(self):
        self.audit()
        self.bump_generation("partial")
        self.bump_generation("strict")
        self.assertFalse(self.activate())
        self.audit()
        record = MarineAuditRecord.objects.filter(source=self.source).latest("pk")
        self.assertEqual(record.generation, 2)
        self.assertTrue(self.activate())

    def test_edited_last_audit_no_longer_matches_ledger(self):
        self.audit()
        audit = dict(Source.objects.get(pk=self.source.pk).last_audit)
        audit["count"] = 99
        Source.objects.filter(pk=self.source.pk).update(last_audit=audit)
        self.assertFalse(self.activate())

    def test_state_disagreeing_with_configuration_records_no_lineage(self):
        MarineProfileState.objects.create(source=self.source)
        self.bump_generation("partial")
        self.audit()
        record = MarineAuditRecord.objects.get(source=self.source)
        self.assertIsNone(record.generation)
        self.assertFalse(self.activate())

    def test_unreviewed_configuration_is_not_recorded(self):
        Source.objects.filter(pk=self.source.pk).update(config={"mode": "marine_products", "x": 1})
        self.audit()
        self.assertFalse(MarineAuditRecord.objects.exists())

    def test_partial_profile_audit_records_partial_profile_without_activation(self):
        Source.objects.filter(pk=self.source.pk).update(config=PARTIAL_CONFIG.copy())
        with patch("ingestion.marine.collect_catalogue", side_effect=RuntimeError("stub")):
            call_command(
                "audit_sources",
                source=self.source.slug,
                audit_marine_partial=True,
                stdout=io.StringIO(),
            )
        record = MarineAuditRecord.objects.get(source=self.source)
        self.assertEqual(record.profile, "partial")
        self.assertIsNone(record.generation)
        self.assertEqual(record.audit["status"], "unverified")
        self.assertFalse(self.activate())
