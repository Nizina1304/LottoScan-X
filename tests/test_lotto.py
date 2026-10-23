import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest
from sqlalchemy import select, update

import lotto
from app import create_app
from lotto import LottoError, RESULT_SHAPES, latest_result, next_result, parse_date
from storage import cache_entries


def fixture(game="Lotto", numbers=None, special=None, **fields):
    count, _ = RESULT_SHAPES[game]
    row = {"gameType": game, "resultsJson": list(range(1, count + 1)) if numbers is None else numbers}
    if special is not None:
        row["specialResults"] = special
    elif game == "EuroJackpot":
        row["specialResults"] = [1, 12]
    result = {"drawSystemId": 123, "drawDate": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
              "results": [row]}
    result.update(fields)
    return [result]


@pytest.mark.parametrize("game", RESULT_SHAPES)
def test_valid_game(game):
    result = latest_result(fixture(game), game)
    assert result["gameType"] == game
    assert len(result["numbers"]) == RESULT_SHAPES[game][0]
    assert result["drawSystemId"] == 123


@pytest.mark.parametrize("numbers", [[99], [1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 50],
    [1, 1, 2, 3, 4, 5], [True, 2, 3, 4, 5, 6], [0, 2, 3, 4, 5, 6], [1.0, 2, 3, 4, 5, 6]])
def test_invalid_lotto_numbers(numbers):
    with pytest.raises(LottoError):
        latest_result(fixture(numbers=numbers), "Lotto")


@pytest.mark.parametrize("special", [[], [1], [1, 1], [1, 13], [0, 12], [1, True], "1,2"])
def test_invalid_euro_numbers(special):
    with pytest.raises(LottoError):
        latest_result(fixture("EuroJackpot", special=special), "EuroJackpot")


@pytest.mark.parametrize("draw_id", [None, 0, -1, True, "123", 2**63])
def test_invalid_id(draw_id):
    with pytest.raises(LottoError):
        latest_result(fixture(drawSystemId=draw_id), "Lotto")


def test_date_validation():
    with pytest.raises(ValueError):
        parse_date("2026-10-01T12:00:00")
    with pytest.raises(LottoError):
        latest_result(fixture(drawDate=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()), "Lotto")
    with pytest.raises(LottoError):
        next_result({"gameType": "Keno", "nextDrawDate": "2000-01-01T00:00:00Z"}, "Keno")


def test_endpoint_alias_and_cache(app, client, monkeypatch):
    fetch = Mock(return_value=fixture("EuroJackpot"))
    monkeypatch.setattr(app.extensions["lotto"], "fetch", fetch)
    first = client.get("/api/lotto/results?game=Eurojackpot")
    second = client.get("/api/lotto/results?game=EuroJackpot")
    assert first.status_code == second.status_code == 200
    assert first.json["cached"] is False and second.json["cached"] is True
    assert first.json["fetchedAt"] == second.json["fetchedAt"]
    assert first.json["data"]["specialResults"] == [1, 12]
    assert first.json["drawAgeSeconds"] >= 0
    assert fetch.call_count == 1
    assert first.headers["Cache-Control"] == "no-store"
    assert client.get("/api/lotto/results?game=unknown").status_code == 400


def test_invalid_payload_not_cached(app, client, monkeypatch):
    fetch = Mock(return_value={})
    monkeypatch.setattr(app.extensions["lotto"], "fetch", fetch)
    assert client.get("/api/lotto/results?game=Lotto").status_code == 502
    assert client.get("/api/lotto/results?game=Lotto").status_code == 502
    with app.extensions["storage"].engine.connect() as conn:
        entry = conn.execute(select(cache_entries)).mappings().one()
        assert entry["payload"] is None and entry["cooldown_until"] > time.time()
    assert fetch.call_count == 1  # Explicit error cooldown, not a cached invalid result.


def test_expired_calendar_is_refetched(app, client, monkeypatch):
    def future():
        return {"gameType": "Keno", "nextDrawDate": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()}
    fetch = Mock(side_effect=lambda *args: future())
    monkeypatch.setattr(app.extensions["lotto"], "fetch", fetch)
    assert client.get("/api/lotto/next-draw").status_code == 200
    with app.extensions["storage"].engine.begin() as conn:
        conn.execute(update(cache_entries).values(
            payload={"gameType": "Keno", "nextDrawDate": "2000-01-01T00:00:00Z"},
            expires_at=time.time() + 60))
    response = client.get("/api/lotto/next-draw")
    assert response.status_code == 200
    assert parse_date(response.json["data"]["nextDrawDate"]) > datetime.now(timezone.utc)
    assert fetch.call_count == 2


def test_calendar_cache_ends_at_draw(app, client, monkeypatch):
    stamp = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    monkeypatch.setattr(app.extensions["lotto"], "fetch", lambda *a: {"gameType": "Keno", "nextDrawDate": stamp})
    assert client.get("/api/lotto/next-draw").status_code == 200
    with app.extensions["storage"].engine.connect() as conn:
        entry = conn.execute(select(cache_entries)).mappings().one()
    assert entry["expires_at"] <= parse_date(stamp).timestamp() + 0.1


def test_single_refresh_shared_between_apps(app, config, monkeypatch):
    other = create_app(config)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(*args):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return fixture()
    for application in (app, other):
        monkeypatch.setattr(application.extensions["lotto"], "fetch", fetch)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks = [pool.submit(application.extensions["lotto"].payload, "results", "Lotto")
                     for application in (app, other, app, other)]
            assert entered.wait(3)
            time.sleep(0.1)
            release.set()
            responses = [task.result(timeout=5) for task in tasks]
        assert len(calls) == 1
        assert sum(not r["cached"] for r in responses) == 1
    finally:
        other.extensions["storage"].engine.dispose()


def test_public_rate_limit_shared(app, config, monkeypatch):
    cfg = dict(config, PUBLIC_API_LIMIT=1)
    first, second = create_app(cfg), create_app(cfg)
    for application in (first, second):
        monkeypatch.setattr(application.extensions["lotto"], "fetch", lambda *a: fixture())
    try:
        assert first.test_client().get("/api/lotto/results?game=Lotto").status_code == 200
        response = second.test_client().get("/api/lotto/results?game=Lotto")
        assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    finally:
        for application in (first, second):
            application.extensions["storage"].engine.dispose()


def test_upstream_budget_and_cooldown(app, monkeypatch):
    client = app.extensions["lotto"]
    client.upstream_limit = 1
    fetch = Mock(side_effect=lambda kind, game: fixture(game))
    monkeypatch.setattr(client, "fetch", fetch)
    client.payload("results", "Lotto")
    with pytest.raises(LottoError) as error:
        client.payload("results", "Keno")
    assert error.value.status == 503 and fetch.call_count == 1
    with pytest.raises(LottoError):
        client.payload("results", "Keno")
    assert fetch.call_count == 1


@pytest.mark.parametrize("code,expected", [(401, 503), (403, 503), (404, 404), (429, 503), (500, 503), (302, 503)])
def test_upstream_http_errors(app, monkeypatch, code, expected):
    opener = Mock()
    opener.open.side_effect = HTTPError("https://developers.lotto.pl/", code, "test", {"Retry-After": "15"}, None)
    monkeypatch.setattr(lotto, "build_opener", lambda *a: opener)
    with pytest.raises(LottoError) as error:
        app.extensions["lotto"].payload("results", "Lotto")
    assert error.value.status == expected
    assert "offline_dummy_key" not in str(error.value)


def test_redirects_not_followed():
    assert lotto.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid") is None


@pytest.mark.parametrize("multiplier", [True, {}, "2", float("nan"), float("inf")])
def test_invalid_multiplier(multiplier):
    with pytest.raises(LottoError):
        latest_result(fixture(multiplierValue=multiplier), "Lotto")


def test_nonfinite_upstream_json(app, monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"value":NaN}'
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(lotto, "build_opener", lambda *a: opener)
    with pytest.raises(LottoError):
        app.extensions["lotto"].payload("results", "Lotto")
