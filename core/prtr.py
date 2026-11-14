"""GET-only search over one verified annual PRTR table; no collector calls."""

from urllib.parse import urlencode

from django import forms
from django.core.paginator import EmptyPage, Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.gios_prtr_table import SLUG, validate_profile
from ingestion.models import DataChannel
from ingestion.prtr_table_read import read_copy
from ingestion.transport import FetchError


class SearchForm(forms.Form):
    facility = forms.CharField(
        label="Zakład — fragment nazwy lub identyfikatora", required=False, max_length=100
    )
    pollutant = forms.CharField(
        label="Substancja — dokładny kod źródłowy",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"list": "prtr-pollutants"}),
    )
    medium = forms.ChoiceField(
        label="Medium",
        required=False,
        choices=[
            ("", "Wszystkie media"),
            ("AIR", "Powietrze (AIR)"),
            ("WATER", "Woda (WATER)"),
            ("LAND", "Gleba (LAND)"),
        ],
    )
    snapshot = forms.IntegerField(
        required=False, min_value=1, max_value=2**63 - 1, widget=forms.HiddenInput
    )
    page = forms.IntegerField(required=False, min_value=1, max_value=100, widget=forms.HiddenInput)

    def clean(self):
        values = super().clean()
        if any(k not in self.fields or len(self.data.getlist(k)) != 1 for k in self.data):
            raise forms.ValidationError("Nieznany lub powtórzony parametr zapytania.")
        return values


@require_GET
@never_cache
def search(request, pk):
    channel = get_object_or_404(
        DataChannel.objects.select_related("registry"),
        pk=pk,
        slug=SLUG,
        access_approved=True,
    )
    try:
        validate_profile(channel)
    except FetchError as exc:
        raise Http404("Profil PRTR niedostępny") from exc
    form = SearchForm(request.GET)
    context = {"channel": channel, "form": form, "copy": None, "page": None, "links": {}}
    status = 200
    if not form.is_valid():
        status = 400
    else:
        values = form.cleaned_data
        try:
            result = read_copy(channel, values["snapshot"], timezone.now())
            if result is None and values["snapshot"]:
                context["error"] = (
                    "Wybrana kopia jest niedostępna lub wygasła. Wybierz aktualną kopię."
                )
                status = 404
            elif result is not None:
                rows = [
                    r
                    for r in result["records"]
                    if (
                        not values["facility"]
                        or values["facility"].casefold() in r["facility_id"].casefold()
                        or values["facility"].casefold() in (r["facility_name"] or "").casefold()
                    )
                    and (
                        not values["pollutant"]
                        or values["pollutant"].casefold() == r["pollutant_code"].casefold()
                    )
                    and (not values["medium"] or values["medium"] == r["medium_code"])
                ]
                rows.sort(key=lambda r: (r["facility_id"], r["medium_code"], r["pollutant_code"]))
                page = Paginator(rows, 50).page(values["page"] or 1)
                params = {k: values[k] for k in ("facility", "pollutant", "medium")}
                params["snapshot"] = result["snapshot"].pk
                links = {"current": "?" + urlencode({**params, "page": page.number})}
                if page.has_next():
                    links["next"] = "?" + urlencode({**params, "page": page.next_page_number()})
                if page.has_previous():
                    links["previous"] = "?" + urlencode(
                        {**params, "page": page.previous_page_number()}
                    )
                pinned = request.GET.copy()
                pinned["snapshot"] = str(result["snapshot"].pk)
                form = SearchForm(pinned)
                context.update(
                    form=form,
                    copy=result,
                    page=page,
                    links=links,
                    pollutants=sorted({r["pollutant_code"] for r in result["records"]}),
                )
        except EmptyPage:
            form.add_error(
                "page", "Strona poza zakresem wyników. Zmień filtry lub wróć do strony 1."
            )
            status = 400
        except FetchError:
            context["error"] = (
                "Nie można potwierdzić integralności tej kopii. Wróć do szczegółu kanału."
            )
            status = 409
    return render(request, "prtr_search.html", context, status=status)
