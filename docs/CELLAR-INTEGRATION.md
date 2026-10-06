# Cellar170 — wdrożony zakres metadanych

Aktualizacja2026-09-30T13:15Z: kanał registry-170 skonfigurowany i aktywny.
Zastępuje poniższy historyczny plan wdrożenia w zakresie opisanym tutaj.

- Publiczne zapytania SPARQL GET: dokładny EuroVoc2470 (`environmental policy`),
  angielskie tytuły wyrażeń, daty dokumentów z ostatnich30 dni kalendarzowych UTC.
- Predykaty CDM: `work_is_about_concept_eurovoc`, `work_date_document`,
  `expression_belongs_to_work`, `expression_uses_language`, `expression_title`.
- Data dokumentu jest metadaną `document_date`, nie datą publikacji ani aktualizacji.
  Pola published/updated pozostają puste. Stabilny klucz: źródłowy URI work/UUID.
- Paginacja50 rekordów, budżet10 stron/500 rekordów. Zmiana schematu, języka,
  daty, identyfikatora, kolejności, duplikaty lub przekroczenie budżetu odrzucają
  całą partię. Skrót partii obejmuje wszystkie wpisy, nie tylko ostatnią stronę.
- configure_cellar dry-run/apply; audyt HTTP200/verified/2/0; aktywacja po audycie;
  ingest dry-run2, import2created, powtórzenie2unchanged/0created/0updated.
- 6 testów konektora (w tym wspólny kontrakt fetch_entries),320 pełnych testów,
  Ruff lint/format PASS, build workera PASS. Worker zatrzymany łagodnie exit0
  i wznowiony; wszystkie5 usług działają.

Nadal otwarte: dodatkowe pojęcia klimatu/INSPIRE/GIS, inne języki, monitoring
modyfikacji dokumentów starszych niż okno daty dokumentu oraz zasoby na żądanie.
Nie pobrano pełnych dokumentów ani załączników. Dwa wyniki nie oznaczają pełnego
pokrycia Cellar ani nowych zmian prawnych; kategorię nadaje źródłowy EuroVoc.

## Historia rozpoznania i pierwotny plan

Odczyt2026-09-30T12:56Z (14:56 Europe/Warsaw). Nie aktywowano kanału170
i nie zapisano wyników zapytań do rejestru publikacji.

## Potwierdzone

- Oficjalna dokumentacja wskazuje publiczny SPARQL:
  https://publications.europa.eu/webapi/rdf/sparql
- Minimalne odczytowe GET `ASK {}`:HTTP200,43B,boolean=true.
- Odczyt z limitem3 dla angielskiej etykiety environmental policy:HTTP200,649B.
  Potwierdzone identyfikatory pojęcia: eurovoc.europa.eu/2470,
  publications.europa.eu/resource/authority/fd_300/002470,data.europa.eu/uxp/2470.
  To wyniki słownika, nie pobrane publikacje ani ocena prawna.
- publications.europa.eu/robots.txt deleguje301 do dokładnego
  https://op.europa.eu/robots.txt. Polityka200text/plain dopuszcza ścieżkę API.
  Transport obsługuje tylko tę zatwierdzoną parę adresów. Test potwierdza,
  że zakaz w docelowej polityce nadal blokuje API, a inne cele/przekierowania
  między hostami pozostają odrzucane. DNS/HTTPS/budżety/retry nadal obowiązują.
- 2nowetesty/314pełnychtestów,Rufflint/format,buildworkeraPASS.
  Worker zakończony łagodnie exit0 i wznowiony.

## Następne zadanie: właściwy konektor metadanych

1. Odczytową próbką ustalić rzeczywiste predykaty CDM dla pojęcia, tytułu,
   identyfikatora publikacji oraz dat publikacji i modyfikacji. Nie zgadywać schematu.
2. Zdefiniować jawny zakres tematyczny i językowy.2470 jest potwierdzonym
   pojęciem polityki środowiskowej, ale nie pokrywa samodzielnie wszystkich
   tematów klimatu, INSPIRE i GIS. Metadane źródłowe mają identyfikować powiązanie.
3. Implementować ograniczone zapytania GET, stabilną kolejność/paginację
   i jawne raportowanie niekompletnego zakresu. Okno30dni wymaga potwierdzonej
   semantyki pola czasu; data dokumentu nie zastępuje daty jego aktualizacji.
4. Parser sprawdza typy SPARQL JSON, wymagane pola, hosty/identyfikatory,
   duplikaty, języki i zakres. Brak tytułu lub zmiana schematu odrzuca import.
   Nie pobiera automatycznie pełnych dokumentów ani załączników.
5. Testy z fixtureHTTP, bezpieczne configure dry-run/apply, audyt,
   aktywacja i import, powtórzenie bez duplikatów. Dopiero wtedy zmienić
   pozycję170 na skonfigurowaną integrację.

## Źródła pierwotne

- [Knowledge graph i SPARQL](https://op.europa.eu/en/web/cellar/cellar-data/metadata/knowledge-graph)
- [RSS/ATOM i parametry powiadomień](https://op.europa.eu/en/web/cellar/cellar-data/rss-and-atom-feeds)
- [Dokumentacja Cellar](https://op.europa.eu/en/web/cellar/documentation)

RSS powiadomień obejmuje działania na dokumentach i metadanych. Nie traktować
daty takiej operacji jako daty publikacji ani kompletnych metadanych dokumentu.
