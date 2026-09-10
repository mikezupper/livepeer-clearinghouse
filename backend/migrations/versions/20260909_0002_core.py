"""Create identity, account, credential, catalog, audit, and ledger core."""

# ruff: noqa: E501 -- constraint declarations are clearer kept as single expressions.

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0002"
down_revision: str | None = "20260909_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

status = sa.Enum("active", "suspended", name="resource_status")
principal_role = sa.Enum("operator", "tenant_admin", "credential_holder", name="principal_role")
credential_status = sa.Enum("active", "revoked", "expired", name="credential_status")
grant_kind = sa.Enum("credit", "debit", "adjustment", name="grant_kind")


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", status, nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(display_name) BETWEEN 1 AND 200", name="display_name_length"),
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("exposure_cap", sa.Numeric(78, 0), nullable=False),
        sa.Column("status", status, nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("exposure_cap >= 0", name="exposure_cap_nonnegative"),
        sa.CheckConstraint("unit ~ '^[a-z][a-z0-9_]{1,31}$'", name="unit_format"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_accounts_id_tenant"),
    )
    op.create_index("ix_accounts_tenant_id", "accounts", ["tenant_id"])
    op.create_table(
        "principals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("status", status, nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "tenant_id IS NOT NULL OR account_id IS NULL", name="operator_has_no_account"
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["accounts.id", "accounts.tenant_id"],
            name="fk_principals_account_tenant",
        ),
        sa.UniqueConstraint(
            "id", "tenant_id", "account_id", name="uq_principals_id_tenant_account"
        ),
    )
    op.create_index("ix_principals_tenant_id", "principals", ["tenant_id"])
    op.create_table(
        "principal_roles",
        sa.Column(
            "principal_id",
            sa.Text(),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", principal_role, primary_key=True),
    )
    op.execute("""
        CREATE FUNCTION enforce_principal_role_scope() RETURNS trigger AS $$
        DECLARE principal principals%ROWTYPE;
        BEGIN
          SELECT * INTO principal FROM principals
          WHERE id = COALESCE(to_jsonb(NEW)->>'principal_id', to_jsonb(NEW)->>'id');
          IF EXISTS (SELECT 1 FROM principal_roles WHERE principal_id=principal.id AND role='operator')
             AND (principal.tenant_id IS NOT NULL OR principal.account_id IS NOT NULL) THEN
            RAISE EXCEPTION 'operator principal must be unscoped';
          END IF;
          IF EXISTS (SELECT 1 FROM principal_roles WHERE principal_id=principal.id AND role='tenant_admin')
             AND principal.tenant_id IS NULL THEN
            RAISE EXCEPTION 'tenant administrator requires tenant scope';
          END IF;
          IF EXISTS (SELECT 1 FROM principal_roles WHERE principal_id=principal.id AND role='credential_holder')
             AND principal.account_id IS NULL THEN
            RAISE EXCEPTION 'credential holder requires account scope';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER principal_role_scope_on_role
        AFTER INSERT OR UPDATE ON principal_roles DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_principal_role_scope();
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER principal_role_scope_on_principal
        AFTER UPDATE ON principals DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_principal_role_scope();
    """)
    op.create_table(
        "credentials",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("principal_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False, unique=True),
        sa.Column("secret_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("status", credential_status, nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_from_id", sa.Text(), sa.ForeignKey("credentials.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(label) BETWEEN 1 AND 200", name="label_length"),
        sa.ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["accounts.id", "accounts.tenant_id"],
            name="fk_credentials_account_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id", "tenant_id", "account_id"],
            ["principals.id", "principals.tenant_id", "principals.account_id"],
            name="fk_credentials_principal_scope",
        ),
    )
    op.create_index("ix_credentials_tenant_id", "credentials", ["tenant_id"])
    op.create_index("ix_credentials_principal_id", "credentials", ["principal_id"])
    op.create_table(
        "rate_cards",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("numerator", sa.Numeric(78, 0), nullable=False),
        sa.Column("denominator", sa.Numeric(78, 0), nullable=False),
        sa.Column("charge_unit", sa.Text(), nullable=False),
        sa.Column("quantity_unit", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("numerator >= 0 AND denominator > 0", name="exact_rate"),
    )
    op.create_index(
        "uq_rate_cards_capability_model_version",
        "rate_cards",
        ["capability", "model", "version"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "account_capability_policies",
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("capability", sa.Text(), primary_key=True),
        sa.Column("model", sa.Text(), primary_key=True, server_default=""),
        sa.Column("allowed", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "account_exposures",
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("open_lease_exposure", sa.Numeric(78, 0), nullable=False, server_default="0"),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.CheckConstraint("open_lease_exposure >= 0", name="open_exposure_nonnegative"),
        sa.ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["accounts.id", "accounts.tenant_id"],
            name="fk_account_exposures_account_tenant",
        ),
    )
    op.create_table(
        "grants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("kind", grant_kind, nullable=False),
        sa.Column("amount", sa.Numeric(78, 0), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("external_reference", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_grants_actor_idempotency_key"),
        sa.CheckConstraint(
            "(kind = 'credit' AND amount > 0) OR (kind = 'debit' AND amount > 0) "
            "OR (kind = 'adjustment' AND amount <> 0)",
            name="kind_amount_sign",
        ),
    )
    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False, unique=True),
        sa.Column("actor_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_ledger_transactions_id_tenant"),
    )
    op.create_table(
        "ledger_postings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "transaction_id", sa.Text(), sa.ForeignKey("ledger_transactions.id"), nullable=False
        ),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_code", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(78, 0), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("amount <> 0", name="amount_nonzero"),
        sa.ForeignKeyConstraint(
            ["transaction_id", "tenant_id"],
            ["ledger_transactions.id", "ledger_transactions.tenant_id"],
            name="fk_ledger_postings_transaction_tenant",
        ),
    )
    op.create_index("ix_ledger_postings_transaction_id", "ledger_postings", ["transaction_id"])
    op.execute("""
        CREATE FUNCTION enforce_balanced_ledger_transaction() RETURNS trigger AS $$
        DECLARE tx text := COALESCE(NEW.transaction_id, OLD.transaction_id);
        BEGIN
          IF (SELECT count(*) FROM ledger_postings WHERE transaction_id = tx) < 2 THEN
            RAISE EXCEPTION 'ledger transaction % requires at least two postings', tx;
          END IF;
          IF EXISTS (
            SELECT 1 FROM ledger_postings WHERE transaction_id = tx
            GROUP BY unit HAVING sum(amount) <> 0
          ) THEN
            RAISE EXCEPTION 'ledger transaction % is not balanced per unit', tx;
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER ledger_transaction_balanced
        AFTER INSERT OR UPDATE OR DELETE ON ledger_postings
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION enforce_balanced_ledger_transaction();
    """)
    op.execute("""
        CREATE FUNCTION enforce_ledger_transaction_has_postings() RETURNS trigger AS $$
        BEGIN
          IF (SELECT count(*) FROM ledger_postings WHERE transaction_id = NEW.id) < 2 THEN
            RAISE EXCEPTION 'ledger transaction % requires at least two postings', NEW.id;
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER ledger_transaction_has_postings
        AFTER INSERT ON ledger_transactions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION enforce_ledger_transaction_has_postings();
    """)
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_scope", sa.Text(), primary_key=True),
        sa.Column("operation", sa.Text(), primary_key=True),
        sa.Column("idempotency_key", sa.Text(), primary_key=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])
    op.execute("""
        CREATE FUNCTION reject_fact_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER grants_append_only BEFORE UPDATE OR DELETE ON grants
        FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()
    """)
    op.execute("""
        CREATE TRIGGER ledger_transactions_append_only BEFORE UPDATE OR DELETE ON ledger_transactions
        FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()
    """)
    op.execute("""
        CREATE TRIGGER ledger_postings_append_only BEFORE UPDATE OR DELETE ON ledger_postings
        FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()
    """)
    op.execute("""
        CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()
    """)
    op.execute("""
        CREATE TRIGGER rate_cards_append_only BEFORE UPDATE OR DELETE ON rate_cards
        FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION enforce_balanced_ledger_transaction() CASCADE")
    op.execute("DROP FUNCTION enforce_ledger_transaction_has_postings() CASCADE")
    op.execute("DROP FUNCTION reject_fact_mutation() CASCADE")
    op.execute("DROP FUNCTION enforce_principal_role_scope() CASCADE")
    for table in (
        "audit_events",
        "idempotency_keys",
        "ledger_postings",
        "ledger_transactions",
        "grants",
        "account_exposures",
        "account_capability_policies",
        "rate_cards",
        "credentials",
        "principal_roles",
        "principals",
        "accounts",
        "tenants",
    ):
        op.drop_table(table)
    for enum in (grant_kind, credential_status, principal_role, status):
        enum.drop(op.get_bind(), checkfirst=True)
