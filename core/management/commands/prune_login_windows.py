from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import LoginWindow


class Command(BaseCommand):
    help = "Remove expired login counters older than one day; dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        rows = LoginWindow.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1))
        count = rows.count()
        if options["apply"]:
            rows.delete()
        self.stdout.write(f"Expired counters: {count}; applied={options['apply']}")
