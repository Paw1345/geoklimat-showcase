# PIG — hałdy i hydrogeologia: geometrie oraz atrybuty

Aktualizacja2026-10-01T00:30+02:00: ukończone hałdy1386/JCWPd174/GZWP140,
spisy i wszystkie zachowane payloady zweryfikowane. [DATA-COVERAGE](DATA-COVERAGE.md)
opisuje panel i nowy scalony eksport staff-only do50MiB. JCWPd56101231B ponad
limit, nadal jako pojedyncze strony; pozostałe dwa pełne eksporty sprawdzone.

Kontrola2026-09-30T21:54Z: worker sam przesunął każdy nowy przebieg z4do6stron,
odpowiednio300hałd/60JCWPd/60GZWP, collecting/noerror. Tabela21:52Z poniżej
pozostaje punktem kontrolnym pierwszego importu, nie bieżącym licznikiem.

Pakiet aktywny od2026-09-30T21:52Z po potwierdzeniu kontynuowania uruchomienia.
Worker zakończył się łagodnie przez SIGTERM(exit0 21:48:13Z), następnie configure
dry/apply i audyty wszystkich3profili zakończyły się poprawnie. Każdy kanał został
zatwierdzony, aktywowany i dwukrotnie pobrany. Worker wznowiono21:51:46Z.
Wcześniejsze próby busy nie wykonały zapisów; blokady nigdy nie obchodzono.

| Kanał / karta | Zapisane strony | Obiekty / spis | Payload w cache | Stan |
|---|---:|---:|---:|---|
| pig-haldy-scan / `/data/15/` |4|200 /1386|1154600 B|running|
| pig-jcwpd-scan / `/data/16/` |4|40 /174|14385268 B|running|
| pig-gzwp-scan / `/data/17/` |4|40 /140|1617089 B|running|

Punkt kontrolny21:52Z, nie licznik na żywo ani pełny zbiór. Razem12kopii,
280obiektów i17156957B; kolejne strony pobiera scheduler. Próby odczytowe poniżej
są wcześniejszym rozpoznaniem i nie zastępują aktualnego stanu zapisanych przebiegów.

| Profil | Rejestr | Usługa / warstwa | Spis dostawcy | Strona | Payload pierwszej strony |
|---|---:|---|---:|---:|---:|
| pig-haldy-scan |64| geosrodowisko/haldy / 2 |1386|50|259963 B|
| pig-jcwpd-scan |63| hydrogeologia/jcwpd / 2 |174|10|2320370 B|
| pig-gzwp-scan |63| hydrogeologia/gzwp / 0 |140|10|817462 B|

Liczby w tabeli pierwszych stron pochodzą z publicznych prób z2026-09-30.
Każdy endpoint ma postać
`https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/{usługa}/MapServer/{warstwa}/query`.
Nie dodano dowolnych adresów, nowych hostów, zapytań SQL ani uwierzytelnienia.

## Zakres biznesowy

- Hałdy: powierzchniowe obiekty i ich atrybuty; bez punktów obiektów górniczych
  oraz rastra spadków terenu. Nie cały portal Geośrodowisko / e-MGśP.
- JCWPd: wyłącznie cykl planistyczny 2022–2027. Warstwa 2028–2033 nie jest
  automatycznie wybierana jako bieżąca, nawet jeśli jest domyślnie widoczna u
  dostawcy. Pola stanu oznaczone 2019 zachowano jako dane historyczne.
- GZWP: wyłącznie udokumentowane główne zbiorniki. Warstwy nieudokumentowanych
  GZWP i lokalnych zbiorników nie zostały objęte tym pakietem.

To geometrie/inwentaryzacje, nie bieżące pomiary wód, ocena prawna granic ani
wnioskowanie o ważności decyzji. Atrybuty i nazwy pól pozostają źródłowe; nie
przeliczamy ani nie przypisujemy niepotwierdzonych jednostek. Czas wytworzenia
pozostaje null. Retencja30dni dotyczy lokalnej kopii, nie wieku warstwy.

SOPO: metadane warstw14(osuwiska) i12(tereny zagrożone) odpowiadały HTTP200,
ale query `returnIdsOnly=true` zwróciło JSON error400: requested operation is not
supported. Nie utworzono profilu danych SOPO, nie obchodzono ograniczenia.
Istniejący monitoring opisów WMS pozostaje; geometrie SOPO nadal są otwartą luką.

## Technologia i bezpieczeństwo

`ingestion/pig_spatial.py` definiuje trzy ścisłe profile. `pig_paging.py` współdzieli
istniejące przypięcie spisu OBJECTID, dokładne podzbiory ID, dwie strony/wywołanie
i atomowy zapis kopii/kursora. Konfiguracje, hash i kursory MIDAS nie zmieniły się.
Nowe profile sprawdzają także nazwę warstwy, Polygon i pole OID przed odczytem.
Zmiana schematu lub spisu zatrzymuje pobieranie bez resetowania postępu.

Transport GET-only z kontrolą robots/DNS/redirect/body i wspólna quota15GB są
zachowane. Strony hydro po10 ograniczają duże geometrie; limit5MiB odpowiedzi
nie jest podnoszony. Inna, większa strona może nadal przekroczyć budżet: wtedy
kursor nie jest przesuwany, a potrzebny będzie odrębny przegląd ograniczenia.
Zbiory puste są obecnie odrzucane jako brak spisu; nie oznaczamy ich complete.

Eksport GeoJSON zawiera jedną stronę, `partial`, source_count, hash spisu, źródło,
warunki, zakres i czasy kopii. ETag odpowiada rzeczywistym bajtom. `complete`
oznacza odczyt spisu ID, nie jednoczasową wersję atrybutów/geometrii ani obecność
wszystkich kopii po ich wygaśnięciu. Dodatkowy scalony eksport ukończonego
przebiegu jest teraz dostępny staff w limicie50MiB; większe zbiory pozostają jako strony.
Po ukończeniu kolejny przebieg jest planowany według interwału1440min.

## Uruchomienie i weryfikacja

W istniejącym kontenerze web:

```text
python manage.py configure_pig_spatial
python manage.py configure_pig_spatial --apply
python manage.py audit_data --source pig-haldy-scan
python manage.py audit_data --source pig-haldy-scan --enable
python manage.py collect_data --source pig-haldy-scan --force
python manage.py collect_data --source pig-haldy-scan --force --apply
```

Analogicznie dla jcwpd/gzwp. Nowe profile domyślnie disabled/unapproved; setup
dry-run nie wykonuje HTTP ani zapisów. Zmieniony profil administratora nie jest
nadpisywany. Blokada `busy` wymaga ponowienia później, nie jej omijania. Przed
aktywacją potrzebny jest świeży audyt i zatwierdzone odświeżenie tylko workera.
Te3profile zostały już uruchomione: nie trzeba powtarzać setupu, aktywacji ani
restartu. Nie usuwaj harvestów35/36/37 lub ich zapisanych kursorów.

16celowanych/388pełnych testów PostgreSQL PASS(15.460s), Ruff225lint/formatPASS,
Django check/nomigrationsPASS i build workeraPASS, bez restartu. Testy sprawdzają
limity stron, wznowienie, zakończenie na mocku, zachowanie historycznych atrybutów,
pochodzenie/eksport, blokadę przyszłej warstwy i zmiany schematu oraz setup/admin.
Live pierwsze strony wszystkich3profilów zostały walidowane z ponownym spisem.
Aktualizacja21:52Z: import do cache i wznowienie potwierdzone dla wszystkich3.
11celowanych testów paginacji ponowniePASS0.257s, Ruff/checkPASS; kod źródłowy
pig_spatial/pig_paging ma identyczne SHA256 jak zbudowany obraz workera.
GET3kart+3eksportów/registry63/64/readyHTTP200; ID/proweniencja/copytimes/ETag
sprawdzone. MIDASscan4000obiektów każdy, collecting/noerror;3próbki po1kopii
zachowane.17DataChannels/189Sources140active49disabled, bez zmian schematu,
workbooka,15GB/30dni lub migracjiD:. Pełne ukończenie tych zbiorów NIE potwierdzone.

Źródła pierwotne: [oficjalny katalog CBDG GIS](https://baza.pgi.gov.pl/geoportal/uslugi/gis)
oraz [warunki PIG](https://www.pgi.gov.pl/rejestry-i-bazy-danych-geologicznych/zasady-korzystania-z-isp.html)
(edytowane2026-09-16). Opis zakresu jest parafrazą, nie opinią prawną ani
deklaracją jednolitej otwartej licencji. Pełne luki63/64 i epik4.5 pozostają otwarte.
