from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.release_assets import build_contract_bundle
from scripts.release_guard import CANONICAL_REPOSITORY, validate_release
from scripts.validate_supply_chain import validate
from scripts.verify_release_descriptors import EXPECTED, validate_descriptors


class SupplyChainWorkflowTests(unittest.TestCase):
    def make_workflow_fixture(
        self, root: Path, *, changed_name: str, changed_source: str
    ) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = Path(temporary.name) / ".github" / "workflows"
        fixture.mkdir(parents=True)
        for name in ("ci.yml", "security.yml", "scorecard.yml", "release.yml"):
            source = (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
            (fixture / name).write_text(
                changed_source if name == changed_name else source, encoding="utf-8"
            )
        return temporary

    def test_repository_workflows_satisfy_policy(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(validate(root), [])

    def test_policy_rejects_mutable_action_and_pull_request_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            for name in ("ci.yml", "scorecard.yml", "release.yml"):
                (workflows / name).write_text(
                    "permissions:\n  contents: read\njobs: {}\n", encoding="utf-8"
                )
            (workflows / "security.yml").write_text(
                "on:\n  pull_request:\npermissions:\n  contents: read\n"
                "jobs:\n  unsafe:\n    steps:\n      - uses: actions/checkout@v7\n"
                "      - run: echo '${{ secrets.UNSAFE }}'\n",
                encoding="utf-8",
            )
            errors = validate(root)
        self.assertTrue(any("not pinned to a full SHA" in error for error in errors))
        self.assertTrue(any("must not consume secrets" in error for error in errors))

    def test_policy_requires_existing_release_guard_before_publish_mutations(self) -> None:
        root = Path(__file__).resolve().parents[2]
        release_path = root / ".github" / "workflows" / "release.yml"
        source = release_path.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            workflows = fixture / ".github" / "workflows"
            workflows.mkdir(parents=True)
            for name in ("ci.yml", "security.yml", "scorecard.yml"):
                (workflows / name).write_text(
                    (root / ".github" / "workflows" / name).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            guard_start = source.index(
                "      - name: Refuse to mutate an existing immutable release"
            )
            guard_end = source.index("      - uses: actions/download-artifact@", guard_start)
            guard = source[guard_start:guard_end]
            unsafe = source[:guard_start] + source[guard_end:]
            promotion = unsafe.index("      - name: Promote qualified digests without rebuilding")
            publish = unsafe.index("      - name: Publish immutable GitHub release")
            unsafe = unsafe[:publish] + guard + unsafe[publish:]
            self.assertGreater(unsafe.index('gh release view "$TAG"'), promotion)
            (workflows / "release.yml").write_text(unsafe, encoding="utf-8")

            errors = validate(fixture)

        self.assertTrue(
            any("existing-release guard must run before" in error for error in errors), errors
        )

    def test_policy_requires_oci_version_guard_before_tag_promotion(self) -> None:
        root = Path(__file__).resolve().parents[2]
        release_path = root / ".github" / "workflows" / "release.yml"
        source = release_path.read_text(encoding="utf-8")
        guard_start = source.index("      - name: Refuse existing immutable OCI version tags")
        guard_end = source.index(
            "      - name: Promote qualified digests without rebuilding", guard_start
        )
        guard = source[guard_start:guard_end]
        unsafe = source[:guard_start] + source[guard_end:]
        insert_at = unsafe.index("      - name: Build deterministic contracts bundle")
        unsafe = unsafe[:insert_at] + guard + unsafe[insert_at:]
        temporary = self.make_workflow_fixture(
            root, changed_name="release.yml", changed_source=unsafe
        )

        errors = validate(Path(temporary.name))

        self.assertTrue(any("OCI version tags must be checked before" in e for e in errors), errors)

    def test_policy_rejects_semantic_supply_chain_regressions(self) -> None:
        root = Path(__file__).resolve().parents[2]
        release = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        security = (root / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")
        cases = {
            "unsafe trigger": (
                "security.yml",
                security.replace("  pull_request:\n", "  pull_request_target:\n"),
                "pull_request_target is forbidden",
            ),
            "permission escalation": (
                "security.yml",
                security.replace(
                    "      contents: read\n    steps:", "      issues: write\n    steps:", 1
                ),
                "writable permissions differ from the least-privilege policy",
            ),
            "release secret": (
                "release.yml",
                release.replace(
                    "          VERSION: ${{ needs.validate.outputs.version }}",
                    "          VERSION: ${{ needs.validate.outputs.version }}\n"
                    "          PRIVATE_KEY: ${{ secrets.SIGNING_KEY }}",
                    1,
                ),
                "must not consume repository secrets",
            ),
            "qualification bypass": (
                "release.yml",
                release.replace("    needs: [validate, qualify]", "    needs: validate"),
                "build must depend on release qualification",
            ),
            "unprotected build push": (
                "release.yml",
                release.replace("    environment: release\n", "", 1),
                "exact occurrences of control: environment: release",
            ),
            "tagged signer target": (
                "release.yml",
                release.replace('"${IMAGE}@${DIGEST}"', '"${IMAGE}:latest"', 1),
                'exact occurrences of control: "${IMAGE}@${DIGEST}"',
            ),
            "tagged promotion source": (
                "release.yml",
                release.replace('"${image}@${digest}"', '"${image}:sha-${REVISION}"'),
                'exact occurrences of control: "${image}@${digest}"',
            ),
            "incomplete per-image checksums": (
                "release.yml",
                release.replace(
                    '            "${COMPONENT}.cyclonedx.json" > "${COMPONENT}.sha256")',
                    '            > "${COMPONENT}.sha256")',
                ),
                "checksums must cover descriptor, SPDX, and CycloneDX",
            ),
        }

        for label, (name, source, expected) in cases.items():
            with self.subTest(case=label):
                temporary = self.make_workflow_fixture(
                    root, changed_name=name, changed_source=source
                )
                errors = validate(Path(temporary.name))
                self.assertTrue(any(expected in error for error in errors), errors)


class ReleaseGuardTests(unittest.TestCase):
    def make_repository(self, version: str = "1.2.3") -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "fixture"\nversion = "{version}"\n', encoding="utf-8"
        )
        subprocess.run(["/usr/bin/git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["/usr/bin/git", "add", "pyproject.toml"], cwd=root, check=True)
        subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            cwd=root,
            check=True,
        )
        subprocess.run(["/usr/bin/git", "tag", "v1.2.3"], cwd=root, check=True)
        return root

    def test_accepts_matching_canonical_tag(self) -> None:
        root = self.make_repository()
        self.assertEqual(
            validate_release(
                root,
                repository=CANONICAL_REPOSITORY,
                event="push",
                trigger_ref="refs/tags/v1.2.3",
                tag="v1.2.3",
            ),
            "1.2.3",
        )

    def test_rejects_fork_non_semver_and_version_mismatch(self) -> None:
        root = self.make_repository()
        cases = (
            {"repository": "fork/clearinghouse", "tag": "v1.2.3"},
            {"repository": CANONICAL_REPOSITORY, "tag": "latest"},
            {"repository": CANONICAL_REPOSITORY, "tag": "v1.2.3+build.1"},
            {"repository": CANONICAL_REPOSITORY, "tag": "v1.2.4"},
        )
        for case in cases:
            with (
                self.subTest(case=case),
                self.assertRaises((ValueError, subprocess.CalledProcessError)),
            ):
                validate_release(
                    root,
                    repository=case["repository"],
                    event="push",
                    trigger_ref=f"refs/tags/{case['tag']}",
                    tag=case["tag"],
                )

    def test_manual_recovery_must_start_from_main(self) -> None:
        root = self.make_repository()
        with self.assertRaisesRegex(ValueError, "dispatched from main"):
            validate_release(
                root,
                repository=CANONICAL_REPOSITORY,
                event="workflow_dispatch",
                trigger_ref="refs/heads/feature",
                tag="v1.2.3",
            )


class ReleaseAssetTests(unittest.TestCase):
    def test_bundle_is_reproducible_and_manifest_checks_every_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "contracts").mkdir()
            (root / "contracts" / "openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
            (root / "contracts" / "ignored.py").write_text("raise SystemExit\n", encoding="utf-8")
            for name in (".env.example", "ARCHITECTURE.md", "compose.yaml"):
                (root / name).write_text(f"{name}\n", encoding="utf-8")
            first = root / "first"
            second = root / "second"
            archive_one, manifest_path = build_contract_bundle(
                root, first, version="1.2.3", source_date_epoch=123456789
            )
            archive_two, _ = build_contract_bundle(
                root, second, version="1.2.3", source_date_epoch=123456789
            )

            self.assertEqual(archive_one.read_bytes(), archive_two.read_bytes())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            by_path = {entry["path"]: entry for entry in manifest["files"]}
            expected = (root / "contracts" / "openapi.yaml").read_bytes()
            self.assertEqual(
                by_path["contracts/openapi.yaml"]["sha256"], hashlib.sha256(expected).hexdigest()
            )
            self.assertNotIn("contracts/ignored.py", by_path)
            with tarfile.open(archive_one, "r:gz") as archive:
                names = archive.getnames()
                self.assertIn("contracts.manifest.json", names)
                self.assertIn("contracts/openapi.yaml", names)


class ReleaseDescriptorTests(unittest.TestCase):
    def test_accepts_only_complete_canonical_descriptor_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            revision = "a" * 40
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

            (directory / "backend.image.json").write_text(
                json.dumps(
                    {
                        "component": "backend",
                        "image": "ghcr.io/attacker/backend",
                        "digest": "latest",
                        "revision": "b" * 40,
                        "version": "9.9.9",
                    }
                ),
                encoding="utf-8",
            )
            errors = validate_descriptors(directory, version="1.2.3", revision=revision)
            self.assertTrue(any("image is not canonical" in error for error in errors))
            self.assertTrue(any("digest is not an exact sha256" in error for error in errors))
            self.assertTrue(any("revision does not match" in error for error in errors))
            self.assertTrue(any("version does not match" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
