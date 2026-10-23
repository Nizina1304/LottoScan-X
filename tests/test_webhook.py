import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

import app as module
from app import create_app
from lotto import LottoError
from storage import bot_sessions, processed_updates

HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "offline_test_secret"}


def send(client, text, identifier, chat_id=123):
    return client.post("/webhook", headers=HEADERS, json={"update_id": identifier,
        "message": {"message_id": identifier, "chat": {"id": chat_id, "type": "private"}, "text": text}})


def state(app, chat_id=123):
    with app.extensions["storage"].engine.connect() as conn:
        return conn.execute(select(bot_sessions.c.state).where(bot_sessions.c.chat_id == chat_id)).scalar_one()


@pytest.mark.parametrize("secret", ["", "wrong", "é", "a" * 257])
def test_bad_secret_is_403(client, secret):
    response = client.post("/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": secret}, json={})
    assert response.status_code == 403


@pytest.mark.parametrize("body", [None, [], "bad"])
def test_bad_body(client, body):
    assert client.post("/webhook", headers=HEADERS, json=body).status_code == 400


@pytest.mark.parametrize("identifier", [None, True, 0, -1, 2**63, "123"])
def test_bad_id(client, identifier):
    assert send(client, "/start", identifier).status_code == 400


def test_generator_flow_and_durable_restart(app, config, client):
    assert send(client, "/kupon", 1).status_code == 200
    assert "Teraz wybierz" in send(client, "3 kupony", 2).json["text"]
    # A different app/engine handles the next step of the same conversation.
    other = create_app(config)
    try:
        result = send(other.test_client(), "Eurojackpot", 3)
        assert "3 zestaw" in result.json["text"] and "Euroliczby" in result.json["text"]
        app.extensions["storage"].engine.dispose()
        restarted = create_app(config)
        try:
            history = send(restarted.test_client(), "/historia", 4)
            assert "Eurojackpot" in history.json["text"]
            assert len(state(restarted)["history"]) == 1
            duplicate = send(restarted.test_client(), "Eurojackpot", 3)
            assert duplicate.json == {"ok": True}
        finally:
            restarted.extensions["storage"].engine.dispose()
    finally:
        other.extensions["storage"].engine.dispose()


def test_duplicate_has_single_effect(app, client):
    first = send(client, "⚡ Szybki strzał", 42)
    second = send(client, "⚡ Szybki strzał", 42)
    assert first.json["method"] == "sendMessage"
    assert second.json == {"ok": True}
    assert len(state(app)["history"]) == 1


def test_concurrent_duplicate_has_single_effect(app, monkeypatch):
    original = module.handle_message
    barrier = threading.Barrier(3)
    def concurrent(*args):
        barrier.wait(timeout=5)
        return original(*args)
    monkeypatch.setattr(module, "handle_message", concurrent)
    def request():
        return send(app.test_client(), "⚡ Szybki strzał", 1)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: request(), range(3)))
    assert all(result.status_code == 200 for result in results)
    assert sum(result.json.get("method") == "sendMessage" for result in results) == 1
    assert len(state(app)["history"]) == 1


def test_late_message_cannot_rewind_state(app, client):
    send(client, "/losuj", 2)
    assert send(client, "/wyniki", 1).json == {"ok": True}
    assert state(app)["mode"] == "single"


def test_session_timeout_preserves_history(app, client):
    send(client, "⚡ Szybki strzał", 1)
    send(client, "/losuj", 2)
    with app.extensions["storage"].engine.begin() as conn:
        conn.execute(update(bot_sessions).values(updated_at=0))
    assert "Najpierw wybierz" in send(client, "Lotto", 3).json["text"]
    assert len(state(app)["history"]) == 1


def test_exception_does_not_ack_or_commit(app):
    storage = app.extensions["storage"]
    def failing(session):
        session["history"].append("will be rolled back")
        raise RuntimeError("Injected crash before commit")
    with pytest.raises(RuntimeError):
        storage.process_update(1, 123, 1, failing)
    with storage.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(processed_updates)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(bot_sessions)).scalar_one() == 0
    assert send(app.test_client(), "⚡ Szybki strzał", 1).status_code == 200
    assert len(state(app)["history"]) == 1


def test_database_failure_is_retryable(app, client, monkeypatch):
    failure = OperationalError("offline", {}, Exception("offline"))
    monkeypatch.setattr(app.extensions["storage"], "process_update", Mock(side_effect=failure))
    assert send(client, "/start", 1).status_code == 503


def test_lotto_failure_never_generates_result(app, client, monkeypatch):
    send(client, "/wyniki", 1)
    monkeypatch.setattr(app.extensions["lotto"], "payload", Mock(side_effect=LottoError("Offline awaria", 503)))
    response = send(client, "Lotto", 2)
    assert "Nie zastępujemy" in response.json["text"]
    assert state(app)["history"] == []


def test_demo_url_is_configurable(config):
    application = create_app(dict(config, DEMO_URL="https://staging.example.org/static/demo.html"))
    try:
        response = send(application.test_client(), "/demo", 1)
        assert response.json["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"].startswith("https://staging.")
        assert "symulowanych" in response.json["text"]
    finally:
        application.extensions["storage"].engine.dispose()


def test_readiness_and_liveness(app, client, monkeypatch):
    fetch = Mock(side_effect=AssertionError("Health must not call LOTTO"))
    monkeypatch.setattr(app.extensions["lotto"], "fetch", fetch)
    assert client.get("/health").status_code == 200
    assert client.get("/health/ready").status_code == 200
    app.config["LOTTO_API_KEY"] = ""
    assert client.get("/health/ready").status_code == 503
    assert client.get("/health/live").status_code == 200
    fetch.assert_not_called()


@pytest.mark.parametrize("setting", [
    {"WEBHOOK_SECRET": "é"}, {"DEMO_URL": "http://example.org"},
    {"APP_ENV": "production"}, {"PUBLIC_API_LIMIT": 0},
])
def test_invalid_config_is_rejected(config, setting):
    if setting == {"APP_ENV": "production"} and config["DATABASE_URL"].startswith("postgres"):
        setting = dict(setting, DATABASE_URL="sqlite:///local.sqlite")
    with pytest.raises(ValueError):
        create_app(dict(config, **setting))


def test_request_limit_and_logging(client, caplog):
    response = client.post("/webhook", headers=HEADERS, data="x" * (1024 * 1024 + 1), content_type="application/json")
    assert response.status_code == 413 and response.is_json
    assert response.headers["X-Request-ID"]
    assert "offline_test_secret" not in caplog.text
    assert "offline_dummy_key" not in caplog.text


def test_cleanup_keeps_history(app, client):
    send(client, "⚡ Szybki strzał", 1)
    with app.extensions["storage"].engine.begin() as conn:
        conn.execute(update(processed_updates).values(created_at=0))
    result = app.test_cli_runner().invoke(args=["cleanup"])
    assert result.exit_code == 0
    assert len(state(app)["history"]) == 1
