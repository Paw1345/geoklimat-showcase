# GDOŚ/Geoserwis — wykaz usług, nie pełne dane — 2026-09-30T17:12Z

Oficjalna podstawa:
https://www.gov.pl/web/gdos/dostep-do-danych-geoprzestrzennych
oraz pomocniczo https://www.gov.pl/web/ia/portale-interoperacyjnosci.
Nie zmieniono oryginalnego skoroszytu ani stanu needs_connector pozycji15/16.

## Co wdrożono

gdos_services.py: ściśle wskazana strona gov.pl, dokładny endpoint bez przekierowań,
jeden obszar editor-content i nagłówek Dostęp do danych geoprzestrzennych.
Parser do80 pozycji: odnośniki HTTPSsdi.gdos.gov.pl/wms lub/wfs;
obowiązkowe2 bazowe adresy, jawny schemat parametrów linków WFS1.0/shape-zip.
Tytuły pochodzą z tekstu odnośnika; brak wymyślonych opisów i dat publikacji.
Rekordy service_directory zachowują adres katalogu, typ usługi, operację,
typ obiektu, CRS, format i filtr regionalny. Bez HTTPdoSDI/ZIP/geometrii.
Zmiany wykazu mogą tworzyć wersje metadanych, ale nie oznaczają zmian geometrii,
nowej wersji WFS ani potwierdzenia dostępności usługi. Nie oznaczamy usuniętych
odnośników jako usuniętych zbiorów; wcześniejsze rekordy zachowują historię.

configure_gdos_services: dry-run domyślnie, --apply tworzy wyłączony,
niezatwierdzony registry-016-gdos-directory przy pozycji16, co120min.
Zachowuje istniejące konfiguracje administratora, audyt/aktywację przy powtórzeniu,
powiązania i pełną lukę konektora. Wszystkie operacje zapisujące korzystają
z blokady pobierania; nie obchodzimy zajętej blokady.

## Rzeczywisty odczyt i ostrzeżenia

GET oficjalnego wykazu HTTP200,46074B. Dry-run parsera30rekordów/0odrzuceń:
2bazowe usługi i28odnośników pobrania, w tym16regionalnych.
W16regionalnych odnośnikach CQL_FILTER zawiera dodatkowy znak% po końcowym
apostrofie. Zachowano dokładnie adresy i źródłowe filtry, bez ich naprawiania.
Wyłącznie ten rozpoznany wariant ma source_link_warning; inne naruszenia schematu,
duplikaty, obce endpointy, kontrolne znaki i przekroczenia budżetu blokują partię.
Ostrzeżenie wyświetla się na home/search/karcie dokumentu. Nie sprawdzono,
czy serwer toleruje te adresy; nie oznaczamy ich jako działających plików.

SDIrobots.txt:HTTP200,text/html,495B,stronaHTML zamiast polityki.
Próba GetCapabilities została zatrzymana na kontroli robots, bez pobrania XML.
Geoserwis/metadane:HTTP200,212BHTML, brak potwierdzonego katalogu maszynowego.
CRFOProot:HTTP200 do/CRFOP/,90931B, formularz wyszukiwania; nie wykonano POST,
logowania, automatyzacji sesji ani pobierania rekordów/PDF.
Nie ustalono licencji i schematu geometrii; pełne dane GDOŚ nadal otwarte.

## Stan uruchomienia — nie mylić z odbiorem

2026-09-30T17:12Z: użytkownik jawnie zatwierdził łagodny restart workera.
Poniższy wynik zastępuje wcześniejsze oczekiwanie na zgodę i brak importu:

- Worker zakończył bieżącą partię po SIGTERM z kodem0 o17:09:12Z; bez SIGKILL.
- Aktualny obraz workera zbudowany;6 testów GDOŚ w izolowanym PostgreSQL PASS.
- Świeży audit_sources: verifiedHTTP200/30/0, wyłącznie dla wykazu gov.pl.
- activate_registry dry-run:1 kwalifikujący się kanał; ingest dry-run:30/0.
- Aktywacja --apply tylko registry-016-gdos-directory; import30created/0updated.
- Powtórzenie: HTTP304/not_modified,0created/0updated; łącznie nadal30Items,
  w tym16 z ostrzeżeniem źródłowego filtra. Nie zmieniano ETagów dla wymuszenia GET.
- Worker uruchomiony z aktualnym obrazem o17:11:42Z; wszystkie5usług running,
  baza healthy. Import przeprowadzono w przerwie workera pod zwykłą blokadą.
- GET registry16, karta rekordu11517 z ostrzeżeniem, wyszukiwarka źródła
  oraz health/ready: HTTP200. Parser dostępny w nowym kontenerze.
- 184Sources/135aktywne/49wyłączonych;34luki konfiguracji i needs_connector16
  pozostają, bo monitor odnośników nie zastępuje konektora geometrii.

Kanał monitoruje wykaz co120min. Nie pobrano plików ani geometrii,
nie resetowano kursorów, nie zmieniono15GB/30dni ani innych kanałów.
Ten wynik nie stanowi odbioru eksperckiego pilota ani zamknięcia epika4.5.

### Historyczne przygotowanie — 2026-09-30T16:17Z

Po dry-run zapisano profil wyłączony/activation_approvedFalse.
audit_sources:verifiedHTTP200/30/0 dotyczy wykazu, NIE endpointów SDI.
Karta/registry16HTTP200 pokazuje kanał i pełną lukę needs_connector.
0zaimportowanychItems tego kanału; aktywacji i rzeczywistego importu nie wykonano.
184Sources/134aktywne/50wyłączonych;34luki konfiguracji bez zmian.

SIGTERM został odrzucony przez kontrolę bezpieczeństwa przed wykonaniem:
brak osobnej zgody na zatrzymanie workera. Niczego nie zatrzymano ani nie restartowano.
Pytanie o łagodny restart wysłano użytkownikowi; nadal bez odpowiedzi na ten moment.
Pierwsza konfiguracja była busy; późniejsze dry-run/apply/audit wykonane pod wolną
blokadą. Nie resetowano harvestów ani nie kasowano danych.

Historyczny plan, wykonany17:12Z: po jawnej zgodzie łagodnie odświeżyć worker bez wymuszania zakończenia,
potwierdzić aktualny kod, ponowić świeży audyt jeśli minęło24h, następnie
activate_registry --source registry-016-gdos-directory (dry-run potem --apply),
ingest --source registry-016-gdos-directory --force --dry-run, rzeczywisty import
i powtórzenie bez nowych rekordów. Wyniki wykonania opisano powyżej.
Nie aktywować kanału, gdy działający worker ma stary parser.

## Weryfikacja kodu i uruchomienia

6nowych/358wszystkichtestów PostgreSQL PASS; Ruff204lint/formatPASS,
Django checkPASS, makemigrationsnodrift. Pierwszy live dry-run wykrył rzeczywisty
wariant błędnego filtra; dodano jawne ostrzeżenie i regresję, następny30/0PASS.
Testy obejmują noSDI-fetch, brak dat, tożsamość, parametry, duplikaty/budżet,
powtórny import i zmianę etykiety z historią, ochronę konfiguracji i rejestru,
ostrzeżenie w3widokach i zachowanie błędnego adresu bez korekty.
Pełne358testów dotyczą przygotowania16:17Z; po zgodzie ponownie uruchomiono6testów
GDOŚ, Ruff lint/format dla3plików i Django check: PASS. Build aktualnego workera
PASS, restart i rzeczywisty import potwierdzone17:12Z powyżej.
Ostrzeżenie BeautifulSoup o tytule wyglądającym
jak URL jest niefatalne: tytuły bazowych usług są dosłownymi adresami źródłowymi;
parser nie wykonuje dodatkowego HTTP. Nie wyciszano globalnie ostrzeżeń.
Nie zmieniono schematu/retencji/cache15GB, domyślnych filtrów ani migracjiD:.
