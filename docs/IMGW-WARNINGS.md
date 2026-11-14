# IMGW — ostrzeżenia meteo (34) i hydro (33)

2026-10-14 [CODE]/[TOOL], Claude. Brak z grupy B kart K6
([karty](EPIC45-K6-DATA-CARDS-2026-10-14.md)): pozycje 33/34 miały pomiary,
ale nie miały kanału danych z ostrzeżeniami.

## Rozpoznanie (PC, 2026-10-14T20:30Z)

Dwa GET przez standardowy Client aplikacji (robots sprawdzony), bez zapisów:

| Endpoint | HTTP | B | Kształt |
|---|---|---|---|
| `https://danepubliczne.imgw.pl/api/data/warningsmeteo` | 200 | 56 | `{"message": "Brak ostrzeżeń meteorologicznych"}` |
| `https://danepubliczne.imgw.pl/api/data/warningshydro` | 200 | 121 551 | lista 95 obiektów |

Hydro: pola `opublikowano`, `stopień`, `data_od`, `data_do`, `prawdopodobienstwo`,
`numer`, `biuro`, `zdarzenie`, `przebieg`, `komentarz` (tekst, także liczby) oraz
`obszary` (lista województw/opisów/kodów zlewni). `data_do` 9999-12-31 23:59:59
oznacza ostrzeżenie bezterminowe (np. susza hydrologiczna). Czasy bez strefy;
czas lokalny Warszawy UNCONFIRMED. Kształt **listy** ostrzeżeń meteo UNCONFIRMED
(w chwili sondy żadne nie obowiązywało). Ten sam publiczny API i warunki co
zatwierdzone kanały pomiarów IMGW (atrybucja, apiinfo); bez nowych warunków.

## Kolektor

`ingestion/imgw_warnings.py`, tryb `imgw_warnings`, kind `table` (poza ścieżką
pomiarów IMGW i dowodami C9). Jeden GET na przebieg, budżet 2 MiB, zapis
**oryginalnych bajtów** dostawcy. Walidacja: obiekt `{"message"}` lub lista
obiektów; dla hydro wymagane pola, typy tekstowe i format dat. Lista meteo
zapisywana z listą kluczy do przeglądu. Podsumowanie: liczba ostrzeżeń, zdarzenia,
stopnie, bezterminowe, najnowsza publikacja (surowa). Identyczna odpowiedź → unchanged.
Retencja standardowa 30 dni. Ostrzeżenie to informacja IMGW, nie lokalny alarm.

## Wdrożenie na PC

```powershell
docker compose exec -T web python manage.py configure_imgw_warnings           # dry-run
docker compose exec -T web python manage.py configure_imgw_warnings --apply   # 2 kanały wyłączone, co 30 min
docker compose exec -T web python manage.py audit_data --source imgw-warnings-meteo --enable
docker compose exec -T web python manage.py audit_data --source imgw-warnings-hydro --enable
```

Potem wymuszona próba (next_fetch_at=teraz dla tych dwóch slugów, origin worker).
