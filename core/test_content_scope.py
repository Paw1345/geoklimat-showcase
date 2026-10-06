from unittest.mock import patch

from django.test import TestCase

from core.content_scope import CATALOGUE_KINDS, filter_content
from ingestion.models import Classification, Item, Publication, Source


class ContentScopeTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug="scope-test",
            name="Test",
            organization="GIOŚ",
            adapter="web",
            url="https://www.gov.pl/test",
            language="pl",
        )

    def item(self, kind=None, **kwargs):
        return Item.objects.create(
            source=self.source,
            external_id=str(Item.objects.count()),
            title="INSPIRE Test",
            url="https://www.gov.pl/test",
            metadata={} if kind is None else {"record_kind": kind},
            **kwargs,
        )

    def rows(self, path, **params):
        response = self.client.get(path, params)
        self.assertEqual(response.status_code, 200)
        return response, set(response.context["items"])

    def test_defaults_unchanged_and_no_filter_is_relevance_decision(self):
        news = self.item("news")
        metadata = self.item("dataset_collection")
        Classification.objects.create(item=news, auto_state="excluded")
        for path in ("/", "/search/"):
            for scope in (None, ""):
                params = {} if scope is None else {"record_scope": scope}
                _, rows = self.rows(path, **params)
                self.assertEqual(rows, {news, metadata})
            _, rows = self.rows(path, record_scope="publications")
            self.assertEqual(rows, {news})

    def test_known_catalogue_types_and_unknowns_are_preserved(self):
        catalogues = {self.item(kind) for kind in CATALOGUE_KINDS}
        publications = {
            self.item(kind)
            for kind in (
                None,
                "news",
                "news_notice",
                "funding_call",
                "technical_document",
                "page_snapshot",
                "publication_metadata",
                "future_kind",
            )
        }
        null = self.item("json_null")
        null.metadata = {"record_kind": None}
        null.save()
        publications.add(null)
        for path in ("/", "/search/"):
            _, rows = self.rows(path, record_scope="publications")
            self.assertEqual(rows, publications)
            _, rows = self.rows(path, record_scope="catalogues")
            self.assertEqual(rows, catalogues)

    def test_dataset_catalogue_is_not_a_publication(self):
        catalogue = self.item("dataset_catalogue")
        news = self.item("news")
        for path in ("/", "/search/"):
            with self.subTest(path=path):
                _, rows = self.rows(path, record_scope="catalogues", source=self.source.slug)
                self.assertEqual(rows, {catalogue})
                _, rows = self.rows(path, record_scope="publications", source=self.source.slug)
                self.assertEqual(rows, {news})

    def test_scope_filters_before_group_representative_selection(self):
        primary = self.item("dataset_collection")
        group = Publication.objects.create(primary_item=primary)
        primary.publication = group
        primary.save()
        news = self.item("news", publication=group)
        self.item("news", publication=group)
        for path in ("/", "/search/"):
            _, rows = self.rows(path, record_scope="publications")
            self.assertEqual(rows, {news})
            _, rows = self.rows(path, record_scope="catalogues")
            self.assertEqual(rows, {primary})
            _, rows = self.rows(path)
            self.assertEqual(len(rows), 1)

    def test_source_topic_manual_state_and_search_combine_with_scope(self):
        news = self.item("news")
        metadata = self.item("metadata")
        for item in (news, metadata):
            Classification.objects.create(
                item=item,
                topics=["spatial_information"],
                auto_state="excluded",
                manual_state="relevant",
            )
        self.item("news")
        for path in ("/", "/search/"):
            _, rows = self.rows(
                path,
                record_scope="publications",
                source=self.source.slug,
                topic="spatial_information",
                status="relevant",
                q="INSPIRE",
            )
            self.assertEqual(rows, {news})

    def test_pagination_and_form_keep_scope(self):
        for _ in range(31):
            self.item("news")
        self.item("metadata")
        for path in ("/", "/search/"):
            response, rows = self.rows(path, record_scope="publications")
            self.assertEqual(len(rows), 30)
            self.assertContains(response, "record_scope=publications&amp;page=2")
            self.assertContains(response, 'value="publications" selected')
            _, rows = self.rows(path, record_scope="publications", page=2)
            self.assertEqual(len(rows), 1)

    def test_eli_home_rule_is_not_changed_by_scope(self):
        self.source.adapter = "eli"
        self.source.save()
        act = self.item()
        for scope in ("", "publications"):
            _, rows = self.rows("/", record_scope=scope)
            self.assertEqual(rows, set())
        _, rows = self.rows("/", record_scope="publications", source=self.source.slug)
        self.assertEqual(rows, {act})
        _, rows = self.rows("/search/", record_scope="publications")
        self.assertEqual(rows, {act})

    def test_invalid_scope_has_visible_error_and_no_unfiltered_fallback(self):
        self.item()
        for path in ("/", "/search/"):
            response = self.client.get(path, {"record_scope": "unknown"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(list(response.context["items"]), [])
            form = response.context["scope_form" if path == "/" else "form"]
            self.assertIn("record_scope", form.errors)
        with self.assertRaises(ValueError):
            filter_content(Item.objects.all(), "unknown")

    def test_filter_is_read_only_without_http_or_classification_changes(self):
        item = self.item("dataset_collection")
        classification = Classification.objects.create(item=item, auto_state="review")
        before = (Item.objects.count(), Classification.objects.count())
        with patch("ingestion.transport.Client.get") as get, patch.object(Item, "save") as save:
            for path in ("/", "/search/"):
                self.rows(path, record_scope="catalogues")
                self.assertEqual(self.client.post(path).status_code, 405)
        get.assert_not_called()
        save.assert_not_called()
        classification.refresh_from_db()
        self.assertEqual(classification.auto_state, "review")
        self.assertEqual(before, (Item.objects.count(), Classification.objects.count()))
