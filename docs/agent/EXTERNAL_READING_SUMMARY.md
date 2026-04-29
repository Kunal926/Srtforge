# EXTERNAL_READING_SUMMARY.md

External principles consulted before designing the Srtforge agent harness,
mapped to concrete repo changes. Sources are summarized from prior
familiarity rather than re-fetched at write time; entries below note when a
source could not be re-verified during this session and are recorded as
"summarized from prior reading" so the next agent can confirm.

## Sources consulted

### 1. OpenAI — "Harness Engineering"

- **URL:** https://openai.com/index/harness-engineering/
- **Status:** summarized from prior reading; not refetched in this session
  (no live web access used).
- **Principle extracted:** A coding agent's effectiveness is bounded by the
  surrounding harness — the validation scripts, contract tests, retrieval,
  and feedback loop. Investing in harness pays off more than tweaking the
  model.
- **Why it matters here:** Srtforge has a wide surface (Python + Rust + TS
  + packaging). A weak harness will produce drifting protocol code.
- **Repo change applied:**
  - `scripts/check.ps1` and `scripts/check.sh` give the agent a single
    canonical "is the repo healthy?" command.
  - `pyproject.toml` pytest markers (`slow`, `requires_*`) keep the fast
    feedback loop fast.
  - `docs/contracts/worker-protocol.md` + JSON schemas + protocol-sync
    test catch drift between Python emitters, the Rust forwarder, and the
    TypeScript consumer.
  - `.claude/skills/harness-check/` exposes the harness as a one-step
    skill.

### 2. Geoffrey Huntley — "The Ralph Loop"

- **URL:** https://ghuntley.com/loop/
- **Status:** summarized from prior reading; not refetched in this session.
- **Principle extracted:** The default agent loop is read context → plan →
  patch → narrow check → fix → broader check → handoff → summarize. Run it
  on one task at a time before reaching for orchestration.
- **Why it matters here:** Srtforge changes are usually small but
  cross-cutting. A disciplined single-agent loop beats parallelism for
  most of the work.
- **Repo change applied:**
  - `docs/agent/WORKFLOW.md` codifies the loop verbatim.
  - `.claude/skills/prime/`, `plan/`, `fix-loop/`, `harness-check/`,
    `handoff/`, `pr-summary/` correspond to loop phases.
  - The "failure-domain rule" sits inside the loop: when stuck, extend
    the harness, don't ask the human to write code.

### 3. agents.md community spec

- **URL:** https://agents.md/
- **Status:** summarized from prior reading; not refetched in this session.
- **Principle extracted:** The repo-root `AGENTS.md` should be a compact
  map, not the encyclopedia. Point to deeper docs rather than duplicating
  them.
- **Why it matters here:** A bloated entry file rots and consumes context
  on every session.
- **Repo change applied:**
  - `AGENTS.md` is intentionally short (under ~150 lines) and links to
    `docs/agent/`, `docs/architecture/`, `docs/contracts/`, `docs/adr/`,
    and `CLAUDE.md`.
  - `CLAUDE.md` is now a Claude-Code-specific operating manual, not a
    duplicate of `AGENTS.md`.

### 4. OpenAI Cookbook — "Codex exec plans"

- **URL:** https://developers.openai.com/cookbook/articles/codex_exec_plans
- **Status:** summarized from prior reading; not refetched in this session.
- **Principle extracted:** For multi-hour or cross-cutting work, a
  self-contained "ExecPlan" file lets a fresh agent execute without chat
  history. Required sections include purpose, context, progress,
  milestones, steps, validation, surprises, decision log, outcomes,
  idempotence.
- **Why it matters here:** Srtforge's bigger changes (worker protocol
  expansion, packaging changes) frequently span multiple sessions.
- **Repo change applied:**
  - `docs/agent/PLANS.md` defines the ExecPlan convention with all required
    sections.
  - `docs/agent/exec-plans/active/` and `archive/` directories established.
  - `.claude/skills/execplan/` walks the agent through creating one.

### 5. Lexi Lambda — "Parse, don't validate"

- **URL:** https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/
- **Status:** summarized from prior reading; not refetched in this session.
- **Principle extracted:** Convert untyped inputs into typed values at the
  boundary of your system. Spread `dict.get` / "validate every step"
  patterns produce subtle drift; explicit parsing produces structured
  errors.
- **Why it matters here:** The Srtforge worker speaks JSON over stdin and
  emits JSON on stdout. The Python side currently uses `payload.get(...)`
  patterns which are easy to drift.
- **Repo change applied:**
  - `srtforge/worker_protocol.py` adds typed parse helpers (`parse_request`)
    and structured event builders. Used additively at first — existing
    `_emit_worker_event` callers continue to work.
  - The contract tests in `tests/test_worker_protocol.py` assert that
    malformed JSON / non-dict payloads / unknown actions produce the
    documented `bad_json` / `bad_payload` / `unknown_action` events.

### 6. Claude Code slash-commands / skills documentation

- **URL:** https://code.claude.com/docs/en/slash-commands
- **Status:** summarized from prior reading; not refetched in this session.
- **Principle extracted:** Skills are markdown files with YAML frontmatter
  (`name`, `description`) under `.claude/skills/<name>/SKILL.md`. They
  load only when invoked, keeping the default context small.
- **Why it matters here:** We want repeatable operations (prime, plan,
  harness-check, handoff, pr-summary, protocol-change) without bloating
  every session's context.
- **Repo change applied:**
  - `.claude/skills/{prime, plan, execplan, fix-loop, harness-check,
    handoff, pr-summary, protocol-change}/SKILL.md`.
  - Skills with side effects (`harness-check`, `handoff`, `protocol-change`)
    use `disable-model-invocation: true` so the human triggers them
    deliberately.

## Anything that could not be accessed

This session ran without live web access for the source pages above. Each
entry is therefore "summarized from prior reading". The next agent should:

- Re-fetch each URL when convenient and confirm the principles still
  match.
- Update this file with anything that has changed (or note that the URL
  is dead).
- Do not invent content for inaccessible pages — leave a "could not
  access" note instead.
