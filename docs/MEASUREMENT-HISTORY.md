# Historia pomiarów IMGW — lokalne kopie

Aktualizacja 2026-10-02T10:59Z: dostępny jest opt-in
[tryb paczek kopii](MEASUREMENT-BATCHES.md), z podpisanym zakresem i jawnymi
wynikami częściowymi. Poniższy opis wszystkich kopii dotyczy domyślnego,
pełnego odczytu; w trybie paczek integralność payloadów i statystyki
obejmują wyłącznie wybraną paczkę. Limity cache/retencji pozostają bez zmian.

## Zakres i obsługa

Panel operatora: `/data/measurements/`, GET, wymagane istniejące konto staff.
Odnośnik znajduje się w katalogu danych i na kartach kanałów IMGW.

1. Wybierz zatwierdzony kanał SYNOP, METEO albo HYDRO, liczbę dni (1–30)
   i format „Podgląd”. Pozostaw identyfikator stacji pusty.
2. Kliknij identyfikator na liście stacji mających wpisy z aktualnym czasem.
3. Opcjonalnie wpisz nazwę parametru z listy pod formularzem. Nieznany
   parametr jest odrzucany, nie zastępowany automatycznie.
4. Wybierz JSON albo CSV. CSV wymaga identyfikatora stacji; JSON może
   również eksportować listę stacji i raport dostępnych kopii.

Przykład: `/data/measurements/?channel=1&station=12295&days=30&limit=500&format=html`.
Numery kanałów są lokalne; przy innej instalacji użyj formularza.

Nie obsługujemy tu archiwum dobowego SYNOP, GIOŚ, alertów zdrowotnych,
prognoz ani progów alarmowych. Produkty dobowe i chwilowe nie są łączone.
Źródłowa dokumentacja API IMGW: https://danepubliczne.imgw.pl/pl/apiinfo
(odczyt 2026-10-02). Wskazuje trzy API, nie dowodzi kompletności lokalnych
kopii ani potwierdzenia wszystkich jednostek i stref czasu w tym panelu.

## Semantyka

- Historia obejmuje wyłącznie zapisane, jeszcze ważne kopie. Brak wyniku
  nie dowodzi braku odczytu u dostawcy. Nie uzupełniamy braków siecią.
- Czas SYNOP składamy z data_pomiaru i godzina_pomiaru. METEO/HYDRO
  używają własnego pola czasu każdego parametru, nigdy daty sąsiedniego
  parametru, czasu pobrania ani metadanych założenia stacji.
- Interpretacja strefy pochodzi z przypiętego observation_timezone
  kopii (UTC albo Europe/Warsaw). Nie zmienia jej późniejsza edycja kanału.
  Mieszane strefy są odrzucane. To zachowanie istniejącego kolektora,
  nie nowe niezależne potwierdzenie strefy u dostawcy.
- Tabela, dni pokrycia i porównania są w UTC. Obserwacje spoza okna
  retencji albo przyszłe nie są zwracane. Dni graniczne są częściowe.
- Dla stacji, parametru i czasu scalane są tylko identyczne surowe
  reprezentacje wartości. Wszystkie identyfikatory kopii pozostają w wyniku.
  String0 i liczba0 pozostają odrębne; różnica reprezentacji nie jest dowodem korekty.
- Rozbieżne wersje pozostają widoczne z liczbą wariantów. Nie wybieramy
  „poprawnej” ani automatycznie najnowszej wersji.
- Null nigdy nie staje się zerem. Zachowujemy null z poprawnym czasem,
  ale nie liczymy go jako dnia z dostępną niepustą wartością.
- Jednostki są jawnie niepotwierdzone (units_verified=false, unit=null).
  Bez konwersji, średnich, interpolacji, oceny jakości czy alarmowania.
- full_history_verified=false, cadence_verified=false. Brak dnia
  oznacza brak zachowanej niepustej wartości dla filtra, nie awarię dostawcy.
  Raport pokrycia obejmuje wszystkie zweryfikowane kopie, nie tylko
  wiersze mieszczące się w limicie podglądu/eksportu.

## Integralność i bezpieczeństwo

ingestion.measurement_history.query działa w PostgreSQL
REPEATABLE READ, READ ONLY. Ściśle akceptuje zatwierdzone kanały imgw,
region PL i trzy konkretne adresy API. Nie wykonuje HTTP, zapisów,
pruning, zadań, migracji ani odświeżenia czasu kopii.

Przed odczytem payloadów sumuje ich rzeczywistą długość przez OCTET_LENGTH,
nie ufa deklarowanemu size_bytes. Limity: 2000 kopii, min(50MiB,
DATA_MANUAL_MAX_BYTES) odczytu, 2000 stacji, 5000 wierszy w jednej kopii,
25000 unikalnych wersji wartości wybranej stacji, 5MiB odpowiedzi,
limit podglądu/eksportu 1–1000 wierszy. Nie zwiększa cache 15GB.

Każda kopia musi mieć poprawny SHA256/rozmiar/media type, schemat listy,
unikalne identyfikatory stacji, version1/kind imgw i przypiętą strefę.
Duplicate JSON keys i niefinitywne wartości są odrzucane.
Uszkodzona kopia powoduje odmowę całego wyniku, również po osiągnięciu
limitu wyświetlanych wierszy. Kopie wygasłe albo starsze niż twarda
retencja pobrania są wykluczane przed odczytem.

JSON zawiera raport pokrycia, listę stacji, surowe wartości, źródłowe czasy,
observation_expires_at oraz receipts (snapshot_id/hash/czas pozyskania/
ważność kopii/strefa). Termin ważności odczytu i kopii to różne ograniczenia.
CSV ma receipts wykorzystanych kopii przy każdym wierszu oraz kolumny
raw_value_is_null i units_verified. CSV jest UTF8 BOM; tekst mogący
uruchomić formułę arkusza otrzymuje apostrof. Dotyczy to również ujemnego
tekstu liczbowego. JSON pozostaje eksportem wiernej surowej reprezentacji.
CSV z zerem wierszy ma tylko nagłówek; pełny raport pokrycia dostępny w JSON.

Widok core.measurements.history jest staff-only GET/no-store; błędny
formularz400, odmowa profilu/kopii/filtra/budżetu409. Błąd eksportu zwraca
wyłącznie bezpieczny komunikat, bez częściowych danych i surowego wyjątku.
HTML automatycznie escapuje dane źródłowe, eksport ma stałą nazwę pliku,
nosniff i ETag zgodny z rzeczywistymi bajtami. Nie dodano zależności.

## Weryfikacja i granice odbioru

2026-10-02T10:07Z, odczytowe próby lokalnych kopii:

| Kanał | Kopie | Odczytane bajty | Stacje w lokalnym oknie | Czas listy |
| --- | ---: | ---: | ---: | ---: |
| SYNOP | 94 | 1438093 | 62 | 0,28s |
| METEO | 82 | 39874721 | 684 | 1,73s |
| HYDRO | 81 | 45336883 | 906 | 1,74s |

Wybrane pierwsze stacje12001/249170080/149180010:552/82/163 unikalnych
wersji wartości, po10 wierszy w próbie; to nie kompletne liczebności
archiwum dostawcy. METEO/HYDRO zbliżają się do budżetu odczytu; jeśli go
przekroczą, nastąpi odmowa zamiast cichego pominięcia kopii.

17 testów PostgreSQL obejmuje deduplikację, warianty, null/zero, czas,
retencję, corrupt-copy-after-limit, actual-byte budget, eksport, CSV formula
guard, XSS, dostęp staff i odmowy bez częściowych danych.
Końcowe wyniki 2026-10-02T10:15Z:664 testy PostgreSQL PASS26.528s,
Ruff343/check/nomigrations/webbuild PASS. Existingstaff RequestFactory:
formularz/lista/stacja/JSON54551B/CSV7106B HTTP200, no-store, eksporty
nosniff i sumy ETag zgodne z rzeczywistymi bajtami.5 usług running/DBhealthy,
runtime cache15000000000B. Bez aktywacji kolektorów i restartu workera.
Budowa zawiera standardowe ostrzeżenie pip o instalacji jako root w istniejącym
etapie obrazu; brak instalacji na hoście i zmian zależności.
Szczegóły pełnej regresji i budowy w VERIFICATION.md.
Cały epic4.5 i pełna historia30dni pozostają otwarte.
