# SOPO — wstrzymanie automatycznego pobierania

## Rozpoznanie 2026-10-04

Przed rozszerzeniem rejestru62 o geometrię sprawdzono oficjalne materiały PIG-PIB.
[Regulamin SOPO](https://www.pgi.gov.pl/osuwiska/dokumenty-prawne/8549-regulamin-korzystania-z-zasobow-sopo/file.html),
wersja2025-04-29, §5h, zabrania automatycznego pobierania informacji i danych.
To parafraza punktu regulaminu, nie opinia prawna ani zgoda na odstępstwo.
[Oficjalna aplikacja](https://www.pgi.gov.pl/osuwiska/123/aplikacja.html) wskazuje
drogi dostępu i odsyła do tego regulaminu oraz warunków wykorzystania.
Nie zaakceptowano nowych warunków ani nie kontaktowano się z instytucją.

Jedna wcześniej zaplanowana próba metadanych REST usługi, wykonana w tym samym
wywołaniu narzędzi co odczyt regulaminu, zwróciła200/12035B i14 nazw warstw.
Nie pobrano geometrii, kart,
plików SHP/PDF z danymi ani spisu identyfikatorów. Po odkryciu ograniczenia
nie wykonywano kolejnych żądań SOPO. Techniczna możliwość Query nie stanowi pozwolenia.

## Co zmieniono

- Source187, `registry-036-pig-sopo-wms`: enabled=false/activation_approved=false.
  Pozostałe kanały PIG186/188/189 pozostają aktywne i zatwierdzone.
- `hold_sopo` jest domyślnie dry-run; `--apply` zmienia wyłącznie dwie flagi
  dopasowanych kanałów. Powtórzenie jest idempotentne. Profil, harmonogram,
  historyczny audyt, etag, rekordy i wersje pozostają nietknięte.
- Cofnięcie uprawnienia blokuje bieżący harmonogram bez restartu workera.
  Używa krótkiej blokady wiersza Source, zgodnej z blokadą commit importu.
  Nie jest pobieraniem: nie korzysta z globalnego advisory lock i nie zmienia
  kursorów, kopii/cache lub plików. Import rozpoczęty przed cofnięciem uprawnienia
  podlega istniejącej kontroli aktualnej polityki przed zapisem.
- `sopo_policy` blokuje znane adresy usług SOPO WMS/REST i stron zasobów SOPO,
  również po normalizacji ścieżki i kodowania. Obejmuje także profil pig_wms/sopo
  przy zmienionym URL. Oficjalne dokumenty prawne nie są zablokowane do odczytu.
  Nie blokuje MIDAS, hałd ani hydrogeologii.
- Kontrola w `fetch_entries` zatrzymuje dry-run/import przed client.get;
  `Client.get` zatrzymuje przed robots/DNS/HTTP, a `validate_url` także przed
  żądaniem przekierowania. Nie ma lokalnego terms_accepted lub override.
- `ProviderPolicyBlocked` zapisuje co najwyżej nieudaną próbę importu, bez
  nowych Items, karania dostawcy licznikiem failures lub network backoff.
- activate_registry pomija SOPO również przy wcześniejszym verified audit.
  audit_sources nie może go włączyć po odmowie preHTTP.
- source_health/readiness pokazują `terms_blocked`, nie ostatni sukces.
  Karta rejestru62 opisuje blokadę i historyczny charakter zapisanych metadanych.
  Odczyt karty nie odpytuje źródła. Stan needs_connector i oryginalny XLS zachowane.

Kod nowych strażników jest dostępny w procesach CLI i web oraz zbudowanych obrazach.
Worker nie został odświeżony; aktualnie respektuje wyłączenie przez flagi i istniejący
Source-policy fence. Nie twierdzimy, że załadował nowy moduł — załaduje go przy
kolejnym uzgodnionym łagodnym odświeżeniu. Nie włączać SOPO ponownie.
Nie dodawać profilu geometrii ani kolejnych prób GET bez wyjaśnienia dostępu z PIG-PIB.
Ewentualne odstępstwo wymaga dowodu uprawnienia od dostawcy i osobnego projektu
ograniczonego interfejsu, nie samego polecenia lokalnego operatora.

## Dowody lokalne

12 nowych testów;54focused PostgreSQL PASS1.298s. Testy braku robots/DNS/GET,
normalizacji URL, innego profilu, lokalnej zgody/force/dry-run, historycznego audytu,
idempotentnego hold bez utraty danych, pozostałych kanałów i czytelnego stanu UI.
Ruff check/format469 plików, Django check/no migrations; buildwebworker.
Końcowe1024 testy PostgreSQL PASS64.011s; build i pozostałe kontrole PASS.
Pełne dowody w VERIFICATION.md.

Live Source187: obie flagi false,15 historycznych Items zachowanych;
audyt verified z2026-09-30T19:43:14.556733Z zachowany jako historyczny.
Nowy fetch_entries odmawia przed GET (mock callcount0). Rejestr62 staff200
i poprawny komunikat, ready200; inne trzy PIG flagsTrue/True.
hold dry changedTrue/appliedFalse; apply changedTrue; repeat changedFalse.
Worker start nadal2026-10-04T18:07:58.051880826Z/RestartCount0/OOMfalse.
Początkowe błędne nazwy pola/modelu routingu w diagnostyce skorygowano;
lint poprawiony formatowaniem, bez niezakończonej zmiany danych.

## Stan epika

Nie jest to nowy dostarczony zbiór danych ani zaakceptowany wyjątek.
SOPO62 jest otwarte i zablokowane warunkami dostawcy; wcześniejsze stwierdzenia
o aktywnym monitoringu metadanych SOPO są historyczne i zastąpione tym stanem.
34 luki konfiguracji całego rejestru nie zmniejszyły się przez samo wyłączenie.
Następny krok: inny publiczny zakres4.5; SOPO dopiero po wyjaśnieniu dostępu.
Nie zmieniono retencji30dni/cache15GB, migracji D:, GIS/MIDAS, innych źródeł,
skoroszytu, backupów, GitHub/Drive ani publicznego wdrożenia.
