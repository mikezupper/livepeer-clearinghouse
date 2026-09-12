from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_supply_chain import (
    DEPENDABOT_DOCKER_DIRECTORIES,
    RELEASE_COMPONENTS,
    validate,
)
from scripts.verify_release_descriptors import EXPECTED, validate_descriptors


class SupplyChainDistributionTests(unittest.TestCase):
    def test_repository_workflows_match_the_three_image_distribution(self) -> None:
        root = Path(__file__).resolve().parents[2]

        self.assertEqual(RELEASE_COMPONENTS, EXPECTED)
        self.assertEqual(DEPENDABOT_DOCKER_DIRECTORIES, {"/deploy", "/deploy/signer"})
        self.assertEqual(validate(root), [])

    def test_policy_rejects_an_extra_release_image(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            workflow_directory = fixture / ".github" / "workflows"
            workflow_directory.mkdir(parents=True)
            (fixture / ".github" / "dependabot.yml").write_text(
                (root / ".github" / "dependabot.yml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            for name in ("ci.yml", "security.yml", "scorecard.yml", "release.yml"):
                source = (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
                if name == "release.yml":
                    marker = "          - component: edge\n"
                    source = source.replace(
                        marker,
                        "          - component: legacy-web\n"
                        "            image: ghcr.io/livepeer/clearinghouse-legacy-web\n"
                        "            dockerfile: deploy/edge.Dockerfile\n"
                        '            build_args: ""\n' + marker,
                        1,
                    )
                (workflow_directory / name).write_text(source, encoding="utf-8")

            errors = validate(fixture)

        self.assertTrue(any("exactly the canonical" in error for error in errors), errors)

    def test_descriptors_accept_only_the_complete_canonical_set(self) -> None:
        revision = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for index, (component, image) in enumerate(EXPECTED.items()):
                (directory / f"{component}.image.json").write_text(
                    json.dumps(
                        {
                            "component": component,
                            "image": image,
                            "digest": f"sha256:{index:064x}",
                            "revision": revision,
                            "version": "1.2.3",
                        }
                    ),
                    encoding="utf-8",
                )

            self.assertEqual(
                validate_descriptors(directory, version="1.2.3", revision=revision), []
            )
            (directory / "legacy-web.image.json").write_text("{}", encoding="utf-8")
            errors = validate_descriptors(directory, version="1.2.3", revision=revision)

        self.assertTrue(any("unexpected=['legacy-web.image.json']" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
