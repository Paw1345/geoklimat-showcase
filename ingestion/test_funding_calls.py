import io
import json
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from ingestion import funding_calls as calls
from ingestion.data_service import collect, run_data
from ingestion.funding_search import DETAIL, discover
from ingestion.funding_search_transport import BASE, request_spec
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
FRAMEWORK = {"life": "43252405", "horizon": "43108390"}


def row(identifier, deadlines, starts=("2026-04-01T00:00:00Z",), framework="43108390"):
    url = DETAIL + identifier + ".json"
    return {
        "language": "en",
        "url": url,
        "metadata": {
            "identifier": [identifier],
            "url": [url],
            "title": ["Call " + identifier],
            "language": ["en"],
            "frameworkProgramme": [framework],
            "type": ["1"],
            "status": ["31094501"],
            "deadlineDate": list(deadlines),
            "startDate": list(starts),
        },
    }


def search_client(rows, profile="horizon"):
    client = Mock()

    def search(page, requested):
        assert requested == profile
        chunk = rows[(page - 1) * 50 : page * 50]
        data = {
            "results": chunk,
            "totalResults": len(rows),
            "pageNumber": page,
            "pageSize": 50,
            "warnings": [],
        }
        return Response(200, json.dumps(data).encode(), {}, request_spec(page, profile)[0])

    client.funding_search.side_effect = search
    return client


HORIZON = [
    row("HORIZON-CL5-2026-D3-01", ["2027-01-20T17:00:00+01:00"]),
    row("HORIZON-CL6-2027-BIODIV-01", ["2027-02-01T17:00:00+01:00"], ["2026-12-01T00:00:00Z"]),
    row(
        "HORIZON-CL6-2026-FARM2FORK-02-two-stage",
        ["2026-09-16T17:00:00+02:00", "2027-02-17T17:00:00+01:00"],
    ),
    row("HORIZON-CL5-2025-D2-01", ["2025-09-02T17:00:00+02:00"]),
    row("HORIZON-CL4-2026-DIGITAL-01", ["2027-01-20T17:00:00+01:00"]),
    row("HORIZON-MSCA-2026-PF-01", ["2026-09-10T17:00:00+02:00"]),
]


def pass_client(passes):
    """Each pass is a list of row lists, one per page, with a constant total."""
    client = Mock()
    total = sum(len(page) for page in passes[0])
    responses = [
        Response(
            200,
            json.dumps(
                {
                    "results": page,
                    "totalResults": total,
                    "pageNumber": number,
                    "pageSize": 50,
                    "warnings": [],
                }
            ).encode(),
            {},
            request_spec(number, "horizon")[0],
        )
        for pages in passes
        for number, page in enumerate(pages, 1)
    ]
    client.funding_search.side_effect = responses
    return client


class FundingPagingStabilityTests(SimpleTestCase):
    rows = [row(f"HORIZON-CL5-2027-T-{i:03}", ["2027-03-01T17:00:00Z"]) for i in range(60)]

    def test_shifted_rows_are_united_until_a_pass_adds_nothing(self):
        # Live 2026-10-08: equal-score rows move between pages, so one pass repeats
        # topic 0 and hides topic 55 while the total stays constant.
        shifted = [self.rows[:50], [self.rows[0], *self.rows[50:55], *self.rows[56:]]]
        full = [self.rows[:50], self.rows[50:]]
        report = discover(pass_client([shifted, full, full]), NOW, "horizon")
        self.assertEqual((len(report["records"]), report["passes"]), (60, 3))
        self.assertEqual(report["duplicate_rows_dropped"], 1)

    def test_set_that_never_converges_is_rejected(self):
        passes = [
            # Each pass reveals one more topic in place of a repeat of topic 0.
            [self.rows[:50], [self.rows[0]] * (10 - n) + self.rows[50 : 50 + n]]
            for n in range(1, 6)
        ]
        with self.assertRaisesMessage(FetchError, "paging unstable"):
            discover(pass_client(passes), NOW, "horizon")

    def test_payload_ignores_per_run_duplicates(self):
        channel = DataChannel(slug="eu-funding-horizon", config={"profile": "horizon"})
        shifted = [self.rows[:50], [self.rows[0], *self.rows[50:55], *self.rows[56:]]]
        full = [self.rows[:50], self.rows[50:]]
        with patch("ingestion.funding_calls.profile", return_value="horizon"):
            first = calls.collect(channel, pass_client([shifted, full, full]), NOW)
            second = calls.collect(channel, pass_client([full, full]), NOW)
        self.assertEqual(first[0], second[0])
        self.assertEqual((first[2]["scan_passes"], second[2]["scan_passes"]), (3, 2))


class FundingDiscoverProfileTests(SimpleTestCase):
    def test_horizon_keeps_only_clusters_five_and_six(self):
        report = discover(search_client(HORIZON), NOW, "horizon")
        self.assertEqual((report["total"], report["outside_scope_count"]), (6, 2))
        self.assertEqual(
            [r["identifier"][:11] for r in report["records"]],
            ["HORIZON-CL5"] * 2 + ["HORIZON-CL6"] * 2,
        )
        self.assertEqual(
            report["scope"], "HorizonEurope/clusters5-6/en/status_codes31094501,31094502"
        )

    def test_horizon_rejects_life_framework_or_identity(self):
        for bad in [
            row("HORIZON-CL5-2026-X", ["2027-01-01T00:00:00Z"], framework=FRAMEWORK["life"]),
            row("LIFE-2026-SAP-ENV", ["2027-01-01T00:00:00Z"]),
        ]:
            with self.assertRaises(FetchError):
                discover(search_client([bad]), NOW, "horizon")

    def test_horizon_live_index_shapes_2026_10_08(self):
        portal = (
            "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/"
        )

        def variant(
            identifier, url=None, kind="1", reference=None, deadline="2027-01-20T17:00:00Z"
        ):
            r = row(identifier, [deadline])
            if url:
                r["url"] = url
                r["metadata"]["url"] = [url]
            r["metadata"]["type"] = [kind]
            if reference:
                r["reference"] = reference
            return r

        rows = [
            variant("HORIZON-CL5-2026-D3-02", portal + "topic-details/HORIZON-CL5-2026-D3-02"),
            row("HORIZON-CL6-2026-X-01", ["2027-02-01T17:00:00Z"]),
            row("HORIZON-CL6-2026-X-01", ["2027-02-01T17:00:00Z"]),
            variant(
                "HORIZON-CL6-2022-GOVERNANCE-01-02",
                DETAIL + "2621COMPETITIVE_CALLen",
                "8",
                "2621COMPETITIVE_CALLen",
                "2023-09-13T15:00:00Z",
            ),
            variant(
                "HORIZON-CL6-2022-GOVERNANCE-01-02",
                portal + "competitive-calls-cs/44092120",
                "8",
                "15721COMPETITIVE_CALLen",
            ),
            variant("ERC-2027-COG", portal + "topic-details/ERC-2027-COG"),
            variant("HORIZON-EIC-2021-EEN-01-01", DETAIL + "2441COMPETITIVE_CALLen", "8"),
            variant("HORIZON-EIC-2021-EEN-01-01", DETAIL + "2442COMPETITIVE_CALLen", "8"),
        ]
        report = discover(search_client(rows), NOW, "horizon")
        # Outside scope counts distinct topics (EIC twice, ERC once), stable across passes.
        self.assertEqual((report["total"], report["outside_scope_count"]), (8, 2))
        self.assertEqual(report["duplicate_rows_dropped"], 1)
        refs = [r.get("source_reference") for r in report["records"]]
        self.assertEqual(
            refs.count("2621COMPETITIVE_CALLen") + refs.count("15721COMPETITIVE_CALLen"), 2
        )
        self.assertEqual(len(report["records"]), 4)
        changed_duplicate = [rows[1], row("HORIZON-CL6-2026-X-01", ["2027-03-01T17:00:00Z"])]
        for bad in [
            changed_duplicate,
            [variant("HORIZON-CL5-2026-Y", portal + "other/HORIZON-CL5-2026-Y")],
            [variant("HORIZON-CL5-2026-Y", portal + "topic-details/HORIZON-CL5-2026-Z")],
            [variant("HORIZON-CL6-2022-G", portal + "competitive-calls-cs/1", "8")],
            [variant("HORIZON-CL6-2022-G", portal + "competitive-calls-cs/1", "8", "bad")],
            [variant("OTHER-2027-X", portal + "topic-details/OTHER-2027-X")],
        ]:
            with self.subTest(bad=bad[-1]["metadata"]["identifier"]), self.assertRaises(FetchError):
                discover(search_client(bad), NOW, "horizon")

    def test_life_keeps_strict_identity_and_url_rules(self):
        life = row("LIFE-2026-SAP-ENV", ["2027-01-01T00:00:00Z"], framework=FRAMEWORK["life"])
        portal = dict(life, url="https://ec.europa.eu/x")
        portal["metadata"] = dict(life["metadata"], url=["https://ec.europa.eu/x"])
        for bad in ([life, life], [portal]):
            with self.assertRaises(FetchError):
                discover(search_client(bad, "life"), NOW, "life")

    def test_profile_page_budgets_and_body(self):
        self.assertIn(b'"43108390"', request_spec(20, "horizon")[1])
        self.assertIn(b'"43252405"', request_spec(5)[1])
        for page, profile in [(6, "life"), (21, "horizon"), (1, "other")]:
            with self.assertRaises(FetchError):
                request_spec(page, profile)


class FundingCallsTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t.xlsx", sha256="f" * 64, sheets={})
        RegistryEntry.objects.create(
            release=release, registry_id=189, master={}, technical={}, integration_state="x"
        )

    def channel(self, key="horizon", **extra):
        return DataChannel.objects.create(
            registry=RegistryEntry.objects.get(registry_id=189),
            slug=calls.slug(key),
            name=key,
            kind="table",
            region="EU",
            url=BASE,
            config=calls.config(key),
            **extra,
        )

    def test_states_from_source_dates_never_index_status(self):
        payload, _, summary = calls.collect(self.channel(), search_client(HORIZON), NOW)
        states = {r["identifier"]: r["state"] for r in json.loads(payload)["records"]}
        self.assertEqual(
            states,
            {
                "HORIZON-CL5-2026-D3-01": "open",
                "HORIZON-CL6-2027-BIODIV-01": "forthcoming",
                "HORIZON-CL6-2026-FARM2FORK-02-two-stage": "later_deadline_only",
                "HORIZON-CL5-2025-D2-01": "deadline_passed",
            },
        )
        self.assertEqual(
            (summary["state_open"], summary["records"], summary["outside_scope"]), (1, 4, 2)
        )
        self.assertNotIn("as_of", json.loads(payload))

    def test_store_then_repeat_unchanged_and_daily_state_change_stores(self):
        self.channel(enabled=True, access_approved=True)
        client = search_client(HORIZON)
        with (
            patch("ingestion.data_service.Client", lambda: client),
            patch("ingestion.funding_calls.timezone.now", return_value=NOW),
        ):
            first = run_data(calls.slug("horizon"), force=True)[0]
            second = run_data(calls.slug("horizon"), force=True)[0]
        self.assertEqual((first["status"], second["status"]), ("stored", "unchanged"))
        later = datetime(2027, 1, 21, tzinfo=UTC)
        with (
            patch("ingestion.data_service.Client", lambda: client),
            patch("ingestion.funding_calls.timezone.now", return_value=later),
        ):
            third = run_data(calls.slug("horizon"), force=True)[0]
        self.assertEqual(third["status"], "stored")
        self.assertEqual(DataSnapshot.objects.count(), 2)

    def test_life_profile_and_refusals(self):
        life = [
            row("LIFE-2026-SAP-ENV", ["2026-09-22T17:00:00+02:00"], framework=FRAMEWORK["life"])
        ]
        _, _, summary = collect(self.channel("life"), search_client(life, "life"))
        self.assertEqual(summary["state_deadline_passed"], 1)
        channel = self.channel()
        channel.url = "https://example.org/"
        with self.assertRaises(FetchError):
            collect(channel, search_client(HORIZON))
        channel.url, channel.region = BASE, "PL"
        with self.assertRaises(FetchError):
            collect(channel, search_client(HORIZON))

    @override_settings(DATA_AUTO_MAX_BYTES=500)
    def test_payload_budget(self):
        with self.assertRaises(FetchError):
            calls.collect(self.channel(), search_client(HORIZON), NOW)

    def test_configuration_dry_run_idempotence_preserves_admin(self):
        out = io.StringIO()
        call_command("configure_funding_calls", stdout=out)
        self.assertFalse(DataChannel.objects.exists())
        call_command("configure_funding_calls", apply=True, stdout=out)
        call_command("configure_funding_calls", apply=True, stdout=out)
        self.assertEqual(
            DataChannel.objects.filter(kind="table", enabled=False, interval_minutes=1440).count(),
            3,
        )
        c = DataChannel.objects.get(slug=calls.slug("life"))
        c.config = {"mode": "funding_calls", "profile": "horizon"}
        c.save()
        with self.assertRaises(CommandError):
            call_command("configure_funding_calls", apply=True, stdout=out)
