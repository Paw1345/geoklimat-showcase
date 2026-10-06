# Funding & Tenders189 i ECHA CHEM186

Aktualizacja2026-10-01T09:20+02:00: oba katalogi **aktywne i zatwierdzone**,
co120min, po jawnej zgodzie użytkownika na odświeżenie wyłącznie workera.
SIGTERM/naturalnyexit0, świeże audytyHTTP200/114+233/0, dryactivation2/dryingest,
apply/import347created/repeat347unchanged/0createdupdated, worker wznowiony.
5usługrunning/DBhealthy;192Sources143active49disabled.347Itemsdataset_catalogue/
allpubupdatedNULL. Następne pobrania09:20Z/11:20+02:00.413pełnychPGPASS17.919s,
8celowanychPASS0.113s,Ruff238lintformat/check/nomigrations/buildworkerPASS.
Aktywacja/restart zakończone — nie ponawiać setup/zgody na ten wykonany restart.
Zastąpione2026-10-01T10:12+02:00: użytkownik zatwierdził wąską diagnostykę
LIFE POST. Próba31/31 ma wszystkie terminy przeszłe; brak importu/aktywacji
i nowego restartu. [FUNDING-SEARCH](FUNDING-SEARCH.md). ECHA bez zmian.

Poniższy opis rozpoznania09:15 jest historyczny: stan wyłączonych profili,
blokady audytów i oczekiwania na zgodę został zastąpiony wynikami powyżej.
Ograniczenia zakresu danych i brak zgody ECHA pozostają aktualne.
Historyczny brak zgody POST zastąpiono diagnostyką opisaną powyżej.

Rozpoznanie2026-10-01T09:15+02:00. Metadane dokumentacji, aktualne nabory
i dane substancji to odrębne zakresy. Obie pełne pozycje pozostają
`needs_connector`; workbook, cache15GB i retencja30dni bez zmian.

## Dwa katalogi dokumentacji GET

`funding_reference.py`: LIFE2027=114wpisów/110954B,
HORIZON=233wpisy/233984B. Ścieżki
`https://ec.europa.eu/info/funding-tenders/opportunities/static-content/reference-documents/{LIFE2027,HORIZON}.json?lang=en`
są opisane w [oficjalnej dokumentacji starszego API](https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/support/apis_old),
do której odsyła [aktualna dokumentacja](https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/support/apis).

Jeden GET/kanał, standardowe robots/DNS/5MiB/retry, do1000węzłów/6poziomów/
500widocznych wpisów. Walidacja całego drzewa, widoczności rodzica, dokładnego
profilu i odpowiedzi, etykiet, dat i linków HTTPS na4oficjalnych hostach.
Brak plików, childHTTP, tłumaczeń, poprzednich wersji, naborów i ocen prawnych.
Tożsamość=program+ścieżka kategorii+URL; powtórzenie URL w innym dziale jest
osobnym wpisem. Identyczny wpis odrzucony. Hash paczki stabilny względem kolejności.

Tytuły pochodzą z label, nie placeholdera `eUI needs it`. Daty DD-MM-YYYY oraz
źródłowy marker `Not published yet` (1Horizon) zachowane w metadata, bez
dodawania czasu/strefy publikacji, wejścia prawa w życie i deadline. Pełne
reference_metadata przy wpisie; zawarte tam linki wersji nie są pobierane.
Typ `dataset_catalogue` odróżnia katalog od aktualnego naboru.

```text
python manage.py configure_funding_reference
python manage.py configure_funding_reference --apply
python manage.py audit_sources --source registry-189-reference-life2027
python manage.py audit_sources --source registry-189-reference-horizon
```

Setup dry-run domyślny/noHTTP, apply atomowy, disabled/unapproved, interwał120min.
Chroni administratorconfig/linkage/audyt/approval, pełne needs_connector zachowane.
Dry/apply wykonano pod zwykłym advisorylockiem; dwie wcześniejsze próby apply
zgłosiły busy/bez zapisów. Dwa profile skonfigurowane, bez Items i aktywacji;
audyty po konfiguracji busy, nie są successful audits. Niezapisane livepróby
parserówHTTP200:114/0hashdf0024c7… i233/0hashf9653d10… . Aktywacja wymaga
oddzielnego zatwierdzenia odświeżenia workera, świeżych audytów, scoped
dryactivation/dryingest/apply/import/powtórzenia i wznowienia. Bez resetu.

## Aktualne nabory — historyczne rozpoznanie przed zgodą POST

Oficjalna dokumentacja w przeglądarce wskazuje HTTPSPOST SEARCH API
`https://api.tech.ec.europa.eu/search-api/prod/rest/search` z publiczną aplikacją
SEDIA. Nie wykonano POST i nie dopuszczono hosta ani wyjątku do transportu.
Wysłano pytanie o publiczne odczytowe wyszukiwanie bez credentials/wniosków/
danych osobowych — odpowiedź UNCONFIRMED. Dokument nie stanowi zgody.
Ewentualny wyjątek musi pozostać dokładnym endpointem z ograniczonym payloadem,
robotspolicy/DNS/budget/redirectcontrols, nie dowolnym klientem POST.

Statyczny Topic List:7618linków/1274991B, bez rozpoznanych prefiksów LIFE2026/27
i HORIZONCL5/CL62026. Nie używamy go jako bieżącego wykazu. Nie dowodzi braku
naborów w UE ani kompletności listy; GETkatalogu dokumentów nie zastępuje naborów.

## ECHA CHEM — warunki, nie pełna replikacja

GETrootHTTP200/66539B jest aplikacjąJS. Widoczny portal ma linki eksportów
REACH/DWD i list obowiązków/procesów. Search wymaga akceptacji legal notice;
nie zaakceptowano warunków, nie pobrano eksportów ani danych substancji.
Nie reverse-engineerowano niewidocznego API. Główny echa.europa.eu robots403
nie został ominięty. Nie twierdzimy, że publiczne API nie istnieje.

[Warunki ECHA CHEM](https://chem.echa.europa.eu/legal-notice) odczytane w UI,
dostawca oznacza Version1/2023-04-02. Parafraza, nie porada prawna:
kopiowanie całości lub istotnej części bazy wymaga uprzedniej pisemnej zgody
ECHA; możliwe prawa podmiotów trzecich i blokowanie masowego zbierania.
Sam publiczny link eksportu nie upoważnia do pełnej replikacji w GeoKlimat.
Dalszy etap wymaga ustalenia dozwolonego zakresu/licencji/zgody dostawcy,
nie tylko kliknięcia checkboxa przez agenta.186nie jest zaakceptowanym wyjątkiem.

## Widoki i testy

Karty186/189 mają datowane ograniczenia i oficjalne linki. GET nie odpytuje
źródeł ani nie zapisuje danych; faktyczny status kanałów jest poniżej ostrzeżeń.
8celowanychtestówPASS0.113s, pełne checks w VERIFICATION.md. Pokrywają kontrakt
4elementów, stablehash/zmiany, dateplaceholder, widoczność, duplikaty, schema,
host/credentials/depth/budget/profile, dry/apply/idempotencję/adminpreservation
i GETreadonlyUI/POST405. Pierwsza fixture poprawiona po kolizji nazwy z DjangoClient;
liveHorizon ujawnił poprawnie zachowany marker niepublikacji. Bez restartu usług.
