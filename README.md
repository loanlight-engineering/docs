# LoanLight engineering documentation

Internal engineering documentation for **LoanLight** — the AI-powered automated underwriting system (AUS) — its audit pipeline, AI agents, integrations, lender portal, Partner API, and the **Octy** self-healing platform.

This site is built with [Mintlify](https://mintlify.com). It is the externalized knowledge base that the Octy AI agent consumes via the Mintlify MCP server, and the reference engineers use day to day.

> The documented systems live in sibling repositories (`loanlight-api`, `loanlight-integrations`, `loanlight-shared`, `octy`). **The source code is the source of truth.** When a doc and the code disagree, fix the doc.

## Structure

- `docs.json` — site configuration and navigation (tabs → groups → pages).
- `*.mdx` — documentation pages, organized by area: `overview/`, `concepts/`, `pipeline/`, `agents/`, `subsystems/`, `data-model/`, `lender-config/`, `storage/`, `ops/`, `integrations/`, `partner-api/`, `portal/`, `octy/`, `runbooks/`.
- `AGENTS.md` — terminology, style, and content boundaries for any AI tool editing these docs.
- `logo/`, `favicon.svg` — LoanLight branding.

## Local development

Install the [Mintlify CLI](https://www.npmjs.com/package/mint):

```bash
npm i -g mint
```

Then, from the repo root (where `docs.json` lives):

```bash
mint dev              # preview at http://localhost:3000
mint broken-links     # check internal links
mint validate         # validate the build
```

## Publishing

Changes deploy automatically after pushing to the default branch once the Mintlify GitHub app is installed. The docs are internal (not public-facing yet) but are written to a publishable standard.

## Contributing

- Match the conventions in `AGENTS.md`: second person, sentence-case headings, code formatting for names, no marketing language or decorative emoji.
- Every page needs `title` and `description` frontmatter.
- Add new pages to `docs.json` navigation or they stay hidden.
- Ground every claim in the source. Flag common misconceptions with a `<Note>`; mark anything unverified with a `{/* TODO */}` comment.
