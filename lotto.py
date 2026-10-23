"""Validate official results before caching or presenting them to a user."""
import hashlib
import json
import secrets
import socket
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_BASE = "https://developers.lotto.pl/api/open/v1/lotteries/"
API_PATHS = {"results": "draw-results/last-results-per-game", "next-draw": "info/next-draw"}
RESULT_SHAPES = {
    "Lotto": (6, 49), "MiniLotto": (5, 42), "EuroJackpot": (5, 50),
    "MultiMulti": (20, 80), "Keno": (20, 70), "Szybkie600": (6, 32),
}


class LottoError(Exception):
    def __init__(self, message, status=502, retry_after=5):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_date(value):
    if not isinstance(value, str):
        raise ValueError("Brak daty")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Brak strefy czasowej")
    return result.astimezone(timezone.utc)


def valid_numbers(numbers, count, maximum):
    return (
        isinstance(numbers, list) and len(numbers) == count
        and all(type(n) is int and 1 <= n <= maximum for n in numbers)
        and len(set(numbers)) == count
    )


def reject_nonfinite(value):
    raise ValueError("Niedozwolona stała JSON")


def latest_result(data, game_type):
    if not isinstance(data, list) or game_type not in RESULT_SHAPES:
        raise LottoError("Nierozpoznany format wyników LOTTO.")
    count, maximum = RESULT_SHAPES[game_type]
    candidates = []
    for draw_info in data:
        if not isinstance(draw_info, dict) or not isinstance(draw_info.get("results"), list):
            continue
        for row in draw_info["results"]:
            if not isinstance(row, dict) or row.get("gameType") != game_type:
                continue
            if not valid_numbers(row.get("resultsJson"), count, maximum):
                continue
            draw_id = row.get("drawSystemId", draw_info.get("drawSystemId"))
            if type(draw_id) is not int or not 0 < draw_id < 2**63:
                continue
            multiplier = draw_info.get("multiplierValue")
            if multiplier is not None and (type(multiplier) is not int or not 0 <= multiplier < 2**63):
                continue
            stamp = row.get("drawDate") or draw_info.get("drawDate")
            try:
                parsed = parse_date(stamp)
            except (ValueError, TypeError, OverflowError):
                continue
            if parsed > datetime.now(timezone.utc):
                continue
            special = row.get("specialResults")
            if special is None:
                special = []
            if game_type == "EuroJackpot":
                if not valid_numbers(special, 2, 12):
                    continue
            elif (not isinstance(special, list) or len(special) > 20
                  or not all(type(n) is int and 0 < n < 2**63 for n in special)
                  or len(set(special)) != len(special)):
                continue
            candidates.append((parsed, {
                "gameType": game_type, "drawSystemId": draw_id,
                "drawDate": parsed.isoformat(), "numbers": row["resultsJson"],
                "specialResults": special, "multiplierValue": multiplier,
            }))
    if not candidates:
        raise LottoError("Brak opublikowanego, kompletnego wyniku tej gry.", 404)
    return max(candidates, key=lambda item: item[0])[1]


def next_result(data, game_type):
    if not isinstance(data, dict) or data.get("gameType") != game_type:
        raise LottoError("Nierozpoznany format terminu losowania LOTTO.")
    try:
        stamp = parse_date(data["nextDrawDate"])
        if stamp <= datetime.now(timezone.utc):
            raise ValueError("Termin minął")
    except (ValueError, KeyError, TypeError, OverflowError):
        raise LottoError("LOTTO nie podało jeszcze aktualnego terminu.", 404) from None
    return {"gameType": game_type, "nextDrawDate": stamp.isoformat()}


class LottoClient:
    def __init__(self, storage, api_key, cache_seconds=60, upstream_limit=12):
        self.storage = storage
        self.api_key = api_key.strip()
        self.cache_seconds = cache_seconds
        self.upstream_limit = upstream_limit

    def fetch(self, kind, game_type):
        query = Request(API_BASE + API_PATHS[kind] + "?" + urlencode({"gameType": game_type}), headers={
            "accept": "application/json", "secret": self.api_key, "User-Agent": "LottoScan-X/0.5",
        })
        try:
            with build_opener(NoRedirect()).open(query, timeout=6) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise LottoError("Odpowiedź LOTTO jest zbyt duża.")
            return json.loads(raw, parse_constant=reject_nonfinite)
        except HTTPError as error:
            code = error.code
            retry_after = error.headers.get("Retry-After", "10") if error.headers else "10"
            error.close()
            if code in (401, 403):
                raise LottoError("LOTTO odrzuciło autoryzację. Sprawdź konfigurację serwera.", 503, 30) from None
            if code == 404:
                raise LottoError("LOTTO nie udostępniło jeszcze tych danych.", 404) from None
            if code == 429:
                delay = min(60, max(1, int(retry_after))) if len(retry_after) <= 4 and retry_after.isdecimal() else 10
                raise LottoError("Limit zapytań LOTTO. Spróbuj ponownie później.", 503, delay) from None
            raise LottoError("Usługa LOTTO jest chwilowo niedostępna.", 503) from None
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise LottoError("Nie udało się połączyć z LOTTO. Spróbuj później.", 503) from None
        except (ValueError, UnicodeError):
            raise LottoError("LOTTO zwróciło niepoprawną odpowiedź.") from None

    def payload(self, kind, game_type):
        if not self.api_key:
            raise LottoError("Brak konfiguracji połączenia z LOTTO.", 503)
        if kind not in API_PATHS or game_type not in RESULT_SHAPES:
            raise LottoError("Nieobsługiwana gra lub rodzaj danych.", 400)
        fingerprint = hashlib.sha256(self.api_key.encode()).hexdigest()
        key = f"{kind}:{game_type}:{fingerprint}"
        owner = secrets.token_hex(16)
        deadline = time.monotonic() + 1.5
        while True:
            cached = self.storage.cached(key)
            now = time.time()
            if self._usable(cached, kind):
                return self._payload(cached["payload"], cached["fetched_at"], True)
            if cached and cached["cooldown_until"] > now:
                raise LottoError(cached["error"], cached["error_status"],
                                 max(1, int(cached["cooldown_until"] - now) + 1))
            if self.storage.acquire(key, owner, 20):
                # Another worker might have filled the cache just before the lease.
                cached = self.storage.cached(key)
                if self._usable(cached, kind):
                    self.storage.release(key, owner)
                    return self._payload(cached["payload"], cached["fetched_at"], True)
                break
            if time.monotonic() >= deadline:
                raise LottoError("Trwa pobieranie wyniku. Spróbuj ponownie za chwilę.", 503, 2)
            time.sleep(0.025)
        try:
            if not self.storage.allow("lotto-upstream", self.upstream_limit):
                raise LottoError("Osiągnięto budżet zapytań do LOTTO. Spróbuj później.", 503, 10)
            data = self.fetch(kind, game_type)
            result = latest_result(data, game_type) if kind == "results" else next_result(data, game_type)
            ttl = self.cache_seconds
            if kind == "next-draw":
                ttl = min(ttl, max(0, parse_date(result["nextDrawDate"]).timestamp() - time.time()))
            fetched_at = self.storage.cache_success(key, owner, result, ttl)
            return self._payload(result, fetched_at, False)
        except LottoError as error:
            self.storage.cache_failure(key, owner, error, error.status, error.retry_after)
            raise

    @staticmethod
    def _usable(cached, kind):
        now = time.time()
        if not cached or not cached["payload"] or cached["expires_at"] <= now:
            return False
        if kind == "next-draw":
            try:
                return parse_date(cached["payload"]["nextDrawDate"]).timestamp() > now
            except (ValueError, TypeError, KeyError, OverflowError):
                return False
        return True

    @staticmethod
    def _payload(result, fetched_at, cached):
        payload = {"ok": True, "source": "LOTTO OpenAPI", "fetchedAt": fetched_at,
                   "cached": cached, "data": result}
        if "drawDate" in result:
            payload["drawAgeSeconds"] = max(0, int(time.time() - parse_date(result["drawDate"]).timestamp()))
        return payload
