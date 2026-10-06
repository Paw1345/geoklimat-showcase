from urllib.parse import urlencode

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.data_coverage import MODES
from ingestion.models import DataChannel
from ingestion.spatial_geometry import GeometryError
from ingestion.spatial_query import query, valid_bbox
from ingestion.transport import FetchError


class SpatialForm(forms.Form):
    channel = forms.ModelChoiceField(
        label="Zbiór danych",
        queryset=DataChannel.objects.filter(
            kind="geojson",
            access_approved=True,
            config__mode__in=sorted(MODES),
        ).order_by("name"),
    )
    west = forms.FloatField(label="Zachód — długość geograficzna", initial=14)
    south = forms.FloatField(label="Południe — szerokość geograficzna", initial=49)
    east = forms.FloatField(label="Wschód — długość geograficzna", initial=24.2)
    north = forms.FloatField(label="Północ — szerokość geograficzna", initial=55)
    limit = forms.IntegerField(label="Limit wyników", min_value=1, max_value=500, initial=100)
    method = forms.ChoiceField(
        label="Metoda selekcji",
        required=False,
        initial="geometry",
        choices=[
            ("geometry", "Przecięcie geometrii z prostokątem (planarne)"),
            ("envelope", "Obwiednie — zgodność ze starszym wyszukiwaniem"),
        ],
    )
    format = forms.ChoiceField(
        label="Wynik", choices=[("html", "Podgląd"), ("geojson", "Plik GeoJSON")]
    )
    copy_page = forms.IntegerField(
        label="Paczka kopii (puste = cały dostępny zakres, 1 = rozpocznij paczki)",
        required=False,
        min_value=1,
        max_value=10000,
    )
    copy_token = forms.CharField(required=False, max_length=2048, widget=forms.HiddenInput)
    result_page = forms.IntegerField(
        label="Strona trafień w paczce", required=False, min_value=1, max_value=100000
    )

    def clean(self):
        values = super().clean()
        keys = ["west", "south", "east", "north"]
        if all(k in values for k in keys):
            try:
                values["bbox"] = valid_bbox([values[k] for k in keys])
            except FetchError as exc:
                raise forms.ValidationError(
                    "Podaj poprawny prostokąt WGS84: zachód < wschód, południe < północ; "
                    "bez przejścia przez południk 180°."
                ) from exc
        return values


@require_GET
@staff_member_required(login_url="login")
@never_cache
def search(request):
    params = request.GET.copy()
    if params.get("new_scope") == "1":
        params.pop("copy_token", None)
        params.pop("new_scope", None)
        params["copy_page"] = "1"
        params["result_page"] = "1"
    form = SpatialForm(params or None)
    result, error, error_code = None, "", ""
    status = 200
    if request.GET:
        if not form.is_valid():
            status = 400
        else:
            try:
                result, payload, digest = query(
                    form.cleaned_data["channel"].pk,
                    form.cleaned_data["bbox"],
                    form.cleaned_data["limit"],
                    copy_page=form.cleaned_data.get("copy_page"),
                    copy_token=form.cleaned_data.get("copy_token", ""),
                    result_page=form.cleaned_data.get("result_page") or 1,
                    method=form.cleaned_data.get("method") or "envelope",
                )
            except GeometryError as exc:
                error_code = "spatial_geometry_" + exc.reason
                error = {
                    "topology": (
                        "W wybranych kopiach wykryto niepoprawną topologię geometrii. "
                        "Nie zwrócono częściowego wyniku i nie naprawiono danych. "
                        "Tryb obwiedni pozostaje dostępny, ale nie potwierdza topologii."
                    ),
                    "budget": (
                        "Przekroczono budżet współrzędnych dla trybu geometrii. "
                        "Rozpocznij przegląd w paczkach, jeśli czytasz cały zbiór. "
                        "Jeśli paczka też przekracza limit, pozostaje tryb obwiedni "
                        "bez potwierdzenia topologii. Mniejszy limit wyników nie "
                        "pomija obowiązkowej walidacji."
                    ),
                    "antimeridian": (
                        "Geometria przekracza południk 180° i wymaga osobnego "
                        "przejrzanego zakresu. Nie zwrócono częściowego wyniku."
                    ),
                    "operation": (
                        "Obliczenie geometrii nie powiodło się; nie zwrócono częściowego wyniku."
                    ),
                }[exc.reason]
                status = 409
            except (FetchError, DataChannel.DoesNotExist):
                error = (
                    "Kopie są niedostępne, nie przeszły weryfikacji lub przekroczono budżet. "
                    "Sprawdź pokrycie, retencję i poprawność topologii. "
                    "Tryb geometrii odrzuca też przekroczenie budżetu współrzędnych; "
                    "niczego nie pobrano ani automatycznie nie naprawiono."
                )
                status = 409
            if form.cleaned_data["format"] == "geojson":
                if error:
                    return JsonResponse(
                        {"error": error, "error_code": error_code or "spatial_unavailable"},
                        status=status,
                    )
                response = HttpResponse(payload, content_type="application/geo+json")
                response["Content-Disposition"] = (
                    'attachment; filename="geoklimat-spatial-query.geojson"'
                )
                response["X-Content-Type-Options"] = "nosniff"
                response["ETag"] = '"' + digest + '"'
                return response
    previews, links = [], {}
    if result:
        batch = result["geoklimat_query"].get("copy_batch")
        if batch:
            form.data = form.data.copy()
            form.data["copy_token"] = batch["token"]
            values = {
                key: form.cleaned_data[key].pk if key == "channel" else form.cleaned_data[key]
                for key in ("channel", "west", "south", "east", "north", "limit")
            }
            values["method"] = form.cleaned_data.get("method") or "envelope"
            base = {**values, "copy_token": batch["token"], "copy_page": batch["page"]}
            pagination = result["geoklimat_query"]["result_pagination"]
            form.data["result_page"] = pagination["page"]
            links = {
                "next": "?" + urlencode({**base, "format": "html", "copy_page": batch["page"] + 1}),
                "previous": "?"
                + urlencode({**base, "format": "html", "copy_page": batch["page"] - 1}),
                "geojson": "?"
                + urlencode({**base, "format": "geojson", "result_page": pagination["page"]}),
                "new": "?" + urlencode({**values, "format": "html", "copy_page": 1}),
                "result_next": "?"
                + urlencode({**base, "format": "html", "result_page": pagination["page"] + 1}),
                "result_previous": "?"
                + urlencode({**base, "format": "html", "result_page": pagination["page"] - 1}),
            }
        previews = [
            {
                "id": f["id"],
                "properties": [
                    (str(k)[:100], str(v)[:200]) for k, v in list(f["properties"].items())[:8]
                ],
            }
            for f in result["features"]
        ]
    return render(
        request,
        "spatial_search.html",
        {
            "form": form,
            "result": result,
            "previews": previews,
            "error": error,
            "links": links,
        },
        status=status,
    )
