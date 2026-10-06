# 4.5.2 — RSS WIOŚ Kielce i Poznań

Uzupełnienie2026-10-01T14:46+02:00: parser ma6profili po dodaniu
[WFOŚKatowice109/Olsztyn111](WFOS-KATOWICE-OLSZTYN.md). Poprzednie94/96
zachowały konfigurację/audyty/aktywację; poniższe wyniki to snapshot13:20.

Stan2026-10-01T13:20+02:00: dwa kolektory aktywne120min, import/powtórzenie/
odświeżenie workera zakończone w ramach powierzonych rutynowych decyzji.
195Sources151active44disabled; pełne34luki rejestru i odbiór4.5.2 nadal otwarte.

## Oficjalne źródła i zakres

| ID / kolektor | Zweryfikowany kanał | Próba i świeży audyt | Najnowsza publikacja RSS |
| --- | --- | --- | --- |
| 94 / registry-094 | [WIOŚ Kielce RSS](https://kielce.wios.gov.pl/feed/) | HTTP200/10rekordów/0odrzuceń | 2026-08-05T11:54:57Z |
| 96 / registry-096 | [WIOŚ Poznań RSS](https://poznan.wios.gov.pl/feed/) | HTTP200/10rekordów/0odrzuceń | 2026-10-01T07:44:10Z |

Oficjalne domy [Kielce](https://kielce.wios.gov.pl/) i
[Poznań](https://poznan.wios.gov.pl/) reklamują dokładne RSS link rel=alternate.
Pierwotne workbookURL z www miały historyczny błąd cross-host redirect.
Narzędzie web nie otworzyło starych wwwURL; nie udajemy świeżego dowodu celu301.
Zweryfikowano bezpośrednie canonicalhome/RSS HTTP200/tożsamość kanału przez
Client z HTTPS/publicznym DNS/robots i budżetami, najpierw izolowany probe.
Do registry_hosts dodano wyłącznie dwa oficjalne hosty bez www.
Nie wprowadzono automatycznego cross-host redirect ani obejścia robots.

Metadane bieżących10wpisów każdego RSS, nie pełny katalog kontroli/awarii,
archiwum ani pomiary. Kielce najnowszy wpis5sierpnia nie dostaje daty importu;
nie ustalono przyczyny rzadszych publikacji. Nie wnioskujemy braku nowych zdarzeń.
Poznań ma Aktualności,Komunikaty i Bez kategorii; zapisujemy source_categories
bez przypisywania rodzaju zdarzenia na podstawie samego członkostwa RSS.
W aktualnym oknie jeden wpis Bez kategorii,pozostałeAktualności.
Komunikaty są dopuszczonym typem źródłowym, nie twierdzeniem o obecności
całego archiwum komunikatów w tych10wpisach.

## Zmiana techniczna i ochrona istniejących kanałów

regional_rss.py rozszerzony z82/108 do82/94/96/108.
94standardHTTPSpermalink i kategoriaAktualności.
96dokładny HTTPShost/datowanypathYYYY/MM/DD/slug/ oraz dodatniWordPressGUID;
unikatowy tytuł/root/atom:self i budżet1–50 wspólny, brak duplikatów.
96kategorie muszą być niepustym podzbiorem przypiętych3wartości.
Nie osłabiono ścisłego schematu82/108. source_categories tylko dla96,
więc istniejącym kanałom nie zmienia się hash przez nowe pole metadanych.

Zapis tylko tytułu, źródłowych dat i linku; puste summary, bez authors/
kontaktów/załączników/childfetch. Brak updated pozostaje NULL, nie aliasem
published zfeedparser. news_notice/current_rss_window, nie prawne fakty,
aktywny nabór czy dowód pełnego odbioru źródła.

configure_regional_rss nadal drydefault/atomicwholepackage/noHTTP.
Pola legacy_home94/96 wiążą zamianę z dokładnymi starymi wwwURL.
Wyłącznie nietknięte disabled/unapprovedweb/home/config{}120min/0Items,
bez sukcesów/błędów, mogą zostać zamienione. Administrator/history/linkage
chronione; SourcePK zachowane94=78/96=80, stan rejestru bez zmian.
Stary audyt i ETag unieważnione tylko przy zamianie endpointu.
Powtórzenie chroni nowe audyty i approval,82/108 pozostają unchanged.

## Wykonane kontrole i uruchomienie

Dryconfigure zaakceptował dokładnie94/96 do zamiany,82/108 unchanged.
Worker SIGTERMnaturalexit0; restart=no tymczasowo zapobiegał wznawianiu starego
obrazu. Configureapply, świeże audyty10+10/0, repeatunchanged,
dryactivation2/dryimports10+10/0, scopedactivate94/96 wykonane.
Import20created/0updated, powtórzenie obu304/0createdupdated.
20Items mają published_at, source_updated_at=NULL/summaryempty/authors[];
10ItemsPoznań mają source_categories. Brak resetu danych/kopii/kursorów.

Worker running11:19:47Z, restartunless-stopped przywrócony.
Manifestb59bd7ca8e07fb84fb68692e8d0671ed842518567a3efc09fcd6bda9c4d4e6d9,
confige28e0f5f9ce19856dd9bfcac9e32184aba76031cb721de5251a999a7973901f4.
Logi potwierdzają wznowienie istniejących skanów Copernicus.
5usługrunning/DBhealthy; /health/ready/ i /registry/94/ oraz96HTTP200.
Następne odświeżenie13:19Z (15:19Warszawa); cache15GB/30dni bez zmian.

9celowanych testówRSS PASS0.227s (2nowe regresje96kategori/path),
463pełne PostgreSQLtests PASS24.719s.
Rufflint/format265plików/Djangocheck/nomigrations/buildPASS.
Nie ponawiać zakończonej aktywacji/restartu.
Następne4.5.2: Bydgoszcz83 lub pozostałe wyłączone WFOŚiGW/
aktywny EUR-Lex168zproblemem, z potwierdzeniem równoważnego zakresu i dostępu.
