import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase
from django.utils import timezone

from ingestion import test_stac_recent as fixtures
from ingestion.models import DataChannel, DataHarvest, DataSnapshot
from ingestion.stac_bounded import defaults
from ingestion.transport import FetchError

PATH = "ingestion.management.commands.activate_stac_bounded"


class StacBoundedActivationTests(TransactionTestCase):
    def test_expired_audit_refuses_both_profiles(self):
        with (
            patch(
                PATH + ".probe", return_value={"checked_at": timezone.now() - timedelta(minutes=11)}
            ),
            patch(PATH + ".capacity", return_value={"fits_with_sample_estimate": True}),
        ):
            with self.assertRaises(CommandError):
                call_command("activate_stac_bounded", apply=True, stdout=io.StringIO())
        self.assertFalse(
            DataChannel.objects.filter(config__mode="stac_bounded", enabled=True).exists()
        )

    def setUp(self):
        fixtures.StacRecentTests.setUp(self)
        fixtures.StacRecentTests.add_europe(self)
        for parent in DataChannel.objects.filter(
            slug__in=[self.channel.slug, "copernicus-scenes-europe"]
        ):
            DataChannel.objects.create(slug=parent.slug + "-bounded", **defaults(parent))

    def response(self, *args, **kwargs):
        return fixtures.StacRecentTests.response(self, *args, **kwargs)

    def transport(self):
        return fixtures.StacRecentTests.transport(self)

    def command(self, **options):
        output = io.StringIO()
        with (
            patch(PATH + ".Client", return_value=self.transport()),
            patch(PATH + ".capacity", return_value={"fits_with_sample_estimate": True}),
        ):
            call_command("activate_stac_bounded", stdout=output, **options)
        return json.loads(output.getvalue())

    def test_dry_probes_actual_bounded_profile_without_writes(self):
        before = list(DataChannel.objects.values())
        result = self.command()
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["audit"]["pages"], 2)
        self.assertEqual(before, list(DataChannel.objects.values()))
        self.assertEqual(DataSnapshot.objects.count(), 0)
        self.assertEqual(DataHarvest.objects.count(), 1)

    def test_apply_two_profiles_and_repeat_without_http_or_schedule_reset(self):
        parent_before = list(DataChannel.objects.exclude(config__mode="stac_bounded").values())
        self.command(apply=True)
        children = DataChannel.objects.filter(config__mode="stac_bounded")
        self.assertEqual(children.filter(enabled=True, access_approved=True).count(), 2)
        before = list(children.values())
        with patch(PATH + ".Client") as client:
            call_command("activate_stac_bounded", apply=True, stdout=io.StringIO())
            client.assert_not_called()
        self.assertEqual(before, list(children.values()))
        self.assertEqual(
            parent_before, list(DataChannel.objects.exclude(config__mode="stac_bounded").values())
        )

    def test_cache_failure_or_unknown_head_estimate_leaves_both_disabled(self):
        for fits in (False, None):
            with (
                patch(PATH + ".Client", return_value=self.transport()),
                patch(PATH + ".capacity", return_value={"fits_with_sample_estimate": fits}),
            ):
                with self.assertRaises(CommandError):
                    call_command("activate_stac_bounded", apply=True, stdout=io.StringIO())
        self.assertFalse(
            DataChannel.objects.filter(config__mode="stac_bounded", enabled=True).exists()
        )

    def test_failed_probe_leaves_both_disabled_and_has_safe_error(self):
        client = self.transport()
        client.get.side_effect = FetchError("private error")
        with patch(PATH + ".Client", return_value=client):
            with self.assertRaisesMessage(CommandError, "activation refused"):
                call_command("activate_stac_bounded", apply=True, stdout=io.StringIO())
        self.assertFalse(
            DataChannel.objects.filter(config__mode="stac_bounded", enabled=True).exists()
        )

    def test_admin_drift_in_second_profile_rolls_back_first_activation(self):
        from ingestion.management.commands.activate_stac_bounded import probe

        calls = []

        def changing_probe(channel):
            with patch(PATH + ".Client", return_value=self.transport()):
                result = probe(channel)
            calls.append(channel.pk)
            if len(calls) == 2:
                DataChannel.objects.filter(pk=channel.pk).update(interval_minutes=60)
            return result

        with (
            patch(PATH + ".probe", side_effect=changing_probe),
            patch(PATH + ".capacity", return_value={"fits_with_sample_estimate": True}),
        ):
            with self.assertRaises(CommandError):
                call_command("activate_stac_bounded", apply=True, stdout=io.StringIO())
        self.assertFalse(
            DataChannel.objects.filter(config__mode="stac_bounded", enabled=True).exists()
        )
        self.assertEqual(DataChannel.objects.filter(pk=calls[1]).get().interval_minutes, 60)

    def test_approval_drift_refuses_activation(self):
        from ingestion.management.commands.activate_stac_bounded import probe

        def changed_probe(channel):
            with patch(PATH + ".Client", return_value=self.transport()):
                result = probe(channel)
            DataChannel.objects.filter(pk=channel.pk).update(access_approved=True)
            return result

        with (
            patch(PATH + ".probe", side_effect=changed_probe),
            patch(PATH + ".capacity", return_value={"fits_with_sample_estimate": True}),
        ):
            with self.assertRaises(CommandError):
                call_command("activate_stac_bounded", apply=True, stdout=io.StringIO())
        self.assertFalse(
            DataChannel.objects.filter(config__mode="stac_bounded", enabled=True).exists()
        )
