import io
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.models import Item, Publication, RegistryEntry, RegistryRelease, Source
from ingestion.pig_services import PROFILES, slug
from ingestion.pig_services import config as pig_config
from ingestion.service import fetch_entries, run_batch
from ingestion.transport import FetchError, Response

XML = b"""<WMS_Capabilities xmlns="http://www.opengis.net/wms" version="1.3.0">
<Service><Name>WMS</Name><Title>Public service</Title>
<AccessConstraints>Terms</AccessConstraints></Service>
<Capability><Layer><Title>Group</Title><Layer><Name>0</Name><Title>Layer A</Title></Layer>
<Layer><Name>1</Name><Title>Layer B</Title></Layer></Layer></Capability></WMS_Capabilities>"""


class PigServicesTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug=slug("midas"),
            name="MIDAS",
            adapter="ogc",
            url=PROFILES["midas"]["url"],
            config=pig_config("midas"),
            enabled=True,
            activation_approved=True,
        )
        self.http = Mock()
        self.http.get.return_value = Response(200, XML, {}, self.source.url)

    def test_contract_layers_no_child_fetch_or_invented_dates(self):
        entries, bad, result, digest = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), bad, result.status), (3, 0, 200))
        self.assertEqual(len(digest), 64)
        self.assertEqual(len({e.key for e in entries}), 3)
        self.assertEqual(entries[1].metadata["layer_name"], "0")
        for entry in entries:
            self.assertEqual(entry.metadata["record_kind"], "service_capabilities")
            self.assertFalse(entry.metadata["resources_downloaded"])
            self.assertEqual((entry.published, entry.updated), ("", ""))
            self.assertEqual(entry.metadata["access_constraints_raw"], "Terms")
        self.http.get.assert_called_once()

    def test_each_layer_remains_separate_in_search_groups(self):
        with patch("ingestion.service.Client", return_value=self.http):
            run_batch(source_slug=self.source.slug, force=True)
        self.assertEqual(
            Item.objects.filter(source=self.source).values("publication_id").distinct().count(), 3
        )

    def test_repair_dry_apply_repeat_and_confirmed_group_guard(self):
        self.source.delete()
        for key, profile in PROFILES.items():
            Source.objects.create(
                slug=slug(key), adapter="ogc", url=profile["url"], config=pig_config(key)
            )
        source = Source.objects.get(slug=slug("midas"))
        group = Publication.objects.create()
        for i in range(3):
            Item.objects.create(
                source=source,
                external_id=str(i),
                title=f"Layer{i}",
                url=source.url,
                metadata={"record_kind": "service_capabilities"},
                publication=group,
            )
        call_command("repair_pig_groups", stdout=io.StringIO())
        self.assertEqual(Publication.objects.count(), 1)
        group.primary_confirmed = True
        group.save()
        with self.assertRaises(CommandError):
            call_command("repair_pig_groups", apply=True, stdout=io.StringIO())
        self.assertEqual(Publication.objects.count(), 1)
        group.primary_confirmed = False
        group.save()
        call_command("repair_pig_groups", apply=True, stdout=io.StringIO())
        call_command("repair_pig_groups", apply=True, stdout=io.StringIO())
        self.assertEqual(Publication.objects.count(), 3)
        self.assertEqual(Item.objects.count(), 3)
        self.assertEqual(Item.objects.values("publication_id").distinct().count(), 3)

    def test_challenge_entities_wrong_schema_and_duplicate_layers_fail_closed(self):
        for body in (
            b"<html>Request unsuccessful. Incapsula</html>",
            b"broken XML",
            b'<!DOCTYPE a [<!ENTITY x "a">]>' + XML,
            XML.replace(b"1.3.0", b"1.1.1"),
            XML.replace(b"http://www.opengis.net/wms", b"urn:wrong"),
            XML.replace(b"<Name>1</Name>", b"<Name>0</Name>"),
            XML.replace(b"<Name>1</Name>", b"<Name/>"),
            XML.replace(b"<Title>Layer A</Title>", b"<Title/>"),
            XML.decode().encode("utf-16"),
        ):
            with self.subTest(body=body[:35]):
                self.http.get.return_value = Response(200, body, {}, self.source.url)
                with self.assertRaises(FetchError):
                    fetch_entries(self.source, self.http)

    def test_exact_profile_response_and_budget(self):
        self.http.get.return_value.url += "&other=1"
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)
        self.http.get.return_value = Response(200, XML, {}, self.source.url)
        self.source.config["other"] = True
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)
        self.source.config = pig_config("midas")
        with patch("ingestion.pig_services.PROFILES", {}), self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)

    def test_repeat_idempotent_and_failed_batch_writes_no_partial_items(self):
        with patch("ingestion.service.Client", return_value=self.http):
            run_batch(source_slug=self.source.slug, force=True)
            run_batch(source_slug=self.source.slug, force=True)
            self.assertEqual(Item.objects.filter(source=self.source).count(), 3)
            self.assertEqual(self.source.runs.latest("pk").unchanged, 3)
            self.http.get.return_value.body = XML.replace(b"Layer A", b"Layer A changed")
            run_batch(source_slug=self.source.slug, force=True)
            self.assertEqual(self.source.runs.latest("pk").updated, 2)
            self.assertEqual(Item.objects.filter(source=self.source).count(), 3)
            self.http.get.return_value.body = XML.replace(b"<Name>1</Name>", b"<Name>0</Name>")
            run_batch(source_slug=self.source.slug, force=True)
            self.assertEqual(Item.objects.filter(source=self.source).count(), 3)
            self.assertEqual(self.source.runs.latest("pk").status, "failed")

    def test_configure_dry_apply_repeat_preserves_registry_and_admin(self):
        self.source.delete()
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="a" * 64, sheets={})
        for i in (36, 37, 62, 63, 64, 65):
            RegistryEntry.objects.create(
                release=release,
                registry_id=i,
                name=f"PIG{i}",
                organization="PIG-PIB",
                priority="P1",
                integration_state="needs_connector",
                master={"original": True},
                technical={},
            )
        call_command("configure_pig_services", stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), 0)
        call_command("configure_pig_services", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), 4)
        self.assertFalse(Source.objects.filter(enabled=True).exists())
        self.assertFalse(Source.objects.filter(activation_approved=True).exists())
        call_command("configure_pig_services", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), 4)
        for entry in RegistryEntry.objects.all():
            self.assertEqual(entry.integration_state, "needs_connector")
            self.assertEqual(entry.master, {"original": True})
        edited = Source.objects.get(slug=slug("hydro"))
        edited.config = {"admin": True}
        edited.save()
        with self.assertRaises(CommandError):
            call_command("configure_pig_services", apply=True, stdout=io.StringIO())
        edited.refresh_from_db()
        self.assertEqual(edited.config, {"admin": True})
