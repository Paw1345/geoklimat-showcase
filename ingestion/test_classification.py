from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from ingestion.admin import ClassificationForm
from ingestion.classification import classify, classify_and_save
from ingestion.classification_examples import EXAMPLES
from ingestion.models import Classification, Item, Source


class ClassificationTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(slug="news", name="News", adapter="html")

    def item(self, title="Aktualizacja WMS", **kwargs):
        return Item.objects.create(
            source=self.source,
            title=title,
            external_id=str(Item.objects.count()),
            url="https://www.gov.pl/test",
            **kwargs,
        )

    def test_regression_examples(self):
        for title, state, topic in EXAMPLES:
            with self.subTest(title=title):
                result = classify(Item(source=self.source, title=title, summary=""))
                self.assertEqual(result["auto_state"], state)
                if topic:
                    self.assertIn(topic, result["topics"])

    def test_summary_and_title_weights(self):
        title = classify(Item(source=self.source, title="WMS", summary=""))
        summary = classify(Item(source=self.source, title="Informacja", summary="WMS"))
        self.assertGreater(title["score"], summary["score"])
        self.assertEqual(summary["evidence"][0]["field"], "summary")

    def test_pilot_water_and_decarbonisation_with_negative_examples(self):
        for text, topic in [
            ("Ocena jakości wód", "water"),
            ("Industrial heat decarbonisation auction", "climate"),
            ("Industrial decarbonization", "climate"),
        ]:
            with self.subTest(text=text):
                result = classify(Item(source=self.source, title=text, summary=""))
                self.assertIn(topic, result["topics"])
        for text in ("Zawód geodety", "Dowód odbioru", "Wódka i napoje", "Carbon copy"):
            result = classify(Item(source=self.source, title=text, summary=""))
            self.assertNotIn("water", result["topics"])
            self.assertNotIn("climate", result["topics"])

    def test_pilot_new_services_kind(self):
        for title in ("Nowe API GIOŚ już dostępne", "Nowe usługi OGC API Features"):
            self.assertEqual(
                classify(Item(source=self.source, title=title, summary=""))["kind"], "Zmiana usługi"
            )
        self.assertEqual(
            classify(Item(source=self.source, title="Nowe budynki urzędu", summary=""))["kind"],
            "Komunikat",
        )

    def test_idempotent_and_preserves_review(self):
        item = self.item()
        self.assertTrue(classify_and_save(item))
        row = Classification.objects.get(item=item)
        row.manual_state, row.review_note = "excluded", "Nie dotyczy pracy urzędu"
        row.save()
        self.assertFalse(classify_and_save(item))
        item.title = "Klimat"
        item.save()
        self.assertTrue(classify_and_save(item))
        row.refresh_from_db()
        self.assertEqual(row.manual_state, "excluded")
        self.assertEqual(row.topics, ["climate", "climate_adaptation"])

    def test_environment_institution_is_not_nature_evidence(self):
        for title in (
            "Główny Inspektor Ochrony Środowiska patronem targów",
            "Konsultacje ustawy o ochronie środowiska",
        ):
            with self.subTest(title=title):
                result = classify(Item(source=self.source, title=title, summary=""))
                self.assertNotIn("nature", result["topics"])
                self.assertNotIn("nature_biodiversity", result["topics"])
                self.assertEqual(result["auto_state"], "review")

    def test_nature_specific_english_and_polish_evidence(self):
        for title in (
            "New Red List shows 1 in 9 moth species could face extinction",
            "Protecting pollinators",
            "Endangered species monitoring",
            "Ochrona przyrody i Natura 2000",
        ):
            with self.subTest(title=title):
                result = classify(Item(source=self.source, title=title, summary=""))
                self.assertIn("nature", result["topics"])
                self.assertEqual(result["auto_state"], "relevant")
        result = classify(Item(source=self.source, title="Species of software bugs", summary=""))
        self.assertNotIn("nature", result["topics"])

    def test_report_publication_does_not_imply_reporting_duties(self):
        for title in (
            "New JRC Report on Green Deal data sharing",
            "Raport o bioróżnorodności",
            "Sprawozdanie z wykonania budżetu",
        ):
            with self.subTest(title=title):
                result = classify(Item(source=self.source, title=title, summary=""))
                self.assertEqual(result["kind"], "Raport")
                self.assertNotIn("reporting", result["topics"])
        for title in (
            "Reporting requirements for emissions",
            "Obowiązek raportowania emisji",
            "Termin składania sprawozdań",
            "Sprawozdawczość INSPIRE",
        ):
            with self.subTest(title=title):
                result = classify(Item(source=self.source, title=title, summary=""))
                self.assertIn("reporting", result["topics"])
        self.assertEqual(
            classify(Item(source=self.source, title="Sprawozdawczość INSPIRE", summary=""))["kind"],
            "Komunikat",
        )

    def test_backfill_dry_run_and_repeat(self):
        self.item()
        call_command("classify_items", stdout=StringIO())
        self.assertFalse(Classification.objects.exists())
        call_command("classify_items", apply=True, stdout=StringIO())
        timestamp = Classification.objects.get().classified_at
        call_command("classify_items", apply=True, stdout=StringIO())
        self.assertEqual(Classification.objects.get().classified_at, timestamp)

    def test_reporting_activity_is_not_a_report_publication(self):
        for title in (
            "Obowiązek raportowania emisji",
            "Raportowanie danych",
            "System raportowy",
            "Raportujący podmiot",
            "Reporting requirements",
        ):
            with self.subTest(title=title):
                result = classify(Item(source=self.source, title=title, summary=""))
                self.assertEqual(result["kind"], "Komunikat")

    def test_report_noun_inflections_still_match(self):
        for word in (
            "Raport",
            "raportu",
            "raportowi",
            "raportem",
            "raporcie",
            "raporty",
            "raportów",
            "raportom",
            "raportami",
            "raportach",
        ):
            with self.subTest(word=word):
                result = classify(Item(source=self.source, title=word, summary=""))
                self.assertEqual(result["kind"], "Raport")

    def test_data_sharing_and_specifications_require_context(self):
        for title in (
            "New JRC Report on Green Deal data sharing",
            "Methodology for data specifications",
            "Data specification update",
            "Zasady udostępniania danych",
            "Specyfikacje danych",
        ):
            with self.subTest(title=title):
                result = classify(Item(source=self.source, title=title, summary=""))
                self.assertIn("data", result["topics"])
                self.assertEqual(result["auto_state"], "review")
                self.assertNotIn("reporting", result["topics"])
        for title in ("Sharing experiences", "Product specifications", "Data spotkania"):
            self.assertNotIn(
                "data", classify(Item(source=self.source, title=title, summary=""))["topics"]
            )

    def test_scoped_reclassification_does_not_touch_other_items(self):
        a, b = self.item("Ocena wód"), self.item("Industrial decarbonisation")
        call_command("classify_items", item=[a.pk], stdout=StringIO())
        self.assertFalse(Classification.objects.exists())
        call_command("classify_items", item=[a.pk], apply=True, stdout=StringIO())
        self.assertTrue(Classification.objects.filter(item=a).exists())
        self.assertFalse(Classification.objects.filter(item=b).exists())

    def test_filters_manual_override_and_history(self):
        a, b = self.item(), self.item("Nabór na stanowisko inspektora GIOŚ")
        classify_and_save(a)
        classify_and_save(b)
        self.assertEqual(len(self.client.get("/?status=relevant&topic=gis").context["items"]), 1)
        self.assertEqual(len(self.client.get("/?status=excluded").context["items"]), 1)
        Classification.objects.filter(item=a).update(manual_state="excluded", review_note="Test")
        self.assertEqual(len(self.client.get("/?status=relevant").context["items"]), 0)
        self.assertContains(self.client.get(f"/items/{a.pk}/"), "Decyzja redakcyjna")
        self.assertEqual(len(self.client.get("/?topic=unknown").context["items"]), 0)

    def test_eli_is_not_automatically_relevant(self):
        self.source.adapter = "eli"
        result = classify(Item(source=self.source, title="Ustawa o podatkach", summary=""))
        self.assertEqual(result["auto_state"], "review")
        self.assertEqual(result["kind"], "Publikacja aktu")

    def test_admin_reason_required(self):
        item = self.item()
        classify_and_save(item)
        form = ClassificationForm(
            data={"manual_state": "excluded", "review_note": " "},
            instance=Classification.objects.get(),
        )
        self.assertFalse(form.is_valid())

    def test_admin_audit_and_permissions(self):
        item = self.item()
        classify_and_save(item)
        row = Classification.objects.get()
        url = f"/admin/ingestion/classification/{row.pk}/change/"
        self.assertEqual(self.client.post(url, {}).status_code, 302)
        user = get_user_model().objects.create_superuser(
            username="editor", email="e@example.org", password="test-only"
        )
        self.client.force_login(user)
        response = self.client.post(
            url, {"manual_state": "excluded", "review_note": "Test redakcyjny", "_save": "Save"}
        )
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.reviewed_by, "editor")
        self.assertEqual(row.manual_state, "excluded")

    def test_pagination_and_legal_filter(self):
        self.source.adapter = "eli"
        self.source.save()
        for number in range(31):
            classify_and_save(self.item(f"WMS {number}"))
        page = self.client.get("/?topic=gis&page=2")
        self.assertEqual(len(page.context["items"]), 1)
        self.assertContains(page, "topic=gis")
