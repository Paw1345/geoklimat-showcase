"""Marine audit history and profile epochs.

Audits append records (``append_audit``), activation checks the newest record
(``latest_audit_matches``) and ``configure_marine_partial --apply`` bumps the generation.
"""

import hmac
import json
import re

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models, transaction

from ingestion.marine import CONFIG, PARTIAL_CONFIG, URL
from ingestion.marine_audit import marine_policy_hash

MAX_AUDIT_BYTES = 2 * 1024 * 1024
PROFILES = {"strict": CONFIG, "partial": PARTIAL_CONFIG}


def require_marine_source(instance):
    try:
        source = instance.source
    except ObjectDoesNotExist as exc:
        raise ValidationError("Marine history requires a source") from exc
    if source.slug != "registry-195" or source.adapter != "stac" or source.url != URL:
        raise ValidationError("Marine history requires the reviewed Marine source")


class MarineProfileState(models.Model):
    source = models.OneToOneField(
        "ingestion.Source", on_delete=models.PROTECT, related_name="marine_profile_state"
    )
    profile = models.CharField(
        max_length=16, default="strict", choices=[("strict", "strict"), ("partial", "partial")]
    )
    generation = models.PositiveBigIntegerField(default=0)

    def clean(self):
        super().clean()
        require_marine_source(self)
        if self.profile not in PROFILES or type(self.generation) is not int or self.generation < 0:
            raise ValidationError("Invalid Marine profile state")

    def save(self, *args, **kwargs):
        self.clean()
        if self._state.adding and (self.generation != 0 or self.pk is not None):
            raise ValidationError("New Marine profile state must start at generation zero")
        with transaction.atomic():
            previous = None
            if not self._state.adding:
                previous = type(self).objects.select_for_update().filter(pk=self.pk).first()
                if previous is None:
                    raise ValidationError("Marine profile state no longer exists")
            if previous is not None:
                changed = self.profile != previous.profile
                # A profile change must bump the generation; an applied re-review of the
                # same profile may bump it once to invalidate every earlier audit.
                allowed = (
                    {previous.generation + 1}
                    if changed
                    else {
                        previous.generation,
                        previous.generation + 1,
                    }
                )
                if self.source_id != previous.source_id or self.generation not in allowed:
                    raise ValidationError("Marine profile generation must follow profile changes")
                update_fields = kwargs.get("update_fields")
                if (
                    update_fields is not None
                    and changed
                    and not {"profile", "generation"}.issubset(update_fields)
                ):
                    raise ValidationError("Profile and generation must be saved together")
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Marine profile state cannot be deleted")


class MarineAuditRecord(models.Model):
    source = models.ForeignKey(
        "ingestion.Source", on_delete=models.PROTECT, related_name="marine_audit_records"
    )
    generation = models.PositiveBigIntegerField(null=True, blank=True)
    profile = models.CharField(
        max_length=16, default="strict", choices=[("strict", "strict"), ("partial", "partial")]
    )
    policy_hash = models.CharField(max_length=64)
    audited_at = models.DateTimeField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    audit = models.JSONField()

    def clean(self):
        super().clean()
        require_marine_source(self)
        if self.profile not in PROFILES or (
            self.generation is not None
            and (type(self.generation) is not int or self.generation < 0)
        ):
            raise ValidationError("Invalid Marine audit lineage")
        if not isinstance(self.policy_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.policy_hash
        ):
            raise ValidationError("Invalid Marine audit policy hash")
        if not isinstance(self.audit, dict):
            raise ValidationError("Marine audit must be an object")
        try:
            data = json.dumps(
                self.audit,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ValidationError("Invalid Marine audit data") from exc
        if len(data) > MAX_AUDIT_BYTES:
            raise ValidationError("Marine audit exceeds storage limit")

    def save(self, *args, **kwargs):
        if not self._state.adding or self.pk is not None:
            raise ValidationError("Marine audit history cannot be edited")
        self.clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Marine audit history cannot be deleted")


def record_matches_profile(record, state, source):
    """Check lineage only, not source permission, registry, audit status or freshness."""
    if (
        not isinstance(record, MarineAuditRecord)
        or not isinstance(state, MarineProfileState)
        or source is None
    ):
        return False
    config = PROFILES.get(state.profile) if isinstance(state.profile, str) else None
    return bool(
        source.pk is not None
        and source.pk == state.source_id == record.source_id
        and source.slug == "registry-195"
        and source.adapter == "stac"
        and source.url == URL
        and config is not None
        and source.config == config
        and record.profile == state.profile
        and record.generation is not None
        and record.generation == state.generation
        and isinstance(record.policy_hash, str)
        and re.fullmatch(r"[0-9a-f]{64}", record.policy_hash) is not None
        and hmac.compare_digest(record.policy_hash, marine_policy_hash(source))
    )


def profile_name(source):
    """Map the exact reviewed configuration to a profile name; anything else is unmatched."""
    for name, config in PROFILES.items():
        if source.config == config:
            return name
    return None


def append_audit(source, audit):
    """Append audit evidence for a locked Marine source inside the caller's transaction.

    Unreviewed identity or configuration is not recorded, so it can never match a profile.
    A profile state that disagrees with the configuration yields a lineage-free record.
    """
    profile = profile_name(source)
    if (
        profile is None
        or source.slug != "registry-195"
        or source.adapter != "stac"
        or source.url != URL
    ):
        return None
    state, _ = MarineProfileState.objects.select_for_update().get_or_create(source=source)
    return MarineAuditRecord.objects.create(
        source=source,
        generation=state.generation if state.profile == profile else None,
        profile=profile,
        policy_hash=marine_policy_hash(source),
        audited_at=source.audited_at,
        audit=audit,
    )


def latest_audit_matches(source):
    """True only when the newest ledger entry is this audit and fits the current profile."""
    state = MarineProfileState.objects.select_for_update().filter(source=source).first()
    record = source.marine_audit_records.order_by("-pk").first()
    return bool(
        state is not None
        and record is not None
        and record.audit == source.last_audit
        and record.audited_at == source.audited_at
        and record_matches_profile(record, state, source)
    )
