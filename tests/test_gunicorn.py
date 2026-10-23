"""Actual HTTP across a restart and two Gunicorn workers, with a test database."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Gunicorn requires POSIX")
def test_two_workers_and_restart(config, tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    environment = dict(os.environ, DATABASE_URL=config["DATABASE_URL"],
                       WEBHOOK_SECRET=config["WEBHOOK_SECRET"], LOTTO_API_KEY=config["LOTTO_API_KEY"],
                       APP_ENV="development", GUNICORN_CMD_ARGS="")
    log = tmp_path / "gunicorn.log"

    def request(text, identifier):
        update = {"update_id": identifier, "message": {"message_id": identifier,
                  "chat": {"id": 999, "type": "private"}, "text": text}}
        data = json.dumps(update).encode()
        req = Request(base + "/webhook", data=data, headers={"Content-Type": "application/json",
            "X-Telegram-Bot-Api-Secret-Token": config["WEBHOOK_SECRET"]})
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def start(stream):
        process = subprocess.Popen([sys.executable, "-m", "gunicorn", "--workers", "2",
            "--bind", f"127.0.0.1:{port}", "--access-logfile", "-", "app:app"],
            cwd=Path(__file__).resolve().parents[1], env=environment, stdout=stream, stderr=stream)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urlopen(base + "/health/ready", timeout=0.3) as response:
                    if response.status == 200:
                        return process
            except (URLError, OSError):
                pass
            if process.poll() is not None:
                pytest.fail(log.read_text())
            time.sleep(0.05)
        process.terminate()
        process.wait(timeout=5)
        pytest.fail("Gunicorn did not become ready: " + log.read_text())

    def stop(process):
        process.terminate()
        process.wait(timeout=8)

    with log.open("a") as stream:
        process = start(stream)
        try:
            assert "Wybierz grę" in request("/losuj", 1)["text"]
            assert "Lotto" in request("Lotto", 2)["text"]
            for _ in range(6):
                assert request("Lotto", 2) == {"ok": True}
        finally:
            stop(process)
        restarted = start(stream)
        try:
            assert "Lotto" in request("/historia", 3)["text"]
            assert request("Lotto", 2) == {"ok": True}
        finally:
            stop(restarted)
    assert log.read_text().count("Booting worker with pid") >= 4
