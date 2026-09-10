# Work tracking

Beads is the sole project work tracker. Repository documents record durable
knowledge and decisions, not status checklists.

```bash
bd prime
bd ready --json
bd update <id> --claim
bd note <id> "material finding"
bd create "discovered work" --deps discovered-from:<id>
bd close <id> --reason "verified outcome"
```

Dependency direction is requirement direction: `bd dep add A B` means A needs
B. The current production walking slice is epic `och-u8d`; use `bd graph
och-u8d` and `bd blocked --parent och-u8d` to inspect its execution graph.

Do not run `bd edit`, parse human-formatted output in automation, manipulate
Dolt directly, or use JSONL export as synchronization. Do not push Beads or Git
state without explicit authority from the project owner.
