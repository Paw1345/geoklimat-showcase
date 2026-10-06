import io
import json
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from ingestion.adapters import parse
from ingestion.gios_stations import CONFIG, LEGACY_CONFIG, fetch_stations
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.transport import (
    GIOS_BROKEN_ROBOTS_TARGET,
    GIOS_POLICY_EXCEPTION,
    GIOS_ROBOTS_URL,
    GIOS_STATIONS_REQUEST,
    GIOS_STATIONS_URL,
    Client,
    FetchError,
    Response,
)


def payload():
    return {
        "Lista stacji pomiarowych": [
            {
                "Identyfikator stacji": 11,
                "Kod stacji": "DsTest",
                "Nazwa stacji": "Stacja testowa",
                "WGS84 φ N": "50.9",
                "WGS84 λ E": "15.3",
                "Identyfikator miasta": 142,
                "Nazwa miasta": "Miasto",
                "Gmina": "Gmina",
                "Powiat": "Powiat",
                "Województwo": "DOLNOŚLĄSKIE",
                "Ulica": None,
            }
        ],
        "links": {
            "self": GIOS_STATIONS_REQUEST,
            "first": GIOS_STATIONS_REQUEST,
            "last": GIOS_STATIONS_REQUEST,
            "next": GIOS_STATIONS_REQUEST.replace("page=0", "page=1"),
        },
        "totalPages": 1,
        "meta": {
            "dc:title": "Lista stacji pomiarowych",
            "schema:provider": {"name": "Główny Inspektorat Ochrony Środowiska"},
            "schema:dateModified": "placeholder-not-a-date",
            "schema:datePublished": "10.04.2017",
        },
    }


def response(data=None):
    return Response(
        200,
        json.dumps(data or payload(), ensure_ascii=False).encode(),
        {},
        GIOS_STATIONS_REQUEST,
        GIOS_POLICY_EXCEPTION,
    )


def broken_robots(**changes):
    return FetchError(
        "Cross-host redirect requires catalog review",
        changes.get("status", 302),
        url=changes.get("url", GIOS_ROBOTS_URL),
        redirect_url=changes.get("target", GIOS_BROKEN_ROBOTS_TARGET),
    )


class StationTransportTests(SimpleTestCase):
    def test_exception_does_not_allow_private_dns_partial_or_extra_headers(self):
        client = Client()
        with (
            patch.object(client, "_request", side_effect=broken_robots()),
            patch(
                "ingestion.transport.socket.getaddrinfo",
                return_value=[(None, None, None, None, ("127.0.0.1", 443))],
            ),
            patch.object(client.opener, "open") as opener,
            self.assertRaises(FetchError),
        ):
            client.get(GIOS_STATIONS_REQUEST, gios_station_exception=True)
        opener.assert_not_called()
        for kwargs in [{"allow_partial": True}, {"headers": {"Authorization": "fixture-only"}}]:
            with self.assertRaises(FetchError):
                client.get(GIOS_STATIONS_REQUEST, gios_station_exception=True, **kwargs)

    def test_opt_in_exact_redirect_only_no_origin_wide_cache(self):
        client = Client()
        with (
            patch.object(client, "_request", side_effect=broken_robots()) as policy,
            patch.object(client, "_raw", return_value=response()) as request,
        ):
            self.assertEqual(
                client.get(GIOS_STATIONS_REQUEST, gios_station_exception=True).access_policy,
                GIOS_POLICY_EXCEPTION,
            )
            request.assert_called_once_with(GIOS_STATIONS_REQUEST, delay=31)
            self.assertNotIn("https://api.gios.gov.pl", client.robots)
            with self.assertRaises(FetchError):
                client.get(GIOS_STATIONS_REQUEST)
            with self.assertRaises(FetchError):
                client.get(GIOS_STATIONS_URL + "/other", gios_station_exception=True)
            self.assertEqual(policy.call_count, 2)
            self.assertEqual(request.call_count, 1)

    def test_unknown_redirect_errors_never_get_exception(self):
        for changes in [
            {"status": 301},
            {"target": "https://www.gios.gov.pl/"},
            {"target": "http://evil.example/"},
            {"url": GIOS_STATIONS_URL},
        ]:
            client = Client()
            with (
                patch.object(client, "_request", side_effect=broken_robots(**changes)),
                patch.object(client, "_raw") as content,
                self.assertRaises(FetchError),
            ):
                client.get(GIOS_STATIONS_REQUEST, gios_station_exception=True)
            content.assert_not_called()
        for status, body in [(200, b"<html>homepage"), (403, b""), (202, b"")]:
            client = Client()
            with (
                patch.object(
                    client, "_request", return_value=Response(status, body, {}, GIOS_ROBOTS_URL)
                ),
                patch.object(client, "_raw") as content,
                self.assertRaises(FetchError),
            ):
                client.get(GIOS_STATIONS_REQUEST, gios_station_exception=True)
            content.assert_not_called()

    def test_real_disallow_still_blocks_and_real_allow_not_exception(self):
        for allowed in [False, True]:
            client = Client()
            rules = b"User-agent: *\n" + (b"Allow: /" if allowed else b"Disallow: /")
            with (
                patch.object(
                    client, "_request", return_value=Response(200, rules, {}, GIOS_ROBOTS_URL)
                ),
                patch.object(client, "_raw", return_value=response()) as content,
            ):
                if not allowed:
                    with self.assertRaises(FetchError):
                        client.get(GIOS_STATIONS_REQUEST, gios_station_exception=True)
                    content.assert_not_called()
                else:
                    content.return_value.access_policy = "allowed-or-404"
                    self.assertEqual(
                        client.get(
                            GIOS_STATIONS_REQUEST, gios_station_exception=True
                        ).access_policy,
                        "allowed-or-404",
                    )

    def test_rate_failure_deferred_no_retry_and_redirect_not_followed(self):
        client = Client()
        with (
            patch.object(client, "_request", side_effect=broken_robots()),
            patch.object(
                client, "_raw", return_value=Response(429, b"", {}, GIOS_STATIONS_REQUEST)
            ) as raw,
            self.assertRaises(FetchError) as caught,
        ):
            client.get(GIOS_STATIONS_REQUEST, gios_station_exception=True)
        self.assertEqual(raw.call_count, 1)
        self.assertGreaterEqual(caught.exception.retry_after, 3600)
        redirect = Mock()
        redirect.__enter__ = Mock(return_value=redirect)
        redirect.__exit__ = Mock(return_value=False)
        redirect.code, redirect.headers = 302, {"Location": "/secret-next"}
        client = Client()
        with (
            patch("ingestion.transport.validate_url"),
            patch("ingestion.transport.time.sleep"),
            patch.object(client.opener, "open", return_value=redirect) as opener,
            self.assertRaises(FetchError),
        ):
            client._raw(GIOS_STATIONS_REQUEST)
        self.assertEqual(opener.call_count, 1)


class StationParserTests(SimpleTestCase):
    def source(self):
        return Source(adapter="json", url=GIOS_STATIONS_URL, config=CONFIG, interval_minutes=60)

    def test_metadata_no_measurements_or_invented_dates_one_request(self):
        client = Mock()
        client.get.return_value = response()
        entries, bad, result, digest = fetch_stations(self.source(), client)
        self.assertEqual((len(entries), bad), (1, 0))
        client.get.assert_called_once_with(GIOS_STATIONS_REQUEST, gios_station_exception=True)
        entry = entries[0]
        self.assertEqual((entry.published, entry.updated), ("", ""))
        self.assertEqual(entry.metadata["station_id"], 11)
        self.assertFalse(entry.metadata["measurement_values_collected"])
        self.assertEqual(entry.metadata["access_policy"], GIOS_POLICY_EXCEPTION)
        self.assertEqual(result.access_policy, GIOS_POLICY_EXCEPTION)
        self.assertEqual(len(digest), 64)

    def test_schema_identity_dupes_partial_catalogue_and_coordinates_blocked(self):
        for change in [
            {"totalPages": 2},
            {"totalPages": True},
            {"Lista stacji pomiarowych": []},
            {"Lista stacji pomiarowych": payload()["Lista stacji pomiarowych"] * 2},
            {"Lista stacji pomiarowych": payload()["Lista stacji pomiarowych"] * 501},
        ]:
            with self.assertRaises(FetchError):
                parse(self.source(), response({**payload(), **change}))
        for key, value in [
            ("Identyfikator stacji", True),
            ("WGS84 φ N", "NaN"),
            ("WGS84 λ E", "99"),
            ("Kod stacji", ""),
            ("Nazwa stacji", None),
            ("Ulica", []),
            ("Identyfikator miasta", False),
        ]:
            data = payload()
            data["Lista stacji pomiarowych"][0][key] = value
            with self.subTest(key=key), self.assertRaises(FetchError):
                parse(self.source(), response(data))

    def test_endpoint_provider_schedule_and_metadata_change_hash(self):
        original = parse(self.source(), response())[0][0]
        data = payload()
        data["Lista stacji pomiarowych"][0]["Nazwa stacji"] = "New name"
        changed = parse(self.source(), response(data))[0][0]
        self.assertEqual(original.key, changed.key)
        self.assertNotEqual(original.digest(), changed.digest())
        data["meta"]["schema:provider"]["name"] = "Other provider"
        with self.assertRaises(FetchError):
            parse(self.source(), response(data))
        source = self.source()
        source.interval_minutes = 30
        with self.assertRaises(FetchError):
            fetch_stations(source, Mock())
        data = response()
        data.url = GIOS_STATIONS_URL
        with self.assertRaises(FetchError):
            parse(self.source(), data)


class StationConfigurationTests(TestCase):
    def test_station_ui_discloses_exception_and_escapes_source_fields(self):
        from ingestion.models import Item

        source = Source.objects.create(
            slug="station-ui",
            name="GIOŚ",
            adapter="json",
            url=GIOS_STATIONS_URL,
            config=CONFIG,
            interval_minutes=60,
        )
        entry = parse(source, response())[0][0]
        entry.metadata["station_code"] = "<script>alert(1)</script>"
        item = Item.objects.create(
            source=source,
            external_id=entry.key,
            title=entry.title,
            url=entry.url,
            metadata=entry.metadata,
            content_hash=entry.digest(),
        )
        page = self.client.get(f"/items/{item.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "zatwierdzonego wyjątku")
        self.assertContains(page, "nie bieżące wyniki pomiarów")
        self.assertContains(page, "&lt;script&gt;")
        self.assertNotContains(page, "<script>alert(1)</script>")

    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=7,
            release=release,
            organization="GIOŚ",
            name="Stations",
            priority="P0",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-007",
            name="Stations",
            adapter="json",
            url=GIOS_STATIONS_URL,
            config=LEGACY_CONFIG,
            enabled=False,
            activation_approved=False,
            interval_minutes=30,
            scope="Lista stacji v1; nie pomiary ani alerty zdrowotne.",
            reuse_notes="Preserve original",
            last_audit={"status": "unverified"},
        )
        self.entry.sources.add(self.source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_gios_stations.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_gios_stations", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserves_identity_and_audit(self):
        self.command()
        self.source.refresh_from_db()
        self.assertEqual(self.source.config, LEGACY_CONFIG)
        self.command(True)
        self.source.refresh_from_db()
        self.assertEqual(self.source.config, CONFIG)
        self.assertEqual(self.source.interval_minutes, 60)
        self.assertTrue(self.source.reuse_notes.startswith("Preserve original"))
        self.source.last_audit = {"status": "verified", "robots": GIOS_POLICY_EXCEPTION}
        self.source.activation_approved = self.source.enabled = True
        self.source.save()
        note = self.source.reuse_notes
        self.command(True)
        self.source.refresh_from_db()
        self.assertEqual(self.source.reuse_notes, note)
        self.assertTrue(self.source.enabled)
        self.assertEqual(self.source.last_audit["robots"], GIOS_POLICY_EXCEPTION)
        self.assertEqual(Source.objects.count(), 1)

    def test_busy_admin_history_and_linkage_preserved(self):
        with self.assertRaises(CommandError):
            self.command(True, False)
        self.source.config = {"admin": True}
        self.source.save()
        with self.assertRaises(CommandError):
            self.command(True)
        self.source.config = LEGACY_CONFIG
        self.source.save()
        self.entry.sources.add(
            Source.objects.create(slug="admin", name="Other", url=GIOS_STATIONS_URL)
        )
        with self.assertRaises(CommandError):
            self.command(True)

    def test_audit_labels_exception_honestly(self):
        self.command(True)
        with (
            patch("ingestion.management.commands.audit_sources.batch_lock") as lock,
            patch(
                "ingestion.management.commands.audit_sources.fetch_entries",
                return_value=([Mock()], 0, response(), "hash"),
            ),
        ):
            lock.return_value.__enter__.return_value = True
            call_command("audit_sources", source="registry-007", stdout=io.StringIO())
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit["robots"], GIOS_POLICY_EXCEPTION)
