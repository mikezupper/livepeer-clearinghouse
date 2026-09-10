"""Create the reference signer topic or reject an incompatible existing topic."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys

from aiokafka.admin import AIOKafkaAdminClient, NewTopic  # type: ignore[import-untyped]
from aiokafka.admin.config_resource import (  # type: ignore[import-untyped]
    ConfigResource,
    ConfigResourceType,
)
from aiokafka.errors import TopicAlreadyExistsError  # type: ignore[import-untyped]

MINIMUM_RETENTION_MS = 604_800_000


class InvalidTopicPolicy(ValueError):
    """The existing topic cannot safely satisfy the signer trust contract."""


def validate_topic(
    description: dict[str, object], configs: dict[str, str | None], retention_ms: int
) -> None:
    partitions = description.get("partitions")
    if not isinstance(partitions, list) or len(partitions) != 1:
        raise InvalidTopicPolicy("signer topic must have exactly one partition")
    replicas = partitions[0].get("replicas") if isinstance(partitions[0], dict) else None
    if not isinstance(replicas, list) or len(replicas) != 1:
        raise InvalidTopicPolicy("reference signer topic must have replication factor one")
    if configs.get("cleanup.policy") != "delete":
        raise InvalidTopicPolicy("signer topic cleanup.policy must be exactly delete")
    if configs.get("retention.ms") != str(retention_ms):
        raise InvalidTopicPolicy("signer topic retention.ms does not match configured policy")


async def ensure_topic(brokers: str, topic: str, retention_ms: int) -> None:
    if retention_ms < MINIMUM_RETENTION_MS:
        raise InvalidTopicPolicy("signer topic retention must be at least seven days")
    admin = AIOKafkaAdminClient(
        bootstrap_servers=brokers,
        request_timeout_ms=10_000,
        client_id="clearinghouse-topic-policy",
    )
    await admin.start()
    try:
        with contextlib.suppress(TopicAlreadyExistsError):
            await admin.create_topics(
                [
                    NewTopic(
                        topic,
                        num_partitions=1,
                        replication_factor=1,
                        topic_configs={
                            "cleanup.policy": "delete",
                            "retention.ms": str(retention_ms),
                        },
                    )
                ]
            )
        descriptions = await admin.describe_topics([topic])
        if len(descriptions) != 1 or descriptions[0].get("topic") != topic:
            raise InvalidTopicPolicy("broker did not describe the configured signer topic")
        responses = await admin.describe_configs([ConfigResource(ConfigResourceType.TOPIC, topic)])
        if len(responses) != 1 or len(responses[0].resources) != 1:
            raise InvalidTopicPolicy("broker did not return signer topic configuration")
        resource = responses[0].resources[0]
        if resource[0] != 0 or resource[3] != topic:
            raise InvalidTopicPolicy("broker rejected signer topic configuration lookup")
        configs = {entry[0]: entry[1] for entry in resource[4]}
        validate_topic(descriptions[0], configs, retention_ms)
    finally:
        await admin.close()


def main() -> int:
    try:
        retention = int(os.environ["RETENTION_MS"])
        asyncio.run(
            ensure_topic(os.environ["KAFKA_BROKERS"], os.environ["METERING_TOPIC"], retention)
        )
    except (InvalidTopicPolicy, KeyError, OSError, ValueError) as error:
        print(f"topic policy: {error}", file=sys.stderr)
        return 1
    print("signer topic policy verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
