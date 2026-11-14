from copy import deepcopy
from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import TransactionTestCase

from ingestion.epic45_scope_cards import build_cards, cell, markdown, scope_cards
from ingestion.models import DataChannel, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError


class ScopeCardTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="a" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            release=release,
            registry_id=189,
            organization="EU",
            name="Calls",
            integration_state="needs_connector",
            technical_status="READY_STRUCTURED",
            master={"Scope": "Actual calls and documents", "Topics": "environment"},
            technical={},
        )
        self.source = Source.objects.create(
            slug="catalogue",
            name="Documents",
            url="https://example.com/",
            scope="Metadata only",
            activation_approved=True,
            enabled=True,
        )
        self.entry.sources.add(self.source)
        self.channel = DataChannel.objects.create(
            registry=self.entry,
            slug="sample",
            name="Sample",
            url="https://example.com/data",
            kind="file",
            access_approved=True,
            enabled=False,
            attribution="Public",
        )

    def test_expected_scope_preserved_no_automatic_acceptance(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            report = scope_cards(1)
        card = report["cards"][0]
        self.assertEqual(card["expected_scope_raw"], "Actual calls and documents")
        self.assertEqual(card["information_states"][0]["scope"], "Metadata only")
        self.assertEqual(card["card_id"], "R189")
        self.assertEqual(card["owner_subepic"], "4.5.6")
        self.assertEqual(card["acceptance_status"], "not_reviewed")
        self.assertFalse(report["scope_approved"])
        self.assertFalse(report["database_writes"])
        self.assertFalse(report["payload_integrity_checked"])
        self.assertEqual(card["work_items"][0]["owner"], "assistant_and_operator")
        self.assertIn("R189-INTEGRATION", [t["id"] for t in card["work_items"]])
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.master["Scope"], "Actual calls and documents")

    def test_snapshot_rejects_database_writes(self):
        from ingestion.epic45_matrix import inventory

        def attempt_write(*args, **kwargs):
            Source.objects.filter(pk=self.source.pk).update(enabled=False)
            return inventory(*args, **kwargs)

        with patch("ingestion.epic45_scope_cards.inventory", side_effect=attempt_write):
            with self.assertRaises(DatabaseError):
                scope_cards(1)
        self.source.refresh_from_db()
        self.assertTrue(self.source.enabled)

    def test_mismatched_and_duplicate_identities_refuse(self):
        from core.registry_readiness import report as readiness
        from ingestion.epic45_matrix import inventory

        matrix, operational = inventory(1), readiness()
        wrong_id = deepcopy(operational)
        wrong_id["rows"][0]["registry_id"] = 999
        duplicate = deepcopy(operational)
        duplicate["rows"].append(deepcopy(duplicate["rows"][0]))
        for altered in (wrong_id, duplicate):
            with self.assertRaises(FetchError):
                build_cards(matrix, altered)
        matrix["entries"].append(deepcopy(matrix["entries"][0]))
        with self.assertRaises(FetchError):
            build_cards(matrix, operational)

    def test_provider_hold_and_ems_review_not_local_acceptance(self):
        for identifier in (62, 196, 172):
            self.entry.registry_id = identifier
            self.entry.integration_state = "configured" if identifier == 172 else "needs_connector"
            self.entry.save()
            card = scope_cards(1)["cards"][0]
            states = {t["state"] for t in card["work_items"]}
            expected = {62: "blocked", 196: "requires_terms_review", 172: "previously_skipped"}
            self.assertIn(expected[identifier], states)
            self.assertEqual(card["acceptance_status"], "not_reviewed")

    def test_missing_contract_fields_are_unknown_and_output_is_bounded(self):
        report = scope_cards(1)
        card = report["cards"][0]
        self.assertIn("product_layer_operation", card["unconfirmed_contract_fields"])
        text = markdown(report)
        self.assertIn("Rejestr SHA256: " + "a" * 64, text)
        self.assertIn("UNCONFIRMED", text)
        self.assertIn("R189-SCOPE", text)
        self.assertIn("Actual calls and documents", text)
        with patch("ingestion.epic45_scope_cards.RESULT_BYTES", 10):
            with self.assertRaises(FetchError):
                markdown(report)

    def test_markdown_untrusted_scope_not_active_markup(self):
        value = cell("<script>alert(1)</script> | [x](https://example.com)\n`code`")
        self.assertNotIn("<script>", value)
        self.assertIn("&lt;script&gt;", value)
        self.assertIn("\\|", value)
        self.assertIn("\\[", value)
        self.assertNotIn("\n", value)

    def test_full_219_identity_contract_not_bypassed(self):
        with self.assertRaises(FetchError):
            scope_cards()
        self.assertEqual(RegistryEntry.objects.count(), 1)
        self.assertEqual(Source.objects.count(), 1)
        self.assertEqual(DataChannel.objects.count(), 1)

    def test_read_only_transaction_state(self):
        from ingestion.epic45_matrix import inventory

        def inspect(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                self.assertEqual(cursor.fetchone()[0], "on")
                cursor.execute("SHOW transaction_isolation")
                self.assertEqual(cursor.fetchone()[0], "repeatable read")
            return inventory(*args, **kwargs)

        with patch("ingestion.epic45_scope_cards.inventory", side_effect=inspect):
            scope_cards(1)
