# Architecture Decision Records

Numbered, immutable records of decisions that shape the repo. Use ADRs for
choices that:

- last beyond a single ExecPlan,
- affect how future contributors (human or agent) work,
- are non-obvious without context.

Numbering is sequential, four-digit, starting at `0001`. Filenames are
`NNNN-slug-with-dashes.md`. Use the template in `ADR_TEMPLATE.md`.

## Lifecycle

- **Proposed** while still under discussion. ADRs land in `Accepted` once
  merged.
- **Accepted** once the change is in `main` (or its successor branch).
- **Superseded** when a later ADR replaces it. The superseded record stays
  in place; never delete an ADR.

## Index

| # | Title | Status |
| --- | --- | --- |
| 0001 | Agent-generated development | Accepted |

When you add a new ADR, add a row here.

## When **not** to use an ADR

- Bug fixes — use a commit message.
- Time-bounded plans — use an ExecPlan.
- Implementation notes for a single task — use `docs/agent/tasks/`.
- Personal opinion / future ideas — use `docs/agent/QUALITY.md`.
