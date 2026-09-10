# Operational exercise record

Store generated, sanitized machine-readable results under the ignored artifacts
directory. A human summary may use this schema; it is evidence, not a work
tracker. Follow-up work belongs only in Beads.

```yaml
schema_version: 1
exercise: backup-restore | broker-recovery | metering-integrity | retention | rotation | migration | capacity | incident
started_at: 2026-09-10T00:00:00Z
finished_at: 2026-09-10T00:00:00Z
release_revision: git-sha-or-image-digest
schema_revision: alembic-revision
topology: reference-compose | production-description
operator: opaque-operator-id
result: passed | failed | aborted
recovery_point_age_seconds: 0
recovery_duration_seconds: 0
capacity:
  expected_authorizations_per_second: 0
  exercised_authorizations_per_second: 0
  expected_events_per_second: 0
  exercised_events_per_second: 0
checks:
  - name: bounded-machine-readable-name
    outcome: passed | failed
    value: bounded-nonsecret-value
beads:
  - och-u8d.18
```

Omit fields that do not apply. Never include raw request/event bodies, tenant or
user identifiers, email/IP data, credentials, database URLs, filesystem secret
paths, decrypted material, or unbounded exception text. A failed or aborted
exercise must name a follow-up Bead before the affected capability is treated as
production-ready.
