"""GET-only search in one validated local MWP copy; no ingestion or HTTP."""

import json
from urllib.parse import urlencode

from django import forms
from django.core.paginator import EmptyPage, Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.models import DataChannel
from ingestion.mwp_preview import decoded_row, read_copy, validate_profile
from ingestion.transport import FetchError


class SearchForm(forms.Form):
    station = forms.IntegerField(
        label="Punkt — dokładny OTWOR_ID", required=False, min_value=-(2**53), max_value=2**53
    )
    date_from = forms.DateField(
        label="Data źródłowa od (włącznie)",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="Data źródłowa do (włącznie)",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    indicator = forms.CharField(
        label="Wskaźnik chemii — fragment WSKAZNIK", required=False, max_length=100
    )
    snapshot = forms.IntegerField(
        required=False, min_value=1, max_value=2**63 - 1, widget=forms.HiddenInput
    )
    page = forms.IntegerField(required=False, min_value=1, max_value=4000, widget=forms.HiddenInput)

    def __init__(self, data, *, key):
        super().__init__(data)
        self.key = key
        if key != "chemia":
            self.fields["indicator"].widget = forms.HiddenInput()

    def clean(self):
        values = super().clean()
        if any(k not in self.fields or len(self.data.getlist(k)) != 1 for k in self.data):
            raise forms.ValidationError("Nieznany lub powtórzony parametr zapytania.")
        for field in ("date_from", "date_to"):
            value = values.get(field)
            if value and self.data.get(field) != value.isoformat():
                self.add_error(field, "Wymagany format YYYY-MM-DD.")
        start, end = values.get("date_from"), values.get("date_to")
        if start and end and start > end:
            raise forms.ValidationError("Data od nie może być późniejsza niż data do.")
        if self.key != "chemia" and values.get("indicator"):
            self.add_error("indicator", "Filtr wskaźnika dotyczy wyłącznie tabeli chemii.")
        return values


def matching_indexes(data, values):
    start = values["date_from"].isoformat() if values["date_from"] else ""
    end = values["date_to"].isoformat() if values["date_to"] else "9999-12-31"
    date_field = "DATA_POBORU" if data["table"] == "chemia" else "DATA_POMIARU"
    indicator = values["indicator"].casefold()
    station_column, date_column = (data["columns"].index(name) for name in ("OTWOR_ID", date_field))
    indicator_column = data["columns"].index("WSKAZNIK") if indicator else None
    indicator_indexes = (
        {
            i
            for i, value in enumerate(data["dictionaries"]["WSKAZNIK"])
            if indicator in value.casefold()
        }
        if indicator
        else None
    )
    # Fold each dictionary string once, then keep only matched positions, not decoded copies.
    for index, row in enumerate(data["rows"]):
        if (
            (values["station"] is None or row[station_column] == values["station"])
            and (start <= row[date_column] <= end)
            and (indicator_indexes is None or row[indicator_column] in indicator_indexes)
        ):
            yield index


def page_cells(data, indexes):
    cells, used = [], 0
    for index in indexes:
        row = []
        for value in decoded_row(data, index).values():
            text = json.dumps(value, ensure_ascii=False, allow_nan=False)
            used += len(text)
            if used > 100000:
                raise FetchError("MWP page output exceeds budget")
            row.append(text)
        cells.append(row)
    return cells


@require_GET
@never_cache
def search(request, pk):
    channel = get_object_or_404(
        DataChannel.objects.select_related("registry"), pk=pk, access_approved=True
    )
    try:
        key, _ = validate_profile(channel)
    except FetchError as exc:
        raise Http404("Profil MWP niedostępny") from exc
    form = SearchForm(request.GET, key=key)
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
                data = result["data"]
                page = Paginator(list(matching_indexes(data, values)), 50).page(values["page"] or 1)
                page.object_list = page_cells(data, page.object_list)
                params = {
                    k: values[k]
                    for k in ("station", "date_from", "date_to", "indicator")
                    if values[k] is not None and values[k] != ""
                }
                params["snapshot"] = result["snapshot"].pk
                links = {"current": "?" + urlencode({**params, "page": page.number})}
                for relation, method in (
                    ("next", "next_page_number"),
                    ("previous", "previous_page_number"),
                ):
                    if getattr(page, "has_" + relation)():
                        links[relation] = "?" + urlencode(
                            {**params, "page": getattr(page, method)()}
                        )
                pinned = request.GET.copy()
                pinned["snapshot"] = str(result["snapshot"].pk)
                pinned.pop("page", None)
                context.update(
                    form=SearchForm(pinned, key=key), copy=result, page=page, links=links
                )
        except EmptyPage:
            form.add_error("page", "Strona poza zakresem wyników. Wróć do strony 1.")
            status = 400
        except FetchError:
            context["error"] = (
                "Nie można potwierdzić integralności lub limitu tej kopii/strony. "
                "Wróć do szczegółu kanału."
            )
            status = 409
    return render(request, "mwp_search.html", context, status=status)
