"""B2 experiment only: no DB writes, no deployment of the candidate prompt.

Run after read-only material probes:
docker compose -f compose.yaml -f compose.ai.yaml exec -T web python manage.py shell
  -c "exec(open('scripts/pilot_ai_candidate.py').read())"
Prints untrusted model output for editorial comparison, never approval.
"""

import json
import time
from unittest.mock import patch

from django.conf import settings

from ingestion.local_ai import (
    MODEL,
    PROMPT,
    failure_message,
    fingerprint,
    inference_lock,
    material,
    request_model,
    validate_output,
)
from ingestion.models import Item

CANDIDATE = PROMPT + (
    " Dodatkowe zasady redakcyjne: summary ma zawierać 1–2 krótkie zdania, "
    "nie kopię całego opisu. Zachowaj etap procesu: przyjęcie ustawy przez Senat "
    "nie oznacza jej wejścia w życie. Zapowiedź publikacji danych nie jest terminem "
    "wykonania obowiązku przez odbiorcę. Nie rozwijaj ogólnego 'dla każdego' "
    "w listę zawodów lub instytucji. W datach zachowuj dokładność źródła: "
    "miesiąc i rok bez dnia pozostają miesiącem i rokiem. "
    "Nazwy produktów i wersje przepisuj bez zmian. Jeśli nie jesteś pewien "
    "tłumaczenia terminu branżowego, zachowaj go w oryginale w cudzysłowie; "
    "nie zastępuj go dosłownym znaczeniem z innej dziedziny. "
    "Zachowuj sens ograniczeń czasowych, np. 'at the latest' znaczy 'najpóźniej'. "
    "Przed odpowiedzią sprawdź polską gramatykę oraz zgodność każdego twierdzenia "
    "z materiałem. Nie wypisuj tego sprawdzenia."
)

if not settings.LOCAL_AI_ENABLED:
    raise RuntimeError("Local AI is disabled")
with inference_lock() as acquired:
    if not acquired:
        raise RuntimeError("Local AI is busy")
    for pk in (10719, 5368, 10814, 5925, 10825):
        started = time.monotonic()
        item = Item.objects.get(pk=pk)
        data = material(item)
        result = {
            "id": pk,
            "model": MODEL,
            "experiment": "b2-candidate-1",
            "input_hash": fingerprint(data),
        }
        try:
            with patch("ingestion.local_ai.PROMPT", CANDIDATE):
                result["output"] = request_model(data)
            validate_output(result["output"], data)
            result["validation"] = "pass"
        except (ValueError, KeyError, TypeError, OSError) as exc:
            result["validation"] = "rejected"
            result["reason"] = failure_message(exc)
        result["seconds"] = round(time.monotonic() - started, 1)
        print(json.dumps(result, ensure_ascii=False), flush=True)
