"""SQLite implementation of the simplified core store."""
# ruff: noqa: S608 -- dynamic fragments are closed sets; all values remain parameterized.

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import cast

import aiosqlite

from clearinghouse.application.core_store import (
    AccessIdentity,
    AccountSummary,
    AdminSummary,
    CoreTransaction,
    GlobalStop,
    StoredChallenge,
    StoredCredential,
    StoredOAuthFlow,
    StoredSession,
    UsageAggregate,
)
from clearinghouse.application.pagination import KeysetPage
from clearinghouse.domain.core import (
    AccountId,
    AuthorizationId,
    CapabilityOffer,
    CredentialId,
    ExactPrice,
    LifecycleStatus,
    PaymentAuthorization,
    PriceObservationId,
    UsageEvent,
    UsageEventId,
    UsageStatus,
    UserId,
    Workload,
    WorkloadId,
    WorkloadStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  is_admin INTEGER NOT NULL CHECK (is_admin IN (0, 1)),
  status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS otp_challenges (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  code_digest BLOB NOT NULL,
  expires_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);
CREATE TABLE IF NOT EXISTS browser_sessions (
  id TEXT PRIMARY KEY,
  token_digest BLOB NOT NULL UNIQUE,
  csrf_digest BLOB NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id),
  account_id TEXT NOT NULL REFERENCES accounts(id),
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_flows (
  provider TEXT NOT NULL,
  state_digest BLOB NOT NULL,
  verifier TEXT NOT NULL,
  nonce_digest BLOB,
  redirect_uri TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  PRIMARY KEY(provider, state_digest)
);
CREATE TABLE IF NOT EXISTS api_credentials (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  token_digest BLOB NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS price_observations (
  id TEXT PRIMARY KEY,
  orchestrator_url TEXT NOT NULL,
  orchestrator_address TEXT,
  capability TEXT NOT NULL,
  model TEXT,
  constraints_json TEXT NOT NULL,
  numerator INTEGER NOT NULL CHECK (numerator >= 0),
  denominator INTEGER NOT NULL CHECK (denominator > 0),
  currency TEXT NOT NULL,
  quantity_unit TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1))
);
CREATE TABLE IF NOT EXISTS workloads (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  capability TEXT NOT NULL,
  model TEXT,
  offer_id TEXT NOT NULL REFERENCES price_observations(id),
  price_numerator INTEGER NOT NULL,
  price_denominator INTEGER NOT NULL,
  price_currency TEXT NOT NULL,
  price_quantity_unit TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'ended', 'revoked')),
  token_digest BLOB NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  runner_session_id TEXT,
  manifest_id TEXT,
  payment_session_id TEXT,
  client_reference TEXT
);
CREATE TABLE IF NOT EXISTS authorizations (
  id TEXT PRIMARY KEY,
  workload_id TEXT NOT NULL REFERENCES workloads(id),
  signer_id TEXT NOT NULL,
  state_id TEXT NOT NULL,
  sequence_number INTEGER NOT NULL,
  orchestrator_address TEXT NOT NULL,
  price_numerator INTEGER NOT NULL,
  price_denominator INTEGER NOT NULL,
  price_currency TEXT NOT NULL,
  price_quantity_unit TEXT NOT NULL,
  authorized_at TEXT NOT NULL,
  UNIQUE(signer_id, state_id, sequence_number)
);
CREATE TABLE IF NOT EXISTS usage_events (
  id TEXT PRIMARY KEY,
  transport_event_id TEXT NOT NULL UNIQUE,
  authorization_id TEXT REFERENCES authorizations(id),
  account_id TEXT REFERENCES accounts(id),
  user_id TEXT REFERENCES users(id),
  workload_id TEXT REFERENCES workloads(id),
  signer_id TEXT NOT NULL,
  state_id TEXT NOT NULL,
  sequence_number INTEGER NOT NULL,
  manifest_id TEXT NOT NULL,
  payment_session_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity >= 0),
  quantity_unit TEXT NOT NULL,
  computed_fee INTEGER NOT NULL CHECK (computed_fee >= 0),
  currency TEXT NOT NULL,
  ticket_count INTEGER NOT NULL CHECK (ticket_count > 0),
  occurred_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('matched', 'unmatched'))
);
CREATE TABLE IF NOT EXISTS system_state (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  stop_enabled INTEGER NOT NULL CHECK (stop_enabled IN (0, 1)),
  stop_reason TEXT NOT NULL,
  changed_at TEXT NOT NULL
);
INSERT OR IGNORE INTO system_state VALUES (1, 0, 'Normal operation', '1970-01-01T00:00:00+00:00');
CREATE TABLE IF NOT EXISTS discovery_state (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  generation TEXT NOT NULL
);
INSERT OR IGNORE INTO discovery_state VALUES (1, 'empty');
CREATE INDEX IF NOT EXISTS ix_credentials_account_page
  ON api_credentials(account_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_workloads_account_created
  ON workloads(account_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_usage_account_occurred
  ON usage_events(account_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_offers_capability_expires
  ON price_observations(capability, expires_at);
CREATE INDEX IF NOT EXISTS ix_offers_page
  ON price_observations(is_current, capability, model, numerator, id);
"""


def _at(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("stored timestamps must be timezone-aware")
    return value.isoformat()


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _id(prefix: str, email: str) -> str:
    import hashlib

    return f"{prefix}_{hashlib.sha256(email.encode()).hexdigest()[:20]}"


class SqliteTransaction(CoreTransaction):
    """Operations executed by one SQLite transaction."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def _one(self, sql: str, values: Sequence[object]) -> aiosqlite.Row | None:
        cursor = await self.connection.execute(sql, values)
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()

    async def _all(self, sql: str, values: Sequence[object] = ()) -> list[aiosqlite.Row]:
        cursor = await self.connection.execute(sql, values)
        try:
            return list(await cursor.fetchall())
        finally:
            await cursor.close()

    async def put_challenge(self, challenge: StoredChallenge) -> None:
        await self.connection.execute(
            "INSERT OR REPLACE INTO otp_challenges VALUES (?, ?, ?, ?, ?)",
            (
                challenge.id,
                challenge.email,
                challenge.code_digest,
                _at(challenge.expires_at),
                challenge.attempts,
            ),
        )

    async def get_challenge(self, challenge_id: str) -> StoredChallenge | None:
        row = await self._one("SELECT * FROM otp_challenges WHERE id = ?", (challenge_id,))
        return (
            None
            if row is None
            else StoredChallenge(
                row["id"],
                row["email"],
                row["code_digest"],
                _time(row["expires_at"]),
                row["attempts"],
            )
        )

    async def increment_challenge_attempts(self, challenge_id: str) -> None:
        await self.connection.execute(
            "UPDATE otp_challenges SET attempts = attempts + 1 WHERE id = ?", (challenge_id,)
        )

    async def delete_challenge(self, challenge_id: str) -> None:
        await self.connection.execute("DELETE FROM otp_challenges WHERE id = ?", (challenge_id,))

    async def resolve_identity(self, email: str, *, admin_email: str | None) -> AccessIdentity:
        user_id = UserId(_id("usr", email))
        account_id = AccountId(_id("acct", email))
        is_admin = admin_email is not None and email == admin_email
        now = datetime.now().astimezone().isoformat()
        await self.connection.execute(
            "INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?)",
            (user_id, email, int(is_admin), LifecycleStatus.ACTIVE, now),
        )
        await self.connection.execute(
            "UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id)
        )
        await self.connection.execute(
            "INSERT OR IGNORE INTO accounts VALUES (?, ?, ?, ?, ?)",
            (account_id, user_id, f"{email}'s account", LifecycleStatus.ACTIVE, now),
        )
        return AccessIdentity(user_id, account_id, email, is_admin)

    async def list_identities(
        self, *, limit: int = 50, after: tuple[str, ...] = ()
    ) -> KeysetPage[AccessIdentity]:
        where = ""
        values: list[object] = []
        if after:
            if len(after) != 2:
                raise ValueError("identity cursor key is invalid")
            where = "WHERE (u.email, u.id) > (?, ?)"
            values.extend(after)
        rows = await self._all(
            f"""SELECT u.id AS user_id, a.id AS account_id, u.email, u.is_admin
                FROM users u JOIN accounts a ON a.owner_user_id = u.id {where}
                ORDER BY u.email ASC, u.id ASC LIMIT ?""",
            (*values, limit + 1),
        )
        items = tuple(
            AccessIdentity(
                UserId(row["user_id"]),
                AccountId(row["account_id"]),
                row["email"],
                bool(row["is_admin"]),
            )
            for row in rows[:limit]
        )
        key = (items[-1].email, str(items[-1].user_id)) if len(rows) > limit else None
        return KeysetPage(items, key)

    async def put_session(self, session: StoredSession) -> None:
        await self.connection.execute(
            "INSERT INTO browser_sessions VALUES (?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.token_digest,
                session.csrf_digest,
                session.identity.user_id,
                session.identity.account_id,
                _at(session.expires_at),
            ),
        )

    async def get_session(self, token_digest: bytes) -> StoredSession | None:
        row = await self._one(
            """SELECT s.*, u.email, u.is_admin FROM browser_sessions s
               JOIN users u ON u.id = s.user_id WHERE s.token_digest = ?""",
            (token_digest,),
        )
        if row is None:
            return None
        identity = AccessIdentity(
            UserId(row["user_id"]),
            AccountId(row["account_id"]),
            row["email"],
            bool(row["is_admin"]),
        )
        return StoredSession(
            row["id"], row["token_digest"], row["csrf_digest"], identity, _time(row["expires_at"])
        )

    async def delete_session(self, token_digest: bytes) -> None:
        await self.connection.execute(
            "DELETE FROM browser_sessions WHERE token_digest = ?", (token_digest,)
        )

    async def put_oauth_flow(self, flow: StoredOAuthFlow) -> None:
        await self.connection.execute(
            "INSERT INTO oauth_flows VALUES (?, ?, ?, ?, ?, ?)",
            (
                flow.provider,
                flow.state_digest,
                flow.verifier,
                flow.nonce_digest,
                flow.redirect_uri,
                _at(flow.expires_at),
            ),
        )

    async def pop_oauth_flow(
        self, provider: str, state_digest: bytes, *, now: datetime
    ) -> StoredOAuthFlow | None:
        row = await self._one(
            """SELECT * FROM oauth_flows
               WHERE provider = ? AND state_digest = ? AND expires_at > ?""",
            (provider, state_digest, _at(now)),
        )
        if row is None:
            return None
        await self.connection.execute(
            "DELETE FROM oauth_flows WHERE provider = ? AND state_digest = ?",
            (provider, state_digest),
        )
        return StoredOAuthFlow(
            row["provider"],
            row["state_digest"],
            row["verifier"],
            row["nonce_digest"],
            row["redirect_uri"],
            _time(row["expires_at"]),
        )

    async def put_credential(self, credential: StoredCredential) -> None:
        await self.connection.execute(
            "INSERT INTO api_credentials VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                credential.id,
                credential.account_id,
                credential.user_id,
                credential.name,
                credential.token_digest,
                _at(credential.created_at),
                None,
            ),
        )

    async def get_credential(self, token_digest: bytes) -> StoredCredential | None:
        row = await self._one(
            "SELECT * FROM api_credentials WHERE token_digest = ? AND revoked_at IS NULL",
            (token_digest,),
        )
        return None if row is None else self._credential(row)

    async def list_credentials(
        self, account_id: AccountId, *, limit: int = 50, after: tuple[str, ...] = ()
    ) -> KeysetPage[StoredCredential]:
        where = ""
        values: list[object] = [account_id]
        if after:
            if len(after) != 2:
                raise ValueError("credential cursor key is invalid")
            where = "AND (created_at, id) < (?, ?)"
            values.extend(after)
        rows = await self._all(
            f"""SELECT * FROM api_credentials WHERE account_id = ? {where}
                ORDER BY created_at DESC, id DESC LIMIT ?""",
            (*values, limit + 1),
        )
        items = tuple(self._credential(row) for row in rows[:limit])
        key = (_at(items[-1].created_at), str(items[-1].id)) if len(rows) > limit else None
        return KeysetPage(items, key)

    @staticmethod
    def _credential(row: aiosqlite.Row) -> StoredCredential:
        return StoredCredential(
            CredentialId(row["id"]),
            AccountId(row["account_id"]),
            UserId(row["user_id"]),
            row["name"],
            row["token_digest"],
            _time(row["created_at"]),
            _time(row["revoked_at"]) if row["revoked_at"] else None,
        )

    async def revoke_credential(
        self, account_id: AccountId, credential_id: CredentialId, at: datetime
    ) -> bool:
        cursor = await self.connection.execute(
            """UPDATE api_credentials SET revoked_at = ?
               WHERE id = ? AND account_id = ? AND revoked_at IS NULL""",
            (_at(at), credential_id, account_id),
        )
        return cursor.rowcount == 1

    async def replace_offers(self, offers: Sequence[CapabilityOffer]) -> None:
        if offers:
            incoming_sources = {
                dict(offer.constraints).get("orchestrator_url", offer.orchestrator_url)
                for offer in offers
            }
            current = await self._all(
                "SELECT id, orchestrator_url, constraints_json FROM price_observations "
                "WHERE is_current = 1"
            )
            replaced_ids = []
            for row in current:
                constraints = dict(json.loads(row["constraints_json"]))
                source = constraints.get("orchestrator_url", row["orchestrator_url"])
                if source in incoming_sources:
                    replaced_ids.append((row["id"],))
            if replaced_ids:
                await self.connection.executemany(
                    "UPDATE price_observations SET is_current = 0 WHERE id = ?", replaced_ids
                )
        for offer in offers:
            await self.connection.execute(
                """INSERT OR REPLACE INTO price_observations
                   (id, orchestrator_url, orchestrator_address, capability, model,
                    constraints_json, numerator, denominator, currency, quantity_unit,
                    observed_at, expires_at, is_current)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    offer.id,
                    offer.orchestrator_url,
                    offer.orchestrator_address,
                    offer.capability,
                    offer.model,
                    json.dumps(offer.constraints, separators=(",", ":")),
                    offer.price.numerator,
                    offer.price.denominator,
                    offer.price.currency,
                    offer.price.quantity_unit,
                    _at(offer.observed_at),
                    _at(offer.expires_at),
                ),
            )

        current = await self._all(
            "SELECT id FROM price_observations WHERE is_current = 1 ORDER BY id", ()
        )
        generation = hashlib.sha256("\0".join(row["id"] for row in current).encode()).hexdigest()[
            :24
        ]
        await self.connection.execute(
            "UPDATE discovery_state SET generation = ? WHERE singleton = 1", (generation,)
        )

    async def list_offers(
        self,
        *,
        now: datetime,
        limit: int = 50,
        after: tuple[str, ...] = (),
        capability: str | None = None,
        model: str | None = None,
    ) -> KeysetPage[CapabilityOffer]:
        clauses = ["is_current = 1", "expires_at > ?"]
        values: list[object] = [_at(now)]
        if capability is not None:
            clauses.append("capability = ?")
            values.append(capability)
        if model is not None:
            clauses.append("model = ?")
            values.append(model)
        if after:
            if len(after) != 4:
                raise ValueError("offer cursor key is invalid")
            clauses.append("(capability, COALESCE(model, ''), numerator, id) > (?, ?, ?, ?)")
            values.extend((after[0], after[1], int(after[2]), after[3]))
        rows = await self._all(
            f"""SELECT * FROM price_observations WHERE {" AND ".join(clauses)}
                ORDER BY capability ASC, COALESCE(model, '') ASC, numerator ASC, id ASC LIMIT ?""",
            (*values, limit + 1),
        )
        items = tuple(self._offer(row) for row in rows[:limit])
        key = (
            (
                items[-1].capability,
                items[-1].model or "",
                str(items[-1].price.numerator),
                str(items[-1].id),
            )
            if len(rows) > limit
            else None
        )
        return KeysetPage(items, key)

    async def offer_generation(self) -> str:
        row = await self._one("SELECT generation FROM discovery_state WHERE singleton = 1", ())
        return "empty" if row is None else cast(str, row["generation"])

    async def get_offer(self, offer_id: str, *, now: datetime) -> CapabilityOffer | None:
        row = await self._one(
            "SELECT * FROM price_observations WHERE id = ? AND expires_at > ?", (offer_id, _at(now))
        )
        return None if row is None else self._offer(row)

    @staticmethod
    def _offer(row: aiosqlite.Row) -> CapabilityOffer:
        return CapabilityOffer(
            PriceObservationId(row["id"]),
            row["orchestrator_url"],
            row["orchestrator_address"],
            row["capability"],
            row["model"],
            tuple(tuple(item) for item in json.loads(row["constraints_json"])),
            ExactPrice(row["numerator"], row["denominator"], row["currency"], row["quantity_unit"]),
            _time(row["observed_at"]),
            _time(row["expires_at"]),
        )

    async def put_workload(self, workload: Workload, token_digest: bytes) -> None:
        await self.connection.execute(
            """INSERT INTO workloads VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                workload.id,
                workload.account_id,
                workload.user_id,
                workload.capability,
                workload.model,
                workload.offer_id,
                workload.max_price.numerator,
                workload.max_price.denominator,
                workload.max_price.currency,
                workload.max_price.quantity_unit,
                workload.status,
                token_digest,
                _at(workload.expires_at),
                _at(workload.created_at),
                workload.runner_session_id,
                workload.manifest_id,
                workload.payment_session_id,
                workload.client_reference,
            ),
        )

    async def get_workload(self, workload_id: WorkloadId) -> Workload | None:
        row = await self._one("SELECT * FROM workloads WHERE id = ?", (workload_id,))
        return None if row is None else self._workload(row)

    async def get_workload_by_token(self, token_digest: bytes) -> Workload | None:
        row = await self._one("SELECT * FROM workloads WHERE token_digest = ?", (token_digest,))
        return None if row is None else self._workload(row)

    async def list_workloads(
        self,
        account_id: AccountId | None = None,
        *,
        limit: int = 50,
        after: tuple[str, ...] = (),
    ) -> KeysetPage[Workload]:
        sql = "SELECT * FROM workloads"
        clauses: list[str] = []
        values: list[object] = []
        if account_id is not None:
            clauses.append("account_id = ?")
            values.append(account_id)
        if after:
            if len(after) != 2:
                raise ValueError("workload cursor key is invalid")
            clauses.append("(created_at, id) < (?, ?)")
            values.extend(after)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = await self._all(  # noqa: S608 -- placeholder count follows bounded identifiers
            sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*values, limit + 1)
        )
        items = tuple(self._workload(row) for row in rows[:limit])
        key = (_at(items[-1].created_at), str(items[-1].id)) if len(rows) > limit else None
        return KeysetPage(items, key)

    async def usage_aggregates(
        self, workload_ids: Sequence[WorkloadId]
    ) -> Sequence[UsageAggregate]:
        if not workload_ids:
            return ()
        placeholders = ",".join("?" for _ in workload_ids)
        rows = await self._all(
            f"""SELECT workload_id, SUM(quantity) AS measured_quantity,
                       MIN(quantity_unit) AS measured_unit,
                       SUM(computed_fee) AS computed_fee, COUNT(*) AS event_count
                FROM usage_events
                WHERE status = 'matched' AND workload_id IN ({placeholders})
                GROUP BY workload_id""",
            tuple(workload_ids),
        )
        return tuple(
            UsageAggregate(
                WorkloadId(row["workload_id"]),
                row["measured_quantity"],
                row["measured_unit"],
                row["computed_fee"],
                row["event_count"],
            )
            for row in rows
        )

    @staticmethod
    def _workload(row: aiosqlite.Row) -> Workload:
        return Workload(
            WorkloadId(row["id"]),
            AccountId(row["account_id"]),
            UserId(row["user_id"]),
            row["capability"],
            row["model"],
            PriceObservationId(row["offer_id"]),
            ExactPrice(
                row["price_numerator"],
                row["price_denominator"],
                row["price_currency"],
                row["price_quantity_unit"],
            ),
            WorkloadStatus(row["status"]),
            _time(row["expires_at"]),
            _time(row["created_at"]),
            row["runner_session_id"],
            row["manifest_id"],
            row["payment_session_id"],
            row["client_reference"],
        )

    async def revoke_workload(self, workload_id: WorkloadId, *, at: datetime) -> bool:
        del at
        cursor = await self.connection.execute(
            "UPDATE workloads SET status = 'revoked' WHERE id = ? AND status = 'active'",
            (workload_id,),
        )
        return cursor.rowcount == 1

    async def bind_workload(
        self,
        workload_id: WorkloadId,
        *,
        state_id: str,
        manifest_id: str | None,
        payment_session_id: str | None,
    ) -> None:
        await self.connection.execute(
            """UPDATE workloads SET runner_session_id = COALESCE(runner_session_id, ?),
               manifest_id = COALESCE(manifest_id, ?),
               payment_session_id = COALESCE(payment_session_id, ?) WHERE id = ?""",
            (state_id, manifest_id, payment_session_id, workload_id),
        )

    async def put_authorization(self, authorization: PaymentAuthorization) -> bool:
        price = authorization.advertised_price
        cursor = await self.connection.execute(
            """INSERT OR IGNORE INTO authorizations VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                authorization.id,
                authorization.workload_id,
                authorization.signer_id,
                authorization.state_id,
                authorization.sequence_number,
                authorization.orchestrator_address,
                price.numerator,
                price.denominator,
                price.currency,
                price.quantity_unit,
                _at(authorization.authorized_at),
            ),
        )
        return cursor.rowcount == 1

    async def get_authorization(
        self, authorization_id: AuthorizationId
    ) -> PaymentAuthorization | None:
        row = await self._one("SELECT * FROM authorizations WHERE id = ?", (authorization_id,))
        if row is None:
            return None
        return PaymentAuthorization(
            AuthorizationId(row["id"]),
            WorkloadId(row["workload_id"]),
            row["signer_id"],
            row["state_id"],
            row["sequence_number"],
            row["orchestrator_address"],
            ExactPrice(
                row["price_numerator"],
                row["price_denominator"],
                row["price_currency"],
                row["price_quantity_unit"],
            ),
            _time(row["authorized_at"]),
        )

    async def put_usage(self, usage: UsageEvent) -> bool:
        cursor = await self.connection.execute(
            """INSERT OR IGNORE INTO usage_events VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                usage.id,
                usage.transport_event_id,
                usage.authorization_id,
                usage.account_id,
                usage.user_id,
                usage.workload_id,
                usage.signer_id,
                usage.state_id,
                usage.sequence_number,
                usage.manifest_id,
                usage.payment_session_id,
                usage.capability,
                usage.quantity,
                usage.quantity_unit,
                usage.computed_fee,
                usage.currency,
                usage.ticket_count,
                _at(usage.occurred_at),
                usage.status,
            ),
        )
        return cursor.rowcount == 1

    async def list_usage(
        self,
        account_id: AccountId | None = None,
        *,
        limit: int = 50,
        after: tuple[str, ...] = (),
    ) -> KeysetPage[UsageEvent]:
        sql = "SELECT * FROM usage_events"
        clauses: list[str] = []
        values: list[object] = []
        if account_id is not None:
            clauses.append("account_id = ?")
            values.append(account_id)
        if after:
            if len(after) != 2:
                raise ValueError("usage cursor key is invalid")
            clauses.append("(occurred_at, id) < (?, ?)")
            values.extend(after)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = await self._all(
            sql + " ORDER BY occurred_at DESC, id DESC LIMIT ?", (*values, limit + 1)
        )
        items = tuple(self._usage(row) for row in rows[:limit])
        key = (_at(items[-1].occurred_at), str(items[-1].id)) if len(rows) > limit else None
        return KeysetPage(items, key)

    async def account_summary(self, account_id: AccountId, *, now: datetime) -> AccountSummary:
        row = await self._one(
            """SELECT
                 (SELECT COUNT(*) FROM price_observations
                    WHERE is_current = 1 AND expires_at > ?) AS offers,
                 (SELECT COUNT(*) FROM api_credentials WHERE account_id = ?) AS credentials,
                 (SELECT COUNT(*) FROM workloads WHERE account_id = ?) AS workloads,
                 (SELECT COUNT(*) FROM workloads WHERE account_id = ?
                    AND status = 'active' AND expires_at > ?) AS active_workloads,
                 (SELECT COUNT(*) FROM usage_events WHERE account_id = ?) AS usage_events,
                 (SELECT COALESCE(SUM(computed_fee), 0) FROM usage_events
                    WHERE account_id = ?) AS computed_fee""",
            (_at(now), account_id, account_id, account_id, _at(now), account_id, account_id),
        )
        if row is None:
            raise RuntimeError("account summary query returned no row")
        return AccountSummary(*(int(row[name]) for name in AccountSummary.__dataclass_fields__))

    async def admin_summary(self, *, now: datetime) -> AdminSummary:
        row = await self._one(
            """SELECT
                 (SELECT COUNT(*) FROM users) AS users,
                 (SELECT COUNT(*) FROM workloads) AS workloads,
                 (SELECT COUNT(*) FROM workloads
                    WHERE status = 'active' AND expires_at > ?) AS active_workloads,
                 (SELECT COUNT(*) FROM usage_events) AS usage_events,
                 (SELECT COUNT(*) FROM usage_events WHERE status = 'unmatched') AS unmatched_usage,
                 (SELECT COALESCE(SUM(computed_fee), 0) FROM usage_events) AS computed_fee""",
            (_at(now),),
        )
        if row is None:
            raise RuntimeError("admin summary query returned no row")
        return AdminSummary(*(int(row[name]) for name in AdminSummary.__dataclass_fields__))

    @staticmethod
    def _usage(row: aiosqlite.Row) -> UsageEvent:
        return UsageEvent(
            UsageEventId(row["id"]),
            row["transport_event_id"],
            AuthorizationId(row["authorization_id"]) if row["authorization_id"] else None,
            AccountId(row["account_id"]) if row["account_id"] else None,
            UserId(row["user_id"]) if row["user_id"] else None,
            WorkloadId(row["workload_id"]) if row["workload_id"] else None,
            row["signer_id"],
            row["state_id"],
            row["sequence_number"],
            row["manifest_id"],
            row["payment_session_id"],
            row["capability"],
            row["quantity"],
            row["quantity_unit"],
            row["computed_fee"],
            row["currency"],
            row["ticket_count"],
            _time(row["occurred_at"]),
            UsageStatus(row["status"]),
        )

    async def get_global_stop(self) -> GlobalStop:
        row = await self._one("SELECT * FROM system_state WHERE singleton = 1", ())
        if row is None:
            raise RuntimeError("system state is missing")
        return GlobalStop(bool(row["stop_enabled"]), row["stop_reason"], _time(row["changed_at"]))

    async def set_global_stop(self, value: GlobalStop) -> None:
        await self.connection.execute(
            """UPDATE system_state SET stop_enabled = ?, stop_reason = ?, changed_at = ?
               WHERE singleton = 1""",
            (int(value.enabled), value.reason, _at(value.changed_at)),
        )


class SqliteStore:
    """Local, persistent store with one supervised write boundary."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        self._writer = asyncio.Lock()
        self._closed = False

    async def _connect(self) -> aiosqlite.Connection:
        if self._closed:
            raise RuntimeError("store is closed")
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms:d}")
        return connection

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._writer:
            connection = await self._connect()
            try:
                cursor = await connection.execute("PRAGMA journal_mode = WAL")
                await cursor.fetchone()
                await cursor.close()
                await connection.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA}\nCOMMIT;")
                columns = await connection.execute("PRAGMA table_info(price_observations)")
                column_names = {row[1] for row in await columns.fetchall()}
                await columns.close()
                if "is_current" not in column_names:
                    await connection.execute(
                        """ALTER TABLE price_observations
                           ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1
                           CHECK (is_current IN (0, 1))"""
                    )
                    await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
            finally:
                await connection.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[CoreTransaction]:
        async with self._writer:
            connection = await self._connect()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                yield SqliteTransaction(connection)
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
            finally:
                await connection.close()

    async def readiness(self) -> bool:
        try:
            connection = await self._connect()
            try:
                row = await (await connection.execute("PRAGMA quick_check")).fetchone()
                return row is not None and cast(str, row[0]) == "ok"
            finally:
                await connection.close()
        except aiosqlite.Error, RuntimeError:
            return False

    async def close(self) -> None:
        self._closed = True
