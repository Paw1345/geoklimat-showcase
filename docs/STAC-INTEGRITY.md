# STAC — integralność lokalnej paczki

[STACbounded](STAC-BOUNDED.md) używa tego weryfikatora z zamrożonym oknem24h.
Walidacja lokalnej paczki nie potwierdza pełnego budżetowo ograniczonego okna,
unikalności całego przebiegu ani pełnej doby. Profile20/21 aktywne;
lokalne PL68/4strony36rekordów iUE69/6/60zweryfikowane, nie pełny30dni.

[Datowane dowody i oś czasu](STAC-TIMELINE.md) rozróżniają brak obserwacji
w manifeście od zakresu, którego zamrożone wyszukiwanie nie obejmuje.

[Audyt wielopaczkowy](STAC-AUDIT.md) łączy podpisane dowody i liczy ID między
paczkami jednego manifestu, bez podnoszenia limitu pojedynczej kontroli50MiB.

[Katalog scen i eksport GeoJSON](STAC-SCENES.md) używa tego samego weryfikatora,
z filtrami ID/dat/bbox; sprawdza całą paczkę także poza limitem wyników.

Pakiet 4.5.8/4.5.9 rozszerza [panel przebiegów](STAC-COVERAGE.md).
Domyślny raport nadal czyta tylko metadane. Przycisk „Zweryfikuj” uruchamia
odczyt treści konkretnej paczki, bez żądań do Copernicusa, zapisów do bazy,
resetowania kursorów, napraw ani pobierania rastrów i załączników.

## Użycie

Zalogowany pracownik staff: `/data/stac/`, wybór paczki przy przebiegu.
Adres weryfikacji przypina kanał i przebieg, np.:

```text
/data/stac/?verify=1&channel=4&harvest=60&start=1
/data/stac/?verify=1&channel=4&harvest=60&start=1&format=json
```

Numery kanału/przebiegu są przykładem z bazy z 2026-10-03, nie konfiguracją
dla nowej instalacji. Wybieraj je z panelu. CLI w kontenerze:

```sh
docker compose exec -T web python manage.py stac_coverage --verify --channel 4 --harvest 60 --start 1
```

Bez `--verify` polecenie zachowuje raport metadanych. Tryb weryfikacji wymaga
`--channel` i `--harvest`; `--start` domyślnie 1. Link następnej paczki zachowuje
ten sam przebieg. Nie agreguj wyników kolejnych paczek jako globalnej unikalności.

## Granice i sprawdzane warunki

- PostgreSQL REPEATABLE READ READ ONLY, maksymalnie 25 kolejnych stron.
- Limit treści: mniejszy z 50 MiB i `DATA_MANUAL_MAX_BYTES`; każda kopia także
  mieści się w `DATA_AUTO_MAX_BYTES`. Rzeczywiste `OCTET_LENGTH(payload)`
  i zgodność deklarowanych rozmiarów są sprawdzane przed odczytem treści.
- Przebieg ma maksymalnie 10000 stron; jego konfiguracja, zamrożone 30-dniowe
  wyszukiwanie, kolekcja i bbox muszą nadal odpowiadać kanałowi STAC GET.
  Kanał musi mieć zatwierdzony dostęp. Nie zatwierdzamy dostępu tym raportem.
- Dla każdej wymaganej strony istnieje dokładnie jedna niewygasła kopia,
  z odpowiednim zakresem, numerem, typem i zgodnym URL paginacji.
- SHA256 całej zapisanej treści, licznik rekordów, JSON bez powtórzonych kluczy
  i NaN/Infinity, FeatureCollection/Feature, identyfikatory oraz kolekcja.
- Daty obserwacji mają strefę i mieszczą się jednocześnie w zamrożonym oknie
  i aktualnej retencji 30 dni. Granice dat odpowiadają metadanym kopii.
- Polygon/MultiPolygon: struktura, zamknięcie pierścieni, skończone współrzędne
  WGS84 i limit pozycji. To nie pełna kontrola topologii ani przecięcie z maską.

Brak, duplikat, wygaśnięcie, niespójność albo przekroczenie budżetu odrzucają
całą paczkę: panel zwraca 409, CLI błąd; nie ma częściowego pozytywnego wyniku.
Niepoprawne parametry panelu zwracają 400. HTML/JSON są staff-only, GET-only,
no-store; JSON ma nosniff. Wynik nie zawiera payloadów, URL assets, kursorów,
konfiguracji ani surowych wyjątków. JSON zawiera kwity kopii: ID, strona, SHA256
i bajty. SHA sprawdza lokalną kopię względem zapisanego hasha, nie podpis dostawcy.

## Znaczenie wyniku

`batch_verified=true` dotyczy wyłącznie wskazanej paczki w czasie odczytu.
Liczba różnych identyfikatorów, powtórzeń i identyfikatorów z różnymi wariantami
treści dotyczy tej paczki; wariant nie oznacza automatycznie korekty sceny.
Nie potwierdzamy oryginalnej strony dostawcy przed filtrowaniem retencji.

`all_harvest_pages_verified`, `full_current_30_day_coverage_verified` oraz
`geographic_mask_verified` pozostają false, nawet jeśli krótki przebieg mieści
się w jednej paczce. Raport metadanych nadal nie deklaruje globalnego SHA ani
globalnej unikalności scen. Dokładna maska PL/UE, pełne aktualne pokrycie,
unikalność całego zbioru i odbiór biznesowy całego 4.5 pozostają otwarte.

## Weryfikacja 2026-10-03T10:16–10:18Z

13 nowych testów; pełne 752 testy PostgreSQL PASS (33.438 s).
Ruff lint/format 376 plików, Django check i brak migracji PASS.
Budowa obrazu web PASS; pięć usług działa, baza healthy. Nie restartowano usług.
Testy obejmują uszkodzony hash, fałszywy rozmiar/budżet przed odczytem treści,
brak/duplikat/wygaśnięcie, kolekcję, daty, geometrię, powtórzone klucze JSON,
warianty, podział 25+1, uprawnienia oraz CLI bez HTTP.

Odczyt rzeczywistych stron 1–25:

| Kanał/przebieg | Strony | Bajty | Wystąpienia | Różne ID | Powtórzenia / ID z wariantami |
| --- | ---: | ---: | ---: | ---: | ---: |
| Polska 4/60 | 25 | 18736895 | 250 | 250 | 0 / 0 |
| UE 6/44 | 25 | 18727605 | 250 | 250 | 0 / 0 |

Panel HTML i JSON dla obu paczek: 200/no-store; CLI Polska: pozytywny wynik.
Kontrola nie zmieniła konfiguracji, retencji, limitu cache 15 GB ani danych.
Worker nadal pracuje niezależnie; nie odświeżano go na potrzeby tego pakietu.
## Zakres świeżych próbek

Uzupełnienie2026-10-03: [STAC-RECENT](STAC-RECENT.md) używa tego samego
weryfikatora z dokładnym oknem24h zamiast30dni. Zweryfikowanie dwóch stron
nie oznacza pełnej doby; globalne flagi pozostają false.
