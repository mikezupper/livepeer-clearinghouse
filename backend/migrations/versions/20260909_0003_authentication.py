"""Create authentication identities, challenges, transactions, and sessions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0003"
down_revision: str | None = "20260909_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create security-sensitive authentication state."""
    op.create_table(
        "auth_identities",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "principal_id",
            sa.Text(),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider IN ('email', 'google', 'github')", name="auth_identity_provider"
        ),
        sa.UniqueConstraint("provider", "provider_subject"),
    )
    op.create_index("ix_auth_identities_principal_id", "auth_identities", ["principal_id"])
    op.create_table(
        "auth_email_challenges",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("request_ip_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="auth_challenge_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="auth_challenge_max_attempts_positive"),
    )
    op.create_index(
        "ix_auth_email_challenges_email_hash_created_at",
        "auth_email_challenges",
        ["email_hash", "created_at"],
    )
    op.create_table(
        "auth_rate_limits",
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("window_epoch", sa.BigInteger(), primary_key=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("request_count > 0", name="auth_rate_limit_count_positive"),
    )
    op.create_table(
        "auth_oauth_transactions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("pkce_verifier", sa.Text(), nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=True),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider IN ('google', 'github')", name="auth_oauth_provider"),
    )
    op.create_table(
        "auth_browser_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "principal_id",
            sa.Text(),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("client_ip_hash", sa.String(64), nullable=False),
        sa.Column("user_agent_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_auth_browser_sessions_principal_id", "auth_browser_sessions", ["principal_id"]
    )


def downgrade() -> None:
    """Remove authentication state in reverse dependency order."""
    op.drop_index("ix_auth_browser_sessions_principal_id", table_name="auth_browser_sessions")
    op.drop_table("auth_browser_sessions")
    op.drop_table("auth_oauth_transactions")
    op.drop_table("auth_rate_limits")
    op.drop_index(
        "ix_auth_email_challenges_email_hash_created_at", table_name="auth_email_challenges"
    )
    op.drop_table("auth_email_challenges")
    op.drop_index("ix_auth_identities_principal_id", table_name="auth_identities")
    op.drop_table("auth_identities")
