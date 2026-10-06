"""Evidence-backed legislative links and append-only dated observations."""

from django.db import models
from django.utils import timezone


class LegalProcess(models.Model):
    identifier = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=1000)
    jurisdiction = models.CharField(max_length=8, choices=[("PL", "Polska"), ("EU", "UE")])
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.identifier


class LegalLink(models.Model):
    process = models.ForeignKey(LegalProcess, on_delete=models.PROTECT, related_name="documents")
    item = models.ForeignKey("Item", on_delete=models.PROTECT, related_name="legal_links")
    origin = models.CharField(
        max_length=12,
        choices=[
            ("listing", "Metadane listy"),
            ("api", "Szczegóły API"),
            ("editorial", "Ocena redakcyjna"),
        ],
    )
    evidence_url = models.URLField(max_length=1500)
    evidence = models.TextField()
    reviewer = models.CharField(max_length=150, blank=True)
    item_hash = models.CharField(max_length=64)
    evidence_hash = models.CharField(max_length=64)
    observed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["process", "item", "origin"], name="legal_link_identity"
            )
        ]

    @property
    def is_current(self):
        return self.item_hash == self.item.content_hash

    @property
    def reference_warning(self):
        import json

        from ingestion.legislation import ELI

        if self.origin == "editorial":
            return ""
        data = json.loads(self.evidence)
        value = data.get("ELI")
        if value and (not isinstance(value, str) or not ELI.fullmatch(value)):
            return "Niejednoznaczny identyfikator ELI w źródle — bez automatycznego powiązania."
        return ""


class LegalEvent(models.Model):
    KINDS = [
        ("issued", "Wydanie aktu"),
        ("published", "Ogłoszenie aktu"),
        ("effective", "Wejście w życie — data z metadanych"),
        ("process_start", "Rozpoczęcie procesu"),
        ("process_end", "Zamknięcie procesu"),
        ("stage", "Etap procesu"),
        ("consultation_end", "Termin konsultacji"),
        ("reporting_deadline", "Termin sprawozdawczy"),
    ]
    link = models.ForeignKey(LegalLink, on_delete=models.PROTECT, related_name="events")
    event_key = models.CharField(max_length=120)
    kind = models.CharField(max_length=24, choices=KINDS)
    date = models.DateField(db_index=True)
    description = models.TextField()
    evidence_url = models.URLField(max_length=1500)
    evidence = models.TextField()
    reviewer = models.CharField(max_length=150, blank=True)
    item_hash = models.CharField(max_length=64)
    observed_at = models.DateTimeField(default=timezone.now)
    superseded_at = models.DateTimeField(null=True, blank=True)
    withdrawal_note = models.TextField(blank=True)

    class Meta:
        ordering = ["date", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["link", "event_key"],
                condition=models.Q(superseded_at__isnull=True),
                name="legal_current_event",
            )
        ]

    @property
    def is_current(self):
        return self.superseded_at is None and self.item_hash == self.link.item.content_hash


class LegalRelation(models.Model):
    link = models.ForeignKey(LegalLink, on_delete=models.PROTECT, related_name="relations")
    target = models.ForeignKey(LegalProcess, on_delete=models.PROTECT, related_name="incoming")
    kind = models.CharField(max_length=40)
    evidence = models.TextField()
    item_hash = models.CharField(max_length=64)
    observed_at = models.DateTimeField(default=timezone.now)
    superseded_at = models.DateTimeField(null=True, blank=True)

    @property
    def kind_label(self):
        return (
            "projekt powiązany z opublikowanym aktem" if self.kind == "published_as" else self.kind
        )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["link", "target", "kind"],
                condition=models.Q(superseded_at__isnull=True),
                name="legal_current_relation",
            )
        ]
