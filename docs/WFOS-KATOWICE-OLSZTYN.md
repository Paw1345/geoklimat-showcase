# WFOŚiGW Katowice i Olsztyn — oficjalne RSS

Stan potwierdzony 2026-10-01T14:46+02:00. Dwa kanały pakietu 4.5.2
uruchomione co 120 minut; trzeci, świętokrzyski, pozostaje wyłączony.
Nie jest to pełny odbiór epika 4.5 ani wszystkich regionalnych naborów.

| Rejestr / zachowany Source | Oficjalny RSS | Import | Okno publikacji UTC |
| --- | --- | ---: | --- |
| 109 / 87 | https://wfosigw.katowice.pl/feed/ | 10 | 15.09.2026 06:30:10–01.10.2026 08:13:34 |
| 111 / 89 | https://wfosigw.olsztyn.pl/feed/ | 10 | 15.09.2026 10:02:45–01.10.2026 08:39:25 |

Obie oficjalne strony główne reklamują te kanały przez `link rel=alternate`
typu RSS. Katowice: canonicalny host bez www został odrębnie sprawdzony,
po odmowie podążania za cross-host redirect ze starego adresu. Nie zmieniano
ogólnej polityki przekierowań. Olsztyn: dotychczasowy host bez zmiany.

## Zakres i tożsamość

`regional_rss.py` ma teraz sześć profili: 82, 94, 96, 108, 109 i 111.
Nowe profile wymagają właściwego adaptera, dokładnej konfiguracji, HTTP200,
identycznego finalnego URL, tytułu kanału, jego linku głównego i jednego
poprawnego linku self. Maksymalnie 50 rekordów w bieżącym oknie RSS.
W każdym rekordzie: oficjalny HTTPS host, zgodna ścieżka artykułu, stabilny
GUID `?p=<ID>`, unikalne GUID/linki, kategoria Aktualności i źródłowa data
publikacji ze strefą czasową. Olsztyn używa ścieżek `/YYYY/MM/DD/slug/`.
Data wydarzenia w tytule nie zastępuje daty publikacji RSS.

Katowice dodają do adresów artykułów trzy parametry śledzące. Tylko dla
profilu109 dopuszczono ich dokładny zestaw (bez duplikatów/dodatkowych pól):

```
utm_source=rss
utm_medium=rss
utm_campaign=<slug artykułu>
```

Po zweryfikowaniu zestawu zapisywany jest czysty adres artykułu, a klucz
nadal pochodzi z GUID. Pozostałe profile nadal odrzucają query; parametry
zmieniające treść, inny campaign lub niezgodne źródło są odrzucane.
Dotychczasowe hashe metadanych czterech wcześniejszych profili nie zostały
rozszerzone o nowe pola. Nie pobiera się artykułów pod adresami z query.

Import: tylko tytuły, daty, linki i techniczne metadane. Bez treści,
autorów, kontaktów, załączników, komentarzy i pełnego archiwum.
`news_notice`, `funding_call_verified=False`, `full_document_downloaded=False`.
Nie dopisuje się dat aktualizacji, jeśli kanał ich nie podaje. Komunikaty
o programach/rozstrzygnięciach nie potwierdzają aktualnie otwartych naborów.
Cache15GB, retencja30dni i duże pliki na żądanie pozostają bez zmian.

## Nierozwiązane źródło świętokrzyskie110

Źródło110 / Source88: `https://www.wfos.com.pl`, niezmieniony web/config{},
wyłączone, niezatwierdzone, bez rekordów i historii sukcesu.
Zwykły transport nie dopuszcza obecnie hostu w katalogu. Izolowana próba
z tymczasowo dopuszczonym oficjalnym hostem wykazała dalszy problem:
lista `https://www.wfos.com.pl/aktualnosci-aktualne` zwraca HTTP301 do
`http://www.wfos.com.pl/aktualnosci-aktualne`. Wariant bez www tak samo
obniża protokół do HTTP. Odczyt samych nagłówków robots.txt zwrócił200,
co nie legalizuje przejścia listy na HTTP. Web reader także zgłosił
redirect loop; to osobny wynik, nie dowód typu przekierowania z aplikacji.

Nie dopuszczono HTTP, nie pominięto robots, nie zaakceptowano nowych warunków,
nie dodano hostu110 na stałe i nie zapisano rzekomo poprawnego audytu.
Potrzebny sprawdzony, rzeczywiście działający HTTPS endpoint RSS/listy lub
naprawa po stronie wydawcy. Aktualny sukces innych kanałów nie zamyka110.

## Bezpieczne uruchomienie i weryfikacja

Konfigurator `configure_regional_rss` nadal dry-run domyślnie, bez HTTP;
waliduje cały pakiet przed zapisem pod blokadą ingestion i transakcją.
Zastępuje tylko nietknięte wyłączone kolektory dawnych stron głównych,
bez historii/rekordów, zachowując ID, dane administratora i powiązania
rejestru. Pozostałe cztery już skonfigurowane profile pozostają bez zmian.
Powtórzenie zachowuje audyty i zatwierdzenie. `configured` nie oznacza
akceptacji pełnego produktu biznesowego.

Zweryfikowana sekwencja:

1. Testy RSS12 PASS0.346s, pełne482testy PostgreSQL PASS22.348s.
2. Ruff lint/format274 PASS, Django check PASS, brak migracji, build worker PASS.
3. Konfiguracja dry-run; SIGTERM tylko worker, naturalne zakończenie exit0,
   chwilowo restart=no, bez resetu lub wymuszenia.
4. Configure dry/apply; nowe109/111, wcześniejsze82/94/96/108 unchanged.
5. Świeże audyty verified10+10/0, configure repeat preserve audit,
   dry activation2, dry imports10+10/0, scoped activation120min.
6. Import20created/0updated; powtórzenie obu HTTP304/zero nowych rekordów.
7. Worker wznowiony2026-10-01T12:45:33Z z manifestem
   `5614589e38e0e5eb6bffadef0c8eb6881370b88a82b4ac31f49ace9aeaf49de8`,
   restartunless-stopped przywrócony; zachowano dane/cache/kursory.

Stan live:195Sources156active39disabled.20Items z precyzją time, bez
opisów/autorów/source_updated_at/query URL/potwierdzonych otwartych naborów.
Poprzednie82/94/96/108 nadal active/approved/auditverified;110 bez zmian.
Pięć usług działa, DBhealthy; ready i strony109/110/111 HTTP200.
Pełne34luki zakresu rejestru pozostają osobne od39wyłączonych kolektorów.
Kolejny pakiet: problem aktywnego EUR-Lex168 i pozostała kolejka4.5.2;
110 wróci dopiero po potwierdzeniu działającego bezpiecznego endpointu.
