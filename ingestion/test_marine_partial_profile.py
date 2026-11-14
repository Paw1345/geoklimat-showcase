import hashlib
import io
import json
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.marine import CONFIG, PARTIAL_CONFIG, URL, fetch_catalogue, product_url
from ingestion.marine_models import MarineCatalogueReport
from ingestion.models import IngestionRun, Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


class MarinePartialProfileTests(TestCase):
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
            config=PARTIAL_CONFIG.copy(),
            enabled=False,
            activation_approved=False,
            last_audit={"status": "previous"},
        )
        self.registry.sources.add(self.source)
        self.root = json.dumps(
            {
                "type": "Catalog",
                "id": "MDS",
                "links": [
                    {"rel": "child", "href": f"{key}/product.stac.json"} for key in ("P1", "P2")
                ],
            }
        ).encode()
        self.products = [
            {
                "type": "Collection",
                "id": "P1",
                "title": "Sea",
                "license": "CC-BY",
                "properties": {},
            },
            {"type": "Collection", "id": "P2", "title": "Sea 2", "license": None, "properties": {}},
        ]
        self.http = Mock()

    def replies(self):
        return [Response(200, self.root, {}, URL)] + [
            Response(
                200, json.dumps(row).encode(), {}, product_url(f"{row['id']}/product.stac.json")
            )
            for row in self.products
        ]

    def audit(self):
        with patch("ingestion.management.commands.audit_sources.Client", return_value=self.http):
            call_command(
                "audit_sources",
                source=self.source.slug,
                enable_verified=True,
                audit_marine_partial=True,
                stdout=io.StringIO(),
            )

    def test_partial_audit_on_disabled_profile_preserves_flags_and_items(self):
        before = Source.objects.values().get(pk=self.source.pk)
        self.http.get.side_effect = self.replies()
        self.audit()
        self.source.refresh_from_db()
        audit = self.source.last_audit
        self.assertEqual(
            (audit["status"], audit["count"], audit["rejected"]), ("manual_review", 1, 1)
        )
        self.assertTrue(audit["scan_complete"])
        self.assertFalse(audit["metadata_complete"])
        self.assertEqual(audit["root_sha256"], hashlib.sha256(self.root).hexdigest())
        self.assertEqual(audit["marine_report"]["manifest"], ["P1", "P2"])
        self.assertEqual(audit["marine_report"]["issues"][0]["product_id"], "P2")
        self.assertEqual(self.http.get.call_count, 3)
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)
        self.assertFalse(Item.objects.exists())
        self.assertFalse(IngestionRun.objects.exists())
        self.assertFalse(MarineCatalogueReport.objects.exists())
        after = Source.objects.values().get(pk=self.source.pk)
        for field in before:
            if field not in {"last_audit", "audited_at"}:
                self.assertEqual(after[field], before[field], field)

    def test_complete_partial_audit_still_cannot_auto_activate(self):
        self.products[1]["license"] = "CC-BY"
        self.http.get.side_effect = self.replies()
        self.audit()
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit["status"], "manual_review")
        self.assertTrue(self.source.last_audit["metadata_complete"])
        call_command(
            "activate_registry", source=[self.source.slug], apply=True, stdout=io.StringIO()
        )
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_active_or_approved_profile_is_not_audited(self):
        for field in ("enabled", "activation_approved"):
            with self.subTest(field=field):
                Source.objects.filter(pk=self.source.pk).update(**{field: True})
                self.http.reset_mock()
                self.audit()
                self.http.get.assert_not_called()
                self.source.refresh_from_db()
                self.assertTrue(getattr(self.source, field))
                Source.objects.filter(pk=self.source.pk).update(**{field: False})

    def test_policy_change_during_audit_does_not_overwrite_new_state(self):
        responses = iter(self.replies())

        def fetch(_url):
            response = next(responses)
            if response.url.endswith("P2/product.stac.json"):
                Source.objects.filter(pk=self.source.pk).update(
                    config=CONFIG, last_audit={"status": "admin"}
                )
            return response

        self.http.get.side_effect = fetch
        self.audit()
        self.source.refresh_from_db()
        self.assertEqual(self.source.config, CONFIG)
        self.assertEqual(self.source.last_audit, {"status": "admin"})
        self.assertFalse(self.source.enabled)

    def test_permission_or_link_change_during_audit_keeps_previous_audit(self):
        for change in ("enabled", "activation_approved", "linkage"):
            with self.subTest(change=change):
                Source.objects.filter(pk=self.source.pk).update(last_audit={"status": "previous"})
                self.registry.sources.add(self.source)
                responses = iter(self.replies())

                def fetch(_url, responses=responses, change=change):
                    response = next(responses)
                    if response.url.endswith("P2/product.stac.json"):
                        if change == "linkage":
                            self.registry.sources.remove(self.source)
                        else:
                            Source.objects.filter(pk=self.source.pk).update(**{change: True})
                    return response

                self.http.get.side_effect = fetch
                self.audit()
                self.source.refresh_from_db()
                self.assertEqual(self.source.last_audit, {"status": "previous"})
                self.assertFalse(Item.objects.exists())
                Source.objects.filter(pk=self.source.pk).update(
                    enabled=False, activation_approved=False
                )

    def test_partial_audit_requires_exact_source_selection(self):
        with self.assertRaises(CommandError):
            call_command("audit_sources", audit_marine_partial=True, stdout=io.StringIO())
        self.http.get.assert_not_called()

    def test_unreviewed_identity_or_linkage_never_fetches_or_overwrites_audit(self):
        for field, value in (
            ("adapter", "feed"),
            ("url", "https://example.org/"),
            ("config", {**PARTIAL_CONFIG, "extra": True}),
            ("linkage", None),
        ):
            with self.subTest(field=field):
                if field == "linkage":
                    self.registry.sources.remove(self.source)
                else:
                    Source.objects.filter(pk=self.source.pk).update(**{field: value})
                self.http.reset_mock()
                self.audit()
                self.http.get.assert_not_called()
                self.source.refresh_from_db()
                self.assertEqual(self.source.last_audit, {"status": "previous"})
                Source.objects.filter(pk=self.source.pk).update(
                    adapter="stac", url=URL, config=PARTIAL_CONFIG
                )
                self.registry.sources.add(self.source)

    def test_partial_audit_does_not_run_when_batch_is_busy(self):
        from contextlib import nullcontext

        with patch(
            "ingestion.management.commands.audit_sources.batch_lock",
            return_value=nullcontext(False),
        ):
            self.audit()
        self.http.get.assert_not_called()
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit, {"status": "previous"})

    def test_all_invalid_products_report_failure_without_activation(self):
        self.products[0]["title"] = None
        self.http.get.side_effect = self.replies()
        self.audit()
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit["status"], "unverified")
        self.assertNotIn("marine_report", self.source.last_audit)
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_configurator_refuses_running_or_active_source(self):
        Source.objects.filter(pk=self.source.pk).update(config=CONFIG)
        for field in ("enabled", "activation_approved"):
            with self.subTest(field=field):
                Source.objects.filter(pk=self.source.pk).update(**{field: True})
                with self.assertRaises(CommandError):
                    call_command("configure_marine_partial", stdout=io.StringIO())
                Source.objects.filter(pk=self.source.pk).update(**{field: False})
        IngestionRun.objects.create(source=self.source, status="running")
        with self.assertRaises(CommandError):
            call_command("configure_marine_partial", stdout=io.StringIO())

    def test_strict_fetch_and_scheduler_remain_closed_to_partial_profile(self):
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.http)
        self.http.get.assert_not_called()

    def test_configurator_defaults_to_dry_run(self):
        Source.objects.filter(pk=self.source.pk).update(config=CONFIG)
        before = Source.objects.values().get(pk=self.source.pk)
        output = io.StringIO()
        call_command("configure_marine_partial", stdout=output)
        self.assertIn("Dry-run", output.getvalue())
        self.assertEqual(Source.objects.values().get(pk=self.source.pk), before)
