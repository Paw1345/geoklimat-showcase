from django.test import TestCase

from core.quality import report
from ingestion.description_quality import has_description
from ingestion.models import Item, Source


class DescriptionQualityTests(TestCase):
    def test_explicit_markers_only(self):
        for value in ("", " ", "-", " – ", "—", "...", "…", "\n-\t", "\u00a0-\u00a0"):
            with self.subTest(value=value):
                self.assertFalse(has_description(value))
        for value in ("0", "- opis", "Krótki opis", "Brak zmian", "A…"):
            self.assertTrue(has_description(value))

    def test_report_and_display_preserve_original_marker(self):
        source = Source.objects.create(slug="quality", name="Quality", adapter="html")
        missing = ("", " ", "-", " – ", "—", "...", "…", "\n-\t", "\u00a0-\u00a0")
        for index, summary in enumerate((*missing, "Rzeczywisty krótki opis")):
            Item.objects.create(
                source=source,
                external_id=str(index),
                title="Dokument",
                summary=summary,
                url="https://www.gov.pl/test",
                content_hash=str(index),
            )
        self.assertEqual(report()["items_without_description"], len(missing))
        item = Item.objects.get(summary="-")
        response = self.client.get(f"/items/{item.pk}/")
        self.assertContains(response, "Brak opisu źródłowego")
        self.assertContains(response, "Wartość podana przez źródło")
        item.refresh_from_db()
        self.assertEqual(item.summary, "-")
        self.assertEqual(item.content_hash, "2")

    def test_short_real_description_is_not_hidden(self):
        source = Source.objects.create(slug="real", name="Real", adapter="html")
        item = Item.objects.create(
            source=source,
            external_id="1",
            title="Dokument",
            summary="Krótki opis",
            url="https://www.gov.pl/test",
        )
        self.assertContains(self.client.get(f"/items/{item.pk}/"), "Krótki opis")
        self.assertTrue(item.has_description)
