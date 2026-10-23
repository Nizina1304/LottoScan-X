# LottoScan-X

Bot Telegram: generator zestawów, wyniki i terminy LOTTO OpenAPI oraz lokalny symulator Keno. Pakiet fundamentu v3 zachowuje wersję aplikacji `0.5`; nie oznacza ukończenia OCR, portfela ani DEMO na oficjalnych wynikach.

## Zawartość pakietu

- Walidacja liczby i zakresu głównych wyników 6 gier, ID losowania oraz Euroliczb.
- Sesje i historia w bazie, wspólne dla procesów i zachowywane po restarcie.
- Atomowy zapis stanu i ID aktualizacji. Powtórzenie nie generuje kolejnego zestawu ani odpowiedzi; spóźniona wiadomość nie nadpisuje nowszego stanu.
- Cache poprawnych wyników, blokada odświeżania wspólna dla procesów i wygaśnięcie terminu kalendarza po losowaniu.
- Wspólne limity publicznego API i pobrań z LOTTO, przerwy po błędach upstream.
- Migracje Alembic, testy Flask/Gunicorn i CI dla SQLite/PostgreSQL.

PostgreSQL przechowuje także cache i liczniki. Redis można dodać po pomiarach; nie jest wymagany w tym pakiecie.

## Uruchomienie lokalne

Python 3.12. Testy nie potrzebują tokenów produkcyjnych.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
mkdir -p instance
export DATABASE_URL=sqlite:///instance/lottoscan.sqlite
export WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
alembic upgrade head
flask --app app run
```

`LOTTO_API_KEY` ustaw w środowisku do rzeczywistych pobrań. Bez niego generator działa, a funkcje LOTTO zwracają kontrolowany błąd. `.env.example` jest wzorem; aplikacja nie ładuje `.env` automatycznie.

## Konfiguracja

| Zmienna | Znaczenie |
| --- | --- |
| `DATABASE_URL` | PostgreSQL w produkcji; prefiksy `postgres://`, `postgresql://`, `postgresql+psycopg://`. SQLite do lokalnej pracy. Jedna baza obsługuje jednego bota. |
| `WEBHOOK_SECRET` | 1–256 znaków `A-Z a-z 0-9 _ -`, identyczny z Telegram `secret_token`. Pusty blokuje webhook; błędny format zatrzymuje start. |
| `LOTTO_API_KEY` | Klucz wysyłany tylko z backendu do LOTTO w nagłówku `secret`. |
| `APP_ENV` | `production` wymaga PostgreSQL i sekretu webhooka. Domyślny na Render (`RENDER=true`). |
| `DEMO_URL` | Publiczny HTTPS miniaplikacji; domyślnie dotychczasowy Render. Na stagingu ustaw osobny URL. |
| `PUBLIC_API_LIMIT` | 60 odczytów/minutę na remote_addr. |
| `PUBLIC_GLOBAL_LIMIT` | 300 odczytów/minutę łącznie w publicznym API. |
| `LOTTO_UPSTREAM_LIMIT` | 12 pobrań/minutę łącznie, współdzielone przez procesy i gry. Dopasuj do potwierdzonego przydziału LOTTO. |

Limity mają stałe okna minutowe; ich granica dopuszcza krótkotrwały większy ruch. Nie deklarują zgodności z nieznanym limitem umownym LOTTO. X-Forwarded-For nie jest automatycznie uznawany za adres klienta; za proxy limit per adres może obejmować więcej użytkowników. Globalny budżet działa niezależnie. Zaufane proxy skonfiguruj dopiero po ustaleniu topologii.

Nie zapisuj sekretów ani pełnych adresów bazy z hasłem w repozytorium lub issue.

## Render

Zmiana wymaga skonfigurowanej bazy i migracji przed uruchomieniem nowej wersji. Zacznij od stagingu z osobnym botem i sekretami.

1. Ustaw `DATABASE_URL`, `WEBHOOK_SECRET`, `LOTTO_API_KEY`, `APP_ENV=production`, właściwy `DEMO_URL`.
2. Build: `pip install -r requirements.txt`.
3. Pre-deploy: `alembic upgrade head`. Jeśli plan nie ma pre-deploy, wykonaj migrację osobno przed przełączeniem ruchu. Nie uruchamiaj jej w każdym workerze.
4. Start: `gunicorn --workers 2 --bind 0.0.0.0:$PORT app:app`. Liczbę procesów dostosuj do planu.
5. Wykonaj [odbiór stagingu](docs/operations.md): readiness, przepływy, restart i prawdziwe odczyty LOTTO.

Nowa baza nie odzyska historii utraconej przy wcześniejszych restartach procesów.

## Endpointy i granice

| Endpoint | Zachowanie |
| --- | --- |
| `POST /webhook` | Wymagany sekret w nagłówku, prywatna wiadomość i poprawne update_id/message_id/chat.id. |
| `GET /api/lotto/results?game=Keno` | Wynik: source, fetchedAt, cached, drawAgeSeconds. Wiek nie potwierdza aktualności. |
| `GET /api/lotto/next-draw?game=Keno` | Przyszły termin losowania. |
| `GET /health`, `/health/live` | Żywotność procesu, bez pytań do LOTTO. |
| `GET /health/ready` | Konfiguracja i schemat bazy; bez potwierdzania klucza u LOTTO. |
| `GET /static/demo.html` | Dotychczasowy lokalny symulator, fikcyjne saldo i wypłaty. |

Gry API: Lotto, Mini Lotto, Eurojackpot, Multi Multi, Keno, Szybkie 600. Generator ma osobną listę 5 gier. Pola specjalne poza Eurojackpotem mają zachowawczą walidację struktury; ich pełne znaczenie i mnożniki wymagają rzeczywistych próbek. Syntetyczne testy nie certyfikują integracji LOTTO.

sendMessage w body webhooka jest mechanizmem Telegrama, ale nie daje wyniku wysłania. Efekt bazy jest pojedynczy; utrata połączenia po zapisie może pozbawić użytkownika odpowiedzi. Trwały outbox/worker jest w [kolejnym etapie](docs/v3-roadmap.md).

## Testy i utrzymanie

```bash
python -m pytest -q
pip check
alembic check
flask --app app cleanup
```

Testy tworzą pliki SQLite w katalogu tymczasowym. `TEST_DATABASE_URL` włącza **jednorazową bazę o nazwie lottoscan_test**: jej schemat jest kasowany i odtwarzany między testami. Nie wskazuj bazy aplikacji. CI sprawdza obie bazy i Gunicorn z dwoma procesami.

cleanup usuwa ID aktualizacji starsze niż 7 dni, stare liczniki i cache. Zachowuje historię rozmów. Uruchamiaj go regularnie we własnym mechanizmie zadań. Stan rozmowy wygasa po 30 minutach bezczynności; historia zachowuje ostatnich 10 zestawów na czat.

Logi zawierają nazwę endpointu, status, czas i ID żądania; bez nagłówków i treści kuponów. Odpowiedzi mają X-Request-ID. Dokładne wersje zależności są w constraints.txt; aktualizuj je w osobnym PR z testami obu baz.
