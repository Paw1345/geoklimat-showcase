# 4.5.2 — WIOŚ Wrocław i WFOŚiGW Gdańsk

2026-10-01T13:20+02:00: późniejszy [pakiet Kielce/Poznań](WIOS-CANONICAL-RSS.md)
rozszerzył moduł do4profili82/94/96/108, bez ponownej aktywacji82/108.
Aktualny globalny stan195Sources151active44disabled zastępuje licznik poniżej,
który jest historycznym wynikiem ukończenia pakietu Wrocław/Gdańsk.

Stan weryfikacji: 2026-10-01T12:55+02:00.
Pakiet skonfigurowany, aktywny co120min i wdrożony po osobnej jawnej zgodzie.
Pierwszy dry-run był busy/bez zapisów; po zgodzie worker zakończył się naturalnie
kodem0 po SIGTERM. Konfiguracja dry/apply/repeat, świeże audyty10+10/0,
dryactivation2 i dryimports10+10/0 zaliczone. Import20created/0updated,
powtórzenie obu kanałów304/0zmian. Worker wznowiony z nowym obrazem.
Zgoda dotycząca Opolskiego nie była przenoszona na ten pakiet.

## Dowody i dostarczany zakres

Oficjalne strony główne reklamują link rel=alternate do kanałów wpisów,
oddzielonych od comments/feed. Bounded GET przez zwykły Client, robots,
publiczny DNS, HTTPS i limity; żadnych obejść polityk.

| Rejestr / kolektor | Reklamowany RSS | Próba parsera | Najnowsza publikacja |
| --- | --- | --- | --- |
| 82 / registry-082 | [WIOŚ Wrocław](https://www.wroclaw.wios.gov.pl/feed/) | HTTP200/10rekordów/0odrzuceń | 2026-09-29T13:21:03Z |
| 108 / registry-108 | [WFOŚiGW Gdańsk](https://wfos.gdansk.pl/feed/) | HTTP200/10rekordów/0odrzuceń | 2026-09-03T12:52:30Z |

Wrocław: osiem tytułów z widocznej listy aktualności odpowiada pierwszym ośmiu
wpisom RSS. Gdańsk: sześć widocznych tytułów listy aktualności odpowiada pierwszym
sześciu wpisom RSS. To porównanie bieżącej listy, nie dowód pełnego archiwum.
Data3września w Gdańsku nie staje się dzisiejszą datą publikacji.
Nie ustalono przyczyny rzadszych publikacji; nie zakładamy awarii ani braku naborów.

Dotychczasowe wyłączone kolektory: adapter web, homeURL, config{},0Items,
enabled=False/activation_approved=False. Wrocław miał historyczny audyt
verified/count8/rejected1: etykieta verified nie dowodzi kompletności i nie spełnia
bramki aktywacji wymagającej0odrzuceń. Przyczyna pierwotnego wyłączenia pozostaje
niepotwierdzona; obecna diagnoza nie rekonstruuje intencji administratora.
Łódź86: homeHTTP200/lista aktualności reklamowana, RSS nie znaleziono;
osobny adapter HTML nadal do zrobienia, nie zastępować stroną główną.

## Parser i bezpieczeństwo zakresu

regional_rss.py: dwa stałe profile, ścisłe adapter/url/config/HTTP200/finalURL,
tytuł/rootlink/atom:self. Limit1–50rekordów; dokładny host, HTTPS,
jednosegmentowy permalink, WordPress GUID?p=ID, brak query/fragment,
brak duplikatów URL/GUID i wymagana kategoria Aktualności.
Naruszenie dowolnego rekordu odrzuca całą partię.

Zapis tylko tytułu, dat źródłowych i linku. Brak summary/authors/kontaktów/
załączników/childfetch. Brak jawnego updated pozostaje pusty, bez aliasu
feedparser z published. Daty wymagają strefy; data publikacji nie jest
datą decyzji, wejścia prawa lub terminem naboru.
record_kind=news_notice/listing_scope=current_rss_window;
funding_call_verified=False/full_document_downloaded=False.
Publiczne tytuły mogą zawierać informacje osobowe; nie obiecujemy anonimizacji.
Publiczny RSS nie jest ogólną licencją na replikację pełnych dokumentów.

## Kontrolowana konfiguracja

```text
python manage.py configure_regional_rss
python manage.py configure_regional_rss --apply
python manage.py audit_sources --source registry-082
python manage.py audit_sources --source registry-108
```

Domyślny dry-run, bez HTTP i zapisów. Apply pod zwykłym batch_lock/transaction,
waliduje cały pakiet przed pierwszym zapisem. Zamienia wyłącznie dokładne
nietknięte kandydaty: web/home/config{}/oryginalny scope/120min, wyłączone,
niezatwierdzone, bez Items/sukcesów/błędów, z wyłącznym poprawnym powiązaniem
rejestru. Administratorskie zmiany blokują cały pakiet.

Zachowuje SourcePK, nazwę, organizację, priorytet, topics i reuse_notes.
Nie zmienia RegistryEntry.integration_state ani workbooka.
Zamiana unieważnia wyłącznie audyt starego endpointu i czyści jego ETag/
Last-Modified; kanał nadal disabled/unapproved. Nowy audyt konieczny.
Powtórzenie już skonfigurowanego profilu zachowuje approval i świeży audyt.
Nie stosować apply podczas działania starego parsera w workerze.

Wykonano po osobnej zgodzie: łagodny SIGTERMwyłącznie workera/naturalny exit,
configure dry/apply, świeże audyty, scoped dryactivation i dryimports,
activate_registry tylko82/108, import/powtórzenie i start nowego workera.
Bez force/resetu kopii/kursorów; dotychczasowa polityka restartu przywrócona.
Nie zwiększać zakresu na wszystkie nabory/dokumenty/archiwum.

## Weryfikacja lokalna

7nowych testówPASS0.192s: tożsamość/kategoria/host/GUID/datowanie/budżet,
minimalne metadane, dry-run/idempotencja, ochrona historii/linkage i
brak częściowego zapisu przy zmianie drugiego kolektora.
453pełne testy PostgreSQL PASS19.728s.
Rufflint i format260plików PASS; Django check PASS; brak nowych migracji.
Worker build PASS, config19ce415ecbaa49130f4c68c425e560aa1f7317117f8f6d1b49eefcd2a4bee069,
manifest2c9964243af86e8ebe772bdcd6b17a6f5308c57681a0fd1c502c4bf9bf3ddb6d.
Obraz wdrożony; worker running10:55:10Z, restart=unless-stopped przywrócony.
Na czas naturalnego końca staremu workerowi ustawiono restart=no; bez SIGKILL.
SourcePK zachowane82=67/108=86. Zapisane20Items mają published_at, wszystkie
source_updated_at=NULL/summaryempty/authors[]/funding_call_verified=False.
5usługrunning/DBhealthy; /health/ready/ i /registry/82/ oraz108 HTTP200.
Logi potwierdzają wznowienie istniejących skanów Copernicus bez resetu.
Następne pobranie12:55Z (14:55Warszawa); cache15GB/retencja30dni bez zmian.
194Sources147active47disabled;34pełne luki nadal otwarte.
Nie ponawiać zakończonej aktywacji/restartu. Następny regionalny kandydat:
WIOŚŁódź86, osobny adapter listy HTML po weryfikacji struktury.
