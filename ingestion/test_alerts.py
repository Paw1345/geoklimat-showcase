import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from ingestion.alerts import collect, collect_user, configure, difference, set_watch, snapshot
from ingestion.legislation import sync_item
from ingestion.models import AlertPreference, Item, LegalEvent, Notification, Source, ThreadWatch


class AlertTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alerts")
        self.other = get_user_model().objects.create_user(username="other")
        source = Source.objects.create(
            slug="alert",
            name="Alert",
            adapter="eli",
            url="https://api.sejm.gov.pl/eli/acts/DU/2026",
        )
        self.item = Item.objects.create(
            source=source,
            external_id="one",
            title="Akt <script>x</script>",
            url="https://eli.gov.pl/eli/DU/2026/1/ogl",
            content_hash="a" * 64,
            metadata={"ELI": "DU/2026/1"},
        )
        self.thread = sync_item(self.item).change_thread
        self.now = timezone.now()

    def change(self):
        self.item.content_hash = "b" * 64
        self.item.save()
        sync_item(self.item)

    def notice(self, user=None, **kwargs):
        return Notification.objects.create(
            user=user or self.user,
            thread=self.thread,
            kind="change",
            title=self.item.title,
            payload={"items": [self.item.pk]},
            **kwargs,
        )

    def test_initial_follow_and_repeat_do_not_flood(self):
        set_watch(self.user, self.thread, True)
        self.assertEqual(collect(), 0)
        self.change()
        self.assertEqual(collect(), 1)
        self.assertEqual(collect(), 0)
        self.assertEqual(Notification.objects.get().payload["changed"], 1)

    def test_existing_watch_baseline_is_silent(self):
        ThreadWatch.objects.create(user=self.user, thread=self.thread)
        self.assertEqual(collect(), 0)
        self.assertIsNotNone(ThreadWatch.objects.get().alert_state)

    def test_repeated_follow_preserves_pending_change(self):
        set_watch(self.user, self.thread, True)
        self.change()
        set_watch(self.user, self.thread, True)
        self.assertEqual(collect(), 1)

    def test_unfollow_and_refollow_reset_baseline(self):
        set_watch(self.user, self.thread, True)
        set_watch(self.user, self.thread, False)
        self.change()
        self.assertEqual(collect(), 0)
        set_watch(self.user, self.thread, True)
        self.assertEqual(collect(), 0)

    def test_pause_resume_and_inactive_account(self):
        set_watch(self.user, self.thread, True)
        configure(self.user, False, "daily")
        self.change()
        self.assertEqual(collect(), 0)
        configure(self.user, True, "daily")
        self.assertEqual(collect(), 0)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(collect(), 0)

    def test_snapshot_detects_removed_or_stale_deadline(self):
        set_watch(self.user, self.thread, True)
        link = self.item.legal_links.first()
        event = LegalEvent.objects.create(
            link=link,
            event_key="deadline",
            kind="consultation_end",
            date=self.now.date(),
            description="Termin",
            evidence_url=self.item.url,
            evidence="Dowód",
            item_hash=self.item.content_hash,
        )
        self.assertEqual(collect(), 1)
        self.assertTrue(Notification.objects.first().payload["deadlines"])
        event.superseded_at = timezone.now()
        event.save()
        self.assertEqual(collect(), 1)
        self.assertEqual(collect(), 0)

    def test_difference_is_bounded_and_impact_state_tracked(self):
        old = {"items": {}, "impact": [1, True], "events": {}}
        new = {"items": {str(i): "x" for i in range(90)}, "impact": [1, False], "events": {}}
        result = difference(old, new)
        self.assertEqual(result["added"], 90)
        self.assertEqual(len(result["items"]), 50)
        self.assertTrue(result["impact"])
        self.assertIsNone(difference(new, new))

    def test_daily_digest_once_and_owner_isolation(self):
        configure(self.user, True, "daily")
        pref = AlertPreference.objects.get(user=self.user)
        start = pref.digest_since
        own = self.notice(created_at=start + timedelta(hours=1))
        self.notice(self.other, created_at=start + timedelta(hours=1))
        self.assertEqual(collect_user(self.user.pk, start + timedelta(hours=23)), 0)
        self.assertEqual(collect_user(self.user.pk, start + timedelta(days=1)), 1)
        digest = Notification.objects.get(kind="digest")
        self.assertEqual(digest.payload["count"], 1)
        self.assertEqual(list(digest.entries.values_list("pk", flat=True)), [own.pk])
        self.assertEqual(collect_user(self.user.pk, start + timedelta(days=1)), 0)
        self.assertEqual(collect_user(self.user.pk, start + timedelta(days=2)), 0)

    def test_weekly_digest_and_disabled_digest(self):
        configure(self.user, True, "weekly")
        start = AlertPreference.objects.get(user=self.user).digest_since
        self.notice(created_at=start + timedelta(hours=1))
        self.assertEqual(collect_user(self.user.pk, start + timedelta(days=6)), 0)
        self.assertEqual(collect_user(self.user.pk, start + timedelta(days=7)), 1)
        configure(self.user, True, "none")
        self.notice()
        self.assertEqual(collect_user(self.user.pk, start + timedelta(days=20)), 0)

    def test_failed_transaction_does_not_advance_snapshot(self):
        watch = set_watch(self.user, self.thread, True)
        before = watch.alert_state
        self.change()
        with patch("ingestion.alerts.Notification.objects.create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                collect()
        watch.refresh_from_db()
        self.assertEqual(watch.alert_state, before)
        self.assertEqual(collect(), 1)

    def test_inbox_get_is_readonly_and_private(self):
        own = self.notice()
        foreign = self.notice(self.other)
        self.assertEqual(self.client.get("/alerts/").status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get("/alerts/")
        self.assertContains(response, "&lt;script&gt;")
        self.assertEqual(self.client.get(f"/alerts/{foreign.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"/alerts/{foreign.pk}/read/").status_code, 404)
        self.assertEqual(self.client.get(f"/alerts/{own.pk}/").status_code, 200)
        own.refresh_from_db()
        self.assertIsNone(own.read_at)
        self.assertEqual(self.client.get(f"/alerts/{own.pk}/read/").status_code, 405)
        self.client.post(f"/alerts/{own.pk}/read/", {"user": self.other.pk})
        own.refresh_from_db()
        self.assertIsNotNone(own.read_at)
        self.assertNotContains(self.client.get("/alerts/?unread=1"), own.title)

    def test_preferences_readonly_validated_and_owner_scoped(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/alerts/preferences/").status_code, 200)
        self.assertFalse(AlertPreference.objects.exists())
        self.client.post("/alerts/preferences/", {"enabled": "on", "cadence": "bad"})
        self.assertFalse(AlertPreference.objects.exists())
        self.client.post(
            "/alerts/preferences/",
            {
                "enabled": "on",
                "cadence": "weekly",
                "user": self.other.pk,
            },
        )
        self.assertEqual(AlertPreference.objects.get().user, self.user)

    def test_csrf_and_post_only(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        notice = self.notice()
        self.assertEqual(client.post("/alerts/preferences/").status_code, 403)
        self.assertEqual(client.post(f"/alerts/{notice.pk}/read/").status_code, 403)
        self.assertEqual(self.client.post("/alerts/").status_code, 405)

    def test_command_dry_run_and_repeat(self):
        ThreadWatch.objects.create(user=self.user, thread=self.thread)
        call_command("collect_alerts", stdout=io.StringIO())
        self.assertIsNone(ThreadWatch.objects.get().alert_state)
        self.assertFalse(AlertPreference.objects.exists())
        call_command("collect_alerts", apply=True, stdout=io.StringIO())
        self.assertEqual(ThreadWatch.objects.get().alert_state, snapshot(self.thread))
        call_command("collect_alerts", apply=True, stdout=io.StringIO())
        self.assertEqual(Notification.objects.count(), 0)

    def test_unread_pagination_and_digest_detail(self):
        self.client.force_login(self.user)
        for _ in range(31):
            self.notice()
        response = self.client.get("/alerts/?page=2")
        self.assertEqual(len(response.context["notifications"]), 1)
        digest = Notification.objects.create(
            user=self.user, kind="digest", title="Podsumowanie", payload={"count": 31}
        )
        Notification.objects.filter(kind="change").update(digest=digest)
        response = self.client.get(f"/alerts/{digest.pk}/?page=2")
        self.assertEqual(len(response.context["entries"]), 1)
