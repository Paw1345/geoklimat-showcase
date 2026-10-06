from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.spatial_query import valid_bbox
from ingestion.stac_coverage import aware
from ingestion.stac_scenes import query
from ingestion.transport import FetchError


class ScenesForm(forms.Form):
    channel = forms.IntegerField(min_value=1, max_value=2**31 - 1, label="Kanał STAC")
    harvest = forms.IntegerField(min_value=1, max_value=2**31 - 1, label="Przebieg")
    start = forms.IntegerField(min_value=1, max_value=10000, initial=1, label="Pierwsza strona")
    limit = forms.IntegerField(min_value=1, max_value=250, initial=100, label="Limit wyników")
    scene = forms.CharField(required=False, max_length=200, label="Dokładny identyfikator sceny")
    since = forms.CharField(required=False, max_length=40, label="Od (ISO 8601 ze strefą)")
    until = forms.CharField(required=False, max_length=40, label="Do (ISO 8601 ze strefą)")
    bbox = forms.CharField(required=False, max_length=160, label="BBox WGS84: W,S,E,N")
    format = forms.ChoiceField(choices=[("html", "HTML"), ("geojson", "GeoJSON")], initial="html")

    def clean(self):
        values = super().clean()
        for key in ("since", "until"):
            raw = values.get(key)
            if raw:
                values[key] = aware(raw)
                if values[key] is None:
                    self.add_error(key, "Podaj datę i czas ISO 8601 z jawnie określoną strefą.")
            else:
                values[key] = None
        if values.get("since") and values.get("until") and values["since"] > values["until"]:
            self.add_error("until", "Koniec musi przypadać nie wcześniej niż początek.")
        raw = values.get("bbox")
        try:
            values["bbox"] = valid_bbox([float(x) for x in raw.split(",")]) if raw else None
        except (ValueError, FetchError):
            self.add_error(
                "bbox", "Podaj cztery poprawne współrzędne WGS84, bez przekroczenia 180°."
            )
        return values


@staff_member_required(login_url="login")
@require_GET
@never_cache
def catalog(request):
    params = request.GET.copy()
    if params:
        for key, default in (("start", "1"), ("limit", "100"), ("format", "html")):
            if key not in params:
                params[key] = default
    form = ScenesForm(params or None)
    result, error, status = None, "", 200
    if params:
        if not form.is_valid():
            status = 400
        else:
            v = form.cleaned_data
            try:
                result, payload, digest = query(
                    v["channel"],
                    v["harvest"],
                    v["start"],
                    v["limit"],
                    v["scene"],
                    v["since"],
                    v["until"],
                    v["bbox"],
                )
            except (FetchError, ObjectDoesNotExist):
                error = (
                    "Paczka niedostępna: zakres, retencja, integralność lub limit. "
                    "Niczego nie pobrano."
                )
                status = 409
            if v["format"] == "geojson" and not error:
                response = HttpResponse(payload, content_type="application/geo+json")
                response["Content-Disposition"] = (
                    'attachment; filename="geoklimat-stac-scenes.geojson"'
                )
                response["ETag"] = '"' + digest + '"'
                response["X-Content-Type-Options"] = "nosniff"
                return response
    if params.get("format") == "geojson" and status != 200:
        response = JsonResponse(
            {"error": error or "Niepoprawne parametry katalogu."}, status=status
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response
    links = {}
    if result:
        export = params.copy()
        export["format"] = "geojson"
        links["export"] = "?" + export.urlencode()
        next_page = result["geoklimat"]["verification"]["next_start_page"]
        if next_page:
            following = params.copy()
            following["start"] = str(next_page)
            following["format"] = "html"
            links["next"] = "?" + following.urlencode()
    return render(
        request,
        "stac_scenes.html",
        {"form": form, "result": result, "error": error, "links": links},
        status=status,
    )
