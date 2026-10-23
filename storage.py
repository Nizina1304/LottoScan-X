"""Shared durable state and short transactions for PostgreSQL or local SQLite."""
import copy
import time
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Column, Float, Integer, MetaData, String, Table,
    create_engine, delete, inspect, select, update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

metadata = MetaData()
bot_sessions = Table(
    "bot_sessions", metadata,
    Column("chat_id", BigInteger, primary_key=True),
    Column("version", Integer, nullable=False),
    Column("message_id", BigInteger, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("state", JSON, nullable=False),
)
processed_updates = Table(
    "processed_updates", metadata,
    Column("update_id", BigInteger, primary_key=True),
    Column("chat_id", BigInteger, nullable=False),
    Column("created_at", Float, nullable=False, index=True),
)
cache_entries = Table(
    "api_cache", metadata,
    Column("key", String(128), primary_key=True),
    Column("payload", JSON, nullable=True),
    Column("fetched_at", String(40), nullable=True),
    Column("expires_at", Float, nullable=False),
    Column("lease_owner", String(64), nullable=True),
    Column("lease_until", Float, nullable=False),
    Column("cooldown_until", Float, nullable=False),
    Column("error", String(512), nullable=True),
    Column("error_status", Integer, nullable=True),
)
rate_buckets = Table(
    "rate_buckets", metadata,
    Column("key", String(128), primary_key=True),
    Column("window", BigInteger, primary_key=True),
    Column("count", Integer, nullable=False),
)


class ConcurrentUpdate(Exception):
    pass


class Storage:
    def __init__(self, url, session_seconds=1800):
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        if not url.startswith(("sqlite:///", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL musi wskazywać PostgreSQL lub lokalny plik SQLite.")
        options = {"timeout": 10} if url.startswith("sqlite:") else {"connect_timeout": 3}
        self.engine = create_engine(url, connect_args=options, pool_pre_ping=True, hide_parameters=True)
        self.session_seconds = session_seconds
        self.insert = sqlite_insert if self.engine.dialect.name == "sqlite" else pg_insert

    def ready(self):
        with self.engine.connect() as conn:
            inspector = inspect(conn)
            if not all(inspector.has_table(table.name) for table in metadata.sorted_tables):
                return False
            conn.execute(select(bot_sessions.c.chat_id).limit(1))
            return True

    def process_update(self, update_id, chat_id, message_id, handler):
        """Compute without DB locks; commit state + deduplication atomically.

        A lost optimistic race recomputes the reply against current state. Nothing
        is acknowledged to Telegram before this transaction commits.
        """
        for _ in range(5):
            with self.engine.connect() as conn:
                if conn.execute(select(processed_updates.c.update_id).where(
                    processed_updates.c.update_id == update_id
                )).first():
                    return None
                row = conn.execute(select(bot_sessions).where(
                    bot_sessions.c.chat_id == chat_id
                )).mappings().first()
            version = row["version"] if row else None
            state = copy.deepcopy(row["state"]) if row else {"mode": None, "count": 1, "history": []}
            if row and time.time() - row["updated_at"] > self.session_seconds:
                state["mode"] = None
                state["count"] = 1
            outdated = bool(row and message_id <= row["message_id"])
            answer = None if outdated else handler(state)
            try:
                with self.engine.begin() as conn:
                    inserted = conn.execute(self.insert(processed_updates).values(
                        update_id=update_id, chat_id=chat_id, created_at=time.time(),
                    ).on_conflict_do_nothing()).rowcount
                    if not inserted:
                        return None
                    # Late delivery is acknowledged but never rewinds the conversation.
                    if outdated:
                        return None
                    values = dict(state=state, message_id=message_id, updated_at=time.time())
                    if version is None:
                        changed = conn.execute(self.insert(bot_sessions).values(
                            chat_id=chat_id, version=1, **values,
                        ).on_conflict_do_nothing()).rowcount
                    else:
                        changed = conn.execute(update(bot_sessions).where(
                            bot_sessions.c.chat_id == chat_id,
                            bot_sessions.c.version == version,
                        ).values(version=version + 1, **values)).rowcount
                    if not changed:
                        raise ConcurrentUpdate()
                return answer
            except ConcurrentUpdate:
                continue
        raise ConcurrentUpdate("Równoczesna zmiana rozmowy; ponów aktualizację.")

    def allow(self, key, limit, now=None):
        now = time.time() if now is None else now
        window = int(now // 60)
        with self.engine.begin() as conn:
            # Every read also prunes expired buckets, so distinct client keys expire.
            conn.execute(delete(rate_buckets).where(rate_buckets.c.window < window - 1))
            query = self.insert(rate_buckets).values(key=key, window=window, count=1)
            count = conn.execute(query.on_conflict_do_update(
                index_elements=[rate_buckets.c.key, rate_buckets.c.window],
                set_={"count": rate_buckets.c.count + 1},
            ).returning(rate_buckets.c.count)).scalar_one()
        return count <= limit

    def cached(self, key):
        with self.engine.connect() as conn:
            row = conn.execute(select(cache_entries).where(cache_entries.c.key == key)).mappings().first()
            return dict(row) if row else None

    def acquire(self, key, owner, seconds):
        now = time.time()
        with self.engine.begin() as conn:
            conn.execute(self.insert(cache_entries).values(
                key=key, expires_at=0, lease_until=0, cooldown_until=0,
            ).on_conflict_do_nothing())
            return bool(conn.execute(update(cache_entries).where(
                cache_entries.c.key == key,
                cache_entries.c.lease_until <= now,
                cache_entries.c.cooldown_until <= now,
            ).values(lease_owner=owner, lease_until=now + seconds)).rowcount)

    def cache_success(self, key, owner, payload, ttl):
        with self.engine.begin() as conn:
            conn.execute(update(cache_entries).where(
                cache_entries.c.key == key, cache_entries.c.lease_owner == owner,
            ).values(payload=payload, fetched_at=datetime.now(timezone.utc).isoformat(),
                     expires_at=time.time() + max(0, ttl), lease_until=0,
                     lease_owner=None, cooldown_until=0, error=None, error_status=None))

    def cache_failure(self, key, owner, error, status, retry_after):
        with self.engine.begin() as conn:
            conn.execute(update(cache_entries).where(
                cache_entries.c.key == key, cache_entries.c.lease_owner == owner,
            ).values(lease_until=0, lease_owner=None, cooldown_until=time.time() + retry_after,
                     error=str(error)[:512], error_status=status))

    def release(self, key, owner):
        with self.engine.begin() as conn:
            conn.execute(update(cache_entries).where(
                cache_entries.c.key == key, cache_entries.c.lease_owner == owner,
            ).values(lease_until=0, lease_owner=None))

    def cleanup(self):
        """Bound coordination records; conversation histories are not purged."""
        now = time.time()
        with self.engine.begin() as conn:
            old_updates = conn.execute(delete(processed_updates).where(
                processed_updates.c.created_at < now - 7 * 86400,
            )).rowcount
            conn.execute(delete(rate_buckets).where(rate_buckets.c.window < int(now // 60) - 1))
            old_cache = conn.execute(delete(cache_entries).where(
                cache_entries.c.expires_at < now - 86400,
                cache_entries.c.lease_until < now,
                cache_entries.c.cooldown_until < now,
            )).rowcount
        return {"updates": old_updates, "cache": old_cache}
