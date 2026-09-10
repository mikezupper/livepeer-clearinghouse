from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_harness import REQUIRED_FILES, validate


class HarnessValidationTests(unittest.TestCase):
    def make_valid_root(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for relative in REQUIRED_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Document\n", encoding="utf-8")
        (root / ".codex/hooks.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [],
                        "PreCompact": [],
                        "PostCompact": [],
                        "UserPromptSubmit": [],
                    }
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_repository_harness_is_valid(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(validate(root), [])

    def test_reports_missing_required_file(self) -> None:
        root = self.make_valid_root()
        (root / "AGENTS.md").unlink()
        self.assertIn("missing required file: AGENTS.md", validate(root))

    def test_rejects_large_agent_map_and_parallel_tracker(self) -> None:
        root = self.make_valid_root()
        (root / "AGENTS.md").write_text("line\n" * 101, encoding="utf-8")
        (root / "TODO.md").write_text("duplicate tracker", encoding="utf-8")
        errors = validate(root)
        self.assertIn("AGENTS.md must remain at or below 100 lines", errors)
        self.assertIn("parallel work tracker is forbidden: TODO.md", errors)

    def test_reports_invalid_hooks_and_broken_local_link(self) -> None:
        root = self.make_valid_root()
        (root / ".codex/hooks.json").write_text("not json", encoding="utf-8")
        (root / "docs/index.md").write_text("[missing](not-there.md)\n", encoding="utf-8")
        errors = validate(root)
        self.assertTrue(any(error.startswith("invalid .codex/hooks.json") for error in errors))
        self.assertIn("broken local link in docs/index.md: not-there.md", errors)

    def test_reports_missing_hook(self) -> None:
        root = self.make_valid_root()
        (root / ".codex/hooks.json").write_text(
            json.dumps({"hooks": {"SessionStart": []}}), encoding="utf-8"
        )
        self.assertIn(
            "missing Codex hooks: PostCompact, PreCompact, UserPromptSubmit",
            validate(root),
        )


if __name__ == "__main__":
    unittest.main()
