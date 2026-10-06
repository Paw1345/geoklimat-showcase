import io
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.registry_readiness import report
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.pig_services import PROFILES, config, slug
from ingestion.service import fetch_entries, ingest_source, run_batch
from ingestion.sopo_policy import ERROR, TERMS_URL, blocked_source, blocked_url
from ingestion.source_health import source_health
from ingestion.transport import Client, ProviderPolicyBlocked, validate_url


class SopoPolicyTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug=slug("sopo"),
            adapter="ogc",
            url=PROFILES["sopo"]["url"],
            config=config("sopo"),
            enabled=True,
            activation_approved=True,
            interval_minutes=360,
            last_success_at=timezone.now(),
            last_audit={
                "status": "verified",
                "url": PROFILES["sopo"]["url"],
                "count": 15,
                "rejected": 0,
            },
            audited_at=timezone.now(),
            etag="old",
            failures=2,
        )
        release = RegistryRelease.objects.create(filename="test", sha256="c" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=62,
            release=release,
            master={"original": True},
            technical={},
            integration_state="needs_connector",
        )
        self.registry.sources.add(self.source)

    def test_target_urls_and_encoded_paths_are_blocked(self):
        for url in (
            self.source.url,
            "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/geozagrozenia/sopo_obszary/MapServer/14/query?f=geojson",
            "https://CBDGMAPA.PGI.GOV.PL/arcgis/rest/services/geozagrozenia/%2573opo_obszary/MapServer",
            "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/midas/../geozagrozenia/sopo_obszary/MapServer",
            "https://mapa.osuwiska.pgi.gov.pl/gosc/",
            "https://osuwiska.pgi.gov.pl/",
            "https://www.pgi.gov.pl/osuwiska/123/aplikacja.html",
        ):
            with self.subTest(url=url):
                self.assertTrue(blocked_url(url))

    def test_other_pig_and_terms_links_are_not_held(self):
        for url in (
            PROFILES["midas"]["url"],
            PROFILES["hydro"]["url"],
            PROFILES["geosrodowisko"]["url"],
            TERMS_URL,
            "https://www.pgi.gov.pl/aktualnosci/",
            "https://www.gov.pl/web/gdos",
        ):
            self.assertFalse(blocked_url(url))

    def test_transport_refuses_before_robots_dns_and_network(self):
        client = Client()
        with patch.object(client, "_policy") as policy, patch.object(client, "_request") as request:
            with self.assertRaisesMessage(ProviderPolicyBlocked, ERROR):
                client.get(self.source.url)
        policy.assert_not_called()
        request.assert_not_called()
        with patch("ingestion.transport.socket.getaddrinfo") as dns:
            with self.assertRaisesMessage(ProviderPolicyBlocked, ERROR):
                validate_url(self.source.url)
        dns.assert_not_called()

    def test_fetch_dry_disabled_or_forged_profile_cannot_bypass(self):
        client = Mock()
        for approved in (True, False):
            self.source.enabled = self.source.activation_approved = approved
            with self.assertRaisesMessage(ProviderPolicyBlocked, ERROR):
                fetch_entries(self.source, client)
        self.source.url = "https://www.gov.pl/test"
        self.assertTrue(blocked_source(self.source))
        with self.assertRaises(ProviderPolicyBlocked):
            fetch_entries(self.source, client)
        client.get.assert_not_called()

    def test_direct_import_retains_only_failure_without_provider_backoff(self):
        client = Mock()
        run = ingest_source(self.source, client)
        self.assertEqual(
            (run.status, run.error, run.created, run.http_status), ("failed", ERROR, 0, None)
        )
        client.get.assert_not_called()
        current = Source.objects.get(pk=self.source.pk)
        self.assertEqual(current.failures, self.source.failures)
        self.assertEqual(current.next_fetch_at, self.source.next_fetch_at)
        self.assertEqual(current.last_audit, self.source.last_audit)
        self.assertFalse(Item.objects.exists())

    def test_force_dry_run_does_not_fetch_or_change_source(self):
        with patch("ingestion.service.Client") as client:
            result = run_batch(source_slug=self.source.slug, force=True, dry_run=True)
        client.return_value.get.assert_not_called()
        self.assertEqual(result[0]["error"], ERROR)
        self.source.refresh_from_db()
        self.assertTrue(self.source.enabled)
        self.assertFalse(self.source.runs.exists())

    def test_hold_dry_apply_repeat_preserves_records_profile_and_other_sources(self):
        other = Source.objects.create(slug="other", url=PROFILES["midas"]["url"], enabled=True)
        item = Item.objects.create(
            source=self.source, external_id="stored", title="Stored metadata", url=self.source.url
        )
        before = {
            field: getattr(self.source, field)
            for field in (
                "url",
                "config",
                "last_audit",
                "audited_at",
                "last_success_at",
                "etag",
                "failures",
                "next_fetch_at",
            )
        }
        call_command("hold_sopo", stdout=io.StringIO())
        self.source.refresh_from_db()
        self.assertTrue(self.source.enabled)
        for _ in range(2):
            call_command("hold_sopo", apply=True, stdout=io.StringIO())
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)
        for field, value in before.items():
            self.assertEqual(getattr(self.source, field), value)
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        self.assertFalse(self.source.runs.exists())
        other.refresh_from_db()
        self.assertTrue(other.enabled)
        self.registry.refresh_from_db()
        self.assertEqual(self.registry.master, {"original": True})
        self.assertEqual(self.registry.integration_state, "needs_connector")

    def test_fresh_historical_audit_cannot_reactivate(self):
        call_command("hold_sopo", apply=True, stdout=io.StringIO())
        output = io.StringIO()
        call_command("activate_registry", apply=True, source=[self.source.slug], stdout=output)
        self.assertIn("Eligible public channels: 0", output.getvalue())
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_audit_cannot_enable_even_with_mock_http(self):
        with patch("ingestion.service.Client.get") as get:
            call_command(
                "audit_sources", source=self.source.slug, enable_verified=True, stdout=io.StringIO()
            )
        get.assert_not_called()
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_audit["error"], ERROR)
        self.assertFalse(self.source.enabled)

    def test_health_and_readiness_show_terms_not_success(self):
        for approved in (True, False):
            self.source.activation_approved = approved
            self.assertEqual(source_health(self.source)["state"], "terms_blocked")
        row = next(row for row in report()["rows"] if row["registry_id"] == 62)
        self.assertEqual(row["sources"][0]["state"], "terms_blocked")
        self.assertTrue(row["attention"])
        self.source.url = "https://www.gov.pl/test"
        self.assertEqual(source_health(self.source)["state"], "terms_blocked")

    def test_registry_notice_readonly_and_no_remote_probes(self):
        user = get_user_model().objects.create_user(username="staff", is_staff=True)
        self.client.force_login(user)
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            response = self.client.get("/registry/62/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "automatyczne pobieranie zatrzymane")
        self.assertContains(response, TERMS_URL)
        self.assertFalse(self.source.runs.exists())

    def test_hold_does_not_call_remote_or_wait_for_ingestion_advisory_lock(self):
        with (
            patch("ingestion.transport.Client.get") as get,
            patch("ingestion.service.batch_lock", side_effect=AssertionError("Not harvesting")),
        ):
            call_command("hold_sopo", apply=True, stdout=io.StringIO())
        get.assert_not_called()
