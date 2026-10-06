from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection, transaction
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.models import DataChannel
from ingestion.stac_capacity import capacity
from ingestion.stac_coverage import coverage
from ingestion.stac_integrity import verify_batch
from ingestion.stac_refresh import plan as refresh_plan
from ingestion.transport import FetchError


def report(verify_channel=None, verify_harvest=None, start=1):
    now = timezone.now()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        query = DataChannel.objects.filter(kind="stac")
        if verify_channel is not None:
            query = query.filter(pk=verify_channel)
        channels = list(query.order_by("pk")[:21])
        if len(channels) > 20:
            raise ValueError("STAC report channel limit exceeded")
        rows = []
        budget = capacity(now)
        refresh = refresh_plan(now)
        verification = None
        if verify_channel is not None and not channels:
            raise ValueError("STAC channel not found")
        for channel in channels:
            harvests = channel.harvests.defer("page_fingerprints", "visited_urls")
            if verify_channel is not None:
                harvests = harvests.filter(pk=verify_harvest)
            harvest = harvests.first()
            if verify_channel is not None:
                if not harvest:
                    raise ValueError("Pinned STAC harvest not found")
                verification = verify_batch(channel, harvest, now, start)
            rows.append(coverage(channel, harvest, now))
    return {
        "as_of": now,
        "rows": rows,
        "read_only": True,
        "verification": verification,
        "capacity": budget,
        "refresh_plan": refresh,
    }


@staff_member_required
@require_GET
@never_cache
def dashboard(request):
    if request.GET.get("format", "html") not in {"html", "json"}:
        return HttpResponseBadRequest("Nieznany format")
    try:
        verify = request.GET.get("verify", "")
        if verify not in {"", "1"}:
            raise ValueError("Invalid verification mode")
        params = {}
        if verify:
            channel = int(request.GET.get("channel", ""))
            harvest = int(request.GET.get("harvest", ""))
            start = int(request.GET.get("start", "1"))
            if (
                not 1 <= channel <= 2**31 - 1
                or not 1 <= harvest <= 2**31 - 1
                or not 1 <= start <= 10000
            ):
                raise ValueError("Invalid verification scope")
            params = {"verify_channel": channel, "verify_harvest": harvest, "start": start}
        result = report(**params)
    except ValueError:
        return HttpResponseBadRequest("Niepoprawny zakres, parametry lub limit raportu")
    except FetchError:
        return HttpResponse(
            "Paczka niedostępna: integralność, zakres, retencja lub limit.", status=409
        )
    if request.GET.get("format") == "json":
        response = JsonResponse(result)
        response["X-Content-Type-Options"] = "nosniff"
        return response
    return render(request, "stac_coverage.html", {"report": result})
