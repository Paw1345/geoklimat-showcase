from unittest.mock import patch

from django.test import TestCase

from ingestion.epic45_matrix import ACCESS_DECISIONS, OWNERS, inventory, owner
from ingestion.models import DataChannel, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError


class AcceptanceMatrixTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="public.xlsx", sha256="a" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            registry_id=189,
            release=release,
            organization="EU",
            name="Calls",
            technical_status="READY_STRUCTURED",
            integration_state="needs_connector",
            master={"Scope": "EU calls", "Poll interval (min)": 120},
            technical={},
        )

    def test_configuration_and_success_do_not_accept_scope(self):
        source = Source.objects.create(
            slug="reference",
            name="Reference",
            url="https://example.com/",
            enabled=True,
            scope="Catalogue only",
        )
        self.entry.sources.add(source)
        DataChannel.objects.create(
            registry=self.entry,
            slug="file",
            name="Historical file",
            kind="file",
            url="https://example.com/file",
            region="PL",
            attribution="Public",
            last_status="complete",
        )
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            report = inventory(1)
        row = report["entries"][0]
        self.assertEqual(row["expected_scope_raw"], "EU calls")
        self.assertEqual(row["delivered_information_channels"][0]["scope"], "Catalogue only")
        self.assertEqual(row["acceptance_status"], "not_reviewed")
        self.assertFalse(report["scope_approved"])
        self.assertFalse(report["payload_integrity_checked"])
        self.assertEqual(row["owner_subepic"], "4.5.6")

    def test_exact_count_and_unknown_gaps_fail_closed(self):
        with self.assertRaises(FetchError):
            inventory()
        self.entry.registry_id = 999
        with self.assertRaises(FetchError):
            owner(self.entry)

    def test_same_count_with_replaced_identity_is_not_the_same_baseline(self):
        ids = (set(range(1, 220)) - {1, 189}) | {999}
        RegistryEntry.objects.bulk_create(
            [
                RegistryEntry(
                    registry_id=identifier,
                    release=self.entry.release,
                    organization="Public",
                    name="Source",
                    technical_status="READY_HTML",
                    integration_state="configured",
                    master={},
                    technical={},
                )
                for identifier in ids
            ]
        )
        with self.assertRaisesMessage(FetchError, "identities differ"):
            inventory()

    def test_owner_sets_do_not_overlap_and_cover_34_primary_gaps(self):
        ids = [identifier for values in OWNERS.values() for identifier in values]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(set(ids) - {168}), 34)

    def test_previously_agreed_access_wait_is_not_acceptance(self):
        self.entry.registry_id = 193
        self.entry.integration_state = "linked"
        self.entry.save()
        row = inventory(1)["entries"][0]
        self.assertIn(193, ACCESS_DECISIONS)
        self.assertTrue(row["access_wait_previously_agreed"])
        self.assertEqual(row["acceptance_status"], "not_reviewed")

    def test_inventory_is_three_queries_and_does_not_mutate(self):
        before = RegistryEntry.objects.get(pk=self.entry.pk).master
        with self.assertNumQueries(3):
            report = inventory(1)
        self.assertEqual(report["acceptance_reviewed_count"], 0)
        self.assertFalse(report["database_writes"])
        self.assertEqual(RegistryEntry.objects.get(pk=self.entry.pk).master, before)
