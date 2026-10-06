from django.core.management.base import BaseCommand

from ingestion.classification import classify
from ingestion.classification_examples import EXAMPLES
from ingestion.models import Item, Source


class Command(BaseCommand):
    help = "Evaluate synthetic regression examples; not a production quality claim."

    def handle(self, *args, **options):
        tp = fp = fn = correct = 0
        for title, expected, topic in EXAMPLES:
            result = classify(Item(title=title, summary="", source=Source(adapter="html")))
            predicted = result["auto_state"]
            correct += predicted == expected and (topic is None or topic in result["topics"])
            tp += predicted == "relevant" and expected == "relevant"
            fp += predicted == "relevant" and expected != "relevant"
            fn += predicted != "relevant" and expected == "relevant"
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / (tp + fn) if tp + fn else 0
        self.stdout.write(
            f"SYNTHETIC ONLY: {correct}/{len(EXAMPLES)} checks; "
            f"relevant precision={precision:.3f}, recall={recall:.3f}"
        )
