import json
from datetime import UTC, datetime
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase

from ingestion.adapters import parse
from ingestion.eli_catalogue import decode, fetch_catalogue
from ingestion.models import Source
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


def row(year=2026, position=1):
    return {
        "ELI": f"DU/{year}/{position}",
        "publisher": "DU",
        "year": year,
        "pos": position,
        "title": "Akt",
        "promulgation": f"{year}-01-02",
        "announcementDate": f"{year}-01-01",
        "changeDate": f"{year}-01-03T12:00:00",
        "inForce": "IN_FORCE",
    }


class EliCatalogueTests(SimpleTestCase):
    def setUp(self):
        self.source = Source(adapter="eli", url="https://api.sejm.gov.pl/eli/acts/DU/{year}")
        self.client = Mock()
        self.clock = patch(
            "ingestion.eli_catalogue.timezone.now", return_value=datetime(2026, 10, 2, tzinfo=UTC)
        )
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def serve(self, url, **changes):
        query = parse_qs(urlsplit(url).query)
        year, offset = int(query["year"][0]), int(query["offset"][0])
        rows = [row(year, offset + 1)]
        data = {"items": rows, "count": 1, "totalCount": 2, "offset": offset}
        data.update(changes)
        return Response(200, json.dumps(data).encode(), {}, url)

    def test_two_years_all_pages_contract_raw_timezone_and_no_conditional_headers(self):
        self.client.get.side_effect = self.serve
        entries, rejected, response, digest = fetch_entries(self.source, self.client)
        self.assertEqual(len(entries), 4)
        self.assertEqual(rejected, 0)
        self.assertEqual(len(digest), 64)
        self.assertEqual(self.client.get.call_count, 4)
        self.assertTrue(all(len(call.args) == 1 for call in self.client.get.call_args_list))
        self.assertEqual(entries[0].published, "2026-01-02")
        self.assertEqual(entries[0].updated, "")
        self.assertEqual(entries[0].metadata["source_change_date_raw"], "2026-01-03T12:00:00")
        self.assertFalse(entries[0].metadata["current_legal_status_verified"])
        self.assertEqual(response.status, 200)

    def test_generic_parser_rejects_partial_using_total_not_page_count(self):
        data = {"items": [row()], "count": 1, "totalCount": 2, "offset": 0}
        with self.assertRaises(FetchError):
            parse(
                Source(adapter="eli"),
                Response(200, json.dumps(data).encode(), {}, "https://api.sejm.gov.pl"),
            )

    def test_changed_total_or_wrong_offset_refuses(self):
        for field, value in (("totalCount", 3), ("offset", 0)):
            with self.subTest(field=field):
                self.client.get.side_effect = lambda url, field=field, value=value: self.serve(
                    url, **({field: value} if "offset=1" in url else {})
                )
                with self.assertRaises(FetchError):
                    fetch_catalogue(self.source, self.client)

    def test_empty_page_before_total_refuses(self):
        self.client.get.side_effect = lambda url: self.serve(url, items=[], count=0)
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client)

    def test_wrong_year_journal_identity_and_duplicate_refuse(self):
        for altered in (row(2025), {**row(), "publisher": "MP"}, {**row(), "ELI": "DU/2026/../1"}):
            self.client.get.side_effect = lambda url, altered=altered: self.serve(
                url, items=[altered], totalCount=1
            )
            with self.assertRaises(FetchError):
                fetch_catalogue(self.source, self.client)
        self.client.get.side_effect = lambda url: self.serve(url, items=[row(), row()], count=2)
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client)

    def test_out_of_order_or_repeated_position_refuses(self):
        self.client.get.side_effect = lambda url: self.serve(url, items=[row()])
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client)
        self.client.get.side_effect = lambda url: self.serve(
            url, items=[row(position=2), row()], count=2
        )
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client)

    def test_duplicate_json_invalid_envelope_and_nonfinite_refuse(self):
        for body in (
            b'{"items":[],"count":0,"count":0}',
            b"[]",
            b'{"items":[],"count":true}',
            b'{"items":[],"x":NaN}',
        ):
            with self.subTest(body=body), self.assertRaises(FetchError):
                decode(body)

    def test_invalid_dates_title_and_types_refuse_entire_batch(self):
        for field, value in (
            ("promulgation", "2026-02-30"),
            ("changeDate", "bad"),
            ("title", ""),
            ("pos", True),
        ):
            self.client.get.side_effect = lambda url, field=field, value=value: self.serve(
                url, items=[{**row(), field: value}], totalCount=1
            )
            with self.subTest(field=field), self.assertRaises(FetchError):
                fetch_catalogue(self.source, self.client)

    def test_redirect_status_and_byte_page_budgets_refuse(self):
        for status, target in ((304, None), (200, "https://example.com")):

            def response(url, status=status, target=target):
                result = self.serve(url)
                result.status, result.url = status, target or url
                return result

            self.client.get.side_effect = response
            with self.assertRaises(FetchError):
                fetch_catalogue(self.source, self.client)
        self.client.get.side_effect = self.serve
        with patch("ingestion.eli_catalogue.MAX_BYTES", 1), self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client)
        with patch("ingestion.eli_catalogue.MAX_PAGES", 1), self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client)

    def test_empty_year_valid_and_unknown_profile_no_http(self):
        self.client.get.side_effect = lambda url: self.serve(url, items=[], count=0, totalCount=0)
        self.assertEqual(fetch_catalogue(self.source, self.client)[0], [])
        self.client.reset_mock()
        self.source.url = "https://example.com/eli/acts/DU/{year}"
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client)
        self.client.get.assert_not_called()
