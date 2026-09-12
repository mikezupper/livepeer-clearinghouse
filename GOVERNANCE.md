# Governance

Open Clearinghouse is an open-source Livepeer project. This document describes
how technical decisions and repository stewardship work; it does not govern the
Livepeer protocol or network.

## Roles

Contributors propose changes, review designs, test releases, improve
documentation, and participate under the [Code of Conduct](CODE_OF_CONDUCT.md).

Maintainers are contributors with write or review authority in the canonical
repository. They triage public intake, maintain the Beads dependency graph,
review changes, protect releases, coordinate security response, and uphold the
project's architecture and quality policies. Repository administrators grant or
remove maintainer access based on sustained, constructive participation and the
needs of the project.

## Decisions

Routine implementation decisions happen in pull-request review against the
documented contracts and required checks. Material changes to security
boundaries, compatibility, data models, public APIs, custody, or project scope
require a written design update and an opportunity for maintainer and community
review before implementation is merged.

The project seeks rough consensus supported by evidence. When consensus is not
available, maintainers with responsibility for the affected area decide and
record the reasoning in durable documentation. A maintainer with a material
conflict of interest must disclose it and recuse from the final decision.

Beads is the sole internal implementation tracker. GitHub Issues collect public
bug reports and proposals; a maintainer creates or links the corresponding bead
when the project accepts work. Roadmap or status checklists must not be
maintained in parallel Markdown files.

## Ownership and review

Branch protection and required status checks are the enforcement boundary. At
least one maintainer review is expected for routine changes; security-sensitive,
release, stored-schema, public-contract, and signer-custody changes should
receive review from a maintainer familiar with that area. A deployment-owned
enterprise extension does not become part of the core's governance or support
surface merely because it implements a public port. No review can waive the
required quality gates.

The repository intentionally does not declare a `CODEOWNERS` rule until an
administrator verifies the canonical GitHub user or team with write access.
GitHub silently ignores owners without suitable access, so inventing a team name
would provide false assurance. After verification, an administrator should add
the least-broad valid team and enable required Code Owner review in branch
protection.

## Amendments

Governance changes use the same public proposal and pull-request process as
other material changes. Security response may be coordinated privately until
responsible disclosure is safe, after which durable policy changes are made
public.
