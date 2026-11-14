"""GET-only filtering of a single validated, pinned EU funding copy."""

from urllib.parse import urlencode

from django import forms
from django.core.paginator import EmptyPage, Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.funding_copy_read import read_copy, validate_profile
from ingestion.models import DataChannel
from ingestion.transport import FetchError

STATE_LABELS = {
    "open": "Otwarte w chwili pobrania",
    "forthcoming": "Zapowiedziane w chwili pobrania",
    "later_deadline_only": "Tylko późniejszy termin — uprawnienia niesprawdzone",
    "deadline_passed": "Po terminie w chwili pobrania",
}


class SearchForm(forms.Form):
    identifier = forms.CharField(
        label="Identyfikator tematu — fragment", required=False, max_length=200
    )
    title = forms.CharField(label="Tytuł — fragment", required=False, max_length=200)
    state = forms.ChoiceField(
        label="Zapisany stan w chwili pobrania",
        required=False,
        choices=[("", "Wszystkie zapisane stany"), *STATE_LABELS.items()],
    )
    snapshot = forms.IntegerField(
        required=False, min_value=1, max_value=2**63 - 1, widget=forms.HiddenInput
    )
    page = forms.IntegerField(required=False, min_value=1, max_value=100, widget=forms.HiddenInput)

    def clean(self):
        values = super().clean()
        if set(self.data) - set(self.fields) or any(
            len(self.data.getlist(k)) != 1 for k in self.data
        ):
            raise forms.ValidationError("Nieznany lub powtórzony parametr zapytania.")
        return values


def matching_rows(rows, values):
    identifier, title, state = (
        values["identifier"].casefold(),
        values["title"].casefold(),
        values["state"],
    )
    return [
        row
        for row in rows
        if identifier in row["identifier"].casefold()
        and title in row["title"].casefold()
        and (not state or row["state"] == state)
    ]


@require_GET
@never_cache
def search(request, pk):
    channel = get_object_or_404(
        DataChannel.objects.select_related("registry"), pk=pk, access_approved=True
    )
    try:
        validate_profile(channel)
    except FetchError as exc:
        raise Http404("Profil naborów niedostępny") from exc
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
                page = Paginator(matching_rows(result["data"]["records"], values), 50).page(
                    values["page"] or 1
                )
                # Bound output independently of the validated whole-copy input budget.
                if sum(len(str(value)) for row in page for value in row.values()) > 150000:
                    raise FetchError("Funding page text exceeds budget")
                params = {k: values[k] for k in ("identifier", "title", "state") if values[k]}
                params["snapshot"] = result["snapshot"].pk
                links = {"current": "?" + urlencode({**params, "page": page.number})}
                for relation in ("next", "previous"):
                    if getattr(page, "has_" + relation)():
                        links[relation] = "?" + urlencode(
                            {**params, "page": getattr(page, relation + "_page_number")()}
                        )
                pinned = request.GET.copy()
                pinned["snapshot"] = str(result["snapshot"].pk)
                pinned.pop("page", None)
                context.update(copy=result, page=page, links=links, form=SearchForm(pinned))
        except EmptyPage:
            form.add_error("page", "Strona poza zakresem wyników. Wróć do strony 1.")
            status = 400
        except FetchError:
            context["error"] = (
                "Nie można potwierdzić integralności lub limitu tej kopii. "
                "Wróć do szczegółu kanału."
            )
            status = 409
    return render(request, "funding_search_local.html", context, status=status)
