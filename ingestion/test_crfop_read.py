import hashlib
import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from ingestion import crfop
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.test_crfop import FIXTURE
from ingestion.transport import FetchError


class CrfopReadTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="synthetic", sha256="1" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=15, release=release, master={}, technical={}
        )
        spec = crfop.profile("parki-narodowe")
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug=spec["slug"],
            url=spec["url"],
            config=spec["config"],
            kind="table",
            region="PL",
            attribution=crfop.ATTRIBUTION,
            access_approved=True,
        )
        table = crfop.parse_register(crfop.REGISTERS["parki-narodowe"], FIXTURE.read_bytes())
        self.data = {
            "schema": "crfop-register-v1",
            "register": "parki-narodowe",
            "part": 1,
            "parts": 1,
            "as_of": crfop.AS_OF,
            "license": "CC0 1.0",
            "attribution": crfop.ATTRIBUTION,
            "resource_id": 1187255,
            "url": crfop.REGISTERS["parki-narodowe"]["link"],
            "sha256": crfop.REGISTERS["parki-narodowe"]["sha256"],
            "units": {
                c: {"unit": None, "unit_status": "UNCONFIRMED"}
                for c in table["form_columns"]
                if c in crfop.AREA_COLUMNS
            },
            "source_flags_note": "Flagi t/f (np. archiwalny) zachowane bez interpretacji.",
            "forms_total_in_register": 23,
            **table,
        }

    def copy(self, data=None):
        data = self.data if data is None else data
        summary = {
            "kind": "crfop",
            "collector": "crfop",
            "register": data["register"],
            "part": data["part"],
            "parts": data["parts"],
            "as_of": crfop.AS_OF,
            "scope": "PL",
            "records": len(data["forms"]),
            "forms_total_in_register": data["forms_total_in_register"],
            "legal_acts": sum(len(f["legal_acts"]) for f in data["forms"]),
            "units_unconfirmed": sorted(data["units"]),
            "partial": data["parts"] > 1,
        }
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        return DataSnapshot.objects.create(
            channel=self.channel,
            payload=payload,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type="application/json",
            summary=summary,
            expires_at=timezone.now() + timedelta(days=30),
        )

    def read(self, pk=None):
        from ingestion.crfop_read import read_copy

        return read_copy(self.channel, pk, timezone.now())

    def test_real_fixture_read_keeps_original_text_and_unknown_units(self):
        snapshot = self.copy()
        result = self.read(snapshot.pk)
        self.assertEqual(result["data"], self.data)
        self.assertEqual(result["snapshot"].sha256, snapshot.sha256)
        self.assertEqual(result["data"]["units"]["powierzchnia"]["unit"], None)

    def test_no_or_expired_or_foreign_copy_is_not_empty_complete_register(self):
        self.assertIsNone(self.read())
        snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(expires_at=timezone.now())
        self.assertIsNone(self.read(snapshot.pk))
        self.assertIsNone(self.read(snapshot.pk + 100))

    def test_hard_retention_uses_fetch_time_not_register_as_of(self):
        snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(
            fetched_at=timezone.now() - timedelta(days=31),
            expires_at=timezone.now() + timedelta(days=2),
        )
        self.assertIsNone(self.read(snapshot.pk))

    def test_size_and_sha_and_wrong_media_are_refused(self):
        snapshot = self.copy()
        for change in ({"size_bytes": 1}, {"sha256": "0" * 64}, {"media_type": "text/html"}):
            with self.subTest(change=change):
                DataSnapshot.objects.filter(pk=snapshot.pk).update(**change)
                with self.assertRaises(FetchError):
                    self.read(snapshot.pk)
                DataSnapshot.objects.filter(pk=snapshot.pk).update(
                    size_bytes=snapshot.size_bytes,
                    sha256=snapshot.sha256,
                    media_type=snapshot.media_type,
                )
        with override_settings(DATA_AUTO_MAX_BYTES=100):
            with self.assertRaises(FetchError):
                self.read(snapshot.pk)

    def test_profile_and_approval_drift_are_refused_before_copy(self):
        self.copy()
        for attribute, value in (
            ("access_approved", False),
            ("url", "https://example.org/"),
            ("region", "EU"),
            ("slug", "other"),
            ("kind", "file"),
        ):
            original = getattr(self.channel, attribute)
            setattr(self.channel, attribute, value)
            with self.subTest(attribute=attribute), self.assertRaises(FetchError):
                self.read()
            setattr(self.channel, attribute, original)

    def test_whole_copy_schema_provenance_and_hidden_rows_are_checked(self):
        for mutate in (
            lambda d: d.update(license="CC BY 4.0"),
            lambda d: d.update(as_of="2025-01-01"),
            lambda d: d.update(resource_id=1),
            lambda d: d.update(sha256="0" * 64),
            lambda d: d.update(forms_total_in_register=24),
            lambda d: d["units"]["powierzchnia"].update(unit="ha"),
            lambda d: d["forms"][-1].update(id=d["forms"][0]["id"]),
            lambda d: d["forms"][-1]["values"].append("unexpected"),
            lambda d: d["forms"][-1]["legal_acts"].append(["wrong"]),
        ):
            changed = json.loads(json.dumps(self.data))
            mutate(changed)
            snapshot = self.copy(changed)
            with self.subTest(snapshot=snapshot.pk), self.assertRaises(FetchError):
                self.read(snapshot.pk)

    def test_original_long_boundary_text_is_bounded_by_copy_not_excel_cell_guess(self):
        index = self.data["form_columns"].index("opis_granicy")
        self.data["forms"][0]["values"][index] = "a" * 36474
        snapshot = self.copy()
        self.assertEqual(self.read(snapshot.pk)["data"], self.data)

    def test_original_natura_column_names_may_contain_digits(self):
        self.data["form_columns"].append("kod_n2k")
        for row in self.data["forms"]:
            row["values"].append("PLH001234")
        snapshot = self.copy()
        self.assertEqual(self.read(snapshot.pk)["data"], self.data)

    def test_noncanonical_duplicate_and_nonfinite_json_with_valid_hash_refused(self):
        snapshot = self.copy()
        original = bytes(snapshot.payload)
        for payload in (
            b" " + original,
            original.replace(b'"part": 1', b'"part": 2, "part": 1', 1),
            original.replace(b'"part": 1', b'"part": NaN', 1),
            b"[]",
            b"null",
            b"{",
        ):
            with self.subTest(prefix=payload[:40]):
                DataSnapshot.objects.filter(pk=snapshot.pk).update(
                    payload=payload,
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
                with self.assertRaises(FetchError):
                    self.read(snapshot.pk)

    def test_summary_drift_and_boolean_integers_refused(self):
        snapshot = self.copy()
        for field, value in (
            ("records", 24),
            ("legal_acts", 200),
            ("part", True),
            ("partial", 0),
            ("scope", "EU"),
        ):
            summary = {**snapshot.summary, field: value}
            DataSnapshot.objects.filter(pk=snapshot.pk).update(summary=summary)
            with self.subTest(field=field), self.assertRaises(FetchError):
                self.read(snapshot.pk)

    def test_oversize_blob_is_refused_before_materializing_payload(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        snapshot = self.copy()
        with (
            override_settings(DATA_AUTO_MAX_BYTES=100),
            CaptureQueriesContext(connection) as queries,
        ):
            with self.assertRaises(FetchError):
                self.read(snapshot.pk)
        self.assertEqual(len(queries), 1)
        self.assertIn("OCTET_LENGTH", queries[0]["sql"])
        # One reference belongs to OCTET_LENGTH; a second would materialize the BLOB.
        self.assertEqual(queries[0]["sql"].count('"ingestion_datasnapshot"."payload"'), 1)
