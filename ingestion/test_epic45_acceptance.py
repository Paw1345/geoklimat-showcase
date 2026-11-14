import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.epic45_acceptance import ACCEPTED_WITH_EXCEPTIONS, DECISIONS, state_plan
from ingestion.epic45_matrix import OWNERS, inventory, markdown
from ingestion.models import RegistryEntry, RegistryRelease
from ingestion.transport import FetchError

GROUP_A = {15, 36, 50, 63, 64, 65, 200, 202}
IMGW = {33, 34}
SPATIAL = {39, 53}
DECIDED_AT = {
    **dict.fromkeys(GROUP_A, "2026-10-14T20:25:59Z"),
    **dict.fromkeys(IMGW, "2026-10-15T12:12:58Z"),
    **dict.fromkeys(SPATIAL, "2026-10-21T20:48:56Z"),
}


class AcceptanceLedgerTests(TestCase):
    def setUp(self):
        self.release = RegistryRelease.objects.create(
            filename="public.xlsx", sha256="a" * 64, sheets={}
        )
        for registry_id in sorted(GROUP_A | IMGW | SPATIAL | {1}):
            RegistryEntry.objects.create(
                registry_id=registry_id,
                release=self.release,
                organization="Org",
                name=f"Entry {registry_id}",
                technical_status="READY_STRUCTURED",
                integration_state=(
                    "data_configured"
                    if registry_id in IMGW
                    else "configured"
                    if registry_id in {1, 53}
                    else "needs_connector"
                ),
                integration_note="old note" if registry_id == 15 else "",
                master={"Scope": "Scope | raw"},
                technical={},
            )

    def run_command(self, *args):
        out = StringIO()
        call_command("epic45_integration_state", *args, stdout=out)
        return json.loads(out.getvalue())

    def test_ledger_holds_only_recorded_user_decisions(self):
        self.assertEqual(set(DECISIONS), GROUP_A | IMGW | SPATIAL)
        owned = {value for ids in OWNERS.values() for value in ids}
        self.assertLessEqual(GROUP_A, owned)
        for registry_id, item in DECISIONS.items():
            self.assertEqual(item.status, ACCEPTED_WITH_EXCEPTIONS)
            self.assertEqual(item.decided_at, DECIDED_AT[registry_id])
            self.assertTrue(item.exceptions)
        # 53 was configured (WFS catalogue) before acceptance, 39 still needed a connector.
        self.assertEqual(DECISIONS[53].from_state, "configured")
        self.assertEqual(DECISIONS[39].from_state, "needs_connector")
        self.assertIn("skipGeometry", DECISIONS[39].exceptions)
        self.assertIn("GetCapabilities", DECISIONS[53].exceptions)

    def test_matrix_reports_decisions_and_pending_state(self):
        report = inventory(13)
        rows = {row["registry_id"]: row for row in report["entries"]}
        self.assertEqual(report["acceptance_reviewed_count"], 12)
        self.assertEqual(report["acceptance"], {ACCEPTED_WITH_EXCEPTIONS: 12, "not_reviewed": 1})
        self.assertFalse(report["scope_approved"])
        self.assertTrue(rows[15]["integration_state_pending"])
        self.assertTrue(rows[53]["integration_state_pending"])
        self.assertEqual(rows[33]["acceptance_status"], ACCEPTED_WITH_EXCEPTIONS)
        self.assertFalse(rows[33]["integration_state_pending"])
        self.assertEqual(rows[1]["acceptance_status"], "not_reviewed")
        text = markdown(report)
        self.assertIn("Odebrane, ale stan rejestru jeszcze niezapisany: 15, 36, 39, 50", text)
        self.assertIn("Scope \\| raw", text)
        self.assertEqual(text.count("\n| "), 2 + 13)

    def test_dry_run_is_default_and_writes_nothing(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            result = self.run_command()
        self.assertFalse(result["apply"])
        self.assertEqual(result["written"], 0)
        self.assertEqual(len(result["pending"]), 10)
        self.assertEqual([row["registry_id"] for row in result["already"]], [33, 34])
        self.assertEqual(
            RegistryEntry.objects.filter(integration_state="needs_connector").count(), 9
        )
        self.assertEqual(RegistryEntry.objects.get(registry_id=53).integration_state, "configured")

    def test_apply_writes_state_keeps_old_note_and_repeats_as_noop(self):
        result = self.run_command("--apply")
        self.assertEqual(result["written"], 10)
        entry = RegistryEntry.objects.get(registry_id=15)
        self.assertEqual(entry.integration_state, "data_configured")
        self.assertIn("2026-10-14T20:25:59Z", entry.integration_note)
        self.assertTrue(entry.integration_note.endswith("Poprzednia notatka: old note"))
        self.assertEqual(RegistryEntry.objects.get(registry_id=33).integration_note, "")
        wfs = RegistryEntry.objects.get(registry_id=53)
        self.assertEqual(wfs.integration_state, "data_configured")
        self.assertIn("2026-10-21T20:48:56Z", wfs.integration_note)
        again = self.run_command("--apply")
        self.assertEqual(again["written"], 0)
        self.assertEqual(len(again["already"]), 12)
        self.assertFalse(any(row["integration_state_pending"] for row in inventory(13)["entries"]))

    def test_unexpected_state_refuses_whole_apply(self):
        RegistryEntry.objects.filter(registry_id=200).update(integration_state="configured")
        with self.assertRaisesMessage(CommandError, "nothing written"):
            self.run_command("--apply")
        self.assertEqual(
            RegistryEntry.objects.filter(integration_state="needs_connector").count(), 8
        )
        self.assertEqual(RegistryEntry.objects.get(registry_id=53).integration_state, "configured")

    def test_selection_is_limited_to_recorded_decisions(self):
        result = self.run_command("--apply", "--registry-id", "36")
        self.assertEqual([row["registry_id"] for row in result["pending"]], [36])
        self.assertEqual(
            RegistryEntry.objects.get(registry_id=15).integration_state, "needs_connector"
        )
        with self.assertRaisesMessage(FetchError, "No recorded K6 decision"):
            state_plan([1])
