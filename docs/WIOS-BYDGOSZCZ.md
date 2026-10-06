# 4.5.2 — aktualności WIOŚ Bydgoszcz

Stan2026-10-01T14:05+02:00: registry-083 naprawiony i aktywny co120min.
Wykonano konfigurację, świeży audyt3/0, dryimport, import3created i
powtórzenie3unchanged/0createdupdated. Worker odświeżony w ramach powierzonych
rutynowych decyzji; bez resetów danych i kursorów.
195Sources152active43disabled;34pełne luki rejestru i odbiór4.5.2otwarte.

## Oficjalne źródło i ograniczenia

[WIOŚ Bydgoszcz](https://wios.bydgoszcz.pl/) reklamuje
[listę Aktualności](https://wios.bydgoszcz.pl/aktualnosci/).
Odczytowe GET przez Client/HTTPS/publiczny DNS/robots potwierdziły HTTP200.
RSS nie jest reklamowany w sprawdzonym HTML; nie twierdzimy, że nie istnieje.
Jawny link api.w.org do wp-json został zauważony, ale nie pobierano API/XMLRPC.
Do hostów dodano tylko wios.bydgoszcz.pl, nie BIP/środowiskowego subportalu.
Nie otwierano załączników, innych stron archiwum ani szczegółów.

Pierwsza strona zawiera3wpisy, źródłowe daty10.08.2026/26.06.2026/03.06.2026.
Pierwszy tytuł mówi o zamknięciu urzędu14sierpnia; publikacja jest10sierpnia.
Nie wnioskujemy daty publikacji z tytułu ani daty importu.
Najnowsza publikacja10sierpnia nie dowodzi ani awarii strony, ani braku nowych
zdarzeń. Zakres to pierwsza strona, nie121stron archiwum czy wszystkie kontrole/
awarie/pomiary. record_kind=news_notice, bez automatycznych faktów prawnych.

## Parser

wios_bydgoszcz.py: dokładny adapterweb/URL/config/HTTP200/finalURL.
DekodowanieUTF-8strict jak zadeklarowany Content-Type źródła, potemHTMLparser;
zapobiega zaobserwowanemu mojibake autodetekcji BeautifulSoup na home.
Wymagane pojedyncze title/main/h1 i dokładna tożsamość instytucji/sekcji.
1–50kart .post-list-item w main; status-publish/type-post i zgodnepost-ID.
Jedno h2/link/datum .post-date/linkCzytajwięcej, te dwa linki muszą być równe.
TylkoHTTPSexacthost/aktualnosci/slug/, bezquery/fragment,duplikatówID/URL.
Źródłowe DD.MM.YYYY musi być prawdziwym dniem; porządek nierosnący.
Zmiana schematu lub błędny rekord zatrzymuje całą partię, nie częściowy import.

Tożsamość wios-bydgoszcz:post:ID niezależna od zmiany slug/tytułu.
Zapis tytułu, datyISOdzień, source_publication_date_text i linku.
date_precision=day; wewnętrzny timestamp normalizacji nie jest źródłową godziną.
Puste summary/updated, bez authors/kontaktów/stopki/zdjęć i treści załączników.
Nie wykonuje JavaScriptu. Publiczne tytuły mogą zawierać dane osobowe;
nie obiecujemy anonimizacji ani ogólnej licencji na replikację dokumentów.

## Konfiguracja

```text
python manage.py configure_wios_bydgoszcz
python manage.py configure_wios_bydgoszcz --apply
python manage.py audit_sources --source registry-083
```

Domyślny dryrun/noHTTP/noDBwrites. Apply pod zwykłym batch_lock i transaction.
Zamienia wyłącznie nietknięty disabled/unapprovedweb/legacywwwroot/config{}120min,
0Items/bezsukcesów i błędów/zwyłączną poprawną relacją registry83.
SourcePK68,nazwa,organizacja,topics/priorytet/reuse_notes zachowane.
Administratorconfig/history/linkage chronione; stary audyt/ETag unieważnione.
RegistryEntry.integration_state unchanged; workbook niezmieniony.
Powtórzenie zachowuje nowy audyt/approval. Bez automatycznej akceptacji zakresu.

## Uruchomienie i weryfikacja

Pierwszy configuredrybusy/no writes. Po zaliczonych testach workerSIGTERM
zakończył naturalnie kodem0; tymczasowy restart=no zapobiegał autorestartowi
starego obrazu. Dryconfigure/apply/auditverified3/0/repeat/dryactivation1/
dryimport3/0/scopedactivate/import3created/repeat3unchanged wykonane.

3Items alldated/date_precision=day/rawsourceDate/source_updated_at=NULL/
summaryempty/authors[]. Worker running12:04:40Z/restartunless-stopped restored,
manifest674f70b08076aec633ea2051c2be0056daae1ece7c06383c19ab557ce92f863e,
configf2622d3a84527d3bdae190f0eed8edf141a2ee867e016559ff26c6776cb0f008.
Logi potwierdzają wznowienie istniejącego skanu CopernicusEU.
5usługrunning/DBhealthy;/health/ready/ i /registry/83/ HTTP200.
Następne pobranie14:04Z (16:04Warszawa);15GBcache/30dni i migracjaD:bez zmian.

8focusedtestsPASS0.146s,471pełnePGtestsPASS19.278s,
Rufflint/format269/check/nomigrations/buildPASS.
PoczątkoweE501 w linii testu poprawione i pełnylint ponowionyPASS.
Testy obejmują UTF-8, datę publikacji inną od daty tytułu, precyzję dnia,
tożsamość/linki/duplikaty/porządek/budżet/schema i ochronę admin/history/linkage.
Nie ponawiać zakończonej aktywacji/restartu.
Kolejny pakiet: wyłączone WFOŚiGW101/102/109/110/111 lub EUR-Lex168zproblemem;
najpierw dopuszczony aktualny interfejs, bez obejścia zasad dostępu.
