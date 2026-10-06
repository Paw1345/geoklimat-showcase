from datetime import UTC, datetime

from django.db import connection
from django.test import TestCase

from core.search import search_items
from ingestion.models import (
    Classification,
    Item,
    Publication,
    RegistryEntry,
    RegistryRelease,
    Source,
)


class SearchTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug="test",
            name="Test",
            organization="GIOŚ",
            adapter="eli",
            url="https://example.org/",
            language="pl",
        )

    def item(self, title, **kwargs):
        return Item.objects.create(
            source=self.source,
            external_id=str(Item.objects.count()),
            title=title,
            url="https://example.org/item",
            **kwargs,
        )

    def results(self, **params):
        response = self.client.get("/search/", params)
        self.assertEqual(response.status_code, 200)
        return list(response.context["items"])

    def test_polish_case_and_legal_acts_included(self):
        item = self.item("OCHRONA ŚRODOWISKA — ustawa")
        self.assertEqual(self.results(q="środowiska"), [item])
        self.assertEqual(self.results(), [item])

    def test_title_weight_above_description(self):
        title = self.item("INSPIRE")
        self.item("Raport", summary="INSPIRE")
        self.assertEqual(self.results(q="INSPIRE")[0], title)

    def test_phrase_or_exclusion_and_punctuation(self):
        first = self.item("ochrona środowiska INSPIRE")
        second = self.item("ochrona wód")
        self.item("ochrona środowiska odpady")
        self.assertEqual(self.results(q='"ochrona środowiska" -odpady'), [first])
        self.assertEqual(set(self.results(q="INSPIRE OR wód")), {first, second})
        self.assertEqual(self.results(q="!!!"), [])

    def test_matching_duplicate_member_not_hidden_by_primary(self):
        primary = self.item("Inny tytuł")
        group = Publication.objects.create(primary_item=primary)
        primary.publication = group
        primary.save()
        hit = self.item("INSPIRE", publication=group)
        self.item("INSPIRE", publication=group)
        self.assertEqual(self.results(q="INSPIRE"), [hit])
        self.assertEqual(len(self.results()), 1)

    def test_filters_manual_override_language_topic_source_kind(self):
        item = self.item("INSPIRE")
        Classification.objects.create(
            item=item,
            topics=["spatial_information"],
            kind="Komunikat",
            auto_state="excluded",
            manual_state="relevant",
        )
        self.assertEqual(
            self.results(
                q="INSPIRE",
                status="relevant",
                topic="spatial_information",
                language="pl",
                source="test",
                kind="Komunikat",
            ),
            [item],
        )
        self.assertEqual(self.results(status="excluded"), [])
        self.assertEqual(self.results(status="pending"), [])

    def test_publication_date_range_and_null_order(self):
        unknown = self.item("Brak daty")
        first = self.item("Pierwszy", published_at=datetime(2026, 1, 1, tzinfo=UTC))
        second = self.item("Drugi", published_at=datetime(2026, 1, 2, tzinfo=UTC))
        self.assertEqual(self.results(sort="oldest"), [first, second, unknown])
        self.assertEqual(self.results(sort="newest"), [second, first, unknown])
        self.assertEqual(self.results(date_from="2026-01-02", date_to="2026-01-02"), [second])

    def test_invalid_inputs_are_visible_errors(self):
        for params in (
            {"q": "x" * 201},
            {"sort": "sql"},
            {"source": "missing"},
            {"date_from": "wrong"},
            {"date_from": "2026-02-01", "date_to": "2026-01-01"},
            {"registry": "999"},
        ):
            with self.subTest(params=params):
                response = self.client.get("/search/", params)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.context["form"].errors)

    def test_registry_derived_consultation_filter(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=61, release=release, master={}, technical={}
        )
        registry.sources.add(self.source)
        hit = self.item("Konsultacje INSPIRE")
        self.item("INSPIRE news")
        Classification.objects.create(item=hit, kind="Konsultacje", auto_state="review")
        self.assertEqual(self.results(registry=61), [hit])

    def test_pagination_keeps_filters_and_escapes_content(self):
        for i in range(31):
            self.item(f"INSPIRE {i}", summary="<script>alert(1)</script>")
        response = self.client.get("/search/", {"q": "INSPIRE", "sort": "collected"})
        self.assertContains(response, "q=INSPIRE&amp;sort=collected&amp;page=2")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")
        self.assertEqual(len(self.results(q="INSPIRE", page=2)), 1)
        self.assertEqual(len(self.results(q="INSPIRE", page="wrong")), 30)

    def test_index_updates_on_insert_and_edit(self):
        item = self.item("INSPIRE")
        self.assertTrue(search_items({"q": "INSPIRE"}).exists())
        Item.objects.filter(pk=item.pk).update(title="GUGiK")
        self.assertFalse(search_items({"q": "INSPIRE"}).exists())
        self.assertTrue(search_items({"q": "GUGiK"}).exists())
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname = %s", ["item_document_search_gin"]
            )
            self.assertIn("USING gin", cursor.fetchone()[0])

    def test_read_only_endpoint(self):
        self.assertEqual(self.client.post("/search/").status_code, 405)
