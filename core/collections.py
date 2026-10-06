"""Read-only, bounded inspection of stored collection metadata, not data downloads."""

import csv
import hashlib
import io
import json
from urllib.parse import urlencode, urlsplit

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection, transaction
from django.db.models import BigIntegerField, Count, F, Func, Q, TextField
from django.db.models.functions import Cast
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core.measurements import safe_cell
from ingestion.models import Item

INPUT_BYTES = 2 * 1024 * 1024
OUTPUT_BYTES = 5 * 1024 * 1024
IDENTIFIERS = ("collection_id", "product_id", "dataset_code", "dataset_uri")
METADATA = (
    *IDENTIFIERS,
    "license_raw",
    "license",
    "doi_raw",
    "extent_raw",
    "extent",
    "listing_scope",
    "dataset_modified_raw",
    "publication_date_raw",
    "updated_date_raw",
)


class CollectionForm(forms.Form):
    source = forms.ChoiceField(label="Źródło", required=False)
    q = forms.CharField(label="Tytuł lub zapisany identyfikator", required=False, max_length=100)
    license = forms.CharField(label="Dokładny wskaźnik licencji", required=False, max_length=100)
    doi = forms.ChoiceField(
        label="Zapisany DOI",
        required=False,
        choices=[("", "Wszystkie"), ("yes", "Podano"), ("no", "Brak zapisanego DOI")],
    )
    page = forms.IntegerField(label="Strona", min_value=1, max_value=100000)
    page_size = forms.IntegerField(label="Rekordów na stronę", min_value=1, max_value=100)
    format = forms.ChoiceField(
        label="Format",
        choices=[("html", "Podgląd"), ("json", "JSON"), ("csv", "CSV")],
    )

    def __init__(self, data, sources):
        super().__init__(data)
        self.fields["source"].choices = [
            ("", "Wszystkie zatwierdzone źródła"),
            *((str(s["source_id"]), s["source__name"]) for s in sources),
        ]


def scalar(value, maximum=1500):
    if value is not None and (not isinstance(value, str) or len(value) > maximum):
        raise ValueError("Invalid stored collection metadata")
    return value


def record(row):
    raw = {key: row[f"metadata__{key}"] for key in METADATA}
    identifier_key = next((key for key in IDENTIFIERS if raw[key] is not None), None)
    extent = raw["extent_raw"] if raw["extent_raw"] is not None else raw["extent"]
    if extent is not None:
        if not isinstance(extent, dict):
            raise ValueError("Invalid stored extent")
        # Only the two collection bounds, never embedded asset/URL/provider keys.
        extent = {key: extent[key] for key in ("spatial", "temporal") if key in extent}
        for key, member in (("spatial", "bbox"), ("temporal", "interval")):
            if key in extent:
                if not isinstance(extent[key], dict):
                    raise ValueError("Invalid stored extent")
                extent[key] = {member: extent[key].get(member)}
        # Expose raw JSON bounds only; no spatial/temporal availability inference.
        if len(json.dumps(extent, allow_nan=False).encode()) > 32 * 1024:
            raise ValueError("Stored extent exceeds display budget")
        boxes = extent.get("spatial", {}).get("bbox")
        intervals = extent.get("temporal", {}).get("interval")
        for values, width in ((boxes, 4), (intervals, 2)):
            if values is not None and (
                not isinstance(values, list)
                or len(values) > 32
                or any(not isinstance(v, list) or len(v) != width for v in values)
            ):
                raise ValueError("Invalid stored collection bounds")
        if boxes is not None and any(
            type(value) not in (int, float) for box in boxes for value in box
        ):
            raise ValueError("Invalid stored spatial bounds")
        if intervals is not None and any(
            value is not None and (not isinstance(value, str) or len(value) > 50)
            for interval in intervals
            for value in interval
        ):
            raise ValueError("Invalid stored temporal bounds")
    parsed = urlsplit(row["url"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError("Invalid stored public item URL")
    return {
        "item_id": row["pk"],
        "title": row["title"],
        "source_id": row["source_id"],
        "source_name": row["source__name"],
        "source_enabled": row["source__enabled"],
        "source_last_success_at": row["source__last_success_at"].isoformat()
        if row["source__last_success_at"]
        else None,
        "provider_identifier": scalar(raw[identifier_key]) if identifier_key else None,
        "identifier_field": identifier_key,
        "source_url": row["url"],
        "content_hash_recorded": row["content_hash"],
        "last_seen_at": row["last_seen_at"].isoformat(),
        "license_indicator_raw": scalar(
            raw["license_raw"] if raw["license_raw"] is not None else raw["license"], 100
        ),
        "doi_raw": scalar(raw["doi_raw"], 200),
        "extent_raw": extent,
        "listing_scope": scalar(raw["listing_scope"], 200),
        "dataset_modified_raw": scalar(raw["dataset_modified_raw"], 100),
        "publication_date_raw": scalar(raw["publication_date_raw"], 100),
        "updated_date_raw": scalar(raw["updated_date_raw"], 100),
        "resources_downloaded_by_this_view": False,
        "license_permission_verified": False,
    }


def report(params):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        base = Item.objects.filter(
            metadata__record_kind="dataset_collection", source__activation_approved=True
        )
        sources = list(
            base.order_by()
            .values("source_id", "source__name")
            .annotate(records=Count("pk"))
            .order_by("source_id")[:1001]
        )
        if len(sources) > 1000:
            raise ValueError("Collection source budget exceeded")
        values = params.copy()
        for key, value in (("page", "1"), ("page_size", "50"), ("format", "html")):
            values.setdefault(key, value)
        form = CollectionForm(values, sources)
        allowed = set(form.fields)
        if set(params) - allowed or any(len(params.getlist(key)) > 1 for key in params):
            form.is_valid()
            form.add_error(None, "Nieznane lub powtórzone parametry.")
            return form, None
        if not form.is_valid():
            return form, None
        filters = form.cleaned_data
        items = base
        if filters["source"]:
            items = items.filter(source_id=int(filters["source"]))
        if filters["q"]:
            query = Q(title__icontains=filters["q"])
            for key in IDENTIFIERS:
                query |= Q(**{f"metadata__{key}__icontains": filters["q"]})
            items = items.filter(query)
        if filters["license"]:
            items = items.filter(
                Q(metadata__license_raw=filters["license"])
                | (
                    (Q(metadata__license_raw__isnull=True) | Q(metadata__license_raw=None))
                    & Q(metadata__license=filters["license"])
                )
            )
        absent_doi = (
            Q(metadata__doi_raw__isnull=True) | Q(metadata__doi_raw=None) | Q(metadata__doi_raw="")
        )
        if filters["doi"]:
            items = (
                items.exclude(absent_doi) if filters["doi"] == "yes" else items.filter(absent_doi)
            )
        total = items.count()
        page, size = filters["page"], filters["page_size"]
        pages = max(1, (total + size - 1) // size)
        if page > pages:
            form.add_error("page", "Strona poza zakresem wyników.")
            return form, None
        offset = (page - 1) * size
        metadata_bytes = Func(
            Cast(F("metadata"), TextField()),
            function="OCTET_LENGTH",
            output_field=BigIntegerField(),
        )
        selected = list(
            items.order_by("pk")
            .annotate(metadata_bytes=metadata_bytes)
            .values("pk", "metadata_bytes")[offset : offset + size]
        )
        if sum(row["metadata_bytes"] for row in selected) > INPUT_BYTES:
            raise ValueError("Stored collection metadata exceeds input budget")
        rows = (
            items.filter(pk__in=[row["pk"] for row in selected])
            .order_by("pk")
            .values(
                "pk",
                "title",
                "url",
                "content_hash",
                "last_seen_at",
                "source_id",
                "source__name",
                "source__enabled",
                "source__last_success_at",
                *(f"metadata__{key}" for key in METADATA),
            )
        )
        result = {
            "records": [record(row) for row in rows],
            "sources": sources,
            "local_filtered_total": total,
            "page": page,
            "pages": pages,
            "page_size": size,
            "has_next": page < pages,
            "has_previous": page > 1,
            "selected_page_only": True,
            "same_provider_snapshot_across_pages_verified": False,
            "as_of": timezone.now().isoformat(),
            "filters": {key: value for key, value in filters.items() if key != "format"},
            "scope": "local_dataset_collection_records_from_approved_sources",
            "metadata_only": True,
            "unique_products_verified": False,
            "resources_downloaded_by_this_view": False,
            "full_provider_catalogues_verified": False,
            "current_30day_measurements_verified": False,
            "exact_pl_eu_mask_verified": False,
            "content_hash_recomputed": False,
        }
    return form, result


def csv_body(result):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    columns = (
        "item_id",
        "source_name",
        "source_enabled",
        "title",
        "provider_identifier",
        "identifier_field",
        "doi_raw",
        "license_indicator_raw",
        "source_url",
        "publication_date_raw",
        "updated_date_raw",
        "dataset_modified_raw",
        "source_last_success_at",
        "last_seen_at",
        "content_hash_recorded",
        "extent_raw",
    )
    writer.writerow(
        [
            *columns,
            "as_of",
            "page",
            "pages",
            "local_filtered_total",
            "selected_page_only",
            "full_provider_catalogues_verified",
            "resources_downloaded_by_this_view",
            "license_permission_verified",
            "exact_pl_eu_mask_verified",
        ]
    )
    for row in result["records"]:
        values = {
            **row,
            "extent_raw": json.dumps(row["extent_raw"], ensure_ascii=False, allow_nan=False),
        }
        writer.writerow(
            [
                *(safe_cell(values.get(key)) for key in columns),
                result["as_of"],
                result["page"],
                result["pages"],
                result["local_filtered_total"],
                True,
                False,
                False,
                False,
                False,
            ]
        )
    return stream.getvalue().encode("utf-8-sig")


@require_GET
@staff_member_required(login_url="login")
@never_cache
def catalog(request):
    try:
        form, result = report(request.GET)
        if result is None:
            response = render(request, "collections.html", {"form": form}, status=400)
        elif form.cleaned_data["format"] in {"json", "csv"}:
            kind = form.cleaned_data["format"]
            body = (
                csv_body(result)
                if kind == "csv"
                else json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
            )
            response = HttpResponse(
                body,
                content_type="text/csv; charset=utf-8" if kind == "csv" else "application/json",
            )
            response["Content-Disposition"] = f'attachment; filename="geoklimat-collections.{kind}"'
            response["ETag"] = '"' + hashlib.sha256(body).hexdigest() + '"'
        else:
            values = {key: value for key, value in form.cleaned_data.items() if value != ""}
            links = {
                "json": "?" + urlencode({**values, "format": "json"}),
                "csv": "?" + urlencode({**values, "format": "csv"}),
                "next": "?" + urlencode({**values, "page": result["page"] + 1}),
                "previous": "?" + urlencode({**values, "page": result["page"] - 1}),
            }
            response = render(
                request, "collections.html", {"form": form, "result": result, "links": links}
            )
        if len(response.content) > OUTPUT_BYTES:
            raise ValueError("Collection output budget exceeded")
    except (ValueError, TypeError, OverflowError):
        response = JsonResponse(
            {
                "error": (
                    "Nie można bezpiecznie wyświetlić tej strony metadanych. "
                    "Bez częściowego eksportu."
                )
            },
            status=409,
        )
    response["X-Content-Type-Options"] = "nosniff"
    return response
