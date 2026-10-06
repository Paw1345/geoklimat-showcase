import csv
import hashlib
import io
import json
from urllib.parse import urlencode

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.measurement_history import RESULT_BYTES, URLS, query
from ingestion.models import DataChannel
from ingestion.transport import FetchError


class HistoryForm(forms.Form):
    channel = forms.ModelChoiceField(
        label="Kanał IMGW",
        queryset=DataChannel.objects.filter(
            kind="imgw",
            access_approved=True,
            url__in=sorted(URLS),
        ).order_by("name"),
    )
    station = forms.RegexField(
        label="Identyfikator stacji (puste = lista stacji)",
        regex=r"\A[0-9]{1,32}\Z",
        required=False,
        max_length=32,
    )
    parameter = forms.CharField(label="Parametr (puste = wszystkie)", required=False, max_length=40)
    days = forms.IntegerField(label="Ostatnie dni", min_value=1, max_value=30, initial=30)
    limit = forms.IntegerField(label="Limit wyników", min_value=1, max_value=1000, initial=500)
    copy_page = forms.IntegerField(
        label="Paczka kopii (puste = cały lokalny zakres; 1 = rozpocznij paczki)",
        required=False,
        min_value=1,
        max_value=2000,
    )
    copy_token = forms.CharField(required=False, max_length=2048, widget=forms.HiddenInput)
    format = forms.ChoiceField(
        label="Wynik",
        choices=[("html", "Podgląd"), ("json", "JSON"), ("csv", "CSV")],
    )

    def clean(self):
        values = super().clean()
        if values.get("format") == "csv" and not values.get("station"):
            raise forms.ValidationError("Eksport CSV wymaga wybrania identyfikatora stacji.")
        return values


def safe_cell(value):
    text = "" if value is None else str(value)
    # Treat raw provider strings as data, never spreadsheet expressions.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def csv_payload(result):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "station_id",
            "parameter",
            "time_utc",
            "raw_value",
            "raw_value_is_null",
            "units_verified",
            "observation_expires_at",
            "value_variants",
            "snapshot_ids",
            "source_times",
            "source_url",
            "attribution",
            "as_of",
            "full_history_verified",
            "truncated",
            "copy_batch_page",
            "copy_batch_pages",
            "all_available_copies_selected",
            "copy_scope_hash",
            "copy_receipts_json",
        ]
    )
    receipts = {r["snapshot_id"]: r for r in result["receipts"]}
    for row in result["records"]:
        writer.writerow(
            [
                safe_cell(v)
                for v in [
                    row["station_id"],
                    row["parameter"],
                    row["time_utc"],
                    row["value"],
                    row["value"] is None,
                    False,
                    row["observation_expires_at"],
                    row["value_variants"],
                    json.dumps(row["snapshot_ids"]),
                    json.dumps(row["source_times"]),
                    result["source_url"],
                    result["attribution"],
                    result["as_of"],
                    False,
                    result["truncated"],
                    result["copy_batches"]["page"],
                    result["copy_batches"]["pages"],
                    result["copy_batches"]["all_available_copies_selected"],
                    result["copy_batches"].get("scope_hash", ""),
                    json.dumps([receipts[pk] for pk in row["snapshot_ids"]], ensure_ascii=False),
                ]
            ]
        )
        if stream.tell() > RESULT_BYTES:
            raise FetchError("CSV output budget exceeded")
    payload = ("\ufeff" + stream.getvalue()).encode()
    if len(payload) > RESULT_BYTES:
        raise FetchError("CSV output budget exceeded")
    return payload


@require_GET
@staff_member_required(login_url="login")
@never_cache
def history(request):
    form = HistoryForm(request.GET or None)
    result, error, status = None, "", 200
    links = {}
    if request.GET:
        if not form.is_valid():
            status = 400
        else:
            values = form.cleaned_data
            links["restart"] = "?" + urlencode(
                {
                    "channel": values["channel"].pk,
                    "station": values["station"],
                    "parameter": values["parameter"],
                    "days": values["days"],
                    "limit": values["limit"],
                    "format": "html",
                    "copy_page": 1,
                }
            )
            try:
                result, payload, digest = query(
                    values["channel"].pk,
                    values["station"],
                    values["parameter"],
                    values["days"],
                    values["limit"],
                    copy_page=values["copy_page"],
                    copy_token=values["copy_token"],
                )
                params = {
                    "channel": result["channel_id"],
                    "station": values["station"],
                    "parameter": values["parameter"],
                    "days": values["days"],
                    "limit": values["limit"],
                    "format": "html",
                }
                batch = result["copy_batches"]
                links["restart"] = "?" + urlencode({**params, "copy_page": 1})
                if batch["mode"] == "batches":
                    params.update(copy_page=batch["page"], copy_token=batch["token"])
                    display_data = request.GET.copy()
                    display_data["copy_token"] = batch["token"]
                    form = HistoryForm(display_data)
                    for key, offset in [("previous", -1), ("next", 1)]:
                        if batch["has_" + key]:
                            links[key] = "?" + urlencode(
                                {**params, "copy_page": batch["page"] + offset}
                            )
                links["json"] = "?" + urlencode({**params, "format": "json"})
                links["csv"] = "?" + urlencode({**params, "format": "csv"})
                if values["format"] == "csv":
                    payload = csv_payload(result)
                    digest = hashlib.sha256(payload).hexdigest()
            except (FetchError, DataChannel.DoesNotExist):
                error = (
                    "Brak danych dla filtra albo kopie, profil lub budżet nie przeszły "
                    "weryfikacji. Token mógł wygasnąć lub zakres kopii się zmienił. "
                    "Niczego nie pobrano. Rozpocznij przegląd od paczki 1 "
                    "lub ponownie otwórz panel."
                )
                status = 409
                result = None
            if values["format"] != "html":
                if error:
                    return JsonResponse({"error": error}, status=status)
                suffix = values["format"]
                response = HttpResponse(
                    payload,
                    content_type="application/json" if suffix == "json" else "text/csv",
                )
                response["Content-Disposition"] = f'attachment; filename="geoklimat-imgw.{suffix}"'
                response["X-Content-Type-Options"] = "nosniff"
                response["ETag"] = '"' + digest + '"'
                return response
    return render(
        request,
        "measurement_history.html",
        {
            "form": form,
            "result": result,
            "error": error,
            "links": links,
        },
        status=status,
    )
