import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from storage import metadata


@pytest.fixture
def config(tmp_path, monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + str(tmp_path / "test.sqlite")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if make_url(url).get_backend_name() == "postgresql":
        # An explicit disposable test database only; never reset an app database.
        assert make_url(url).database == "lottoscan_test"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine(url)
    metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "head")
    return {"TESTING": True, "DATABASE_URL": url, "APP_ENV": "development",
            "WEBHOOK_SECRET": "offline_test_secret", "LOTTO_API_KEY": "offline_dummy_key",
            "PUBLIC_API_LIMIT": 60, "PUBLIC_GLOBAL_LIMIT": 300, "LOTTO_UPSTREAM_LIMIT": 12}


@pytest.fixture
def app(config):
    application = create_app(config)
    yield application
    application.extensions["storage"].engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()
