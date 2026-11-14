import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from ingestion.alert_email import send_pending
from ingestion.models import Notification


@override_settings(ALERT_EMAIL_ENABLED=True, SITE_URL="https://geoklimat.example/")
class AlertEmailTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = get_user_model().objects.create_user("reader", email="reader@example.org")

    def digest(self, user=None, entries=2, **fields):
        user = user or self.user
        digest = Notification.objects.create(
            user=user,
            kind="digest",
            title="Podsumowanie dzienne",
            payload={"count": entries},
            created_at=fields.pop("created_at", self.now),
            **fields,
        )
        for number in range(entries):
            Notification.objects.create(
                user=user, kind="change", title=f"Wątek {number}", digest=digest
            )
        return digest

    def test_sends_one_message_with_entries_and_link(self):
        digest = self.digest()
        self.assertEqual(send_pending(self.now), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["reader@example.org"])
        self.assertIn("(2)", message.subject)
        self.assertIn("- Wątek 0", message.body)
        self.assertIn(f"https://geoklimat.example/alerts/{digest.pk}/", message.body)
        digest.refresh_from_db()
        self.assertEqual(digest.payload["emailed_at"], self.now.isoformat())

    def test_second_run_does_not_send_again(self):
        self.digest()
        send_pending(self.now)
        self.assertEqual(send_pending(self.now + timedelta(minutes=1)), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_empty_day_sends_nothing(self):
        Notification.objects.create(user=self.user, kind="change", title="Bez podsumowania")
        self.assertEqual(send_pending(self.now), 0)
        self.assertEqual(mail.outbox, [])

    def test_skips_missing_address_inactive_user_and_old_digest(self):
        users = get_user_model().objects
        self.digest(user=users.create_user("no-mail"))
        self.digest(user=users.create_user("gone", email="gone@example.org", is_active=False))
        self.digest(created_at=self.now - timedelta(days=4))
        self.assertEqual(send_pending(self.now), 0)
        self.assertEqual(mail.outbox, [])

    def test_long_digest_lists_first_entries_and_the_rest_as_a_count(self):
        self.digest(entries=23)
        send_pending(self.now)
        body = mail.outbox[0].body
        self.assertIn("- Wątek 19", body)
        self.assertNotIn("- Wątek 20", body)
        self.assertIn("i 3 kolejnych", body)

    def test_other_users_entries_are_never_listed(self):
        digest = self.digest(entries=1)
        other = get_user_model().objects.create_user("other", email="other@example.org")
        Notification.objects.create(user=other, kind="change", title="Cudzy wątek", digest=digest)
        send_pending(self.now)
        self.assertNotIn("Cudzy", mail.outbox[0].body)

    def test_failed_send_is_retried_and_logs_no_address(self):
        digest = self.digest()
        with (
            patch("ingestion.alert_email.send_mail", side_effect=OSError("reader@example.org")),
            self.assertLogs("ingestion.alert_email", "ERROR") as logs,
        ):
            self.assertEqual(send_pending(self.now), 0)
        self.assertNotIn("example.org", "".join(logs.output))
        digest.refresh_from_db()
        self.assertNotIn("emailed_at", digest.payload)
        self.assertEqual(send_pending(self.now), 1)

    def test_disabled_by_default_and_command_reports_count(self):
        self.digest()
        with override_settings(ALERT_EMAIL_ENABLED=False):
            self.assertEqual(send_pending(self.now), 0)
        self.assertEqual(mail.outbox, [])
        out = io.StringIO()
        call_command("collect_alerts", apply=True, stdout=out)
        self.assertIn("Mailed digests: 1", out.getvalue())
        out = io.StringIO()
        call_command("collect_alerts", stdout=out)
        self.assertEqual(len(mail.outbox), 1)
