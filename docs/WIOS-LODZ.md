# 4.5.2 — dwie listy WIOŚ Łódź

Stan2026-10-01T13:09+02:00: oba kanały aktywne120min, import i odświeżenie wykonane.
Użytkownik powierzył asystentowi decyzje w takich rutynowych sprawach; wybrano
uruchomienie obu zweryfikowanych list i łagodny SIGTERM wyłącznie workera.
Nie stosowano zgody z zakończonego pakietu Wrocław/Gdańsk do tego pakietu.
Początkowy dry-run busy/no writes zastąpiony przez dry/apply/repeatkonfiguracji,
świeże audyty10+10/0, dryactivation2 i dryimports10+10/0.
Import20created/0updated; powtórzenie20unchanged/0created/0updated.

## Zakres i dowody

Oficjalna strona główna i sekcja Aktualności i komunikaty reklamują dwie listy:

| Kanał | Oficjalna lista | Wynik parsera GET | Widoczne daty |
| --- | --- | --- | --- |
| registry-086 | [Aktualności](https://www.wios.lodz.pl/Aktualnosci,46) | HTTP200/10rekordów/0odrzuceń | 2026-10-01 do2026-07-24 |
| registry-086-notices | [Komunikaty](https://www.wios.lodz.pl/Komunikaty,47) | HTTP200/10rekordów/0odrzuceń | 2026-09-09 do2024-02-05 |

Tylko pierwsza strona każdej listy. Komunikaty zawierają starsze publikacje;
nie oznaczamy ich jako nowych datą importu. Brak pełnego archiwum i gwarancji
pełnego pokrycia wszystkich kontroli/awarii. To news_notice, nie fakty prawne,
terminy obowiązków ani pomiary. Link do źródła służy dalszej ręcznej weryfikacji.

Nie pobieramy szczegółów, zdjęć, załączników, kontaktów ani pozostałych stron.
Pierwotny kolektor86web/home/config{} wyłączony, niezatwierdzony,0Items,
bez sukcesów/błędów,120min; źródłowa pozycja rejestru pozostaje configured.

## Ścisły parser

wios_lodz.py: dwa stałe endpointy, exact adapter/url/config/HTTP200/finalURL,
unikatowe title/#text_title h2/#text_content i zgodność z konkretną kategorią.
1–50bezpośrednich kart .news_spacer; w każdej jeden prawy kontener, h3,
data .col_gray i przycisk input.more_m.

Przycisk ma dokładne location.href='basename,category,recordID';.
Parser dopasowuje dane wyrażeniem regularnym: ASCIIbasename bez slash,
fixed46/47 i dodatnie ID. Nigdy nie wykonuje JavaScriptu i nie nawiguje.
Inne instrukcje, zewnętrzne URL, query/fragment i inna kategoria odrzucone.
Tożsamość wpisu wios-lodz:category:recordID nie zależy od tytułu/slugURL.
Duplikaty ID/URL, brak pola i nieprawidłowy porządek dat odrzucają całą partię.

Źródłowe polskie daty są walidowane jako istniejący dzień kalendarza.
Zachowujemy source_publication_date_text oraz ISOdzień do normalizacji.
date_precision=day: wewnętrzny timestamp normalizacji nie dowodzi godziny
publikacji. source_updated_at pozostaje puste; brak domyślnej daty „dzisiaj”.
Puste summary, bez autorów/kontaktów/stopki. Publiczny tytuł nie jest obietnicą
anonimizacji; publiczna lista nie jest ogólną licencją na pełną replikację.

## Konfiguracja i uruchomienie

```text
python manage.py configure_wios_lodz
python manage.py configure_wios_lodz --apply
python manage.py audit_sources --source registry-086
python manage.py audit_sources --source registry-086-notices
```

Dry-run domyślny, bez HTTP/zapisów. Apply pod zwykłą blokadą batch_lock i
transaction; cały pakiet sprawdzany przed pierwszym zapisem.
Zamiana registry-086 zachowuje SourcePK, nazwę, priorytet/topics/reuse_notes;
tylko dokładny nietknięty disabled/unapproved web/home/config{} z0Items,
bez historii sukcesów/błędów i z120min może zostać zmieniony.
Unieważnia audyt starego endpointu i ETag/Last-Modified, nie historyczne Items.
Nowy registry-086-notices powstaje disabled/unapproved120min.
Administratorconfig/history/linkage chronione; nie zmieniamy stanu rejestru.
Powtórzenie zachowuje audyty i approval; bez autoaktywacji.

Wykonano po powierzeniu decyzji: SIGTERMtylko workera/naturalny exit0, dry/applykonfiguracja,
świeże audyty/dryactivation/dryimports, scopedactivation/import/powtórzenie
i wznowienie nowego obrazu. Nie zmieniać innych usług, danych, kursorów,
15GBcache/30dni ani migracjiD:. Nie stosować force/SIGKILL.
Nie uruchamiać nowych profili w starym procesie workera.

## Weryfikacja

8celowanych testówPASS0.211s: oba profile, rawdate/precyzja dnia, realdates,
tożsamość/kategoria/budżet/duplikaty/porządek/konfiguracja/redirect,
onclick niezdatny do wykonania, minimalne metadane,
dry/apply/repeat/adminhistory/linkage/atomicpackage.
461pełne testy PostgreSQL PASS18.889s.
Rufflint/format264plików PASS; Django check PASS; brak nowych migracji.
Worker build PASS:
config097beb4d9611994a3ff80d8ba9f14c4c2fbafb8bbd4be31a261713be4b88b946,
manifest040727c2a3a3a2417c05742b0a4aed138f8123444b58e122c28b7f7315a72e7a.
Obraz wdrożony; worker running11:08:29Z/restartunless-stopped przywrócony.
Stary kontener miał tymczasowo restart=no dla naturalnego zakończenia poSIGTERM.
Źródło86 zachowałoPK70; nowe komunikatyPK195.20Items datowane zdate_precision=day,
source_date_text zachowane, source_updated_at=NULL/summaryempty/authors[]all20.
5usługrunning/DBhealthy; /health/ready/ i /registry/86/ HTTP200.
Logi potwierdzają wznowienie istniejących skanów Copernicus bez resetu.
195Sources149active46disabled; cache15GB/30dni bez zmian.
Następne pobranie13:08Z (15:08Warszawa). Pełne4.5.2otwarte; nie ponawiać
zakończonej aktywacji/restartu. Następnie pozostałe regionalne redirect/parser
reviews lub aktywny EUR-Lex z problemem, z zachowaniem polityk dostępu.
