"""Operator-only live pilot, no DB writes. Run with manage.py shell after dry probes.

docker compose -f compose.yaml -f compose.ai.yaml exec -T web python manage.py shell
    -c "exec(open('scripts/pilot_ai.py').read())"
Fixed public sample IDs from docs/PILOT.md; no account or user data.
"""

import json
import time

from django.conf import settings

from ingestion.local_ai import (
    MODEL,
    PROMPT_VERSION,
    fingerprint,
    inference_lock,
    material,
    request_model,
    validate_output,
)
from ingestion.models import Item

if not settings.LOCAL_AI_ENABLED:
    raise RuntimeError("Local AI is disabled")
with inference_lock() as acquired:
    if not acquired:
        raise RuntimeError("Local AI is busy")
    for pk in (10719, 5368, 10814, 5925, 10825):
        started = time.monotonic()
        result = {"id": pk, "model": MODEL, "prompt_version": PROMPT_VERSION}
        try:
            item = Item.objects.get(pk=pk)
            data = material(item)
            result["input_hash"] = fingerprint(data)
            output = request_model(data)
            result["output"] = output
            validate_output(output, data)
            result["validation"] = "pass"
        except (ValueError, OSError) as exc:
            result["validation"] = type(exc).__name__
        result["seconds"] = round(time.monotonic() - started, 1)
        print(json.dumps(result, ensure_ascii=False), flush=True)
