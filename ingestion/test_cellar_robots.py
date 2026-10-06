from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from ingestion.transport import Client, FetchError


class CellarRobotsTests(SimpleTestCase):
    def reply(self, status, location=None, body=b""):
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.code = status
        reply.headers = {"Location": location} if location else {}
        reply.read.side_effect = [body, b""] if body else [b""]
        return reply

    @patch("ingestion.transport.validate_url")
    @patch("ingestion.transport.time.sleep")
    def test_reviewed_robots_redirect_preserves_policy(self, sleep, validate):
        client = Client()
        client.opener.open = MagicMock(
            side_effect=[
                self.reply(301, "https://op.europa.eu/robots.txt"),
                self.reply(200, body=b"User-agent: *\nDisallow: /webapi/\n"),
            ]
        )
        with self.assertRaisesMessage(FetchError, "robots.txt disallows"):
            client.get("https://publications.europa.eu/webapi/rdf/sparql")
        self.assertEqual(client.opener.open.call_count, 2)
        self.assertEqual(
            [c.args[0] for c in validate.call_args_list],
            [
                "https://publications.europa.eu/robots.txt",
                "https://op.europa.eu/robots.txt",
            ],
        )

    @patch("ingestion.transport.validate_url")
    @patch("ingestion.transport.time.sleep")
    def test_other_cross_host_redirects_remain_rejected(self, sleep, validate):
        for source, target in (
            ("https://publications.europa.eu/robots.txt", "https://outside.example/robots.txt"),
            ("https://publications.europa.eu/robots.txt", "http://op.europa.eu/robots.txt"),
            ("https://publications.europa.eu/robots.txt", "https://op.europa.eu/"),
            ("https://publications.europa.eu/webapi/rdf/sparql", "https://op.europa.eu/robots.txt"),
            ("https://publications.europa.eu/robots.txt?x=1", "https://op.europa.eu/robots.txt"),
        ):
            client = Client()
            client.opener.open = MagicMock(return_value=self.reply(301, target))
            with (
                self.subTest(source=source, target=target),
                self.assertRaisesMessage(FetchError, "Cross-host redirect"),
            ):
                client._raw(source, delay=0)
            self.assertEqual(client.opener.open.call_count, 1)
