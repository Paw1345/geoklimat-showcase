from django.test import SimpleTestCase

from ingestion.adapters import parse
from ingestion.models import Source
from ingestion.transport import FetchError, Response


class SnapshotContentTests(SimpleTestCase):
    def setUp(self):
        self.source = Source(
            adapter="watch",
            name="INSPIRE",
            url="https://www.gov.pl/web/gugik/inspire2",
            config={"content": "article"},
        )
        self.body = "Racjonalne korzystanie z przestrzeni i dane INSPIRE. " * 5

    def parse(self, html):
        return parse(self.source, Response(200, html.encode(), {}, self.source.url))[0][0]

    def test_government_article_ignores_navigation_and_metrics(self):
        def page(menu, metric):
            return (
                f"<main><aside>Menu zewnętrzne</aside><article><h2>INSPIRE</h2>"
                f'<div class="art-prev"><ul><li>{menu}</li></ul></div>'
                f'<div class="editor-content">{self.body}<a href="/law">Prawo</a></div>'
                f'<div class="metrics">{metric}</div></article></main>'
            )

        a, b = (
            self.parse(page("Sprawozdawczość", "Wersja 1")),
            self.parse(page("Raport", "Wersja 2")),
        )
        self.assertEqual(a.summary, self.body.strip() + " Prawo")
        self.assertEqual(a.metadata["document_hash"], b.metadata["document_hash"])
        self.assertNotIn("Sprawozdawczość", a.summary)

    def test_missing_or_ambiguous_configured_body_fails_closed(self):
        for html in (
            f"<main>{self.body}</main>",
            f"<article>{self.body}</article>",
            '<article><div class="editor-content">A</div>'
            '<div class="editor-content">B</div></article>',
            "<article>A</article><article>B</article>",
        ):
            with self.subTest(html=html), self.assertRaises(FetchError):
                self.parse(html)

    def test_configured_non_government_content_and_aria_navigation(self):
        self.source.url = "https://example.org/info"
        self.source.config = {"content": "#prose"}
        entry = self.parse(
            f'<main>Outside<div id="prose"><div role="navigation">Menu</div>'
            f"{self.body}</div></main>"
        )
        self.assertEqual(entry.summary, self.body.strip())

    def test_real_content_change_updates_hash(self):
        a = self.parse(f'<article><div class="editor-content">{self.body}</div></article>')
        b = self.parse(f'<article><div class="editor-content">{self.body}Zmiana.</div></article>')
        self.assertNotEqual(a.metadata["document_hash"], b.metadata["document_hash"])
