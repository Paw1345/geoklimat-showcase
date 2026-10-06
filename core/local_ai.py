from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from core.changes import ImpactForm
from ingestion.changes import require_editor, thread_items
from ingestion.local_ai import current, enqueue, review
from ingestion.models import AIDraft, ChangeThread


@require_POST
@login_required
@never_cache
def create(request, pk, item_pk):
    require_editor(request.user)
    thread = get_object_or_404(ChangeThread, pk=pk)
    item = get_object_or_404(thread_items(thread), pk=item_pk)
    try:
        draft = enqueue(thread, item, request.user)
    except ValidationError as exc:
        return render(
            request, "ai/error.html", {"error": exc.messages, "thread": thread}, status=400
        )
    return redirect("ai_draft", pk=draft.pk)


@require_http_methods(["GET", "POST"])
@login_required
@never_cache
def detail(request, pk):
    require_editor(request.user)
    draft = get_object_or_404(
        AIDraft.objects.select_related("thread", "item", "created_by", "reviewed_by"), pk=pk
    )
    old = draft.thread.impacts.filter(superseded_at__isnull=True).first()
    initial = {
        "change": draft.output.get("change", ""),
        "audience": draft.output.get("audience", ""),
        "kinds": draft.output.get("kinds", []),
        "evidence_url": draft.item.url,
        "evidence": draft.output.get("quote", ""),
        "expected_hash": draft.item_hash,
        "previous_id": old.pk if old else 0,
    }
    form = ImpactForm(request.POST if request.method == "POST" else None, initial=initial)
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "reject":
                review(draft, request.user, action)
                return redirect("ai_draft", pk=pk)
            if action == "approve" and form.is_valid():
                review(draft, request.user, action, form.cleaned_data)
                return redirect("ai_draft", pk=pk)
            if action not in {"approve", "reject"}:
                error = "Nieprawidłowa akcja."
        except ValidationError as exc:
            error = " ".join(exc.messages)
    return render(
        request,
        "ai/detail.html",
        {"draft": draft, "current": current(draft), "form": form, "error": error},
    )


@require_http_methods(["GET"])
@login_required
@never_cache
def catalog(request):
    require_editor(request.user)
    return render(
        request,
        "ai/catalog.html",
        {
            "enabled": settings.LOCAL_AI_ENABLED,
            "drafts": Paginator(AIDraft.objects.select_related("thread", "item"), 30).get_page(
                request.GET.get("page")
            ),
        },
    )
