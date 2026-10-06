# BDL — diagnostyka geometrii, bez kolektora

Pakiet4.5.4, 2026-10-03T17:45Z. CLI bdl_probe jest gotowe i sprawdzone na żywo.
Nie tworzy kanałów danych, kopii, przebiegów, kursorów ani harmonogramów.
Aktywny katalog20kolekcji/Source201 pozostaje bez zmian.

2026-10-03T18:04Z: [mechanizm oryginalnych kopii](BDL-RAW-SAMPLES.md) gotowy,
profile22–24 wyłączone/niezatwierdzone/0kopii. probe nadal bez zapisu i aktywacji.

## Warunki i zakres

[Strona OGC API](https://ogcapi.bdl.lasy.gov.pl/?f=html) i świeży dokument
OpenAPI deklarują CC BY4.0 oraz termsOfService wskazujący tę licencję.
To deklaracja tego API, nie potwierdzenie warunków wszystkich zasobów BDL.
[Regulamin portalu](https://www9.bdl.lasy.gov.pl/portal/regulamin) zawiera
zasady wskazania źródła/czasu wytworzenia i pozyskania, dalszego udostępniania
w pierwotnej formie oraz ostrzeżenie o poglądowym charakterze map.
[Formularz zamówienia](https://www.bdl.lasy.gov.pl/portal/wniosek) przewiduje
również informowanie operatora o przetwarzaniu. Formularza nie wysyłano,
warunków nie akceptowano za użytkownika, operatora nie kontaktowano.
Zakres stosowania tych zasad do trwałych kopii API wymaga dalszej weryfikacji.

Próba nie przedstawia danych jako dokumentu urzędowego, nie wyznacza aktualności
na podstawie roku a_year i nie wnioskuje o objętości całości z pierwszego obiektu.
Nie potwierdza pełnego zakresu Polski ani spójności pomiędzy stronami.

## Polecenia

```powershell
docker compose exec -T web python manage.py bdl_probe
docker compose exec -T web python manage.py bdl_probe --collection rdlp --fetch
docker compose exec -T web python manage.py bdl_probe --collection nadlesnictwa --fetch
docker compose exec -T web python manage.py bdl_probe --collection lesnictwa --fetch
```

Domyślnie dry-plan: brak HTTP i DB. Jawne --fetch: dwa GET przez dotychczasowy
Client (OpenAPI+jedna strona limit1) oraz sprawdzenia robots. Każda odpowiedź
<=5MiB, bez zmiany transportowych timeout/retry. To nie limit całego ruchu
sieciowego/retry/robots ani sztywna granica czasu całego polecenia.
Offset0..1000000, wyłącznie trzy zatwierdzone kolekcje. Nie podążamy za next.
Raport pokazuje rozmiar/hash/count/rok/typ geometrii, nie surowe dane obiektu.

Walidacja: dokładne URL/HTTP200/BDLidentity/declaredAPIlicense, brak podwójnych
kluczy JSON i NaN/Infinity, counts niebool/spójne z offsetem, Feature/id/a_year,
Polygon/MultiPolygon i zamknięte pierścienie WGS84<=100000pozycji.
Self dokładny; wyłącznie offset0 może być pominięty przez serwer (potwierdzone
na żywo). Next musi zachować host/path/GET/limit1/offset+1 i opcjonalne f=json.
Zmiana licencji odrzuca próbę przed GET geometrii. Błąd CLI nie ujawnia payloadu.

## Dowody

Wszystkie trzy próby: HTTP200, allowed-or-404, po jednym poprawnym MultiPolygon,
a_year2026, poprawny next niepobrany. Deklaracja liczby wszystkich obiektów
pochodzi z numberMatched, nie z pełnego pobrania.

| Kolekcja | numberMatched | Liczba pobrana | Bajty odpowiedzi |
|---|---:|---:|---:|
| RDLP | 17 | 1 | 2332088 |
| Nadleśnictwa | 429 | 1 | 916770 |
| Leśnictwa | 5259 | 1 | 128490 |

9nowychtestówPASS0.048s; pełne855PGtestsPASS51.114s, Rufflint/format414PASS,
checkPASS/brak migracji/buildwebworkerPASS.5usługrunning/DBhealthy.
Bez restartów/aktywacji/resetów/cache15GB/retencji30dni/XLS/Gitpush.

Scenariusz odczytowy17:45Z: deklarowany cache10159917003B/quota15GB.
Hipotetyczne3strony*5MiB dziennie*30dni=471859200B plus rezerwa1GiB mieściły
się w tej chwili. Nie jest to pomiar wolnego dysku, gwarancja wzrostu innych
kolektorów, koszt całej bazy ani zgoda/ochrona budżetu dla aktywacji.

## Następny etap

Rozstrzygnięcie warunków trwałych kopii API i atrybucji, zachowanie oryginalnych
bajtów/hash/czasu pozyskania, atomowy dzienny limit z kontrolą cache,
ograniczona paginacja i wznowienie bez duplikatów. Regionalne wydzielenia
stanowią osobny profil, nie automatyczne rozszerzenie tej diagnostyki.
Nie zamykamy needs_connector39 ani pełnego4.5.
