import io
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from ingestion.copernicus_wms import MAX_TIME_VALUES, PROFILES, config, slug, time_extent
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries, run_batch
from ingestion.transport import FetchError, Response

EFFIS = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities xmlns="http://www.opengis.net/wms" version="1.3.0">
<Service><Name>WMS</Name><Title>EFFIS</Title><Fees>none</Fees>
<AccessConstraints>None</AccessConstraints></Service>
<Capability><Layer><Title>EFFIS</Title><CRS>EPSG:4326</CRS><CRS>EPSG:3857</CRS>
<Dimension name="time" units="ISO8601" default="2026-10-08">2026-10-01/2026-10-08/P1D</Dimension>
<Layer><Name>mf010.fwi</Name><Title>Fire Weather Index</Title></Layer>
<Layer><Name>modis.ba.2025</Name><Title>Burnt areas 2025</Title>
<Dimension name="time" units="ISO8601">2025-01-01,2025-06-01,2025-12-31</Dimension></Layer>
</Layer></Capability></WMS_Capabilities>"""

EDO = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMT_MS_Capabilities version="1.1.1">
<Service><Name>OGC:WMS</Name><Title>EDO WMS</Title><Fees></Fees></Service>
<Capability><Layer><Title>EDO</Title>
<Layer><Name>cdiad</Name><Title>Combined Drought Indicator</Title><SRS>EPSG:4326</SRS>
<Dimension name="time" units="ISO8601"/>
<Extent name="time" default="2026-09-21">2024-07-01,2024-07-11,2026-09-21</Extent></Layer>
<Layer><Name>lfinx_300_sms</Name><Title>Low-flow index</Title><SRS>EPSG:3035</SRS></Layer>
</Layer></Capability></WMT_MS_Capabilities>"""


def make_source(key, **extra):
    return Source.objects.create(
        slug=slug(key),
        name=PROFILES[key]["name"],
        adapter="ogc",
        url=PROFILES[key]["url"],
        config=config(key),
        **extra,
    )


class CopernicusTimeExtentTests(SimpleTestCase):
    def test_two_part_range_keeps_end_without_inventing_period(self):
        raw = "2018-01-01/2099-12-31"
        self.assertEqual(
            time_extent(raw),
            {
                "time_first": "2018-01-01",
                "time_last": "2099-12-31",
                "time_values": 1,
                "time_intervals": 1,
            },
        )
        self.assertEqual(raw, "2018-01-01/2099-12-31")

    def test_three_part_range_keeps_existing_summary(self):
        self.assertEqual(
            time_extent("2020-01-01/2020-12-31/P1M"),
            {
                "time_first": "2020-01-01",
                "time_last": "2020-12-31",
                "time_values": 1,
                "time_intervals": 1,
            },
        )

    def test_list_preserves_source_order_not_sorted_dates(self):
        self.assertEqual(
            time_extent("2025-12-31,2025-06-01,2025-01-01"),
            {
                "time_first": "2025-12-31",
                "time_last": "2025-01-01",
                "time_values": 3,
                "time_intervals": 0,
            },
        )

    def test_mixed_list_counts_both_range_shapes(self):
        self.assertEqual(
            time_extent("2020-01-01/2020-02-01/P1D,2023-01-01,2024-01-01/2024-12-31"),
            {
                "time_first": "2020-01-01",
                "time_last": "2024-12-31",
                "time_values": 3,
                "time_intervals": 2,
            },
        )

    def test_timestamp_offsets_remain_raw(self):
        raw = "2021-01-01T01:00:00+01:00/2021-01-02T00:00:00Z"
        result = time_extent(raw)
        self.assertEqual(result["time_first"], "2021-01-01T01:00:00+01:00")
        self.assertEqual(result["time_last"], "2021-01-02T00:00:00Z")

    def test_malformed_parts_are_rejected_even_inside_list(self):
        for invalid in (
            "/2020-12-31",
            "2020-01-01/",
            "2020-01-01//P1D",
            "2020-01-01/2020-12-31/",
            "2020-01-01/2020-12-31/P1D/extra",
            "2020-01-01/ /P1D",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(FetchError):
                time_extent(f"2019-01-01,{invalid},2021-01-01")

    def test_empty_and_nonprintable_values_are_rejected(self):
        for invalid in (" , ", "2020-01-01/2020-12-31\x00", "2020-01-01\n/2020-12-31"):
            with self.subTest(invalid=invalid), self.assertRaises(FetchError):
                time_extent(invalid)

    def test_existing_value_count_budget(self):
        self.assertEqual(
            time_extent(",".join(["2020-01-01"] * MAX_TIME_VALUES))["time_values"], MAX_TIME_VALUES
        )
        with self.assertRaises(FetchError):
            time_extent(",".join(["2020-01-01"] * (MAX_TIME_VALUES + 1)))

    def test_existing_length_budget_and_summary_truncation(self):
        self.assertEqual(time_extent("x" * 80)["time_last"], "x" * 40)
        with self.assertRaises(FetchError):
            time_extent("x" * 81)


class CopernicusWmsParserTests(TestCase):
    def fetch(self, key, body, url=None):
        source = make_source(key, enabled=True, activation_approved=True)
        http = Mock()
        http.get.return_value = Response(200, body, {}, url or source.url)
        return fetch_entries(source, http), http

    def test_effis_two_part_time_inherits_without_changing_default_or_readiness(self):
        body = EFFIS.replace(b"2026-10-01/2026-10-08/P1D", b"2018-01-01/2099-12-31")
        body = body.replace(b'default="2026-10-08"', b'default="2021-03-16"')
        (entries, bad, _, _), _ = self.fetch("effis", body)
        self.assertEqual(bad, 0)
        fwi = entries[1]
        self.assertEqual(fwi.metadata["time_first"], "2018-01-01")
        self.assertEqual(fwi.metadata["time_last"], "2099-12-31")
        self.assertEqual(fwi.metadata["time_intervals"], 1)
        self.assertEqual(fwi.metadata["time_default"], "2021-03-16")
        self.assertFalse(fwi.metadata["resources_downloaded"])
        self.assertEqual(
            parse_qs(urlsplit(fwi.metadata["preview_url"]).query)["TIME"], ["2021-03-16"]
        )

    def test_effis_layers_inherit_time_and_preview_uses_lat_lon_order(self):
        (entries, bad, result, digest), http = self.fetch("effis", EFFIS)
        self.assertEqual((len(entries), bad), (3, 0))
        service, fwi, burnt = entries
        self.assertEqual(service.metadata["fees_raw"], "none")
        self.assertEqual(service.metadata["layers_count"], 3)
        self.assertEqual(fwi.metadata["layer_name"], "mf010.fwi")
        self.assertEqual(
            (fwi.metadata["time_first"], fwi.metadata["time_last"]), ("2026-10-01", "2026-10-08")
        )
        self.assertEqual(fwi.metadata["time_intervals"], 1)
        self.assertEqual(burnt.metadata["time_last"], "2025-12-31")
        self.assertEqual(burnt.metadata["time_values"], 3)
        query = parse_qs(urlsplit(fwi.metadata["preview_url"]).query)
        self.assertEqual(query["CRS"], ["EPSG:4326"])
        self.assertEqual(query["BBOX"], ["49.0,14.0,55.0,24.2"])
        self.assertEqual(query["TIME"], ["2026-10-08"])
        self.assertEqual(fwi.url, fwi.metadata["preview_url"])
        for entry in entries:
            self.assertFalse(entry.metadata["resources_downloaded"])
            self.assertEqual((entry.published, entry.updated), ("", ""))
        http.get.assert_called_once()

    def test_edo_111_extent_and_layer_without_4326_has_no_preview(self):
        (entries, bad, result, digest), http = self.fetch("edo", EDO)
        cdi, low_flow = entries[1:]
        self.assertEqual(cdi.metadata["time_last"], "2026-09-21")
        self.assertEqual(cdi.metadata["time_default"], "2026-09-21")
        query = parse_qs(urlsplit(cdi.metadata["preview_url"]).query)
        self.assertEqual(query["SRS"], ["EPSG:4326"])
        self.assertEqual(query["BBOX"], ["14.0,49.0,24.2,55.0"])
        self.assertNotIn("preview_url", low_flow.metadata)
        self.assertNotIn("time_first", low_flow.metadata)
        self.assertEqual(low_flow.url, PROFILES["edo"]["url"])

    def test_edo_111_external_dtd_allowed_internal_subset_refused(self):
        dtd = b'<!DOCTYPE WMT_MS_Capabilities SYSTEM "http://schemas.opengis.net/wms/1.1.1/WMS_MS_Capabilities.dtd">'
        body = EDO.replace(b"?>", b"?>" + dtd, 1)
        (entries, *_), _ = self.fetch("edo", body)
        self.assertEqual(len(entries), 3)
        Source.objects.all().delete()
        with self.assertRaises(FetchError):
            self.fetch(
                "edo", EDO.replace(b"?>", b'?><!DOCTYPE WMT_MS_Capabilities [<!ENTITY a "b">]>', 1)
            )
        Source.objects.all().delete()
        with self.assertRaises(FetchError):
            self.fetch("effis", EFFIS.replace(b"?>", b"?>" + dtd.replace(b"WMT_MS", b"WMS"), 1))

    def test_new_time_step_changes_layer_hash(self):
        (before, *_), _ = self.fetch("effis", EFFIS)
        Source.objects.all().delete()
        (after, *_), _ = self.fetch("effis", EFFIS.replace(b"2026-10-08/P1D", b"2026-10-09/P1D"))
        self.assertNotEqual(before[1].metadata["document_hash"], after[1].metadata["document_hash"])
        self.assertEqual(before[2].metadata["document_hash"], after[2].metadata["document_hash"])

    def test_edo_reviewed_empty_vendor_declaration_allowed(self):
        # Actual EDO 1.1.1 response, read 2026-10-09; this subset declares no entities.
        dtd = (
            b"<!DOCTYPE WMT_MS_Capabilities SYSTEM "
            b'"http://schemas.opengis.net/wms/1.1.1/WMS_MS_Capabilities.dtd" [\n'
            b"<!ELEMENT VendorSpecificCapabilities EMPTY>\n]>"
        )
        entries = self.fetch("edo", EDO.replace(b"?>", b"?>" + dtd, 1))[0][0]
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[1].metadata["layer_name"], "cdiad")

    def test_edo_vendor_subset_entities_and_other_elements_refused(self):
        subsets = [
            b'<!ELEMENT VendorSpecificCapabilities EMPTY><!ENTITY x "injected">',
            b"<!ELEMENT VendorSpecificCapabilities ANY>",
            b"<!ELEMENT OtherCapabilities EMPTY>",
            b"<!ELEMENT VendorSpecificCapabilities EMPTY><!ELEMENT Other EMPTY>",
        ]
        for subset in subsets:
            with self.subTest(subset=subset):
                Source.objects.all().delete()
                dtd = (
                    b"<!DOCTYPE WMT_MS_Capabilities SYSTEM "
                    b'"http://schemas.opengis.net/wms/1.1.1/WMS_MS_Capabilities.dtd" ['
                    + subset
                    + b"]>"
                )
                with self.assertRaises(FetchError):
                    self.fetch("edo", EDO.replace(b"?>", b"?>" + dtd, 1))

    def test_layer_names_with_spaces_preserved_and_url_encoded(self):
        # EDO names grouping layers "Soil Moisture" and "Vegetation response".
        body = EDO.replace(b"<Name>cdiad</Name>", b"<Name>Soil Moisture</Name>")
        layer = self.fetch("edo", body)[0][0][1]
        self.assertEqual(layer.metadata["layer_name"], "Soil Moisture")
        self.assertEqual(parse_qs(urlsplit(layer.url).query)["LAYERS"], ["Soil Moisture"])
        self.assertNotIn(" ", layer.url)

    def test_layer_name_control_character_refused(self):
        body = EDO.replace(b"<Name>cdiad</Name>", b"<Name>Soil&#9;Moisture</Name>")
        with self.assertRaises(FetchError):
            self.fetch("edo", body)

    def test_identity_xml_and_layer_policies_fail_closed(self):
        cases = [
            ("effis", EFFIS, "https://maps.effis.emergency.copernicus.eu/other"),
            ("effis", EDO, None),
            ("edo", EFFIS, None),
            ("effis", b'<!DOCTYPE x [<!ENTITY a "b">]>' + EFFIS, None),
            ("effis", EFFIS.replace(b"modis.ba.2025", b"mf010.fwi"), None),
            ("effis", EFFIS.replace(b"<Title>EFFIS</Title><Fees>", b"<Fees>"), None),
        ]
        for key, body, url in cases:
            Source.objects.all().delete()
            with self.subTest(key=key, url=url), self.assertRaises(FetchError):
                self.fetch(key, body, url)

    def test_time_extent_budget(self):
        with self.assertRaises(FetchError):
            time_extent(" , ")
        self.assertEqual(time_extent("2020-01-01/2020-12-31/P1M")["time_last"], "2020-12-31")

    def test_each_layer_is_a_separate_publication(self):
        source = make_source("effis", enabled=True, activation_approved=True)
        http = Mock()
        http.get.return_value = Response(200, EFFIS, {}, source.url)
        with patch("ingestion.service.Client", return_value=http):
            run_batch(source_slug=source.slug, force=True)
        self.assertEqual(
            Item.objects.filter(source=source).values("publication_id").distinct().count(), 3
        )


class ConfigureCopernicusWmsTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="a" * 64, sheets={})
        for registry_id in (200, 202):
            RegistryEntry.objects.create(
                release=release,
                registry_id=registry_id,
                name=f"Registry {registry_id}",
                organization="JRC",
                priority="P1",
                integration_state="needs_connector",
                master={"original": True},
                technical={},
            )

    def run_command(self, *args):
        out = io.StringIO()
        call_command("configure_copernicus_wms", *args, stdout=out)
        return out.getvalue()

    def test_dry_apply_repeat(self):
        self.assertIn("Dry-run", self.run_command())
        self.assertFalse(Source.objects.exists())
        self.run_command("--apply")
        for key, profile in PROFILES.items():
            source = Source.objects.get(slug=slug(key))
            self.assertFalse(source.enabled or source.activation_approved)
            self.assertEqual(source.interval_minutes, 1440)
            self.assertEqual(
                list(source.registry_entries.values_list("registry_id", flat=True)),
                [profile["registry_id"]],
            )
        self.assertIn("unchanged", self.run_command("--apply"))
        self.assertEqual(Source.objects.count(), 2)

    def test_preserves_administrator_edits_and_existing_sources(self):
        self.run_command("--apply")
        Source.objects.filter(slug=slug("edo")).update(url="https://example.com/wms")
        with self.assertRaises(CommandError):
            self.run_command("--apply")
        Source.objects.all().delete()
        other = Source.objects.create(slug="manual-edo", adapter="web", url="https://x.eu/")
        RegistryEntry.objects.get(registry_id=200).sources.add(other)
        with self.assertRaises(CommandError):
            self.run_command("--apply")
        self.assertEqual(Source.objects.count(), 1)
