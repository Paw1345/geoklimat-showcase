# PIG — pierwszy pakiet GIS

Aktualizacja2026-10-04: [SOPO zatrzymane](SOPO-ACCESS-HOLD.md) po sprawdzeniu
regulaminu. Source187 wyłączony/niezatwierdzony,15rekordów historycznych zachowano.
Poniższe wyniki aktywacji SOPO z2026-09-30 są historyczne, nie stan bieżący.
Inne usługi PIG186/188/189 pozostają aktywne; brak pobrania geometrii SOPO.

Aktualizacja2026-09-30T20:35Z: Geośrodowisko64 WMS odpady wydobywcze aktywny,
4opisy/import4created/repeat4unchanged;razem4WMS/29opisów.3kolektory MIDAS
po100geometrii/atrybutów: [PIG-DATA](PIG-DATA.md).377tests/Ruff217/buildPASS,
5usługrunning. Stan19:52Z poniżej jest historyczny; zastępuje brak kanału64.

Stan2026-09-30T19:52Z: trzy kanały aktywne po zatwierdzonej kontynuacji.
Zastępuje przygotowanie19:22Z i oczekiwanie na zgodę. SIGTERMworker exit0
19:42:40Z; configure dry/apply, świeże audyty verified7/15/3,0odrzuceń,
activationdry3/ingestdry25/activationapply3. Pierwsze importy25created,
powtórzenie25unchanged/0created/0updated/0rejected, HTTP200; failures0.
Interwał360min, najbliższe terminy2026-10-01T01:44:30Z–01:44:43Z.

Kontrola UI wykryła grupowanie warstw przez wspólny URL. Poprawiono izolację
service_capabilities w publications.py; repair_pig_groups dry22/apply22/repeat0
rozdzielił tylko nowe niepotwierdzone grupy PIG, bez usuwania rekordów/historii.
Komenda odmawia zmiany potwierdzonych/mieszanych grup, profili i własnych notatek.
25Items/25Publication/25ItemVersion; wszystkie daty publikacji/aktualizacji NULL.
Po ponownym SIGTERMexit0at19:51:20Z worker wznowiony19:51:48Z na nowym obrazie.
5usług running/DBhealthy; registry36/62/63/65,item13606,ready HTTP200.
GET wyszukiwarki catalogues pokazuje7/15/3 oddzielnych wyników, publications0.

## Rzeczywisty odczyt próbny

| Profil | Usługa i nazwane warstwy | Wynik |
|---|---|---|
| MIDAS | Usługa +6 warstw złóż/terenów/obszarów górniczych, również archiwalnych | HTTP200,19622B,7 rekordów,0 odrzuceń |
| SOPO | Usługa +14 warstw osuwisk/geozagrożeń | HTTP200,42318B,15 rekordów,0 odrzuceń |
| Hydrogeologia | Usługa +2 warstwy obszarów bilansowych | HTTP200,4127B,3 rekordy,0 odrzuceń |

Łącznie25 opisów/22 nazwane warstwy, przez niesaved Source i aplikacyjny Client,
bez zapisów Items/aktywacji. Dokładne endpointy w ingestion/pig_services.py.
Publiczny GET z robots/DNS/HTTPS/redirect/limit5MiB; brak nowego wyjątku robots.
Hosty cbdgmapa.pgi.gov.pl i epsh.pgi.gov.pl wskazane przez źródła pierwotne:
[CBDG GIS](https://baza.pgi.gov.pl/geoportal/uslugi/gis),
[MIDAS — dane przestrzenne](https://www.pgi.gov.pl/midas/dane-przestrzenne.html).

## Zakres i zabezpieczenia

Adapter śledzi opis usługi i każdą nazwaną warstwę osobno. Hash pełnego XML usługi
i XML warstwy pozwala rejestrować zmiany metadanych. Zmiana GetCapabilities
NIE potwierdza zmian obiektów przestrzennych. record_kind=service_capabilities,
bez wymyślonych dat; oryginalne Fees/AccessConstraints zachowane, bez założenia
jednolitej licencji. Nie wykonuje GetMap/GetFeatureInfo/GetFeature, nie pobiera
geometrii, pomiarów, SHP/ZIP ani innych dokumentów.

Dokładny profil/adres/wersja/namespace WMS1.3.0; UTF8 bez DTD/entity/UTF16,
<=200 elementów Layer, unikalne nazwy i wymagane tytuły. Cały XML walidowany
przed zapisem partii. Strona zabezpieczeń HTML200 nie jest poprawnym katalogiem.
CBDG/GEOLOG/SOPO/PSH/MIDAS zwracały HTML200/212B, CBDG GIS Incapsula955B;
nie omijano zabezpieczeń. e-MGśP/start/ zwrócił3936B opisu historycznych edycji,
a nie machine API.64Geośrodowisko pozostaje bez nowego kanału.

Powiązania:36/37 z trzema usługami,62SOPO,63hydro,65MIDAS.
Pełne luki needs_connector zachowane: to nie pełny katalog CBDG/GeoLOG/hydro/MIDAS.
Konfiguracja tworzy wyłączone/niezatwierdzone kanały, interwał360min,
dry-run domyślny, transakcja+advisory lock; zachowuje profile administratora,
istniejące audyty/aktywacje i oryginalne master/technical/skoroszyt.

## Procedura uruchomienia / ponownej instalacji

Po zgodzie: SIGTERM tylko workera i potwierdzenie exit0, bez force/resetu;
configure_pig_services dry-run/--apply. Osobne audit_sources dla
registry-036-pig-midas-wms, registry-036-pig-sopo-wms, registry-036-pig-hydro-wms.
Świeży verified audit, załadowany aktualny kod, activate_registry dry-run i ingest
--force --dry-run muszą poprzedzać aktywację --apply/import/powtórzenie.
Potwierdzić brak duplikatów/UI i wznowić worker.

Weryfikacja końcowa:19 celowanych/372 pełne testy PostgreSQL PASS15.390s,
Ruff lint/format214 plików PASS, Django check PASS, brak migracji, build worker PASS.
Testy obejmują UTF16, zmianę metadanych, odrębne grupy i ochronę potwierdzeń.
Wyniki historyczne370 i przygotowanie bez zapisu zastąpione powyższym uruchomieniem.
Cache15GB/30dni i wstrzymana migracja D: bez zmian.
Kolejny zakres: publiczne WFS/REST, rzeczywiste dane Polski, licencje i pliki na żądanie.
2026-10-04: lokalny GIS ma [tryb geometrii](SPATIAL-GEOMETRY.md).
Przecina poligon/bbox planarnie i waliduje topologię wszystkich wybranych
obiektów bez naprawiania danych. GZWP próba140obiektów/127261pozycji PASS;
hałdy pierwsze25stron zawierają10niepoprawnych topologicznie geometrii,
przy poprawnej integralności kopii. Tryb geometry bezpiecznie odmawia;
envelope pozostaje bez dowodu topologii. Nie maskaPLUE/nowy import/pełny4.5.
