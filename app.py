
import os
import hmac
import secrets
from flask import Flask, request, jsonify

app = Flask(__name__)
rng = secrets.SystemRandom()

MENU = [
    ["🎲 Wylosuj liczby", "🎰 Wylosuj grę"],
    ["🎟️ Generuj kupony", "🍀 Szczęśliwa liczba"],
    ["⚡ Szybki strzał", "📜 Moje losowania"]
]

GRY = {
    "Lotto": (6, 49),
    "Mini Lotto": (5, 42),
    "Multi Multi": (10, 80),
    "Keno": (7, 70)
}

historia = {}


def losuj(gra):
    if gra == "Eurojackpot":
        a = sorted(rng.sample(range(1, 51), 5))
        b = sorted(rng.sample(range(1, 13), 2))
        return f"{a}\nEuroliczby: {b}"

    ile, zakres = GRY[gra]
    liczby = sorted(rng.sample(range(1, zakres + 1), ile))
    return " • ".join(map(str, liczby))


def zapisz(user, tekst):
    historia.setdefault(user, []).append(tekst)
    historia[user] = historia[user][-10:]


def klawiatura(rows):
    return {
        "keyboard": [
            [{"text": x} for x in row] for row in rows
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


@app.get("/")
def home():
    return jsonify(status="LottoScan X online")


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.post("/webhook")
def webhook():
    secret = os.environ.get("WEBHOOK_SECRET", "")
    token = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

    if not secret or not hmac.compare_digest(secret, token):
        return jsonify(error="Brak dostepu"), 403

    data = request.get_json(silent=True) or {}
    msg = data.get("message") or {}
    chat = msg.get("chat") or {}

    if chat.get("type") != "private":
        return jsonify(ok=True)

    user = str(chat.get("id", ""))
    tekst = (msg.get("text") or "").strip()
    odpowiedz = ""
    menu = MENU

    if tekst == "/start":
        odpowiedz = (
            "🎱 Witaj w LottoScan X!\n\n"
            "Wybierz funkcję z kafelków poniżej.\n"
            "🍀 Powodzenia!"
        )

    elif tekst in ["/losuj", "🎲 Wylosuj liczby"]:
        odpowiedz = "🎲 Wybierz grę:"
        menu = [
            ["Lotto", "Mini Lotto"],
            ["Eurojackpot", "Multi Multi"],
            ["Keno", "🔙 Menu główne"]
        ]

    elif tekst in GRY or tekst == "Eurojackpot":
        wynik = losuj(tekst)
        odpowiedz = f"🎱 {tekst}\n\n{wynik}"
        zapisz(user, odpowiedz)

    elif tekst in ["/gra", "🎰 Wylosuj grę"]:
        gra = rng.choice(list(GRY) + ["Eurojackpot"])
        odpowiedz = f"🎰 Wylosowana gra: {gra}"

    elif tekst in ["/kupon", "🎟️ Generuj kupony"]:
        kupony = []
        for i in range(1, 6):
            kupony.append(f"{i}. {losuj('Lotto')}")
        odpowiedz = "🎟️ 5 kuponów Lotto:\n\n" + "\n".join(kupony)
        zapisz(user, odpowiedz)

    elif tekst == "🍀 Szczęśliwa liczba":
        n = rng.randint(1, 49)
        odpowiedz = f"🍀 Twoja szczęśliwa liczba: {n}"

    elif tekst == "⚡ Szybki strzał":
        gra = rng.choice(list(GRY) + ["Eurojackpot"])
        odpowiedz = f"⚡ {gra}\n\n{losuj(gra)}"
        zapisz(user, odpowiedz)

    elif tekst == "📜 Moje losowania":
        wpisy = historia.get(user, [])
        odpowiedz = (
            "\n\n".join(wpisy[-5:])
            if wpisy else "Nie masz jeszcze losowań."
        )

    elif tekst == "🔙 Menu główne":
        odpowiedz = "🏠 Menu główne"

    elif tekst in ["/help", "/pomoc"]:
        odpowiedz = "Wybierz funkcję z menu lub wpisz /start."

    else:
        odpowiedz = "🎱 Wybierz funkcję z menu poniżej."

    return jsonify({
        "method": "sendMessage",
        "chat_id": chat["id"],
        "text": odpowiedz,
        "reply_markup": klawiatura(menu)
    })
