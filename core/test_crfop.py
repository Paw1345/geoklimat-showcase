import json
from unittest.mock import patch

from django.test import TestCase

from ingestion import crfop
from ingestion import test_crfop_read as fixtures
from ingestion.models import DataSnapshot


class CrfopSearchTests(TestCase):
    copy = fixtures.CrfopReadTests.copy

    def setUp(self):
        fixtures.CrfopReadTests.setUp(self)
        self.url = f"/data/{self.channel.pk}/crfop/"

    def test_public_search_is_read_only_and_keeps_historical_scope(self):
        snapshot = self.copy()
        before = list(DataSnapshot.objects.values())
        with patch("ingestion.transport.Client.get") as get:
            response = self.client.get(self.url)
            get.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026-02-04")
        self.assertContains(response, "bez geometrii")
        self.assertContains(response, "jednostka niepotwierdzona")
        self.assertContains(response, "bez interpretacji")
        self.assertContains(response, f"snapshot={snapshot.pk}")
        self.assertEqual(len(response.context["page"]), 23)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(before, list(DataSnapshot.objects.values()))

    def test_name_and_identifier_filters_combine_with_unicode_casefold(self):
        self.copy()
        response = self.client.get(self.url, {"name": "ŚWIĘTOKRZYSKI", "identifier": "2485"})
        self.assertEqual([r["id"] for r in response.context["page"]], [2485])
        response = self.client.get(self.url, {"identifier": "pl.zipop.1393.pn.6"})
        self.assertEqual([r["id"] for r in response.context["page"]], [2485])
        response = self.client.get(self.url, {"name": "Świętokrzyski", "identifier": "absent"})
        self.assertContains(response, "Brak dopasowań")

    def test_pagination_pins_snapshot_and_resets_form_page(self):
        row = self.data["forms"][0]
        self.data["forms"] = [{**row, "id": i} for i in range(1, 121)]
        self.data["forms_total_in_register"] = 120
        snapshot = self.copy()
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["page"]), 50)
        self.assertEqual(response.context["page"][0]["id"], 1)
        link = response.context["links"]["next"]
        self.assertIn(f"snapshot={snapshot.pk}", link)
        self.data["forms"] = [{**row, "id": 999}]
        self.data["forms_total_in_register"] = 1
        self.copy()
        response = self.client.get(self.url + link)
        self.assertEqual(response.context["copy"]["snapshot"].pk, snapshot.pk)
        self.assertEqual(response.context["page"][0]["id"], 51)
        self.assertNotContains(response, 'name="page"')

    def test_invalid_parameters_refuse_results(self):
        self.copy()
        for query in (
            "?page=0",
            "?page=401",
            "?page=x",
            "?page=2",
            "?snapshot=0",
            "?snapshot=" + "9" * 30,
            "?name=" + "a" * 101,
            "?identifier=" + "b" * 101,
            "?year=2027",
            "?name=a&name=b",
        ):
            with self.subTest(query=query):
                response = self.client.get(self.url + query)
                self.assertEqual(response.status_code, 400)
                self.assertIsNone(response.context["page"])

    def test_no_copy_and_missing_pinned_copy_do_not_claim_empty_register(self):
        self.assertContains(self.client.get(self.url), "Brak aktualnej kopii")
        self.copy()
        response = self.client.get(self.url, {"snapshot": 999999})
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.context["page"])

    def test_sha_or_hidden_row_damage_refuses_whole_page(self):
        snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(sha256="0" * 64)
        self.assertEqual(self.client.get(self.url).status_code, 409)
        self.data["forms"][-1]["values"].append("extra")
        self.copy()
        response = self.client.get(self.url, {"name": "Świętokrzyski"})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(response.context["page"])

    def test_profile_approval_other_channel_and_post_refused(self):
        self.copy()
        for field, value in (("access_approved", False), ("region", "EU"), ("kind", "file")):
            before = getattr(self.channel, field)
            setattr(self.channel, field, value)
            self.channel.save()
            self.assertEqual(self.client.get(self.url).status_code, 404)
            setattr(self.channel, field, before)
            self.channel.save()
        self.assertEqual(self.client.get("/data/999999/crfop/").status_code, 404)
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_blank_names_escaped_text_and_limited_acts(self):
        row = self.data["forms"][0]
        row["values"][3] = ""
        row["legal_acts"] = [["zmiana", "<script>bad()</script>", "", "", "", ""]] * 11
        self.copy()
        response = self.client.get(self.url)
        self.assertContains(response, "brak nazwy w źródle")
        self.assertContains(response, "&lt;script&gt;bad()&lt;/script&gt;", count=10)
        self.assertNotContains(response, "<script>bad()")
        self.assertContains(response, "Pominięte w podglądzie: 1")

    def test_ecological_search_is_explicitly_one_of_three_parts(self):
        spec = crfop.profile("uzytki-ekologiczne", 2)
        self.channel.slug, self.channel.config = spec["slug"], spec["config"]
        self.channel.save()
        self.data.update(
            register="uzytki-ekologiczne",
            part=2,
            parts=3,
            resource_id=crfop.REGISTERS["uzytki-ekologiczne"]["resource"],
            url=crfop.REGISTERS["uzytki-ekologiczne"]["link"],
            sha256=crfop.REGISTERS["uzytki-ekologiczne"]["sha256"],
            forms_total_in_register=69,
        )
        self.copy()
        response = self.client.get(self.url)
        self.assertContains(response, "Część 2 z 3")
        self.assertContains(response, "Nie wyszukuje pozostałych części")
        self.assertEqual(response.context["page"].paginator.count, 23)

    def test_search_link_from_channel_and_whole_copy_export(self):
        snapshot = self.copy()
        response = self.client.get(f"/data/{self.channel.pk}/")
        self.assertContains(response, self.url)
        response = self.client.get(self.url, {"name": "Świętokrzyski"})
        self.assertContains(response, f"/data/snapshots/{snapshot.pk}/download/")
        exported = self.client.get(f"/data/snapshots/{snapshot.pk}/download/")
        self.assertEqual(json.loads(exported.content), self.data)

    def test_act_preview_truncates_but_export_preserves_original(self):
        original = "prefix-" + "a" * 600 + "-unseen-tail"
        self.data["forms"][0]["legal_acts"] = [["zmiana", original, "", "", "", ""]]
        snapshot = self.copy()
        response = self.client.get(self.url)
        self.assertContains(response, "do 500 znaków")
        self.assertNotContains(response, "-unseen-tail")
        exported = json.loads(self.client.get(f"/data/snapshots/{snapshot.pk}/download/").content)
        self.assertEqual(exported["forms"][0]["legal_acts"][0][1], original)
