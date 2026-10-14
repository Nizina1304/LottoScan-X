import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
rng = secrets.SystemRandom()
WARSAW = ZoneInfo("Europe/Warsaw")
sessions = defaultdict(lambda: {"mode": None, "count": 1, "history": []})

GAMES = {
    "Lotto": (6, 49), "Mini Lotto": (5, 42),
    "Eurojackpot": (5, 50), "Multi Multi": (10, 80), "Keno": (7, 70),
}
API_GAMES = {
    "Lotto": "Lotto", "Mini Lotto": "MiniLotto",
    "Eurojackpot": "EuroJackpot", "Multi Multi": "MultiMulti",
    "Keno": "Keno", "Szybkie 600": "Szybkie600",
}
MAIN_MENU = [
    ["🎲 Wylosuj liczby", "🎰 Wylosuj grę"],
    ["🎟️ Generuj kupony", "🍀 Szczęśliwa liczba"],
    ["⚡ Szybki strzał", "📜 Moje losowania"],
    ["🏆 Oficjalne wyniki", "📅 Kalendarz"],
    ["📖 Zasady gier", "📷 Skaner"],
    ["🎮 Strefa DEMO"],
]
GAME_MENU = [
    ["Lotto", "Mini Lotto"], ["Eurojackpot", "Multi Multi"],
    ["Keno", "🔙 Menu główne"],
]
COUNT_MENU = [
    ["1 kupon", "3 kupony", "5 kuponów"],
    ["10 kuponów", "🔙 Menu główne"],
]
RULES_MENU = [
    ["Lotto", "Mini Lotto"], ["Eurojackpot", "Multi Multi"],
    ["Keno", "Szybkie 600"], ["🔙 Menu główne"],
]
RULES = {
    "Lotto": "Wybierasz 6 liczb z 49.",
    "Mini Lotto": "Wybierasz 5 liczb z 42.",
    "Eurojackpot": "Wybierasz 5 liczb z 50 i 2 Euroliczby z 12.",
    "Multi Multi": (
        "Typujesz od 1 do 10 liczb z 80. Losowanych jest 20 liczb. "
        "Generator typuje 10."
    ),
    "Keno": (
        "Typujesz od 1 do 10 liczb z 70. Losowanych jest 20 liczb. "
        "Generator typuje 7."
    ),
    "Szybkie 600": (
        "Aktualne zasady i tabele wygranych znajdziesz w regulaminie na lotto.pl."
    ),
}
DEMO_URL = "https://lottoscan-x.onrender.com/static/demo.html"
API_BASE = "https://developers.lotto.pl/api/open/v1/lotteries/"
API_PATHS = {
    "results": "draw-results/last-results-per-game",
    "next-draw": "info/next-draw",
}
CACHE_SECONDS = 60
api_cache = {}
api_cache_lock = threading.Lock()


class LottoError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    # Klucz pozostaje przy żądaniu do oficjalnego hosta LOTTO.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_date(value):
    if not isinstance(value, str):
        raise ValueError("Brak daty")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Brak strefy czasowej")
    return result


def date_pl(value):
    return parse_date(value).astimezone(WARSAW).strftime("%d.%m.%Y, %H:%M:%S")


def lotto_data(kind, game_type):
    key = os.environ.get("LOTTO_API_KEY", "").strip()
    if not key:
        raise LottoError("Brak LOTTO_API_KEY w ustawieniach Render.", 503)
    if kind not in API_PATHS or game_type not in API_GAMES.values():
        raise LottoError("Nieobsługiwana gra lub rodzaj danych.", 400)

    # Zmiana klucza unieważnia poprzednie wpisy pamięci podręcznej.
    cache_key = (kind, game_type, hashlib.sha256(key.encode()).digest())
    with api_cache_lock:
        cached = api_cache.get(cache_key)
        if cached and time.monotonic() < cached[0]:
            return cached[1], cached[2], True

    url = API_BASE + API_PATHS[kind] + "?" + urlencode({"gameType": game_type})
    query = Request(url, headers={
        "accept": "application/json", "secret": key,
        "User-Agent": "LottoScan-X/0.4",
    })
    try:
        with build_opener(NoRedirect()).open(query, timeout=6) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise LottoError("Odpowiedź LOTTO jest zbyt duża.")
        data = json.loads(raw)
    except HTTPError as error:
        error.close()
        if error.code in (401, 403):
            raise LottoError(
                "LOTTO odrzuciło autoryzację. Sprawdź klucz API w Render.", 503
            ) from None
        if error.code == 404:
            raise LottoError("LOTTO nie udostępniło jeszcze tych danych.", 404) from None
        if error.code == 429:
            raise LottoError("Limit zapytań LOTTO. Spróbuj ponownie później.", 503) from None
        raise LottoError("Usługa LOTTO jest chwilowo niedostępna.", 503) from None
    except (URLError, TimeoutError, socket.timeout, OSError):
        raise LottoError("Nie udało się połączyć z LOTTO. Spróbuj później.", 503) from None
    except (ValueError, UnicodeError):
        raise LottoError("LOTTO zwróciło niepoprawną odpowiedź.") from None

    if not isinstance(data, (list, dict)):
        raise LottoError("Nierozpoznany format odpowiedzi LOTTO.")
    fetched_at = datetime.now(timezone.utc).isoformat()
    with api_cache_lock:
        # Usunięcie wygasłych wpisów ogranicza rozmiar pamięci.
        now = time.monotonic()
        for old_key in list(api_cache):
            if api_cache[old_key][0] <= now:
                del api_cache[old_key]
        api_cache[cache_key] = (now + CACHE_SECONDS, data, fetched_at)
    return data, fetched_at, False


def latest_result(data, game_type):
    if not isinstance(data, list):
        raise LottoError("Nierozpoznany format wyników LOTTO.")
    candidates = []
    for draw_info in data:
        if not isinstance(draw_info, dict):
            continue
        rows = draw_info.get("results")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("gameType") != game_type:
                continue
            numbers = row.get("resultsJson")
            if not isinstance(numbers, list) or not numbers or not all(
                type(n) is int and n > 0 for n in numbers
            ) or len(set(numbers)) != len(numbers):
                continue
            stamp = row.get("drawDate") or draw_info.get("drawDate")
            try:
                parsed = parse_date(stamp)
            except (ValueError, TypeError):
                continue
            if parsed > datetime.now(timezone.utc):
                continue
            special = row.get("specialResults") or []
            if not isinstance(special, list) or not all(
                type(n) is int and n > 0 for n in special
            ):
                special = []
            candidates.append((parsed, {
                "gameType": game_type,
                "drawSystemId": row.get("drawSystemId") or draw_info.get("drawSystemId"),
                "drawDate": stamp,
                "numbers": numbers,
                "specialResults": special,
                "multiplierValue": draw_info.get("multiplierValue"),
            }))
    if not candidates:
        raise LottoError("Brak opublikowanego, poprawnego wyniku tej gry.", 404)
    return max(candidates, key=lambda item: item[0])[1]


def next_result(data, game_type):
    if not isinstance(data, dict) or data.get("gameType") != game_type:
        raise LottoError("Nierozpoznany format terminu losowania LOTTO.")
    try:
        stamp = data["nextDrawDate"]
        if parse_date(stamp) <= datetime.now(timezone.utc):
            raise ValueError("Termin minął")
    except (ValueError, KeyError, TypeError):
        raise LottoError("LOTTO nie podało jeszcze aktualnego terminu.", 404) from None
    return {"gameType": game_type, "nextDrawDate": stamp}


def official_payload(kind, game_type):
    data, fetched_at, cached = lotto_data(kind, game_type)
    result = latest_result(data, game_type) if kind == "results" else next_result(data, game_type)
    return {
        "ok": True, "source": "LOTTO OpenAPI", "fetchedAt": fetched_at,
        "cached": cached, "data": result,
    }


def keyboard(rows=MAIN_MENU):
    return {
        "keyboard": [[{"text": title} for title in row] for row in rows],
        "resize_keyboard": True, "is_persistent": True,
    }


def send(chat_id, text, rows=MAIN_MENU, inline=None):
    markup = {"inline_keyboard": inline} if inline is not None else keyboard(rows)
    return jsonify({
        "method": "sendMessage", "chat_id": chat_id, "text": text[:4000],
        "reply_markup": markup,
    })


def draw(game):
    count, maximum = GAMES[game]
    numbers = sorted(rng.sample(range(1, maximum + 1), count))
    result = " • ".join(map(str, numbers))
    if game == "Eurojackpot":
        stars = sorted(rng.sample(range(1, 13), 2))
        result += "\n⭐ Euroliczby: " + " • ".join(map(str, stars))
    return result


def save_history(session, entry):
    session["history"].append(entry)
    session["history"] = session["history"][-10:]


@app.get("/")
def home():
    return jsonify(status="LottoScan X online", version="0.4")


@app.get("/health")
def health():
    # Kontrola działania aplikacji nie wykonuje zapytań do LOTTO.
    return jsonify(ok=True, service="LottoScan X")


@app.get("/api/lotto/results")
@app.get("/api/lotto/next-draw")
def lotto_endpoint():
    game = request.args.get("game", "Keno")
    game_type = API_GAMES.get(game, game)
    if game_type not in API_GAMES.values():
        return jsonify(ok=False, error="Nieobsługiwana gra."), 400
    kind = "results" if request.path.endswith("/results") else "next-draw"
    try:
        response = jsonify(official_payload(kind, game_type))
        response.headers["Cache-Control"] = "no-store"
        return response
    except LottoError as error:
        return jsonify(ok=False, source="LOTTO OpenAPI", error=str(error)), error.status


@app.post("/webhook")
def webhook():
    secret = os.environ.get("WEBHOOK_SECRET", "")
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or not hmac.compare_digest(secret, supplied):
        return jsonify(error="Brak dostępu"), 403
    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return jsonify(error="Niepoprawne dane"), 400
    msg = update.get("message")
    if not isinstance(msg, dict):
        return jsonify(ok=True)
    chat = msg.get("chat") or {}
    if not isinstance(chat, dict) or chat.get("type") != "private" or type(chat.get("id")) is not int:
        return jsonify(ok=True)
    chat_id = chat["id"]
    session = sessions[chat_id]
    value = msg.get("text")
    text = value.strip() if isinstance(value, str) else ""
    command = text.split()[0].split("@")[0] if text.startswith("/") else text

    if command in ("/start", "/menu", "🔙 Menu główne"):
        session["mode"] = None
        return send(chat_id, "🎱 Witaj w LottoScan X!\n\nWybierz kafelek poniżej.\n\n"
                    "🍀 Liczby generatora są losowe, nie są prognozą wyników.")

    if command in ("🏆 Oficjalne wyniki", "/wyniki"):
        session["mode"] = "results"
        return send(chat_id, "🏆 Wybierz grę — pobiorę ostatni oficjalny wynik:", RULES_MENU)

    if command in ("📅 Kalendarz", "/kalendarz"):
        session["mode"] = "calendar"
        return send(chat_id, "📅 Wybierz grę — sprawdzę termin następnego losowania:", RULES_MENU)

    if session["mode"] in ("results", "calendar") and text in API_GAMES:
        kind = "results" if session["mode"] == "results" else "next-draw"
        try:
            payload = official_payload(kind, API_GAMES[text])
            info = payload["data"]
            if kind == "results":
                answer = (
                    f"🏆 {text} — ostatni oficjalny wynik\n\n"
                    f"📅 {date_pl(info['drawDate'])}\n"
                    f"🎟️ Numer losowania: {info['drawSystemId']}\n\n"
                    + " • ".join(map(str, info["numbers"]))
                )
                if info["specialResults"]:
                    label = "⭐ Euroliczby" if text == "Eurojackpot" else "⭐ Wynik specjalny"
                    answer += "\n" + label + ": " + " • ".join(map(str, info["specialResults"]))
            else:
                answer = f"📅 {text} — następne losowanie\n\n{date_pl(info['nextDrawDate'])}"
            answer += (
                "\n\nCzas: Polska. Źródło: LOTTO OpenAPI.\n"
                f"Pobrano: {date_pl(payload['fetchedAt'])}.\n"
                "Dane mogą być przechowywane w pamięci do 60 sekund."
            )
        except LottoError as error:
            answer = f"⚠️ {error}\n\nNie zastępujemy brakujących danych losowymi wynikami."
        return send(chat_id, answer, RULES_MENU)

    if command in ("🎲 Wylosuj liczby", "/losuj"):
        session["mode"] = "single"
        return send(chat_id, "🎲 Wybierz grę:", GAME_MENU)
    if command in ("🎟️ Generuj kupony", "/kupon"):
        session["mode"] = "count"
        return send(chat_id, "🎟️ Ile zestawów mam wygenerować?", COUNT_MENU)
    if session["mode"] == "count" and text in ("1 kupon", "3 kupony", "5 kuponów", "10 kuponów"):
        session["count"] = int(text.split()[0])
        session["mode"] = "multiple"
        return send(chat_id, "🎟️ Teraz wybierz grę:", GAME_MENU)
    if command in ("📖 Zasady gier", "/zasady"):
        session["mode"] = "rules"
        return send(chat_id, "📖 Wybierz grę:", RULES_MENU)
    if session["mode"] == "rules" and text in RULES:
        return send(chat_id, f"📖 {text}\n\n{RULES[text]}\n\n"
                    "Pełny aktualny regulamin:\nhttps://www.lotto.pl/", RULES_MENU)
    if session["mode"] in ("single", "multiple") and text in GAMES:
        count = session["count"] if session["mode"] == "multiple" else 1
        lines = [f"{i}. {draw(text)}" for i in range(1, count + 1)]
        answer = f"🎟️ {text} — {count} zestaw(ów):\n\n" + "\n".join(lines)
        save_history(session, answer)
        session["mode"] = None
        return send(chat_id, answer)
    if command in ("🎰 Wylosuj grę", "/gra"):
        game = rng.choice(list(GAMES))
        return send(chat_id, f"🎰 Wylosowana gra: {game}\n\n"
                    "Wybierz «Wylosuj liczby», aby przygotować zestaw.")
    if command == "🍀 Szczęśliwa liczba":
        return send(chat_id, f"🍀 Twoja szczęśliwa liczba: {rng.randint(1, 49)}")
    if command == "⚡ Szybki strzał":
        game = rng.choice(list(GAMES))
        answer = f"⚡ {game}\n\n{draw(game)}"
        save_history(session, answer)
        return send(chat_id, answer)
    if command in ("📜 Moje losowania", "/historia"):
        recent = session["history"][-5:]
        answer = "📜 Ostatnie zestawy:\n\n" + "\n\n".join(recent) if recent else (
            "📜 Historia jest pusta.\nZnika również po restarcie serwera."
        )
        return send(chat_id, answer)
    if command in ("🎮 Strefa DEMO", "/demo"):
        session["mode"] = None
        return send(chat_id,
            "🎮 LottoScan X — STREFA DEMO\n\n🎱 KENO DEMO\n"
            "💰 Portfel startowy: 50 zł\n🔢 Plansza z 70 liczbami\n"
            "🎯 Wybór liczb i wirtualnej stawki\n\n"
            "Ta miniaplikacja nadal używa symulowanych losowań. "
            "Podłączenie oficjalnych wyników w grze jest kolejnym etapem.\n\n"
            "Szybkie 600 DEMO dodamy później.\n\n"
            "To symulator bez prawdziwych pieniędzy i nagród.",
            inline=[[{"text": "🎱 OTWÓRZ KENO DEMO", "web_app": {"url": DEMO_URL}}]],
        )
    if command in ("📷 Skaner", "/skanuj", "/zdrapki"):
        return send(chat_id, "📷 SKANER KUPONÓW\n\nFunkcja jest w przygotowaniu.\n"
                    "Nie potwierdzamy wygranych bez oficjalnej weryfikacji.")
    if command in ("/help", "/pomoc"):
        return send(chat_id, "🎱 LottoScan X — Pomoc\n\n"
                    "/wyniki — oficjalne wyniki LOTTO\n/kalendarz — następne losowanie\n"
                    "/losuj — losowanie liczb\n/gra — losowanie gry\n"
                    "/kupon — generator kuponów\n/historia — ostatnie zestawy\n"
                    "/zasady — instrukcje\n/demo — gry DEMO\n/skanuj — skaner")
    if text in GAMES:
        return send(chat_id, "Najpierw wybierz 🎲 Wylosuj liczby albo 🎟️ Generuj kupony.")
    return send(chat_id, "Nie rozpoznaję polecenia.\nKliknij przycisk w menu lub wpisz /start.")
