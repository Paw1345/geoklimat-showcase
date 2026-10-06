# STAC Polska/UE — przebiegi i dostępność lokalnych stron

[Ograniczone wznawialne okna](STAC-BOUNDED.md) mają acquisition_mode bounded_window
i zakres24h. Bounded/paused/expired nie są pełnym skanem; profile20/21 aktywne
po [chronionej aktywacji](STAC-BOUNDED-ACTIVATION.md), PL68complete4/36 iUE69running6/60.

[Planer pojemności](STAC-CAPACITY.md) w panelu i JSON (`capacity`) podaje
odczytowe scenariusze retencji/rezerwy przed rozszerzaniem pobierania.
Nie czyta payloadów ani nie zatwierdza aktywacji; limit15GB pozostaje bez zmian.

[Świeże próbki](STAC-RECENT.md): osobne kanały mają okno24h i status bounded
po limicie2stron/20scen, nie koniec katalogu. Panel podaje acquisition_mode,
search_window_hours i sample_page_limit; historia zachowuje okno30dni.

[Aktualność zakresu i oś czasu](STAC-TIMELINE.md): domyślny panel podaje
całkowite/częściowe dni UTC poza frozenwindow bez czytania treści. Liczniki
obserwacji w tym kalendarzu są null; ich datowany rozkład daje audyt treści.

[Audyt manifestu](STAC-AUDIT.md): osobne kontrole paczek i podpisane dowody,
unikalność między paczkami. Domyślny raport metadanych nie zmienia swoich flag
SHA/globalnej unikalności na podstawie historycznych dowodów CLI.

[Katalog scen dla GIS](STAC-SCENES.md): link przy każdym przebiegu prowadzi
do filtrowania i eksportu GeoJSON zweryfikowanej lokalnej paczki.

Pakiet przekrojowy 4.5.8/4.5.9: panel staff `/data/stac/`, JSON
`/data/stac/?format=json` oraz CLI `python manage.py stac_coverage`.
Wspólny raport odczytuje wyłącznie bazę; nie wykonuje HTTP, zmian danych,
resetu, napraw ani ręcznego wznowienia kolektorów.

Uzupełnienie 2026-10-03T10:18Z: opis poniżej dotyczy domyślnego raportu
metadanych. Opcjonalna [weryfikacja paczki](STAC-INTEGRITY.md) odczytuje do25
payloadów/50MiB i potwierdza SHA/schema/daty/strukturę geometrii tylko tej paczki.
Nie zmienia globalnych flag pokrycia ani unikalności w raporcie metadanych.

## Rozdzielone informacje

- Zakończenie paginacji: zapisany status `complete` i pusty kolejny kursor.
- Zamrożone okno wyszukiwania: daty pobrane z zapisanego URL startowego,
  a nie z dzisiejszej daty. Podajemy również wiek końca tego okna.
- Odczytane strony i wystąpienia rekordów: historyczne liczniki przebiegu.
- Zachowane strony: niewygasłe kopie o zgodnych metadanych zakresu i dat.
- Brak kopii: brak zapisanego numeru strony; pierwsze 20 numerów jako przykład.
  Nie zgadujemy przyczyny usunięcia ani nie odtwarzamy brakującego payloadu.
- Wygasłe kopie nadal zapisane oraz odrzucone metadane/datowania: osobne liczniki.
- Granice dat obserwacji z metadanych: nie ciągłość szeregu ani pełne pokrycie.

Wystąpienia rekordów NIE są zweryfikowaną liczbą unikalnych scen. Bbox jest
prostokątem, nie oficjalną maską granic. Nie pobieramy rastrów/assets ani nie
sprawdzamy SHA256 i geometrii payloadów. `full_current_30_day_coverage_verified`,
`unique_scenes_verified`, `geographic_mask_verified`, `payload_hashes_verified`
pozostają false. Także komplet zachowanych stron nie daje odbioru biznesowego.

## Bezpieczeństwo i granice

PostgreSQL REPEATABLE READ READ ONLY; maksymalnie 20 kanałów i 10000 kopii
na wybrany najnowszy przebieg. Limit ma wartownika +1; przekroczenie nie daje
częściowego pozytywnego wyniku. Payloady i pełne historie kursorów nie są czytane.
Raport nie wypisuje URL, tokenów kursorów, surowych błędów ani konfiguracji.
Panel HTML escapuje tekst; staff-only, GET-only, no-store; JSON nosniff.

Profil STAC GET, hash konfiguracji, kolekcja/bbox, unikalne parametry URL,
30-dniowy przedział, metoda zakresu oraz metadane stron są sprawdzane.
Niepoprawne numery stron, duplikaty, liczniki, media/rozmiar lub daty/strefy
są odrzucane. Równość granicy wygasania oznacza wygaśnięcie. Mieszana strona
ze starą obserwacją wymaga przeglądu retencji, zamiast zapewnienia kompletności.
To kontrola deklarowanych metadanych — nie porównanie z treścią payloadu.

## Weryfikacja 2026-10-03T10:00Z

11 nowych testów i pełne 739 testów PostgreSQL PASS (30.672 s).
Ruff lint/format 373 plików, Django check i brak migracji PASS.
HTML/JSON na bieżącej bazie 200/no-store. Budowa obrazu web PASS;
standardowe ostrzeżenie pip o użytkowniku root dotyczy budowy kontenera.

Odczyt 10:00:44Z przy działającym workerze:

- Polska, przebieg60: complete/146 stron/1458 odczytanych wystąpień rekordów.
  Zakres 2026-09-03T08:43:10Z–2026-10-03T08:43:10Z.
  Dostępne138 stron/1380 wystąpień; brak kopii143–146, trzy zapisane wygasłe,
  jedna odrzucona przez kontrolę dat obserwacji. Niepotwierdzone pełne kopie.
- UE, przebieg44: running/1325 stron/13250 wystąpień; tyle samo dostępnych
  według metadanych, bez zgłoszonych problemów. Zakres
  2026-09-01T18:55:36Z–2026-10-01T18:55:36Z; nie obejmuje najnowszych dwóch dni.

Wyniki zmieniają się wraz z pobieraniem i retencją. Nie zmieniano limitu15GB,
okna30dni, kursorów, harmonogramów ani workerów. Nie wykonano nowych żądań
do dostawcy w ramach weryfikacji panelu.

## Dalszy odbiór

Nie zamknięto całego4.5.8/4.5.9/4.5. Wymagane są rozstrzygnięcie dokładnej
maski PL/UE, unikalności/pokrycia scen i pełnych dostępnych kopii, a następnie
odbiór rzeczywistego okna historii. Braki po normalnym wygaśnięciu nie uprawniają
do resetowania istniejących przebiegów ani przedłużania retencji.
