from django.core.management.base import BaseCommand

from ingestion.alert_email import send_pending
from ingestion.alerts import collect
from ingestion.models import ThreadWatch


class Command(BaseCommand):
    help = (
        "Collect private in-app alerts and due digests. Dry-run default. With --apply it also "
        "mails unsent digests when ALERT_EMAIL_ENABLED is set."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        if options["apply"]:
            self.stdout.write(f"Created notifications: {collect()}")
            self.stdout.write(f"Mailed digests: {send_pending()}")
        else:
            self.stdout.write(
                f"Active watches: {ThreadWatch.objects.filter(active=True).count()}; no writes."
            )
