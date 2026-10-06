from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ingestion.alerts import configure
from ingestion.models import AlertPreference, Notification


class PreferencesForm(forms.Form):
    enabled = forms.BooleanField(label="Alerty w aplikacji włączone", required=False, initial=True)
    cadence = forms.ChoiceField(
        label="Podsumowania",
        choices=AlertPreference._meta.get_field("cadence").choices,
        initial="daily",
    )


@require_GET
@login_required
@never_cache
def inbox(request):
    rows = Notification.objects.filter(user=request.user)
    if request.GET.get("unread") == "1":
        rows = rows.filter(read_at__isnull=True)
    return render(
        request,
        "alerts/inbox.html",
        {
            "notifications": Paginator(rows, 30).get_page(request.GET.get("page")),
            "unread": request.GET.get("unread") == "1",
            "unread_count": Notification.objects.filter(
                user=request.user, read_at__isnull=True
            ).count(),
        },
    )


@require_GET
@login_required
@never_cache
def detail(request, pk):
    notice = get_object_or_404(Notification, pk=pk, user=request.user)
    entries = notice.entries.filter(user=request.user)
    return render(
        request,
        "alerts/detail.html",
        {"notice": notice, "entries": Paginator(entries, 30).get_page(request.GET.get("page"))},
    )


@require_POST
@login_required
@never_cache
def read(request, pk):
    notice = get_object_or_404(Notification, pk=pk, user=request.user)
    Notification.objects.filter(pk=notice.pk, user=request.user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return redirect("alert_detail", pk=pk)


@require_http_methods(["GET", "POST"])
@login_required
@never_cache
def preferences(request):
    pref = AlertPreference.objects.filter(user=request.user).first()
    form = PreferencesForm(
        request.POST if request.method == "POST" else None,
        initial={"enabled": pref.enabled, "cadence": pref.cadence} if pref else None,
    )
    if request.method == "POST" and form.is_valid():
        configure(request.user, **form.cleaned_data)
        return redirect("alert_inbox")
    return render(request, "alerts/preferences.html", {"form": form})
