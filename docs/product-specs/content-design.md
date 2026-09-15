# Product content design

Clearinghouse copy helps people make informed authorization and cost decisions. It is concise, specific, and operational: each page explains what it contains, each control explains its consequence, and each state explains the next useful action.

## Voice and structure

Use direct, calm language. Address the reader as “you” when guidance depends on their action. Prefer concrete verbs such as *create*, *revoke*, *filter*, and *resume* over generic verbs such as *manage* or *submit*. Do not use promotional language, jokes, blame, or unexplained implementation jargon.

Each route follows this content hierarchy:

1. An eyebrow names the broader task area.
2. The `h2` names the page in familiar product language.
3. A visible introduction explains what the page shows or enables and why it matters.
4. Each region has an outcome-oriented heading and, when needed, a brief description.
5. Labels name the value to enter; associated hints explain format, source, consequence, or privacy.
6. Actions use verb-first labels that state the result.
7. Loading, empty, success, stale, and failure states state what happened and the next available action.

The application shell owns the route `h1` and its one-sentence summary. Route
content begins at `h2`; regions beneath it use `h3` without skipping levels.
Navigation labels and shell summaries come from each application's immutable
route metadata. Page templates own region headings, labels, captions, hints,
and state messages. Do not copy all interface strings into this document or
construct a second content registry.

## Help layers

Choose the least hidden layer that gives the reader enough context:

| Layer | Use | Do not use |
| --- | --- | --- |
| Visible help | Prerequisites, consequences, security or privacy boundaries, price meaning, destructive effects, and recovery steps | Information a person can safely discover only after acting |
| `details` disclosure | Supplemental explanations longer than one sentence, worked examples, and optional background | Required instructions or the sole explanation of a control |
| Contextual-help tooltip | A short definition for a nearby unfamiliar term | Interactive content, multi-step guidance, errors, warnings, or essential information |

A native `details` element starts with a specific `summary`; its expanded text
must make sense immediately after that summary. A tooltip trigger has an
accessible name that identifies the term, works with keyboard and pointer,
stays available on focus, and can be dismissed without moving focus. Never use
the HTML `title` attribute as the only help or accessible name.

## Descriptions and validation

Every form control has a visible `label`. When a hint is necessary, give the
hint a stable page-local ID and reference it from the control with
`aria-describedby`. If validation adds an error, append the error ID to the
same attribute so assistive technology receives both the persistent hint and
the current failure. Remove the error reference when the error is resolved;
do not remove the persistent hint. `aria-describedby` supplements the label—it
does not replace it.

Descriptions belong next to the control or region they explain. Text that
describes a whole group follows its `legend` or region heading rather than
being repeated on every field. IDs must remain unique within the rendered
Shadow Root.

## Canonical terms

| Term | Meaning | Avoid |
| --- | --- | --- |
| Network offer | A discovered orchestrator capability and its advertised price | listing, catalog item |
| Capability | A workload function advertised by an orchestrator | feature, service type |
| Runner | The endpoint that performs a capability | worker, provider when referring to the endpoint |
| Workload | A quoted, time-bounded authorization for one user job | active job, token |
| Workload SDK token | The short-lived secret used by `livepeer-python-gateway` for one workload | Python SDK token, API token |
| Account API credential | A revocable `och_live_` secret for Clearinghouse control-plane API calls | SDK token, API key when the distinction matters |
| Advertised rate | The exact price attached to a network offer | current cost, final price |
| Estimated cost | Advertised rate applied to user-supplied assumptions | quote when no workload exists |
| Quoted cost | The workload's snapshotted rate applied to measured usage | estimated cost |
| Signer-reported cost | The fee reported by signer metering | invoice, charge |
| Maximum spend | The immutable workload authorization ceiling selected by the user | budget when referring to an unenforced estimate |
| Pending cost | Signer exposure already authorized but not yet reconciled to a usage event | pending charge, estimated cost |
| Remaining spend | Maximum spend minus signer-reported and pending cost | account balance, available funds |
| Usage event | A signer event attributed to a workload | transaction unless it is an on-chain transaction |
| Authorization paused | The global signer stop is enabled | system down, disabled |

Use *user* for a signed-in identity and *account* for the boundary that owns credentials, workloads, and usage. The simplified core currently creates one personal account per user; copy must not imply organizations or multi-tenant roles.

## Control and state copy

Field help is connected with `aria-describedby`. Destructive and security-sensitive actions state their object: “Revoke account API credential,” “Revoke workload access,” and “Pause signer authorization.” Confirmation or success messages use past tense and identify the affected object.

State copy names the object and preserves a next step:

| State | Required content |
| --- | --- |
| Loading | The task or data being loaded, such as checking a session or loading network offers |
| Empty | What is absent, whether filters caused the result, and the action that can create or reveal data |
| Success | The completed action and affected object; mention one-time secret handling when applicable |
| Stale | What changed, what the application reset or retained, and what the reader should review |
| Denied | The policy or status that prevented the action when known, plus a safe alternative |
| Failure | The attempted task, a typed and safe reason when available, and a retry or recovery action |

Errors follow this sequence:

1. State the failed task in user language.
2. Give a safe, likely reason only when the typed failure establishes it.
3. State a concrete recovery action.

Do not expose stack traces, raw response bodies, credentials, or internal dependency names. A fallback message must still identify the attempted task and invite a retry; “The clearinghouse could not complete that request” is not sufficient by itself. Keep the failed control available when retry is safe and preserve valid input. Use `role="alert"` for a newly rendered failure that needs immediate attention and `role="status"` for nonurgent progress or success; do not add a live-region role to static help.

Empty states distinguish between no records, no filter matches, unavailable upstream data, and data not yet loaded. Never describe an empty filtered page as an empty account.

## Verification locations

- `frontend/apps/user-web/src/user-app.test.ts` and
  `frontend/apps/admin-web/src/admin-app.test.ts` cover route content, state
  messages, form descriptions, disclosures, and user-visible actions.
- Tests beside shared components in `frontend/packages/ui/src/*.test.ts` cover
  reusable contextual-help and shell semantics.
- `frontend/e2e/clearinghouse.accessibility.spec.ts` covers accessible names,
  relationships, focus, and Axe findings across rendered Shadow DOM.
- `frontend/e2e/clearinghouse.journey.spec.ts` covers copy in complete user and
  administrator tasks; the visual spec protects hierarchy and density, not the
  exact wording of every string.

Prefer assertions on visible meaning, accessible names/descriptions, and the
resulting action. Avoid snapshots of entire templates and selectors coupled to
private markup.

## Review checklist

- Heading order and landmark names describe the page without visual context.
- Page introductions and important consequences are visible without interaction.
- Every input has a visible label and any necessary hint is programmatically associated.
- Acronyms and domain terms are expanded or have accessible contextual help on first use.
- Link text names a destination; button text names an action and its object.
- Tables have descriptive captions; empty collections render explanatory prose outside an empty table body.
- Dates identify whether they are creation, expiration, measurement, or update times.
- Amount labels distinguish advertised, estimated, quoted, signer-reported,
  pending, maximum, and remaining values.
- Success, stale, denied, and failure states identify a next step.
- `details` contains only supplemental help and has a specific `summary`.
- Tooltip content is a short definition, remains available on focus, is
  dismissible, and duplicates no essential instruction.
- `aria-describedby` references existing, unique IDs and includes both hint and
  current error text when both apply.
- Tests assert user-observable meaning and accessible names rather than private markup.
