# INSPIRE — osiem wybranych słowników wartości PL/EN

Stan 2026-10-02T10:39Z: osobny aktywny Source199
registry-180-inspire-codes,1440min,76 zapisanych wartości,62 z etykietą PL.
Niezależne powtórzenie HTTP200:76 unchanged/0created/0updated.
Katalog schematów Source190/370Items pozostaje bez zmian.

## Użycie

Panel `/vocabularies/` wymaga istniejącego konta staff. Odnośnik w `/data/`.
Wybierz słownik, opcjonalnie fragment etykiety PL albo EN i format JSON.
Podgląd ma50rekordów/stronę; eksport do1000rekordów/5MiB.
Każda karta prowadzi do istniejącej historii rekordu.
GET i eksport czytają tylko lokalną bazę, bez pobierania identyfikatorów URI.

| Schemat | Wpisy w świeżej odpowiedzi |
| --- | ---: |
| Tematy INSPIRE | 35 |
| EnvironmentalDomain | 11 |
| MediaTypeValue | 8 |
| EnvironmentValue | 3 |
| ConditionOfGroundwaterValue | 5 |
| WaterSalinityValue | 7 |
| WaterPersistenceValue | 5 |
| SoilInvestigationPurposeValue | 2 |

Polskie etykiety pochodzą od dostawcy, nie z tłumaczenia AI. Przy braku PL
wyświetlamy EN i oznaczamy język. Definicje, jeśli dostępne, są w EN;
puste pozostają puste. Pełny tekst pozostaje w definition_raw, zwykłe
summary jest ograniczane istniejącym kontraktem safe_entry do600znaków.

## Pochodzenie i granice zakresu

Źródła pierwotne sprawdzone 2026-10-02:

- https://op.europa.eu/en/web/eu-vocabularies/inspire-registry
- https://op.europa.eu/en/web/webguide/uris

Parafraza: rejestr dostarcza identyfikatory, etykiety i definicje; endpoint
Cellar pozwala pobierać dane RDF. W tym pakiecie używamy istniejącego
dopuszczonego HTTPSpublications.europa.eu/webapi/rdf/sparql, bez nowych
hostów, wyjątków robots, akceptacji nowych warunków ani pobrań dzieci URI.

76wartości NIE oznacza całego INSPIRE/EU Vocabularies/EuroVoc.
35rekordów theme obejmuje również osobny wpis ac-mf; nie usuwamy go ani
nie twierdzimy, że dyrektywa zawiera35obecnie obowiązujących tematów.
Nie pobieramy statusów ważności, dat, oznaczeń wycofania, hierarchii,
notacji, geometrii ani aktów. Nie wnioskujemy kodu z końcówki URI.
URI HTTP zachowujemy jako identyfikator dostawcy, nie zamieniamy go na HTTPS.

Rekordy mają dataset_catalogue/inspire_code_value=true, nie news_notice
ani potwierdzoną zmianę prawa. Status kodu i obowiązywanie prawne false
oznaczają brak weryfikacji, nie nieważność wartości.
published_at/source_updated_at pozostają NULL dla wszystkich76.
as_of i last_successful_import w eksporcie są czasami lokalnymi, nie datami
publikacji. Późniejsze zniknięcie kodu z odpowiedzi nie powoduje kasowania
historii ani automatycznej deklaracji wycofania; widok pokazuje zapisane
wartości, nie potwierdzony jednoczasowy/aktualny stan całego rejestru.
W czasie importu lokalny widok może obejmować aktualizowane rekordy z różnych
wersji; nie nazywamy go transakcyjnym snapshotem dostawcy.

## Parser i konfiguracja

inspire_codes.py: dokładny profil/config i stałe osiem URI w VALUES,
SKOS Concept/inScheme, wymagane EN label, opcjonalne PL label/EN definition.
Jedna odpowiedź GET, LIMIT1001 z limitem1000 jako sentinel, bez OFFSET,
automatycznego zwiększania limitu lub stronicowania między wersjami.
Każdy schemat musi mieć co najmniej jedną poprawną wartość.
Ścisły head/bindings/language/URI/membership/order/uniqueness/budgets;
duplicate JSON keys/missing scheme/schema mismatch odrzucają całą paczkę.
Tożsamość scheme+concept, nie tytuł ani miejsce na stronie.
Zmiany etykiet/definicji przechodzą istniejącą historię wersji.

configure_inspire_codes: dry-run domyślnie/noHTTP, lock+transaction,
osobny disabled/unapproved kanał1440min. Wymaga oryginalnego schematowego
profilu i jego jedynego powiązania180, zachowuje owner/topics/reuse notes.
Chroni admin konfigurację, obce powiązania i gap needs_connector;
powtórzenie zachowuje audyt, historię i zgodę. Nie zmienia primary profilu,
workbooka ani integration_note pierwotnego zakresu.

```text
python manage.py configure_inspire_codes
python manage.py configure_inspire_codes --apply
python manage.py audit_sources --source registry-180-inspire-codes
python manage.py ingest --source registry-180-inspire-codes --force --dry-run
python manage.py activate_registry --source registry-180-inspire-codes
```

To opis workflow; aktywacja i wznowienie zostały już wykonane — nie powtarzać
konfiguracji/importu/restartu bez potrzeby.

core.vocabularies: staff-only GET/no-store, approved exact profile only,
filtry przez ORM,400invalidform; eksport JSON z allowlist, nie cały metadata,
source config/owner/audit/errors. ETag aktualnych bajtów, nosniff, stała nazwa;
przekroczenie1000rekordów/5MiB409bez częściowego wyniku. HTML escapowany.
Nie zmienia ekspresji ani indeksu istniejącej wyszukiwarki wiadomości.

## Weryfikacja i eksploatacja

11celowanych testów PostgreSQL PASS0.208s po poprawce nazwy mock fixture
kolidującej z klientem Django; final675testów PASS26.701s.
Ruff347lintformat/check/nomigrations PASS, workerbuild PASS.
Istniejące ostrzeżenie pip-root dotyczy etapu budowy kontenera;
brak instalacji hosta, nowych zależności i migracji.
Parser live HTTP200/43085B/76/0/hash
565731213a822485343f193792a02be94c1813e8ac4dbb1b58152e2b0a6d1e2c.

Worker SIGTERM zakończył naturalnie exit0 o10:36:27Z. Następnie configure
dry/apply/repeat, fresh audit76/0, ingest dry76/0, activation dry1/apply1,
import76created, independent GET76unchanged. Worker wznowiony10:38:28Z,
image434200623fb13fbc247e75608a7e030d1785c2eee31da0288f8e4df78c4ff728,
unless-stopped. Wszystkie5usług running/DBhealthy;health-ready HTTP200.

Existingstaff RequestFactory: pełny panel/PLfilter/allJSON45781B/themeJSON22152B
HTTP200/no-store, JSONnosniff+actualETag;bad scheme400.
199Sources170active29disabled, cache15000000000B niezmieniony.
Pełna pozycja180/pod-epik4.5.6 i cały4.5 pozostają otwarte.
