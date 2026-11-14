"""The optional local settings template must not activate normal installations."""

import os
import runpy
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from config.settings import development
from ingestion.collection_evidence import prune_attempts, read_attempts

TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "docs/examples/collection-evidence/collection_evidence_local.py"
)
OPT_IN = "GEOKLIMAT_COLLECTION_EVIDENCE_OPT_IN"


class LocalEvidenceSettingsTests(SimpleTestCase):
    def profile(self, value=None):
        with patch.dict(os.environ):
            os.environ.pop(OPT_IN, None)
            if value is not None:
                os.environ[OPT_IN] = value
            return runpy.run_path(str(TEMPLATE))

    def test_missing_opt_in_is_off(self):
        self.assertIs(self.profile()["COLLECTION_EVIDENCE_ENABLED"], False)

    def test_truthy_and_misspelled_values_remain_off(self):
        for value in ("", "off", "1", "true", "True", "yes", "IMGW-PILOT-C8", "imgw-pilot-c8 "):
            with self.subTest(value=value):
                self.assertIs(self.profile(value)["COLLECTION_EVIDENCE_ENABLED"], False)

    def test_exact_explicit_opt_in_is_boolean_true(self):
        self.assertIs(self.profile("imgw-pilot-c8")["COLLECTION_EVIDENCE_ENABLED"], True)

    def test_template_changes_only_the_evidence_setting(self):
        profile = self.profile("imgw-pilot-c8")
        inherited = {key: value for key, value in vars(development).items() if key.isupper()}
        expected = {**inherited, "COLLECTION_EVIDENCE_ENABLED": True}
        self.assertEqual({key: value for key, value in profile.items() if key.isupper()}, expected)

    def test_normal_development_stays_off_even_with_opt_in_environment(self):
        with patch.dict(os.environ, {OPT_IN: "imgw-pilot-c8"}):
            from config.settings import base

            self.assertIs(base.COLLECTION_EVIDENCE_ENABLED, False)
            self.assertIs(development.COLLECTION_EVIDENCE_ENABLED, False)

    def test_local_off_reader_and_retention_need_no_database(self):
        from django.test import override_settings

        for value in (None, "true", "off"):
            with (
                self.subTest(value=value),
                override_settings(
                    COLLECTION_EVIDENCE_ENABLED=self.profile(value)["COLLECTION_EVIDENCE_ENABLED"]
                ),
            ):
                self.assertEqual(
                    read_attempts(None, now=None), {"status": "disabled", "by_channel": {}}
                )
                self.assertEqual(
                    prune_attempts(apply=True),
                    {"status": "disabled", "deleted": 0, "applied": False},
                )
