# LottoScan-X v3 — zakres i dalsze kroki

Zadanie nadrzędne: [Nizina1304/Nexus-bot#1](https://github.com/Nizina1304/Nexus-bot/issues/1).

## Przygotowany pakiet fundamentu

- [x] Schematy głównych wyników 6 gier, poprawne ID i Euroliczby.
- [x] Cache zwalidowanych danych z wygaśnięciem terminu kalendarza.
- [x] Sesje i historia SQLAlchemy, migracja SQLite/PostgreSQL.
- [x] Atomowy zapis stanu i deduplikacji; spóźniona wiadomość nie cofa stanu.
- [x] Wspólne limity i blokada odświeżania; cooldown po błędach.
- [x] Walidacja sekretu, konfigurowany DEMO_URL, readiness i logi bez treści.
- [x] Testy HTTP, współbieżności i Gunicorn oraz workflow CI obu baz.
- [x] Instrukcje konfiguracji, backupu/restore i cofania aplikacji.

Oznaczenia dotyczą implementacji, nie potwierdzonego wdrożenia produkcyjnego. Historia to ostatnich 10 tekstowych zestawów, nie ewidencja zakupionych kuponów.

## Przed produkcją

- [ ] Rzeczywiste próbki obu endpointów LOTTO dla 6 gier, pola specjalne i mnożniki.
- [ ] Przydział API, topologia proxy i właściwe limity.
- [ ] PostgreSQL, sekrety, migracje i SHA wdrożenia w Render.
- [ ] Webhook, restart i odtworzenie backupu na stagingu.

## Kolejne pakiety

1. Powiadomienia: trwały inbox/outbox i worker, bez blokowania webhooka na LOTTO; kontrola wyniku sendMessage i niepewnego dostarczenia.
2. Keno DEMO na oficjalnych wynikach: initData/auth_date, konto na serwerze, wersjonowane reguły, zamknięcie typowania przed wynikiem, pojedyncze obciążenie/rozliczenie. Brak wyniku oznacza oczekiwanie. Osobno oznaczone odtwarzanie historii.
3. Szybkie 600 DEMO: własne reguły i testy, bez kopiowania tabeli Keno.
4. Portfel: User/Ticket/WalletTransaction, kwoty w groszach, oddzielenie DEMO od wydatków, jedno rozliczenie kuponu.
5. OCR: odczyt zdjęcia, pewność i korekta przed zapisem, weryfikacja gry/losowania. OCR nie potwierdza wygranej.
6. Zasady i panel: bezpośrednie źródła regulaminów z datą oraz interfejs aktualnych wyników oparty na wspólnym adapterze.

Każdy pakiet ma własne kryteria i wymaga weryfikacji integracji. Obecnego symulatora nie oznaczamy jako ukończonego DEMO z oficjalnymi wynikami.
