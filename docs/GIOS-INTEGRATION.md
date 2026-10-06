# GIOŚ — rozpoznanie publicznych usług

2026-10-01T14:07Z: [lista aktywnych stacji APIv1](GIOS-STATIONS.md) działa:
registry7/Source24/288metadata entries,co60min,source attribution+coordinates.
Użytkownik zatwierdził wyjątek wyłącznie dla znanego robots302->HTTPhome i
konkretnego endpointu stacji; audit/UI nie nazywają go robotsAllowed.
511tests/buildPASS, import288/replay288unchanged/workerexit0resumed.
To zastępuje blokadę listy stacji7, nie blokady nowych API dane.gios.gov.pl,
INSPIRE, sensorów/pomiarów i pozostałych6pełnych luk GIOŚ.

Aktualizacja2026-09-30T14:15Z (16:15Europe/Warsaw): pakiet obsługi plików
zastępuje wcześniejsze ograniczenie eksportu `.bin` dla dokładnego profilu GIOŚ.
Eksport job1: `GIOS-halas-drogowy-2024.xls`, MIMEapplication/vnd.ms-excel.
Typ/nazwa pochodzą z zatwierdzonej mapy, nie nagłówków serwera ani danych formularza;
zmiana URL/config lub skrótu konfiguracji zadania daje bezpieczny fallbackbin.

Karta/data8 pokazuje osobno historyczny rok2024 i retencję lokalnej kopii,
bez nieprawdziwego komunikatu o wycinaniu starych pomiarów i braku danych.
Zlecenia staff mają polskie stany, postęp transferu i datę wygaśnięcia.
Link eksportu dostępny tylko dla complete/niewygasłej/zatwierdzonej kopii;
nowa próba tylko dla niewygasłego blocked z aktywną zgodą.
Katalog/data/?kind=file rozróżnia zatwierdzenie pliku od harmonogramu.

7nowych testów/26celowanych/337pełnychtestów,lint/format/buildPASS.
Live/data8 i katalogfileHTTP200; descriptor rzeczywistego job1 poprawny,
integracja HTTPeksportu/uprawnień w izolowanych testach, bez tworzenia kont
ani logowania za użytkownika na działającej instalacji. Bez nowych pobrań,
migracji lub zmiany źródłowego pliku. Nie zamyka nowego APIhałasu.

## Historyczny plik hałasu — pierwszy rzeczywisty zasób

2026-09-30T14:06Z: profil `gios-noise-road-2024-file` (data8, rejestr48),
wyłącznie na żądanie, nieaktywny w automatycznym harmonogramie.
GIOŚ/dane.gov.pl zbiór1404, zasób989426, wyniki hałasu drogowego za2024r.
Metadane zbioru wskazują CC BY4.0 i brak dodatkowych warunków, zasób nie zawiera
danych chronionych według deklaracji katalogu. Zachowano atrybucję GIOŚ i adres
zbioru; nie jest to samodzielny audyt prawny wszystkich danych GIOŚ.

`configure_gios_noise_file` sprawdza tożsamości/właściciela/powiązanie zbioru,
licencję, wersję zasobu oraz1024B przez Range. Dry-run domyślny, `--apply`
tworzy zatwierdzony profil bez kolejki i bez zmiany stanu rejestru48.
Chroni konfigurację administratora; zmiany metadanych wymagają ponownego przeglądu.
HTTP206/Range0–1023/754688, silny ETag, sygnatura kontenera OLE zgodna z XLS.
Nie parsuje arkusza ani nie waliduje merytorycznie wyników pomiarów.

Jednorazowy test rzeczywisty: queue_download dry-run, następnieapply job1,
run_downloads przez wspólną blokadę -> complete754688/754688,1chunk.
SHA256 `1104c27c78ac16a821ece3c4958421098d7ba55ee209ce87a88f75707d279a7f`;
niezależne przeliczenie zapisanych chunków zgodne. Kopia wygasa2026-10-30T14:04:17Z.
Retencja30dni dotyczy lokalnej kopii od zlecenia, NIE wieku pomiarów2024.
Profil/data8HTTP200. Eksport staff-only, domyślna nazwa `.bin`; zawartość jest
oryginalnym XLS i po pobraniu należy zachować rozszerzenie `.xls` do otwierania.
Testy eksportu/uprawnień wykonane w izolowanej bazie, nie jako zalogowany
użytkownik produkcyjnej instalacji. Nie pobrano pozostałych roczników.

3nowetesty/330pełnychtestów,19testów mechanizmu pobrań,lint/format/buildPASS.
Brak nowej migracji, zmiany quota15GB lub pozostałych reguł retencji.
Rejestr48 nadalneeds_connector: historyczny plik nie zastępuje nowego API.

Polecenia: `configure_gios_noise_file [--apply]`,
`queue_download --source gios-noise-road-2024-file [--apply]`.
Powtórne zlecenie w okresie ważności używa istniejącego zadania.

## Rozpoznanie katalogów API

Aktualizacja2026-09-30T13:53Z: karty rejestru6/46–50 pokazują oficjalne katalogi
nowych API i datowany wynik audytu, bez wywołań HTTP przy otwieraniu strony.
Portal6 obejmuje6 zbiorów; karty tematyczne tylko właściwe zbiory (47 ma2).
Mapa w ingestion/gios_catalogue.py jest współdzielona z discover_gios.
Nie utworzono kanałów ani nie zmieniono statusów integracji.

Izolowany audyt inspire.gios.gov.pl: odczyt robots kończy się
`Remote service unavailable`. Próby przez Client dla ATOMmonit_noise i WFS
GetCapabilities zatrzymały się na tej kontroli; nie odczytano treści usług.
Host dopuszczono tylko w procesie audytu, bez trwałego dodania do allowlisty.
To problem dostępności polityki, nie potwierdzony zakaz ani ocena zawartości API.

Weryfikacja:7testów kart/rozpoznania,327pełnychtestów,Rufflint/format i buildPASS;
działający /registry/6/ HTTP200,6linków katalogowych. GET nie aktywuje źródła,
POST405; inne karty nie pokazują ostrzeżenia GIOŚ.

Stan2026-09-30T13:28Z. To rozpoznanie metadanych katalogowych, nie wdrożenie
konektorów pomiarów. Rejestr6/46–50 nadal `needs_connector`.

Oficjalny komunikat GIOŚ z2026-07-20 potwierdza nowe publiczne API i ich
publikację w dane.gov.pl:
https://www.gov.pl/web/gios/nowe-api-gios-juz-dostepne

## Wyniki odczytu

- Instytucja100 w API dane.gov.pl: Główny Inspektorat Ochrony Środowiska,
  38 zbiorów/275 zasobów według metadanych instytucji. Nie pobrano275 zasobów.
- Zapytanie tekstowe GIOŚ zwraca tylko16 wyników, w tym inne instytucje.
  Nie używać wyszukiwania nazwy jako kompletnego filtra pochodzenia.
- Sześć konkretnych zbiorów nowych API:20 adresów zasobów.
  Dane katalogowe dostępne przez HTTPS z poprawną kontrolą robots.
- `https://dane.gios.gov.pl/robots.txt`: HTTP200, Content-Type text/html,
  492B, HTML aplikacji Dane GIOŚ zamiast polityki. Wspólny Client zwraca
  `Cannot confirm robots policy`. Nie wyłączono kontroli, nie odczytano API
  pomiarów i nie dodano wyjątku dla HTML jako robots.

| Rejestr | Zbiór dane.gov.pl | Liczba zasobów | Zakres katalogu |
| --- | --- | --- | --- |
| 46 | 34655 | 1 | Programy monitoringu wód powierzchniowych |
| 47 | 34649 | 2 | Punkty i programy monitoringu wód podziemnych |
| 47 | 34653 | 4 | Metodyki, oceny, trendy i plik programu |
| 48 | 34644 | 9 | Hałas: pomiary, zasięgi, ekspozycja, działania, koszty, poziomy |
| 49 | 34584 | 2 | PRTR: uwolnienia i transfery |
| 50 | 34643 | 2 | Rejestr awarii i zakładów zagrożonych awarią |

Adresy pochodzą z `attributes.link` zasobów, nie z odgadywania tras:
`/api/wody-powierzchniowe/v1/`, `/api/wody-podziemne/v1/`, `/api/halas/v1/`,
`/api/prtr/v1/`, `/api/powazne-awarie/v1/` na dane.gios.gov.pl.
Katalog pokazuje `numerStrony=0&liczbaElementowNaStronie=10`; nie potwierdzono
jeszcze semantyki paginacji, schematu odpowiedzi ani pola modyfikacji pomiarów.

## Powtarzalne sprawdzenie

`docker compose exec -T web python manage.py discover_gios`

GET-only, brak parametrów umożliwiających dowolne URL, bez zapisów do bazy,
aktywacji lub pobierania plików. Sprawdza instytucję100, tożsamość zbiorów,
kompletność list zasobów (<=100 na zbiór), unikalność i zatwierdzony host/ścieżkę.
Zmiana katalogu odrzuca raport zamiast zwracać pozornie kompletny wynik.
Odczytuje osobno politykę hosta, ale nie testuje dostępności każdego zasobu.

4 testy odczytowego polecenia/324pełne testy PASS; Ruff lint/format i build workera PASS.
Testy sprawdzają brak pobierania zasobów, blokadę robots, błędną instytucję,
niekompletność oraz niezaufane adresy. Brak zmian schematu, retencji i quota.

## Następne kroki

1. Potwierdzić poprawną politykę robots na hostach usług lub oficjalny alternatywny
   interfejs. Korekta po stronie GIOŚ umożliwi ponowienie `discover_gios`.
2. Po uzyskaniu dozwolonego dostępu: dokumentacja OpenAPI i mała próbka,
   pole czasu/jednostki/geografia/licencja, paginacja i stabilne identyfikatory.
3. Implementować pierwszą operację wód powierzchniowych46, testować zapis,
   historię30dni/15GB i wznowienie przed rozbudową pozostałych usług.
4. Konfiguracja dry-run/apply, audyt i rzeczywisty zapis; dopiero wtedy zmiana
   statusu rejestru. Same adresy/metadane nie zamykają pakietu.

Alternatywne zasoby historycznego monitoringu hałasu (zbiór1404) wskazują
inspire.gios.gov.pl ATOM/WMS/WFS/SOS oraz roczne CSV/XLS na api.dane.gov.pl.
W tym kroku odczytano tylko metadane adresów; dostęp/licencja i parsery nadal
wymagają audytu. Nie zastępują całego nowego API hałasu.
