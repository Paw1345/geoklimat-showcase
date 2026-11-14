import io
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from ingestion import imgw_warnings as warnings
from ingestion.data_service import run_data
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

# Shape of the 2026-10-14 probe (one of 95 hydro warnings, text shortened).
HYDRO = {
    "opublikowano": "2026-04-17 08:45:56",
    "stopień": "-1",
    "data_od": "2026-04-17 08:46:51",
    "data_do": "9999-12-31 23:59:59",
    "prawdopodobienstwo": "90",
    "numer": "21",
    "biuro": "Biuro Prognoz Hydrologicznych we Wrocławiu",
    "zdarzenie": "Susza hydrologiczna",
    "przebieg": "Dalsze utrzymywanie się przepływów wody poniżej SNQ.",
    "komentarz": "Ostrzeżenie wydawane jest przy przepływach poniżej SNQ.",
    "obszary": [{"wojewodztwo": "łódzkie", "opis": "Warta górna", "kod_zlewni": ["Z_P_LD_1816"]}],
}
NO_METEO = {"message": "Brak ostrzeżeń meteorologicznych"}


def body(data):
    return json.dumps(data, ensure_ascii=False).encode()


class FakeClient:
    max_bytes = 0

    def __init__(self, *bodies):
        self.bodies, self.urls = list(bodies), []

    def get(self, url):
        self.urls.append(url)
        return Response(200, self.bodies.pop(0), {"content-type": "application/json"}, url)


class Channel:
    kind, region = "table", "PL"

    def __init__(self, product, registry_id=None):
        self.url, self.config = warnings.url(product), warnings.config(product)
        self.registry = type("R", (), {"registry_id": registry_id or warnings.PRODUCTS[product]})


class ParsingTests(SimpleTestCase):
    def test_hydro_list_is_summarised(self):
        later = {**HYDRO, "opublikowano": "2026-10-14 07:00:00", "data_do": "2026-10-16 08:00:00"}
        result = warnings.parse("hydro", body([HYDRO, later]))
        self.assertEqual(result["records"], 2)
        self.assertEqual(result["open_ended"], 1)
        self.assertEqual(result["levels"], {"-1": 2})
        self.assertEqual(result["events"], {"Susza hydrologiczna": 2})
        self.assertEqual(result["newest_published_raw"], "2026-10-14 07:00:00")

    def test_no_warning_message_and_empty_list_are_zero_records(self):
        self.assertEqual(
            warnings.parse("meteo", body(NO_METEO)),
            {"records": 0, "provider_message": "Brak ostrzeżeń meteorologicznych"},
        )
        self.assertEqual(warnings.parse("hydro", b"[]")["records"], 0)

    def test_unconfirmed_meteo_list_keeps_keys(self):
        result = warnings.parse("meteo", body([{"id": "1", "nazwa_zdarzenia": "Burze"}]))
        self.assertEqual((result["records"], result["keys_seen"]), (1, ["id", "nazwa_zdarzenia"]))

    def test_changed_shapes_are_refused(self):
        for product, payload in (
            ("hydro", body([{**HYDRO, "numer": 21}])),
            ("hydro", body([{k: v for k, v in HYDRO.items() if k != "obszary"}])),
            ("hydro", body([{**HYDRO, "data_od": "17.04.2026"}])),
            ("meteo", body({"message": "x", "extra": 1})),
            ("meteo", body(["text"])),
            ("meteo", b"<html>"),
            ("meteo", b""),
        ):
            with self.subTest(payload=payload[:40]), self.assertRaises(FetchError):
                warnings.parse(product, payload)

    def test_changed_profile_is_refused_before_http(self):
        client = FakeClient()
        changed_url = Channel("meteo")
        changed_url.url += "?x=1"
        for channel in (Channel("hydro", registry_id=34), changed_url):
            with self.assertRaises(FetchError):
                warnings.collect(channel, client)
        self.assertEqual(client.urls, [])

    def test_collect_preserves_provider_bytes(self):
        raw = body([HYDRO])
        client = FakeClient(raw)
        payload, media, summary = warnings.collect(Channel("hydro"), client)
        self.assertEqual((payload, media), (raw, "application/json"))
        self.assertEqual(client.urls, ["https://danepubliczne.imgw.pl/api/data/warningshydro"])
        self.assertEqual(summary["records"], 1)
        self.assertNotIn("newest_observation", summary)


class ChannelTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t", sha256="c" * 64, sheets={})
        for registry_id in (33, 34):
            RegistryEntry.objects.create(
                registry_id=registry_id, release=release, priority="P1", master={}, technical={}
            )

    def test_configure_is_dry_run_then_creates_disabled_and_preserves(self):
        call_command("configure_imgw_warnings", stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.exists())
        call_command("configure_imgw_warnings", "--apply", stdout=io.StringIO())
        channels = DataChannel.objects.order_by("slug")
        self.assertEqual(
            [(c.slug, c.registry.registry_id, c.interval_minutes) for c in channels],
            [("imgw-warnings-hydro", 33, 30), ("imgw-warnings-meteo", 34, 30)],
        )
        self.assertFalse(any(c.enabled or c.access_approved for c in channels))
        call_command("configure_imgw_warnings", "--apply", stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.count(), 2)
        DataChannel.objects.filter(slug="imgw-warnings-meteo").update(config={"mode": "other"})
        with self.assertRaises(CommandError):
            call_command("configure_imgw_warnings", "--apply", stdout=io.StringIO())

    def test_worker_run_stores_then_dedupes_identical_response(self):
        call_command("configure_imgw_warnings", "--apply", stdout=io.StringIO())
        slug = warnings.slug("hydro")
        DataChannel.objects.filter(slug=slug).update(enabled=True, access_approved=True)
        client = FakeClient(body([HYDRO]), body([HYDRO]))
        with patch("ingestion.data_service.Client", return_value=client):
            first = run_data(slug, force=True)
            second = run_data(slug, force=True)
        self.assertEqual([first[0]["status"], second[0]["status"]], ["stored", "unchanged"])
        snapshot = DataSnapshot.objects.get()
        self.assertEqual(snapshot.summary["product"], "hydro")
        self.assertEqual(snapshot.media_type, "application/json")
