import hashlib

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.inspire_codes import CONFIG, SCHEMES, SLUG, URL
from ingestion.models import Item, Source


class VocabularyForm(forms.Form):
    scheme = forms.ChoiceField(
        label="Słownik",
        required=False,
        choices=[("", "Wszystkie osiem słowników"), *SCHEMES.items()],
    )
    q = forms.CharField(label="Nazwa PL lub EN", required=False, max_length=100)
    format = forms.ChoiceField(
        label="Format",
        required=False,
        choices=[("", "Podgląd"), ("json", "JSON")],
    )


@require_GET
@staff_member_required(login_url="login")
@never_cache
def catalog(request):
    form = VocabularyForm(request.GET or None)
    if request.GET and not form.is_valid():
        return render(request, "vocabularies.html", {"form": form}, status=400)
    filters = form.cleaned_data if request.GET else {}
    source = Source.objects.filter(
        slug=SLUG,
        adapter="json",
        url=URL,
        config=CONFIG,
        activation_approved=True,
    ).first()
    items = Item.objects.filter(source=source, metadata__inspire_code_value=True).order_by("url")
    if filters.get("scheme"):
        items = items.filter(metadata__scheme_uri=filters["scheme"])
    if filters.get("q"):
        from django.db.models import Q

        items = items.filter(
            Q(metadata__label_pl__icontains=filters["q"])
            | Q(metadata__label_en__icontains=filters["q"])
        )
    if filters.get("format") == "json":
        rows = list(items.values("pk", "title", "url", "metadata", "content_hash")[:1001])
        if len(rows) > 1000:
            return JsonResponse({"error": "Przekroczony limit eksportu."}, status=409)
        # Explicit allowlist; never serialize administrator/source configuration or owners.
        records = [
            {
                "item_id": row["pk"],
                "uri": row["url"],
                "content_hash": row["content_hash"],
                **{
                    key: row["metadata"].get(key)
                    for key in [
                        "scheme_uri",
                        "scheme_label",
                        "label_pl",
                        "label_en",
                        "display_language",
                        "definition_raw",
                        "definition_language",
                        "code_status_verified",
                    ]
                },
            }
            for row in rows
        ]
        response = JsonResponse(
            {
                "records": records,
                "count": len(records),
                "selected_schemes": list(SCHEMES),
                "filters": {"scheme": filters.get("scheme", ""), "q": filters.get("q", "")},
                "as_of": timezone.now().isoformat(),
                "source_url": URL,
                "last_successful_import": source.last_success_at.isoformat()
                if source and source.last_success_at
                else None,
                "full_registry_verified": False,
                "current_legal_status_verified": False,
            },
            json_dumps_params={"ensure_ascii": False},
        )
        response["Content-Disposition"] = 'attachment; filename="geoklimat-inspire-codes.json"'
        response["X-Content-Type-Options"] = "nosniff"
        if len(response.content) > 5 * 1024 * 1024:
            return JsonResponse({"error": "Przekroczony budżet eksportu."}, status=409)
        response["ETag"] = '"' + hashlib.sha256(response.content).hexdigest() + '"'
        return response
    return render(
        request,
        "vocabularies.html",
        {
            "form": form,
            "source": source,
            "items": Paginator(items.defer("summary"), 50).get_page(request.GET.get("page")),
            "filters": filters,
        },
    )
