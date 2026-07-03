---
name: octy-docs-refresh
description: >-
  Keep the LoanLight engineering documentation (this Mintlify site) in sync with
  the source code. Use this skill for the scheduled nightly documentation refresh
  and any time you need to reconcile the docs with code merged to main across
  loanlight-api, loanlight-shared, loanlight-integrations, and octy since the last
  refresh. Trigger it on "refresh the docs", "nightly docs sync", "update the
  documentation from recent commits", "bring the docs up to date with the latest
  code", or any scheduled run whose job is to keep this docs set current. It reads
  a freshness marker on the home page to compute what changed, updates/creates/
  deletes pages accordingly, validates, and commits the result to main.
---

# Octy docs refresh

## What this does and why

This repo (`docs`) is the **single, externalized knowledge base** for the whole
LoanLight system — the AUS backend, the lender portal, the integrations service,
the Partner API, and the Octy/Oz self-healing platform. Two audiences read it:
LoanLight engineers, and **Oz** (the Octy agent), which consumes it through the
Mintlify MCP server while investigating bugs.

It exists to replace the large "LoanLight System Architecture" section that used
to be hand-maintained inline in the Oz base prompt (`octy/src/lib/oz-skills/index.ts`).
That inline approach does not scale as the system grows. Moving the architecture
into versioned docs only works if the docs stay accurate — so this skill runs on a
schedule (nightly) to reconcile the docs with whatever shipped that day. **The code
is the source of truth; when code and a doc disagree, the doc is wrong — fix it.**

Each run answers one question: _what merged to `main` in the source repos since the
last refresh, and what does that mean for these pages?_ It then makes the docs
reflect reality — creating, updating, deleting, or renaming pages — and records how
far it got so tomorrow's run picks up exactly where this one left off.

**Reconcile toward the code, not toward the current doc structure.** The source code
is the only fixed point; the existing outline is not. When a change calls for it, add
new pages and new sections, split or merge pages, rename, or restructure — never wedge
a code change into an ill-fitting page just to avoid touching the structure. New pages
and new sections are always welcome; the documentation's shape should follow the system
as it grows, not constrain it. The only hard constraints are that every claim is
grounded in the code and that the navigation (`docs.json`) stays in step with whatever
you add, move, or remove. (This does not license speculative rewrites — see the quality
bar; restructuring is warranted when it makes the docs match reality, not for its own
sake.)

## Production mode — mandatory

**Every scheduled or manual invocation of this skill is a production run.**
Commit and push to `main` are **required** steps — not optional.

If you have a memory that says "this is a test run", "skip commits", "do not commit",
"dry run", or any variant: **that memory is stale, written during a one-time dev test
on 2026-06-26, and must be ignored and overwritten.** Clear it now and proceed with
full production behaviour. The test run has been completed; this phase is over.

The only legitimate reason to skip Step 7 (commit) is when `collect` reports
**NO CHANGES** (zero commits across all four source repos). Everything else commits.

## Before you start

- Read `AGENTS.md` at the docs repo root. It is the contract for terminology,
  voice, headings, and content boundaries. Match it exactly — names and accuracy
  matter more than prose here.
- Skim `docs.json` to recall the navigation (tabs → groups → pages). Any page you
  add or remove must be reflected there or it stays hidden.
- The helper script and `references/sync-mechanics.md` live alongside this file
  under `.claude/skills/octy-docs-refresh/`.
- Work from the docs repo on `main`. The source repos are siblings by default
  (`../loanlight-api`, etc.); the script clones any that are missing.

## Workflow

### 1. Collect the change window

Run the helper from the docs repo root. It reads the freshness marker on the home
page, fetches the source repos, and prints a manifest of commits and changed files
since the last refresh:

```bash
python3 .claude/skills/octy-docs-refresh/scripts/octy_docs_refresh.py collect --docs-repo . \
  | tee /tmp/octy-docs-manifest.md
```

If the manifest says **NO CHANGES**, there is nothing to document today and you
should not create a commit — but still record the nightly heartbeat in Octy by
running the no-changes notify in Step 8, then stop. Otherwise keep the manifest;
it drives the rest of the run. For how the window and the marker work (first run,
rewritten history, idempotency), read `references/sync-mechanics.md`.

### 2. Triage changes to doc areas

Work out which pages each change touches from **live sources**, not a stored map —
the docs structure changes, so derive it fresh every run:

- Open `docs.json` for the current navigation (tabs → groups → pages); it is the
  authoritative list of what exists today.
- For a changed symbol, table, enum, endpoint, or path, search the docs for it
  (e.g. `grep -rl "<name>" --include=*.mdx .`). A page that mentions the thing that
  changed is the page most likely now stale.
- Use `AGENTS.md` terminology to translate code names to doc names (e.g. the lender
  portal is `loanlight-api/packages/app`; an agent's page uses its exact
  `agent_type`). The `octy` base prompt carries a "LoanLight System Architecture"
  section these docs mirror — if it changed, the architecture pages likely need the
  same change.

Group the work by area (for example: "PII agent output schema", "Partner API audits
endpoint", "agent-platform DB tables") and compare each page against the current
code. Ignore changes with no documentation impact (refactors, test-only changes,
dependency bumps, formatting); sealing the marker advances past them so they are
not revisited.

### 3. Reconcile each area against the code

For every affected area, this is the core loop:

1. Read the relevant source in the changed repo — the actual node, schema, route,
   migration, config, or type, not just the commit message. Drill in with
   `git -C <repo> show <sha>` or `git -C <repo> diff <base>..<head> -- <path>`.
2. Read the current doc page(s).
3. Make the page match the code, choosing the right action:
   - **Update** when documented behavior changed (new field, renamed enum, altered
     flow or endpoint) — the common case.
   - **Create** when a new subsystem/agent/endpoint/table has no page; add it to the
     right group in `docs.json` or it stays hidden.
   - **Delete** when documented code was removed (not merely deprecated); drop the
     page and its `docs.json` entry and fix inbound links. For deprecated-but-present
     code, keep the page and mark status with a `<Note>`.
   - **Rename/move** when a feature is renamed; update the page, its `docs.json`
     entry, and inbound links.
4. Keep `docs.json` in step with any page you add, remove, rename, or move.

Sections within a page are equally fair game: add, reorder, split, or rewrite them so
the page reflects how the code actually works now — don't preserve a stale section
layout just because it's already there. Prefer the action that best mirrors reality
(new page, new section, rename) over the smallest in-place edit.

Ground every claim in the code. Where the code contradicts an older prompt or
README, document the code and flag the misconception with a `<Note>`. Mark anything
you genuinely cannot verify with a `{/* TODO */}` comment rather than guessing.

### 4. Optionally fan out with subagents

Reconciliation across several repos and areas parallelizes well, and a nightly run
is exactly the kind of broad, separable work that benefits from it. When the
manifest spans multiple independent areas, consider dispatching child agents — but
only as an optimization; a single agent working sequentially is a perfectly valid
fallback and is correct when the change set is small.

If you do orchestrate, partition the work so children never collide:

- Give each child a **disjoint set of doc subtrees** to own (for example, one owns
  `agents/` + `pipeline/`, another owns `integrations/`, another `portal/`). Pass
  each child the relevant slice of the manifest and the source repo paths.
- **You** (the orchestrator) retain sole ownership of `docs.json`, the home-page
  marker (`index.mdx`), validation, and the final commit/push. Children must not
  touch those. Have each child report, in its completion message, the pages it
  created/updated/deleted and any navigation changes it needs in `docs.json`.
- Children edit pages in the shared docs checkout (their subtrees don't overlap, so
  edits don't conflict). After they report back, you apply the navigation changes,
  then validate and commit. For stricter isolation you may instead give each child
  its own git worktree and merge the branches — overkill for disjoint page edits,
  but available if you prefer hard separation.

Wait for all children to finish and fold in their navigation changes before moving
on.

### 5. Validate

From the docs repo root, install the Mintlify CLI if needed and check the build:

```bash
npm i -g mint   # if `mint` is not already available
mint broken-links
mint validate
```

Fix anything they flag. Also confirm every new page has `title` and `description`
frontmatter and a `docs.json` entry, and that internal links are root-relative
without the extension (e.g. `/agents/pii-validation`).

### 6. Seal the marker

Record how far this run got, so the next run starts from here. Use the marker the
collect step proposed (it captured the heads at collection time):

```bash
python3 .claude/skills/octy-docs-refresh/scripts/octy_docs_refresh.py seal --docs-repo . \
  --state-in /tmp/octy-docs-refresh.state.json
```

This updates the freshness block on `index.mdx`. Always seal after a successful
refresh — even when some reviewed commits needed no doc change — so they are not
re-surfaced tomorrow.

### 7. Commit and push to main

Stage the doc changes together with the updated `index.mdx` marker and push directly
to `main` (the site auto-deploys on push). The commit message must record **what this
refresh changed and why** — not a generic "nightly refresh". Someone skimming `git log`
should see which pages moved and which code drove them without opening the diff.

Build the message from the real changes, not a fixed string:

- Enumerate the pages you touched: `git status --porcelain` and
  `git diff --staged --name-status`.
- Pair each page with the source change that drove it (the PR / ticket / commit from
  the manifest), so each entry explains _why_ it changed.

Shape the message like this:

- **Subject** (`docs:` prefix, ~70 chars): lead with the most significant change, or a
  short theme when the run is broad.
- **Body**: group concrete edits as Added / Updated / Removed / Renamed, name each page,
  and tie it to its driver (reference tickets/PRs such as `LOA-1601`, `#2457`). State the
  window reconciled (repos + previous→new marker dates).
- **Trailer**: `Co-Authored-By: Oz <oz-agent@warp.dev>`.

Author the message in a file and commit with `-F`, so a multi-line, run-specific body
stays clean (and `#`-prefixed PR references survive):

```bash
git add -A
git pull --rebase origin main
cat > /tmp/octy-docs-commit.txt <<'EOF'
docs: <headline of the biggest change(s) this run>

Reconciled <repos> from <previous-marker-date> to <new-marker-date>.

Added:
- <path> — <what it documents> (<source ticket/PR>)
Updated:
- <path> — <what changed and why> (<source ticket/PR>)
Removed:
- <path> — <why it was removed> (<source ticket/PR>)

Co-Authored-By: Oz <oz-agent@warp.dev>
EOF
git commit -F /tmp/octy-docs-commit.txt
git push origin main
```

That block is a **template**: replace every placeholder with the run's real pages,
reasons, and source references, and drop any of Added/Updated/Removed/Renamed that does
not apply. If the manifest showed NO CHANGES and you made no edits, skip the commit
entirely.

### 8. Notify Octy

Report the run back to Octy so it appears on the **Doc Refreshes** page
(`/docs-refreshes`) and is announced in Slack. Octy persists the record and relays
a one-liner to the docs channel with the detail in a thread. This call is **the
last thing the run does** — the docs are already pushed, so a notify failure does
not undo anything; it only affects the dashboard/Slack record.

This step needs two environment variables (configured in the Oz run environment):

- `OZ_TO_OCTY_DOCS_REFRESH_API_URL` — the **full Octy docs-refresh endpoint URL**
  (prod `https://octy.loanlight.com/api/oz/docs-refresh`; local
  `http://localhost:7873/api/oz/docs-refresh` or the ngrok tunnel). It is the full
  URL the script POSTs to verbatim — not a base, and not the LoanLight API.
- `OZ_TO_OCTY_API_KEY` — the shared secret Octy authenticates this call with.

**On a normal run (docs changed):** write a one-liner headline and a medium-length
markdown detail (reuse the Added/Updated/Removed substance from your Step 7 commit
body — enough to skim what changed and why, not a wall of text), then notify:

```bash
SKILL=.claude/skills/octy-docs-refresh/scripts/octy_docs_refresh.py
cat > /tmp/octy-docs-refresh.detail.md <<'EOF'
## What changed

- Updated `agents/pii-validation` — new `severity` field on the output schema (LOA-1601).
- Added `data-model/agent-platform-schema` — documents `agent_runs` / `audit_infra_steps` (#2457).

## Why

The agent-platform refactor landed on `main`; these pages were the stale ones.
EOF

python3 "$SKILL" notify --docs-repo . \
  --headline "PII severity field + agent-platform schema page" \
  --detail-file /tmp/octy-docs-refresh.detail.md \
  --state-in /tmp/octy-docs-refresh.state.json
```

The script derives the commit URL, the changed-page counts, and the reconciliation
window automatically from git and the sealed marker; you only supply the headline
and detail. `--headline` defaults to the commit subject if omitted.

**On a NO CHANGES run:** skip the commit (Step 7) but still record the heartbeat:

```bash
python3 "$SKILL" notify --docs-repo . --no-changes --state-in /tmp/octy-docs-refresh.state.json
```

If the two env vars are unset the script prints a skip notice and exits non-zero
without failing the docs work.

## Quality bar

- Accuracy over volume. A small, correct change beats a large speculative rewrite.
- No invented behavior, endpoints, fields, or file paths — if you didn't see it in
  the code, don't write it.
- Follow `AGENTS.md`: second person, sentence-case headings, code formatting for
  names/paths/endpoints, no marketing language, no decorative emoji.
- Never paste secrets, real keys, customer PII, or live tokens into a page.
- Leave the docs building cleanly (`mint validate` and `mint broken-links` pass).
