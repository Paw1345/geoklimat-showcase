"""Local pilot template; copy explicitly, never import from normal settings."""

import os

from config.settings.development import *  # noqa: F403

# An explicit process-local choice, not a secret or an authorization mechanism.
COLLECTION_EVIDENCE_ENABLED = (
    os.environ.get("GEOKLIMAT_COLLECTION_EVIDENCE_OPT_IN") == "imgw-pilot-c8"
)
