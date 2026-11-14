import io
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from ingestion.adapters import Entry
from ingestion.marine import CONFIG, URL
from ingestion.marine_audit import marine_policy_hash
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.transport import Response


class MarineAuditGuardTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="c" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=195, release=release, master={}, technical={}
        )
        self.source = Source.objects.create(
            slug="registry-195",
            name="Marine",
            adapter="stac",
            url=URL,
            config=CONFIG.copy(),
            enabled=True,
            activation_approved=True,
        )
        self.registry.sources.add(self.source)
        self.response = Response(200, b"", {}, URL)
        self.entries = [Entry("key", "Sea", URL)]

    def audit(self, rejected=0, side_effect=None):
        result = (self.entries, rejected, self.response, "a" * 64)
        with patch(
            "ingestion.management.commands.audit_sources.fetch_entries",
            side_effect=side_effect,
            return_value=result,
        ):
            call_command(
                "audit_sources", source=self.source.slug, enable_verified=True, stdout=io.StringIO()
            )

    def activate(self):
        call_command(
            "activate_registry", source=[self.source.slug], apply=True, stdout=io.StringIO()
        )

    def test_partial_audit_preserves_existing_enable_and_approval(self):
        self.audit(rejected=1)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit["status"], "partial")
        self.assertTrue(self.source.enabled)
        self.assertTrue(self.source.activation_approved)

    def test_changed_configuration_cannot_reuse_old_verified_audit(self):
        self.audit()
        Source.objects.filter(pk=self.source.pk).update(
            enabled=False, activation_approved=False, config={"mode": "different"}
        )
        self.activate()
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_old_audit_without_policy_fingerprint_cannot_activate(self):
        self.source.last_audit = {"status": "verified", "url": URL, "count": 1, "rejected": 0}
        self.source.audited_at = timezone.now()
        self.source.enabled = self.source.activation_approved = False
        self.source.save()
        self.activate()
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_fresh_matching_audit_can_activate(self):
        self.audit()
        Source.objects.filter(pk=self.source.pk).update(enabled=False, activation_approved=False)
        self.activate()
        self.source.refresh_from_db()
        self.assertTrue(self.source.enabled)
        self.assertTrue(self.source.activation_approved)

    def test_config_change_during_fetch_does_not_overwrite_admin_audit_or_flags(self):
        previous = {"status": "admin", "note": "keep"}
        Source.objects.filter(pk=self.source.pk).update(last_audit=previous)

        def change(*args, **kwargs):
            Source.objects.filter(pk=self.source.pk).update(
                config={"mode": "other"}, enabled=False, activation_approved=False
            )
            return (self.entries, 0, self.response, "a" * 64)

        self.audit(side_effect=change)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit, previous)
        self.assertEqual(self.source.config, {"mode": "other"})
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_new_partial_profile_is_not_audited_or_activated_automatically(self):
        self.source.config = {"mode": "marine_products", "incomplete_policy": "missing_metadata_v1"}
        self.source.save(update_fields=["config"])
        with patch("ingestion.management.commands.audit_sources.fetch_entries") as fetch:
            call_command(
                "audit_sources", source=self.source.slug, enable_verified=True, stdout=io.StringIO()
            )
        fetch.assert_not_called()
        self.source.refresh_from_db()
        self.assertTrue(self.source.enabled)
        self.assertTrue(self.source.activation_approved)
        self.assertNotEqual(self.source.last_audit["status"], "verified")

    def test_unapproved_partial_profile_preserves_flags_without_http(self):
        self.source.config = {"mode": "marine_products", "incomplete_policy": "missing_metadata_v1"}
        self.source.enabled = self.source.activation_approved = False
        self.source.save()
        with patch("ingestion.management.commands.audit_sources.fetch_entries") as fetch:
            call_command(
                "audit_sources", source=self.source.slug, enable_verified=True, stdout=io.StringIO()
            )
        fetch.assert_not_called()
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)
        self.activate()
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_mutation_of_in_memory_config_during_fetch_does_not_store_audit(self):
        previous = {"status": "admin"}
        Source.objects.filter(pk=self.source.pk).update(last_audit=previous)

        def mutate(source, *args, **kwargs):
            source.config["unexpected"] = True
            return (self.entries, 0, self.response, "a" * 64)

        self.audit(side_effect=mutate)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit, previous)
        self.assertEqual(self.source.config, CONFIG)

    def test_registry_unlinked_during_fetch_does_not_store_audit(self):
        previous = {"status": "admin"}
        Source.objects.filter(pk=self.source.pk).update(last_audit=previous)

        def unlink(*args, **kwargs):
            self.registry.sources.remove(self.source)
            return (self.entries, 0, self.response, "a" * 64)

        self.audit(side_effect=unlink)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit, previous)
        self.assertTrue(self.source.enabled)
        self.assertTrue(self.source.activation_approved)

    def test_extra_registry_link_during_fetch_does_not_store_audit(self):
        previous = {"status": "admin"}
        Source.objects.filter(pk=self.source.pk).update(last_audit=previous)
        other = RegistryEntry.objects.create(
            registry_id=196, release=self.registry.release, master={}, technical={}
        )

        def link_extra(*args, **kwargs):
            other.sources.add(self.source)
            return (self.entries, 0, self.response, "a" * 64)

        self.audit(side_effect=link_extra)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit, previous)
        self.assertTrue(self.source.enabled)
        self.assertTrue(self.source.activation_approved)

    def test_additional_registry_link_invalidates_previous_audit(self):
        self.audit()
        other = RegistryEntry.objects.create(
            registry_id=196, release=self.registry.release, master={}, technical={}
        )
        other.sources.add(self.source)
        Source.objects.filter(pk=self.source.pk).update(enabled=False, activation_approved=False)
        self.activate()
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_generic_candidate_changed_to_marine_mode_cannot_be_bulk_activated(self):
        other = Source.objects.create(
            slug="registry-196",
            name="Other",
            adapter="feed",
            url="https://example.org/feed",
            config={},
            enabled=False,
            activation_approved=False,
            audited_at=timezone.now(),
            last_audit={
                "status": "verified",
                "url": "https://example.org/feed",
                "count": 1,
                "rejected": 0,
            },
        )
        from ingestion.management.commands.activate_registry import marine_target

        def change_after_selection(source):
            result = marine_target(source)
            if source.pk == other.pk:
                Source.objects.filter(pk=other.pk).update(config={"mode": "marine_products"})
            return result

        with patch(
            "ingestion.management.commands.activate_registry.marine_target",
            side_effect=change_after_selection,
        ):
            call_command("activate_registry", source=[other.slug], apply=True, stdout=io.StringIO())
        other.refresh_from_db()
        self.assertFalse(other.enabled)
        self.assertFalse(other.activation_approved)

    def test_verified_audit_does_not_survive_other_policy_change(self):
        self.audit()
        Source.objects.filter(pk=self.source.pk).update(
            enabled=False, activation_approved=False, scope="changed"
        )
        self.activate()
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_partial_profile_cannot_activate_even_with_matching_hash_and_zero_rejections(self):
        self.source.config = {"mode": "marine_products", "incomplete_policy": "missing_metadata_v1"}
        self.source.enabled = self.source.activation_approved = False
        self.source.last_audit = {
            "status": "verified",
            "url": URL,
            "count": 1,
            "rejected": 0,
            "policy_hash": marine_policy_hash(self.source),
        }
        self.source.audited_at = timezone.now()
        self.source.save()
        self.activate()
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)
