# STAC — rzeczywiste obserwacje i aktualność okna

Pakiet4.5.8/4.5.9, uzupełnienie [audytu wielopaczkowego](STAC-AUDIT.md).
Oddzielamy zamrożony zakres wyszukiwania od dat rzeczywistych obserwacji
w zweryfikowanym lokalnym manifeście. Brak obserwacji w manifeście nie oznacza
braku scen u dostawcy, awarii, zachmurzenia ani kompletności pobrania.

## W aplikacji

Panel `/data/stac/` pokazuje liczbę dni UTC całkowicie lub częściowo poza
zamrożonym wyszukiwaniem w bieżącym oknie30×24h. To porównanie metadanych
dat, bez odczytu payloadów i HTTP. JSON zawiera `window_timeline`: kalendarz
z granicami aktywnych części dni i stanem wyszukiwania.

Liczby obserwacji w tym domyślnym kalendarzu są `null`, nie0: raport metadanych
ich nie sprawdził. Nie włączamy globalnych flag SHA, pełnej historii czy maski
na podstawie samego zakresu wyszukiwania.

## Rozkład z audytu treści

Nowe dowody paczek mają wersję2 i podpisane trójki ID/hash/data obserwacji.
Data pochodzi z cechy, która przeszła pełny weryfikator paczki. Połączenie
sprawdza ponownie strefę dat i zgodność z zamrożonym zakresem i retencją.
Plan pozostaje w wersji1. Limit25 kopii/50MiB nie zmienia się.

`stac_audit combine` dodaje `timeline`:

- `observations_available_for_entire_manifest`: wszystkie dowody mają daty;
- najstarsza i najnowsza zweryfikowana obserwacja;
- włączne datowania cech przyporządkowane do kalendarzowego dnia UTC;
- wystąpienia i różne ID w każdym dniu, z deduplikacją ID pomiędzy paczkami
  tego samego manifestu/dnia; suma unikalności dni nie jest globalną unikalnością;
- liczba dni bez lokalnej obserwacji w manifeście oraz dni poza wyszukiwaniem.

Stare dowody1 nadal dają zliczenie ID i powtórzeń. Jeśli choć jeden dowód nie
ma dat, pełna oś obserwacji jest nieznana: liczniki/daty są `null`, a flaga
dostępności osi false. Nie przerabiamy ani nie datujemy wstecz starych dowodów.

Okno30×24h zwykle przecina31 dat UTC: pierwsza i ostatnia są częściowe.
Przy granicy dokładnie o północy jest30 pełnych dni, bez dodatkowej pustej doby.
Daty ze strefą inną niż UTC są normalizowane do UTC przed grupowaniem.

Stany dnia opisują aktywną część doby, a nie samą etykietę daty:

| Stan | Znaczenie |
| --- | --- |
| `within_frozen_search` | Cała aktywna część doby mieści się w zakresie wyszukiwania |
| `partly_searched` | Tylko część ma wspólny zakres z wyszukiwaniem |
| `outside_frozen_search` | Aktywna część doby leży poza wyszukiwaniem |

`no_observations_in_manifest=true` to wyłącznie brak w audytowanym manifeście.
`source_missing_data_confirmed` oraz `daily_cadence_verified` pozostają false.
Nawet dodatnia liczba scen nie potwierdza pełnego pokrycia dnia, maski, produktu
czy kompletności katalogu dostawcy. Wynik w toku może obejmować tylko jego prefiks.

## Audyt UE — 2026-10-03T13:08:03Z

Kanał6/przebieg44, manifest1623 stron,65 osobnych paczek,
1216044607 sprawdzonych bajtów. 16230 wystąpień i16230 różnych ID,
0 powtórzeń i0 ID z wariantami. Kontrole treści13:05:36.044Z–13:08:01.412Z,
metadane manifestu zgodne przy łączeniu13:08:03.377Z; treści wtedy nie czytano
ponownie. Powstało67 nowych tymczasowych plików dowodowych w kontenerowym
`/tmp/geoklimat-stac-eu-20261003T1310-*`, bez zapisów do bazy aplikacji i HTTP.

Plan SHA256 `b9760ed1bc2936fd44370d1c72db73a88b472a65ef0aa99621cc942aa8b4646d`.
Przebieg nadal running. W czasie audytu powstały nowe strony: manifest nie
obejmuje już wszystkich odczytanych stron przy łączeniu. Nie dołączamy ich
samowolnie do istniejącego planu; wymagają nowego manifestu i kontroli.

Zamrożone wyszukiwanie2026-09-01T18:55:36Z–2026-10-01T18:55:36Z.
Rzeczywiste obserwacje manifestu:
2026-09-20T13:02:59.024Z–2026-10-01T14:10:09.024Z.

| Dzień UTC | Wystąpienia / różne ID w manifeście |
| --- | ---: |
| 2026-09-20 | 87 / 87 |
| 2026-09-21 | 1536 / 1536 |
| 2026-09-22 | 1423 / 1423 |
| 2026-09-23 | 1435 / 1435 |
| 2026-09-24 | 1499 / 1499 |
| 2026-09-25 | 1423 / 1423 |
| 2026-09-26 | 1526 / 1526 |
| 2026-09-27 | 1493 / 1493 |
| 2026-09-28 | 1484 / 1484 |
| 2026-09-29 | 1459 / 1459 |
| 2026-09-30 | 1425 / 1425 |
| 2026-10-01 | 1440 / 1440 |

W aktywnym oknie2026-09-03T13:08:03Z–2026-10-03T13:08:03Z:
2 dni UTC całkiem poza wyszukiwaniem (2–3 października),1 częściowo objęty
(1 października),19 bez obserwacji w tym manifeście. Ten ostatni licznik
obejmuje także część historii, której kompletność jest niepotwierdzona.
Nie są to19 potwierdzonych przerw u dostawcy. Pełne bieżące30dni i maskaUE
pozostają nieodebrane.

## Weryfikacja i dalszy krok

7 nowych testów: kalendarz i częściowe granice, północ, zerowe vs nieznane
obserwacje, ID pomiędzy paczkami, zgodność starego/mieszanego formatu, daty
podpisanego dowodu i raport metadanych bez payloadów.18 celowanych PASS2.476s,
785 pełnych PostgreSQL PASS37.812s, Ruff387 lint/format, Django check i brak
migracji PASS. Istniejący staff RequestFactory: panelHTML/JSON200/no-store;
PL62 ma0 całych dni poza wyszukiwaniem/1 częściowy, UE44 odpowiednio2/1;
w obu raportach metadanych liczby obserwacji nieznane.
Budowa obrazu web PASS; pięć usług running, baza healthy, bez restartów usług.

Nie zmieniano retencji, limitu cache15GB, kursorów, harmonogramów, źródeł,
workerów ani schematu. Następny projekt: świeże obserwacje niezależnie od
długiego przebiegu historycznego, z zachowaniem starego kursora i po audycie
zakresu/bezpieczeństwa. Nie resetować przebiegu44, żeby pozornie odświeżyć daty.
## Interpretacja świeżych próbek

Uzupełnienie2026-10-03: [STAC-RECENT](STAC-RECENT.md) porównuje próbkę24h
z nadal30-dniowym kalendarzem. Dni poza oknem zapytania są nieprzeszukane,
nie dowodzą awarii dostawcy; max20scen nie zapewnia pełnej doby.
