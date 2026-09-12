"""Read-only signer preflight. Never funds, unlocks, signs, or prints secrets."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from aiokafka.admin import AIOKafkaAdminClient  # type: ignore[import-untyped]
from aiokafka.admin.config_resource import (  # type: ignore[import-untyped]
    ConfigResource,
    ConfigResourceType,
)

CONTROLLER = "0xD8E8328501E9645d16Cf49539efC04f734606ee4"
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
IMAGE = (
    "livepeer/go-livepeer@sha256:972870bed3f302ee69ada1e719d7767274a77d2e752be1ab82581ddf2365dfe7"
)
REPOSITORY = Path(__file__).resolve().parents[2]
MINIMUM_RETENTION_MS = 604_800_000


class InvalidConfiguration(ValueError):
    """An actionable error containing names and instructions, never secret values."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidConfiguration(message)


def validate_topic(
    description: dict[str, object], configs: dict[str, str | None], retention_ms: int
) -> None:
    partitions = description.get("partitions")
    require(
        isinstance(partitions, list) and len(partitions) == 1,
        "signer topic must have exactly one partition",
    )
    partition = partitions[0] if isinstance(partitions, list) else None
    replicas = partition.get("replicas") if isinstance(partition, dict) else None
    require(
        isinstance(replicas, list) and len(replicas) == 1,
        "reference signer topic must have replication factor one",
    )
    require(configs.get("cleanup.policy") == "delete", "cleanup.policy must be exactly delete")
    require(
        configs.get("retention.ms") == str(retention_ms),
        "signer topic retention.ms does not match configured policy",
    )


def read_env(path: Path) -> dict[str, str]:
    """Read literal KEY=value lines; never execute shell or expand substitutions."""
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(
            bool(separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key)),
            f"environment line {number}: expected literal KEY=value",
        )
        if value[:1] in {"'", '"'}:
            require(
                len(value) >= 2 and value[-1] == value[0],
                f"environment line {number}: mismatched quotes",
            )
            value = value[1:-1]
        values[key] = value
    return values


def compose_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Translate public clearinghouse names to the signer's private contract."""
    resolved = dict(env)
    aliases = {
        "WEBHOOK_SECRET": "CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET",
        "KAFKA_GATEWAY_TOPIC": "CLEARINGHOUSE_KAFKA_METERING_TOPIC",
        "KAFKA_RETENTION_MS": "CLEARINGHOUSE_KAFKA_RETENTION_MS",
    }
    defaults = {
        "KAFKA_BROKERS": "redpanda:9092",
        "REMOTE_SIGNER_WEBHOOK_URL": "http://core:8000/v1/compat/go-livepeer/authorize",
    }
    for target, source in aliases.items():
        if not resolved.get(target):
            resolved[target] = resolved.get(source, "")
    for target, default in defaults.items():
        if not resolved.get(target):
            resolved[target] = default
    secret_path = resolved.get("WEBHOOK_SECRET_FILE") or resolved.get(
        "CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET_HOST_FILE", ""
    )
    if not resolved.get("WEBHOOK_SECRET") and secret_path:
        path = Path(secret_path)
        require(path.is_file() and path.stat().st_size > 0, "signer webhook secret file is missing")
        resolved["WEBHOOK_SECRET"] = path.read_text().strip()
    return resolved


def positive(env: Mapping[str, str], key: str) -> int:
    value = env.get(key, "")
    require(bool(re.fullmatch(r"[1-9][0-9]*", value)), f"set {key} to a positive integer")
    return int(value)


def url(value: str, label: str) -> None:
    parsed = urlsplit(value)
    require(
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment,
        f"{label} requires an HTTP(S) URL without userinfo or fragment",
    )


def validate_orchestrator_addresses(env: Mapping[str, str]) -> None:
    """Validate go-livepeer's comma-separated static discovery source."""
    value = env.get("SIGNER_ORCH_ADDR", "")
    if not value:
        return
    require(
        env.get("SIGNER_REMOTE_DISCOVERY", "true") == "true",
        "SIGNER_ORCH_ADDR requires SIGNER_REMOTE_DISCOVERY=true",
    )
    require(len(value) <= 8192, "SIGNER_ORCH_ADDR must not exceed 8192 characters")
    entries = value.split(",")
    require(
        1 <= len(entries) <= 256 and all(entries),
        "SIGNER_ORCH_ADDR requires 1..256 comma-separated service addresses",
    )
    for entry in entries:
        require(
            entry == entry.strip() and not any(character.isspace() for character in entry),
            "SIGNER_ORCH_ADDR entries must not contain whitespace",
        )
        candidate = entry if "://" in entry else "https://" + entry
        parsed = urlsplit(candidate)
        require(
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and bool(re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname or ""))
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment,
            "SIGNER_ORCH_ADDR entries require DNS or IPv4 HTTP(S) service addresses "
            "without credentials, paths, queries, or fragments",
        )
        try:
            port = parsed.port
        except ValueError as error:
            raise InvalidConfiguration(
                "SIGNER_ORCH_ADDR entries require ports in the range 1..65535"
            ) from error
        require(
            port is not None and 1 <= port <= 65535,
            "SIGNER_ORCH_ADDR entries require an explicit port in the range 1..65535",
        )
        if env.get("SIGNER_MODE") == "production":
            require(
                parsed.scheme == "https",
                "production SIGNER_ORCH_ADDR entries must use HTTPS",
            )


def validate(env: Mapping[str, str]) -> None:
    require(
        env.get("SIGNER_MODE") in {"evaluation", "production"},
        "SIGNER_MODE must be evaluation or production; test mode uses fixtures",
    )
    network = env.get("SIGNER_NETWORK")
    require(network in {"arbitrum-one-mainnet", "custom"}, "unsupported SIGNER_NETWORK")
    chain = positive(env, "SIGNER_CHAIN_ID")
    if network == "arbitrum-one-mainnet":
        require(
            chain == 42161 and env.get("SIGNER_CONTROLLER") == CONTROLLER,
            "Arbitrum requires chain 42161 and the documented controller",
        )
    if env["SIGNER_MODE"] == "production":
        require(network == "arbitrum-one-mainnet", "production requires arbitrum-one-mainnet")
    for name in ("SIGNER_ETH_ADDR", "SIGNER_CONTROLLER"):
        value = env.get(name, "")
        require(
            bool(ADDRESS.fullmatch(value)) and int(value[2:], 16) != 0,
            f"{name} requires a nonzero Ethereum address",
        )
    url(env.get("ETH_RPC_URL", ""), "ETH_RPC_URL")
    url(env.get("REMOTE_SIGNER_WEBHOOK_URL", ""), "REMOTE_SIGNER_WEBHOOK_URL")
    require(
        not urlsplit(env["REMOTE_SIGNER_WEBHOOK_URL"]).query,
        "webhook URL must not contain a query; use WEBHOOK_SECRET",
    )
    require(
        urlsplit(env["REMOTE_SIGNER_WEBHOOK_URL"]).path == "/v1/compat/go-livepeer/authorize",
        "REMOTE_SIGNER_WEBHOOK_URL must use the compatibility route",
    )
    require(
        bool(re.fullmatch(r"[A-Za-z0-9_-]{32,}", env.get("WEBHOOK_SECRET", ""))),
        "WEBHOOK_SECRET requires at least 32 URL-safe characters",
    )
    signer_port = positive({"SIGNER_PORT": env.get("SIGNER_PORT", "8935")}, "SIGNER_PORT")
    require(
        1024 <= signer_port <= 65535 and signer_port != 4935,
        "SIGNER_PORT must be 1024..65535, except 4935",
    )
    require(
        bool(re.fullmatch(r"[A-Za-z0-9.-]+:[0-9]{1,5}", env.get("KAFKA_BROKERS", ""))),
        "KAFKA_BROKERS requires one DNS hostname:port",
    )
    require(
        1 <= int(env["KAFKA_BROKERS"].rsplit(":", 1)[1]) <= 65535,
        "KAFKA_BROKERS port must be 1..65535",
    )
    require(
        bool(re.fullmatch(r"[A-Za-z0-9._-]{1,249}", env.get("KAFKA_GATEWAY_TOPIC", "")))
        and env["KAFKA_GATEWAY_TOPIC"] not in {".", ".."},
        "KAFKA_GATEWAY_TOPIC requires a valid topic name",
    )
    require(
        bool(env.get("LP_KAFKAUSER")) == bool(env.get("LP_KAFKAPASSWORD")),
        "configure both LP_KAFKAUSER and LP_KAFKAPASSWORD or neither",
    )
    require(
        env.get("SIGNER_REMOTE_DISCOVERY", "true") in {"true", "false"},
        "SIGNER_REMOTE_DISCOVERY must be true or false",
    )
    validate_orchestrator_addresses(env)
    for name in ("SIGNER_MIN_GAS_WEI", "SIGNER_MIN_DEPOSIT_WEI", "SIGNER_MIN_RESERVE_WEI"):
        positive(env, name)
    require(
        positive(env, "KAFKA_RETENTION_MS") >= MINIMUM_RETENTION_MS,
        "KAFKA_RETENTION_MS must be at least seven days",
    )
    keyfile = Path(env.get("SIGNER_KEYSTORE_HOST_FILE") or env.get("SIGNER_ETH_KEYSTORE_PATH", ""))
    password = Path(env.get("SIGNER_PASSWORD_HOST_FILE") or env.get("SIGNER_PASSWORD_FILE", ""))
    require(keyfile.is_file(), "mount SIGNER_KEYSTORE_HOST_FILE as an encrypted V3 JSON file")
    require(password.is_file(), "mount SIGNER_PASSWORD_HOST_FILE as a password file")
    for path, label in ((keyfile, "keystore"), (password, "password")):
        require(
            not path.resolve().is_relative_to(REPOSITORY),
            f"{label} path must be outside the repository build context",
        )
    require(bool(password.read_bytes().strip()), "keystore password must not be empty")
    for path, label in ((keyfile, "keystore"), (password, "password")):
        require(
            stat.S_IMODE(path.stat().st_mode) in {0o400, 0o440, 0o600, 0o640},
            f"{label} file mode must be 0400, 0440, 0600, or 0640",
        )
    try:
        key = json.loads(keyfile.read_text())
    except (ValueError, UnicodeError) as error:
        raise InvalidConfiguration("keystore must be encrypted Ethereum V3 JSON") from error
    require(isinstance(key, dict), "keystore must be a JSON object")
    crypto = key.get("crypto", key.get("Crypto", {}))
    require(
        key.get("version") == 3
        and isinstance(crypto, dict)
        and crypto.get("cipher") == "aes-128-ctr"
        and bool(crypto.get("ciphertext"))
        and crypto.get("kdf") in {"scrypt", "pbkdf2"},
        "keystore requires Ethereum V3 encrypted crypto fields",
    )
    require(
        str(key.get("address", "")).lower() == env["SIGNER_ETH_ADDR"][2:].lower(),
        "keystore address does not match SIGNER_ETH_ADDR",
    )


def request_json(
    endpoint: str, payload: object | None = None, headers: Mapping[str, str] | None = None
) -> object:
    url(endpoint, "dependency endpoint")
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(  # noqa: S310 — HTTP(S) scheme validated above.
        endpoint,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "go-ethereum/rpc",
            **(headers or {}),
        },
    )
    with urlopen(request, timeout=6) as response:  # noqa: S310 — validated HTTP(S) request.
        body = response.read(1_048_577)
    require(len(body) <= 1_048_576, "dependency response exceeded 1 MiB")
    return json.loads(body)


def rpc(env: Mapping[str, str], method: str, params: list[object]) -> object:
    response = request_json(
        env["ETH_RPC_URL"], {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    if not isinstance(response, dict) or "result" not in response or "error" in response:
        raise InvalidConfiguration(f"RPC {method} failed; verify provider permissions and network")
    result: object = response["result"]
    return result


def check_rpc(env: Mapping[str, str]) -> None:
    require(
        int(str(rpc(env, "eth_chainId", [])), 16) == positive(env, "SIGNER_CHAIN_ID"),
        "RPC chain mismatch; correct ETH_RPC_URL before startup",
    )
    require(
        rpc(env, "eth_getCode", [env["SIGNER_CONTROLLER"], "latest"]) not in {"0x", "0x0", "0x00"},
        "no controller bytecode on the selected chain",
    )
    require(
        int(str(rpc(env, "eth_getBalance", [env["SIGNER_ETH_ADDR"], "latest"])), 16)
        >= positive(env, "SIGNER_MIN_GAS_WEI"),
        "signer gas balance is below SIGNER_MIN_GAS_WEI",
    )
    require(int(str(rpc(env, "eth_blockNumber", [])), 16) > 0, "RPC has no current block")


def check_admin(env: Mapping[str, str], endpoint: str) -> None:
    """Run in the signer network namespace; never publish this endpoint."""
    parsed = urlsplit(endpoint)
    require(
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.port == 4935
        and parsed.path in {"", "/"}
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment,
        "admin URL must be loopback HTTP port 4935",
    )
    # Scheme, loopback host, port and absence of credentials were checked above.
    with urlopen(endpoint.rstrip("/") + "/ethAddr", timeout=6) as response:  # noqa: S310
        address = response.read(128).decode().strip().lower().removeprefix("0x")
    require(
        address == env["SIGNER_ETH_ADDR"].lower().removeprefix("0x"),
        "running signer address mismatch",
    )
    require(
        int(str(request_json(endpoint.rstrip("/") + "/EthChainID")))
        == positive(env, "SIGNER_CHAIN_ID"),
        "running signer chain mismatch",
    )
    sender = request_json(endpoint.rstrip("/") + "/senderInfo")
    if not isinstance(sender, dict) or not isinstance(sender.get("Reserve"), dict):
        raise InvalidConfiguration("invalid senderInfo response")
    require(
        int(sender["Deposit"]) >= positive(env, "SIGNER_MIN_DEPOSIT_WEI"),
        "TicketBroker deposit is below SIGNER_MIN_DEPOSIT_WEI",
    )
    require(
        int(sender["Reserve"]["FundsRemaining"]) >= positive(env, "SIGNER_MIN_RESERVE_WEI"),
        "TicketBroker reserve is below SIGNER_MIN_RESERVE_WEI",
    )
    require(
        int(sender["WithdrawRound"]) == 0,
        "TicketBroker withdrawal is pending; resolve before admission",
    )


def check_webhook(env: Mapping[str, str]) -> None:
    """Authenticate the service, but intentionally supply no usable user credential."""
    state = {
        "StateID": "preflight_no_signing",
        "PMSessionID": "",
        "LastUpdate": datetime.now(UTC).isoformat(),
        "OrchestratorAddress": "0x" + "1" * 40,
        "App": "preflight",
        "AuthExpiry": 0,
        "SenderNonce": 0,
        "Balance": "0",
        "InitialPricePerUnit": 1,
        "InitialPixelsPerUnit": 1,
        "Type": "fixed",
        "SequenceNumber": 0,
        "AuthID": "",
    }
    response = request_json(
        env["REMOTE_SIGNER_WEBHOOK_URL"],
        {
            "headers": {"Authorization": ["Bearer preflight_invalid_session_credential"]},
            "state": state,
        },
        {"Authorization": "Bearer " + env["WEBHOOK_SECRET"]},
    )
    require(
        isinstance(response, dict)
        and response.get("status") in {401, 402, 403}
        and response.get("expiry", 0) == 0
        and bool(response.get("reason")),
        "webhook must return HTTP 200 with a typed deny for an invalid session, "
        "without cached authorization",
    )


async def check_broker(env: Mapping[str, str]) -> None:
    """Require broker metadata access and the configured signer topic."""
    admin = AIOKafkaAdminClient(
        bootstrap_servers=env["KAFKA_BROKERS"],
        request_timeout_ms=6_000,
        client_id="clearinghouse-signer-preflight",
    )
    try:
        await admin.start()
        topics = await admin.list_topics()
        descriptions = await admin.describe_topics([env["KAFKA_GATEWAY_TOPIC"]])
        resources = await admin.describe_configs(
            [ConfigResource(ConfigResourceType.TOPIC, env["KAFKA_GATEWAY_TOPIC"])]
        )
    finally:
        await admin.close()
    require(env["KAFKA_GATEWAY_TOPIC"] in topics, "configured signer topic is missing")
    require(len(descriptions) == 1, "broker returned ambiguous signer topic metadata")
    require(
        len(resources) == 1 and len(resources[0].resources) == 1,
        "broker returned ambiguous signer topic configuration",
    )
    resource = resources[0].resources[0]
    require(resource[0] == 0, "broker rejected signer topic configuration lookup")
    validate_topic(
        descriptions[0],
        {entry[0]: entry[1] for entry in resource[4]},
        positive(env, "KAFKA_RETENTION_MS"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--rpc", action="store_true", help="check chain, controller code, gas and block access"
    )
    parser.add_argument(
        "--webhook",
        action="store_true",
        help="verify authenticated compatibility endpoint denies an invalid session",
    )
    parser.add_argument(
        "--admin-url",
        help="check signer address, chain, deposit/reserve from its private network namespace",
    )
    parser.add_argument(
        "--broker", action="store_true", help="check broker metadata and configured topic"
    )
    args = parser.parse_args(argv)
    try:
        env = dict(os.environ)
        if args.env_file:
            env.update(read_env(args.env_file))
        env = compose_environment(env)
        validate(env)
        if args.rpc:
            check_rpc(env)
        if args.admin_url:
            check_admin(env, args.admin_url)
        if args.webhook:
            check_webhook(env)
        if args.broker:
            asyncio.run(check_broker(env))
    except InvalidConfiguration as error:
        print(f"signer preflight: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as _error:
        # Underlying exceptions can include RPC URLs, tokens, paths or raw bodies.
        print(
            "signer preflight: dependency or file check failed; "
            "check mounts, endpoint reachability and credentials",
            file=sys.stderr,
        )
        return 1
    print(
        "signer preflight passed requested read-only checks; "
        "an actual funded signing request remains a separate operator gate"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
