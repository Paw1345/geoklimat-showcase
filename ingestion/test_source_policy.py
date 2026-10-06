from datetime import timedelta
from unittest.mock import Mock, patch

from django.db import OperationalError, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from ingestion.adapters import Entry
from ingestion.models import IngestionRun, Item, Source
from ingestion.service import ingest_source, run_batch
from ingestion.source_policy import POLICY_FIELDS
from ingestion.transport import FetchError, Response

URL = "https://www.gov.pl/web/gios/feed"


def source_fixture():
    return Source.objects.create(
        slug="policy-test",
        name="GIOS",
        adapter="feed",
        url=URL,
        enabled=True,
        activation_approved=True,
        config={"nested": {"value": 1}},
    )


def fetched(status=200):
    return ([Entry("one", "GIS update", URL)], 0, Response(status, b"", {}, URL), "a" * 64)


class SourcePolicyTests(TestCase):
    def setUp(self):
        self.source = source_fixture()

    def test_direct_disabled_or_unapproved_import_never_fetches(self):
        for flag in ("enabled", "activation_approved"):
            with self.subTest(flag=flag):
                Source.objects.filter(pk=self.source.pk).update(**{flag: False})
                with patch("ingestion.service.fetch_entries") as fetch:
                    run = ingest_source(self.source, None)
                fetch.assert_not_called()
                self.assertEqual((run.status, run.created, run.http_status), ("failed", 0, None))
                self.assertIn("approval or import profile changed", run.error)
                current = Source.objects.get(pk=self.source.pk)
                self.assertEqual(current.failures, 0)
                self.assertEqual(current.next_fetch_at, self.source.next_fetch_at)
                Source.objects.filter(pk=self.source.pk).update(**{flag: True})

    def test_stale_in_memory_profile_never_fetches(self):
        Source.objects.filter(pk=self.source.pk).update(url="https://www.gov.pl/new")
        with patch("ingestion.service.fetch_entries") as fetch:
            run = ingest_source(self.source, None)
        fetch.assert_not_called()
        self.assertEqual(run.status, "failed")

    def test_each_policy_field_changed_during_fetch_discards_batch(self):
        original = {field: getattr(self.source, field) for field in POLICY_FIELDS}
        for field in POLICY_FIELDS:
            with self.subTest(field=field):
                value = {"nested": {"value": 2}} if field == "config" else "changed"
                if field == "language":
                    value = "en"

                def change(*args, field=field, value=value, **kwargs):
                    Source.objects.filter(pk=self.source.pk).update(**{field: value})
                    return fetched()

                with patch("ingestion.service.fetch_entries", side_effect=change):
                    run = ingest_source(self.source, None)
                self.assertEqual((run.status, run.fetched, run.created), ("failed", 1, 0))
                self.assertEqual(run.http_status, 200)
                self.assertEqual(Item.objects.count(), 0)
                current = Source.objects.get(pk=self.source.pk)
                self.assertEqual(getattr(current, field), value)
                self.assertEqual(current.failures, 0)
                self.assertIsNone(current.last_success_at)
                self.assertEqual(current.next_fetch_at, self.source.next_fetch_at)
                Source.objects.filter(pk=self.source.pk).update(**original)

    def test_revocation_preserves_existing_items_versions_and_admin_schedule(self):
        with patch("ingestion.service.fetch_entries", return_value=fetched()):
            ingest_source(self.source, None)
        item = Item.objects.get()
        initial = (item.content_hash, item.last_seen_at, item.versions.count())
        schedule = timezone.now() + timedelta(days=3)

        def revoke(*args, **kwargs):
            Source.objects.filter(pk=self.source.pk).update(
                activation_approved=False, failures=7, next_fetch_at=schedule, etag="admin"
            )
            return (
                [Entry("one", "Changed headline", URL), Entry("two", "New", URL)],
                0,
                Response(200, b"", {"etag": "stale"}, URL),
                "b" * 64,
            )

        with patch("ingestion.service.fetch_entries", side_effect=revoke):
            run = ingest_source(self.source, None)
        self.assertEqual((run.status, run.created, run.updated, run.unchanged), ("failed", 0, 0, 0))
        item.refresh_from_db()
        self.assertEqual((item.content_hash, item.last_seen_at, item.versions.count()), initial)
        self.assertEqual(Item.objects.count(), 1)
        self.source.refresh_from_db()
        self.assertEqual(
            (self.source.failures, self.source.next_fetch_at, self.source.etag),
            (7, schedule, "admin"),
        )
        self.assertFalse(self.source.activation_approved)

    def test_304_after_revocation_is_not_reported_as_success(self):
        def revoke(*args, **kwargs):
            Source.objects.filter(pk=self.source.pk).update(enabled=False)
            return ([], 0, Response(304, b"", {}, URL), "")

        with patch("ingestion.service.fetch_entries", side_effect=revoke):
            run = ingest_source(self.source, None)
        self.assertEqual((run.status, run.http_status), ("failed", 304))
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_success_at)
        self.assertFalse(self.source.enabled)

    def test_network_failure_after_profile_change_does_not_backoff_new_profile(self):
        schedule = timezone.now() + timedelta(days=2)

        def change_and_fail(*args, **kwargs):
            Source.objects.filter(pk=self.source.pk).update(
                config={"new": True}, next_fetch_at=schedule, failures=4
            )
            raise FetchError("busy", 429, 900)

        with patch("ingestion.service.fetch_entries", side_effect=change_and_fail):
            run = ingest_source(self.source, None)
        self.assertEqual((run.status, run.http_status), ("failed", 429))
        self.assertIn("profile changed", run.error)
        self.source.refresh_from_db()
        self.assertEqual((self.source.failures, self.source.next_fetch_at), (4, schedule))
        self.assertEqual(self.source.config, {"new": True})

    def test_current_interval_and_failure_count_are_used(self):
        now = timezone.now()

        def reschedule(*args, **kwargs):
            Source.objects.filter(pk=self.source.pk).update(interval_minutes=600, failures=5)
            return fetched()

        with patch("ingestion.service.fetch_entries", side_effect=reschedule):
            run = ingest_source(self.source, None)
        self.assertEqual(run.status, "success")
        self.source.refresh_from_db()
        self.assertEqual(self.source.failures, 0)
        self.assertGreater(self.source.next_fetch_at, now + timedelta(minutes=599))

    def test_network_backoff_uses_current_failure_count(self):
        now = timezone.now()

        def fail(*args, **kwargs):
            Source.objects.filter(pk=self.source.pk).update(failures=5)
            raise FetchError("busy", 503)

        with patch("ingestion.service.fetch_entries", side_effect=fail):
            run = ingest_source(self.source, None)
        self.assertEqual(run.status, "failed")
        self.source.refresh_from_db()
        self.assertEqual(self.source.failures, 6)
        self.assertGreater(self.source.next_fetch_at, now + timedelta(seconds=3830))

    def test_nested_config_mutation_does_not_change_captured_policy(self):
        def mutate(source, *args, **kwargs):
            source.config["nested"]["value"] = 2
            source.save(update_fields=["config"])
            return fetched()

        with patch("ingestion.service.fetch_entries", side_effect=mutate):
            run = ingest_source(self.source, None)
        self.assertEqual(run.status, "failed")
        self.assertEqual(Item.objects.count(), 0)

    def test_unsaved_parser_policy_mutation_also_discards_batch(self):
        def mutate(source, *args, **kwargs):
            source.config["nested"]["value"] = 2
            return fetched()

        with patch("ingestion.service.fetch_entries", side_effect=mutate):
            run = ingest_source(self.source, None)
        self.assertEqual((run.status, run.created), ("failed", 0))
        self.assertEqual(Source.objects.get(pk=self.source.pk).config, {"nested": {"value": 1}})

    def test_success_uses_fresh_validators_even_with_reused_caller_object(self):
        Source.objects.filter(pk=self.source.pk).update(etag="latest", last_modified="fresh")
        validators = []

        def fetch_fresh(source, *args, **kwargs):
            validators.append((source.etag, source.last_modified))
            return fetched()

        with patch("ingestion.service.fetch_entries", side_effect=fetch_fresh) as fetch:
            run = ingest_source(self.source, None)
        self.assertEqual(run.status, "success")
        current = fetch.call_args.args[0]
        self.assertEqual(validators, [("latest", "fresh")])
        self.assertEqual(self.source.etag, "")
        self.assertIsNot(current, self.source)

    def test_commit_failure_rolls_back_item_and_history_before_backoff(self):
        with (
            patch("ingestion.service.fetch_entries", return_value=fetched()),
            patch(
                "ingestion.service.classify_and_save", side_effect=ValueError("sensitive payload")
            ),
        ):
            run = ingest_source(self.source, None)
        self.assertEqual((run.status, run.created, run.updated), ("failed", 0, 0))
        self.assertEqual(run.error, "ValueError")
        self.assertEqual(Item.objects.count(), 0)
        self.source.refresh_from_db()
        self.assertEqual(self.source.failures, 1)

    def test_failure_isolated_and_force_cannot_bypass_approval(self):
        other = Source.objects.create(
            slug="other",
            name="Other",
            adapter="feed",
            url=URL,
            enabled=True,
            activation_approved=True,
        )

        def revoke_one(source, *args, **kwargs):
            if source.pk == self.source.pk:
                Source.objects.filter(pk=source.pk).update(enabled=False)
            return fetched()

        with patch("ingestion.service.fetch_entries", side_effect=revoke_one):
            result = run_batch(force=True)
        self.assertEqual({row["status"] for row in result}, {"success", "failed"})
        self.assertEqual(set(Item.objects.values_list("source_id", flat=True)), {other.pk})
        with patch("ingestion.service.fetch_entries") as fetch:
            self.assertEqual(run_batch(source_slug=self.source.slug, force=True), [])
        fetch.assert_not_called()

    def test_diagnostic_dry_run_stays_read_only_and_can_check_disabled_source(self):
        Source.objects.filter(pk=self.source.pk).update(enabled=False, activation_approved=False)
        before = Source.objects.values().get(pk=self.source.pk)
        with patch("ingestion.service.fetch_entries", return_value=fetched()) as fetch:
            result = run_batch(source_slug=self.source.slug, dry_run=True)
        fetch.assert_called_once()
        self.assertEqual(result[0]["status"], "dry_run")
        self.assertEqual(Source.objects.values().get(pk=self.source.pk), before)
        self.assertEqual((Item.objects.count(), IngestionRun.objects.count()), (0, 0))


class SourcePolicyLockTests(TransactionTestCase):
    def test_source_row_is_locked_during_item_commit_but_not_http(self):
        source = source_fixture()
        other = connection.copy(alias="source_policy_lock")
        other.ensure_connection()
        try:
            with other.cursor() as cursor:
                cursor.execute("SET lock_timeout = '100ms'")

            def fetched_without_lock(*args, **kwargs):
                with other.cursor() as cursor:
                    cursor.execute(
                        "UPDATE ingestion_source SET owner = %s WHERE id = %s", ["Admin", source.pk]
                    )
                return fetched()

            def classified_under_lock(item):
                with self.assertRaises(OperationalError):
                    with other.cursor() as cursor:
                        cursor.execute(
                            "UPDATE ingestion_source SET enabled = false WHERE id = %s", [source.pk]
                        )

            with (
                patch("ingestion.service.fetch_entries", side_effect=fetched_without_lock),
                patch("ingestion.service.classify_and_save", side_effect=classified_under_lock),
            ):
                run = ingest_source(source, Mock())
            self.assertEqual((run.status, run.created), ("success", 1))
            with other.cursor() as cursor:
                cursor.execute(
                    "UPDATE ingestion_source SET enabled = false WHERE id = %s", [source.pk]
                )
            source.refresh_from_db()
            self.assertFalse(source.enabled)
            self.assertEqual(source.owner, "Admin")
        finally:
            other.close()
