# Kompletność, integralność i scalony eksport PIG

Aktualizacja2026-10-01T09:17+02:00: MIDASzłoża complete18147/18147/182kopie,
32930146B/fullscope+payloadverified07:17Z/exporteligible. Zastępuje running
poniżej; pozostałe5fullscopeverified06:49Z. Certyfikacja dotyczy momentu kontroli,
nie całej instytucji i nie przyszłej dostępności kopii. Nowego scalonego eksportu
MIDAS0 nie testowano przez przeglądarkę; wykonany odczytowy audyt lokalnych kopii.

Wdrożone2026-10-01T00:30+02:00 (2026-09-30T22:30Z).
Panel `/data/coverage/` i sekcja kart rozdzielają:

- zapisany koniec przebiegu (`complete`),
- niewygasłe kopie według metadanych (strony, obiekty, brakujące kopie),
- rzeczywistą integralność payloadów i kompletność przypiętego spisu ID.

Panel i zwykłe GET nie czytają wszystkich payloadów, nie wykonują HTTP do
dostawców, nie zapisują statusu ani nie resetują postępu. Metadane nie dowodzą
integralności bajtów. Niespójne, powtórzone strony i obce hashe są raportowane
konserwatywnie, nie jako pełny zbiór. Bez zachowanych kopii count pozostaje
nieznany — nie dopisujemy brakujących liczb. Liczniki dotyczą najnowszego przebiegu.

Operator w kontenerze web może wykonać:

Aktualizacja2026-10-01T08:51+02:00 zastępuje blokadę08:42: `--require-verified`
zweryfikowane po zatwierdzonym wznowieniu usług.13testówcoverage+4INSPIRE PASS,
405pełnychPG PASS18.302s,Ruff234/check/nomigrations/buildweb/workerPASS.
LiveGZWPexit0/fullverified;MIDASzłożaexit1expected16200/18147running06:50Z.
Readonlypełne sprawdzenie06:49Z: pięć ukończonych PIGspisów nadal scope_verified,
bez brakujących kopii; JCWPd pozostaje ponad limitem eksportu. Retencja/cache bez zmian.

```text
python manage.py data_coverage
python manage.py data_coverage --source pig-gzwp-scan --verify-payloads
python manage.py data_coverage --source pig-gzwp-scan --require-verified
```

CLI używa spójnej transakcji PostgreSQL REPEATABLE READ READ ONLY; nie blokuje
workera advisorylockiem. Odczyt lokalny, maks10000metadanych stron/spis100000ID,
jedna kopia naraz do5MiB, bez HTTP/zapisów. SHA256 i długość payloadu, struktura,
pole OBJECTID, kolejność/unikalność, geometrie WGS84, pochodzenie i pełny hash
spisu są sprawdzane. `scope_verified` może być true tylko dla ukończonego,
niewygasłego pełnego przebiegu z prawidłowym hashem konfiguracji i wszystkich ID.
`verified_available` samo w sobie nie oznacza kompletności. Wynik nie jest
utrwalany jako wieczna certyfikacja: dotyczy konkretnego momentu odczytu.

Opcjonalne `--require-verified` automatycznie sprawdza payloady i wymaga pełnego
zweryfikowanego pokrycia każdego wybranego przebiegu. Po wypisaniu raportu JSON
na stdout kończy się kodem niezerowym, jeśli przebieg trwa, nie rozpoczął się,
brakuje niewygasłych kopii, integralność/hash jest błędna albo nie ma żadnych
pasujących kanałów. Błąd komendy trafia na stderr. Zwykły raport (również z
`--verify-payloads`) nadal służy diagnostyce i nie wymaga pełnego pokrycia.
Warunek nie wymaga scalonego eksportu: JCWPd może przejść weryfikację nawet gdy
przekracza limit pojedynczego pliku. Bez zapisów, HTTP i zatrzymywania workera.

## Pełny plik na żądanie

`/data/harvests/<id>/download/` jest GET-only/staff-only. Link jest na karcie,
gdy metadane wskazują pełne pokrycie i rozmiar mieszczący się w limicie.
Przed odpowiedzią wszystkie kopie są ponownie walidowane w spójnej transakcji.
Brak stron, wygaśnięcie, uszkodzenie, obcy profil/hash lub przekroczenie rozmiaru
powoduje404, bez udostępnienia częściowego pliku. Zwykłe kopie pozostają osobno.

Powstaje GeoJSON FeatureCollection z pełnym spisem ID, nie pełną obsługą
instytucji. `partial=false` oznacza wyłącznie ten spis; nie jednoczasową wersję
atrybutów ani aktualne pomiary. Zachowano źródłowe atrybuty/warunki, dodano
receipts stron z czasami pozyskania/wygaśnięcia, zakres scalenia i czas eksportu.
ETag jest SHA256 rzeczywistych bajtów. Cache-Control private/no-store i nosniff.

Istniejący limit DATA_MANUAL_MAX_BYTES=50MiB jest zachowany zarówno dla sumy
wejściowych payloadów, jak i wyjściowego pliku. Eksport używa tymczasowego spool:
do1MiB RAM, potem dysk kontenera, do50MiB na żądanie, zamykany wraz z odpowiedzią.
Nie tworzy nowych DataSnapshots i nie przedłuża retencji. To nie globalny limit
wszystkich równoległych eksportów; publiczne wdrożenie wymaga osobnej oceny
obciążenia/staff i miejsca na pliki tymczasowe. Nie podnosimy cachequota15GB.

## Wynik rzeczywisty

Kontrola22:17Z potwierdziła integralność i pełny spis: MIDASobszary6214,
MIDAStereny6038,hałdy1386,JCWPd174,GZWP140. Złoża7200/18147 nadalrunning.
Lokalne scalone eksporty sprawdzone przez RequestFactory z testowym obiektem
staff, bez tworzenia rzeczywistych kont/sesji:7321475B,7069772B,6888925B,
5045479B odpowiednio MIDAS1/MIDAS2/hałdy/GZWP, actualETagPASS.
To nie dowód logowania przez przeglądarkę — kontrolę uprawnień obejmują testy.
JCWPd payloady56101231B przekraczają50MiB; celowo brak scalonego eksportu,
jawny komunikat na karcie, pojedyncze strony dostępne. Nie zmieniono limitu.

10celowanych/398pełnych testów PostgreSQL PASS18.024s,Ruff229lint/formatPASS,
Django check/nomigrations i buildwebPASS. Panel HTTP200. Bez restartowania
usług, migracji schematu, resetowania harvestów, zmiany workbooka/retencji/D:.
Zakres modułu to6PIGidentityscans, nie ogólna certyfikacja STAC/WFS ani pełnego rejestru.
