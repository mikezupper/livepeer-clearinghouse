"""Create explicit identity linking and one-shot operator bootstrap state."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0004"
down_revision: str | None = "20260909_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create secret-backed invitations and immutable onboarding facts."""
    op.create_unique_constraint(
        "uq_principals_id_tenant_onboarding", "principals", ["id", "tenant_id"]
    )
    op.create_table(
        "auth_identity_invitations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source_principal_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("target_principal_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("target_account_id", sa.Text(), nullable=True),
        sa.Column("target_roles", sa.Text(), nullable=False),
        sa.Column("target_status", sa.Text(), nullable=False),
        sa.Column("issued_by", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("issuer_authority", sa.Text(), nullable=False),
        sa.Column("secret_prefix", sa.Text(), nullable=False, unique=True),
        sa.Column("secret_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("identity_id", sa.Text(), sa.ForeignKey("auth_identities.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "octet_length(secret_hash) = 32", name="identity_invitation_hash_length"
        ),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1000", name="identity_invitation_reason"),
        sa.CheckConstraint("expires_at > created_at", name="identity_invitation_expiry"),
        sa.CheckConstraint(
            "(consumed_at IS NULL) = (identity_id IS NULL)",
            name="identity_invitation_consumption",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR superseded_at IS NULL",
            name="identity_invitation_one_terminal_state",
        ),
        sa.CheckConstraint(
            "issuer_authority IN ('operator','tenant_admin')",
            name="identity_invitation_issuer_authority",
        ),
        sa.CheckConstraint("target_status = 'active'", name="identity_invitation_target_status"),
        sa.CheckConstraint("length(target_roles) > 0", name="identity_invitation_target_roles"),
        sa.CheckConstraint(
            "source_principal_id <> target_principal_id",
            name="identity_invitation_distinct_principals",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="identity_invitation_consumed_after_creation",
        ),
        sa.CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= created_at",
            name="identity_invitation_superseded_after_creation",
        ),
        sa.ForeignKeyConstraint(
            ["target_principal_id", "tenant_id"],
            ["principals.id", "principals.tenant_id"],
            name="fk_identity_invitation_target_tenant",
        ),
    )
    op.execute("""
        CREATE FUNCTION validate_identity_invitation_insert() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM principals source
            JOIN principals target ON target.id=NEW.target_principal_id
            JOIN principals issuer ON issuer.id=NEW.issued_by
            WHERE source.id=NEW.source_principal_id
              AND source.status='active' AND source.tenant_id IS NULL
              AND source.account_id IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM principal_roles sr WHERE sr.principal_id=source.id
              )
              AND (SELECT count(*) FROM auth_identities ai
                   WHERE ai.principal_id=source.id)=1
              AND target.status::text=NEW.target_status
              AND target.tenant_id=NEW.tenant_id
              AND target.account_id IS NOT DISTINCT FROM NEW.target_account_id
              AND COALESCE((SELECT string_agg(tr.role::text, ',' ORDER BY tr.role::text)
                            FROM principal_roles tr WHERE tr.principal_id=target.id), '')
                  =NEW.target_roles
              AND issuer.status='active'
              AND (
                (NEW.issuer_authority='operator'
                 AND issuer.tenant_id IS NULL AND issuer.account_id IS NULL
                 AND (SELECT count(*) FROM principal_roles ir
                      WHERE ir.principal_id=issuer.id AND ir.role='operator')=1
                 AND (SELECT count(*) FROM principal_roles ir
                      WHERE ir.principal_id=issuer.id)=1)
                OR
                (NEW.issuer_authority='tenant_admin'
                 AND issuer.tenant_id=NEW.tenant_id
                 AND EXISTS (SELECT 1 FROM principal_roles ir
                             WHERE ir.principal_id=issuer.id AND ir.role='tenant_admin')
                 AND NEW.target_roles='credential_holder')
              )
          ) THEN
            RAISE EXCEPTION 'invalid identity invitation state';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER auth_identity_invitations_valid_insert
        BEFORE INSERT ON auth_identity_invitations
        FOR EACH ROW EXECUTE FUNCTION validate_identity_invitation_insert()
    """)
    op.execute("""
        CREATE FUNCTION enforce_identity_invitation_transition() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'identity invitations cannot be deleted';
          END IF;
          IF OLD.id IS DISTINCT FROM NEW.id
             OR OLD.source_principal_id IS DISTINCT FROM NEW.source_principal_id
             OR OLD.target_principal_id IS DISTINCT FROM NEW.target_principal_id
             OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
             OR OLD.target_account_id IS DISTINCT FROM NEW.target_account_id
             OR OLD.target_roles IS DISTINCT FROM NEW.target_roles
             OR OLD.target_status IS DISTINCT FROM NEW.target_status
             OR OLD.issued_by IS DISTINCT FROM NEW.issued_by
             OR OLD.issuer_authority IS DISTINCT FROM NEW.issuer_authority
             OR OLD.secret_prefix IS DISTINCT FROM NEW.secret_prefix
             OR OLD.secret_hash IS DISTINCT FROM NEW.secret_hash
             OR OLD.reason IS DISTINCT FROM NEW.reason
             OR OLD.expires_at IS DISTINCT FROM NEW.expires_at
             OR OLD.created_at IS DISTINCT FROM NEW.created_at
             OR OLD.consumed_at IS NOT NULL
             OR OLD.superseded_at IS NOT NULL
             OR NOT (
               (NEW.consumed_at IS NOT NULL AND NEW.identity_id IS NOT NULL
                AND NEW.superseded_at IS NULL)
               OR
               (NEW.superseded_at IS NOT NULL AND NEW.consumed_at IS NULL
                AND NEW.identity_id IS NULL)
             ) THEN
            RAISE EXCEPTION 'invalid identity invitation mutation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER auth_identity_invitations_transition_only
        BEFORE UPDATE OR DELETE ON auth_identity_invitations
        FOR EACH ROW EXECUTE FUNCTION enforce_identity_invitation_transition()
    """)
    op.create_index(
        "ix_auth_identity_invitations_target",
        "auth_identity_invitations",
        ["target_principal_id"],
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_auth_identity_invitations_active_source
        ON auth_identity_invitations(source_principal_id)
        WHERE consumed_at IS NULL AND superseded_at IS NULL
    """)
    op.create_table(
        "auth_identity_links",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "identity_id",
            sa.Text(),
            sa.ForeignKey("auth_identities.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("source_principal_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("target_principal_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("actor_id", sa.Text(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column(
            "invitation_id",
            sa.Text(),
            sa.ForeignKey("auth_identity_invitations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_principal_id <> target_principal_id",
            name="identity_link_distinct_principals",
        ),
        sa.CheckConstraint("actor_id = source_principal_id", name="identity_link_self_proof"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1000", name="identity_link_reason"),
    )
    op.execute("""
        CREATE FUNCTION enforce_identity_link_consistency() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM auth_identity_invitations v
            JOIN auth_identities i ON i.id=NEW.identity_id
            WHERE v.id=NEW.invitation_id
              AND v.identity_id=NEW.identity_id
              AND v.source_principal_id=NEW.source_principal_id
              AND v.target_principal_id=NEW.target_principal_id
              AND v.tenant_id=NEW.tenant_id
              AND v.consumed_at=NEW.linked_at
              AND v.superseded_at IS NULL
              AND i.principal_id=NEW.target_principal_id
          ) THEN
            RAISE EXCEPTION 'identity link does not match invitation and identity state';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER auth_identity_links_consistent
        BEFORE INSERT ON auth_identity_links
        FOR EACH ROW EXECUTE FUNCTION enforce_identity_link_consistency()
    """)
    op.execute("""
        CREATE FUNCTION protect_linked_auth_identity() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM auth_identity_links WHERE identity_id=OLD.id)
             AND (TG_OP='DELETE' OR OLD.principal_id IS DISTINCT FROM NEW.principal_id) THEN
            RAISE EXCEPTION 'linked authentication identity cannot be reassigned';
          END IF;
          IF TG_OP='DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER auth_identities_link_protected
        BEFORE UPDATE OR DELETE ON auth_identities
        FOR EACH ROW EXECUTE FUNCTION protect_linked_auth_identity()
    """)
    op.execute("""
        CREATE FUNCTION protect_linked_source_principal() RETURNS trigger AS $$
        DECLARE linked boolean;
        DECLARE principal text;
        BEGIN
          IF TG_OP='DELETE' THEN
            principal := COALESCE(to_jsonb(OLD)->>'principal_id', to_jsonb(OLD)->>'id');
          ELSE
            principal := COALESCE(to_jsonb(NEW)->>'principal_id', to_jsonb(NEW)->>'id');
          END IF;
          SELECT EXISTS(SELECT 1 FROM auth_identity_links
                        WHERE source_principal_id=principal) INTO linked;
          IF NOT linked THEN
            IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
          END IF;
          IF TG_TABLE_NAME='principals' AND TG_OP='UPDATE'
             AND NEW.status='suspended' AND NEW.tenant_id IS NULL
             AND NEW.account_id IS NULL THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'linked source principal is an immutable tombstone';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER principals_linked_source_tombstone
        BEFORE UPDATE OR DELETE ON principals
        FOR EACH ROW EXECUTE FUNCTION protect_linked_source_principal()
    """)
    op.execute("""
        CREATE TRIGGER principal_roles_linked_source_tombstone
        BEFORE INSERT OR UPDATE OR DELETE ON principal_roles
        FOR EACH ROW EXECUTE FUNCTION protect_linked_source_principal()
    """)
    op.execute("""
        CREATE TRIGGER credentials_linked_source_tombstone
        BEFORE INSERT OR UPDATE ON credentials
        FOR EACH ROW EXECUTE FUNCTION protect_linked_source_principal()
    """)
    op.create_table(
        "operator_bootstrap",
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column(
            "principal_id",
            sa.Text(),
            sa.ForeignKey("principals.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("singleton", name="operator_bootstrap_singleton"),
        sa.CheckConstraint("configuration_hash ~ '^[0-9a-f]{64}$'", name="operator_bootstrap_hash"),
    )
    op.execute("""
        CREATE TRIGGER auth_identity_links_append_only
        BEFORE UPDATE OR DELETE ON auth_identity_links
        FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()
    """)
    op.execute("""
        CREATE TRIGGER operator_bootstrap_append_only
        BEFORE UPDATE OR DELETE ON operator_bootstrap
        FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()
    """)


def downgrade() -> None:
    """Remove onboarding state in reverse dependency order."""
    op.execute("DROP TRIGGER operator_bootstrap_append_only ON operator_bootstrap")
    op.execute("DROP TRIGGER auth_identity_links_append_only ON auth_identity_links")
    op.execute("DROP TRIGGER auth_identities_link_protected ON auth_identities")
    op.execute("DROP FUNCTION protect_linked_auth_identity()")
    op.execute("DROP TRIGGER credentials_linked_source_tombstone ON credentials")
    op.execute("DROP TRIGGER principal_roles_linked_source_tombstone ON principal_roles")
    op.execute("DROP TRIGGER principals_linked_source_tombstone ON principals")
    op.execute("DROP FUNCTION protect_linked_source_principal()")
    op.execute("DROP TRIGGER auth_identity_links_consistent ON auth_identity_links")
    op.execute("DROP FUNCTION enforce_identity_link_consistency()")
    op.drop_table("operator_bootstrap")
    op.drop_table("auth_identity_links")
    op.execute(
        "DROP TRIGGER auth_identity_invitations_transition_only ON auth_identity_invitations"
    )
    op.execute("DROP FUNCTION enforce_identity_invitation_transition()")
    op.execute("DROP TRIGGER auth_identity_invitations_valid_insert ON auth_identity_invitations")
    op.execute("DROP FUNCTION validate_identity_invitation_insert()")
    op.drop_index("uq_auth_identity_invitations_active_source")
    op.drop_index("ix_auth_identity_invitations_target", table_name="auth_identity_invitations")
    op.drop_table("auth_identity_invitations")
    op.drop_constraint("uq_principals_id_tenant_onboarding", "principals", type_="unique")
