from django import forms
from django.contrib import admin
from django.utils import timezone

from ingestion.models import (
    Classification,
    DataChannel,
    DataHarvest,
    DataSnapshot,
    DownloadJob,
    DuplicateCandidate,
    IngestionRun,
    Item,
    ItemVersion,
    Publication,
    RegistryEntry,
    RegistryRelease,
    Source,
    Topic,
)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        needs_audit = not change or bool({"url", "adapter", "config"} & set(form.changed_data))
        if needs_audit:
            obj.enabled = False
            obj.activation_approved = False
            obj.etag = obj.last_modified = ""
            obj.last_audit = {}
            obj.audited_at = None
            self.message_user(
                request, "Konfiguracja zmieniona. Wykonaj audyt przed włączeniem źródła."
            )
        super().save_model(request, obj, form, change)

    list_display = (
        "name",
        "organization",
        "adapter",
        "enabled",
        "last_success_at",
        "failures",
        "next_fetch_at",
    )
    list_filter = ("enabled", "adapter", "topics")
    search_fields = ("name", "organization", "url")
    filter_horizontal = ("topics",)
    readonly_fields = (
        "last_audit",
        "audited_at",
        "last_success_at",
        "failures",
        "etag",
        "last_modified",
    )


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from ingestion.models import LegalEvent, LegalLink, LegalProcess, LegalRelation  # noqa: E402

for legal_model in (LegalProcess, LegalLink, LegalEvent, LegalRelation):
    admin.site.register(legal_model, ReadOnlyAdmin)


@admin.register(Item)
class ItemAdmin(ReadOnlyAdmin):
    list_display = ("title", "source", "published_at", "first_seen_at")
    list_filter = ("source",)
    search_fields = ("title", "url")


@admin.register(IngestionRun)
class RunAdmin(ReadOnlyAdmin):
    list_display = ("source", "started_at", "status", "fetched", "created", "updated", "rejected")
    list_filter = ("status", "source")


@admin.register(ItemVersion)
class VersionAdmin(ReadOnlyAdmin):
    list_display = ("item", "number", "observed_at", "origin")
    list_filter = ("origin",)


@admin.register(RegistryEntry)
class RegistryEntryAdmin(ReadOnlyAdmin):
    list_display = (
        "registry_id",
        "organization",
        "name",
        "priority",
        "technical_status",
        "integration_state",
    )
    list_filter = ("priority", "country", "level", "technical_status", "integration_state")
    search_fields = ("organization", "name")


@admin.register(RegistryRelease)
class RegistryReleaseAdmin(ReadOnlyAdmin):
    list_display = ("filename", "sha256", "imported_at")


@admin.register(DataChannel)
class DataChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "region", "access_approved", "enabled", "last_status")
    list_filter = ("kind", "region", "enabled", "last_status")
    readonly_fields = ("last_status", "last_error", "checked_at")

    def save_model(self, request, obj, form, change):
        if not change or {"url", "kind", "config", "region"} & set(form.changed_data):
            obj.enabled = False
            obj.access_approved = False
            obj.last_status = "needs_audit"
            self.message_user(request, "Wymagany audyt oraz osobne zatwierdzenie dostępu.")
        super().save_model(request, obj, form, change)


@admin.register(DataSnapshot)
class DataSnapshotAdmin(ReadOnlyAdmin):
    list_display = ("id", "channel", "fetched_at", "size_bytes", "expires_at")
    exclude = ("payload",)

    def get_queryset(self, request):
        return super().get_queryset(request).defer("payload")


@admin.register(DataHarvest)
class DataHarvestAdmin(ReadOnlyAdmin):
    list_display = ("id", "channel", "status", "pages", "records", "started_at", "error")
    list_filter = ("status", "channel")


@admin.register(DownloadJob)
class DownloadJobAdmin(ReadOnlyAdmin):
    list_display = ("id", "channel", "status", "offset", "total_bytes", "expires_at", "error")
    list_filter = ("status", "channel")


@admin.register(Publication)
class PublicationAdmin(ReadOnlyAdmin):
    list_display = ("id", "primary_item", "primary_confirmed")


@admin.register(DuplicateCandidate)
class CandidateAdmin(ReadOnlyAdmin):
    list_display = ("id", "left", "right", "score", "status", "reviewed_by")
    list_filter = ("status",)


class ClassificationForm(forms.ModelForm):
    class Meta:
        model = Classification
        fields = ("manual_state", "review_note")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("manual_state") and not cleaned.get("review_note", "").strip():
            self.add_error("review_note", "Podaj uzasadnienie decyzji redakcyjnej.")
        return cleaned


@admin.register(Classification)
class ClassificationAdmin(admin.ModelAdmin):
    form = ClassificationForm
    list_display = ("item", "auto_state", "manual_state", "kind", "score", "rule_version")
    list_filter = ("auto_state", "manual_state", "kind")
    search_fields = ("item__title", "review_note")
    readonly_fields = (
        "item",
        "topics",
        "kind",
        "score",
        "auto_state",
        "evidence",
        "rule_version",
        "item_revision",
        "classified_at",
        "reviewed_by",
        "reviewed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.reviewed_by = request.user.get_username()[:150]
        obj.reviewed_at = timezone.now()
        # Do not overwrite automatic fields if ingestion ran while the form was open.
        obj.save(update_fields=["manual_state", "review_note", "reviewed_by", "reviewed_at"])
