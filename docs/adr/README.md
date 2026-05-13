# Architecture Decision Records

Numbered, immutable records of decisions that shape the repo. Use ADRs for
choices that:

- last beyond a single implementation phase,
- affect how future contributors work,
- are non-obvious without context.

Numbering is sequential, four-digit, starting at `0001`. Filenames are
`NNNN-slug-with-dashes.md`. Use the template in `ADR_TEMPLATE.md`.

## Lifecycle

- **Proposed** while still under discussion. ADRs land in `Accepted` once
  merged.
- **Accepted** once the change is in `main` or its successor branch.
- **Superseded** when a later ADR replaces it. The superseded record stays in
  place; never delete an ADR.

## Index

| # | Title | Status |
| --- | --- | --- |

When you add a new ADR, add a row here.

## When Not To Use An ADR

- Bug fixes: use a commit message.
- Time-bounded implementation plans: keep them in the issue, PR, or project
  tracker.
- Implementation notes for a single task: keep them in the PR.
- Personal opinion or future ideas: keep them out of ADRs until they become
  decisions.
