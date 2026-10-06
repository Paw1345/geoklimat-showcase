"""One rolling observation window for collection, export and cache maintenance."""

from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ingestion.transport import FetchError

TIMED_KINDS = {"imgw", "imgw_daily", "gios", "stac"}


def daily_anchor(day):
    # A conservative retention boundary, NOT an invented observation time.
    return datetime.combine(day, time(), tzinfo=ZoneInfo("Europe/Warsaw"))


def daily_window(now):
    today = now.astimezone(ZoneInfo("Europe/Warsaw")).date()
    return [
        day
        for offset in range(30, 0, -1)
        if now - retention_window() < daily_anchor(day := today - timedelta(days=offset))
    ]


def timestamp(value, zone="Europe/Warsaw"):
    if not isinstance(value, str):
        return None
    try:
        stamp = parse_datetime(value)
    except ValueError:
        return None
    if stamp and timezone.is_naive(stamp):
        stamp = timezone.make_aware(stamp, ZoneInfo(zone))
    return stamp


def retention_window():
    return timedelta(days=min(30, settings.DATA_RETENTION_DAYS))


def recent(value, now, zone="Europe/Warsaw"):
    stamp = timestamp(value, zone)
    return bool(stamp and now - retention_window() < stamp <= now + timedelta(minutes=5))


def filter_observations(data, kind, now, zone="UTC"):
    """Return an independent payload and retained timestamps; never turn null into zero."""
    data = deepcopy(data)
    stamps = []
    if kind == "imgw":
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise FetchError("IMGW list schema changed")
        rows = []
        for row in data:
            valid = []
            if "godzina_pomiaru" in row:
                stamp = str(row.get("data_pomiaru", ""))
                if len(stamp) == 10:
                    stamp += "T" + str(row["godzina_pomiaru"]).zfill(2) + ":00:00"
                if recent(stamp, now, zone):
                    valid.append(timestamp(stamp, zone))
            else:
                for key, value in list(row.items()):
                    if key.endswith(("_data_pomiaru", "_data")):
                        if recent(value, now, zone):
                            valid.append(timestamp(value, zone))
                        else:
                            row[key] = None
                            field = key.removesuffix("_data_pomiaru").removesuffix("_data")
                            if field in row:
                                row[field] = None
            if valid:
                rows.append(row)
                stamps.extend(valid)
        data = rows
    elif kind == "imgw_daily":
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise FetchError("IMGW daily observation schema changed")
        window = daily_window(now)
        rows = []
        for row in data["records"]:
            if not isinstance(row, dict) or not isinstance(row.get("date"), str):
                raise FetchError("IMGW daily date missing")
            try:
                day = date.fromisoformat(row["date"])
                if day.isoformat() != row["date"]:
                    raise ValueError
            except ValueError as exc:
                raise FetchError("IMGW daily date invalid") from exc
            if day in window:
                rows.append(row)
                stamps.append(daily_anchor(day))
        data["records"] = rows
        available = sorted({row["date"] for row in rows})
        data["coverage"] = {
            "available_dates": available,
            "expected_dates": [day.isoformat() for day in window],
            "missing_dates": [
                day.isoformat() for day in window if day.isoformat() not in available
            ],
            "stations": len({row.get("NSP") for row in rows}),
            "date_precision": "day",
            "retention_anchor": "start_of_day_Europe/Warsaw_not_measurement_time",
        }
    elif kind == "gios":
        if not isinstance(data, dict) or not isinstance(data.get("Lista danych pomiarowych"), list):
            raise FetchError("GIOS measurement schema changed")
        rows = data["Lista danych pomiarowych"]
        if any(not isinstance(row, dict) for row in rows):
            raise FetchError("GIOS measurement row schema changed")
        rows = [row for row in rows if recent(row.get("Data"), now, "Europe/Warsaw")]
        data["Lista danych pomiarowych"] = rows
        stamps = [timestamp(row["Data"]) for row in rows]
    elif kind == "stac":
        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            raise FetchError("STAC feature schema changed")
        rows = data["features"]
        if any(
            not isinstance(row, dict) or not isinstance(row.get("properties"), dict) for row in rows
        ):
            raise FetchError("STAC properties schema changed")
        rows = [row for row in rows if recent(row["properties"].get("datetime"), now, "UTC")]
        data["features"] = rows
        stamps = [timestamp(row["properties"]["datetime"], "UTC") for row in rows]
        # Provider counts describe the unfiltered page; do not silently reinterpret them.
    else:
        raise FetchError("Unsupported observation profile")
    return data, stamps


def observation_summary(summary, stamps, zone):
    summary = dict(summary)
    summary.update(retention_version=1, observation_timezone=zone)
    if stamps:
        summary.update(
            oldest_observation=min(stamps).astimezone(UTC).isoformat(),
            newest_observation=max(stamps).astimezone(UTC).isoformat(),
        )
    else:
        summary.pop("oldest_observation", None)
        summary.pop("newest_observation", None)
    return summary
