import hashlib
import json
import tempfile

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Sum
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ingestion.data_coverage import MODES, coverage_report, write_verified_export
from ingestion.data_retention import retained_snapshot
from ingestion.downloads import enqueue, restart_blocked
from ingestion.file_exports import export_descriptor, present_job, write_verified_file
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, DownloadJob
from ingestion.mwp_preview import owns_preview, read_preview
from ingestion.transport import FetchError


@require_GET
def catalog(request):
    channels = DataChannel.objects.select_related("registry").order_by("name")
    if request.GET.get("registry", "").isdigit():
        channels = channels.filter(registry__registry_id=int(request.GET["registry"]))
    if request.GET.get("kind"):
        channels = channels.filter(kind=request.GET["kind"])
    return render(
        request,
        "data_catalog.html",
        {
            "channels": channels,
            "used": DataSnapshot.objects.aggregate(total=Sum("size_bytes"))["total"] or 0,
            "quota": settings.DATA_STORAGE_MAX_BYTES,
            "snapshots": DataSnapshot.objects.filter(
                expires_at__gt=timezone.now(), download_job__isnull=True
            ).count(),
        },
    )


@require_GET
def channel_detail(request, pk):
    channel = get_object_or_404(DataChannel.objects.select_related("registry"), pk=pk)
    now = timezone.now()
    downloads = (
        [present_job(job, now, channel.access_approved) for job in channel.downloads.all()[:10]]
        if request.user.is_staff
        else []
    )
    snapshots = channel.snapshots.filter(
        expires_at__gt=timezone.now(), download_job__isnull=True
    ).defer("payload")
    latest = snapshots.first()
    preview = ""
    mwp_preview = None
    mwp_error = False
    mwp_view = owns_preview(channel, latest)
    coverage = None
    harvest_coverage = None
    if not mwp_view and channel.config.get("mode") in MODES:
        h = channel.harvests.defer("page_fingerprints", "visited_urls").first()
        if h:
            h.channel = channel
            harvest_coverage = coverage_report(h, now)
    if mwp_view:
        try:
            mwp_preview = read_preview(channel, now)
        except FetchError:
            mwp_error = True
    elif latest and latest.media_type == "application/json":
        retained = export_payload(DataSnapshot.objects.select_related("channel").get(pk=latest.pk))
        if retained:
            data = json.loads(retained)
            if channel.kind == "imgw_daily":
                coverage = data.get("coverage")
            rows = (
                data
                if isinstance(data, list)
                else data.get(
                    "features", data.get("Lista danych pomiarowych", data.get("records", data))
                )
            )
            preview = json.dumps(
                rows[:3] if isinstance(rows, list) else rows, ensure_ascii=False, indent=2
            )[:12000]
    return render(
        request,
        "data_detail.html",
        {
            "channel": channel,
            "downloads": downloads,
            "file_profile": export_descriptor(channel) if channel.kind == "file" else None,
            "harvests": channel.harvests.defer("page_fingerprints", "visited_urls")[:10],
            "preview": preview,
            "mwp_preview": mwp_preview,
            "mwp_error": mwp_error,
            "mwp_view": mwp_view,
            "coverage": coverage,
            "harvest_coverage": harvest_coverage,
            "snapshots": Paginator(snapshots, 30).get_page(request.GET.get("page")),
        },
    )


@require_GET
def coverage_dashboard(request):
    now = timezone.now()
    rows = []
    for channel in DataChannel.objects.filter(config__mode__in=sorted(MODES)).order_by("name"):
        h = channel.harvests.defer("page_fingerprints", "visited_urls").first()
        if h:
            h.channel = channel
        rows.append({"channel": channel, "report": coverage_report(h, now) if h else None})
    return render(request, "data_coverage.html", {"rows": rows, "as_of": now})


@staff_member_required
@require_GET
def download_harvest(request, pk):
    stream = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            h = get_object_or_404(DataHarvest.objects.select_related("channel"), pk=pk)
            digest = write_verified_export(h, timezone.now(), stream)
        stream.seek(0)
        response = FileResponse(
            stream,
            as_attachment=True,
            filename=f"geoklimat-pig-harvest-{pk}.geojson",
            content_type="application/geo+json",
        )
        response["ETag"] = '"' + digest + '"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
    except FetchError as exc:
        stream.close()
        raise Http404("Pełny eksport niedostępny: zakres, integralność lub limit rozmiaru") from exc
    except Exception:
        stream.close()
        raise


def export_payload(snapshot):
    try:
        result = retained_snapshot(snapshot, timezone.now())
    except (ValueError, TypeError, FetchError):
        return None
    if not result:
        return None
    payload = result[0]
    if snapshot.summary.get("acquisition_mode") in {"bdl_raw_sample", "bdl_rdlp_manual_scan"}:
        from ingestion.bdl_probe import COLLECTIONS, LICENSE, plan

        summary = snapshot.summary
        collection = summary.get("collection")
        expected_url = plan(collection)["url"] if collection in COLLECTIONS else None
        if summary.get("acquisition_mode") == "bdl_rdlp_manual_scan":
            from ingestion.bdl_scan import page_url

            ids = summary.get("feature_ids")
            if collection != "rdlp" or not isinstance(ids, list) or len(ids) != 1:
                return None
            try:
                expected_url = page_url(ids[0])
            except FetchError:
                return None
        if (
            collection not in COLLECTIONS
            or summary.get("url") != expected_url
            or summary.get("declared_api_license") != LICENSE
            or summary.get("original_response_preserved") is not True
            or len(payload) != snapshot.size_bytes
            or hashlib.sha256(payload).hexdigest() != snapshot.sha256
        ):
            return None
    if snapshot.summary.get("collector") in {
        "pig_midas_sample",
        "pig_midas_scan",
        "pig_spatial_scan",
    }:
        data = json.loads(payload)
        if not isinstance(data, dict) or not isinstance(data.get("geoklimat_provenance"), dict):
            return None
        data["geoklimat_provenance"]["copy_acquired_at"] = snapshot.fetched_at.isoformat()
        data["geoklimat_provenance"]["copy_expires_at"] = result[2].isoformat()
        return json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    return payload


@require_GET
def download(request, pk):
    snapshot = get_object_or_404(
        DataSnapshot, pk=pk, expires_at__gt=timezone.now(), download_job__isnull=True
    )
    payload = export_payload(snapshot)
    if payload is None:
        raise Http404("No unexpired observations")
    response = HttpResponse(payload, content_type="application/octet-stream")
    suffix = (
        "geojson"
        if snapshot.media_type == "application/geo+json"
        else "json"
        if snapshot.media_type == "application/json"
        else "tif"
        if snapshot.media_type == "image/tiff"
        else "bin"
    )
    response["Content-Disposition"] = f'attachment; filename="geoklimat-{snapshot.pk}.{suffix}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["ETag"] = '"' + hashlib.sha256(payload).hexdigest() + '"'
    if snapshot.summary.get("acquisition_mode") in {"bdl_raw_sample", "bdl_rdlp_manual_scan"}:
        from ingestion.bdl_probe import LICENSE

        response["Link"] = (
            f'<{LICENSE}>; rel="license", <{snapshot.summary["url"]}>; rel="canonical"'
        )
        response["X-Data-Attribution"] = "Bank Danych o Lasach; unmodified original API response"
        response["X-Data-Acquired-At"] = snapshot.fetched_at.isoformat()
    return response


@staff_member_required
@require_POST
def request_download(request, pk):
    channel = get_object_or_404(DataChannel, pk=pk, kind="file", access_approved=True)
    # No URL from the browser; only an administrator-reviewed, allowlisted channel.
    try:
        enqueue(channel)
    except FetchError:
        return HttpResponseBadRequest(
            "Kanał nie spełnia warunków pobrania. Odśwież jego konfigurację."
        )
    return redirect("data_detail", pk=channel.pk)


@staff_member_required
@require_GET
def download_file(request, pk):
    job = get_object_or_404(
        DownloadJob,
        pk=pk,
        status="complete",
        expires_at__gt=timezone.now(),
        channel__access_approved=True,
    )

    stream = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        digest = write_verified_file(job, timezone.now(), stream)
        stream.seek(0)
        descriptor = export_descriptor(job.channel, job)
        response = FileResponse(
            stream,
            as_attachment=True,
            filename=descriptor["filename"],
            content_type=descriptor["media_type"],
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        response["ETag"] = '"' + digest + '"'
        return response
    except FetchError as exc:
        stream.close()
        raise Http404("Eksport niedostępny: integralność, zakres lub limit pliku") from exc
    except Exception:
        stream.close()
        raise


@staff_member_required
@require_POST
def restart_file(request, pk):
    job = get_object_or_404(DownloadJob, pk=pk, channel__access_approved=True)
    try:
        restart_blocked(job)
    except FetchError:
        return HttpResponseBadRequest(
            "Kanał nie spełnia warunków pobrania. Odśwież jego konfigurację."
        )
    return redirect("data_detail", pk=job.channel_id)


def jrc_dataset(pk):
    from ingestion.jrc_catalogue import CONFIG, SLUG, URL
    from ingestion.models import Item

    return get_object_or_404(
        Item,
        pk=pk,
        source__slug=SLUG,
        source__url=URL,
        source__config=CONFIG,
        metadata__record_kind="dataset_collection",
    )


@staff_member_required
@require_GET
def jrc_files(request, pk):
    from ingestion.jrc_files import distributions
    from ingestion.transport import Client

    item = jrc_dataset(pk)
    try:
        files, error = distributions(Client(), item.metadata.get("dataset_uri", "")), ""
    except FetchError as exc:
        files, error = [], str(exc)[:300]
    return render(request, "jrc_files.html", {"item": item, "files": files, "error": error})


@staff_member_required
@require_POST
def jrc_file_request(request, pk, index):
    from ingestion.jrc_files import distributions, file_channel
    from ingestion.transport import Client

    item = jrc_dataset(pk)
    # The file is chosen by position in a fresh provider listing, never by a browser URL.
    try:
        files = distributions(Client(), item.metadata.get("dataset_uri", ""))
        if not 0 <= index < len(files):
            raise FetchError("JRC file list changed; reload")
        channel = file_channel(item.metadata["dataset_uri"], item.title, files[index])
        enqueue(channel)
    except FetchError:
        return HttpResponseBadRequest("Plik nie spełnia warunków pobrania. Odśwież listę.")
    return redirect("data_detail", pk=channel.pk)
