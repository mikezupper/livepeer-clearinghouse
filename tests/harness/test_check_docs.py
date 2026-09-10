from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.check_docs import BADGES, validate

ROOT = Path(__file__).resolve().parents[2]


class DocumentationTruthTests(unittest.TestCase):
    def fixture(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name) / "repository"
        shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns("node_modules", ".git"))
        return target

    def test_checked_in_documentation_is_truthful(self) -> None:
        self.assertEqual(validate(ROOT), [])

    def test_reports_broken_links_across_root_docs_and_github_docs(self) -> None:
        root = self.fixture()
        with (root / "SUPPORT.md").open("a", encoding="utf-8") as stream:
            stream.write("\n[missing](docs/missing.md)\n")
        with (root / ".github" / "PULL_REQUEST_TEMPLATE.md").open("a", encoding="utf-8") as stream:
            stream.write("\n[also missing](../missing.md)\n")
        errors = validate(root)
        self.assertIn("broken local link in SUPPORT.md: docs/missing.md", errors)
        self.assertIn(
            "broken local link in .github/PULL_REQUEST_TEMPLATE.md: ../missing.md", errors
        )

    def test_reports_badge_without_exact_backing_workflow(self) -> None:
        root = self.fixture()
        image, target, _ = BADGES["Security"]
        readme = (root / "README.md").read_text(encoding="utf-8")
        (root / "README.md").write_text(
            readme.replace(f"[![Security]({image})]({target})", ""), encoding="utf-8"
        )
        errors = validate(root)
        self.assertTrue(any(error.startswith("README badge set mismatch") for error in errors))
        self.assertIn("README badge is not canonical: Security", errors)

    def test_reports_documented_make_target_and_configuration_drift(self) -> None:
        root = self.fixture()
        with (root / "README.md").open("a", encoding="utf-8") as stream:
            stream.write("\n`make imaginary-target` with `CLEARINGHOUSE_IMAGINARY`.\n")
        errors = validate(root)
        self.assertIn("undocumented Make implementation in README.md: imaginary-target", errors)
        self.assertIn(
            "README configuration is absent from .env.example: CLEARINGHOUSE_IMAGINARY",
            errors,
        )

    def test_reports_compose_configuration_and_image_drift(self) -> None:
        root = self.fixture()
        compose = root / "compose.yaml"
        compose.write_text(
            compose.read_text(encoding="utf-8") + "\n# ${CLEARINGHOUSE_UNDOCUMENTED}\n",
            encoding="utf-8",
        )
        release = root / ".github" / "workflows" / "release.yml"
        release.write_text(
            release.read_text(encoding="utf-8").replace(
                "ghcr.io/livepeer/clearinghouse-edge", "ghcr.io/example/edge"
            ),
            encoding="utf-8",
        )
        errors = validate(root)
        self.assertIn(
            "Compose configuration is absent from .env.example: CLEARINGHOUSE_UNDOCUMENTED",
            errors,
        )
        self.assertTrue(any(error.startswith("release image set mismatch") for error in errors))

    def test_reports_unwired_documented_runtime_configuration(self) -> None:
        root = self.fixture()
        compose = root / "compose.yaml"
        compose.write_text(
            compose.read_text(encoding="utf-8").replace(
                "  CLEARINGHOUSE_AUTH_OTP_TTL_SECONDS: "
                "${CLEARINGHOUSE_AUTH_OTP_TTL_SECONDS:-600}\n",
                "",
            ),
            encoding="utf-8",
        )
        self.assertIn(
            "documented runtime configuration is not wired through Compose: "
            "CLEARINGHOUSE_AUTH_OTP_TTL_SECONDS",
            validate(root),
        )

    def test_reports_stale_fork_setup_and_missing_ignores(self) -> None:
        root = self.fixture()
        (root / ".github" / "REPO_SETUP.md").write_text(
            "https://github.com/mikezupper/livepeer-clearninghouse\n", encoding="utf-8"
        )
        gitignore = root / ".gitignore"
        gitignore.write_text(
            gitignore.read_text(encoding="utf-8").replace(".idea/\n", ""), encoding="utf-8"
        )
        errors = validate(root)
        self.assertIn(
            "stale fork/template reference remains: mikezupper/livepeer-clearninghouse", errors
        )
        self.assertIn("stale inherited setup artifact remains: .github/REPO_SETUP.md", errors)
        self.assertIn("generated/editor artifact is not ignored: .idea/", errors)

    def test_reports_any_noncanonical_clearinghouse_repository(self) -> None:
        root = self.fixture()
        with (root / "README.md").open("a", encoding="utf-8") as stream:
            stream.write("\nhttps://github.com/example/clearinghouse\n")
        self.assertIn(
            "non-canonical clearinghouse repository reference: example/clearinghouse",
            validate(root),
        )

    def test_allows_explicit_optional_pymthouse_boundary(self) -> None:
        self.assertFalse(
            any("pymthouse" in error.casefold() for error in validate(ROOT)),
            "intentional optional/non-goal Pymthouse documentation must remain valid",
        )


if __name__ == "__main__":
    unittest.main()
