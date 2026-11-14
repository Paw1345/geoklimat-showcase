"""Staff-only preview of the reviewed FIGARO2015 file; no ingest or aggregation."""

from urllib.parse import urlencode

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import EmptyPage, Paginator
from django.db import connection, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion import jrc_figaro as figaro
from ingestion.models import DataChannel, DownloadJob
from ingestion.transport import FetchError


class PreviewForm(forms.Form):
    area = forms.ChoiceField(
        label="Obszar — kod refArea (nie tylko UE)",
        required=False,
        choices=[("", "Wszystkie kody źródłowe"), *[(code, code) for code in sorted(figaro.AREAS)]],
    )
    indicator = forms.ChoiceField(
        label="Wskaźnik — kod i jednostka źródłowa",
        required=False,
        choices=[("", "Wszystkie wskaźniki")]
        + [(code, f"{code} — {unit}") for code, unit in figaro.INDICATOR_UNITS.items()],
    )
    job = forms.IntegerField(
        required=False, min_value=1, max_value=2**63 - 1, widget=forms.HiddenInput
    )
    page = forms.IntegerField(required=False, min_value=1, max_value=667, widget=forms.HiddenInput)

    def clean(self):
        values = super().clean()
        if set(self.data) - set(self.fields) or any(
            len(self.data.getlist(key)) != 1 for key in self.data
        ):
            raise forms.ValidationError("Nieznany lub powtórzony parametr zapytania.")
        if (values.get("page") or 1) > 1 and not values.get("job"):
            raise forms.ValidationError("Kolejna strona wymaga przypiętej kopii (job).")
        return values


def matching_rows(rows, values):
    return [
        row
        for row in rows
        if (not values["area"] or row["refArea"] == values["area"])
        and (not values["indicator"] or row["codeIndicator"] == values["indicator"])
    ]


@never_cache
@staff_member_required(login_url="login")
@require_GET
def preview(request, pk):
    if pk > 2**63 - 1:
        raise Http404("Kanał niedostępny")
    form = PreviewForm(request.GET)
    context = {"form": form, "copy": None, "page": None, "links": {}}
    status = 200
    try:
        if connection.vendor != "postgresql" or connection.in_atomic_block:
            raise FetchError("FIGARO preview requires a standalone PostgreSQL snapshot")
        with transaction.atomic():
            # Fetch both channel and job inside the same snapshot as the payload fence.
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            channel = get_object_or_404(
                DataChannel.objects.select_related("registry"),
                pk=pk,
                slug=figaro.SLUG,
                kind="file",
                region="EU",
                url=figaro.URL,
                access_approved=True,
            )
            if channel.config != figaro.CONFIG:
                raise Http404("Profil FIGARO niedostępny")
            context["channel"] = channel
            if not form.is_valid():
                status = 400
            else:
                values = form.cleaned_data
                now = timezone.now()
                jobs = DownloadJob.objects.select_related("channel").filter(
                    channel=channel, status="complete", expires_at__gt=now
                )
                if values["job"]:
                    jobs = jobs.filter(pk=values["job"])
                job = jobs.order_by("-created_at", "-pk").first()
                if job is None and values["job"]:
                    context["error"] = "Wybrana kopia jest niedostępna lub wygasła."
                    status = 404
                elif job is not None:
                    rows, summary = figaro.read_verified_figaro(job, now)
                    page = Paginator(matching_rows(rows, values), 50).page(values["page"] or 1)
                    params = {key: values[key] for key in ("area", "indicator") if values[key]}
                    params["job"] = job.pk
                    links = {"current": "?" + urlencode({**params, "page": page.number})}
                    for relation in ("next", "previous"):
                        if getattr(page, "has_" + relation)():
                            links[relation] = "?" + urlencode(
                                {**params, "page": getattr(page, relation + "_page_number")()}
                            )
                    pinned = request.GET.copy()
                    pinned["job"] = str(job.pk)
                    pinned.pop("page", None)
                    context.update(copy=summary, page=page, links=links, form=PreviewForm(pinned))
    except EmptyPage:
        form.add_error("page", "Strona poza zakresem wyników. Wróć do strony 1.")
        status = 400
    except FetchError:
        context["error"] = "Nie można potwierdzić integralności lub bezpiecznego odczytu tej kopii."
        status = 409
    return render(request, "figaro_preview.html", context, status=status)
