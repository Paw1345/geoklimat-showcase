"""Model comparison experiment only: no DB writes, no change of the production model.

Runs the production prompt (PROMPT_VERSION) on the five B2 documents plus up to
AI_COMPARE_EXTRA (default 15) recent items with a longer description, one per source,
for each model below and prints one JSON line per document with its source text, so
claims can be checked against it. AI_COMPARE_ITEMS="1,2,3" replaces the selection,
AI_COMPARE_MODELS (space-separated Ollama tags) the models.
Run in a one-off container so the running web service is not recreated
(see docs/AI-MODEL-COMPARISON.md):
docker compose -f compose.yaml -f compose.ai.yaml run --rm --no-deps -T web
  python manage.py shell -c "exec(open('scripts/pilot_ai_compare.py').read())"
Model output is untrusted and printed for editorial comparison, never approval.
"""

import http.client
import json
import os
import re
import time

from django.conf import settings
from django.db.models.functions import Length

from ingestion.local_ai import (
    PROMPT,
    PROMPT_VERSION,
    SCHEMA,
    failure_message,
    fingerprint,
    inference_lock,
    material,
    validate_output,
)
from ingestion.models import Item

MODELS = tuple(os.environ.get("AI_COMPARE_MODELS", "qwen3:4b qwen3:14b").split())
ITEMS = (10719, 5368, 10814, 5925, 10825)


def selection():
    if os.environ.get("AI_COMPARE_ITEMS"):
        return [int(value) for value in os.environ["AI_COMPARE_ITEMS"].split(",")]
    chosen, sources = list(ITEMS), set()
    extra = int(os.environ.get("AI_COMPARE_EXTRA", "15"))
    recent = (
        Item.objects.annotate(summary_length=Length("summary"))
        .filter(summary_length__gte=200)
        .exclude(pk__in=ITEMS)
        .order_by("-first_seen_at", "-pk")
        .values_list("pk", "source_id")
    )
    for pk, source in recent.iterator():
        if len(chosen) >= len(ITEMS) + extra:
            break
        if source not in sources:
            sources.add(source)
            chosen.append(pk)
    return chosen


def ask(model, data):
    # Same request as local_ai.request_model, with the model as a parameter.
    schema = json.loads(json.dumps(SCHEMA))
    quotes = [
        value for value in re.split(r"(?<=[.!?])\s+", data["summary"]) if 20 <= len(value) <= 1000
    ][:8]
    schema["properties"]["quote"]["enum"] = quotes or [data["summary"][:500]]
    payload = {
        "model": model,
        "stream": False,
        "format": schema,
        "keep_alive": "1m",
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 800},
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
    }
    if model.startswith("qwen3"):
        payload["think"] = False
    client = http.client.HTTPConnection("ollama", 11434, timeout=240)
    try:
        client.request(
            "POST",
            "/api/chat",
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        raw = response.read(131073)
        if response.status != 200 or len(raw) > 131072:
            raise ValueError(f"local_response {response.status} {raw[:200]!r}")
        result = json.loads(raw)
        if result.get("done") is not True or result.get("done_reason") != "stop":
            raise ValueError("incomplete")
        if result.get("model") != model or result.get("message", {}).get("tool_calls"):
            raise ValueError("unexpected_model_or_tools")
        return json.loads(result["message"]["content"])
    finally:
        client.close()


if not settings.LOCAL_AI_ENABLED:
    raise RuntimeError("Local AI is disabled")
with inference_lock() as acquired:
    if not acquired:
        raise RuntimeError("Local AI is busy")
    items = selection()
    for model in MODELS:
        for pk in items:
            started = time.monotonic()
            item = Item.objects.get(pk=pk)
            data = material(item)
            result = {
                "id": pk,
                "model": model,
                "prompt": PROMPT_VERSION,
                "input_hash": fingerprint(data),
                "url": item.url,
                "source": data,
            }
            try:
                result["output"] = ask(model, data)
                validate_output(result["output"], data)
                result["validation"] = "pass"
            except (ValueError, KeyError, TypeError, OSError) as exc:
                result["validation"] = "rejected"
                result["reason"] = str(exc)[:300] or failure_message(exc)
            result["seconds"] = round(time.monotonic() - started, 1)
            print(json.dumps(result, ensure_ascii=False), flush=True)
