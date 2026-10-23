"""Durable conversations, idempotency, cache and rate budgets."""
from alembic import op
import sqlalchemy as sa

revision = "0001_foundations"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("bot_sessions",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False))
    op.create_table("processed_updates",
        sa.Column("update_id", sa.BigInteger(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False))
    op.create_index("ix_processed_updates_created_at", "processed_updates", ["created_at"])
    op.create_table("api_cache",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.String(40), nullable=True),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.Float(), nullable=False),
        sa.Column("cooldown_until", sa.Float(), nullable=False),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("error_status", sa.Integer(), nullable=True))
    op.create_table("rate_buckets",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("window", sa.BigInteger(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("rate_buckets")
    op.drop_table("api_cache")
    op.drop_index("ix_processed_updates_created_at", table_name="processed_updates")
    op.drop_table("processed_updates")
    op.drop_table("bot_sessions")
