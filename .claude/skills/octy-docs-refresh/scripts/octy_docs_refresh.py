#!/usr/bin/env python3
"""Helper for the octy-docs-refresh skill.

This script does the deterministic, fiddly parts of the nightly documentation
refresh so the agent can spend its attention on reading code and writing docs
instead of re-deriving git plumbing every run:

  * collect  Read the freshness marker from the docs home page, locate/fetch the
             source repositories, compute what changed on `main` since the last
             refresh, and print a human-readable manifest. It also writes the
             *proposed* new marker (the commits this run is reconciling up to) to
             a temp file so `seal` can record exactly what was processed.

  * seal     Write the freshness marker block into the docs home page. In the
             normal nightly flow it reads the proposed marker produced by
             `collect`; with --from-current it recomputes the current
             origin/<branch> heads (used to initialize the marker the first time).

Only `git` and Python 3 are required. The source repositories are discovered as
siblings of the docs repo by default, and cloned (treeless) if missing.

See ../references/sync-mechanics.md for the full mechanics and edge cases.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SOURCES = ["loanlight-api", "loanlight-shared", "loanlight-integrations", "octy"]
DEFAULT_ORG = "loanlight-engineering"
DEFAULT_BRANCH = "main"

# Sentinels that delimit the auto-maintained marker block in the home page.
BEGIN = "OCTY-DOCS-REFRESH:BEGIN"
END = "OCTY-DOCS-REFRESH:END"
STATE_SCHEMA = 1

# Empty tree object — used as a diff base when no prior commit can be resolved.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Cap how many files we list per group so the manifest stays readable; the agent
# can always drill in with `git -C <repo> diff <base>..<head> -- <path>`.
MAX_FILES_PER_GROUP = 60


# --------------------------------------------------------------------------- #
# Process helpers
# --------------------------------------------------------------------------- #
def run(args: list[str], cwd: str | Path | None = None, check: bool = True) -> tuple[str, int]:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr.strip()}"
        )
    return proc.stdout.strip(), proc.returncode


def git(repo: str | Path, *args: str, check: bool = True) -> str:
    out, rc = run(["git", "-C", str(repo), *args], check=check)
    return out


def git_ok(repo: str | Path, *args: str) -> bool:
    _, rc = run(["git", "-C", str(repo), *args], check=False)
    return rc == 0


def eprint(*a) -> None:
    print(*a, file=sys.stderr)


# --------------------------------------------------------------------------- #
# Repo discovery / fetch
# --------------------------------------------------------------------------- #
def locate_or_clone(name: str, repos_dir: Path, org: str, allow_clone: bool) -> Path:
    """Return a path to a git checkout of `name`, cloning it (treeless) if absent."""
    path = repos_dir / name
    if (path / ".git").exists() or git_ok(path, "rev-parse", "--git-dir"):
        return path
    if not allow_clone:
        raise FileNotFoundError(
            f"repository '{name}' not found at {path} and cloning is disabled (--no-clone)"
        )
    url = f"https://github.com/{org}/{name}.git"
    eprint(f"[octy-docs-refresh] cloning {url} -> {path} (treeless)")
    repos_dir.mkdir(parents=True, exist_ok=True)
    # blob:none keeps full commit history (needed for ranges/log) but defers blob
    # download until a file is actually inspected — cheap for a CI-style clone.
    run(["git", "clone", "--filter=blob:none", url, str(path)])
    return path


def fetch(repo: Path, branch: str) -> None:
    run(["git", "-C", str(repo), "fetch", "--quiet", "--filter=blob:none", "origin", branch])


def head_of(repo: Path, branch: str) -> tuple[str, str, str]:
    """Return (full_sha, short_sha, committer_date_iso) of origin/<branch>."""
    ref = f"origin/{branch}"
    full = git(repo, "rev-parse", ref)
    short = git(repo, "rev-parse", "--short", ref)
    date = git(repo, "log", "-1", "--format=%cI", ref)
    return full, short, date


def resolve_base(repo: Path, branch: str, prior_sha: str | None, since_date: str | None) -> str | None:
    """Pick the commit to diff against.

    Preference order:
      1. The prior recorded sha, if it still exists in this checkout.
      2. The last commit at/before the prior commit date (handles a rewritten
         history where the exact sha is gone).
      3. The last commit at/before `since_date` (first-run date fallback).
    Returns None when nothing sensible can be resolved (caller treats as baseline).
    """
    ref = f"origin/{branch}"
    if prior_sha and git_ok(repo, "cat-file", "-e", f"{prior_sha}^{{commit}}"):
        return prior_sha
    for cutoff in (since_date,):
        if cutoff:
            base, rc = run(
                ["git", "-C", str(repo), "rev-list", "-1", f"--before={cutoff}", "--first-parent", ref],
                check=False,
            )
            if rc == 0 and base:
                return base
    return None


# --------------------------------------------------------------------------- #
# Change collection
# --------------------------------------------------------------------------- #
def commits_between(repo: Path, base: str | None, head: str) -> list[dict]:
    rng = f"{base}..{head}" if base else head
    fmt = "%H%x1f%h%x1f%cI%x1f%an%x1f%s"
    out = git(repo, "log", f"--pretty=format:{fmt}", rng)
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        full, short, date, author, subject = (line.split("\x1f") + ["", "", "", "", ""])[:5]
        commits.append({"sha": full, "short": short, "date": date, "author": author, "subject": subject})
    return commits


def changed_files(repo: Path, base: str | None, head: str) -> list[tuple[str, str]]:
    """Return [(status, path)] for the net change between base and head."""
    base = base or EMPTY_TREE
    out = git(repo, "diff", "--name-status", "-M", "-C", f"{base}", f"{head}")
    files: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][0]  # R100 / C75 -> R / C
        path = parts[-1]      # for renames/copies, the new path is last
        files.append((status, path))
    return files


def group_files(files: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = {}
    for status, path in files:
        parts = path.split("/")
        key = "/".join(parts[:2]) if len(parts) >= 2 else "(top-level)"
        groups.setdefault(key, []).append((status, path))
    return dict(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def status_counts(files: list[tuple[str, str]]) -> dict[str, int]:
    counts = {"A": 0, "M": 0, "D": 0, "R": 0, "C": 0}
    for status, _ in files:
        counts[status] = counts.get(status, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Marker block parsing / rendering (lives in the docs home page)
# --------------------------------------------------------------------------- #
def read_state(home_page: Path) -> dict | None:
    if not home_page.exists():
        return None
    text = home_page.read_text(encoding="utf-8")
    b = text.find(BEGIN)
    e = text.find(END)
    if b == -1 or e == -1 or e < b:
        return None
    region = text[b:e]
    m = re.search(r"```[^\n]*\n(.*?)```", region, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def render_block(state: dict) -> str:
    refreshed = state.get("lastRefreshedAt", "")
    lines = [
        f"{{/* {BEGIN} — auto-maintained by the octy-docs-refresh skill; do not edit by hand */}}",
        "## Documentation freshness",
        (
            f"These docs were last reconciled with the source repositories on **{refreshed[:10]}**. "
            "Each entry below is the latest `main` commit the documentation is known to reflect; the "
            "nightly `octy-docs-refresh` skill reads these markers to compute what changed since the "
            "last run."
        ),
        "",
    ]
    for name in sorted(state.get("repos", {})):
        info = state["repos"][name]
        short = info.get("shortSha") or (info.get("sha", "")[:9])
        committed = (info.get("committedAt") or "")[:10]
        branch = info.get("branch", DEFAULT_BRANCH)
        lines.append(f"- `{name}` — `{short}` on `{branch}` · committed {committed}")
    lines += [
        "",
        "```json",
        json.dumps(state, indent=2, sort_keys=True),
        "```",
        f"{{/* {END} */}}",
    ]
    return "\n".join(lines)


def write_state(home_page: Path, state: dict) -> None:
    block = render_block(state)
    text = home_page.read_text(encoding="utf-8") if home_page.exists() else ""
    b = text.find(BEGIN)
    e = text.find(END)
    if b != -1 and e != -1 and e > b:
        # Replace the existing block, extending the end index to the closing brace
        # of the END sentinel comment so we don't leave a dangling "*/}".
        tail = text.find("}", e)
        end_idx = (tail + 1) if tail != -1 else (e + len(END))
        # Trim the opening "{/* " that precedes BEGIN, if present.
        start_idx = text.rfind("{/*", 0, b)
        if start_idx == -1:
            start_idx = b
        new_text = text[:start_idx].rstrip() + "\n\n" + block + "\n" + text[end_idx:].lstrip("\n")
    else:
        new_text = text.rstrip() + "\n\n" + block + "\n"
    home_page.write_text(new_text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# State construction
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_current_state(repos: dict[str, Path], branch: str) -> dict:
    state = {"schema": STATE_SCHEMA, "lastRefreshedAt": now_iso(), "repos": {}}
    for name, path in repos.items():
        full, short, date = head_of(path, branch)
        state["repos"][name] = {
            "branch": branch,
            "sha": full,
            "shortSha": short,
            "committedAt": date,
        }
    return state


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_collect(args: argparse.Namespace) -> int:
    docs_repo = Path(args.docs_repo).resolve()
    home_page = Path(args.home_page) if args.home_page else docs_repo / "index.mdx"
    repos_dir = Path(args.repos_dir).resolve() if args.repos_dir else docs_repo.parent
    sources = [s.strip() for s in args.source_repos.split(",") if s.strip()]
    branch = args.branch

    prior = read_state(home_page)
    prior_repos = (prior or {}).get("repos", {})

    # First-run lower bound: the docs repo's own latest commit date on its branch.
    docs_since = None
    if not prior:
        if not args.no_fetch:
            fetch(docs_repo, branch)
        ref = f"origin/{branch}" if git_ok(docs_repo, "rev-parse", f"origin/{branch}") else "HEAD"
        docs_since = git(docs_repo, "log", "-1", "--format=%cI", ref)

    located: dict[str, Path] = {}
    for name in sources:
        path = locate_or_clone(name, repos_dir, args.org, allow_clone=not args.no_clone)
        if not args.no_fetch:
            fetch(path, branch)
        located[name] = path

    proposed = build_current_state(located, branch)

    # ---- Build the manifest -------------------------------------------------
    out: list[str] = []
    out.append("# octy-docs-refresh — change manifest")
    out.append("")
    if prior:
        out.append(f"Prior refresh marker: **{prior.get('lastRefreshedAt', 'unknown')}**")
    else:
        out.append(
            "No prior freshness marker found — this is a **first run**. Lower bound is the docs "
            f"repo's last commit on `{branch}` (**{(docs_since or '')[:10]}**). Review broadly; the "
            "window may be large."
        )
    out.append("")

    total_commits = 0
    per_repo_sections: list[str] = []
    for name in sources:
        path = located[name]
        head_full = proposed["repos"][name]["sha"]
        head_short = proposed["repos"][name]["shortSha"]
        prior_sha = prior_repos.get(name, {}).get("sha")
        prior_date = prior_repos.get(name, {}).get("committedAt")
        base = resolve_base(path, branch, prior_sha, prior_date or docs_since)

        commits = commits_between(path, base, head_full)
        files = changed_files(path, base, head_full)
        total_commits += len(commits)
        counts = status_counts(files)

        sec = [f"## {name}", ""]
        base_label = f"`{base[:9]}`" if base else "(baseline — no resolvable base)"
        sec.append(f"- Window: {base_label} → `{head_short}` on `{branch}`")
        sec.append(
            f"- {len(commits)} commit(s), {len(files)} file(s) changed "
            f"(A{counts.get('A',0)} M{counts.get('M',0)} D{counts.get('D',0)} "
            f"R{counts.get('R',0)} C{counts.get('C',0)})"
        )
        if not commits:
            sec.append("- No new commits in this window.")
            sec.append("")
            per_repo_sections.append("\n".join(sec))
            continue

        sec.append("")
        sec.append("### Commits")
        for c in commits[:80]:
            sec.append(f"- `{c['short']}` {c['date'][:10]} {c['subject']}  — {c['author']}")
        if len(commits) > 80:
            sec.append(f"- …and {len(commits) - 80} more commit(s).")

        sec.append("")
        sec.append("### Changed files (grouped)")
        for group, items in group_files(files).items():
            sec.append(f"- **{group}** ({len(items)})")
            for status, path_ in items[:MAX_FILES_PER_GROUP]:
                sec.append(f"  - {status} {path_}")
            if len(items) > MAX_FILES_PER_GROUP:
                sec.append(f"  - …and {len(items) - MAX_FILES_PER_GROUP} more file(s).")
        sec.append("")
        per_repo_sections.append("\n".join(sec))

    out.append(f"**{total_commits} new commit(s)** across {len(sources)} repositories.")
    out.append("")
    if total_commits == 0:
        out.append(
            "## NO CHANGES\n\nNothing landed since the last refresh. Do not commit; exit cleanly."
        )

    out.append("")
    out.extend(per_repo_sections)

    # Persist the proposed marker so `seal` records exactly what was reconciled.
    # Also stash the prior refresh date so `notify` can report the reconciliation
    # window (window_from). `seal` strips this key before writing the marker.
    proposed["prevRefreshedAt"] = (prior or {}).get("lastRefreshedAt")
    state_out = Path(args.state_out) if args.state_out else Path(tempfile.gettempdir()) / "octy-docs-refresh.state.json"
    state_out.write_text(json.dumps(proposed, indent=2, sort_keys=True), encoding="utf-8")

    out.append("---")
    out.append(
        f"After updating the docs, seal the new marker with:\n\n"
        f"    python3 {Path(__file__).name} seal --docs-repo {docs_repo} --state-in {state_out}"
    )
    out.append(f"\n(Proposed marker written to {state_out}.)")

    print("\n".join(out))
    return 0


def cmd_seal(args: argparse.Namespace) -> int:
    docs_repo = Path(args.docs_repo).resolve()
    home_page = Path(args.home_page) if args.home_page else docs_repo / "index.mdx"
    branch = args.branch

    if args.from_current:
        sources = [s.strip() for s in args.source_repos.split(",") if s.strip()]
        repos_dir = Path(args.repos_dir).resolve() if args.repos_dir else docs_repo.parent
        located = {}
        for name in sources:
            path = locate_or_clone(name, repos_dir, args.org, allow_clone=not args.no_clone)
            if not args.no_fetch:
                fetch(path, branch)
            located[name] = path
        state = build_current_state(located, branch)
    else:
        state_in = Path(args.state_in) if args.state_in else Path(tempfile.gettempdir()) / "octy-docs-refresh.state.json"
        if not state_in.exists():
            eprint(
                f"[octy-docs-refresh] no marker file at {state_in}. Run `collect` first, "
                "or pass --from-current to seed from current heads."
            )
            return 1
        state = json.loads(state_in.read_text(encoding="utf-8"))
        state["lastRefreshedAt"] = now_iso()

    # `prevRefreshedAt` is collect/notify bookkeeping — keep it out of the marker.
    state.pop("prevRefreshedAt", None)
    write_state(home_page, state)
    eprint(f"[octy-docs-refresh] wrote freshness marker to {home_page}")
    return 0


# --------------------------------------------------------------------------- #
# Notify — report the refresh result back to Octy
# --------------------------------------------------------------------------- #
def remote_commit_url(repo: Path, sha: str) -> str | None:
    """Build a GitHub commit URL from the repo's origin remote."""
    url, rc = run(["git", "-C", str(repo), "remote", "get-url", "origin"], check=False)
    if rc != 0 or not url:
        return None
    u = url.strip()
    if u.startswith("git@"):
        # git@github.com:org/repo.git -> https://github.com/org/repo
        host, _, path = u[4:].partition(":")
        u = f"https://{host}/{path}"
    if u.endswith(".git"):
        u = u[:-4]
    return f"{u}/commit/{sha}"


def mdx_change_summary(repo: Path, commit: str) -> tuple[dict, list[dict]]:
    """Per-status counts + the list of changed .mdx pages in `commit`."""
    out = git(repo, "show", "--name-status", "--format=", "-M", commit, check=False)
    counts = {"A": 0, "M": 0, "D": 0, "R": 0, "C": 0}
    pages: list[dict] = []
    for line in out.splitlines():
        if not line.strip() or "\t" not in line:
            continue
        parts = line.split("\t")
        status = parts[0][0]  # R100 / C75 -> R / C
        path = parts[-1]      # new path for renames/copies
        if not path.endswith(".mdx"):
            continue
        counts[status] = counts.get(status, 0) + 1
        pages.append({"status": status, "path": path})
    return counts, pages


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do NOT follow redirects. A 3xx from this endpoint means an auth gate /
    middleware bounced us to a login page — surface it as the 3xx it is instead
    of silently following it to a 200 + HTML body that looks like success."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def post_to_octy(payload: dict, api_url: str, api_key: str) -> tuple[int, str]:
    # OZ_TO_OCTY_DOCS_REFRESH_API_URL is the FULL endpoint URL (not a base), e.g.
    # https://octy.loanlight.com/api/oz/docs-refresh — used verbatim.
    url = api_url
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with _OPENER.open(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        return 0, str(exc)


def cmd_notify(args: argparse.Namespace) -> int:
    docs_repo = Path(args.docs_repo).resolve()
    branch = args.branch

    api_url = args.api_url or os.environ.get("OZ_TO_OCTY_DOCS_REFRESH_API_URL")
    api_key = args.api_key or os.environ.get("OZ_TO_OCTY_API_KEY")
    if not api_url or not api_key:
        eprint(
            "[octy-docs-refresh] notify skipped: set OZ_TO_OCTY_DOCS_REFRESH_API_URL "
            "(the full Octy docs-refresh endpoint URL, e.g. "
            "https://octy.loanlight.com/api/oz/docs-refresh) and OZ_TO_OCTY_API_KEY "
            "in the environment."
        )
        return 1

    status = "no_changes" if args.no_changes else (args.status or "updated")

    # Reconciliation window + source provenance from the proposed marker.
    window_from = window_to = None
    source_repos = None
    state_in = (
        Path(args.state_in)
        if args.state_in
        else Path(tempfile.gettempdir()) / "octy-docs-refresh.state.json"
    )
    if state_in.exists():
        try:
            st = json.loads(state_in.read_text(encoding="utf-8"))
            window_to = st.get("lastRefreshedAt")
            window_from = st.get("prevRefreshedAt")
            source_repos = {
                name: {
                    "shortSha": info.get("shortSha"),
                    "committedAt": info.get("committedAt"),
                    "branch": info.get("branch", branch),
                }
                for name, info in (st.get("repos") or {}).items()
            }
        except (json.JSONDecodeError, OSError):
            pass

    detail_md = None
    if args.detail_file:
        detail_path = Path(args.detail_file)
        if detail_path.exists():
            detail_md = detail_path.read_text(encoding="utf-8")

    payload: dict = {
        "status": status,
        "detail_md": detail_md,
        "window_from": window_from,
        "window_to": window_to,
        "source_repos": source_repos,
        "warp_run_id": args.warp_run_id or os.environ.get("WARP_RUN_ID"),
    }

    if status == "no_changes":
        day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        payload["idempotency_key"] = f"docs-refresh:{day}"
        if args.headline:
            payload["headline"] = args.headline
    else:
        full = git(docs_repo, "rev-parse", args.commit)
        committed_at = git(docs_repo, "show", "-s", "--format=%cI", full)
        subject = git(docs_repo, "show", "-s", "--format=%s", full)
        counts, pages = mdx_change_summary(docs_repo, full)
        payload.update(
            {
                "idempotency_key": full,
                "headline": args.headline or subject,
                "commit_url": remote_commit_url(docs_repo, full),
                "committed_at": committed_at,
                "pages_added": counts.get("A", 0),
                "pages_updated": counts.get("M", 0),
                "pages_removed": counts.get("D", 0),
                "pages_renamed": counts.get("R", 0) + counts.get("C", 0),
                "changed_pages": pages,
            }
        )

    code, text = post_to_octy(payload, api_url, api_key)
    # A 2xx is necessary but NOT sufficient: an auth gate / misrouted endpoint can
    # answer 200 with an HTML login page, which would otherwise read as success
    # while the row never lands in Octy's DB. Require a real JSON `{ ok: true }`.
    if 200 <= code < 300:
        try:
            parsed = json.loads(text)
            ok = isinstance(parsed, dict) and parsed.get("ok") is True
        except json.JSONDecodeError:
            ok = False
        if ok:
            eprint(f"[octy-docs-refresh] notified Octy ({code}). {text[:300]}")
            return 0
        eprint(
            f"[octy-docs-refresh] notify FAILED: endpoint returned {code} but the "
            f"body is not a JSON success (got: {text[:300]!r}). This usually means "
            "the request was redirected to a login page — verify "
            "OZ_TO_OCTY_DOCS_REFRESH_API_URL points at /api/oz/docs-refresh and that "
            "the route is exempt from auth middleware. The docs are already committed "
            "& pushed — this only affects the Octy dashboard / Slack record."
        )
        return 1
    eprint(
        f"[octy-docs-refresh] notify FAILED ({code}): {text[:500]}\n"
        "The docs are already committed & pushed — this only affects the Octy "
        "dashboard / Slack record, not the docs. Safe to ignore for this run; "
        "check the endpoint URL / OZ_TO_OCTY_API_KEY if it recurs."
    )
    return 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--docs-repo", default=os.environ.get("OCTY_DOCS_REPO", "."),
                   help="Path to the docs repo (default: $OCTY_DOCS_REPO or CWD).")
    p.add_argument("--home-page", default=os.environ.get("OCTY_DOCS_HOME_PAGE"),
                   help="Path to the home page holding the marker (default: <docs-repo>/index.mdx).")
    p.add_argument("--repos-dir", default=os.environ.get("OCTY_REPOS_DIR"),
                   help="Directory containing the source repos (default: parent of docs repo).")
    p.add_argument("--source-repos", default=os.environ.get("OCTY_SOURCE_REPOS", ",".join(DEFAULT_SOURCES)),
                   help="Comma-separated source repo names.")
    p.add_argument("--org", default=os.environ.get("OCTY_GH_ORG", DEFAULT_ORG),
                   help="GitHub org used when cloning a missing repo.")
    p.add_argument("--branch", default=os.environ.get("OCTY_BRANCH", DEFAULT_BRANCH),
                   help="Integration branch to track (default: main).")
    p.add_argument("--no-fetch", action="store_true", help="Skip git fetch (use existing refs).")
    p.add_argument("--no-clone", action="store_true", help="Error instead of cloning a missing repo.")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="octy-docs-refresh helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="Compute the change window and print a manifest.")
    add_common(p_collect)
    p_collect.add_argument("--state-out", help="Where to write the proposed marker JSON.")
    p_collect.set_defaults(func=cmd_collect)

    p_seal = sub.add_parser("seal", help="Write the freshness marker into the home page.")
    add_common(p_seal)
    p_seal.add_argument("--state-in", help="Marker JSON produced by `collect`.")
    p_seal.add_argument("--from-current", action="store_true",
                        help="Recompute current origin/<branch> heads instead of reading --state-in.")
    p_seal.set_defaults(func=cmd_seal)

    p_notify = sub.add_parser("notify", help="Report the refresh result back to Octy.")
    add_common(p_notify)
    p_notify.add_argument("--commit", default="HEAD",
                          help="Commit to report (default: HEAD — the refresh commit).")
    p_notify.add_argument("--headline",
                          help="One-liner summary (defaults to the commit subject).")
    p_notify.add_argument("--detail-file",
                          help="Path to a markdown file with the medium-length detail.")
    p_notify.add_argument("--status", choices=["updated", "no_changes", "failed"],
                          help="Override the reported status (default: updated).")
    p_notify.add_argument("--no-changes", action="store_true",
                          help="Report a no-changes night (no commit; status=no_changes).")
    p_notify.add_argument("--state-in",
                          help="Proposed marker JSON from `collect` (for window + provenance).")
    p_notify.add_argument("--warp-run-id", help="Warp/Oz run id, if available.")
    p_notify.add_argument("--api-url",
                          help="Full Octy docs-refresh endpoint URL "
                               "(default: $OZ_TO_OCTY_DOCS_REFRESH_API_URL).")
    p_notify.add_argument("--api-key",
                          help="Shared secret (default: $OZ_TO_OCTY_API_KEY).")
    p_notify.set_defaults(func=cmd_notify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, FileNotFoundError) as exc:
        eprint(f"[octy-docs-refresh] error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
