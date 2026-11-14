"""Marine-specific audit binding and conservative activation guard."""

import hashlib

from ingestion.marine import CONFIG, PARTIAL_CONFIG, URL
from ingestion.source_policy import policy_snapshot


def marine_target(source):
    config = source.config
    return source.slug == "registry-195" or (
        isinstance(config, dict) and config.get("mode") == "marine_products"
    )


def marine_profile_valid(source, *, lock=False):
    if (
        source.slug != "registry-195"
        or source.adapter != "stac"
        or source.url != URL
        or source.config != CONFIG
    ):
        return False
    registry = source.registry_entries
    if lock:
        registry = registry.select_for_update()
    return list(registry.order_by("registry_id").values_list("registry_id", flat=True)[:2]) == [195]


def marine_policy_hash(source):
    value = source.slug + "\0" + policy_snapshot(source)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def marine_partial_audit_valid(source, *, lock=False):
    if (
        source.slug != "registry-195"
        or source.adapter != "stac"
        or source.url != URL
        or source.config != PARTIAL_CONFIG
        or source.enabled
        or source.activation_approved
    ):
        return False
    registry = source.registry_entries
    if lock:
        registry = registry.select_for_update()
    return list(registry.order_by("registry_id").values_list("registry_id", flat=True)[:2]) == [195]


def marine_audit_current(source):
    from ingestion.marine_history import latest_audit_matches

    audit = source.last_audit
    return (
        isinstance(audit, dict)
        and marine_profile_valid(source, lock=True)
        and audit.get("policy_hash") == marine_policy_hash(source)
        and latest_audit_matches(source)
    )
