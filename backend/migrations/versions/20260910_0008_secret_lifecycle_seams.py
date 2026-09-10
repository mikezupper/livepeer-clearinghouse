# ruff: noqa: S608

"""Add key identity and safe secret-purge evidence seams.

Revision ID: 20260910_0008
Revises: 20260910_0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0008"
down_revision: str | None = "20260910_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SECRET_TABLES = (
    "credentials",
    "auth_email_challenges",
    "auth_oauth_transactions",
    "auth_browser_sessions",
    "auth_identity_invitations",
    "signer_sessions",
)


def upgrade() -> None:
    """Tag keyed secrets and add append-only purge evidence."""
    for table in SECRET_TABLES:
        op.add_column(
            table,
            sa.Column("key_id", sa.Text(), nullable=False, server_default="legacy"),
        )
        op.create_check_constraint(
            f"{table}_key_id_bounds", table, "length(key_id) BETWEEN 1 AND 128"
        )
        op.create_index(f"ix_{table}_key_id", table, ["key_id"])
    op.create_table(
        "secret_purge_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("operations_jobs.id"), nullable=False),
        sa.Column("target_table", sa.Text(), nullable=False),
        sa.Column("target_id_sha256", sa.String(64), nullable=False),
        sa.Column("former_key_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "job_id", "target_table", "target_id_sha256", name="uq_secret_purge_target"
        ),
        sa.CheckConstraint(
            "target_table IN ('auth_email_challenges','auth_oauth_transactions',"
            "'auth_browser_sessions')",
            name="secret_purge_supported_table",
        ),
        sa.CheckConstraint("target_id_sha256 ~ '^[0-9a-f]{64}$'", name="secret_purge_target_hash"),
        sa.CheckConstraint("length(former_key_id) BETWEEN 1 AND 128", name="secret_purge_key_id"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1000", name="secret_purge_reason"),
    )
    op.execute("""CREATE TRIGGER secret_purge_events_immutable BEFORE UPDATE OR DELETE
      ON secret_purge_events FOR EACH ROW EXECUTE FUNCTION reject_operational_fact_mutation()""")


def downgrade() -> None:
    """Refuse to erase purge or non-legacy key lineage."""
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM secret_purge_events) THEN
          RAISE EXCEPTION 'secret lifecycle downgrade requires empty purge evidence';
        END IF;
      END $$
    """)
    for table in SECRET_TABLES:
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table} WHERE key_id<>'legacy') "
            "THEN RAISE EXCEPTION 'secret lifecycle downgrade requires legacy key ids'; "
            "END IF; END $$"
        )
    op.drop_table("secret_purge_events")
    for table in reversed(SECRET_TABLES):
        op.drop_index(f"ix_{table}_key_id", table_name=table)
        op.drop_constraint(f"{table}_key_id_bounds", table, type_="check")
        op.drop_column(table, "key_id")
