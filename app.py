
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
    "game": "Lotto",
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

MENU = [
    ["🎲 Wylosuj liczby", "🎰 Wylosuj grę"],
    ["🎟️ Generuj kupony", "🍀 Szczęśliwa liczba"],
    ["⚡ Szybki strzał", "📜 Moje losowania"],
    ["📅 Kalendarz", "📖 Zasady gier"],
    ["🎮 Strefa DEMO", "📷 Skaner"],
]

GAME_MENU = [
    ["Lotto", "Mini Lotto"],
    ["Eurojackpot", "Multi Multi"],
    ["Keno", "🔙 Menu główne"]
]

COUNT_MENU = [
    ["1 kupon", "3 kupony", "5 kuponów"],
    ["10 kuponów", "🔙 Menu główne"]
]

RULES_MENU = [
    ["Lotto", "Mini Lotto"],
    ["Eurojackpot", "Multi Multi"],
    ["Keno", "Szybkie 600"],
    ["🔙 Menu główne"]
]

RULES = {
    "Lotto": (
        "Wybiera się 6 liczb z 49. "
        "Trafienia porównuje się z oficjalnym losowaniem."
    ),
    "Mini Lotto": (
        "Wybiera się 5 liczb z 42. "
        "Trafienia porównuje się z oficjalnym losowaniem."
    ),
    "Eurojackpot": (
        "Wybiera się 5 liczb z 50 "
        "oraz 2 dodatkowe z 12."
    ),
    "Multi Multi": (
        "Można wytypować od 1 do 10 liczb z 80; "
        "w losowaniu pada 20 liczb. "
        "Generator przykładowo typuje 10."
    ),
    "Keno": (
        "Można wytypować od 1 do 10 liczb z 70; "
        "w losowaniu pada 20. "
        "Generator przykładowo typuje 7."
    ),
    "Szybkie 600": (
        "Ta gra ma własne zasady typowania i wygranych. "
        "Zanim dodamy kupony DEMO, zweryfikujemy "
        "aktualny regulamin organizatora."
    ),
}


def keyboard(rows=MENU):
    return {
        "keyboard": [
            [{"text": title} for title in row]
            for row in rows
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def pick(game):
    n, maximum = GAMES[game]
    numbers = sorted(
        rng.sample(range(1, maximum + 1), n)
    )
    result = " • ".join(map(str, numbers))

    if game == "Eurojackpot":
        extra = sorted(
            rng.sample(range(1, 13), 2)
        )
        result += (
            "\n⭐ Euroliczby: "
            + " • ".join(map(str, extra))
        )

    return result


def save(session, entry):
    session["history"].append(entry)
    session["history"] = session["history"][-10:]


def send(chat_id, answer, rows=MENU):
    return jsonify({
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": answer[:4000],
        "reply_markup": keyboard(rows)
    })


@app.get("/")
def home():
    return jsonify(
        status="LottoScan X online",
        version="0.2"
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

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(error="Niepoprawne dane"), 400

    msg = data.get("message")

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
    command = (
        text.split()[0].split("@")[0]
        if text.startswith("/") else text
    )

    if command in (
        "/start", "🔙 Menu główne", "/menu"
    ):
        session["mode"] = None
        return send(
            chat_id,
            "🎱 Witaj w LottoScan X!\n"
            "Wybierz kafelek poniżej.\n\n"
            "🍀 Losowania są losowe "
            "i nie przewidują wyników."
        )

    if command in (
        "🎲 Wylosuj liczby", "/losuj"
    ):
        session["mode"] = "single"
        return send(
            chat_id,
            "🎲 Z jakiej gry mam wylosować liczby?",
            GAME_MENU
        )

    if command in (
        "🎟️ Generuj kupony", "/kupon"
    ):
        session["mode"] = "count"
        return send(
            chat_id,
            "🎟️ Ile zestawów wygenerować?",
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
            f"🎟️ {session['count']} zestawów "
            "— wybierz grę:",
            GAME_MENU
        )

    if command in (
        "📖 Zasady gier", "/zasady"
    ):
        session["mode"] = "rules"
        return send(
            chat_id,
            "📖 Wybierz grę, której "
            "zasady chcesz poznać:",
            RULES_MENU
        )

    if (
        session["mode"] == "rules"
        and text in RULES
    ):
        return send(
            chat_id,
            f"📖 {text}\n\n{RULES[text]}\n\n"
            "Aktualne stawki, terminy "
            "i regulaminy sprawdź na lotto.pl.",
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

        entries = [
            f"{i}. {pick(text)}"
            for i in range(1, count + 1)
        ]

        answer = (
            f"🎟️ {text} — {count} zestaw(ów):\n\n"
            + "\n".join(entries)
        )

        save(session, answer)
        session["mode"] = None
        return send(chat_id, answer)

    if command in (
        "🎰 Wylosuj grę", "/gra"
    ):
        selected = rng.choice(list(GAMES))
        return send(
            chat_id,
            f"🎰 Wylosowana gra: {selected}\n"
            "Wybierz «Wylosuj liczby», "
            "aby wygenerować kupon."
        )

    if command == "🍀 Szczęśliwa liczba":
        number = rng.randint(1, 49)
        return send(
            chat_id,
            f"🍀 Szczęśliwa liczba (1–49): {number}"
        )

    if command == "⚡ Szybki strzał":
        game = rng.choice(list(GAMES))
        answer = f"⚡ {game}\n\n{pick(game)}"
        save(session, answer)
        return send(chat_id, answer)

    if command in (
        "📜 Moje losowania", "/historia"
    ):
        entries = session["history"][-5:]

        if entries:
            answer = (
                "📜 Ostatnie zestawy:\n\n"
                + "\n\n".join(entries)
            )
        else:
            answer = (
                "📜 Nie masz jeszcze "
                "wygenerowanych zestawów.\n"
                "Historia znika po restarcie serwera."
            )

        return send(chat_id, answer)

    if command in (
        "📅 Kalendarz", "/kalendarz"
    ):
        today = datetime.now(WARSAW).strftime(
            "%d.%m.%Y"
        )

        return send(
            chat_id,
            f"📅 Dziś: {today}\n\n"
            "Aktualne godziny losowań "
            "i wyniki sprawdzisz tutaj:\n"
            "https://www.lotto.pl/\n\n"
            "Automatyczny terminarz dodamy "
            "po podłączeniu źródła danych."
        )

    if command in (
        "🎮 Strefa DEMO", "/demo"
    ):
        return send(
            chat_id,
            "🎮 Strefa DEMO jest w przygotowaniu.\n"
            "Plan: Keno i Szybkie 600, "
            "wirtualne 50 zł, wybór liczb, "
            "losowanie i historia.\n"
            "Bez wpłat ani prawdziwych nagród."
        )

    if command in (
        "📷 Skaner", "/skanuj", "/zdrapki"
    ):
        return send(
            chat_id,
            "📷 Skaner jest w przygotowaniu.\n"
            "Zdjęcie lub odczyt kodu nie stanowią "
            "potwierdzenia wygranej.\n"
            "Do weryfikacji potrzebny jest "
            "oficjalny system LOTTO."
        )

    if command in (
        "/help", "/pomoc"
    ):
        return send(
            chat_id,
            "🎱 Wybierz kafelek z menu.\n"
            "/losuj — wybierz grę\n"
            "/kupon — wiele zestawów\n"
            "/gra — losowa gra\n"
            "/historia — ostatnie zestawy\n"
            "/kalendarz — informacje\n"
            "/zasady — instrukcje\n"
            "/demo — informacje o DEMO\n"
            "/skanuj — informacje o skanerze"
        )

    if text in GAMES:
        return send(
            chat_id,
            "Najpierw kliknij "
            "«🎲 Wylosuj liczby» albo "
            "«🎟️ Generuj kupony»."
        )

    return send(
        chat_id,
        "Nie rozpoznaję polecenia. "
        "Wybierz kafelek z menu "
        "lub wyślij /start."
    )
