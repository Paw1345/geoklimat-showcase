import io
import json
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from ingestion.funding_search import DETAIL, discover
from ingestion.funding_search_transport import request_spec
from ingestion.transport import Client, FetchError, Response


class FundingSearchQualityTests(SimpleTestCase):
    now = datetime(2026, 10, 1, tzinfo=UTC)

    def row(self, suffix="2023-TEST"):
        identifier = "LIFE-" + suffix
        url = DETAIL + identifier + ".json"
        return {
            "language": "en",
            "url": url,
            "metadata": {
                "identifier": [identifier],
                "url": [url],
                "title": ["Public call"],
                "language": ["en"],
                "frameworkProgramme": ["43252405"],
                "type": ["1"],
                "status": ["31094502"],
                "deadlineDate": ["2023-09-05T17:00:00+02:00", "2024-03-05T17:00:00+01:00"],
                "startDate": ["2023-04-18T00:00:00Z"],
            },
        }

    def response(self, rows, total=None, page=1, **extra):
        data = {
            "results": rows,
            "totalResults": len(rows) if total is None else total,
            "pageNumber": page,
            "pageSize": 50,
            "warnings": [],
        }
        data.update(extra)
        return Response(200, json.dumps(data).encode(), {}, request_spec(page)[0])

    def report(self, rows):
        client = Mock()
        client.funding_search.return_value = self.response(rows)
        return discover(client, self.now)

    def test_elapsed_deadlines_preserved_without_current_call_claim(self):
        row = self.row()
        report = self.report([row])
        self.assertEqual(report["quality"], "all_source_deadlines_elapsed")
        self.assertEqual(
            report["records"][0]["deadline_dates_raw"], row["metadata"]["deadlineDate"]
        )
        for flag in ["current_call_verified", "database_writes", "attachments_downloaded"]:
            self.assertIs(report[flag], False)
        self.assertNotIn("published_at", report["records"][0])

    def test_empty_and_future_dates_never_prove_current_coverage(self):
        self.assertEqual(self.report([])["total"], 0)
        row = self.row()
        row["metadata"]["deadlineDate"] = ["2027-01-01T00:00:00Z"]
        report = self.report([row])
        self.assertEqual(report["quality"], "review_required")
        self.assertFalse(report["current_call_verified"])

    def test_past_first_phase_future_second_phase_is_not_open_for_new_applicants(self):
        row = self.row()
        dates = ["2027-03-04T17:00:00+01:00", "2026-09-03T17:00:00+02:00"]
        row["metadata"]["deadlineDate"] = dates
        report = self.report([row])
        evidence = report["records"][0]["deadline_time_evidence"]
        self.assertEqual(evidence["state"], "mixed_deadlines_require_stage_review")
        self.assertEqual((evidence["elapsed_count"], evidence["future_count"]), (1, 1))
        self.assertEqual(evidence["next_source_deadline_raw"], dates[0])
        self.assertFalse(evidence["new_applicant_eligibility_verified"])
        self.assertEqual(report["mixed_deadline_records_count"], 1)
        self.assertEqual(report["records"][0]["deadline_dates_raw"], dates)

    def test_deadline_equal_now_is_elapsed_and_offsets_are_compared_as_instants(self):
        row = self.row()
        row["metadata"]["deadlineDate"] = ["2026-10-01T02:00:00+02:00"]
        report = self.report([row])
        self.assertTrue(report["records"][0]["all_source_deadlines_elapsed"])
        self.assertEqual(report["future_deadline_records_count"], 0)
        self.assertIsNone(
            report["records"][0]["deadline_time_evidence"]["next_source_deadline_raw"]
        )

    def test_future_deadlines_sorted_by_time_not_source_order_or_offset_text(self):
        row = self.row()
        row["metadata"]["deadlineDate"] = [
            "2027-01-01T01:30:00+01:00",
            "2027-01-01T00:15:00Z",
        ]
        report = self.report([row])
        evidence = report["records"][0]["deadline_time_evidence"]
        self.assertEqual(evidence["next_source_deadline_raw"], "2027-01-01T00:15:00Z")
        self.assertEqual(evidence["state"], "future_deadlines_require_opening_review")
        self.assertFalse(report["current_call_verified"])

    def test_competitive_call_url_preserved_only_for_type_eight(self):
        row = self.row()
        row["metadata"]["type"] = ["8"]
        row["url"] = DETAIL + "3261COMPETITIVE_CALLen"
        row["metadata"]["url"] = [row["url"]]
        self.assertEqual(self.report([row])["records"][0]["source_url"], row["url"])
        row["metadata"]["type"] = ["1"]
        with self.assertRaises(FetchError):
            self.report([row])

    def test_scope_identity_and_date_corruption_fail_closed(self):
        for key, value in [
            ("language", ["bg"]),
            ("frameworkProgramme", ["other"]),
            ("identifier", ["OTHER"]),
            ("url", ["https://example.com/"]),
            ("status", ["unknown"]),
            ("deadlineDate", ["2023-01-01"]),
            ("startDate", ["bad"]),
        ]:
            row = self.row()
            row["metadata"][key] = value
            with self.subTest(key=key), self.assertRaises(FetchError):
                self.report([row])
        with self.assertRaises(FetchError):
            self.report([self.row(), self.row()])

    def test_page_budget_warnings_and_incomplete_results_fail_closed(self):
        for response in [
            self.response([], 251),
            self.response([], 1),
            self.response([], warnings=["partial"]),
            self.response([], pageSize=20),
        ]:
            client = Mock()
            client.funding_search.return_value = response
            with self.assertRaises(FetchError):
                discover(client, self.now)

    def test_multiple_pages_sorted_and_count_change_or_duplicate_rejected(self):
        rows = [self.row(f"2023-TEST-{i:03}") for i in range(51)]
        client = Mock()
        client.funding_search.side_effect = [
            self.response(rows[:50], 51),
            self.response(rows[50:], 51, 2),
        ]
        report = discover(client, self.now)
        self.assertEqual((report["total"], report["pages"]), (51, 2))
        for second in [self.response(rows[:1], 51, 2), self.response(rows[50:], 52, 2)]:
            client.funding_search.side_effect = [self.response(rows[:50], 51), second]
            with self.assertRaises(FetchError):
                discover(client, self.now)

    def test_command_default_makes_no_http_or_database_writes(self):
        with patch("ingestion.management.commands.discover_funding_calls.Client") as client:
            output = io.StringIO()
            call_command("discover_funding_calls", stdout=output)
            client.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())["http"])


class HTTP(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.code = status
        self.headers = {"Content-Type": "application/json"}


@override_settings(INGEST_ALLOWED_HOSTS=["api.tech.ec.europa.eu"])
class FundingSearchTransportTests(SimpleTestCase):
    def http_client(self, policy=b"User-agent: *\nAllow: /", response=None, policy_status=200):
        client = Client()
        client.opener = Mock()
        client.opener.open.side_effect = [HTTP(policy, policy_status), response or HTTP(b"{}")]
        return client

    def perform(self, client, page=1):
        with (
            patch(
                "ingestion.transport.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("1.1.1.1", 443))],
            ),
            patch("ingestion.transport.time.sleep"),
            patch("ingestion.funding_search_transport.time.sleep"),
        ):
            return client.funding_search(page)

    def test_only_reviewed_fixed_post_follows_get_policy_and_no_credentials(self):
        client = self.http_client()
        self.assertEqual(self.perform(client).status, 200)
        first, second = [call.args[0] for call in client.opener.open.call_args_list]
        self.assertEqual(first.get_method(), "GET")
        self.assertEqual(first.full_url, "https://api.tech.ec.europa.eu/robots.txt")
        self.assertEqual(second.get_method(), "POST")
        self.assertEqual(second.full_url, request_spec(1)[0])
        self.assertEqual(second.data, request_spec(1)[1])
        self.assertNotIn("Authorization", second.headers)
        self.assertNotIn("Cookie", second.headers)
        self.assertIn(b'"43252405"', second.data)

    def test_robots_disallow_html_and_unknown_policy_block_post(self):
        for body, status in [
            (b"User-agent: *\nDisallow: /", 200),
            (b"<html>error", 200),
            (b"", 403),
        ]:
            client = self.http_client(body, policy_status=status)
            with self.assertRaises(FetchError):
                self.perform(client)
            self.assertEqual(client.opener.open.call_count, 1)

    def test_policy_404_allowed_and_redirect_never_replayed(self):
        client = self.http_client(b"", HTTP(b"", 302), 404)
        with self.assertRaisesMessage(FetchError, "redirects are not permitted"):
            self.perform(client)
        self.assertEqual(client.opener.open.call_count, 2)

    def test_invalid_page_does_not_make_any_http(self):
        for page in [0, 6, True, "1", None]:
            client = self.http_client()
            with self.assertRaises(FetchError):
                self.perform(client, page)
            client.opener.open.assert_not_called()

    def test_private_dns_and_unapproved_host_block_all_requests(self):
        client = self.http_client()
        with patch(
            "ingestion.transport.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
        ):
            with self.assertRaises(FetchError):
                client.funding_search(1)
        client.opener.open.assert_not_called()
        with override_settings(INGEST_ALLOWED_HOSTS=[]):
            with self.assertRaises(FetchError):
                self.perform(client)
        client.opener.open.assert_not_called()

    def test_response_budget_and_non_success_fail_closed(self):
        for response in [HTTP(b"x" * (5 * 1024 * 1024 + 1)), HTTP(b"{}", 401)]:
            with self.assertRaises(FetchError):
                self.perform(self.http_client(response=response))
