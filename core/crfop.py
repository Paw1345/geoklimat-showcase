"""GET-only, snapshot-pinned CRFOP search; no collector calls."""

from urllib.parse import urlencode

from django import forms
from django.core.paginator import EmptyPage, Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.crfop_read import read_copy, validate_profile
from ingestion.models import DataChannel
from ingestion.transport import FetchError


class SearchForm(forms.Form):
    name = forms.CharField(label="Nazwa — fragment", required=False, max_length=100)
    identifier = forms.CharField(
        label="Identyfikator — fragment fop_id lub inspire_id", required=False, max_length=100
    )
    snapshot = forms.IntegerField(
        required=False, min_value=1, max_value=2**63 - 1, widget=forms.HiddenInput
    )
    page = forms.IntegerField(required=False, min_value=1, max_value=400, widget=forms.HiddenInput)

    def clean(self):
        values = super().clean()
        if any(k not in self.fields or len(self.data.getlist(k)) != 1 for k in self.data):
            raise forms.ValidationError("Nieznany lub powtórzony parametr zapytania.")
        return values


def display_rows(data, values):
    name, identifier = values["name"].casefold(), values["identifier"].casefold()
    for row in data["forms"]:
        fields = dict(zip(data["form_columns"], row["values"], strict=True))
        if name not in fields["nazwa"].casefold() or (
            identifier not in str(row["id"]) and identifier not in fields["inspire_id"].casefold()
        ):
            continue
        yield {
            "id": row["id"],
            "name": fields["nazwa"],
            "inspire_id": fields["inspire_id"],
            "created": fields["data_utworzenia"],
            "archived": fields["archiwalny"],
            "area": fields.get("powierzchnia", ""),
            "acts_count": len(row["legal_acts"]),
            "omitted_acts": max(0, len(row["legal_acts"]) - 10),
            "source_acts": row["legal_acts"],
        }


@require_GET
@never_cache
def search(request, pk):
    channel = get_object_or_404(
        DataChannel.objects.select_related("registry"),
        pk=pk,
        access_approved=True,
    )
    try:
        validate_profile(channel)
    except FetchError as exc:
        raise Http404("Profil CRFOP niedostępny") from exc
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
                page = Paginator(list(display_rows(result["data"], values)), 50).page(
                    values["page"] or 1
                )
                # Only materialize legal-act labels for the displayed page, not the whole register.
                for row in page.object_list:
                    row["acts"] = [
                        list(zip(result["data"]["act_columns"], act, strict=True))
                        for act in row.pop("source_acts")[:10]
                    ]
                params = {k: values[k] for k in ("name", "identifier")}
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
                context.update(form=SearchForm(pinned), copy=result, page=page, links=links)
        except EmptyPage:
            form.add_error("page", "Strona poza zakresem wyników. Wróć do strony 1.")
            status = 400
        except FetchError:
            context["error"] = (
                "Nie można potwierdzić integralności tej kopii. Wróć do szczegółu kanału."
            )
            status = 409
    return render(request, "crfop_search.html", context, status=status)
