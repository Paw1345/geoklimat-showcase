from django.core.management.base import BaseCommand, CommandError

from ingestion.models import DuplicateCandidate
from ingestion.publications import review_candidate
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Review a candidate: default preview, --apply required to accept/reject."

    def add_arguments(self, parser):
        parser.add_argument("candidate", type=int)
        parser.add_argument("decision", choices=["accept", "reject"])
        parser.add_argument("--reviewer", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired:
            if not acquired:
                raise CommandError("Ingestion is busy; retry later.")
            try:
                candidate = DuplicateCandidate.objects.select_related("left", "right").get(
                    pk=options["candidate"]
                )
                self.stdout.write(
                    f"{candidate.left.title}\n{candidate.right.title}\n"
                    f"Score: {candidate.score}; status: {candidate.status}"
                )
                if options["apply"]:
                    review_candidate(
                        candidate.pk, options["decision"] == "accept", options["reviewer"]
                    )
                    self.stdout.write("Decision saved.")
                else:
                    self.stdout.write("Preview only. Add --apply to save the decision.")
            except (DuplicateCandidate.DoesNotExist, ValueError) as exc:
                raise CommandError(str(exc)) from exc
