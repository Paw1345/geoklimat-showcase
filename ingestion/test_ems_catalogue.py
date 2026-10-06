import io
import json
from copy import deepcopy
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ingestion.ems_catalogue import CONFIG, MAX_BYTES, ORIGIN, PATH, SLUG, URL, parse_page
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries, run_batch
from ingestion.transport import EMS_ACTIVATIONS_REQUEST, Client, FetchError, Response


def activation(code="EMSR10", country="Poland"):
    return {
        "code": code,
        "name": "Flood mapping",
        "countries": [{"short_name": country}],
        "category": {"slug": "flood", "name": "Flood"},
        "drmPhase": "response",
        "activationTime": "2026-09-15T16:58:00",
        "lastUpdate": "2026-09-17T12:48:32.188752",
        "closed": True,
        "n_aois": 2,
        "n_products": 3,
        "centroid": "POINT (20 50)",
        "search_snippet": "PRIVATE ignored prose",
        "download": "https://example.org/raster.tif",
    }


class EmsCatalogueTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug=SLUG,
            url=URL,
            adapter="json",
            config={**CONFIG, "terms_accepted": True},
            enabled=True,
            activation_approved=True,
            interval_minutes=120,
        )
        self.data = {
            "count": 3,
            "next": None,
            "previous": None,
            "results": [activation(), activation("EMSN20", "Spain"), activation("EMSR30", "Nepal")],
        }
        self.http = Mock()
        self.respond()

    def respond(self):
        self.http.get.return_value = Response(200, json.dumps(self.data).encode(), {}, URL)

    def test_single_bounded_get_eu_names_no_dates_or_files(self):
        entries, bad, response, fingerprint = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), bad, response.status), (2, 0, 200))
        self.http.get.assert_called_once_with(URL, {})
        self.assertEqual(self.http.max_bytes, MAX_BYTES)
        self.assertEqual(len(fingerprint), 64)
        for entry in entries:
            self.assertEqual((entry.published, entry.updated, entry.summary), ("", "", ""))
            self.assertEqual(entry.metadata["record_kind"], "dataset_catalogue")
            self.assertEqual(entry.metadata["activation_time_raw"], "2026-09-15T16:58:00")
            self.assertFalse(entry.metadata["local_alert_verified"])
            self.assertFalse(entry.metadata["resources_downloaded"])
            self.assertFalse(entry.metadata["exact_geographic_mask_verified"])
            self.assertNotIn("PRIVATE", json.dumps(entry.metadata))
            self.assertNotIn("raster.tif", json.dumps(entry.metadata))
            self.assertTrue(entry.url.startswith(ORIGIN + PATH))

    def test_http_next_is_inert_and_partial_coverage_explicit(self):
        self.data["results"] = [activation(f"EMSR{index}") for index in range(20)]
        self.data["count"] = 1065
        self.data["next"] = ORIGIN.replace("https:", "http:") + PATH + "?limit=20&offset=20"
        self.respond()
        entries, _, _, _ = fetch_entries(self.source, self.http)
        self.assertEqual(len(entries), 20)
        self.http.get.assert_called_once()
        self.assertFalse(entries[0].metadata["provider_next_followed"])
        self.assertFalse(entries[0].metadata["full_archive_coverage"])

    def test_invalid_page_count_or_links_fail_closed(self):
        original = deepcopy(self.data)
        for mutation in (
            lambda d: d.update(count=True),
            lambda d: d.update(count=4),
            lambda d: d.update(count=-1),
            lambda d: d.update(previous=URL),
            lambda d: d.update(next="https://example.org/"),
            lambda d: d.update(results=[]),
            lambda d: d.pop("next"),
        ):
            with self.subTest(mutation=mutation):
                self.data = deepcopy(original)
                mutation(self.data)
                self.respond()
                with self.assertRaises(FetchError):
                    fetch_entries(self.source, self.http)

    def test_invalid_non_eu_rows_are_not_silently_dropped(self):
        original = deepcopy(self.data)
        for mutation in (
            lambda r: r.update(countries=[]),
            lambda r: r.update(n_products=True),
            lambda r: r.update(closed="false"),
            lambda r: r.update(activationTime="2026-02-30T12:00:00"),
            lambda r: r.update(lastUpdate="2026-09-17"),
            lambda r: r.update(category={"slug": "../bad", "name": "bad"}),
            lambda r: r.update(drmPhase="unknown"),
            lambda r: r.update(code="EMSR10"),
        ):
            self.data = deepcopy(original)
            mutation(self.data["results"][-1])
            self.respond()
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http)

    def test_country_alias_and_mixed_activation_do_not_claim_geometry(self):
        self.data["results"][0]["countries"] = [
            {"short_name": "Czech Republic"},
            {"short_name": "Nepal"},
        ]
        self.respond()
        entries, _, _, _ = fetch_entries(self.source, self.http)
        self.assertEqual(entries[0].metadata["matched_eu_countries"], ["Czechia"])
        self.assertEqual(entries[0].metadata["countries_raw"], ["Czech Republic", "Nepal"])

    def test_identity_json_budget_and_profile_fail_closed(self):
        for body in (
            b'{"count":3,"count":3}',
            b'{"count":NaN}',
            b"{}\xff",
            b"[]",
            b" " * (MAX_BYTES + 1),
        ):
            self.http.get.return_value = Response(200, body, {}, URL)
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http)
        self.respond()
        self.source.config = {**CONFIG, "admin": True}
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)
        self.source.config = CONFIG
        for status, target in ((206, URL), (200, ORIGIN), (302, URL)):
            self.http.get.return_value = Response(status, b"{}", {}, target)
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http)

    def test_conditional_304_and_empty_catalogue_remain_no_new_entries(self):
        self.source.etag = '"v1"'
        self.http.get.return_value = Response(304, b"", {}, URL)
        result = fetch_entries(self.source, self.http)
        self.assertEqual((result[0], result[1], result[3]), ([], 0, ""))
        self.http.get.assert_called_once_with(URL, {"If-None-Match": '"v1"'})
        self.data.update(count=0, results=[])
        self.respond()
        self.assertEqual(parse_page(self.source, self.http.get.return_value), ([], 0, 0))

    def test_import_repeat_and_changed_update_preserve_identity_and_no_publication_dates(self):
        with patch("ingestion.service.Client", return_value=self.http):
            first = run_batch(source_slug=SLUG, force=True)
            second = run_batch(source_slug=SLUG, force=True)
            self.data["results"][0]["n_products"] = 4
            self.respond()
            third = run_batch(source_slug=SLUG, force=True)
        self.assertEqual(
            (first[0]["created"], second[0]["unchanged"], third[0]["updated"]), (2, 2, 1)
        )
        self.assertEqual(Item.objects.filter(source=self.source).count(), 2)
        self.assertTrue(all(item.published_at is None for item in self.source.items.all()))
        self.assertEqual(self.source.items.values("publication_id").distinct().count(), 2)
        item = self.source.items.filter(external_id="EMSR10").first()
        self.assertContains(
            self.client.get(f"/items/{item.pk}/"), "nie potwierdza lokalnego alarmu"
        )

    def test_provider_page_order_and_non_eu_change_do_not_rewrite_selected_records(self):
        first = fetch_entries(self.source, self.http)[3]
        self.data["results"].reverse()
        self.data["results"][0]["name"] = "Changed outside EU"
        self.respond()
        self.assertEqual(fetch_entries(self.source, self.http)[3], first)

    def registry(self):
        self.source.delete()
        release = RegistryRelease.objects.create(filename="fixture", sha256="ems", sheets={})
        return RegistryEntry.objects.create(
            registry_id=196,
            release=release,
            master={"original": True},
            technical={},
            integration_state="needs_connector",
        )

    def test_configure_dry_disabled_apply_repeat_preserve_history_and_gap(self):
        registry = self.registry()
        call_command("configure_ems", stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), 0)
        call_command("configure_ems", apply=True, stdout=io.StringIO())
        source = Source.objects.get(slug=SLUG)
        self.assertFalse(source.enabled)
        self.assertFalse(source.activation_approved)
        self.assertContains(self.client.get("/registry/196/"), "trwały import i zwykła aktywacja")
        source.last_audit = {"status": "verified"}
        source.enabled = source.activation_approved = True
        source.save()
        call_command("configure_ems", apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        registry.refresh_from_db()
        self.assertTrue(source.enabled)
        self.assertEqual(source.last_audit, {"status": "verified"})
        self.assertEqual(registry.integration_state, "needs_connector")
        self.assertEqual(registry.master, {"original": True})

    def test_configure_refuses_admin_edits_or_preexisting_linkage(self):
        registry = self.registry()
        registry.sources.add(Source.objects.create(slug="admin", url="https://example.org/"))
        with self.assertRaises(CommandError):
            call_command("configure_ems", apply=True, stdout=io.StringIO())
        self.assertFalse(Source.objects.filter(slug=SLUG).exists())

    def test_probe_default_no_http_and_fetch_never_writes(self):
        output = io.StringIO()
        before = (Source.objects.count(), Item.objects.count())
        with patch("ingestion.management.commands.ems_probe.Client", return_value=self.http):
            call_command("ems_probe", stdout=output)
            self.http.get.assert_not_called()
            output = io.StringIO()
            call_command("ems_probe", fetch=True, stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual((result["provider_count"], result["matched_eu_rows"]), (3, 2))
        self.assertEqual(before, (Source.objects.count(), Item.objects.count()))

    def test_api_redirect_rejected_before_following_target(self):
        self.assertEqual(EMS_ACTIVATIONS_REQUEST, URL)
        client = Client()
        response = Mock(code=301, headers={"Location": ORIGIN + "/raster.tif"})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        client.opener.open = Mock(return_value=response)
        with patch("ingestion.transport.validate_url"), patch("ingestion.transport.time.sleep"):
            with self.assertRaisesMessage(FetchError, "EMS activation API redirect rejected"):
                client._request(URL)
        client.opener.open.assert_called_once()

    def test_real_disallow_remains_a_blocker_no_exception(self):
        client = Client()
        policy = Response(200, b"User-agent: *\nDisallow: /\n", {}, ORIGIN + "/robots.txt")
        with patch.object(client, "_request", return_value=policy) as request:
            with self.assertRaisesMessage(FetchError, "robots.txt disallows"):
                client.get(URL)
        request.assert_called_once_with(ORIGIN + "/robots.txt")

    def test_unaccepted_terms_block_get_and_all_generic_activation_paths(self):
        self.source.config = CONFIG
        self.source.last_audit = {
            "status": "verified",
            "url": URL,
            "count": 2,
            "rejected": 0,
            "http": 200,
            "robots": "allowed-or-404",
        }
        self.source.audited_at = timezone.now()
        self.source.save()
        with patch("ingestion.service.Client", return_value=self.http):
            result = run_batch(source_slug=SLUG, force=True)
        self.assertEqual(result[0]["status"], "failed")
        self.http.get.assert_not_called()
        self.assertEqual(self.source.items.count(), 0)
        self.source.enabled = self.source.activation_approved = False
        self.source.save()
        call_command("activate_registry", source=[SLUG], apply=True, stdout=io.StringIO())
        with patch("ingestion.management.commands.audit_sources.Client", return_value=self.http):
            call_command("audit_sources", source=SLUG, enable_verified=True, stdout=io.StringIO())
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.activation_approved)

    def test_approval_dry_missing_accept_flag_then_explicit_only_never_enables(self):
        self.registry()
        call_command("configure_ems", apply=True, stdout=io.StringIO())
        source = Source.objects.get(slug=SLUG)
        source.audited_at = timezone.now()
        source.last_audit = {
            "status": "verified",
            "url": URL,
            "count": 2,
            "rejected": 0,
            "http": 200,
            "robots": "allowed-or-404",
        }
        source.save()
        call_command("approve_ems_metadata", stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("approve_ems_metadata", apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        self.assertFalse(source.config["terms_accepted"])
        call_command("approve_ems_metadata", apply=True, accept_terms=True, stdout=io.StringIO())
        source.refresh_from_db()
        self.assertTrue(source.config["terms_accepted"])
        self.assertFalse(source.enabled)
        self.assertFalse(source.activation_approved)
        before = source.config
        call_command("approve_ems_metadata", apply=True, accept_terms=True, stdout=io.StringIO())
        source.refresh_from_db()
        self.assertEqual(source.config, before)
        self.assertFalse(source.enabled)

    def test_stale_or_missing_audit_cannot_record_terms(self):
        self.registry()
        call_command("configure_ems", apply=True, stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command(
                "approve_ems_metadata", apply=True, accept_terms=True, stdout=io.StringIO()
            )
        self.assertFalse(Source.objects.get(slug=SLUG).config["terms_accepted"])

    def test_terms_revoked_during_get_roll_back_all_items_without_restoring_flags(self):
        response = self.http.get.return_value

        def revoke(*args, **kwargs):
            Source.objects.filter(pk=self.source.pk).update(
                config=CONFIG,
                enabled=False,
                activation_approved=False,
            )
            return response

        self.http.get.side_effect = revoke
        with patch("ingestion.service.Client", return_value=self.http):
            result = run_batch(source_slug=SLUG, force=True)
        self.assertEqual(result[0]["status"], "failed")
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)
        self.assertFalse(self.source.config["terms_accepted"])
        self.assertEqual(self.source.items.count(), 0)
