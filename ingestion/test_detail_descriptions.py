from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from ingestion.adapters import Entry, safe_entry
from ingestion.detail_descriptions import eligible, extract, save_description
from ingestion.models import Item, Source
from ingestion.service import ingest_source
from ingestion.transport import FetchError, Response


class DetailDescriptionTests(TestCase):
    def inspire_setup(self):
        self.source.url = "https://knowledge-base.inspire.ec.europa.eu/tools/document-library_en"
        self.source.save()
        self.item.url = "https://knowledge-base.inspire.ec.europa.eu/publications/test_en"
        self.item.save()
        return (
            '<h1>Nowe dane GIS</h1><main id="main-content"><nav>Menu</nav>'
            '<h2 id="details">Details</h2><p>Data publikacji</p>'
            '<h2 id="description">Description</h2><div><div class="ecl">'
            + self.prose
            + '</div></div><h2 id="files">Files</h2><p>Download</p></main>'
        )

    def test_inspire_excerpt_provenance_refresh_and_preservation(self):
        html = self.inspire_setup()
        response = Response(200, html.encode(), {}, self.item.url)
        with patch(
            "ingestion.management.commands.fetch_description.Client.get", return_value=response
        ):
            call_command("fetch_description", self.item.pk, stdout=StringIO())
            self.item.refresh_from_db()
            self.assertFalse(self.item.summary)
            call_command("fetch_description", self.item.pk, apply=True, stdout=StringIO())
        self.item.refresh_from_db()
        proof = self.item.metadata["description_detail"]
        self.assertEqual(proof["profile"], "inspire-publication-v1")
        self.assertNotIn("Download", self.item.summary)
        self.assertNotIn("Menu", self.item.summary)
        summary, digest = extract(self.item, response)
        self.assertEqual(save_description(self.item, summary, digest, refresh=True).revision, 2)
        from ingestion.detail_descriptions import preserve_description

        fields = {
            "content_hash": proof["listing_hash"],
            "url": self.item.url,
            "title": self.item.title,
        }
        preserve_description(self.item, fields)
        self.assertEqual(fields["summary"], self.item.summary)

    def test_inspire_rejects_wrong_scope_heading_and_ambiguous_body(self):
        html = self.inspire_setup()
        for text in (
            html.replace("Nowe dane GIS", "Other title"),
            html.replace('id="description"', 'id="other"'),
            html.replace(self.prose, "-"),
            html.replace(self.prose, '<div class="ecl">Duplicate</div>' + self.prose),
        ):
            with self.assertRaises(FetchError):
                extract(self.item, Response(200, text.encode(), {}, self.item.url))
        for path in ("/news/test_en", "/publications/test_pl", "/publications/test_en?x=1"):
            self.item.url = "https://knowledge-base.inspire.ec.europa.eu" + path
            self.assertFalse(eligible(self.item))

    def test_refresh_same_text_is_noop_and_changed_text_keeps_listing_hash(self):
        summary, digest = extract(self.item, self.response)
        initial = save_description(self.item, summary, digest)
        initial_proof = dict(initial.metadata["description_detail"])
        unchanged = save_description(initial, summary, digest, refresh=True)
        self.assertEqual(unchanged.revision, 2)
        self.assertEqual(unchanged.metadata["description_detail"], initial_proof)
        changed_response = Response(
            200, self.html.replace(self.prose, self.prose + " Zmiana.").encode(), {}, self.url
        )
        excerpt, new_digest = extract(initial, changed_response)
        # Even a change beyond the stored 600-character excerpt is detected.
        self.assertEqual(excerpt, summary)
        updated = save_description(initial, excerpt, new_digest, refresh=True)
        self.assertEqual(updated.revision, 3)
        self.assertNotEqual(updated.content_hash, initial.content_hash)
        self.assertEqual(
            updated.metadata["description_detail"]["listing_hash"], self.entry.digest()
        )
        with patch(
            "ingestion.service.fetch_entries", return_value=([self.entry], 0, self.response, "hash")
        ):
            run = ingest_source(self.source, None)
        self.assertEqual(run.unchanged, 1)

    def test_refresh_preview_and_failed_fetch_never_mutate(self):
        summary, digest = extract(self.item, self.response)
        initial = save_description(self.item, summary, digest)
        changed = Response(
            200, self.html.replace(self.prose, "Nowy opis. " * 20).encode(), {}, self.url
        )
        with patch(
            "ingestion.management.commands.fetch_description.Client.get", return_value=changed
        ):
            call_command("fetch_description", self.item.pk, refresh=True, stdout=StringIO())
        self.item.refresh_from_db()
        self.assertEqual(self.item.content_hash, initial.content_hash)
        with patch(
            "ingestion.management.commands.fetch_description.Client.get",
            side_effect=FetchError("HTTP401", 401),
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "fetch_description", self.item.pk, refresh=True, apply=True, stdout=StringIO()
                )
        self.item.refresh_from_db()
        self.assertEqual(self.item.revision, 2)
        self.assertEqual(self.item.content_hash, initial.content_hash)

    def test_refresh_cannot_overwrite_list_description_or_stale_item(self):
        with patch("ingestion.management.commands.fetch_description.Client.get") as get:
            with self.assertRaises(CommandError):
                call_command(
                    "fetch_description", self.item.pk, refresh=True, apply=True, stdout=StringIO()
                )
            get.assert_not_called()
        summary, digest = extract(self.item, self.response)
        initial = save_description(self.item, summary, digest)
        Item.objects.filter(pk=initial.pk).update(content_hash="changed")
        with self.assertRaises(FetchError):
            save_description(initial, summary, digest, refresh=True)

    def test_profiles_require_same_reviewed_institution(self):
        for institution in ("gugik", "gios"):
            self.source.url = f"https://www.gov.pl/web/{institution}/aktualnosci"
            self.item.url = f"https://www.gov.pl/web/{institution}/dokument"
            self.assertTrue(eligible(self.item))
            self.item.url = "https://www.gov.pl/web/klimat/dokument"
            self.assertFalse(eligible(self.item))
        self.source.url = "https://www.gov.pl/web/gios/aktualnosci"
        self.item.url = "https://www.gov.pl/web/gugik/dokument"
        self.assertFalse(eligible(self.item))

    def test_gios_provenance_and_short_placeholder_rejected(self):
        self.source.url = "https://www.gov.pl/web/gios/aktualnosci"
        self.source.save()
        self.item.url = "https://www.gov.pl/web/gios/dokument"
        self.item.save()
        self.response.url = self.item.url
        summary, digest = extract(self.item, self.response)
        updated = save_description(self.item, summary, digest)
        self.assertEqual(updated.metadata["description_detail"]["profile"], "gios-article-v1")
        with self.assertRaises(FetchError):
            extract(
                self.item,
                Response(200, self.html.replace(self.prose, " - ").encode(), {}, self.item.url),
            )

    def setUp(self):
        self.source = Source.objects.create(
            slug="gugik",
            name="GUGiK",
            adapter="html",
            url="https://www.gov.pl/web/gugik/wiadomosci",
            enabled=True,
            activation_approved=True,
        )
        self.url = "https://www.gov.pl/web/gugik/test"
        self.entry = safe_entry(Entry(self.url, "Nowe dane GIS", self.url), self.url)
        self.item = Item.objects.create(
            source=self.source,
            external_id=self.entry.key,
            title=self.entry.title,
            url=self.url,
            content_hash=self.entry.digest(),
            metadata=self.entry.metadata,
        )
        self.prose = "Źródłowy opis danych przestrzennych. " * 30
        self.html = (
            '<article id="main-content"><h2>Nowe dane GIS</h2>'
            f'<div class="editor-content">{self.prose}</div>'
            '<div class="metrics">Nie pobieraj metryki</div></article>'
        )
        self.response = Response(200, self.html.encode(), {}, self.url)

    def test_preview_apply_repeat_preserve_history(self):
        with patch(
            "ingestion.management.commands.fetch_description.Client.get", return_value=self.response
        ) as get:
            call_command("fetch_description", self.item.pk, stdout=StringIO())
            self.item.refresh_from_db()
            self.assertEqual(self.item.summary, "")
            self.assertEqual(self.item.versions.count(), 0)
            call_command("fetch_description", self.item.pk, apply=True, stdout=StringIO())
            self.item.refresh_from_db()
            self.assertEqual(len(self.item.summary), 600)
            self.assertEqual(self.item.versions.count(), 2)
            self.assertEqual(self.item.versions.get(number=1).snapshot["summary"], "")
            self.assertEqual(self.item.metadata["description_detail"]["url"], self.url)
            call_command("fetch_description", self.item.pk, apply=True, stdout=StringIO())
            self.assertEqual(get.call_count, 2)
            self.assertContains(self.client.get(f"/items/{self.item.pk}/"), "Fragment podstrony")

    def test_reject_identity_structure_and_short_text(self):
        variants = [
            self.html.replace("Nowe dane GIS", "Inny tytuł"),
            self.html.replace('id="main-content"', 'id="other"'),
            self.html.replace(self.prose, "Krótki opis"),
            self.html + self.html,
        ]
        for html in variants:
            with self.subTest(html=html[:50]), self.assertRaises(FetchError):
                extract(self.item, Response(200, html.encode(), {}, self.url))
        with self.assertRaises(FetchError):
            extract(self.item, Response(200, self.html.encode(), {}, self.source.url))
        self.source.enabled = False
        with self.assertRaises(FetchError):
            extract(self.item, self.response)

    def test_concurrent_change_rejected_without_revision(self):
        summary, digest = extract(self.item, self.response)
        Item.objects.filter(pk=self.item.pk).update(content_hash="changed")
        with self.assertRaises(FetchError):
            save_description(self.item, summary, digest)
        self.assertEqual(self.item.versions.count(), 0)

    def test_normal_ingestion_preserves_until_listing_changes(self):
        summary, digest = extract(self.item, self.response)
        updated = save_description(self.item, summary, digest)
        with patch(
            "ingestion.service.fetch_entries",
            return_value=([self.entry], 0, self.response, "responsehash"),
        ):
            run = ingest_source(self.source, None)
        self.assertEqual(run.unchanged, 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.content_hash, updated.content_hash)
        self.assertEqual(self.item.revision, 2)
        self.entry.title = "Zmieniony dokument"
        with patch(
            "ingestion.service.fetch_entries",
            return_value=([self.entry], 0, self.response, "newresponse"),
        ):
            run = ingest_source(self.source, None)
        self.assertEqual(run.updated, 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.summary, "")
        self.assertNotIn("description_detail", self.item.metadata)
        self.assertTrue(self.item.versions.filter(origin="detail_excerpt").exists())
