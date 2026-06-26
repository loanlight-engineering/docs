# Sync mechanics

How the refresh knows what changed and how it records progress. The helper
script `scripts/octy_docs_refresh.py` implements all of this; this file explains
what it does so you can reason about edge cases and run the steps by hand if the
script is unavailable.

## The freshness marker
State lives in the docs **home page** (`index.mdx`) inside a block delimited by
`{/* OCTY-DOCS-REFRESH:BEGIN … */}` and `{/* OCTY-DOCS-REFRESH:END */}`. The block
renders a short human summary plus a machine-readable JSON object:

```json
{
  "schema": 1,
  "lastRefreshedAt": "2026-06-26T00:00:00Z",
  "repos": {
    "loanlight-api": { "branch": "main", "sha": "<full sha>", "shortSha": "1ca9d55f6", "committedAt": "2026-06-25T23:51:02-04:00" },
    "loanlight-shared": { "...": "..." },
    "loanlight-integrations": { "...": "..." },
    "octy": { "...": "..." }
  }
}
```

`sha` per repo is the last `main` commit the docs are known to reflect. It is the
authority for the next run's window — not the docs repo's own commit date, which
can move for reasons unrelated to a content refresh (a config tweak, a typo fix).
Keeping the marker keyed on source SHAs makes the refresh **idempotent**: rerun it
and it computes the same window; once sealed, those commits are never reprocessed.

## Computing the window
For each source repo the script picks a diff base in this order:
1. **Prior `sha`** from the marker, if it still exists in the checkout. Window is
   `sha..origin/<branch>`.
2. **Prior `committedAt` date** if the exact sha is gone (history was rewritten):
   the last commit at/before that date becomes the base.
3. **First run (no marker):** the docs repo's own last commit date on `<branch>`
   is the lower bound — the behavior originally requested. The window can be large;
   review broadly.

It then reports, per repo:
- the commit list (`sha..head`), and
- the **net** changed files (`git diff --name-status <base> <head>`), grouped by
  the first two path segments. Net diff is used deliberately so intermediate churn
  that was later reverted does not create phantom doc work.

Drill into anything with normal git, e.g.
`git -C ../loanlight-api show <sha>` or
`git -C ../loanlight-api diff <base>..<head> -- packages/api/src/langgraph/agents/PII-Validation`.

## Sealing
After the docs are updated, `seal` writes the marker the `collect` step proposed
(the heads captured at collect time, so the recorded SHAs match exactly what you
reconciled). It refreshes `lastRefreshedAt` to now. `seal --from-current` instead
recomputes the live heads — use it only to initialize or hard-reset the marker.

## Idempotency and the no-changes case
- If `collect` reports **NO CHANGES**, do not commit anything — just exit.
- Always seal after a successful refresh, even if some commits needed no doc edit:
  sealing advances the markers past commits you reviewed and judged irrelevant, so
  they are not re-surfaced tomorrow. That commit may contain only the `index.mdx`
  marker bump, which is fine and keeps the freshness date honest.

## Exact invocations
From the docs repo root (source repos are siblings by default):

```bash
SKILL=.claude/skills/octy-docs-refresh/scripts/octy_docs_refresh.py

# 1. See what changed since the last refresh.
python3 "$SKILL" collect --docs-repo . | tee /tmp/octy-docs-manifest.md

# 2. (after editing docs) record the new marker the collect step proposed.
python3 "$SKILL" seal --docs-repo . --state-in /tmp/octy-docs-refresh.state.json

# One-time: initialize the marker from current heads.
python3 "$SKILL" seal --docs-repo . --from-current
```

Useful flags: `--no-fetch` (reuse local refs, e.g. offline testing), `--no-clone`
(fail instead of cloning a missing repo), `--branch`, `--source-repos`, `--org`,
`--repos-dir`, `--home-page`. All have `OCTY_*` environment-variable equivalents.

## Notifying Octy
After the docs are committed and pushed (or judged a no-changes night), the run
reports back to Octy with the `notify` subcommand so the result shows up on the
**Doc Refreshes** page and is relayed to Slack:

```bash
python3 "$SKILL" notify --docs-repo . \
  --headline "<one-liner>" --detail-file /tmp/octy-docs-refresh.detail.md \
  --state-in /tmp/octy-docs-refresh.state.json
# no-changes night:
python3 "$SKILL" notify --docs-repo . --no-changes --state-in /tmp/octy-docs-refresh.state.json
```

- **Endpoint:** `POST {OZ_TO_OCTY_DOCS_REFRESH_API_URL}` with
  `Authorization: Bearer {OZ_TO_OCTY_API_KEY}`. That env var is the **full** Octy
  endpoint URL (e.g. `https://octy.loanlight.com/api/oz/docs-refresh`) — posted
  verbatim, not a base, and not the LoanLight API.
- **Idempotency key:** the commit sha on a change-night, `docs-refresh:<UTC date>`
  on a no-changes night. The endpoint upserts on this key, so a retried notify
  updates the existing record (and does not double-post to Slack).
- **Derived automatically** from git + the sealed marker: `commit_url`,
  `committed_at`, the `.mdx` page counts (added/updated/removed/renamed),
  `changed_pages`, the reconciliation window (`window_from` = the prior marker
  date that `collect` stashed as `prevRefreshedAt`; `window_to` = this run's
  marker date), and `source_repos` provenance.
- **You supply:** `--headline` (defaults to the commit subject) and
  `--detail-file` (the medium-length markdown shown in Octy's side panel and the
  Slack thread).
- Notify runs **last**. The docs are already pushed, so a notify failure is
  logged but never rolls anything back.

## Environment assumptions
- `git` and Python 3 are present.
- The runtime can reach GitHub for `loanlight-engineering/*` and (for the final
  push) has push rights to the docs repo's `main`. In a scheduled Oz run these come
  from the environment's configured credentials; the skill does not handle secrets.
