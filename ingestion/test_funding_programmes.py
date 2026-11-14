import io
import json
from datetime import UTC, datetime

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from ingestion import funding_calls as calls
from ingestion.funding_copy_read import checked_data
from ingestion.funding_search import IN_SCOPE, PORTAL, discover
from ingestion.funding_search_transport import BASE, PROFILES, PROGRAMMES, query, request_spec
from ingestion.models import DataChannel, RegistryEntry, RegistryRelease
from ingestion.test_funding_calls import row, search_client
from ingestion.transport import FetchError

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
CEF, DIGITAL, I3 = PROGRAMMES["CEF"], PROGRAMMES["DIGITAL"], PROGRAMMES["I3"]

ROWS = [
    row("CEF-T-2026-SIMOBGEN-WORKS", ["2027-01-28T17:00:00+01:00"], framework=CEF),
    row("DIGITAL-2026-DEPLOY-04-GREENDATA", ["2026-09-02T17:00:00+02:00"], framework=DIGITAL),
    row("I3-2026-INV1", ["2027-02-01T17:00:00+01:00"], ["2026-12-01T00:00:00Z"], framework=I3),
    row(
        "DIGITAL-ECCC-2026-DEPLOY-CYBER-08",
        ["2026-03-01T17:00:00+01:00", "2027-03-01T17:00:00+01:00"],
        framework=DIGITAL,
    ),
]


class FundingProgrammesProfileTests(SimpleTestCase):
    def test_census_ids_are_pinned_and_queried_together(self):
        self.assertEqual(
            PROFILES["programmes"]["frameworks"],
            ("43251567", "43152860", "43392145", "44416173", "44773066", "43253967"),
        )
        terms = query("programmes")["bool"]["must"][2]["terms"]["frameworkProgramme"]
        self.assertEqual(sorted(terms), sorted(PROGRAMMES.values()))
        # Horizon and LIFE keep their own channels; they never enter this profile.
        self.assertNotIn("43108390", terms)
        self.assertNotIn("43252405", terms)
        request_spec(10, "programmes")
        with self.assertRaises(FetchError):
            request_spec(11, "programmes")

    def test_whole_programmes_kept_with_source_date_states(self):
        report = discover(search_client(ROWS, "programmes"), NOW, "programmes")
        self.assertEqual((report["total"], report["outside_scope_count"]), (4, 0))
        self.assertEqual(len(report["records"]), 4)
        self.assertEqual(
            report["scope"],
            "CEF/DIGITAL/EMFAF/I3/JTM/RENEWFM/whole/en/status_codes31094501,31094502",
        )
        channel = DataChannel(slug=calls.slug("programmes"), config=calls.config("programmes"))
        channel.url, channel.kind, channel.region = BASE, "table", "EU"
        payload, _, summary = calls.collect(channel, search_client(ROWS, "programmes"), NOW)
        states = {r["identifier"]: r["state"] for r in json.loads(payload)["records"]}
        self.assertEqual(
            states,
            {
                "CEF-T-2026-SIMOBGEN-WORKS": "open",
                "DIGITAL-2026-DEPLOY-04-GREENDATA": "deadline_passed",
                "I3-2026-INV1": "forthcoming",
                "DIGITAL-ECCC-2026-DEPLOY-CYBER-08": "later_deadline_only",
            },
        )
        self.assertEqual(summary["programme_counts"], {"CEF": 1, "DIGITAL": 2, "I3": 1})

    def test_prefix_must_match_programme_id(self):
        for bad in [
            row("CEF-T-2026-X", ["2027-01-01T00:00:00Z"], framework=DIGITAL),
            row("HORIZON-CL5-2026-X", ["2027-01-01T00:00:00Z"], framework=CEF),
            row("EDF-2026-X", ["2027-01-01T00:00:00Z"], framework="44181033"),
            row("LIFE-2026-X", ["2027-01-01T00:00:00Z"], framework="43252405"),
            row("cef-2026-X", ["2027-01-01T00:00:00Z"], framework=CEF),
        ]:
            with (
                self.subTest(bad=bad["metadata"]["identifier"]),
                self.assertRaises(FetchError),
            ):
                discover(search_client([bad], "programmes"), NOW, "programmes")

    def test_portal_addresses_and_identical_duplicates_tolerated(self):
        url = PORTAL + "topic-details/CEF-T-2026-SIMOBGEN-WORKS"
        portal = {**ROWS[0], "url": url, "metadata": {**ROWS[0]["metadata"], "url": [url]}}
        report = discover(search_client([portal, portal], "programmes"), NOW, "programmes")
        self.assertEqual((len(report["records"]), report["duplicate_rows_dropped"]), (1, 1))
        self.assertTrue(report["records"][0]["source_url"].startswith(PORTAL))

    def test_profile_without_programmes_is_refused(self):
        saved = PROFILES["programmes"]["frameworks"]
        PROFILES["programmes"]["frameworks"] = ()
        try:
            with self.assertRaisesMessage(FetchError, "no confirmed programme"):
                discover(search_client(ROWS, "programmes"), NOW, "programmes")
        finally:
            PROFILES["programmes"]["frameworks"] = saved

    def test_local_copy_check_rejects_unknown_prefix(self):
        self.assertTrue(IN_SCOPE["programmes"].match("JTM-2026-PSLF"))
        self.assertFalse(IN_SCOPE["programmes"].match("EDF-2026-X"))
        channel = DataChannel(slug=calls.slug("programmes"), config=calls.config("programmes"))
        channel.url, channel.kind, channel.region = BASE, "table", "EU"
        payload, _, summary = calls.collect(channel, search_client(ROWS, "programmes"), NOW)
        data = json.loads(payload)
        checked_data(data, summary, "programmes", NOW)
        data["records"][0]["identifier"] = "EDF-T-2026-SIMOBGEN-WORKS"
        with self.assertRaises(FetchError):
            checked_data(data, summary, "programmes", NOW)


class FundingProgrammesConfigureTests(TestCase):
    def test_configure_adds_disabled_programmes_channel(self):
        release = RegistryRelease.objects.create(filename="t.xlsx", sha256="f" * 64, sheets={})
        RegistryEntry.objects.create(
            release=release, registry_id=189, master={}, technical={}, integration_state="x"
        )
        call_command("configure_funding_calls", apply=True, stdout=io.StringIO())
        channel = DataChannel.objects.get(slug="eu-funding-programmes")
        self.assertEqual(
            (channel.enabled, channel.access_approved, channel.interval_minutes),
            (False, False, 1440),
        )
        self.assertEqual(channel.config, {"mode": "funding_calls", "profile": "programmes"})
