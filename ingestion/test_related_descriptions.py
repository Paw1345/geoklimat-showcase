from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import TestCase

from ingestion.detail_descriptions import preserve_description
from ingestion.models import Item, Source
from ingestion.related_descriptions import fetch, save
from ingestion.transport import FetchError, Response


class RelatedDescriptionTests(TestCase):
    def setUp(self):
        source = Source.objects.create(
            slug="mkis-links",
            name="MKiŚ",
            adapter="html",
            enabled=True,
            activation_approved=True,
            url="https://www.gov.pl/web/klimat/konsultacje-publiczne",
        )
        self.item = Item.objects.create(
            source=source,
            external_id="a",
            title="Katalog",
            summary="",
            url="https://www.gov.pl/web/klimat/katalog",
            content_hash="a" * 64,
        )
        self.target = "https://www.gov.pl/web/klimat/komunikat"
        self.parent = (
            '<main><article class="article-area__article"><h2>Katalog</h2>'
            '<div class="art-prev"><a href="/web/klimat/komunikat">Komunikat</a>'
            "</div></article></main>"
        )
        self.body = (
            '<article id="main-content"><h2>Komunikat</h2><div class="editor-content">'
            + "Źródłowa treść konsultacji. " * 30
            + "</div></article>"
        )

    def client_for(self, parent=None, body=None):
        return Mock(
            get=Mock(
                side_effect=[
                    Response(200, (parent or self.parent).encode(), {}, self.item.url),
                    Response(200, (body or self.body).encode(), {}, self.target),
                ]
            )
        )

    def test_preview_save_repeat_history_and_separate_provenance(self):
        with patch(
            "ingestion.management.commands.fetch_related_description.Client",
            return_value=self.client_for(),
        ):
            call_command("fetch_related_description", self.item.pk, stdout=StringIO())
        self.item.refresh_from_db()
        self.assertEqual(self.item.metadata, {})
        evidence = fetch(self.item, self.client_for())
        updated = save(self.item, evidence)
        self.assertEqual(updated.summary, "")
        self.assertEqual(updated.url, self.item.url)
        self.assertEqual(updated.metadata["related_description"]["url"], self.target)
        self.assertEqual(updated.revision, 2)
        self.assertEqual(save(updated, evidence).content_hash, updated.content_hash)
        self.assertContains(self.client.get(f"/items/{updated.pk}/"), "Powiązany komunikat MKiŚ")
        self.assertContains(self.client.get(f"/items/{updated.pk}/"), self.target)

    def test_ambiguous_foreign_link_and_title_mismatch_fail_closed(self):
        for parent in (
            self.parent.replace("Katalog", "Inny tytuł"),
            self.parent.replace("/web/klimat/komunikat", "https://example.org/other"),
            self.parent.replace("</a>", '</a><a href="/web/klimat/drugi">Drugi</a>'),
        ):
            client = self.client_for(parent=parent)
            with self.assertRaises(FetchError):
                fetch(self.item, client)
            self.assertEqual(client.get.call_count, 1)
        with self.assertRaises(FetchError):
            fetch(self.item, self.client_for(body=self.body.replace("Komunikat", "Inny")))
        self.assertEqual(self.item.versions.count(), 0)

    def test_preserve_changed_listing_and_concurrent_guard(self):
        evidence = fetch(self.item, self.client_for())
        updated = save(self.item, evidence)
        fields = {"content_hash": "a" * 64, "url": updated.url, "title": updated.title}
        preserve_description(updated, fields)
        self.assertEqual(fields["metadata"], updated.metadata)
        changed = {"content_hash": "b" * 64, "url": updated.url, "title": updated.title}
        preserve_description(updated, changed)
        self.assertNotIn("metadata", changed)
        Item.objects.filter(pk=updated.pk).update(content_hash="c" * 64)
        with self.assertRaises(FetchError):
            save(updated, evidence)
