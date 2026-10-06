import io

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client, TestCase

from ingestion.changes import assess, decide_document, ensure_thread, thread_items, threads_for_item
from ingestion.legislation import sync_item
from ingestion.models import (
    ChangeThread,
    Item,
    LegalEvent,
    LegalLink,
    LegalProcess,
    LegalRelation,
    Source,
    ThreadDocument,
    ThreadImpact,
    ThreadWatch,
)


class ChangeTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug="changes",
            name="Test",
            adapter="eli",
            url="https://api.sejm.gov.pl/eli/acts/DU/2026",
        )
        self.item = Item.objects.create(
            source=self.source,
            external_id="one",
            title="Akt <script>alert(1)</script>",
            url="https://eli.gov.pl/eli/DU/2026/1/ogl",
            content_hash="a" * 64,
            metadata={"ELI": "DU/2026/1"},
        )
        self.process = sync_item(self.item)
        self.thread = ensure_thread(self.process)
        self.editor = get_user_model().objects.create_user(username="editor")
        self.editor.user_permissions.add(Permission.objects.get(codename="edit_change_threads"))
        self.reader = get_user_model().objects.create_user(username="reader")
        self.other = get_user_model().objects.create_user(username="other")
        self.fields = dict(
            change="Zmiana techniczna opisana w źródle.",
            audience="Administratorzy usług przestrzennych.",
            kinds=["technical"],
            evidence_url=self.item.url,
            evidence="Sekcja 2 dokumentu źródłowego.",
        )

    def impact(self, **overrides):
        args = dict(expected_hash=self.item.content_hash, previous_id=0, **self.fields)
        args.update(overrides)
        return assess(self.thread, self.item, self.editor, **args)

    def test_sync_same_identity_and_no_title_merging(self):
        sync_item(self.item)
        self.assertEqual(ChangeThread.objects.count(), 1)
        second = Item.objects.create(
            source=self.source,
            external_id="two",
            title=self.item.title,
            url="https://eli.gov.pl/eli/DU/2026/2/ogl",
            content_hash="b" * 64,
            metadata={"ELI": "DU/2026/2"},
        )
        sync_item(second)
        self.assertEqual(ChangeThread.objects.count(), 2)
        self.assertEqual(list(thread_items(self.thread)), [self.item])
        self.assertEqual(list(threads_for_item(self.item)), [self.thread])

    def test_one_hop_published_relation_and_stale_evidence(self):
        target = LegalProcess.objects.create(
            identifier="SEJM:10/1", title="Projekt", jurisdiction="PL"
        )
        ensure_thread(target)
        related = Item.objects.create(
            source=self.source,
            external_id="related",
            title="Projekt",
            url="https://example.org/project",
            content_hash="c" * 64,
        )
        link = LegalLink.objects.create(
            process=target, item=related, origin="listing", item_hash=related.content_hash
        )
        relation = LegalRelation.objects.create(
            link=link, target=self.process, kind="published_as", item_hash=related.content_hash
        )
        self.assertEqual(set(thread_items(self.thread)), {self.item, related})
        self.assertIn(self.thread, threads_for_item(related))
        relation.kind = "amends"
        relation.save()
        self.assertEqual(list(thread_items(self.thread)), [self.item])
        relation.kind = "published_as"
        relation.save()
        related.content_hash = "d" * 64
        related.save()
        self.assertEqual(list(thread_items(self.thread)), [self.item])
        self.assertNotIn(self.thread, threads_for_item(related))

    def test_exclusion_history_and_idempotence(self):
        for _ in range(2):
            decide_document(
                self.thread,
                self.item,
                self.editor,
                False,
                "Nie dotyczy tego wątku",
                self.item.content_hash,
            )
        self.assertEqual(ThreadDocument.objects.count(), 1)
        self.assertFalse(thread_items(self.thread).exists())
        self.assertFalse(threads_for_item(self.item).exists())
        decide_document(
            self.thread,
            self.item,
            self.editor,
            True,
            "Potwierdzone powiązanie",
            self.item.content_hash,
        )
        self.assertEqual(ThreadDocument.objects.count(), 2)
        self.assertEqual(ThreadDocument.objects.filter(superseded_at__isnull=True).count(), 1)
        self.assertEqual(list(thread_items(self.thread)), [self.item])

    def test_manual_link_goes_stale_and_is_not_replaced_automatically(self):
        decide_document(
            self.thread,
            self.item,
            self.editor,
            True,
            "Potwierdzone powiązanie",
            self.item.content_hash,
        )
        self.item.content_hash = "b" * 64
        self.item.save()
        sync_item(self.item)
        self.assertFalse(thread_items(self.thread).exists())
        self.assertFalse(threads_for_item(self.item).exists())
        with self.assertRaises(ValidationError):
            decide_document(
                self.thread, self.item, self.editor, True, "Potwierdzone powiązanie", "a" * 64
            )

    def test_impact_revisions_stale_form_and_repeat(self):
        first = self.impact()
        self.assertEqual(self.impact().pk, first.pk)
        with self.assertRaises(ValidationError):
            self.impact(change="Inna redakcyjna interpretacja.")
        second = self.impact(previous_id=first.pk, change="Inna redakcyjna interpretacja.")
        self.assertNotEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertIsNotNone(first.superseded_at)
        self.assertEqual(ThreadImpact.objects.count(), 2)

    def test_invalid_impact_and_unauthorized_write(self):
        for values in [
            {"evidence_url": "javascript:alert(1)"},
            {"kinds": ["invented"]},
            {"kinds": []},
            {"evidence": ""},
            {"expected_hash": "x" * 64},
        ]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.impact(**values)
        with self.assertRaises(PermissionDenied):
            assess(self.thread, self.item, self.reader, self.item.content_hash, 0, **self.fields)
        self.assertFalse(ThreadImpact.objects.exists())

    def test_stale_impact_not_displayed_as_current(self):
        self.impact()
        url = f"/changes/{self.thread.pk}/"
        self.assertTrue(self.client.get(url).context["impact_current"])
        self.item.content_hash = "b" * 64
        self.item.save()
        response = self.client.get(url)
        self.assertFalse(response.context["impact_current"])
        self.assertContains(response, "ponownej kontroli")
        self.assertEqual(ThreadImpact.objects.count(), 1)

    def test_removed_member_cannot_support_current_impact(self):
        self.impact()
        decide_document(
            self.thread,
            self.item,
            self.editor,
            False,
            "Błędne powiązanie dokumentu",
            self.item.content_hash,
        )
        self.assertFalse(self.client.get(f"/changes/{self.thread.pk}/").context["impact_current"])
        with self.assertRaises(ValidationError):
            self.impact()

    def test_public_views_are_read_only_escape_and_paginate(self):
        before = (
            ChangeThread.objects.count(),
            ThreadImpact.objects.count(),
            ThreadWatch.objects.count(),
        )
        for url in ["/changes/", f"/changes/{self.thread.pk}/", f"/items/{self.item.pk}/"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "<script>alert(1)</script>")
            self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(
            before,
            (
                ChangeThread.objects.count(),
                ThreadImpact.objects.count(),
                ThreadWatch.objects.count(),
            ),
        )
        self.assertEqual(self.client.get("/changes/999999/").status_code, 404)
        self.assertContains(self.client.get("/changes/?q=no-such-thread"), "Brak wątków")
        for index in range(32):
            ChangeThread.objects.create(title=f"Thread {index}")
        self.assertEqual(len(self.client.get("/changes/").context["threads"]), 30)

    def test_follow_unfollow_are_owner_scoped_and_idempotent(self):
        url = f"/changes/{self.thread.pk}/watch/"
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"action": "follow"}).status_code, 302)
        self.assertFalse(ThreadWatch.objects.exists())
        self.client.force_login(self.reader)
        for _ in range(2):
            self.assertEqual(
                self.client.post(url, {"action": "follow", "user": self.other.pk}).status_code, 302
            )
        self.assertEqual(ThreadWatch.objects.count(), 1)
        self.assertEqual(ThreadWatch.objects.get().user, self.reader)
        self.assertEqual(
            self.client.get("/changes/?watching=1").context["threads"].paginator.count, 1
        )
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get("/changes/?watching=1").context["threads"].paginator.count, 0
        )
        self.client.force_login(self.reader)
        self.client.post(url, {"action": "unfollow"})
        self.assertFalse(ThreadWatch.objects.get().active)
        self.assertEqual(self.client.post(url, {"action": "toggle"}).status_code, 400)

    def test_csrf_on_follow_and_edit(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.editor)
        for url in [
            f"/changes/{self.thread.pk}/watch/",
            "/changes/new/",
            f"/changes/{self.thread.pk}/documents/{self.item.pk}/",
        ]:
            self.assertEqual(client.post(url, {}).status_code, 403)

    def test_editor_forms_and_reader_denial(self):
        document_url = f"/changes/{self.thread.pk}/documents/{self.item.pk}/"
        impact_url = f"/changes/{self.thread.pk}/impact/{self.item.pk}/"
        self.client.force_login(self.reader)
        for url in ["/changes/new/", document_url, impact_url]:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.client.force_login(self.editor)
        self.assertEqual(self.client.get(impact_url).status_code, 200)
        response = self.client.post(
            impact_url, dict(expected_hash=self.item.content_hash, previous_id=0, **self.fields)
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ThreadImpact.objects.get().author, self.editor)
        self.assertEqual(
            self.client.post(
                document_url,
                {
                    "included": "no",
                    "reason": "Dokument błędnie przypisany",
                    "expected_hash": self.item.content_hash,
                },
            ).status_code,
            302,
        )
        self.assertFalse(thread_items(self.thread).exists())
        self.assertEqual(self.client.get(impact_url).status_code, 404)
        self.assertEqual(
            self.client.post("/changes/new/", {"title": "Nowy wątek"}).status_code, 302
        )

    def test_sync_dry_run_no_write_then_repeat(self):
        LegalProcess.objects.create(
            identifier="ELI:DU/2026/99", title="Reference", jurisdiction="PL"
        )
        before = ChangeThread.objects.count()
        call_command("sync_change_threads", stdout=io.StringIO())
        self.assertEqual(ChangeThread.objects.count(), before)
        call_command("sync_change_threads", apply=True, stdout=io.StringIO())
        call_command("sync_change_threads", apply=True, stdout=io.StringIO())
        self.assertEqual(ChangeThread.objects.count(), before + 1)

    def test_manual_thread_reuses_only_current_document_deadlines(self):
        from datetime import date

        manual = ChangeThread.objects.create(title="Wątek redakcyjny")
        decide_document(
            manual, self.item, self.editor, True, "Powiązanie potwierdzone", self.item.content_hash
        )
        link = self.item.legal_links.get()
        LegalEvent.objects.create(
            link=link,
            event_key="effective",
            kind="effective",
            date=date(2026, 10, 1),
            item_hash=self.item.content_hash,
            evidence_url=self.item.url,
            evidence="entryIntoForce",
        )
        response = self.client.get(f"/changes/{manual.pk}/")
        self.assertEqual(len(response.context["events"]), 1)
        self.item.content_hash = "b" * 64
        self.item.save()
        self.assertEqual(len(self.client.get(f"/changes/{manual.pk}/").context["events"]), 0)

    def test_relation_membership_does_not_walk_transitively(self):
        middle = LegalProcess.objects.create(
            identifier="SEJM:10/2", title="Middle", jurisdiction="PL"
        )
        far = LegalProcess.objects.create(identifier="SEJM:10/3", title="Far", jurisdiction="PL")
        far_thread = ensure_thread(far)
        middle_link = LegalLink.objects.create(
            process=middle, item=self.item, origin="editorial", item_hash=self.item.content_hash
        )
        far_item = Item.objects.create(
            source=self.source,
            external_id="far",
            title="Far",
            url="https://example.org/far",
            content_hash="f" * 64,
        )
        far_link = LegalLink.objects.create(
            process=far, item=far_item, origin="editorial", item_hash=far_item.content_hash
        )
        LegalRelation.objects.create(
            link=middle_link,
            target=self.process,
            kind="published_as",
            item_hash=self.item.content_hash,
        )
        LegalRelation.objects.create(
            link=far_link, target=middle, kind="published_as", item_hash=far_item.content_hash
        )
        self.assertNotIn(far_item, thread_items(self.thread))
        self.assertNotIn(self.thread, threads_for_item(far_item))
        self.assertIn(far_thread, threads_for_item(far_item))

    def test_login_safe_next_and_logout_requires_post(self):
        self.reader.set_password("test-only-fixture-password-1729")
        self.reader.save()
        response = self.client.post(
            "/accounts/login/",
            {
                "username": "reader",
                "password": "test-only-fixture-password-1729",
                "next": "https://outside.example/",
            },
        )
        self.assertRedirects(response, "/changes/")
        self.assertEqual(self.client.get("/accounts/logout/").status_code, 405)
        self.assertEqual(self.client.post("/accounts/logout/").status_code, 302)
