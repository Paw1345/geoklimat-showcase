import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings

from ingestion.legislation import sync_item
from ingestion.local_ai import (
    FAILURES,
    PROMPT_VERSION,
    current,
    enqueue,
    failure_message,
    material,
    request_model,
    review,
    run_one,
    validate_output,
)
from ingestion.models import AIDraft, Item, Source, ThreadImpact


@override_settings(LOCAL_AI_ENABLED=True)
class LocalAITests(TestCase):
    def setUp(self):
        source = Source.objects.create(
            slug="ai", name="AI", adapter="eli", url="https://eli.gov.pl"
        )
        self.item = Item.objects.create(
            source=source,
            external_id="a",
            title="Akt <script>x</script>",
            summary="Projekt dotyczy zasad udostępniania danych przestrzennych "
            "przez administrację. "
            "W opisie nie podano daty wejścia przepisów w życie.",
            url="https://eli.gov.pl/eli/DU/2026/1/ogl",
            content_hash="a" * 64,
            metadata={"ELI": "DU/2026/1"},
        )
        self.thread = sync_item(self.item).change_thread
        self.editor = get_user_model().objects.create_user(username="editor")
        self.editor.user_permissions.add(Permission.objects.get(codename="edit_change_threads"))
        self.reader = get_user_model().objects.create_user(username="reader")
        self.output = {
            "summary": "Proponowane zasady udostępniania danych.",
            "change": "Projekt zmienia zasady udostępniania danych.",
            "audience": "Pracownicy administracji publicznej.",
            "kinds": ["technical"],
            "quote": "Projekt dotyczy zasad udostępniania danych przestrzennych "
            "przez administrację.",
        }

    def draft(self):
        return enqueue(self.thread, self.item, self.editor)

    def ready(self):
        draft = self.draft()
        with patch("ingestion.local_ai.request_model", return_value=self.output):
            self.assertEqual(run_one(), "ready")
        draft.refresh_from_db()
        return draft

    def fields(self):
        return {
            "change": self.output["change"],
            "audience": self.output["audience"],
            "kinds": self.output["kinds"],
            "evidence": self.output["quote"],
            "evidence_url": self.item.url,
            "expected_hash": self.item.content_hash,
            "previous_id": 0,
        }

    def test_disabled_and_permissions(self):
        with override_settings(LOCAL_AI_ENABLED=False), self.assertRaises(ValidationError):
            self.draft()
        with self.assertRaises(PermissionDenied):
            enqueue(self.thread, self.item, self.reader)
        self.assertEqual(AIDraft.objects.count(), 0)

    def test_material_scope_and_bound(self):
        self.item.summary = "x" * 6000
        data = material(self.item)
        self.assertEqual(len(data["summary"]), 5000)
        self.assertTrue(data["truncated"])
        self.assertEqual(set(data), {"title", "summary", "truncated"})
        self.item.summary = "short"
        with self.assertRaises(ValidationError):
            material(self.item)

    def test_enqueue_idempotent_and_unchanged_ready_reused(self):
        draft = self.ready()
        self.assertEqual(self.draft().pk, draft.pk)
        self.assertFalse(ThreadImpact.objects.exists())

    def test_validation_rejects_invented_quote_extra_fields_and_kinds(self):
        for output in [
            {**self.output, "quote": "Nieistniejący dowód w dokumencie źródłowym."},
            {**self.output, "tools": ["delete"]},
            {**self.output, "kinds": ["invalid"]},
            {**self.output, "summary": ""},
            {**self.output, "audience": 123},
        ]:
            with self.assertRaises(ValueError):
                validate_output(output, material(self.item))

    def test_failure_never_publishes_and_can_retry(self):
        draft = self.draft()
        with patch("ingestion.local_ai.request_model", side_effect=OSError("private-path")):
            self.assertEqual(run_one(), "failed")
        draft.refresh_from_db()
        self.assertNotIn("private-path", draft.error)
        self.assertFalse(ThreadImpact.objects.exists())
        self.assertNotEqual(self.draft().pk, draft.pk)

    def test_safe_failure_reasons_do_not_expose_exception_details(self):
        maximum = AIDraft._meta.get_field("error").max_length
        for code in FAILURES:
            self.assertLessEqual(len(failure_message(ValueError(code))), maximum)
        for exc in (
            ValueError("private-secret"),
            KeyError("private-secret"),
            TypeError("private-secret"),
            OSError("private-secret"),
            json.JSONDecodeError("private-secret", "private-secret", 0),
        ):
            with self.subTest(exception=type(exc).__name__):
                self.assertNotIn("private-secret", failure_message(exc))
                self.assertLessEqual(len(failure_message(exc)), maximum)
        self.assertIn("połączyć", failure_message(OSError("private-secret")))
        self.assertIn("nie zakończył", failure_message(ValueError("incomplete")))

    def test_unsupported_number_reason_is_saved_and_visible_only_to_editor(self):
        draft = self.draft()
        output = {**self.output, "change": "Nowy termin to 2099 rok."}
        with patch("ingestion.local_ai.request_model", return_value=output):
            self.assertEqual(run_one(), "failed")
        draft.refresh_from_db()
        self.assertIn("zapisu liczbowego", draft.error)
        self.assertEqual(draft.output, {})
        self.assertFalse(ThreadImpact.objects.exists())
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(f"/ai/{draft.pk}/").status_code, 403)
        self.client.force_login(self.editor)
        self.assertContains(self.client.get(f"/ai/{draft.pk}/"), "zapisu liczbowego")

    def test_unsupported_quote_has_distinct_reason(self):
        draft = self.draft()
        output = {**self.output, "quote": "Nieobecny w źródle fragment cytatu."}
        with patch("ingestion.local_ai.request_model", return_value=output):
            self.assertEqual(run_one(), "failed")
        draft.refresh_from_db()
        self.assertIn("dosłownym fragmentem", draft.error)
        self.assertFalse(ThreadImpact.objects.exists())

    def test_stale_before_generation_makes_no_request(self):
        self.draft()
        self.item.summary += " Korekta."
        self.item.save()
        with patch("ingestion.local_ai.request_model") as mocked:
            self.assertEqual(run_one(), "failed")
            mocked.assert_not_called()

    def test_stale_during_generation_blocks_output(self):
        draft = self.draft()

        def changed(data):
            Item.objects.filter(pk=self.item.pk).update(content_hash="b" * 64)
            return self.output

        with patch("ingestion.local_ai.request_model", side_effect=changed):
            self.assertEqual(run_one(), "failed")
        draft.refresh_from_db()
        self.assertFalse(current(draft))
        self.assertIn("podczas analizy", draft.error)

    def test_review_explicit_atomic_and_idempotent(self):
        draft = self.ready()
        result = review(draft, self.editor, "approve", self.fields())
        self.assertEqual(result.state, "approved")
        self.assertEqual(result.impact.author, self.editor)
        review(draft, self.editor, "approve", self.fields())
        self.assertEqual(ThreadImpact.objects.count(), 1)
        self.assertContains(self.client.get(f"/changes/{self.thread.pk}/"), "wsparciu lokalnego AI")

    def test_stale_review_and_rejection(self):
        draft = self.ready()
        Item.objects.filter(pk=self.item.pk).update(content_hash="c" * 64)
        with self.assertRaises(ValidationError):
            review(draft, self.editor, "approve", self.fields())
        self.assertEqual(review(draft, self.editor, "reject").state, "rejected")
        self.assertFalse(ThreadImpact.objects.exists())

    def test_conflicting_editorial_revision_preserves_draft(self):
        draft = self.ready()
        fields = self.fields()
        fields["previous_id"] = 999
        with self.assertRaises(ValidationError):
            review(draft, self.editor, "approve", fields)
        draft.refresh_from_db()
        self.assertEqual(draft.state, "ready")

    def test_private_views_and_csrf_no_get_mutation(self):
        self.assertEqual(self.client.get("/ai/").status_code, 302)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get("/ai/").status_code, 403)
        self.client.force_login(self.editor)
        url = f"/changes/{self.thread.pk}/ai/{self.item.pk}/"
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.editor)
        self.assertEqual(client.post(url).status_code, 403)
        self.assertEqual(self.client.get("/ai/").status_code, 200)
        self.assertFalse(AIDraft.objects.exists())
        self.assertEqual(self.client.post(url).status_code, 302)
        draft = AIDraft.objects.get()
        self.assertContains(self.client.get(f"/ai/{draft.pk}/"), "&lt;script&gt;")
        self.assertFalse(ThreadImpact.objects.exists())

    def test_actual_review_form(self):
        draft = self.ready()
        self.client.force_login(self.editor)
        response = self.client.post(f"/ai/{draft.pk}/", {"action": "approve", **self.fields()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ThreadImpact.objects.count(), 1)

    def test_transport_fixed_local_bounded_no_tools(self):
        client = MagicMock()
        response = client.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(
            {
                "model": "qwen3:4b",
                "done": True,
                "done_reason": "stop",
                "message": {"content": json.dumps(self.output)},
            }
        ).encode()
        with patch("ingestion.local_ai.http.client.HTTPConnection", return_value=client) as conn:
            self.assertEqual(request_model(material(self.item)), self.output)
            conn.assert_called_once_with("ollama", 11434, timeout=240)
        args, kwargs = client.request.call_args
        self.assertEqual(args, ("POST", "/api/chat"))
        body = json.loads(kwargs["body"])
        self.assertNotIn("tools", body)
        self.assertFalse(body["think"])
        for quote in body["format"]["properties"]["quote"]["enum"]:
            self.assertIn(quote, self.item.summary)
        client.close.assert_called_once()

    def test_transport_rejects_redirect_incomplete_and_cloud(self):
        for status, result in [
            (302, {}),
            (200, {"done": False}),
            (200, {"done": True, "done_reason": "stop", "model": "cloud", "message": {}}),
        ]:
            client = MagicMock()
            client.getresponse.return_value.status = status
            client.getresponse.return_value.read.return_value = json.dumps(result).encode()
            with patch("ingestion.local_ai.http.client.HTTPConnection", return_value=client):
                with self.assertRaises(ValueError):
                    request_model(material(self.item))

    def test_interrupted_job_marked_failed_without_repeat(self):
        draft = self.draft()
        draft.state = "running"
        draft.save()
        with patch("ingestion.local_ai.request_model") as mocked:
            self.assertEqual(run_one(), "empty")
            mocked.assert_not_called()
        draft.refresh_from_db()
        self.assertEqual(draft.state, "failed")

    def test_revoked_editor_does_not_run_pending_job(self):
        self.draft()
        self.editor.user_permissions.clear()
        with patch("ingestion.local_ai.request_model") as mocked:
            self.assertEqual(run_one(), "failed")
            mocked.assert_not_called()

    def test_daily_limit(self):
        for _ in range(10):
            draft = self.draft()
            draft.state = "failed"
            draft.save()
        with self.assertRaises(ValidationError):
            self.draft()

    def test_empty_draft_kinds_allowed_but_not_publishable(self):
        self.output["kinds"] = []
        draft = self.ready()
        self.client.force_login(self.editor)
        self.assertContains(self.client.get(f"/ai/{draft.pk}/"), "Nie wybieraj kategorii")
        response = self.client.post(f"/ai/{draft.pk}/", {"action": "approve", **self.fields()})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ThreadImpact.objects.exists())
        draft.refresh_from_db()
        self.assertEqual(draft.state, "ready")

    def test_old_ready_version_not_reused_or_relabelled(self):
        draft = self.ready()
        draft.prompt_version = "geoklimat-local-v1"
        draft.save()
        new = self.draft()
        self.assertNotEqual(new.pk, draft.pk)
        self.assertEqual(new.prompt_version, PROMPT_VERSION)
        draft.refresh_from_db()
        self.assertEqual(draft.prompt_version, "geoklimat-local-v1")

    def test_pending_job_records_actual_prompt_version(self):
        draft = self.draft()
        draft.prompt_version = "geoklimat-local-v1"
        draft.save()
        self.assertEqual(self.draft().pk, draft.pk)
        with patch("ingestion.local_ai.request_model", return_value=self.output):
            self.assertEqual(run_one(), "ready")
        draft.refresh_from_db()
        self.assertEqual(draft.prompt_version, PROMPT_VERSION)

    def test_numeric_guard_rejects_pilot_date_and_compound_mutation(self):
        data = {"title": "INSPIRE", "summary": "Training available in May 2024, version 3.0."}
        base = {**self.output, "quote": data["summary"]}
        for field in ("summary", "change", "audience"):
            for value in ("Training in May 2:2024.", "Termin: 2025 rok.", "Wersja to 3.1."):
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaisesRegex(ValueError, "unsupported_number"),
                ):
                    validate_output({**base, field: value}, data)
        self.assertEqual(
            validate_output({**base, "summary": "Szkolenie w 2024, wersja 3.0."}, data)["summary"],
            "Szkolenie w 2024, wersja 3.0.",
        )

    def test_legacy_ready_invalid_number_cannot_be_reused_or_approved(self):
        draft = self.ready()
        draft.output["summary"] = "Nowa data zmiany: 2099."
        draft.save()
        self.assertNotEqual(self.draft().pk, draft.pk)
        with self.assertRaises(ValidationError):
            review(draft, self.editor, "approve", self.fields())
        self.assertFalse(ThreadImpact.objects.exists())
        self.assertEqual(review(draft, self.editor, "reject").state, "rejected")
