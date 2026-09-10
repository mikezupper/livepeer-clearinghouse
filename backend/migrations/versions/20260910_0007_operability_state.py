# ruff: noqa: E501

"""Create bounded operational control-plane state.

Revision ID: 20260910_0007
Revises: 20260909_0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0007"
down_revision: str | None = "20260909_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _immutable(table: str) -> None:
    op.execute(f"""
      CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
      FOR EACH ROW EXECUTE FUNCTION reject_operational_fact_mutation()
    """)


def upgrade() -> None:
    """Add append-only operational evidence and bounded job state."""
    op.execute("""
      CREATE FUNCTION reject_operational_fact_mutation() RETURNS trigger AS $$
      BEGIN
        RAISE EXCEPTION 'operational facts are append-only';
      END; $$ LANGUAGE plpgsql
    """)
    op.create_table(
        "operations_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("initiator_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("initiator_id", "idempotency_key", name="uq_operations_job_request"),
        sa.CheckConstraint(
            "kind IN ('projection_reconciliation','retention','backup','restore','key_rotation')",
            name="operations_job_kind",
        ),
        sa.CheckConstraint(
            "mode IN ('check','dry_run','apply','repair','record','verify')",
            name="operations_job_mode",
        ),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','escalated')", name="operations_job_status"
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 16 AND 200", name="operations_job_idempotency"
        ),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name="operations_job_request_hash"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1000", name="operations_job_reason"),
        sa.CheckConstraint(
            "octet_length(parameters::text)<=16384", name="operations_job_parameters_size"
        ),
        sa.CheckConstraint(
            "result IS NULL OR octet_length(result::text)<=65536", name="operations_job_result_size"
        ),
        sa.CheckConstraint(
            "(status='running' AND completed_at IS NULL) OR "
            "(status<>'running' AND completed_at IS NOT NULL AND completed_at>=started_at)",
            name="operations_job_completion",
        ),
    )
    op.create_index(
        "ix_operations_jobs_status_started", "operations_jobs", ["status", "started_at", "id"]
    )
    op.execute("""
      CREATE FUNCTION protect_operations_job() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'operation jobs cannot be deleted'; END IF;
        IF ROW(OLD.id,OLD.kind,OLD.mode,OLD.initiator_id,OLD.idempotency_key,
               OLD.request_sha256,OLD.reason,OLD.parameters::text,OLD.started_at)
           IS DISTINCT FROM
           ROW(NEW.id,NEW.kind,NEW.mode,NEW.initiator_id,NEW.idempotency_key,
               NEW.request_sha256,NEW.reason,NEW.parameters::text,NEW.started_at)
        THEN RAISE EXCEPTION 'operation job identity is immutable'; END IF;
        IF OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','escalated')
        THEN RAISE EXCEPTION 'invalid operation job transition'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER operations_jobs_protected BEFORE UPDATE OR DELETE
      ON operations_jobs FOR EACH ROW EXECUTE FUNCTION protect_operations_job()""")

    op.create_table(
        "backup_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("backup_type", sa.Text(), nullable=False),
        sa.Column("location_sha256", sa.String(64), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("high_watermark", sa.Text(), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("initiator_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("artifact_id", "sequence", name="uq_backup_event_sequence"),
        sa.CheckConstraint("sequence>0", name="backup_event_positive_sequence"),
        sa.CheckConstraint(
            "action IN ('created','verified','expired','restore_verified')",
            name="backup_event_action",
        ),
        sa.CheckConstraint("backup_type IN ('logical','base','wal')", name="backup_event_type"),
        sa.CheckConstraint(
            "location_sha256 ~ '^[0-9a-f]{64}$' AND checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="backup_event_hashes",
        ),
        sa.CheckConstraint("length(key_id) BETWEEN 1 AND 128", name="backup_event_key"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1000", name="backup_event_reason"),
        sa.CheckConstraint("retention_until>occurred_at", name="backup_event_retention"),
    )
    op.create_index(
        "ix_backup_events_artifact_sequence", "backup_events", ["artifact_id", "sequence"]
    )
    _immutable("backup_events")
    op.execute("""
      CREATE FUNCTION validate_backup_event() RETURNS trigger AS $$
      DECLARE prior record;
      BEGIN
        SELECT * INTO prior FROM backup_events WHERE artifact_id=NEW.artifact_id
          ORDER BY sequence DESC LIMIT 1 FOR UPDATE;
        IF prior IS NULL THEN
          IF NEW.sequence<>1 OR NEW.action<>'created' THEN
            RAISE EXCEPTION 'backup lineage must begin with created'; END IF;
        ELSIF NEW.sequence<>prior.sequence+1
          OR ROW(NEW.backup_type,NEW.location_sha256,NEW.checksum_sha256,NEW.key_id,
                 NEW.high_watermark,NEW.retention_until)
             IS DISTINCT FROM
             ROW(prior.backup_type,prior.location_sha256,prior.checksum_sha256,prior.key_id,
                 prior.high_watermark,prior.retention_until)
          OR (prior.action='created' AND NEW.action NOT IN ('verified','expired'))
          OR (prior.action='verified' AND NEW.action NOT IN ('restore_verified','expired'))
          OR (prior.action='restore_verified' AND NEW.action NOT IN ('restore_verified','expired'))
          OR prior.action='expired'
        THEN RAISE EXCEPTION 'invalid backup event transition'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER backup_events_valid BEFORE INSERT ON backup_events
      FOR EACH ROW EXECUTE FUNCTION validate_backup_event()""")

    op.create_table(
        "retention_policy_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("initiator_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("category", "version", name="uq_retention_policy_version"),
        sa.CheckConstraint(
            "category IN ('auth_ephemeral','browser_sessions','operational_detail','financial','audit','backups')",
            name="retention_policy_category",
        ),
        sa.CheckConstraint(
            "version>0 AND retention_days BETWEEN 1 AND 3650", name="retention_policy_bounds"
        ),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1000", name="retention_policy_reason"),
    )
    _immutable("retention_policy_versions")
    op.execute("""
      CREATE FUNCTION validate_retention_policy_version() RETURNS trigger AS $$
      DECLARE prior integer;
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtext('retention-policy:'||NEW.category));
        SELECT max(version) INTO prior FROM retention_policy_versions
          WHERE category=NEW.category;
        IF NEW.version<>COALESCE(prior,0)+1 THEN
          RAISE EXCEPTION 'retention policy versions must be contiguous'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER retention_policy_versions_valid BEFORE INSERT
      ON retention_policy_versions FOR EACH ROW
      EXECUTE FUNCTION validate_retention_policy_version()""")

    op.create_table(
        "legal_hold_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("hold_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("approver_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("hold_id", "sequence", name="uq_legal_hold_sequence"),
        sa.CheckConstraint("sequence>0", name="legal_hold_positive_sequence"),
        sa.CheckConstraint("action IN ('placed','released')", name="legal_hold_action"),
        sa.CheckConstraint(
            "category IN ('auth_ephemeral','browser_sessions','operational_detail','financial','audit','backups')",
            name="legal_hold_category",
        ),
        sa.CheckConstraint(
            "scope_type IN ('global','tenant','account','principal')", name="legal_hold_scope"
        ),
        sa.CheckConstraint("length(scope_id) BETWEEN 1 AND 200", name="legal_hold_scope_id"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1000", name="legal_hold_reason"),
        sa.CheckConstraint("review_at>occurred_at", name="legal_hold_review"),
    )
    op.create_index(
        "ix_legal_hold_events_hold_sequence", "legal_hold_events", ["hold_id", "sequence"]
    )
    _immutable("legal_hold_events")
    op.execute("""
      CREATE FUNCTION validate_legal_hold_event() RETURNS trigger AS $$
      DECLARE prior record;
      BEGIN
        SELECT sequence,action,category,scope_type,scope_id INTO prior
        FROM legal_hold_events WHERE hold_id=NEW.hold_id ORDER BY sequence DESC LIMIT 1 FOR UPDATE;
        IF prior IS NULL THEN
          IF NEW.sequence<>1 OR NEW.action<>'placed' THEN RAISE EXCEPTION 'legal hold must begin with placed'; END IF;
        ELSIF prior.action<>'placed' OR NEW.sequence<>prior.sequence+1 OR NEW.action<>'released'
          OR ROW(NEW.category,NEW.scope_type,NEW.scope_id) IS DISTINCT FROM ROW(prior.category,prior.scope_type,prior.scope_id)
        THEN RAISE EXCEPTION 'invalid legal hold transition'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER legal_hold_events_valid BEFORE INSERT ON legal_hold_events
      FOR EACH ROW EXECUTE FUNCTION validate_legal_hold_event()""")

    op.create_table(
        "key_rotation_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("rotation_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("prior_key_id", sa.Text(), nullable=True),
        sa.Column("initiator_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rotation_id", "sequence", name="uq_key_rotation_sequence"),
        sa.CheckConstraint("sequence>0", name="key_rotation_positive_sequence"),
        sa.CheckConstraint(
            "purpose IN ('auth_pepper','credential_pepper','session_pepper','backup_encryption','signer_webhook')",
            name="key_rotation_purpose",
        ),
        sa.CheckConstraint(
            "action IN ('started','activated','retired')", name="key_rotation_action"
        ),
        sa.CheckConstraint(
            "length(key_id) BETWEEN 1 AND 128 AND (prior_key_id IS NULL OR length(prior_key_id) BETWEEN 1 AND 128)",
            name="key_rotation_key_ids",
        ),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1000", name="key_rotation_reason"),
    )
    _immutable("key_rotation_events")
    op.execute("""
      CREATE FUNCTION validate_key_rotation_event() RETURNS trigger AS $$
      DECLARE prior record;
      BEGIN
        SELECT * INTO prior FROM key_rotation_events WHERE rotation_id=NEW.rotation_id
          ORDER BY sequence DESC LIMIT 1 FOR UPDATE;
        IF prior IS NULL THEN
          IF NEW.sequence<>1 OR NEW.action<>'started' THEN
            RAISE EXCEPTION 'key rotation must begin with started'; END IF;
        ELSIF NEW.sequence<>prior.sequence+1
          OR ROW(NEW.purpose,NEW.key_id,NEW.prior_key_id)
             IS DISTINCT FROM ROW(prior.purpose,prior.key_id,prior.prior_key_id)
          OR (prior.action='started' AND NEW.action<>'activated')
          OR (prior.action='activated' AND NEW.action<>'retired')
          OR prior.action='retired'
        THEN RAISE EXCEPTION 'invalid key rotation transition'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER key_rotation_events_valid BEFORE INSERT
      ON key_rotation_events FOR EACH ROW EXECUTE FUNCTION validate_key_rotation_event()""")

    op.create_table(
        "lifecycle_tombstones",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_sha256", sa.String(64), nullable=False),
        sa.Column("categories", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("initiator_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "subject_type", "subject_sha256", name="uq_lifecycle_tombstone_subject"
        ),
        sa.CheckConstraint(
            "subject_type IN ('identity','principal','account','tenant')",
            name="lifecycle_tombstone_type",
        ),
        sa.CheckConstraint("subject_sha256 ~ '^[0-9a-f]{64}$'", name="lifecycle_tombstone_hash"),
        sa.CheckConstraint(
            "cardinality(categories) BETWEEN 1 AND 8", name="lifecycle_tombstone_categories"
        ),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1000", name="lifecycle_tombstone_reason"),
    )
    _immutable("lifecycle_tombstones")

    op.create_table(
        "retention_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id", sa.Text(), sa.ForeignKey("operations_jobs.id"), nullable=False, unique=True
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("high_watermark", sa.Text(), nullable=False),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.Column("deleted_count", sa.Integer(), nullable=False),
        sa.Column("held_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('auth_ephemeral','browser_sessions','operational_detail')",
            name="retention_run_category",
        ),
        sa.CheckConstraint("mode IN ('dry_run','apply')", name="retention_run_mode"),
        sa.CheckConstraint(
            "scanned_count>=0 AND deleted_count>=0 AND held_count>=0 AND deleted_count+held_count<=scanned_count",
            name="retention_run_counts",
        ),
    )
    _immutable("retention_runs")

    op.create_table(
        "projection_checks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id", sa.Text(), sa.ForeignKey("operations_jobs.id"), nullable=False, unique=True
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("ledger_high_watermark", sa.Text(), nullable=False),
        sa.Column("lease_high_watermark", sa.Text(), nullable=False),
        sa.Column("account_drift_count", sa.Integer(), nullable=False),
        sa.Column("global_drift", sa.Boolean(), nullable=False),
        sa.Column("authoritative_violation_count", sa.Integer(), nullable=False),
        sa.Column("shadow_sha256", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('check','repair')", name="projection_check_mode"),
        sa.CheckConstraint(
            "status IN ('consistent','drift','repaired','escalated')",
            name="projection_check_status",
        ),
        sa.CheckConstraint(
            "account_drift_count>=0 AND authoritative_violation_count>=0",
            name="projection_check_counts",
        ),
        sa.CheckConstraint("shadow_sha256 ~ '^[0-9a-f]{64}$'", name="projection_check_hash"),
        sa.CheckConstraint(
            "octet_length(details::text)<=65536", name="projection_check_details_size"
        ),
    )
    op.create_index("ix_projection_checks_created", "projection_checks", ["created_at", "id"])
    _immutable("projection_checks")


def downgrade() -> None:
    """Refuse to discard operational evidence, then remove the schema."""
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM operations_jobs) OR EXISTS (SELECT 1 FROM backup_events)
          OR EXISTS (SELECT 1 FROM retention_policy_versions)
          OR EXISTS (SELECT 1 FROM legal_hold_events) OR EXISTS (SELECT 1 FROM key_rotation_events)
          OR EXISTS (SELECT 1 FROM lifecycle_tombstones) OR EXISTS (SELECT 1 FROM retention_runs)
          OR EXISTS (SELECT 1 FROM projection_checks)
        THEN RAISE EXCEPTION 'operability downgrade requires empty operational state'; END IF;
      END $$
    """)
    for table in (
        "projection_checks",
        "retention_runs",
        "lifecycle_tombstones",
        "key_rotation_events",
        "legal_hold_events",
        "retention_policy_versions",
        "backup_events",
    ):
        op.drop_table(table)
    op.execute("DROP TRIGGER operations_jobs_protected ON operations_jobs")
    op.execute("DROP FUNCTION protect_operations_job()")
    op.drop_table("operations_jobs")
    op.execute("DROP FUNCTION validate_key_rotation_event()")
    op.execute("DROP FUNCTION validate_legal_hold_event()")
    op.execute("DROP FUNCTION validate_retention_policy_version()")
    op.execute("DROP FUNCTION validate_backup_event()")
    op.execute("DROP FUNCTION reject_operational_fact_mutation()")
