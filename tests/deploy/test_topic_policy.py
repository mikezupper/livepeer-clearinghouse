from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("topic_policy", ROOT / "deploy/topic_policy.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("topic policy module is missing")
topic_policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(topic_policy)


class TopicPolicyTests(unittest.TestCase):
    def test_exact_single_partition_delete_policy(self) -> None:
        topic_policy.validate_topic(
            {"partitions": [{"partition": 0, "replicas": [0]}]},
            {"cleanup.policy": "delete", "retention.ms": "604800000"},
            604_800_000,
        )

    def test_incompatible_existing_topic_is_refused(self) -> None:
        cases = (
            (
                {"partitions": [{"partition": 0, "replicas": [0]}, {"partition": 1}]},
                {"cleanup.policy": "delete", "retention.ms": "604800000"},
                "exactly one partition",
            ),
            (
                {"partitions": [{"partition": 0, "replicas": [0]}]},
                {"cleanup.policy": "compact,delete", "retention.ms": "604800000"},
                "cleanup.policy",
            ),
            (
                {"partitions": [{"partition": 0, "replicas": [0]}]},
                {"cleanup.policy": "delete", "retention.ms": "1"},
                "retention.ms",
            ),
        )
        for description, configs, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(topic_policy.InvalidTopicPolicy, message),
            ):
                topic_policy.validate_topic(description, configs, 604_800_000)


if __name__ == "__main__":
    unittest.main()
