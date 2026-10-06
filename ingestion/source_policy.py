"""Fence a source import against revoked permission or changed collection policy."""

import json

from ingestion.transport import FetchError

POLICY_FIELDS = (
    "adapter",
    "url",
    "config",
    "name",
    "organization",
    "language",
    "scope",
    "reuse_notes",
)
POLICY_ERROR = "Source approval or import profile changed; batch not persisted"


class SourcePolicyChanged(FetchError):
    """Local administrative decision, not a provider fault or retry instruction."""


def policy_snapshot(source):
    # Serialize now: a parser may mutate the same nested config object later.
    return json.dumps({field: getattr(source, field) for field in POLICY_FIELDS}, sort_keys=True)


def check_policy(current, expected):
    if (
        current is None
        or not current.enabled
        or not current.activation_approved
        or policy_snapshot(current) != expected
    ):
        raise SourcePolicyChanged(POLICY_ERROR)
