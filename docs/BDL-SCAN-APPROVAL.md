# BDL — zatwierdzenie ręcznego dostępu i ukończony przebieg RDLP

Aktualizacja2026-10-04T11:34+02:00: [WORKER-RESUME](WORKER-RESUME.md) opisuje
wznowienie zwykłego workera. Supersedes poniższe „automatyczny monitoring
zatrzymany”; RDLP25manual/disabled17kopii pozostaje bez zmian. AI nadal wyłączone.

Stan: 2026-10-04T11:23+02:00. Zastępuje wcześniejsze informacje o
niezatwierdzonym kanale25/0kopii wyłącznie dla ręcznego profilu RDLP.
Kanał25 access_approved=true, enabled=false. Przebieg113 complete17/17,
17 kopii26593–26609, 78414771B. Nie włączono harmonogramu ani innych profili BDL.

## Chronione zatwierdzenie

```powershell
# Świeża próba bez zmiany zgód i bez importu:
docker compose exec -T web python manage.py approve_bdl_scan
# Powtórna świeża próba i zatwierdzenie tylko ręcznego dostępu:
docker compose exec -T web python manage.py approve_bdl_scan --apply
```

Opcjonalne --wait-seconds0..30 dotyczy wspólnej blokady konfiguracji. HTTP jest
poza transakcją SQL. Walidowany profil musi być dokładnym wyłączonym RDLP;
niezatwierdzony profil z historią wymaga osobnego przeglądu, bez resetu.
Pierwsza geometria musi być niepusta, oryginalna, w budżecie5MiB, ze świeżym
manifestem/licencją i zgodnym hashem zakresu przed/po GET. Zatwierdzenie sprawdza
stan profilu, zgody, diagnostyki, harmonogramu i historii ponownie pod blokadami
konfiguracji/cache/wiersza. Receipt ważny10min; zmiany lub niewystarczająca
pojemność powodują odmowę bez nadpisania konfiguracji administratora.

Budżet rozpatruje scenariusz liczba_id*50MiB + rezerwa1GiB, przed i po blokadzie.
To kontrola pojemności, nie rezerwacja miejsca na przyszłość. Każdy krok dalej
sprawdza bieżący cache. Zatwierdzenie zapisuje tylko access_approved i diagnostykę;
enabled/next_fetch_at/kopie/kursory pozostają bez zmian. Powtórzenie już
zatwierdzonego profilu nie wykonuje GET ani nie zmienia harmonogramu/historii.

Podstawa zakresu: deklaracja CC BY4.0 oficjalnego API dla oryginalnych odpowiedzi
RDLP, z wymaganym źródłem/licencją/czasem pobrania i bez przypisywania poparcia
dostawcy. Nie jest to ogólne rozstrzygnięcie warunków wszystkich zasobów BDL.
Nie wysyłano formularzy, nie kontaktowano operatora ani nie akceptowano umów.

## Wykonany rzeczywisty przebieg

Dry zatwierdzenia:17id/budżet/licencja/raw firstpagePASS; applyPASS.
Pierwszy krok5MiB zapisał id1 (2331884B). Następny id2 zakończył się rzeczywistym
blocked na limicie rozmiaru/czasu: records=1, next_url nadal id2. UI200 i opis
blokady potwierdzone; ponowienie bez on-demand przy podmienionym GET na wyjątek
potwierdziło brak pobierania. On-demand dry odczytał id2 (8294135B), a apply
zapisał go i usunął blokadę w tym samym przebiegu113.

Pozostałe id pobrano ręcznymi pojedynczymi krokami --fetch --on-demand --apply,
każdy do50MiB, ze świeżym manifestem i sprawdzeniem pojemności. Nie uruchomiono
automatycznego fallbacku/kolektora. Wynik:17/17id, pages=records=17,
next_url puste, error puste, status complete. Wszystkie kopie zachowują
oryginalne bajty, źródło/CC BY/rok2026/czas pobrania/retencję30dni.

Niezależna kontrola po zakończeniu: komplet id i hash zakresu zgodne ze świeżym
manifestem, SHA256/oryginalny eksport PASS dla17kopii, pobranie200 i nagłówki
licencji dla17kopii, suma rozmiarów zgodna z kursorem. Kolejne advance i
approve --apply przy zabronionym GET potwierdziły brak ponownego pobierania.
Rzeczywista strona [kanału25](http://localhost:8001/data/25/) odpowiada200.

Complete oznacza odczyt przypiętego spisu, nie jednoczasową wersję geometrii
dostawcy. Kopie nadal partial/full_dataset_coverage=false. Nie uruchamiają
nowego skanu automatycznie i nie obejmują nadleśnictw/leśnictw/wydzieleń.
Registry39/fullBDL/całyepik4.5 pozostają otwarte.

## Testy, zasoby i stan usług

6 nowych testów approval: dry/apply/repeat/noGET/nohistory/noenable, drift,
niepoprawny receipt/licencja, niezatwierdzona historia, preGETcache oraz pełny
scenariusz pojemności.20focusedPASS0.406s;894fullPGPASS53.707s.
Ruff432/check/format/nomigrations/buildwebworkerPASS; istniejące ostrzeżenie
pip-root podczas build, bez instalacji na hoście. Poprawiono wyłącznie
lintowaną długość etykiety wyniku po pierwszej kontroli.

Wykorzystanie cache po imporcie12775038213B/15000000000B, retencja30dni bez zmian.
Zastano wszystkie kontenery zatrzymane; uruchomiono tylko istniejące db i web
bez wymiany obrazów/kontenerów. dbhealthy/webrunning. Worker pozostał zatrzymany:
exit137, OOMKilled=false, finished2026-10-04T08:01:14Z; przyczyna UNCONFIRMED.
AI-worker/Ollama też nie uruchamiano. Automatyczny monitoring pozostaje zatrzymany.
Nie resetowano danych/kursorów/XLS i nie wykonano push/publikacji do GitHub.

Następne: bezpiecznie ustalić/wznowić zwykły monitoring oraz wybrać następny
brakujący zakres epika4.5. Nie powtarzać zatwierdzania/importu ukończonego RDLP.
