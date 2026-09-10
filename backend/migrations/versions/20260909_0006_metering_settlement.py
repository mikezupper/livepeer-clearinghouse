# ruff: noqa: E501

"""Durable Kafka metering, settlement, and reconciliation.

Revision ID: 20260909_0006
Revises: 20260909_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260909_0006"
down_revision: str | None = "20260909_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO principals(id,display_name,status) VALUES "
        "('principal_system_metering','Clearinghouse metering','active')"
    )
    op.execute("""
      CREATE FUNCTION protect_metering_system_principal() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' THEN
          IF OLD.id='principal_system_metering' THEN
            RAISE EXCEPTION 'metering system principal is reserved';
          END IF;
          RETURN OLD;
        END IF;
        IF OLD.id='principal_system_metering' THEN
          IF ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
            RAISE EXCEPTION 'metering system principal is reserved'; END IF;
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER metering_system_principal_protected BEFORE UPDATE OR DELETE
      ON principals FOR EACH ROW EXECUTE FUNCTION protect_metering_system_principal()""")
    op.execute("""
      CREATE FUNCTION reject_metering_system_reference() RETURNS trigger AS $$
      BEGIN
        IF NEW.principal_id='principal_system_metering'
        THEN RAISE EXCEPTION 'metering system principal cannot authenticate or hold roles'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    for table in ("principal_roles", "credentials", "auth_identities", "auth_browser_sessions"):
        op.execute(f"""CREATE TRIGGER {table}_reject_metering_system BEFORE INSERT OR UPDATE
          ON {table} FOR EACH ROW EXECUTE FUNCTION reject_metering_system_reference()""")
    op.create_table(
        "metering_checkpoints",
        sa.Column("consumer_group", sa.Text(), primary_key=True),
        sa.Column("topic", sa.Text(), primary_key=True),
        sa.Column("partition", sa.Integer(), primary_key=True),
        sa.Column("next_offset", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("partition >= 0", name="metering_checkpoint_partition"),
        sa.CheckConstraint("next_offset >= 0", name="metering_checkpoint_offset"),
    )
    op.create_table(
        "metering_worker_heartbeats",
        sa.Column("consumer_group", sa.Text(), primary_key=True),
        sa.Column("topic", sa.Text(), primary_key=True),
        sa.Column("signer_id", sa.Text(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("last_seen_at>=started_at", name="metering_heartbeat_order"),
    )
    op.create_table(
        "metering_observations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("producer_id", sa.Text(), nullable=False),
        sa.Column("transport_event_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("consumer_group", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("partition", sa.Integer(), nullable=False),
        sa.Column("kafka_offset", sa.BigInteger(), nullable=False),
        sa.Column("broker_beginning_offset", sa.BigInteger(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("business_sha256", sa.String(64), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "receipt_id", sa.Text(), sa.ForeignKey("authorization_receipts.id"), nullable=True
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("computed_fee", sa.Numeric(78, 0), nullable=True),
        sa.UniqueConstraint(
            "consumer_group",
            "topic",
            "partition",
            "kafka_offset",
            name="uq_metering_transport_position",
        ),
        sa.CheckConstraint(
            "partition >= 0 AND kafka_offset >= 0 AND broker_beginning_offset>=0 "
            "AND broker_beginning_offset<=kafka_offset",
            name="metering_position_nonnegative",
        ),
        sa.CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="metering_payload_hash"),
        sa.CheckConstraint(
            "business_sha256 IS NULL OR business_sha256 ~ '^[0-9a-f]{64}$'",
            name="metering_business_hash",
        ),
        sa.CheckConstraint(
            "outcome IN ('settled','duplicate','quarantined','ignored')",
            name="metering_observation_outcome",
        ),
        sa.CheckConstraint(
            "(outcome='quarantined')=(reason IS NOT NULL)", name="metering_quarantine_reason"
        ),
        sa.CheckConstraint(
            "(outcome='settled')=(computed_fee IS NOT NULL)", name="metering_settled_fee"
        ),
        sa.CheckConstraint(
            "(outcome='settled' AND transport_event_id IS NOT NULL AND receipt_id IS NOT NULL) OR "
            "outcome<>'settled'",
            name="metering_settled_shape",
        ),
        sa.CheckConstraint("processed_at >= received_at", name="metering_processed_order"),
    )
    op.create_index(
        "ix_metering_producer_event", "metering_observations", ["producer_id", "transport_event_id"]
    )
    op.create_table(
        "metering_position_conflicts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("consumer_group", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("partition", sa.Integer(), nullable=False),
        sa.Column("kafka_offset", sa.BigInteger(), nullable=False),
        sa.Column("original_payload_sha256", sa.String(64), nullable=False),
        sa.Column("conflicting_payload_sha256", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "consumer_group",
            "topic",
            "partition",
            "kafka_offset",
            "conflicting_payload_sha256",
            name="uq_metering_position_conflict",
        ),
        sa.CheckConstraint(
            "partition>=0 AND kafka_offset>=0", name="metering_conflict_position_nonnegative"
        ),
        sa.CheckConstraint(
            "original_payload_sha256 ~ '^[0-9a-f]{64}$' AND "
            "conflicting_payload_sha256 ~ '^[0-9a-f]{64}$' AND "
            "original_payload_sha256<>conflicting_payload_sha256",
            name="metering_conflict_hashes",
        ),
    )
    op.create_table(
        "metering_transport_gaps",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("consumer_group", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("partition", sa.Integer(), nullable=False),
        sa.Column("expected_offset", sa.BigInteger(), nullable=False),
        sa.Column("observed_offset", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "consumer_group",
            "topic",
            "partition",
            "expected_offset",
            "observed_offset",
            name="uq_metering_transport_gap",
        ),
        sa.CheckConstraint(
            "partition>=0 AND expected_offset>=0 AND observed_offset>expected_offset",
            name="metering_transport_gap_order",
        ),
        sa.CheckConstraint("reason='retention_gap'", name="metering_transport_gap_reason"),
    )
    op.create_table(
        "metering_canonical_events",
        sa.Column("producer_id", sa.Text(), primary_key=True),
        sa.Column("transport_event_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "observation_id",
            sa.Text(),
            sa.ForeignKey("metering_observations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("business_sha256", sa.String(64), nullable=False),
        sa.Column(
            "receipt_id",
            sa.Text(),
            sa.ForeignKey("authorization_receipts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="metering_canonical_hash"),
    )
    op.create_table(
        "receipt_transition_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "receipt_id", sa.Text(), sa.ForeignKey("authorization_receipts.id"), nullable=False
        ),
        sa.Column("from_status", sa.Text(), nullable=False),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "observation_id", sa.Text(), sa.ForeignKey("metering_observations.id"), nullable=True
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("receipt_id", "to_status", name="uq_receipt_transition_target"),
        sa.CheckConstraint(
            "(from_status='pending' AND to_status IN ('unresolved','settled','quarantined')) OR "
            "(from_status='unresolved' AND to_status IN ('settled','quarantined'))",
            name="receipt_transition_valid",
        ),
        sa.CheckConstraint(
            "length(reason) BETWEEN 1 AND 64", name="receipt_transition_reason_length"
        ),
    )
    op.create_index(
        "ix_receipt_transition_receipt", "receipt_transition_events", ["receipt_id", "created_at"]
    )
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "reservation_id",
            sa.Text(),
            sa.ForeignKey("authorization_receipts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "observation_id",
            sa.Text(),
            sa.ForeignKey("metering_observations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("lease_id", sa.Text(), sa.ForeignKey("leases.id"), nullable=False),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(78, 0), nullable=False),
        sa.Column("quantity_unit", sa.Text(), nullable=False),
        sa.Column("rate_card_id", sa.Text(), sa.ForeignKey("rate_cards.id"), nullable=False),
        sa.Column("rate_numerator", sa.Numeric(78, 0), nullable=False),
        sa.Column("rate_denominator", sa.Numeric(78, 0), nullable=False),
        sa.Column("charge_unit", sa.Text(), nullable=False),
        sa.Column("producer_id", sa.Text(), nullable=False),
        sa.Column("transport_event_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("sequence_number", sa.Numeric(20, 0), nullable=False),
        sa.Column("ticket_count", sa.SmallInteger(), nullable=False),
        sa.Column("manifest_id", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurred_at_text", sa.String(35), nullable=False),
        sa.Column("occurred_at_ns", sa.Numeric(30, 0), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="usage_quantity_positive"),
        sa.CheckConstraint(
            "rate_numerator > 0 AND rate_denominator > 0", name="usage_rate_positive"
        ),
        sa.CheckConstraint("charge_unit='wei'", name="usage_charge_unit"),
        sa.CheckConstraint("ticket_count BETWEEN 1 AND 100", name="usage_ticket_count"),
        sa.CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="usage_state_hash"),
        sa.ForeignKeyConstraint(["account_id", "tenant_id"], ["accounts.id", "accounts.tenant_id"]),
        sa.ForeignKeyConstraint(
            ["principal_id", "tenant_id", "account_id"],
            ["principals.id", "principals.tenant_id", "principals.account_id"],
        ),
    )
    op.create_index(
        "ix_usage_events_tenant_created", "usage_events", ["tenant_id", "created_at", "id"]
    )
    op.create_index(
        "ix_usage_events_account_created", "usage_events", ["account_id", "created_at", "id"]
    )
    op.create_table(
        "charges",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "usage_event_id",
            sa.Text(),
            sa.ForeignKey("usage_events.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "reservation_id",
            sa.Text(),
            sa.ForeignKey("authorization_receipts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("lease_id", sa.Text(), sa.ForeignKey("leases.id"), nullable=False),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(78, 0), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column(
            "ledger_transaction_id",
            sa.Text(),
            sa.ForeignKey("ledger_transactions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("rate_card_id", sa.Text(), sa.ForeignKey("rate_cards.id"), nullable=False),
        sa.Column("rate_numerator", sa.Numeric(78, 0), nullable=False),
        sa.Column("rate_denominator", sa.Numeric(78, 0), nullable=False),
        sa.Column("quantity_unit", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount >= 0", name="charge_amount_nonnegative"),
        sa.CheckConstraint("unit='wei'", name="charge_unit_wei"),
        sa.ForeignKeyConstraint(["account_id", "tenant_id"], ["accounts.id", "accounts.tenant_id"]),
    )
    op.create_index("ix_charges_tenant_created", "charges", ["tenant_id", "created_at", "id"])
    op.create_index("ix_charges_account_created", "charges", ["account_id", "created_at", "id"])
    op.create_table(
        "reconciliation_cases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "reservation_id", sa.Text(), sa.ForeignKey("authorization_receipts.id"), nullable=True
        ),
        sa.Column(
            "observation_id", sa.Text(), sa.ForeignKey("metering_observations.id"), nullable=True
        ),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('unknown_reservation','fee_mismatch','sequence_gap','forked_observation',"
            "'lineage_mismatch','missing_confirmation','invalid_schema','key_mismatch',"
            "'payload_too_large','transport_divergence','transport_gap')",
            name="reconciliation_kind",
        ),
        sa.CheckConstraint("status IN ('open','resolved')", name="reconciliation_status"),
        sa.CheckConstraint(
            "(status='resolved')=(resolved_at IS NOT NULL) AND "
            "(resolved_at IS NULL OR resolved_at>=created_at)",
            name="reconciliation_resolution_shape",
        ),
    )
    op.create_index(
        "uq_reconciliation_open_receipt_kind",
        "reconciliation_cases",
        ["reservation_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status='open' AND reservation_id IS NOT NULL"),
    )
    op.create_index(
        "ix_reconciliation_open", "reconciliation_cases", ["status", "created_at", "id"]
    )
    op.create_table(
        "metering_outbox",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("attempts >= 0", name="metering_outbox_attempts"),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at>=created_at", name="metering_outbox_publish_order"
        ),
        sa.UniqueConstraint("event_type", "aggregate_id", name="uq_metering_outbox_aggregate"),
    )
    op.create_index(
        "ix_metering_outbox_pending",
        "metering_outbox",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )

    op.drop_constraint("amount_nonzero", "ledger_postings", type_="check")
    op.execute(
        "ALTER FUNCTION protect_authorization_receipt() RENAME TO protect_authorization_receipt_v5"
    )
    op.execute("DROP TRIGGER authorization_receipts_protected ON authorization_receipts")
    op.execute("ALTER FUNCTION protect_lease() RENAME TO protect_lease_v5")
    op.execute("DROP TRIGGER leases_protected ON leases")
    op.execute("""
      CREATE FUNCTION protect_authorization_receipt() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'authorization receipt facts are immutable'; END IF;
        IF ROW(OLD.id,OLD.signer_id,OLD.state_id,OLD.sequence_number,OLD.session_id,OLD.lease_id,
          OLD.tenant_id,OLD.account_id,OLD.principal_id,OLD.reserved_amount,OLD.quantity,
          OLD.quantity_unit,OLD.pm_session_id,OLD.app,OLD.orchestrator_address,OLD.last_update_ns,
          OLD.payment_type,OLD.rate_card_id,OLD.rate_numerator,OLD.rate_denominator,OLD.charge_unit,
          OLD.state_hash,OLD.created_at) IS DISTINCT FROM
          ROW(NEW.id,NEW.signer_id,NEW.state_id,NEW.sequence_number,NEW.session_id,NEW.lease_id,
          NEW.tenant_id,NEW.account_id,NEW.principal_id,NEW.reserved_amount,NEW.quantity,
          NEW.quantity_unit,NEW.pm_session_id,NEW.app,NEW.orchestrator_address,NEW.last_update_ns,
          NEW.payment_type,NEW.rate_card_id,NEW.rate_numerator,NEW.rate_denominator,NEW.charge_unit,
          NEW.state_hash,NEW.created_at) OR
          (OLD.status IS DISTINCT FROM NEW.status AND NOT (
            (OLD.status='pending' AND NEW.status IN ('unresolved','settled','quarantined')) OR
            (OLD.status='unresolved' AND NEW.status IN ('settled','quarantined'))
          )) OR (OLD.signer_confirmed_at IS NOT NULL AND
                 OLD.signer_confirmed_at IS DISTINCT FROM NEW.signer_confirmed_at) OR
          NEW.signer_confirmed_at < NEW.created_at THEN
          RAISE EXCEPTION 'authorization receipt facts are immutable';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER authorization_receipts_protected BEFORE UPDATE OR DELETE
      ON authorization_receipts FOR EACH ROW EXECUTE FUNCTION protect_authorization_receipt()""")
    op.execute("""
      CREATE FUNCTION protect_lease() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'leases cannot be deleted'; END IF;
        IF ROW(OLD.id,OLD.session_id,OLD.tenant_id,OLD.account_id,OLD.principal_id,OLD.rate_card_id,
          OLD.capability,OLD.model,OLD.app,OLD.rate_numerator,OLD.rate_denominator,OLD.charge_unit,
          OLD.quantity_unit,OLD.policy_snapshot,OLD.policy_snapshot_json::text,OLD.cap,OLD.unit,
          OLD.expires_at,OLD.created_at) IS DISTINCT FROM
          ROW(NEW.id,NEW.session_id,NEW.tenant_id,NEW.account_id,NEW.principal_id,NEW.rate_card_id,
          NEW.capability,NEW.model,NEW.app,NEW.rate_numerator,NEW.rate_denominator,NEW.charge_unit,
          NEW.quantity_unit,NEW.policy_snapshot,NEW.policy_snapshot_json::text,NEW.cap,NEW.unit,
          NEW.expires_at,NEW.created_at) OR NEW.pending<0 OR
          NEW.settled<OLD.settled OR
          (NEW.pending>OLD.pending AND (NEW.pending-OLD.pending<>OLD.available-NEW.available OR
            NEW.settled<>OLD.settled)) OR
          (NEW.settled>OLD.settled AND
            NEW.settled-OLD.settled>OLD.pending-NEW.pending) OR
          (NEW.available>OLD.available AND NOT (NEW.pending<OLD.pending AND
            NEW.available-OLD.available<=OLD.pending-NEW.pending-(NEW.settled-OLD.settled))) THEN
          RAISE EXCEPTION 'lease facts and value conservation are immutable';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER leases_protected BEFORE UPDATE OR DELETE ON leases
      FOR EACH ROW EXECUTE FUNCTION protect_lease()""")
    op.execute("""
      CREATE FUNCTION enforce_metering_settlement() RETURNS trigger AS $$
      DECLARE rid text;
      BEGIN
        IF TG_TABLE_NAME='authorization_receipts' THEN rid := COALESCE(NEW.id,OLD.id);
        ELSE rid := COALESCE(NEW.reservation_id,OLD.reservation_id); END IF;
        IF EXISTS (SELECT 1 FROM usage_events u JOIN authorization_receipts r
          ON r.id=u.reservation_id WHERE u.reservation_id=rid AND r.status<>'settled')
        THEN RAISE EXCEPTION 'usage requires a settled receipt'; END IF;
        IF EXISTS (SELECT 1 FROM charges c JOIN authorization_receipts r
          ON r.id=c.reservation_id WHERE c.reservation_id=rid AND r.status<>'settled')
        THEN RAISE EXCEPTION 'charge requires a settled receipt'; END IF;
        IF EXISTS (
          SELECT 1 FROM authorization_receipts r
          WHERE r.id=rid AND r.status='settled' AND NOT EXISTS (
            SELECT 1 FROM usage_events u
            JOIN metering_observations o ON o.id=u.observation_id
            JOIN metering_canonical_events ce ON ce.observation_id=o.id
            JOIN charges c ON c.usage_event_id=u.id
            JOIN ledger_transactions tx ON tx.id=c.ledger_transaction_id
              AND tx.source_id=c.id AND tx.kind='charge'
            WHERE u.reservation_id=r.id AND c.reservation_id=r.id
              AND o.outcome='settled' AND o.receipt_id=r.id AND o.computed_fee=c.amount
              AND o.producer_id=r.signer_id
              AND o.producer_id=u.producer_id AND o.transport_event_id=u.transport_event_id
              AND u.producer_id=r.signer_id
              AND ce.receipt_id=r.id AND ce.payload_sha256=o.payload_sha256
              AND u.lease_id=r.lease_id AND u.tenant_id=r.tenant_id
              AND u.account_id=r.account_id AND u.principal_id=r.principal_id
              AND u.quantity<=r.quantity AND u.quantity_unit=r.quantity_unit
              AND u.rate_card_id=r.rate_card_id AND u.rate_numerator=r.rate_numerator
              AND u.rate_denominator=r.rate_denominator AND u.charge_unit=r.charge_unit
              AND u.state_hash=r.state_hash AND u.sequence_number=r.sequence_number
              AND u.occurred_at_ns=r.last_update_ns
              AND c.amount<=r.reserved_amount AND c.lease_id=r.lease_id
              AND c.account_id=r.account_id AND c.tenant_id=r.tenant_id
              AND c.rate_card_id=r.rate_card_id AND c.rate_numerator=r.rate_numerator
              AND c.rate_denominator=r.rate_denominator AND c.quantity_unit=r.quantity_unit
              AND c.unit=r.charge_unit AND tx.tenant_id=r.tenant_id
              AND c.amount=floor((2*u.quantity*u.rate_numerator+u.rate_denominator)
                /(2*u.rate_denominator))
              AND tx.actor_id='principal_system_metering'
              AND (SELECT count(*) FROM ledger_postings p WHERE p.transaction_id=tx.id)=2
              AND EXISTS (SELECT 1 FROM ledger_postings p WHERE p.transaction_id=tx.id
                AND p.account_code='payer:'||r.account_id AND p.amount=-c.amount AND p.unit=c.unit)
              AND EXISTS (SELECT 1 FROM ledger_postings p WHERE p.transaction_id=tx.id
                AND p.account_code='system:usage' AND p.amount=c.amount AND p.unit=c.unit)
          )
        ) THEN RAISE EXCEPTION 'settled receipt lacks usage, charge, or ledger evidence'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    for table in ("authorization_receipts", "usage_events", "charges"):
        op.execute(f"""CREATE CONSTRAINT TRIGGER {table}_settlement_reconciled
          AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_metering_settlement()""")
    op.execute("""
      CREATE FUNCTION enforce_lease_settlement() RETURNS trigger AS $$
      DECLARE lid text := COALESCE(NEW.id,OLD.id);
      BEGIN
        IF EXISTS (SELECT 1 FROM leases l WHERE l.id=lid AND
          (l.pending<>COALESCE((SELECT sum(r.reserved_amount) FROM authorization_receipts r
             WHERE r.lease_id=lid AND r.status IN ('pending','unresolved','quarantined')),0)
           OR l.settled<>COALESCE((SELECT sum(c.amount) FROM charges c WHERE c.lease_id=lid),0)))
        THEN RAISE EXCEPTION 'lease settlement projection mismatch'; END IF;
        IF EXISTS (SELECT 1 FROM authorization_receipts r WHERE r.lease_id=lid AND r.status='settled'
          AND NOT EXISTS (SELECT 1 FROM usage_events u JOIN charges c ON c.usage_event_id=u.id
            WHERE u.reservation_id=r.id AND c.reservation_id=r.id))
        THEN RAISE EXCEPTION 'lease contains settlement without evidence'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE CONSTRAINT TRIGGER leases_settlement_reconciled AFTER INSERT OR UPDATE OR
      DELETE ON leases DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION enforce_lease_settlement()""")
    op.execute("""
      CREATE FUNCTION protect_metering_fact() RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION '% is append-only',TG_TABLE_NAME; END; $$ LANGUAGE plpgsql
    """)
    for table in (
        "metering_observations",
        "metering_position_conflicts",
        "metering_transport_gaps",
        "metering_canonical_events",
        "usage_events",
        "charges",
    ):
        op.execute(f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION protect_metering_fact()""")
    op.execute("""CREATE TRIGGER receipt_transition_events_append_only BEFORE UPDATE OR DELETE
      ON receipt_transition_events FOR EACH ROW EXECUTE FUNCTION protect_metering_fact()""")
    op.execute("""
      CREATE FUNCTION enforce_receipt_transition_evidence() RETURNS trigger AS $$
      BEGIN
        IF TG_TABLE_NAME='authorization_receipts' THEN
          IF TG_OP='UPDATE' AND OLD.status<>NEW.status AND NOT EXISTS
            (SELECT 1 FROM receipt_transition_events e WHERE e.receipt_id=NEW.id
              AND e.from_status=OLD.status AND e.to_status=NEW.status)
          THEN RAISE EXCEPTION 'receipt status transition lacks immutable evidence'; END IF;
        ELSE
          IF NEW.to_status='settled' AND NOT EXISTS (SELECT 1 FROM metering_observations o
            WHERE o.id=NEW.observation_id AND o.receipt_id=NEW.receipt_id AND o.outcome='settled')
          THEN RAISE EXCEPTION 'settlement transition lacks accepted observation'; END IF;
          IF NEW.to_status='quarantined' AND NEW.observation_id IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM metering_observations o WHERE o.id=NEW.observation_id
              AND o.receipt_id=NEW.receipt_id AND o.outcome='quarantined')
          THEN RAISE EXCEPTION 'quarantine transition lacks observation'; END IF;
          IF NEW.to_status='quarantined' AND NEW.observation_id IS NULL AND NOT EXISTS
            (SELECT 1 FROM authorization_receipts r JOIN signer_state_heads h
              ON h.signer_id=r.signer_id AND h.state_id=r.state_id
              WHERE r.id=NEW.receipt_id AND h.quarantined)
          THEN RAISE EXCEPTION 'quarantine transition lacks fork evidence'; END IF;
          IF NEW.to_status='unresolved' AND NOT EXISTS
            (SELECT 1 FROM authorization_receipts r JOIN leases l ON l.id=r.lease_id
              WHERE r.id=NEW.receipt_id AND
                (r.signer_confirmed_at<=NEW.occurred_at-interval '30 seconds'
                  OR l.expires_at<=NEW.occurred_at))
          THEN RAISE EXCEPTION 'unresolved transition lacks missing-event evidence'; END IF;
          IF NEW.to_status IN ('unresolved','quarantined') AND NOT EXISTS
            (SELECT 1 FROM reconciliation_cases c WHERE c.reservation_id=NEW.receipt_id
              AND (NEW.to_status='unresolved' AND c.kind='missing_confirmation' OR
                NEW.to_status='quarantined' AND c.kind=CASE NEW.reason
                  WHEN 'state_fork' THEN 'forked_observation'
                  WHEN 'payload_too_large' THEN 'invalid_schema'
                  WHEN 'key_mismatch' THEN 'invalid_schema'
                  ELSE NEW.reason END))
          THEN RAISE EXCEPTION 'terminal receipt transition lacks reconciliation case'; END IF;
        END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    for table in ("authorization_receipts", "receipt_transition_events"):
        op.execute(f"""CREATE CONSTRAINT TRIGGER {table}_transition_evidenced
          AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_receipt_transition_evidence()""")
    op.execute("""
      CREATE FUNCTION protect_reconciliation_case() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' OR ROW(OLD.id,OLD.reservation_id,OLD.observation_id,OLD.tenant_id,
          OLD.account_id,OLD.kind,OLD.reason,OLD.evidence::text,OLD.created_at) IS DISTINCT FROM
          ROW(NEW.id,NEW.reservation_id,NEW.observation_id,NEW.tenant_id,NEW.account_id,NEW.kind,
          NEW.reason,NEW.evidence::text,NEW.created_at) OR
          OLD.status='resolved' OR NOT (OLD.status='open' AND NEW.status='resolved'
            AND OLD.resolved_at IS NULL AND NEW.resolved_at IS NOT NULL)
        THEN RAISE EXCEPTION 'reconciliation evidence is immutable'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER reconciliation_cases_protected BEFORE UPDATE OR DELETE
      ON reconciliation_cases FOR EACH ROW EXECUTE FUNCTION protect_reconciliation_case()""")
    op.execute("""
      CREATE FUNCTION enforce_reconciliation_lineage() RETURNS trigger AS $$
      BEGIN
        IF length(NEW.reason)>64 OR length(NEW.evidence::text)>4096 OR NOT (
          (NEW.kind=NEW.reason) OR
          (NEW.kind='forked_observation' AND NEW.reason='state_fork')
        ) THEN RAISE EXCEPTION 'reconciliation reason or evidence is invalid'; END IF;
        IF NEW.reservation_id IS NULL THEN
          IF NEW.tenant_id IS NOT NULL OR NEW.account_id IS NOT NULL OR
            (NEW.observation_id IS NOT NULL AND EXISTS (SELECT 1 FROM metering_observations o
              WHERE o.id=NEW.observation_id AND o.receipt_id IS NOT NULL))
          THEN RAISE EXCEPTION 'unscoped reconciliation has scoped lineage'; END IF;
        ELSIF NOT EXISTS (SELECT 1 FROM authorization_receipts r
          WHERE r.id=NEW.reservation_id AND r.tenant_id=NEW.tenant_id
            AND r.account_id=NEW.account_id AND (NEW.observation_id IS NULL OR EXISTS
              (SELECT 1 FROM metering_observations o WHERE o.id=NEW.observation_id
                AND o.receipt_id=r.id)))
        THEN RAISE EXCEPTION 'reconciliation scope does not match receipt'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE CONSTRAINT TRIGGER reconciliation_cases_lineage AFTER INSERT OR UPDATE
      ON reconciliation_cases DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION enforce_reconciliation_lineage()""")
    op.execute("""
      CREATE FUNCTION protect_metering_checkpoint() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' OR ROW(OLD.consumer_group,OLD.topic,OLD.partition) IS DISTINCT FROM
          ROW(NEW.consumer_group,NEW.topic,NEW.partition) OR NEW.next_offset<=OLD.next_offset OR
          (NEW.next_offset<>OLD.next_offset+1 AND NOT EXISTS
            (SELECT 1 FROM metering_transport_gaps g
              WHERE g.consumer_group=NEW.consumer_group AND g.topic=NEW.topic
                AND g.partition=NEW.partition AND g.expected_offset=OLD.next_offset
                AND g.observed_offset=NEW.next_offset-1)) THEN
          RAISE EXCEPTION 'metering checkpoints are monotonic'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER metering_checkpoints_monotonic BEFORE UPDATE OR DELETE
      ON metering_checkpoints FOR EACH ROW EXECUTE FUNCTION protect_metering_checkpoint()""")
    op.execute("""
      CREATE FUNCTION enforce_checkpoint_observations() RETURNS trigger AS $$
      DECLARE grp text; top text; part integer; nxt bigint; prior bigint; beginning bigint;
      BEGIN
        IF TG_TABLE_NAME='metering_checkpoints' THEN
          grp:=COALESCE(NEW.consumer_group,OLD.consumer_group); top:=COALESCE(NEW.topic,OLD.topic);
          part:=COALESCE(NEW.partition,OLD.partition); nxt:=COALESCE(NEW.next_offset,OLD.next_offset);
          IF TG_OP='INSERT' THEN prior:=0; ELSE prior:=OLD.next_offset; END IF;
        ELSE
          grp:=COALESCE(NEW.consumer_group,OLD.consumer_group); top:=COALESCE(NEW.topic,OLD.topic);
          part:=COALESCE(NEW.partition,OLD.partition);
          SELECT next_offset INTO nxt FROM metering_checkpoints
            WHERE consumer_group=grp AND topic=top AND partition=part;
          IF nxt IS NULL OR nxt<>COALESCE(NEW.kafka_offset,OLD.kafka_offset)+1
          THEN RAISE EXCEPTION 'observation does not match final checkpoint'; END IF;
        END IF;
        IF nxt IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM metering_observations o WHERE o.consumer_group=grp AND o.topic=top
            AND o.partition=part AND o.kafka_offset=nxt-1
        ) THEN RAISE EXCEPTION 'checkpoint does not match contiguous durable outcomes'; END IF;
        IF TG_TABLE_NAME='metering_checkpoints' THEN
          SELECT o.broker_beginning_offset INTO beginning FROM metering_observations o
            WHERE o.consumer_group=grp AND o.topic=top AND o.partition=part
              AND o.kafka_offset=nxt-1;
          IF nxt<>prior+1 AND NOT (beginning>prior AND nxt-1=beginning AND EXISTS
            (SELECT 1 FROM metering_transport_gaps g WHERE g.consumer_group=grp AND g.topic=top
              AND g.partition=part AND g.expected_offset=prior AND g.observed_offset=beginning))
          THEN RAISE EXCEPTION 'retention checkpoint jump lacks durable gap evidence'; END IF;
        END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    for table in ("metering_checkpoints", "metering_observations"):
        op.execute(f"""CREATE CONSTRAINT TRIGGER {table}_checkpoint_reconciled
          AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_checkpoint_observations()""")
    op.execute("""
      CREATE FUNCTION enforce_canonical_observation() RETURNS trigger AS $$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM metering_observations o WHERE o.id=NEW.observation_id
          AND o.producer_id=NEW.producer_id AND o.transport_event_id=NEW.transport_event_id
          AND o.payload_sha256=NEW.payload_sha256 AND o.business_sha256=NEW.business_sha256
          AND o.receipt_id=NEW.receipt_id
          AND o.outcome='settled')
        THEN RAISE EXCEPTION 'canonical event does not match accepted observation'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE CONSTRAINT TRIGGER metering_canonical_observation AFTER INSERT OR UPDATE
      ON metering_canonical_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION enforce_canonical_observation()""")
    op.execute("""
      CREATE FUNCTION protect_metering_outbox() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' OR ROW(OLD.id,OLD.event_type,OLD.aggregate_id,OLD.payload::text,OLD.created_at)
          IS DISTINCT FROM ROW(NEW.id,NEW.event_type,NEW.aggregate_id,NEW.payload::text,NEW.created_at)
          OR NEW.attempts<OLD.attempts OR OLD.published_at IS NOT NULL AND
             OLD.published_at IS DISTINCT FROM NEW.published_at
        THEN RAISE EXCEPTION 'metering outbox facts are immutable'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER metering_outbox_protected BEFORE UPDATE OR DELETE ON metering_outbox
      FOR EACH ROW EXECUTE FUNCTION protect_metering_outbox()""")
    op.execute("""
      CREATE FUNCTION enforce_reconciliation_outbox() RETURNS trigger AS $$
      DECLARE cid text;
      BEGIN
        IF TG_TABLE_NAME='reconciliation_cases' THEN
          IF TG_OP='DELETE' THEN cid:=OLD.id; ELSE cid:=NEW.id; END IF;
        ELSE
          IF TG_OP='DELETE' THEN cid:=OLD.aggregate_id; ELSE cid:=NEW.aggregate_id; END IF;
        END IF;
        IF EXISTS (SELECT 1 FROM reconciliation_cases c WHERE c.id=cid AND c.status='open')
          AND NOT EXISTS (SELECT 1 FROM metering_outbox x WHERE x.aggregate_id=cid
            AND x.event_type='metering.reconciliation.opened')
        THEN RAISE EXCEPTION 'open reconciliation lacks transactional outbox'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    for table in ("reconciliation_cases", "metering_outbox"):
        op.execute(f"""CREATE CONSTRAINT TRIGGER {table}_reconciliation_outbox
          AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_reconciliation_outbox()""")
    op.execute("""
      CREATE FUNCTION enforce_observation_quarantine_outbox() RETURNS trigger AS $$
      DECLARE oid text := COALESCE(NEW.id,OLD.id);
      BEGIN
        IF EXISTS (SELECT 1 FROM metering_observations o WHERE o.id=oid AND o.outcome='quarantined')
          AND NOT EXISTS (SELECT 1 FROM metering_outbox x WHERE x.aggregate_id=oid
            AND x.event_type='metering.quarantined')
        THEN RAISE EXCEPTION 'quarantine lacks transactional outbox'; END IF;
        IF EXISTS (SELECT 1 FROM metering_observations o WHERE o.id=oid AND o.outcome='quarantined')
          AND NOT EXISTS (SELECT 1 FROM metering_observations o JOIN reconciliation_cases c
            ON c.observation_id=o.id OR (o.receipt_id IS NOT NULL AND c.reservation_id=o.receipt_id
              AND c.kind=o.reason)
            WHERE o.id=oid)
        THEN RAISE EXCEPTION 'quarantine lacks reconciliation case'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE CONSTRAINT TRIGGER metering_observations_quarantine_outbox
      AFTER INSERT OR UPDATE OR DELETE ON metering_observations DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION enforce_observation_quarantine_outbox()""")
    op.execute("""
      CREATE FUNCTION enforce_outbox_quarantine() RETURNS trigger AS $$
      DECLARE oid text := COALESCE(NEW.aggregate_id,OLD.aggregate_id);
      BEGIN
        IF COALESCE(NEW.event_type,OLD.event_type)='metering.quarantined' AND NOT EXISTS
          (SELECT 1 FROM metering_observations o WHERE o.id=oid AND o.outcome='quarantined')
        THEN RAISE EXCEPTION 'quarantine outbox lacks observation'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE CONSTRAINT TRIGGER metering_outbox_quarantine_observation
      AFTER INSERT OR UPDATE OR DELETE ON metering_outbox DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION enforce_outbox_quarantine()""")
    op.execute("""
      CREATE FUNCTION enforce_zero_charge_postings() RETURNS trigger AS $$
      DECLARE txid text;
      BEGIN
        IF TG_TABLE_NAME='charges' THEN
          IF TG_OP='DELETE' THEN txid:=OLD.ledger_transaction_id;
          ELSE txid:=NEW.ledger_transaction_id; END IF;
        ELSE
          IF TG_OP='DELETE' THEN txid:=OLD.transaction_id;
          ELSE txid:=NEW.transaction_id; END IF;
        END IF;
        IF EXISTS (SELECT 1 FROM ledger_postings WHERE transaction_id=txid AND amount=0) AND NOT EXISTS (
          SELECT 1 FROM charges c WHERE c.ledger_transaction_id=txid AND c.amount=0
            AND (SELECT count(*) FROM ledger_postings p WHERE p.transaction_id=txid)=2
            AND (SELECT count(*) FROM ledger_postings p WHERE p.transaction_id=txid AND p.amount=0)=2
            AND EXISTS (SELECT 1 FROM ledger_postings p WHERE p.transaction_id=txid
              AND p.account_code='payer:'||c.account_id AND p.amount=0 AND p.unit=c.unit)
            AND EXISTS (SELECT 1 FROM ledger_postings p WHERE p.transaction_id=txid
              AND p.account_code='system:usage' AND p.amount=0 AND p.unit=c.unit)
        ) THEN RAISE EXCEPTION 'zero postings require exact zero-charge lineage'; END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql
    """)
    for table in ("ledger_postings", "charges"):
        op.execute(f"""CREATE CONSTRAINT TRIGGER {table}_zero_charge_reconciled
          AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_zero_charge_postings()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM metering_checkpoints) OR EXISTS (SELECT 1 FROM metering_observations) OR
         EXISTS (SELECT 1 FROM metering_position_conflicts) OR
         EXISTS (SELECT 1 FROM metering_transport_gaps) OR
         EXISTS (SELECT 1 FROM metering_canonical_events) OR
         EXISTS (SELECT 1 FROM receipt_transition_events) OR EXISTS (SELECT 1 FROM usage_events) OR
         EXISTS (SELECT 1 FROM charges) OR EXISTS (SELECT 1 FROM reconciliation_cases) OR
         EXISTS (SELECT 1 FROM metering_outbox) OR EXISTS (SELECT 1 FROM ledger_transactions
           WHERE actor_id='principal_system_metering')
      THEN RAISE EXCEPTION '0006 downgrade requires empty metering state'; END IF;
    END $$""")
    for table in ("ledger_postings", "charges"):
        op.execute(f"DROP TRIGGER {table}_zero_charge_reconciled ON {table}")
    op.execute("DROP FUNCTION enforce_zero_charge_postings()")
    for table in ("metering_checkpoints", "metering_observations"):
        op.execute(f"DROP TRIGGER {table}_checkpoint_reconciled ON {table}")
    op.execute("DROP FUNCTION enforce_checkpoint_observations()")
    op.execute("DROP TRIGGER metering_checkpoints_monotonic ON metering_checkpoints")
    op.execute("DROP FUNCTION protect_metering_checkpoint()")
    op.execute("DROP TRIGGER metering_outbox_quarantine_observation ON metering_outbox")
    op.execute("DROP FUNCTION enforce_outbox_quarantine()")
    op.execute("DROP TRIGGER metering_observations_quarantine_outbox ON metering_observations")
    op.execute("DROP FUNCTION enforce_observation_quarantine_outbox()")
    op.execute("DROP TRIGGER metering_canonical_observation ON metering_canonical_events")
    op.execute("DROP FUNCTION enforce_canonical_observation()")
    for table in ("reconciliation_cases", "metering_outbox"):
        op.execute(f"DROP TRIGGER {table}_reconciliation_outbox ON {table}")
    op.execute("DROP FUNCTION enforce_reconciliation_outbox()")
    op.execute("DROP TRIGGER metering_outbox_protected ON metering_outbox")
    op.execute("DROP FUNCTION protect_metering_outbox()")
    for table in ("authorization_receipts", "receipt_transition_events"):
        op.execute(f"DROP TRIGGER {table}_transition_evidenced ON {table}")
    op.execute("DROP FUNCTION enforce_receipt_transition_evidence()")
    op.execute("DROP TRIGGER reconciliation_cases_protected ON reconciliation_cases")
    op.execute("DROP FUNCTION protect_reconciliation_case()")
    op.execute("DROP TRIGGER reconciliation_cases_lineage ON reconciliation_cases")
    op.execute("DROP FUNCTION enforce_reconciliation_lineage()")
    op.execute("DROP TRIGGER receipt_transition_events_append_only ON receipt_transition_events")
    for table in (
        "metering_observations",
        "metering_position_conflicts",
        "metering_transport_gaps",
        "metering_canonical_events",
        "usage_events",
        "charges",
    ):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.execute("DROP FUNCTION protect_metering_fact()")
    op.execute("DROP TRIGGER leases_settlement_reconciled ON leases")
    op.execute("DROP FUNCTION enforce_lease_settlement()")
    for table in ("authorization_receipts", "usage_events", "charges"):
        op.execute(f"DROP TRIGGER {table}_settlement_reconciled ON {table}")
    op.execute("DROP FUNCTION enforce_metering_settlement()")
    op.execute("DROP TRIGGER leases_protected ON leases")
    op.execute("DROP FUNCTION protect_lease()")
    op.execute("DROP TRIGGER authorization_receipts_protected ON authorization_receipts")
    op.execute("DROP FUNCTION protect_authorization_receipt()")
    op.create_check_constraint("amount_nonzero", "ledger_postings", "amount <> 0")
    for table in (
        "metering_outbox",
        "reconciliation_cases",
        "charges",
        "usage_events",
        "receipt_transition_events",
        "metering_canonical_events",
        "metering_position_conflicts",
        "metering_transport_gaps",
        "metering_observations",
        "metering_checkpoints",
        "metering_worker_heartbeats",
    ):
        op.drop_table(table)
    for table in ("principal_roles", "credentials", "auth_identities", "auth_browser_sessions"):
        op.execute(f"DROP TRIGGER {table}_reject_metering_system ON {table}")
    op.execute("DROP FUNCTION reject_metering_system_reference()")
    op.execute("DROP TRIGGER metering_system_principal_protected ON principals")
    op.execute("DROP FUNCTION protect_metering_system_principal()")
    op.execute("DELETE FROM principals WHERE id='principal_system_metering'")
    # Restore the exact 0005 functions retained under versioned names.
    op.execute(
        "ALTER FUNCTION protect_authorization_receipt_v5() RENAME TO protect_authorization_receipt"
    )
    op.execute("""CREATE TRIGGER authorization_receipts_protected BEFORE UPDATE OR DELETE
      ON authorization_receipts FOR EACH ROW EXECUTE FUNCTION protect_authorization_receipt()""")
    op.execute("ALTER FUNCTION protect_lease_v5() RENAME TO protect_lease")
    op.execute("""CREATE TRIGGER leases_protected BEFORE UPDATE OR DELETE ON leases
      FOR EACH ROW EXECUTE FUNCTION protect_lease()""")
