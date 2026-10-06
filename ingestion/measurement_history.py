"""Bounded, read-only history of raw observations in retained IMGW API copies."""

import hashlib
import json
import math
from datetime import UTC, timedelta

from django.db import connection, transaction
from django.utils import timezone

from ingestion.measurement_batches import prepare
from ingestion.models import DataChannel
from ingestion.observations import recent, retention_window, timestamp
from ingestion.transport import FetchError

PROFILES = {
    "synop": (
        "id_stacji",
        "stacja",
        {
            "temperatura": None,
            "predkosc_wiatru": None,
            "kierunek_wiatru": None,
            "wilgotnosc_wzgledna": None,
            "suma_opadu": None,
            "cisnienie": None,
        },
    ),
    "meteo": (
        "kod_stacji",
        "nazwa_stacji",
        {
            "temperatura_gruntu": "temperatura_gruntu_data",
            "temperatura_powietrza": "temperatura_powietrza_data",
            "wiatr_kierunek": "wiatr_kierunek_data",
            "wiatr_srednia_predkosc": "wiatr_srednia_predkosc_data",
            "wiatr_predkosc_maksymalna": "wiatr_predkosc_maksymalna_data",
            "wilgotnosc_wzgledna": "wilgotnosc_wzgledna_data",
            "wiatr_poryw_10min": "wiatr_poryw_10min_data",
            "opad_10min": "opad_10min_data",
        },
    ),
    "hydro": (
        "id_stacji",
        "stacja",
        {
            "stan_wody": "stan_wody_data_pomiaru",
            "temperatura_wody": "temperatura_wody_data_pomiaru",
            "przeplyw": "przeplyw_data",
            "zjawisko_lodowe": "zjawisko_lodowe_data_pomiaru",
            "zjawisko_zarastania": "zjawisko_zarastania_data_pomiaru",
        },
    ),
}
URLS = {"https://danepubliczne.imgw.pl/api/data/" + p: p for p in PROFILES}
RESULT_BYTES = 5 * 1024 * 1024


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FetchError("Duplicate measurement JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise FetchError("Non-finite measurement value")


def _stamp(row, key):
    if key is not None:
        return row.get(key)
    day, hour = row.get("data_pomiaru"), row.get("godzina_pomiaru")
    if (
        not isinstance(day, str)
        or len(day) != 10
        or not isinstance(hour, str)
        or not hour.isascii()
        or not hour.isdigit()
        or not 1 <= len(hour) <= 2
        or not 0 <= int(hour) <= 23
    ):
        return None
    return day + "T" + hour.zfill(2) + ":00:00"


def query(
    channel_id, station="", parameter="", days=30, limit=500, *, copy_page=None, copy_token=""
):
    if (
        type(days) is not int
        or not 1 <= days <= 30
        or type(limit) is not int
        or not 1 <= limit <= 1000
        or not isinstance(station, str)
        or len(station) > 32
        or (station and (not station.isascii() or not station.isdigit()))
        or not isinstance(parameter, str)
        or (copy_page is not None and (type(copy_page) is not int or not 1 <= copy_page <= 2000))
        or not isinstance(copy_token, str)
        or len(copy_token) > 2048
    ):
        raise FetchError("Invalid measurement query")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        channel = DataChannel.objects.get(pk=channel_id)
        profile = URLS.get(channel.url)
        if (
            not channel.access_approved
            or channel.kind != "imgw"
            or channel.region != "PL"
            or not profile
        ):
            raise FetchError("Reviewed IMGW channel required")
        identity, name, fields = PROFILES[profile]
        if parameter and parameter not in fields:
            raise FetchError("Unsupported measurement parameter")
        now = timezone.now()
        copies, batching, anchor = prepare(channel, now, days, copy_page, copy_token)
        cutoff = anchor - min(retention_window(), timedelta(days=days))
        stations, observations, receipts, zones = {}, {}, [], set()
        ignored_timestamps = 0
        for snapshot in copies.iterator(chunk_size=1):
            payload = bytes(snapshot.payload)
            summary = snapshot.summary
            zone = summary.get("observation_timezone")
            if (
                snapshot.media_type != "application/json"
                or snapshot.size_bytes != len(payload)
                or snapshot.sha256 != hashlib.sha256(payload).hexdigest()
                or summary.get("kind") != "imgw"
                or summary.get("retention_version") != 1
                or zone not in {"UTC", "Europe/Warsaw"}
            ):
                raise FetchError("Measurement copy integrity/profile invalid")
            zones.add(zone)
            if len(zones) != 1:
                raise FetchError("Mixed measurement timezone interpretation")
            try:
                data = json.loads(
                    payload, object_pairs_hook=_pairs, parse_constant=_invalid_constant
                )
            except (ValueError, TypeError, RecursionError) as exc:
                raise FetchError("Invalid measurement JSON") from exc
            if not isinstance(data, list) or len(data) > 5000:
                raise FetchError("Measurement list schema invalid")
            ids = set()
            for row in data:
                if not isinstance(row, dict):
                    raise FetchError("Measurement row schema invalid")
                sid, label = row.get(identity), row.get(name)
                if (
                    not isinstance(sid, str)
                    or not sid.isascii()
                    or not sid.isdigit()
                    or len(sid) > 32
                    or sid in ids
                    or not isinstance(label, str)
                    or not 1 <= len(label) <= 200
                ):
                    raise FetchError("Measurement station identity invalid")
                ids.add(sid)
                for field, time_key in fields.items():
                    if parameter and field != parameter:
                        continue
                    if field not in row:
                        continue
                    value, raw_time = row[field], _stamp(row, time_key)
                    if (
                        type(value) not in {str, int, float, type(None)}
                        or (type(value) is float and not math.isfinite(value))
                        or len(str(value)) > 128
                    ):
                        raise FetchError("Measurement value schema invalid")
                    if not recent(raw_time, now, zone):
                        if value is not None:
                            ignored_timestamps += 1
                        continue
                    stamp = timestamp(raw_time, zone).astimezone(UTC)
                    if stamp <= cutoff or stamp > anchor:
                        continue
                    stations.setdefault(sid, set()).add(label)
                    if len(stations) > 2000:
                        raise FetchError("Measurement station budget exceeded")
                    if sid != station:
                        continue
                    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
                    key = (stamp.isoformat(), field, encoded)
                    record = observations.setdefault(
                        key,
                        {
                            "station_id": sid,
                            "parameter": field,
                            "time_utc": key[0],
                            "observation_expires_at": (stamp + retention_window()).isoformat(),
                            "value": value,
                            "unit": None,
                            "snapshot_ids": [],
                            "source_times": [],
                        },
                    )
                    record["snapshot_ids"].append(snapshot.pk)
                    if raw_time not in record["source_times"]:
                        record["source_times"].append(raw_time)
                    if len(observations) > 25000:
                        raise FetchError("Measurement observation budget exceeded")
            receipts.append(
                {
                    "snapshot_id": snapshot.pk,
                    "sha256": snapshot.sha256,
                    "fetched_at": snapshot.fetched_at.isoformat(),
                    "expires_at": min(
                        snapshot.expires_at, snapshot.fetched_at + retention_window()
                    ).isoformat(),
                    "observation_timezone": zone,
                }
            )
        if station and station not in stations and copy_page is None:
            raise FetchError("Station has no retained observations for query")
        variants = {}
        for stamp, field, _ in observations:
            variants[(stamp, field)] = variants.get((stamp, field), 0) + 1
        records = []
        for key in sorted(observations, reverse=True)[:limit]:
            row = observations[key]
            row["value_variants"] = variants[key[:2]]
            records.append(row)
        observed_dates = sorted(
            {key[0][:10] for key, row in observations.items() if row["value"] is not None}
        )
        null_dates = sorted(
            {key[0][:10] for key, row in observations.items() if row["value"] is None}
        )
        calendar_days = [
            (cutoff.date() + timedelta(days=i)).isoformat()
            for i in range((anchor.date() - cutoff.date()).days + 1)
        ]
        result = {
            "channel_id": channel.pk,
            "channel": channel.name,
            "source_url": channel.url,
            "attribution": channel.attribution,
            "as_of": now.isoformat(),
            "cutoff_exclusive": cutoff.isoformat(),
            "station_id": station,
            "parameter": parameter,
            "limit": limit,
            "copy_batches": batching,
            "station_found_in_selected_copies": station in stations,
            "parameters": list(fields),
            "stations": [
                {"id": sid, "names": sorted(names)} for sid, names in sorted(stations.items())
            ],
            "records": records,
            "unique_values": len(observations),
            "null_values": sum(row["value"] is None for row in observations.values()),
            "returned": len(records),
            "truncated": len(observations) > len(records),
            "variant_groups": sum(n > 1 for n in variants.values()),
            "ignored_nonnull_values_without_recent_time": ignored_timestamps,
            "coverage": {
                "copy_count": batching["selected_copies"],
                "read_bytes": batching["selected_bytes"],
                "all_available_copies_selected": batching["all_available_copies_selected"],
                "selected_copies_payload_verified": True,
                "observed_dates_utc": observed_dates,
                "dates_with_timestamped_null_utc": null_dates,
                "dates_without_saved_observations_utc": [
                    day for day in calendar_days if day not in observed_dates
                ]
                if station
                else [],
                "full_history_verified": False,
                "cadence_verified": False,
                "timezone_interpretation": sorted(zones),
            },
            "receipts": receipts,
            "units_verified": False,
            "official_quality_verified": False,
        }
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > RESULT_BYTES:
            raise FetchError("Measurement history exceeds output budget")
        return result, payload, hashlib.sha256(payload).hexdigest()
