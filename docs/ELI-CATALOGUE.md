# ELI — bezpieczna paginacja katalogów DU i MP

2026-10-02T11:11Z. Dokumentacja oficjalna sprawdzona tego dnia:
[ELI API Sejmu](https://api.sejm.gov.pl/eli_pl.html), sekcje wyszukiwania
i pól odpowiedzi. Dokument nie podaje tu niezależnie zweryfikowanej daty wydania.

## Co zmieniono

ingestion.eli_catalogue obsługuje istniejące profile adaptera eli:
https://api.sejm.gov.pl/eli/acts/DU/{year} i odpowiednik MP.
fetch_entries deleguje te dwa profile do paginacji oficjalnego /eli/acts/search.
Adres i konfiguracja zapisane przy Source nie są zmieniane.

Zakres: bieżący i poprzedni rok według zegara aplikacji, wszystkie typy aktów.
To surowa kolejka metadanych, nie wyłącznie akty środowiskowe/GIS.
Nie filtrujemy po obowiązywaniu, nie pobieramy dokumentów/PDF/odnośników
ani nie nazywamy tego pełną historią prawa. Są to katalogi polskie, nie EUR-Lex.

Strony: publisher, year, limit250, offset oraz sortBy=position/sortDir=asc.
Maksimum40 stron i10000 rekordów na rocznik; 20MiB odpowiedzi łącznie dla
obu roczników. Transport nadal wymaga zatwierdzonego HTTPS, robots, DNS,
limitów i czasu oczekiwania. Nie dodano hostów ani wyjątków robots.

Kontrole: HTTP200/dokładny URL, count równe liczbie items, totalCount,
oczekiwany offset, stały total w roczniku, poprawny postęp, ścisła rosnąca
pozycja, brak duplikatów, zgodność ELI/publisher/year/pos, daty i pola tekstowe.
Duplicate JSON keys, NaN/Infinity, przekroczony budżet i pusta strona przed
końcem powodują odmowę. Parser pojedynczej odpowiedzi również odrzuca
totalCount większe niż otrzymany zakres — samo count nie dowodzi kompletności.
Pobieranie zwraca wynik dopiero po powodzeniu obu roczników; nie zatwierdza
pierwszego rocznika, gdy drugi zawiedzie.

Stała liczba, kolejność i unikalność nie dowodzą transakcyjnej migawki dostawcy:
treść może zmienić się między żądaniami przy tej samej liczbie aktów.
Digest opisuje otrzymane odpowiedzi, nie całość prawa ani identyczny moment źródła.

## Daty i statusy

promulgation to data ogłoszenia, announcementDate data wydania,
entryIntoForce data wejścia w życie. Daty dzienne nie oznaczają godziny publikacji.
inForce to źródłowy status tekstowy, nie data. Zachowujemy go bez samodzielnego
wnioskowania prawnego; current_legal_status_verified=false i catalogue_only=true.

changeDate bez offsetu pozostaje w source_change_date_raw, a updated jest puste:
nie przypisujemy arbitralnie UTC. Jeśli źródło poda offset, zachowujemy ten czas
w updated i source_change_offset_present=true. Flaga potwierdza obecność offsetu
w tekście, nie niezależną weryfikację zegara dostawcy. Nie przepisujemy historycznych
rekordów ani wcześniejszych zdarzeń podczas samego wdrożenia kodu.

## Aktualny stan operacyjny

Odczyt z2026-10-02: istniejące eli-du (Source15) i eli-mp (Source16) były
aktywne, zatwierdzone i bez błędów. Pozycja rejestru23 ma natomiast osobny
niezatwierdzony kanał strony głównej Source31, bez powiązania z tymi kolektorami.
Flaga rejestru nie była dowodem awarii działającego DU/MP.

Ta paczka zmienia kod i testy; nie zmienia źródeł, relacji rejestru, auditów,
harmonogramów, danych, schematu, quota ani workbooka i nie odświeża workera.
Kod sprawdzono bezpośrednio przez CLI w kontenerze web. Długowieczny worker
nie został przeładowany, więc jego użycia nowej implementacji nie potwierdzono.
Następny etap: kontrolowane powiązanie już istniejących kanałów z23,
świeży audyt/dry-run i łagodne odświeżenie workera bez resetów.

### Aktualizacja powiązania — 2026-10-02T11:33Z

Powyższy brak powiązania jest historyczny. Komenda link_eli_registry ma
dry-run jako domyślny tryb, nie wykonuje HTTP ani nie tworzy/modyfikuje Source.
Pod blokadą kolektorów i transakcją zastępuje wyłącznie powiązanie pozycji23:
Source31 strony głównej -> istniejące Source15/16 DU/MP. Nie usuwa Source31,
historii, kopii ani powiązań pozycji22; nie zmienia statusu integracji czy note.
Niestandardowy profil, dodatkowe relacje, historia kanału strony głównej albo
brak świeżych15min audytów verified/count>0/rejected0 powodują odmowę.
Powtórzenie jest bez zmian. Nie jest to zatwierdzenie pełnego zakresu biznesowego.

6 nowych testów tej komendy i16 celowanych PASS0.156s; pełna regresja703
testów PostgreSQL PASS31.379s; Ruff356/lint/format/check/brak migracji i
buildworker PASS. Początkowy dry-run odmówił wykonania bez świeżych audytów.
Worker otrzymał wyłącznie SIGTERM i zakończył się kodem0
2026-10-02T11:30:54.964Z. Polityka unless-stopped pozostała bez zmian:
próba tymczasowego restart=no została odrzucona przed wykonaniem.
Świeże audyty11:31:28Z DU3189/0 i11:31:40Z MP2280/0 oraz oba dry-runy
3189/0+2280/0 poprawne. Link dry/apply/repeat ukończone; panel gotowości
pokazuje22 i23 bez flag technicznych, cały rejestr66 pozycji do przeglądu.
Rzeczywisty import i wznowienie workera: wynik końcowy w VERIFICATION.md.

### Wynik końcowy — 2026-10-02T11:37Z

Powiązanie ukończone i niezależnie sprawdzone:23 ->15/16,22 nadal ->15/16,
legacy31 nadal istnieje wyłączony i niezatwierdzony. Nie powstał żaden Source.
Zachowano rzeczywisty interwał obu kolektorów30min (nie domyślny360min katalogu).
Importy: DU3189updated/0created, MP2280updated/0created. Jednorazowa aktualizacja
wynika z nowych metadanych i usunięcia domyślnego UTC przy naivechangeDate;
historia wcześniejszych wersji zachowana. Niezależne powtórzenia:
DU3189unchanged/0created/0updated i MP2280unchanged/0created/0updated.

Worker wznowiony2026-10-02T11:36:46Z z obrazem
8319f1d7c6e8ef803883a8fa89b7864bd17a2435e80c31fbdbe4bbc16691c646,
polityka unless-stopped bez zmian. Wszystkie5usług running/DBhealthy;
199Source/170enabled/cache15000000000B bez zmian. Istniejący staff:
registry23 HTML200, readinessELI HTML/JSON200/no-store. Panel22/23 bez flag,
całość66pozycji do przeglądu; to stan techniczny, nie zatwierdzenie prawa/pokrycia.
Nowy obraz i proces workera uruchomione; następny harmonogramowany cykl
ELI nie był dodatkowo wymuszany. Pełny epik4.5 nadal otwarty.

## Weryfikacja

10 nowych testów i53 celowane PASS: paginacja obu roczników, kontrakt serwisu,
total/offset/order/duplicates/schema/dates/budgets/redirect/emptyyear i brak
domyślnego przypisywania UTC. Pełna regresja697 testów PostgreSQL PASS.
Ruff/format/check/migrations/build: wyniki końcowe w VERIFICATION.md.

Odczytowe rzeczywiste próby przez standardowy Client, bez importu DB:
DU3189 rekordów/0odrzuceń/HTTP200/digest6bfcea66fa21b4dc77e02099b45f5077fb5b7b92334b642470e0870c44d2d0ae;
MP2280/0/HTTP200/digested03a9cab0edffb81ea0c3823e5d00069e24eb1293ee1512bd93391bb79056a3.
To liczby pobranej lokalnie próby, nie liczba nowych wpisów zapisanych w aplikacji.
Pierwsza próba wykryła błędne traktowanie inForce jako daty; poprawiono i
zweryfikowano oba dzienniki przed zakończeniem. Pełny epik4.5 pozostaje otwarty.
