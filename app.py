
import hmac
import os
import secrets
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

rng = secrets.SystemRandom()
WARSAW = ZoneInfo("Europe/Warsaw")

sessions = defaultdict(lambda: {
    "mode": None,
    "count": 1,
    "history": []
})

GAMES = {
    "Lotto": (6, 49),
    "Mini Lotto": (5, 42),
    "Eurojackpot": (5, 50),
    "Multi Multi": (10, 80),
    "Keno": (7, 70),
}

MAIN_MENU = [
    ["🎲 Wylosuj liczby", "🎰 Wylosuj grę"],
    ["🎟️ Generuj kupony", "🍀 Szczęśliwa liczba"],
    ["⚡ Szybki strzał", "📜 Moje losowania"],
    ["📅 Kalendarz", "📖 Zasady gier"],
    ["🎮 Strefa DEMO", "📷 Skaner"],
]

GAME_MENU = [
    ["Lotto", "Mini Lotto"],
    ["Eurojackpot", "Multi Multi"],
    ["Keno", "🔙 Menu główne"],
]

COUNT_MENU = [
    ["1 kupon", "3 kupony", "5 kuponów"],
    ["10 kuponów", "🔙 Menu główne"],
]

RULES_MENU = [
    ["Lotto", "Mini Lotto"],
    ["Eurojackpot", "Multi Multi"],
    ["Keno", "Szybkie 600"],
    ["🔙 Menu główne"],
]

RULES = {
    "Lotto": "Wybierasz 6 liczb z 49.",
    "Mini Lotto": "Wybierasz 5 liczb z 42.",
    "Eurojackpot": (
        "Wybierasz 5 liczb z 50 "
        "i 2 Euroliczby z 12."
    ),
    "Multi Multi": (
        "Typujesz od 1 do 10 liczb z 80. "
        "Losowanych jest 20 liczb. "
        "Generator typuje 10."
    ),
    "Keno": (
        "Typujesz od 1 do 10 liczb z 70. "
        "Losowanych jest 20 liczb. "
        "Generator typuje 7."
    ),
    "Szybkie 600": (
        "Aktualne zasady i tabele wygranych "
        "znajdziesz w regulaminie na lotto.pl."
    ),
}

DEMO_URL = (
    "https://lottoscan-x.onrender.com/static/demo.html"
)


def keyboard(rows=MAIN_MENU):
    return {
        "keyboard": [
            [{"text": title} for title in row]
            for row in rows
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def send(chat_id, text, rows=MAIN_MENU, inline=None):
    if inline is not None:
        markup = {"inline_keyboard": inline}
    else:
        markup = keyboard(rows)

    return jsonify({
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": text[:4000],
        "reply_markup": markup
    })


def draw(game):
    count, maximum = GAMES[game]

    numbers = sorted(
        rng.sample(range(1, maximum + 1), count)
    )

    result = " • ".join(map(str, numbers))

    if game == "Eurojackpot":
        stars = sorted(
            rng.sample(range(1, 13), 2)
        )
        result += (
            "\n⭐ Euroliczby: "
            + " • ".join(map(str, stars))
        )

    return result


def save_history(session, entry):
    session["history"].append(entry)
    session["history"] = session["history"][-10:]


@app.get("/")
def home():
    return jsonify(
        status="LottoScan X online",
        version="0.3"
    )


@app.get("/health")
def health():
    return jsonify(
        ok=True,
        service="LottoScan X"
    )


@app.post("/webhook")
def webhook():
    secret = os.environ.get("WEBHOOK_SECRET", "")
    supplied = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

    if not secret or not hmac.compare_digest(
        secret, supplied
    ):
        return jsonify(error="Brak dostępu"), 403

    update = request.get_json(silent=True)

    if not isinstance(update, dict):
        return jsonify(error="Niepoprawne dane"), 400

    msg = update.get("message")

    if not isinstance(msg, dict):
        return jsonify(ok=True)

    chat = msg.get("chat") or {}

    if (
        chat.get("type") != "private"
        or not isinstance(chat.get("id"), int)
    ):
        return jsonify(ok=True)

    chat_id = chat["id"]
    session = sessions[chat_id]

    text = (msg.get("text") or "").strip()

    if text.startswith("/") and text:
        command = text.split()[0].split("@")[0]
    else:
        command = text

    if command in (
        "/start", "/menu", "🔙 Menu główne"
    ):
        session["mode"] = None

        return send(
            chat_id,
            "🎱 Witaj w LottoScan X!\n\n"
            "Wybierz kafelek poniżej.\n\n"
            "🍀 Liczby są losowe, "
            "nie są prognozą wyników."
        )

    if command in (
        "🎲 Wylosuj liczby", "/losuj"
    ):
        session["mode"] = "single"

        return send(
            chat_id,
            "🎲 Wybierz grę:",
            GAME_MENU
        )

    if command in (
        "🎟️ Generuj kupony", "/kupon"
    ):
        session["mode"] = "count"

        return send(
            chat_id,
            "🎟️ Ile zestawów mam wygenerować?",
            COUNT_MENU
        )

    if (
        session["mode"] == "count"
        and text in (
            "1 kupon", "3 kupony",
            "5 kuponów", "10 kuponów"
        )
    ):
        session["count"] = int(text.split()[0])
        session["mode"] = "multiple"

        return send(
            chat_id,
            "🎟️ Teraz wybierz grę:",
            GAME_MENU
        )

    if command in (
        "📖 Zasady gier", "/zasady"
    ):
        session["mode"] = "rules"

        return send(
            chat_id,
            "📖 Wybierz grę:",
            RULES_MENU
        )

    if (
        session["mode"] == "rules"
        and text in RULES
    ):
        return send(
            chat_id,
            f"📖 {text}\n\n"
            f"{RULES[text]}\n\n"
            "Pełny aktualny regulamin:\n"
            "https://www.lotto.pl/",
            RULES_MENU
        )

    if (
        session["mode"] in ("single", "multiple")
        and text in GAMES
    ):
        count = (
            session["count"]
            if session["mode"] == "multiple"
            else 1
        )

        lines = [
            f"{i}. {draw(text)}"
            for i in range(1, count + 1)
        ]

        answer = (
            f"🎟️ {text} — {count} zestaw(ów):\n\n"
            + "\n".join(lines)
        )

        save_history(session, answer)
        session["mode"] = None

        return send(chat_id, answer)

    if command in (
        "🎰 Wylosuj grę", "/gra"
    ):
        game = rng.choice(list(GAMES))

        return send(
            chat_id,
            f"🎰 Wylosowana gra: {game}\n\n"
            "Wybierz «Wylosuj liczby», "
            "aby przygotować zestaw."
        )

    if command == "🍀 Szczęśliwa liczba":
        number = rng.randint(1, 49)

        return send(
            chat_id,
            f"🍀 Twoja szczęśliwa liczba: {number}"
        )

    if command == "⚡ Szybki strzał":
        game = rng.choice(list(GAMES))

        answer = f"⚡ {game}\n\n{draw(game)}"

        save_history(session, answer)

        return send(chat_id, answer)

    if command in (
        "📜 Moje losowania", "/historia"
    ):
        recent = session["history"][-5:]

        if recent:
            answer = (
                "📜 Ostatnie zestawy:\n\n"
                + "\n\n".join(recent)
            )
        else:
            answer = (
                "📜 Historia jest pusta.\n"
                "Znika również po restarcie serwera."
            )

        return send(chat_id, answer)

    if command in (
        "📅 Kalendarz", "/kalendarz"
    ):
        date = datetime.now(WARSAW).strftime(
            "%d.%m.%Y"
        )

        return send(
            chat_id,
            f"📅 Dziś: {date}\n\n"
            "Aktualny harmonogram losowań:\n"
            "https://www.lotto.pl/\n\n"
            "Automatyczny kalendarz dodamy później."
        )

    if command in (
        "🎮 Strefa DEMO", "/demo"
    ):
        return send(
            chat_id,
            "🎮 LOTTOScan X — STREFA DEMO\n\n"
            "🎱 KENO DEMO\n"
            "💰 Portfel startowy: 50 zł\n"
            "🔢 Plansza z 70 liczbami\n"
            "🎯 Wybór liczb i wirtualnej stawki\n"
            "🏆 Symulowane losowania i wyniki\n\n"
            "Szybkie 600 DEMO dodamy później.\n\n"
            "To symulator bez prawdziwych "
            "pieniędzy i nagród.",
            inline=[[
                {
                    "text": "🎱 OTWÓRZ KENO DEMO",
                    "web_app": {
                        "url": DEMO_URL
                    }
                }
            ]]
        )

    if command in (
        "📷 Skaner", "/skanuj", "/zdrapki"
    ):
        return send(
            chat_id,
            "📷 SKANER KUPONÓW\n\n"
            "Funkcja jest w przygotowaniu.\n"
            "Nie potwierdzamy wygranych "
            "bez oficjalnej weryfikacji."
        )

    if command in (
        "/help", "/pomoc"
    ):
        return send(
            chat_id,
            "🎱 LOTTOScan X — Pomoc\n\n"
            "/losuj — losowanie liczb\n"
            "/gra — losowanie gry\n"
            "/kupon — generator kuponów\n"
            "/historia — ostatnie zestawy\n"
            "/kalendarz — terminarz\n"
            "/zasady — instrukcje\n"
            "/demo — gry DEMO\n"
            "/skanuj — skaner"
        )

    if text in GAMES:
        return send(
            chat_id,
            "Najpierw wybierz "
            "🎲 Wylosuj liczby albo "
            "🎟️ Generuj kupony."
        )

    return send(
        chat_id,
        "Nie rozpoznaję polecenia.\n"
        "Kliknij przycisk w menu "
        "lub wpisz /start."
    )
