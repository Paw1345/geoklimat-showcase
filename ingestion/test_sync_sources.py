import io
import tempfile
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.forms.models import model_to_dict
from django.test import TestCase
from django.utils import timezone

from ingestion import (
    bdpn_news,
    clima_policies,
    environment_policies,
    mkis_journal,
    opn_news,
    p0_profiles,
    regional_profiles,
    regional_rss,
    wfos_news,
    wios_bydgoszcz,
    wios_lodz,
)
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.source_catalogue import CatalogueError, load, parse

IMPORT_SCOPE = (
    "Lista rozpoznana przez parser lub jawny monitoring tekstu strony. Bez pełnego archiwum."
)
# Registry ids per group, as the old configure_* commands touch them.
GROUPS = {
    "regional": list(regional_profiles.PROFILES),
    "regional_rss": list(regional_rss.PROFILES),
    "wfos_news": list(wfos_news.PROFILES),
    "wios_bydgoszcz": [83],
    "opole_bip": [132],
    "environment_policies": [165],
    "clima_policies": [167],
    "mkis_journal": [4],
    "bdpn_news": [144],
    "opn_news": [151],
    "wios_lodz": [86],
    "bdpn_plan": [144],
    "opn_documents": [151],
    "p0": list(p0_profiles.PROFILES),
}
# Companion catalogues need the park news route first, as configure_* does.
PREREQUISITES = {"bdpn_plan": "configure_bdpn_news", "opn_documents": "configure_opn_news"}
# Untouched registry-import routes the old commands replace: (home, interval).
LEGACY_HOMES = {
    **{k: (p.get("legacy_home", p["home"]), 120) for k, p in regional_rss.PROFILES.items()},
    **{k: (p["legacy"], 120) for k, p in wfos_news.PROFILES.items()},
    83: (wios_bydgoszcz.LEGACY_HOME, 120),
    165: (environment_policies.LEGACY, 240),
    167: (clima_policies.LEGACY, 240),
    4: (mkis_journal.LEGACY, 120),
    144: (bdpn_news.LEGACY, 360),
    151: (opn_news.LEGACY, 360),
    86: (wios_lodz.HOME, 120),
}


class SyncSourcesTestBase(TestCase):
    def setUp(self):
        self.release = RegistryRelease.objects.create(
            filename="registry.xlsx", sha256="c" * 64, sheets={}
        )
        for key in GROUPS["regional"]:
            self.entry(key, "needs_endpoint")
        self.entry(132, "needs_endpoint")
        for key in GROUPS["p0"]:
            entry = self.entry(key, "needs_endpoint", country="UE" if key == 179 else "Polska")
            entry.master = {"Poll interval (min)": 60 + key}
            entry.save()
        # Untouched first routes that configure_p0 upgrades.
        for key in (17, 179):
            entry = RegistryEntry.objects.get(registry_id=key)
            entry.sources.add(
                Source.objects.create(
                    slug=f"registry-{key:03d}",
                    name="Old",
                    organization="Old",
                    url=p0_profiles.PREVIOUS_URLS[key],
                    adapter="web",
                    enabled=False,
                    activation_approved=False,
                    audited_at=timezone.now(),
                    etag="old",
                )
            )
        for key, (home, interval) in LEGACY_HOMES.items():
            entry = self.entry(key, "configured")
            source = Source.objects.create(
                slug=f"registry-{key:03d}",
                name=entry.organization,
                organization=entry.organization,
                url=home,
                adapter="web",
                config={},
                enabled=False,
                activation_approved=False,
                interval_minutes=interval,
                priority=2,  # import_registry: int(entry.priority[1]) for P2
                scope=IMPORT_SCOPE,
                last_audit={"status": "verified"},
                audited_at=timezone.now(),
                etag="old",
            )
            entry.sources.add(source)

    def entry(self, key, state, country=""):
        return RegistryEntry.objects.create(
            registry_id=key,
            country=country,
            release=self.release,
            organization=f"Instytucja {key}",
            name=f"Pozycja {key}",
            priority="P2",
            technical_status="VERIFY_ENDPOINT",
            integration_state=state,
            master={},
            technical={"Reuse / licence": f"Licencja {key}"},
        )

    def sync(self, *groups, apply=False):
        out = io.StringIO()
        call_command("sync_sources", *[f"--group={g}" for g in groups], apply=apply, stdout=out)
        return out.getvalue()

    def snapshot(self, keys):
        rows = {}
        for source in Source.objects.filter(registry_entries__registry_id__in=keys).distinct():
            row = model_to_dict(source, exclude=["id", "next_fetch_at", "topics"])
            row["registry"] = sorted(source.registry_entries.values_list("registry_id", flat=True))
            rows[source.slug] = row
        for entry in RegistryEntry.objects.filter(registry_id__in=keys):
            rows[f"entry-{entry.registry_id}"] = (entry.integration_state, entry.integration_note)
        return rows

    def outcome(self, command, keys, prerequisite=None, **kwargs):
        with transaction.atomic():
            if prerequisite:
                call_command(prerequisite, apply=True, stdout=io.StringIO())
            call_command(command, stdout=io.StringIO(), **kwargs)
            result = self.snapshot(keys)
            transaction.set_rollback(True)
        return result


class SyncSourcesEqualityTests(SyncSourcesTestBase):
    def test_each_group_gives_same_rows_as_old_command(self):
        for group, keys in GROUPS.items():
            with self.subTest(group=group):
                before = self.snapshot(keys)
                first = PREREQUISITES.get(group)
                old = self.outcome(f"configure_{group}", keys, first, apply=True)
                new = self.outcome("sync_sources", keys, first, apply=True, group=[group])
                self.assertNotEqual(old, before)
                if first:
                    self.assertNotEqual(old, self.outcome(first, keys, apply=True))
                self.assertEqual(new, old)
                self.assertEqual(self.snapshot(keys), before)

    def test_after_old_commands_sync_reports_no_differences(self):
        for group in GROUPS:
            call_command(f"configure_{group}", apply=True, stdout=io.StringIO())
        output = self.sync()
        specs = load()
        self.assertIn(f"Summary: unchanged {len(specs)}\n", output)
        self.assertNotIn("conflict", output)

    def test_after_sync_old_commands_report_unchanged(self):
        self.sync(apply=True)
        for group in GROUPS:
            if group in {"bdpn_news", "opn_news"}:
                # The old news commands require the news row to be the only one;
                # they refuse once the companion catalogue exists (also on the PC).
                continue
            out = io.StringIO()
            call_command(f"configure_{group}", apply=True, stdout=out)
            self.assertNotIn("replace", out.getvalue())
            self.assertNotIn("create", out.getvalue())


class SyncSourcesBehaviourTests(SyncSourcesTestBase):
    def test_dry_run_writes_nothing_and_apply_is_repeatable(self):
        before = self.snapshot(sum(GROUPS.values(), []))
        output = self.sync()
        self.assertIn("Dry-run", output)
        self.assertEqual(self.snapshot(sum(GROUPS.values(), [])), before)
        self.sync(apply=True)
        self.assertFalse(Source.objects.filter(enabled=True).exists())
        self.assertFalse(Source.objects.filter(activation_approved=True).exists())
        ids = dict(Source.objects.values_list("slug", "pk"))
        Source.objects.filter(slug="registry-084").update(enabled=True, activation_approved=True)
        output = self.sync(apply=True)
        self.assertNotIn(": create", output)
        self.assertNotIn(": replace", output)
        self.assertEqual(dict(Source.objects.values_list("slug", "pk")), ids)
        self.assertTrue(Source.objects.get(slug="registry-084").activation_approved)

    def test_replace_resets_audit_and_keeps_interval(self):
        self.sync("wios_bydgoszcz", apply=True)
        source = Source.objects.get(slug="registry-083")
        self.assertEqual(
            (source.adapter, source.url, source.config),
            ("web", wios_bydgoszcz.URL, wios_bydgoszcz.CONFIG),
        )
        self.assertEqual((source.last_audit, source.audited_at, source.etag), ({}, None, ""))
        self.assertEqual((source.scope, source.interval_minutes), (wios_bydgoszcz.NOTE, 120))

    def test_administrator_change_stops_whole_run_before_writes(self):
        Source.objects.filter(slug="registry-083").update(url="https://example.org/admin")
        before = self.snapshot(sum(GROUPS.values(), []))
        with self.assertRaisesMessage(CommandError, "nothing written"):
            self.sync(apply=True)
        self.assertEqual(self.snapshot(sum(GROUPS.values(), [])), before)
        # Other groups still sync when the conflicting one is left out.
        self.sync("regional", apply=True)
        self.assertTrue(Source.objects.filter(slug="registry-084").exists())

    def test_used_history_linkage_and_state_are_preserved(self):
        cases = [
            ("regional_rss", lambda: Source.objects.filter(slug="registry-082").update(failures=1)),
            ("wfos_news", lambda: Source.objects.filter(slug="registry-101").update(enabled=True)),
            (
                "wios_bydgoszcz",
                lambda: RegistryEntry.objects.filter(registry_id=83).update(
                    integration_state="linked"
                ),
            ),
            (
                "regional",
                lambda: RegistryEntry.objects.get(registry_id=84).sources.add(
                    Source.objects.get(slug="registry-083")
                ),
            ),
            (
                "opole_bip",
                lambda: RegistryEntry.objects.filter(registry_id=132).update(
                    integration_state="configured"
                ),
            ),
        ]
        for group, change in cases:
            with self.subTest(group=group), transaction.atomic():
                change()
                with self.assertRaises(CommandError):
                    self.sync(group, apply=True)
                transaction.set_rollback(True)

    def test_busy_lock_and_unknown_group(self):
        with patch("ingestion.management.commands.sync_sources.batch_lock") as lock:
            lock.return_value.__enter__.return_value = False
            with self.assertRaisesMessage(CommandError, "busy"):
                self.sync(apply=True)
        with self.assertRaisesMessage(CommandError, "Unknown group"):
            self.sync("missing")


class SourceCatalogueTests(TestCase):
    def valid(self, **changes):
        source = {
            "registry": 1,
            "adapter": "feed",
            "url": "https://example.org/feed/",
            "interval_minutes": 60,
            **changes,
        }
        return {"version": 1, "groups": {"g": {"sources": [source]}}}

    def test_catalogue_loads_only_feed_and_web_https_sources(self):
        specs = load()
        self.assertEqual({spec.group for spec in specs}, set(GROUPS))
        self.assertTrue(all(spec.adapter in {"feed", "web"} for spec in specs))
        self.assertTrue(all(spec.url.startswith("https://") for spec in specs))
        self.assertEqual(len({spec.slug for spec in specs}), len(specs))

    def test_invalid_catalogue_rejected(self):
        self.assertEqual(parse(self.valid())[0].slug, "registry-001")
        self.assertEqual(
            parse(self.valid(interval_minutes="registry"))[0].interval_minutes, "registry"
        )
        bad = [
            {"version": 2, "groups": {}},
            self.valid(adapter="stac"),
            self.valid(url="http://example.org/"),
            self.valid(interval_minutes=5),
            self.valid(interval_minutes="weekly"),
            self.valid(language="de"),
            self.valid(registry="1"),
            self.valid(config=[]),
            self.valid(unknown=True),
            self.valid(replaces=[{"enabled": False}]),
        ]
        duplicate = self.valid()
        duplicate["groups"]["h"] = duplicate["groups"]["g"]
        for data in [*bad, duplicate]:
            with self.subTest(data=data), self.assertRaises(CatalogueError):
                parse(data)

    def test_yaml_syntax_error_is_catalogue_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as handle:
            handle.write("version: 1\ngroups:\n  g:\n    note: a: b\n")
            handle.flush()
            with self.assertRaises(CatalogueError):
                load(handle.name)
            with self.assertRaisesMessage(CommandError, "Catalogue invalid"):
                call_command("sync_sources", catalogue=handle.name, stdout=io.StringIO())


class SyncSourcesP0Tests(SyncSourcesTestBase):
    def test_registry_interval_language_and_name(self):
        self.sync("p0", apply=True)
        news = Source.objects.get(slug="registry-010")
        self.assertEqual(
            (news.name, news.interval_minutes, news.language, news.scope),
            ("Instytucja 10 — Pozycja 10", 70, "pl", p0_profiles.PROFILES[10][2]),
        )
        library = Source.objects.get(slug="registry-179")
        # Replace keeps the existing row fields; only the route and scope change.
        self.assertEqual((library.language, library.name), ("pl", "Old"))
        self.assertEqual(library.config, p0_profiles.PROFILES[179][1])
        entry = RegistryEntry.objects.get(registry_id=179)
        self.assertEqual(entry.integration_state, "configured")

    def test_used_first_route_is_kept_unlike_old_command(self):
        # configure_p0 would disable and repoint a used source; sync_sources refuses.
        Source.objects.filter(slug="registry-017").update(enabled=True, activation_approved=True)
        with self.assertRaisesMessage(CommandError, "nothing written"):
            self.sync("p0", apply=True)
        self.assertTrue(Source.objects.get(slug="registry-017").enabled)

    def test_invalid_registry_interval_rolls_back(self):
        RegistryEntry.objects.filter(registry_id=10).update(master={})
        with self.assertRaisesMessage(CommandError, "poll interval"):
            self.sync("p0", apply=True)
        self.assertFalse(Source.objects.filter(slug="registry-011").exists())


class SyncSourcesCompanionTests(SyncSourcesTestBase):
    def test_park_companion_catalogues_do_not_conflict_with_news(self):
        # PC dry-run 2026-11-13: news rows conflicted while companions were not listed.
        call_command("configure_bdpn_news", apply=True, stdout=io.StringIO())
        call_command("configure_bdpn_plan", apply=True, stdout=io.StringIO())
        output = self.sync("bdpn_news")
        self.assertIn("bdpn_news/registry-144: unchanged", output)
        self.assertNotIn("conflict", output)
        output = self.sync(apply=True)
        self.assertIn("bdpn_plan/registry-144-protection-plan: unchanged", output)
        self.assertIn("opn_documents/registry-151-protection-documents: create", output)
        self.assertEqual(RegistryEntry.objects.get(registry_id=151).sources.count(), 2)
