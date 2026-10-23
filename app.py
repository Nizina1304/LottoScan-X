import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse
import secrets
import time
from zoneinfo import ZoneInfo

from flask import Blueprint, Flask, current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from lotto import LottoClient, LottoError, parse_date
from storage import ConcurrentUpdate, Storage

routes = Blueprint("bot", __name__)
rng = secrets.SystemRandom()
WARSAW = ZoneInfo("Europe/Warsaw")

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
def date_pl(value):
    return parse_date(value).astimezone(WARSAW).strftime("%d.%m.%Y, %H:%M:%S")


def official_payload(kind, game_type):
    return current_app.extensions["lotto"].payload(kind, game_type)


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


@routes.get("/")
def home():
    return jsonify(status="LottoScan X online", version="0.5")


@routes.get("/health")
@routes.get("/health/live")
def health():
    return jsonify(ok=True, service="LottoScan X")


@routes.get("/health/ready")
def ready():
    try:
        database = current_app.extensions["storage"].ready()
    except SQLAlchemyError:
        database = False
    checks = {"database": database,
              "webhookConfigured": bool(SECRET_PATTERN.fullmatch(current_app.config["WEBHOOK_SECRET"])),
              "lottoConfigured": bool(current_app.config["LOTTO_API_KEY"].strip())}
    return jsonify(ok=all(checks.values()), checks=checks), 200 if all(checks.values()) else 503


@routes.get("/api/lotto/results")
@routes.get("/api/lotto/next-draw")
def lotto_endpoint():
    game = request.args.get("game", "Keno")
    game_type = API_GAMES.get(game, game)
    if game_type not in API_GAMES.values():
        return jsonify(ok=False, error="Nieobsługiwana gra."), 400
    kind = "results" if request.path.endswith("/results") else "next-draw"
    storage = current_app.extensions["storage"]
    try:
        client = hashlib.sha256((request.remote_addr or "unknown").encode()).hexdigest()
        if not storage.allow("public:" + client, current_app.config["PUBLIC_API_LIMIT"]):
            return jsonify(ok=False, error="Zbyt wiele zapytań. Spróbuj za minutę."), 429, {"Retry-After": "60"}
        if not storage.allow("public-global", current_app.config["PUBLIC_GLOBAL_LIMIT"]):
            return jsonify(ok=False, error="Zbyt wiele zapytań. Spróbuj za minutę."), 429, {"Retry-After": "60"}
        response = jsonify(official_payload(kind, game_type))
        response.headers["Cache-Control"] = "no-store"
        return response
    except LottoError as error:
        return jsonify(ok=False, source="LOTTO OpenAPI", error=str(error)), error.status, {"Retry-After": str(error.retry_after)}
    except SQLAlchemyError:
        current_app.logger.error("database_unavailable")
        return jsonify(ok=False, error="Usługa jest chwilowo niedostępna."), 503


@routes.post("/webhook")
def webhook():
    secret = current_app.config["WEBHOOK_SECRET"]
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not SECRET_PATTERN.fullmatch(secret) or not SECRET_PATTERN.fullmatch(supplied) or not hmac.compare_digest(secret, supplied):
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
    value = msg.get("text")
    text = value.strip() if isinstance(value, str) else ""
    update_id = update.get("update_id")
    message_id = msg.get("message_id")
    if not all(type(value) is int and 0 < value < 2**63 for value in (update_id, message_id, chat_id)):
        return jsonify(error="Niepoprawny identyfikator aktualizacji lub wiadomości."), 400
    try:
        response = current_app.extensions["storage"].process_update(
            update_id, chat_id, message_id,
            lambda session: handle_message(chat_id, session, text),
        )
        return response if response is not None else jsonify(ok=True)
    except (SQLAlchemyError, ConcurrentUpdate):
        current_app.logger.error("telegram_state_unavailable")
        return jsonify(error="Nie udało się zapisać aktualizacji. Ponów żądanie."), 503


def handle_message(chat_id, session, text):
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
                "Dane mogą pochodzić z pamięci podręcznej do 60 sekund."
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
            "📜 Historia jest pusta."
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
            inline=[[{"text": "🎱 OTWÓRZ KENO DEMO", "web_app": {"url": current_app.config["DEMO_URL"]}}]],
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


SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")


def create_app(config=None):
    app = Flask(__name__)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=1024 * 1024,
        WEBHOOK_SECRET=os.environ.get("WEBHOOK_SECRET", ""),
        LOTTO_API_KEY=os.environ.get("LOTTO_API_KEY", ""),
        DATABASE_URL=os.environ.get("DATABASE_URL", "sqlite:///" + str(Path(app.instance_path) / "lottoscan.sqlite")),
        DEMO_URL=os.environ.get("DEMO_URL", "https://lottoscan-x.onrender.com/static/demo.html"),
        APP_ENV=os.environ.get("APP_ENV", "production" if os.environ.get("RENDER") == "true" else "development"),
        PUBLIC_API_LIMIT=int(os.environ.get("PUBLIC_API_LIMIT", "60")),
        PUBLIC_GLOBAL_LIMIT=int(os.environ.get("PUBLIC_GLOBAL_LIMIT", "300")),
        LOTTO_UPSTREAM_LIMIT=int(os.environ.get("LOTTO_UPSTREAM_LIMIT", "12")),
    )
    if config:
        app.config.update(config)
    if app.config["WEBHOOK_SECRET"] and not SECRET_PATTERN.fullmatch(app.config["WEBHOOK_SECRET"]):
        raise ValueError("WEBHOOK_SECRET musi zawierać 1–256 znaków A-Z, a-z, 0-9, _ lub -.")
    if app.config["APP_ENV"] == "production":
        if not app.config["DATABASE_URL"].startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            raise ValueError("W produkcji wymagany jest DATABASE_URL do PostgreSQL.")
        if not app.config["WEBHOOK_SECRET"]:
            raise ValueError("Brak WEBHOOK_SECRET dla produkcyjnego webhooka.")
    url = urlparse(app.config["DEMO_URL"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("DEMO_URL musi być poprawnym publicznym adresem HTTPS.")
    for key in ("PUBLIC_API_LIMIT", "PUBLIC_GLOBAL_LIMIT", "LOTTO_UPSTREAM_LIMIT"):
        if type(app.config[key]) is not int or app.config[key] < 1:
            raise ValueError(f"{key} musi być dodatnią liczbą całkowitą.")
    storage = Storage(app.config["DATABASE_URL"])
    app.extensions["storage"] = storage
    app.extensions["lotto"] = LottoClient(storage, app.config["LOTTO_API_KEY"], upstream_limit=app.config["LOTTO_UPSTREAM_LIMIT"])
    app.register_blueprint(routes)
    app.logger.setLevel("INFO")

    @app.cli.command("cleanup")
    def cleanup():
        """Remove expired coordination records, preserving conversation histories."""
        print(json.dumps(storage.cleanup()))

    @app.before_request
    def begin_request():
        g.request_started = time.monotonic()
        g.request_id = secrets.token_hex(8)

    @app.after_request
    def finish_request(response):
        response.headers["X-Request-ID"] = g.request_id
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        app.logger.info(json.dumps({"event": "http_request", "route": request.endpoint,
                                   "status": response.status_code, "request_id": g.request_id,
                                   "elapsed_ms": round((time.monotonic() - g.request_started) * 1000)}))
        return response

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(ok=False, error="Żądanie jest zbyt duże."), 413

    return app


app = create_app()
