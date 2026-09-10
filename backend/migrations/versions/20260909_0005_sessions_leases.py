"""Create signer sessions, bounded leases, and authorization receipts."""

# ruff: noqa: E501 -- SQL invariants remain auditable as complete expressions.

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0005"
down_revision: str | None = "20260909_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_credentials_scope_lineage",
        "credentials",
        ["id", "tenant_id", "account_id", "principal_id"],
    )
    op.create_table(
        "global_exposure",
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("exposure_cap", sa.Numeric(78, 0), nullable=False),
        sa.Column("open_exposure", sa.Numeric(78, 0), nullable=False, server_default="0"),
        sa.Column("configured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=False, server_default="initial state"),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "singleton AND exposure_cap >= 0 AND open_exposure >= 0 AND open_exposure <= exposure_cap",
            name="global_exposure_bounds",
        ),
    )
    op.execute("INSERT INTO global_exposure(singleton,exposure_cap) VALUES (true,0)")
    op.create_table(
        "signer_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("credential_id", sa.Text(), nullable=True),
        sa.Column("auth_source", sa.Text(), nullable=False),
        sa.Column("signer_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("app", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "replaced_from_id", sa.Text(), sa.ForeignKey("signer_sessions.id"), nullable=True
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','expired','revoked')", name="signer_session_status"
        ),
        sa.CheckConstraint(
            "(auth_source='browser' AND credential_id IS NULL) OR "
            "(auth_source='credential' AND credential_id IS NOT NULL)",
            name="signer_session_auth_source",
        ),
        sa.CheckConstraint("length(signer_id) BETWEEN 8 AND 128", name="signer_session_signer_id"),
        sa.CheckConstraint(
            "length(capability) BETWEEN 1 AND 128", name="signer_session_capability"
        ),
        sa.CheckConstraint(
            "model IS NULL OR length(model) BETWEEN 1 AND 512", name="signer_session_model"
        ),
        sa.CheckConstraint("length(app) BETWEEN 1 AND 256", name="signer_session_app"),
        sa.UniqueConstraint("signer_id", "id", name="uq_signer_sessions_signer_id"),
        sa.UniqueConstraint("principal_id", "operation_key", name="uq_signer_session_operation"),
        sa.CheckConstraint("length(operation_key) BETWEEN 16 AND 256", name="signer_operation_key"),
        sa.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="signer_request_hash"),
        sa.UniqueConstraint(
            "id", "tenant_id", "account_id", "principal_id", name="uq_signer_sessions_scope"
        ),
        sa.ForeignKeyConstraint(
            ["principal_id", "tenant_id", "account_id"],
            ["principals.id", "principals.tenant_id", "principals.account_id"],
        ),
        sa.ForeignKeyConstraint(
            ["credential_id", "tenant_id", "account_id", "principal_id"],
            [
                "credentials.id",
                "credentials.tenant_id",
                "credentials.account_id",
                "credentials.principal_id",
            ],
            name="fk_signer_session_credential_lineage",
        ),
    )
    op.create_table(
        "leases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("signer_sessions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("rate_card_id", sa.Text(), sa.ForeignKey("rate_cards.id"), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("app", sa.Text(), nullable=False),
        sa.Column("rate_numerator", sa.Numeric(78, 0), nullable=False),
        sa.Column("rate_denominator", sa.Numeric(78, 0), nullable=False),
        sa.Column("charge_unit", sa.Text(), nullable=False),
        sa.Column("quantity_unit", sa.Text(), nullable=False),
        sa.Column("policy_snapshot", sa.String(64), nullable=False),
        sa.Column("policy_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("cap", sa.Numeric(78, 0), nullable=False),
        sa.Column("available", sa.Numeric(78, 0), nullable=False),
        sa.Column("pending", sa.Numeric(78, 0), nullable=False, server_default="0"),
        sa.Column("settled", sa.Numeric(78, 0), nullable=False, server_default="0"),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cap >= 0 AND available >= 0 AND pending >= 0 AND settled >= 0 AND available + pending + settled <= cap",
            name="lease_value_bounds",
        ),
        sa.CheckConstraint("unit='wei'", name="lease_wei_only"),
        sa.CheckConstraint(
            "rate_numerator > 0 AND rate_denominator > 0", name="lease_positive_rate"
        ),
        sa.CheckConstraint("charge_unit='wei'", name="lease_wei_charge"),
        sa.CheckConstraint("length(capability) BETWEEN 1 AND 128", name="lease_capability_length"),
        sa.CheckConstraint(
            "model IS NULL OR length(model) BETWEEN 1 AND 512", name="lease_model_length"
        ),
        sa.CheckConstraint("length(app) BETWEEN 1 AND 256", name="lease_app_length"),
        sa.CheckConstraint(
            "length(quantity_unit) BETWEEN 1 AND 32", name="lease_quantity_unit_length"
        ),
        sa.CheckConstraint("policy_snapshot ~ '^[0-9a-f]{64}$'", name="lease_policy_hash"),
        sa.ForeignKeyConstraint(["account_id", "tenant_id"], ["accounts.id", "accounts.tenant_id"]),
        sa.ForeignKeyConstraint(
            ["principal_id", "tenant_id", "account_id"],
            ["principals.id", "principals.tenant_id", "principals.account_id"],
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id", "account_id", "principal_id"],
            [
                "signer_sessions.id",
                "signer_sessions.tenant_id",
                "signer_sessions.account_id",
                "signer_sessions.principal_id",
            ],
            name="fk_lease_session_scope",
        ),
        sa.UniqueConstraint(
            "id", "session_id", "tenant_id", "account_id", "principal_id", name="uq_leases_lineage"
        ),
    )
    op.create_table(
        "signer_state_heads",
        sa.Column("signer_id", sa.Text(), primary_key=True),
        sa.Column("state_id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("signer_sessions.id"), nullable=False),
        sa.Column("last_sequence", sa.Numeric(20, 0), nullable=False),
        sa.Column("last_update_ns", sa.Numeric(30, 0), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("quarantined", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("last_sequence >= 0", name="signer_state_sequence"),
        sa.CheckConstraint("last_sequence <= 18446744073709551615", name="signer_state_uint64"),
        sa.CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="signer_state_hash"),
        sa.ForeignKeyConstraint(
            ["signer_id", "session_id"],
            ["signer_sessions.signer_id", "signer_sessions.id"],
            name="fk_signer_state_session",
        ),
    )
    op.create_table(
        "authorization_receipts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("signer_id", sa.Text(), nullable=False),
        sa.Column("state_id", sa.Text(), nullable=False),
        sa.Column("sequence_number", sa.Numeric(20, 0), nullable=False),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("signer_sessions.id"), nullable=False),
        sa.Column("lease_id", sa.Text(), sa.ForeignKey("leases.id"), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("reserved_amount", sa.Numeric(78, 0), nullable=False),
        sa.Column("quantity", sa.Numeric(78, 0), nullable=False),
        sa.Column("quantity_unit", sa.Text(), nullable=False),
        sa.Column("pm_session_id", sa.Text(), nullable=False),
        sa.Column("app", sa.Text(), nullable=False),
        sa.Column("orchestrator_address", sa.Text(), nullable=False),
        sa.Column("last_update_ns", sa.Numeric(30, 0), nullable=False),
        sa.Column("payment_type", sa.Text(), nullable=False),
        sa.Column("rate_card_id", sa.Text(), sa.ForeignKey("rate_cards.id"), nullable=False),
        sa.Column("rate_numerator", sa.Numeric(78, 0), nullable=False),
        sa.Column("rate_denominator", sa.Numeric(78, 0), nullable=False),
        sa.Column("charge_unit", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("signer_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "signer_id", "state_id", "sequence_number", name="uq_authorization_reservation"
        ),
        sa.CheckConstraint("reserved_amount >= 0 AND quantity > 0", name="authorization_positive"),
        sa.CheckConstraint(
            "rate_numerator > 0 AND rate_denominator > 0", name="authorization_rate"
        ),
        sa.CheckConstraint("charge_unit='wei'", name="authorization_wei_only"),
        sa.CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="authorization_state_hash"),
        sa.CheckConstraint(
            "orchestrator_address ~ '^0x[0-9a-f]{40}$'", name="authorization_address"
        ),
        sa.CheckConstraint(
            "sequence_number BETWEEN 0 AND 18446744073709551615",
            name="authorization_uint64_sequence",
        ),
        sa.CheckConstraint(
            "status IN ('pending','unresolved','settled','quarantined')",
            name="authorization_status",
        ),
        sa.ForeignKeyConstraint(
            ["lease_id", "session_id", "tenant_id", "account_id", "principal_id"],
            [
                "leases.id",
                "leases.session_id",
                "leases.tenant_id",
                "leases.account_id",
                "leases.principal_id",
            ],
            name="fk_authorization_lease_lineage",
        ),
    )
    for table in ("signer_sessions", "leases", "authorization_receipts"):
        op.create_index(f"ix_{table}_account", table, ["account_id"])
    op.create_index(
        "ix_authorization_receipts_lease_status",
        "authorization_receipts",
        ["lease_id", "status"],
    )
    op.execute("""
      CREATE FUNCTION enforce_exposure_projection() RETURNS trigger AS $$
      BEGIN
        IF (SELECT open_exposure FROM global_exposure WHERE singleton) <>
           COALESCE((SELECT sum(available+pending) FROM leases),0) THEN
          RAISE EXCEPTION 'global lease exposure projection drift';
        END IF;
        IF EXISTS (SELECT 1 FROM account_exposures e WHERE e.open_lease_exposure <>
          COALESCE((SELECT sum(l.available+l.pending) FROM leases l
                    WHERE l.account_id=e.account_id),0)) THEN
          RAISE EXCEPTION 'account lease exposure projection drift';
        END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    for table in ("leases", "account_exposures", "global_exposure"):
        op.execute(f"""CREATE CONSTRAINT TRIGGER {table}_exposure_reconciled
          AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_exposure_projection()""")
    op.execute("""
      CREATE FUNCTION enforce_account_exposure_funding() RETURNS trigger AS $$
      BEGIN
        IF EXISTS (
          SELECT 1 FROM account_exposures e JOIN accounts a ON a.id=e.account_id
          WHERE e.unit<>a.unit OR e.open_lease_exposure>a.exposure_cap OR
            e.open_lease_exposure>COALESCE((
              SELECT sum(p.amount) FROM ledger_postings p
              WHERE p.tenant_id=a.tenant_id AND p.account_code='payer:'||a.id
                AND p.unit=a.unit
            ),0)
        ) THEN RAISE EXCEPTION 'account lease exposure exceeds cap or posted funding'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    for table in ("accounts", "account_exposures", "ledger_postings"):
        op.execute(f"""CREATE CONSTRAINT TRIGGER {table}_exposure_funded
          AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_account_exposure_funding()""")
    op.execute("""
      CREATE FUNCTION protect_authorization_receipt() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'authorization receipt facts are immutable';
        END IF;
        IF OLD.id IS DISTINCT FROM NEW.id OR
           OLD.signer_id IS DISTINCT FROM NEW.signer_id OR
           OLD.state_id IS DISTINCT FROM NEW.state_id OR
           OLD.sequence_number IS DISTINCT FROM NEW.sequence_number OR
           OLD.session_id IS DISTINCT FROM NEW.session_id OR
           OLD.lease_id IS DISTINCT FROM NEW.lease_id OR
           OLD.tenant_id IS DISTINCT FROM NEW.tenant_id OR
           OLD.account_id IS DISTINCT FROM NEW.account_id OR
           OLD.principal_id IS DISTINCT FROM NEW.principal_id OR
           OLD.reserved_amount IS DISTINCT FROM NEW.reserved_amount OR
           OLD.quantity IS DISTINCT FROM NEW.quantity OR
           OLD.quantity_unit IS DISTINCT FROM NEW.quantity_unit OR
           OLD.pm_session_id IS DISTINCT FROM NEW.pm_session_id OR
           OLD.app IS DISTINCT FROM NEW.app OR
           OLD.orchestrator_address IS DISTINCT FROM NEW.orchestrator_address OR
           OLD.last_update_ns IS DISTINCT FROM NEW.last_update_ns OR
           OLD.payment_type IS DISTINCT FROM NEW.payment_type OR
           OLD.rate_card_id IS DISTINCT FROM NEW.rate_card_id OR
           OLD.rate_numerator IS DISTINCT FROM NEW.rate_numerator OR
           OLD.rate_denominator IS DISTINCT FROM NEW.rate_denominator OR
           OLD.charge_unit IS DISTINCT FROM NEW.charge_unit OR
           OLD.state_hash IS DISTINCT FROM NEW.state_hash OR
           OLD.created_at IS DISTINCT FROM NEW.created_at OR
           (OLD.status IS DISTINCT FROM NEW.status AND NOT (
             OLD.status='pending' AND NEW.status='quarantined'
           )) OR
           (OLD.signer_confirmed_at IS NOT NULL AND
             OLD.signer_confirmed_at IS DISTINCT FROM NEW.signer_confirmed_at) OR
           NEW.signer_confirmed_at < NEW.created_at THEN
          RAISE EXCEPTION 'authorization receipt facts are immutable';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER authorization_receipts_protected
      BEFORE UPDATE OR DELETE ON authorization_receipts FOR EACH ROW
      EXECUTE FUNCTION protect_authorization_receipt()""")
    op.execute("""
      CREATE FUNCTION protect_signer_session() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'signer sessions cannot be deleted'; END IF;
        IF OLD.id IS DISTINCT FROM NEW.id OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id OR
           OLD.account_id IS DISTINCT FROM NEW.account_id OR
           OLD.principal_id IS DISTINCT FROM NEW.principal_id OR
           OLD.credential_id IS DISTINCT FROM NEW.credential_id OR
           OLD.auth_source IS DISTINCT FROM NEW.auth_source OR
           OLD.signer_id IS DISTINCT FROM NEW.signer_id OR
           OLD.token_hash IS DISTINCT FROM NEW.token_hash OR
           OLD.operation_key IS DISTINCT FROM NEW.operation_key OR
           OLD.request_hash IS DISTINCT FROM NEW.request_hash OR
           OLD.capability IS DISTINCT FROM NEW.capability OR
           OLD.model IS DISTINCT FROM NEW.model OR
           OLD.app IS DISTINCT FROM NEW.app OR
           OLD.replaced_from_id IS DISTINCT FROM NEW.replaced_from_id OR
           OLD.expires_at IS DISTINCT FROM NEW.expires_at OR
           OLD.created_at IS DISTINCT FROM NEW.created_at OR
           (OLD.status IS DISTINCT FROM NEW.status AND NOT (
             OLD.status='active' AND NEW.status IN ('expired','revoked')
           )) THEN
          RAISE EXCEPTION 'signer session facts are immutable';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER signer_sessions_protected BEFORE UPDATE OR DELETE ON signer_sessions
      FOR EACH ROW EXECUTE FUNCTION protect_signer_session()""")
    op.execute("""
      CREATE FUNCTION protect_lease() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'leases cannot be deleted'; END IF;
        IF OLD.id IS DISTINCT FROM NEW.id OR OLD.session_id IS DISTINCT FROM NEW.session_id OR
           OLD.tenant_id IS DISTINCT FROM NEW.tenant_id OR
           OLD.account_id IS DISTINCT FROM NEW.account_id OR
           OLD.principal_id IS DISTINCT FROM NEW.principal_id OR
           OLD.rate_card_id IS DISTINCT FROM NEW.rate_card_id OR
           OLD.capability IS DISTINCT FROM NEW.capability OR OLD.model IS DISTINCT FROM NEW.model OR
           OLD.app IS DISTINCT FROM NEW.app OR
           OLD.rate_numerator IS DISTINCT FROM NEW.rate_numerator OR
           OLD.rate_denominator IS DISTINCT FROM NEW.rate_denominator OR
           OLD.charge_unit IS DISTINCT FROM NEW.charge_unit OR
           OLD.quantity_unit IS DISTINCT FROM NEW.quantity_unit OR
           OLD.policy_snapshot IS DISTINCT FROM NEW.policy_snapshot OR
           OLD.policy_snapshot_json::text IS DISTINCT FROM NEW.policy_snapshot_json::text OR
           OLD.cap IS DISTINCT FROM NEW.cap OR OLD.unit IS DISTINCT FROM NEW.unit OR
           OLD.expires_at IS DISTINCT FROM NEW.expires_at OR
           OLD.created_at IS DISTINCT FROM NEW.created_at OR
           NEW.available > OLD.available OR
           NEW.pending < OLD.pending OR NEW.settled IS DISTINCT FROM OLD.settled OR
           NEW.available + NEW.pending > OLD.available + OLD.pending THEN
          RAISE EXCEPTION 'lease facts and held exposure are immutable';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER leases_protected BEFORE UPDATE OR DELETE ON leases
      FOR EACH ROW EXECUTE FUNCTION protect_lease()""")
    op.execute("""
      CREATE FUNCTION enforce_lease_snapshot() RETURNS trigger AS $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM signer_sessions s JOIN rate_cards r ON r.id=NEW.rate_card_id
          WHERE s.id=NEW.session_id AND s.tenant_id=NEW.tenant_id
            AND s.account_id=NEW.account_id AND s.principal_id=NEW.principal_id
            AND s.capability=NEW.capability AND s.model IS NOT DISTINCT FROM NEW.model
            AND s.app=NEW.app AND s.expires_at=NEW.expires_at
            AND r.capability=NEW.capability AND r.model IS NOT DISTINCT FROM NEW.model
            AND r.numerator=NEW.rate_numerator AND r.denominator=NEW.rate_denominator
            AND r.charge_unit=NEW.charge_unit AND r.quantity_unit=NEW.quantity_unit
        ) THEN RAISE EXCEPTION 'lease snapshot does not match session and rate card'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE CONSTRAINT TRIGGER lease_snapshot_consistent
      AFTER INSERT OR UPDATE ON leases DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION enforce_lease_snapshot()""")
    op.execute("""
      CREATE FUNCTION enforce_receipt_snapshot() RETURNS trigger AS $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM signer_sessions s JOIN leases l ON l.id=NEW.lease_id
          WHERE s.id=NEW.session_id AND s.signer_id=NEW.signer_id
            AND l.session_id=s.id AND l.app=NEW.app AND l.rate_card_id=NEW.rate_card_id
            AND l.rate_numerator=NEW.rate_numerator
            AND l.rate_denominator=NEW.rate_denominator AND l.charge_unit=NEW.charge_unit
            AND l.quantity_unit=NEW.quantity_unit AND l.tenant_id=NEW.tenant_id
            AND l.account_id=NEW.account_id AND l.principal_id=NEW.principal_id
        ) THEN RAISE EXCEPTION 'authorization receipt does not match session and lease'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE CONSTRAINT TRIGGER receipt_snapshot_consistent
      AFTER INSERT OR UPDATE ON authorization_receipts DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION enforce_receipt_snapshot()""")
    op.execute("""
      CREATE FUNCTION enforce_authorization_reconciliation() RETURNS trigger AS $$
      DECLARE checked_lease text;
      BEGIN
        IF TG_TABLE_NAME='authorization_receipts' THEN
          checked_lease := COALESCE(NEW.lease_id,OLD.lease_id);
          IF TG_OP<>'DELETE' AND
             NEW.reserved_amount<>ceil(NEW.quantity*NEW.rate_numerator/NEW.rate_denominator)
          THEN RAISE EXCEPTION 'authorization reservation amount mismatch'; END IF;
        ELSIF TG_TABLE_NAME='leases' THEN
          checked_lease := COALESCE(NEW.id,OLD.id);
        ELSE
          IF TG_OP<>'DELETE' AND NOT EXISTS (
            SELECT 1 FROM authorization_receipts r WHERE r.signer_id=NEW.signer_id
              AND r.state_id=NEW.state_id AND r.sequence_number=NEW.last_sequence
              AND r.session_id=NEW.session_id AND r.state_hash=NEW.state_hash
          ) THEN RAISE EXCEPTION 'signer state head has no matching receipt'; END IF;
        END IF;
        IF checked_lease IS NOT NULL AND EXISTS (
          SELECT 1 FROM leases l WHERE l.id=checked_lease AND l.pending<>COALESCE((
            SELECT sum(r.reserved_amount) FROM authorization_receipts r
            WHERE r.lease_id=checked_lease
              AND r.status IN ('pending','unresolved','quarantined')
          ),0)
        ) THEN RAISE EXCEPTION 'lease pending reservation reconciliation failed'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    for table in ("leases", "authorization_receipts", "signer_state_heads"):
        op.execute(f"""CREATE CONSTRAINT TRIGGER {table}_authorization_reconciled
          AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_authorization_reconciliation()""")
    op.execute("""
      CREATE FUNCTION protect_signer_state_head() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'signer state heads cannot be deleted'; END IF;
        IF OLD.signer_id IS DISTINCT FROM NEW.signer_id OR
           OLD.state_id IS DISTINCT FROM NEW.state_id OR
           OLD.session_id IS DISTINCT FROM NEW.session_id OR
           (NEW.last_sequence <> OLD.last_sequence + 1 AND NOT (
             OLD.quarantined=false AND NEW.quarantined=true AND
             NEW.last_sequence=OLD.last_sequence AND
             NEW.last_update_ns=OLD.last_update_ns AND NEW.state_hash=OLD.state_hash
           )) OR (OLD.quarantined AND NOT NEW.quarantined) THEN
          RAISE EXCEPTION 'signer state must advance monotonically';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER signer_state_heads_protected BEFORE UPDATE OR DELETE ON signer_state_heads
      FOR EACH ROW EXECUTE FUNCTION protect_signer_state_head()""")


def downgrade() -> None:
    for table in ("leases", "authorization_receipts", "signer_state_heads"):
        op.execute(f"DROP TRIGGER {table}_authorization_reconciled ON {table}")
    op.execute("DROP FUNCTION enforce_authorization_reconciliation()")
    op.execute("DROP TRIGGER signer_state_heads_protected ON signer_state_heads")
    op.execute("DROP FUNCTION protect_signer_state_head()")
    op.execute("DROP TRIGGER receipt_snapshot_consistent ON authorization_receipts")
    op.execute("DROP FUNCTION enforce_receipt_snapshot()")
    op.execute("DROP TRIGGER leases_protected ON leases")
    op.execute("DROP FUNCTION protect_lease()")
    op.execute("DROP TRIGGER lease_snapshot_consistent ON leases")
    op.execute("DROP FUNCTION enforce_lease_snapshot()")
    op.execute("DROP TRIGGER signer_sessions_protected ON signer_sessions")
    op.execute("DROP FUNCTION protect_signer_session()")
    for table in ("accounts", "account_exposures", "ledger_postings"):
        op.execute(f"DROP TRIGGER {table}_exposure_funded ON {table}")
    op.execute("DROP FUNCTION enforce_account_exposure_funding()")
    op.execute("DROP TRIGGER authorization_receipts_protected ON authorization_receipts")
    op.execute("DROP FUNCTION protect_authorization_receipt()")
    for table in ("leases", "account_exposures", "global_exposure"):
        op.execute(f"DROP TRIGGER {table}_exposure_reconciled ON {table}")
    op.execute("DROP FUNCTION enforce_exposure_projection()")
    for table in ("authorization_receipts", "leases", "signer_sessions"):
        op.drop_index(f"ix_{table}_account", table_name=table)
    op.drop_table("authorization_receipts")
    op.drop_table("signer_state_heads")
    op.drop_table("leases")
    op.drop_table("signer_sessions")
    op.drop_table("global_exposure")
    op.drop_constraint("uq_credentials_scope_lineage", "credentials", type_="unique")
