from io import StringIO

from django.core.management import call_command
from django.db import transaction
from django.test import TestCase

from ingestion.models import DuplicateCandidate, Item, ItemVersion, Source
from ingestion.normalization import canonical_url, normalized_date, title_key
from ingestion.publications import enrich, review_candidate, suggest_duplicates


class PublicationTests(TestCase):
    def setUp(self):
        self.a = Source.objects.create(slug="a", name="A", organization="A", adapter="html")
        self.b = Source.objects.create(slug="b", name="B", organization="B", adapter="html")

    def item(self, source=None, **kwargs):
        fields = dict(
            source=source or self.a,
            external_id=str(Item.objects.count()),
            title="Zmiany zasad udostępniania danych środowiskowych w 2026 roku",
            url=f"https://www.gov.pl/test/{Item.objects.count()}",
            summary="Informacje o aktualizacji usług przestrzennych i zasadach dostępu do danych. "
            * 4,
            metadata={"publication_date_raw": "18.09.2026"},
            content_hash="a",
        )
        fields.update(kwargs)
        item = Item.objects.create(**fields)
        with transaction.atomic():
            enrich(item)
        return item

    def test_canonical_conservative(self):
        self.assertEqual(
            canonical_url("https://WWW.gov.pl:443/a?version=2&utm_source=x#top"),
            "https://www.gov.pl/a?version=2",
        )
        self.assertNotEqual(
            canonical_url("https://a.pl/?version=1"), canonical_url("https://a.pl/?version=2")
        )
        self.assertNotEqual(canonical_url("https://a.pl/"), canonical_url("http://a.pl/"))
        with self.assertRaises(ValueError):
            canonical_url("javascript:alert(1)")

    def test_dates_and_unicode(self):
        value, precision = normalized_date("18.09.2026")
        self.assertEqual(value.isoformat(), "2026-09-17T22:00:00+00:00")
        self.assertEqual(precision, "day")
        self.assertEqual(normalized_date("unknown"), (None, "unknown"))
        self.assertEqual(title_key("<b> ŻÓŁĆ 2026 </b>"), "żółć 2026")

    def test_exact_merge_preserves_records(self):
        left, right = self.item(), self.item(self.b)
        left.refresh_from_db()
        self.assertEqual(left.publication_id, right.publication_id)
        self.assertEqual(Item.objects.count(), 2)
        self.assertFalse(right.publication.primary_confirmed)

    def test_url_merge_without_excerpt(self):
        left = self.item(summary="")
        right = self.item(self.b, url=left.url + "?utm_source=b", summary="")
        self.assertEqual(left.publication_id, right.publication_id)

    def test_different_year_not_merged(self):
        left = self.item()
        right = self.item(self.b, title=left.title.replace("2026", "2027"))
        self.assertNotEqual(left.publication_id, right.publication_id)
        suggest_duplicates(right)
        self.assertEqual(DuplicateCandidate.objects.count(), 1)

    def test_legal_metadata_never_content_merged(self):
        self.a.adapter = self.b.adapter = "eli"
        self.a.save()
        self.b.save()
        self.assertNotEqual(self.item().publication_id, self.item(self.b).publication_id)

    def test_version_idempotency_and_revert(self):
        item = self.item()
        enrich(item)
        self.assertEqual(item.versions.count(), 1)
        original = item.summary
        item.summary = "Changed"
        enrich(item)
        item.summary = original
        enrich(item)
        self.assertEqual(item.versions.count(), 3)
        self.assertIn("summary", item.versions.first().changed_fields)

    def test_candidate_review_and_stale_guard(self):
        left = self.item()
        right = self.item(self.b, title=left.title + " — komunikat")
        suggest_duplicates(right)
        candidate = DuplicateCandidate.objects.get()
        right.summary += " Updated"
        enrich(right)
        with self.assertRaises(ValueError):
            review_candidate(candidate.pk, True, "editor")
        suggest_duplicates(right)
        review_candidate(candidate.pk, True, "editor")
        left.refresh_from_db()
        right.refresh_from_db()
        self.assertEqual(left.publication_id, right.publication_id)
        candidate.refresh_from_db()
        self.assertEqual(candidate.reviewed_by, "editor")

    def test_reject_is_preserved(self):
        left = self.item()
        right = self.item(self.b, title=left.title + " — komunikat")
        suggest_duplicates(right)
        candidate = DuplicateCandidate.objects.get()
        review_candidate(candidate.pk, False, "editor")
        suggest_duplicates(right)
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, "rejected")

    def test_backfill_no_loss_and_repeat(self):
        self.item()
        call_command("normalize_items", stdout=StringIO())
        call_command("normalize_items", apply=True, stdout=StringIO())
        call_command("normalize_items", apply=True, stdout=StringIO())
        self.assertEqual(ItemVersion.objects.count(), 1)
        self.assertEqual(Item.objects.count(), 1)

    def test_history_escaping_and_grouped_home(self):
        left = self.item(title="<script>alert(1)</script>")
        self.item(self.b, url=left.url)
        page = self.client.get(f"/items/{left.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "<script>")
        self.assertEqual(len(self.client.get("/").context["items"]), 1)
        self.assertEqual(len(self.client.get("/?source=b").context["items"]), 1)
        self.assertEqual(self.client.get("/items/999999/").status_code, 404)

    def test_confirm_primary(self):
        item = self.item()
        call_command(
            "confirm_primary",
            item.pk,
            note="Official attribution",
            reviewer="editor",
            apply=True,
            stdout=StringIO(),
        )
        enrich(item)
        item.publication.refresh_from_db()
        self.assertTrue(item.publication.primary_confirmed)
