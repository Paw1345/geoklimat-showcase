from django.contrib.postgres.indexes import GinIndex
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from ingestion.ai_models import AIDraft  # noqa: F401
from ingestion.alert_models import AlertPreference, Notification  # noqa: F401
from ingestion.change_models import (  # noqa: F401
    ChangeThread,
    ThreadDocument,
    ThreadImpact,
    ThreadWatch,
)
from ingestion.legal_models import LegalEvent, LegalLink, LegalProcess, LegalRelation  # noqa: F401
from ingestion.search import document_vector


class Topic(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    keywords = models.JSONField(default=list)

    def __str__(self):
        return self.name


class RegistryRelease(models.Model):
    filename = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64, unique=True)
    imported_at = models.DateTimeField(default=timezone.now)
    sheets = models.JSONField()


class RegistryEntry(models.Model):
    registry_id = models.PositiveIntegerField(unique=True)
    release = models.ForeignKey(RegistryRelease, on_delete=models.PROTECT)
    organization = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=60, db_index=True)
    level = models.CharField(max_length=100, db_index=True)
    priority = models.CharField(max_length=2, db_index=True)
    technical_status = models.CharField(max_length=40, db_index=True)
    master = models.JSONField()
    technical = models.JSONField()
    queue = models.JSONField(default=dict)
    integration_state = models.CharField(max_length=30, default="needs_endpoint", db_index=True)
    integration_note = models.TextField(blank=True)
    sources = models.ManyToManyField("Source", blank=True, related_name="registry_entries")

    class Meta:
        ordering = ["priority", "registry_id"]

    def __str__(self):
        return f"{self.registry_id}: {self.organization} — {self.name}"


class Source(models.Model):
    class Adapter(models.TextChoices):
        HTML = "html", "Lista HTML"
        FEED = "feed", "RSS / Atom"
        ELI = "eli", "ELI JSON API"
        PDF = "pdf", "Dokument PDF"
        WATCH = "watch", "Obserwowana strona"
        WEB = "web", "Lista HTML / monitoring strony"
        JSON = "json", "Metadane JSON"
        OGC = "ogc", "OGC GetCapabilities"
        STAC = "stac", "Kolekcje STAC"

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=160)
    organization = models.CharField(max_length=120)
    url = models.URLField(max_length=1500)
    adapter = models.CharField(max_length=12, choices=Adapter.choices)
    config = models.JSONField(default=dict, blank=True)
    topics = models.ManyToManyField(Topic, blank=True)
    language = models.CharField(max_length=5, default="pl")
    priority = models.PositiveSmallIntegerField(default=1)
    enabled = models.BooleanField(default=False)
    interval_minutes = models.PositiveIntegerField(
        default=60, validators=[MinValueValidator(15), MaxValueValidator(10080)]
    )
    owner = models.CharField(max_length=100, default="Redakcja GeoKlimat")
    scope = models.TextField(blank=True)
    reuse_notes = models.TextField(blank=True)
    last_audit = models.JSONField(default=dict, blank=True)
    audited_at = models.DateTimeField(null=True, blank=True)
    next_fetch_at = models.DateTimeField(default=timezone.now)
    last_success_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0)
    activation_approved = models.BooleanField(default=True)
    etag = models.CharField(max_length=500, blank=True)
    last_modified = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["priority", "name"]
        indexes = [models.Index(fields=["enabled", "next_fetch_at"])]

    def __str__(self):
        return self.name


class DataChannel(models.Model):
    registry = models.ForeignKey(
        RegistryEntry, on_delete=models.PROTECT, related_name="data_channels"
    )
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    kind = models.CharField(
        max_length=16,
        choices=[
            ("imgw", "Pomiary IMGW"),
            ("imgw_daily", "IMGW — archiwum dobowe SYNOP"),
            ("gios", "Pomiary GIOŚ"),
            ("geojson", "GeoJSON / WFS"),
            ("stac", "Sceny STAC"),
            ("file", "Plik na żądanie"),
        ],
    )
    url = models.URLField(max_length=1500)
    config = models.JSONField(default=dict, blank=True)
    region = models.CharField(max_length=8, choices=[("PL", "Polska"), ("EU", "Unia Europejska")])
    attribution = models.TextField()
    access_approved = models.BooleanField(default=False)
    enabled = models.BooleanField(default=False)
    interval_minutes = models.PositiveIntegerField(default=60, validators=[MinValueValidator(15)])
    next_fetch_at = models.DateTimeField(default=timezone.now)
    last_status = models.CharField(max_length=24, default="not_tested")
    last_error = models.CharField(max_length=300, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class DataHarvest(models.Model):
    channel = models.ForeignKey(DataChannel, on_delete=models.PROTECT, related_name="harvests")
    config_hash = models.CharField(max_length=64)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default="running")
    start_url = models.TextField()
    next_url = models.TextField()
    pages = models.PositiveIntegerField(default=0)
    records = models.PositiveBigIntegerField(default=0)
    size_bytes = models.PositiveBigIntegerField(default=0)
    page_fingerprints = models.JSONField(default=list)
    visited_urls = models.JSONField(default=list)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-started_at", "-pk"]


class DownloadJob(models.Model):
    channel = models.ForeignKey(DataChannel, on_delete=models.PROTECT, related_name="downloads")
    url = models.TextField()
    config_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default="queued")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    next_attempt_at = models.DateTimeField(default=timezone.now)
    offset = models.PositiveBigIntegerField(default=0)
    total_bytes = models.PositiveBigIntegerField(null=True)
    etag = models.CharField(max_length=500, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class DataSnapshot(models.Model):
    download_job = models.ForeignKey(
        DownloadJob, null=True, blank=True, on_delete=models.PROTECT, related_name="chunks"
    )
    channel = models.ForeignKey(DataChannel, on_delete=models.PROTECT, related_name="snapshots")
    fetched_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    media_type = models.CharField(max_length=100)
    payload = models.BinaryField()
    summary = models.JSONField(default=dict)

    class Meta:
        ordering = ["-fetched_at", "-pk"]


class Item(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="items")
    external_id = models.CharField(max_length=64)
    title = models.CharField(max_length=1000)
    url = models.URLField(max_length=1500)
    summary = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    content_hash = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict)
    canonical_url = models.URLField(max_length=1500, blank=True, db_index=True)
    normalized_title = models.CharField(max_length=1000, blank=True)
    publisher = models.CharField(max_length=120, blank=True)
    authors = models.JSONField(default=list, blank=True)
    date_precision = models.CharField(max_length=12, default="unknown")
    duplicate_key = models.CharField(max_length=64, blank=True, db_index=True)
    revision = models.PositiveIntegerField(default=0)
    publication = models.ForeignKey(
        "Publication", null=True, blank=True, on_delete=models.PROTECT, related_name="items"
    )

    class Meta:
        ordering = ["-first_seen_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="unique_source_item")
        ]
        indexes = [
            models.Index(fields=["-first_seen_at"]),
            GinIndex(document_vector(), name="item_document_search_gin"),
        ]

    def __str__(self):
        return self.title

    @property
    def has_description(self):
        from ingestion.description_quality import has_description

        return has_description(self.summary)


class Classification(models.Model):
    STATES = [("relevant", "Trafne"), ("review", "Do oceny"), ("excluded", "Wykluczone")]
    item = models.OneToOneField(Item, on_delete=models.PROTECT, related_name="classification")
    topics = models.JSONField(default=list)
    kind = models.CharField(max_length=40, default="Komunikat")
    score = models.PositiveSmallIntegerField(default=0)
    auto_state = models.CharField(max_length=12, choices=STATES, db_index=True)
    evidence = models.JSONField(default=list)
    rule_version = models.CharField(max_length=30)
    item_revision = models.PositiveIntegerField(default=0)
    classified_at = models.DateTimeField(default=timezone.now)
    manual_state = models.CharField(max_length=12, choices=STATES, blank=True)
    review_note = models.TextField(blank=True)
    reviewed_by = models.CharField(max_length=150, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    @property
    def state_label(self):
        return dict(self.STATES).get(self.manual_state or self.auto_state)

    @property
    def topic_labels(self):
        from ingestion.catalog import TOPICS

        return list(dict.fromkeys(TOPICS.get(slug, (slug, []))[0] for slug in self.topics))


class IngestionRun(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="runs")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=24, default="running")
    fetched = models.PositiveIntegerField(default=0)
    created = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    unchanged = models.PositiveIntegerField(default=0)
    rejected = models.PositiveIntegerField(default=0)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    response_hash = models.CharField(max_length=64, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-started_at"]


class Publication(models.Model):
    created_at = models.DateTimeField(default=timezone.now)
    primary_item = models.ForeignKey(
        Item,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="represented_publications",
    )
    primary_confirmed = models.BooleanField(default=False)
    primary_note = models.CharField(max_length=300, blank=True)

    def __str__(self):
        return f"Publikacja {self.pk}"


class ItemVersion(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    observed_at = models.DateTimeField(default=timezone.now)
    origin = models.CharField(max_length=16, default="ingestion")
    snapshot = models.JSONField()
    changed_fields = models.JSONField(default=list)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["item", "number"], name="unique_item_version")
        ]


class DuplicateCandidate(models.Model):
    left = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="candidate_left")
    right = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="candidate_right")
    score = models.FloatField()
    status = models.CharField(
        max_length=12,
        default="pending",
        choices=[("pending", "Do oceny"), ("accepted", "Połączone"), ("rejected", "Odrzucone")],
    )
    created_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.CharField(max_length=150, blank=True)
    left_revision = models.PositiveIntegerField(default=0)
    right_revision = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["left", "right"], name="unique_duplicate_pair")
        ]
