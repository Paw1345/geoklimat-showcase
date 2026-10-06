# BDL — aktywacja trzech oryginalnych próbek

2026-10-03T18:19Z. Profile22–24 aktywne/zatwierdzone, po jednej kopii,
1440min/offset0/limit1. Supersedes disabled/pending stan18:04Z.

## Wąska podstawa wykorzystania

[API BDL](https://ogcapi.bdl.lasy.gov.pl/?f=html) oraz świeży OpenAPI
deklarują CC BY4.0 i ten sam URL termsOfService. [Tekst licencji](https://creativecommons.org/licenses/by/4.0/legalcode.en)
przewiduje kopiowanie i dalsze udostępnianie materiału z wymaganym wskazaniem
źródła/licencji i zmian. Dla trzech niezmienionych stron API przyjmujemy tę
deklarację dostawcy jako podstawę wąskiego użycia; to decyzja implementacyjna,
nie opinia prawna ani potwierdzenie praw do wszystkich zasobów portalu.
Zasady pozostałych źródeł BDL/formularzy/bulk i pełne wydzielenia pozostają osobno.
Nie składano wniosku, nie wysyłano wiadomości operatorowi, nie akceptowano
formularzy czy kontraktów w imieniu użytkownika.

Publiczny widok kopii wskazuje BDL/link źródłowy/link CC BY4.0, czas pobrania,
rok a_year, brak zmian i brak poparcia/urzędowego potwierdzenia przez dostawcę.
Pobranie zachowuje bajty, ma .geojson, Link rel=license/canonical, atrybucję
i czas pozyskania w nagłówkach. Hash/rozmiar/licencja/source URL sprawdzane przed
eksportem; uszkodzona kopia nie jest udostępniana. Brak gwarancji aktualności.

## Aktywacja

```powershell
docker compose exec -T web python manage.py activate_bdl_samples
docker compose exec -T web python manage.py activate_bdl_samples --apply
```

Drydefault: świeże próby, bez zapisu. Apply: ponowne GET poza transakcją,
dokładny profil/approval/schedule baseline, niepuste próbki i receipt<=10min;
sharedbatchlock/cache726043/rowlocks, aktualny budżet3*5MiB*30+1GiB,
all-or-nothing zgody tylko22–24. Nie zapisuje kopii, nie dotyka innych kanałów.
Aktywne powtórzenie nie pobiera próbek i nie resetuje harmonogramu.
Ogólne audit_data --enable dla BDLrawsample jest odrzucane — ta ścieżka ma
osobne zabezpieczenia. Plan budżetu nie gwarantuje wzrostu innych kolektorów;
store ma niezależną kontrolę realnego rozmiaru/rezerwy przy każdej kopii.

## Dowody live

Sucha próba przy pracującym workerze busy/nochanges. Po testach workerONLYSIGTERM,
naturalnyexit0 18:15:54.442Z. Świeży dry18:16:48–54Z oraz apply18:16:59–18:17:04Z:
3próbki200/po1obiekcie, oryginalne bajty2332088/916770/128490.
Apply aktywował tylko22–24. Pierwsze collect_data --apply bez force:
kopie23071/23072/23073, łącznie3377348B, po1obiekcie.
Sameworkerbc6abebb…/bindrepo start18:17:19.047Z/unless-stopped bez zmian;
log18:18:13Z normalnecykleGUGiK/STAC wznowione. Web/DB/AI bez restartu.

Kontrola liveDB/RequestFactory: wszystkie3active/1copy/SHA-PASS,
download200/originalbytes i publicdetail200/licencevisible.
run_sample repeat z Client.get zabronionym przez mock: wszystkie daily_limit,
brak HTTP i kopii dodatkowych. Próba liveCLIactivationrepeat busy/nochanges;
idempotencja aktywacji potwierdzona unit, nie successfulrepeatlive.
Nie potwierdzono jeszcze kolejnego automatycznego pobrania po24h.
Diagnostykę RequestFactory poprawiono po błędnym imporcie detail i brakującym
request.user; końcowa próba z channel_detail/AnonymousUser PASS, bez DBwrites.

4nowetesty,21focusedPASS0.319s; pełne867PGPASS51.408s/Ruff421/check/
nomigrations/buildwebworkerPASS.5runningDBhealthy. Nie zastąpiono kontenerów
nowymi obrazami; nowy proces workera czyta bindrepo.
Bez cache15GB/retencji30dni/XLS/schema/historyreset/Gitpush zmian.

## Dalej

Nie powtarzać zakończonej aktywacji/restartu. Kolejny zakres: oddzielny ograniczony
przebieg RDLP ze spójnością i kursem stron albo niezależna luka4.5.
Próbki offset0 nie skanują całych17/429/5259obiektów ani regionalnych wydzieleń;
pełny rejestr39/4.5 i dokładna maska/aktualność/akceptacja biznesowa otwarte.
