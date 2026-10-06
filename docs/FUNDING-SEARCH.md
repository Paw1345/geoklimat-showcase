# Publiczna diagnostyka naborów LIFE

## Weryfikacja kalendarza i terminów etapowych — 2026-10-01T10:59+02:00

Parafraza [oficjalnego kalendarza CINEA2026](https://cinea.ec.europa.eu/life-calls-proposals-2026_en):
SAP mają termin22września2026; SNaP/SIP mają termin concept notes3września2026
i pełnych wniosków4marca2027. [PRIVAFIN2026](https://cinea.ec.europa.eu/funding-opportunities/calls-proposals/crowding-private-finance-1_en)
ma status Closed i deadline16września2026. Nie jest to kopia całego kalendarza
ani automatyczny kanał danych. Nie przeniesiono dat dziennych CINEA do API
z wymyśloną godziną lub strefą.

Wniosek: brak2026 w zapytaniu ograniczonym statusami nie dowodzi nieaktualności
całego indeksu; część konkursów2026 zakończyła pierwszy/jedyny etap.
Nadal istnieje31rekordów API z przeszłymi terminami w wybranych statusach.
Przyczyna ich pozostawienia w indeksie i kompletność filtrowanego wyniku
UNCONFIRMED. Nie rozszerzono POST, endpointu, programu ani budżetu.

Raport dodaje deadline_time_evidence: liczby przeszłych/przyszłych dat,
najbliższą surową datę i state: all_deadlines_elapsed,
mixed_deadlines_require_stage_review albo future_deadlines_require_opening_review.
Równość terminu z as_of oznacza termin osiągnięty, porównania uwzględniają
offsety. Kolejność źródłowej listy zachowana; nie nazywamy etapów bez metadanych.
Przyszła data nie dowodzi otwarcia ani uprawnienia nowego wnioskodawcy:
new_applicant_eligibility_verified i current_call_verified zawsze false.
Agregaty future_deadline_records_count i mixed_deadline_records_count
opisują wyłącznie daty, nie liczbę dostępnych konkursów.

Weryfikacja:16testów celowanych/429pełnych PG PASS17.816s, lint/243format/
Djangocheck/no migrations/build PASS. Live2026-10-01T09:00:52Z:
31all_elapsed/0mixed/0future, brak zapisów i pobrania załączników.
Kolektor ani harmonogram nie aktywowane, worker bez restartu.

Stan: 2026-10-01T10:12+02:00. Użytkownik zatwierdził publiczne, odczytowe
wyszukiwanie POST bez logowania, danych osobowych i składania wniosków.
Zgoda nie obejmuje ECHA, dowolnych POST ani kolejnego restartu workera.

## Zakres i zabezpieczenia

[Oficjalna dokumentacja](https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/support/apis)
opisuje SEARCH API oraz multipart pole pliku query. Wąski interfejs
Client.funding_search(page) używa wyłącznie HTTPS
api.tech.ec.europa.eu/search-api/prod/rest/search, publicznego identyfikatora
aplikacji SEDIA i stałego filtra: LIFE43252405, typy1/2/8,
surowe statusy31094501/31094502, język en. Brak dowolnego payloadu.

Do5stron po50rekordów, limit250. Robots GET przed POST, standardowe kontrole
hosta/publicznego DNS; robots404 dopuszczony zgodnie z dotychczasową polityką,
403/HTML/disallow blokują. Bez Authorization/Cookie; bez przekierowań POST,
bez pobierania załączników i linków szczegółów. Limit odpowiedzi5MiB,
timeout15s, budżet odczytu30s, maksymalnie3próby i ograniczony Retry-After.
Pozostałe zdalne pobrania pozostają GET-only.

## Polecenie

```text
python manage.py discover_funding_calls
python manage.py discover_funding_calls --probe
```

Domyślnie tylko specyfikacja JSON: brak HTTP i zapisów do bazy.
--probe wypisuje drugą linię JSON z pełnym, zwalidowanym raportem jakości.
W razie zmiany schematu, tożsamości, języka, liczby, ostrzeżeń API lub
niekompletnej strony kończy się błędem bez zwracania częściowej paczki.
Identyfikatory LIFE i źródłowe daty/strefy zachowane; osobne linki typu8
COMPETITIVE_CALLen dozwolone tylko na dokładnej ścieżce topicDetails.
Żaden rekord nie otrzymuje domyślnej daty publikacji ani oceny aktualności.

## Wynik rzeczywistej próby

2026-10-01T08:11:59Z: HTTP200, jedna strona31/31 EN; wszystkie31 mają
wszystkie źródłowe terminy w przeszłości. quality=all_source_deadlines_elapsed,
current_call_verified=false, database_writes=false, attachments_downloaded=false.
Sam parametr language=en początkowo nie ograniczał języka; dodano wymagany
filtr języka w query. Surowe statusy nie są samodzielnym dowodem aktualności.

Nie utworzono kolektora, harmonogramu ani Items z tej paczki. Nie dowodzi ona
braku bieżących naborów LIFE ani kompletności UE. Aktualne nabory nadal
wymagają wyjaśnienia indeksu/filtrów na podstawie oficjalnego interfejsu.
Aktywne katalogi dokumentacji LIFE/Horizon114+233 pozostają odrębnym zakresem.
Pełne189 needs_connector i ograniczenia186 ECHA pozostają otwarte.
