from django import forms
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Max, Q
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ingestion.alerts import set_watch
from ingestion.changes import (
    assess,
    decide_document,
    require_editor,
    thread_items,
    thread_relations,
)
from ingestion.legislation import current_events
from ingestion.models import AIDraft, ChangeThread, Item, LegalEvent, ThreadImpact, ThreadWatch


class TitleForm(forms.Form):
    title = forms.CharField(label="Nazwa wątku", max_length=1000)


class DocumentForm(forms.Form):
    included = forms.TypedChoiceField(
        label="Decyzja",
        choices=[("yes", "Dołącz / potwierdź dokument"), ("no", "Wyłącz dokument")],
        coerce=lambda value: value == "yes",
    )
    reason = forms.CharField(
        label="Uzasadnienie powiązania lub wyłączenia",
        min_length=10,
        max_length=2000,
        widget=forms.Textarea,
    )
    expected_hash = forms.CharField(max_length=64, widget=forms.HiddenInput)


class DocumentThreadForm(TitleForm):
    reason = forms.CharField(
        label="Dlaczego dokument rozpoczyna wątek?",
        min_length=10,
        max_length=2000,
        widget=forms.Textarea,
    )
    expected_hash = forms.CharField(max_length=64, widget=forms.HiddenInput)


class ImpactForm(forms.Form):
    change = forms.CharField(
        label="Co się zmienia — ocena redakcyjna",
        min_length=10,
        max_length=3000,
        widget=forms.Textarea,
    )
    audience = forms.CharField(
        label="Kogo dotyczy", min_length=10, max_length=3000, widget=forms.Textarea
    )
    kinds = forms.MultipleChoiceField(
        label="Rodzaj wpływu", choices=ThreadImpact.KINDS, widget=forms.CheckboxSelectMultiple
    )
    evidence_url = forms.URLField(
        label="Adres HTTPS dowodu", max_length=1500, assume_scheme="https"
    )
    evidence = forms.CharField(
        label="Fragment / sekcja źródła uzasadniająca ocenę",
        min_length=10,
        max_length=3000,
        widget=forms.Textarea,
    )
    expected_hash = forms.CharField(max_length=64, widget=forms.HiddenInput)
    previous_id = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


@require_GET
@never_cache
def catalog(request):
    query = request.GET.get("q", "").strip()[:200]
    rows = (
        ChangeThread.objects.select_related("process")
        .annotate(latest=Coalesce(Max("process__documents__item__published_at"), "created_at"))
        .order_by(F("latest").desc(), "-pk")
    )
    if query:
        rows = rows.filter(
            Q(title__icontains=query)
            | Q(process__title__icontains=query)
            | Q(process__identifier__icontains=query)
        )
    watching = request.GET.get("watching") == "1"
    if watching:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url="login")
        rows = rows.filter(watches__user=request.user, watches__active=True)
    return render(
        request,
        "changes/catalog.html",
        {
            "threads": Paginator(rows, 30).get_page(request.GET.get("page")),
            "q": query,
            "watching": watching,
        },
    )


@require_GET
@never_cache
def detail(request, pk):
    thread = get_object_or_404(ChangeThread.objects.select_related("process"), pk=pk)
    items = thread_items(thread)
    impact = (
        thread.impacts.filter(superseded_at__isnull=True).select_related("item", "author").first()
    )
    impact_current = bool(
        impact
        and impact.item_hash == impact.item.content_hash
        and items.filter(pk=impact.item_id).exists()
    )
    events = current_events(
        LegalEvent.objects.filter(
            link__item_id__in=items.values("pk"),
            kind__in=["effective", "consultation_end", "reporting_deadline"],
        ).select_related("link__item", "link__process")
    )
    return render(
        request,
        "changes/detail.html",
        {
            "thread": thread,
            "items": Paginator(items, 30).get_page(request.GET.get("page")),
            "relations": thread_relations(thread),
            "events": events[:100],
            "impact": impact,
            "impact_current": impact_current,
            "impact_ai": bool(
                impact and AIDraft.objects.filter(impact=impact, state="approved").exists()
            ),
            "history": Paginator(
                thread.impacts.select_related("item", "author").order_by("-pk"), 10
            ).get_page(request.GET.get("history")),
            "decisions": Paginator(
                thread.decisions.select_related("item", "author").order_by("-pk"), 10
            ).get_page(request.GET.get("decisions")),
            "watching": request.user.is_authenticated
            and ThreadWatch.objects.filter(user=request.user, thread=thread, active=True).exists(),
        },
    )


@require_POST
@login_required(login_url="login")
@never_cache
def watch(request, pk):
    thread = get_object_or_404(ChangeThread, pk=pk)
    action = request.POST.get("action")
    if action not in {"follow", "unfollow"}:
        return HttpResponseBadRequest("Nieprawidłowa akcja")
    set_watch(request.user, thread, action == "follow")
    return redirect("change_detail", pk=pk)


@require_http_methods(["GET", "POST"])
@login_required(login_url="login")
@never_cache
def create(request):
    require_editor(request.user)
    item = None
    item_id = request.GET.get("item")
    if item_id is not None:
        if not item_id.isdecimal() or len(item_id) > 12:
            return HttpResponseBadRequest("Nieprawidłowe ID dokumentu.")
        item = get_object_or_404(Item, pk=int(item_id))
    form_type = DocumentThreadForm if item else TitleForm
    initial = {"title": item.title, "expected_hash": item.content_hash} if item else {}
    form = form_type(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                thread = ChangeThread.objects.create(title=form.cleaned_data["title"])
                if item:
                    decide_document(
                        thread,
                        item,
                        request.user,
                        True,
                        form.cleaned_data["reason"],
                        form.cleaned_data["expected_hash"],
                    )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("change_detail", pk=thread.pk)
    return render(
        request,
        "changes/form.html",
        {"form": form, "item": item, "heading": "Nowy wątek redakcyjny"},
    )


@require_http_methods(["GET", "POST"])
@login_required(login_url="login")
@never_cache
def edit(request, pk, item_pk, mode):
    require_editor(request.user)
    thread = get_object_or_404(ChangeThread, pk=pk)
    item = get_object_or_404(Item, pk=item_pk)
    if mode not in {"document", "impact"}:
        raise Http404
    initial = {"expected_hash": item.content_hash}
    if mode == "impact":
        if not thread_items(thread).filter(pk=item_pk).exists():
            raise Http404
        old = thread.impacts.filter(superseded_at__isnull=True).first()
        initial.update(previous_id=old.pk if old else 0, evidence_url=item.url)
        if old:
            initial.update(
                {
                    key: getattr(old, key)
                    for key in ("change", "audience", "kinds", "evidence_url", "evidence")
                }
            )
        form_type = ImpactForm
    else:
        form_type = DocumentForm
    form = form_type(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            if mode == "document":
                decide_document(thread, item, request.user, **form.cleaned_data)
            else:
                assess(thread, item, request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("change_detail", pk=pk)
    return render(
        request,
        "changes/form.html",
        {
            "form": form,
            "thread": thread,
            "item": item,
            "heading": "Ocena wpływu z dowodem" if mode == "impact" else "Powiązanie dokumentu",
        },
    )


@require_GET
@login_required(login_url="login")
def choose_document(request, pk):
    require_editor(request.user)
    get_object_or_404(ChangeThread, pk=pk)
    value = request.GET.get("item", "")
    if not value.isdecimal() or len(value) > 12:
        return HttpResponseBadRequest("Podaj numeryczne ID dokumentu z jego strony historii.")
    return redirect("change_document", pk=pk, item_pk=int(value))
