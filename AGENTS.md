# Documentation project instructions

## About this project

- This is the **internal engineering documentation** for **LoanLight**, an AI-powered automated underwriting system (AUS) for mortgage loans.
- The docs are read by LoanLight engineers and by the **Octy** AI agent (via the Mintlify MCP server). Accuracy and exact names matter more than prose.
- Built on [Mintlify](https://mintlify.com). Pages are MDX files with YAML frontmatter. Site configuration lives in `docs.json`.
- The documented systems live in sibling repositories: `loanlight-api`, `loanlight-integrations`, `loanlight-shared`, and `octy`. **The source code is the source of truth** — when code contradicts a doc, the code wins; fix the doc.
- Use the Mintlify MCP server `https://mcp.mintlify.com` to edit content/settings, and `https://www.mintlify.com/docs/mcp` to query Mintlify product knowledge.

## Terminology

- **LoanLight** — the product (one word, capital L twice). The backend is the **LoanLight API** (`loanlight-api`).
- **AUS** — automated underwriting system. **LOS** — loan origination system (Encompass).
- **Audit run** — the central unit of work; an audit of one loan. Stored in `audit_runs`. Prefer "audit run", not "job".
- **Agent** — a LangGraph AI agent in the audit pipeline (e.g. PII-Validation, Document-Health). Use the exact `agent_type` string when naming one.
- **EPC** — Encompass Partner Connect, the production Encompass integration. **Dev Connect** is the deprecated legacy path.
- **classify-split** — the two-phase document classification pipeline. `split_type` is the Phase 1 bucket; `classification_type` is the Phase 2 fine type.
- **URLA** / **Form 1003** — the residential loan application; **MISMO** is the XML format it arrives in.
- **Octy** — LoanLight's internal self-healing AI platform. **Oz** is the agent Octy runs. Octy's skills are called skills (or, informally, "tentacles").
- **Lender** — a tenant. A lender's config code is `lenders.config_profile` (there is no `lender_code` column on `lenders`).
- Use **portal** for the lender-facing web app (`loanlight-api/packages/app`), not "frontend".

## Style preferences

- Second person ("you"), active voice. One idea per sentence.
- Sentence case for headings ("Audit pipeline overview", not "Audit Pipeline Overview").
- Bold for UI elements (Click **Bug Reports**). Code formatting for file names, paths, commands, table/column names, env vars, and endpoints.
- No marketing language (powerful, seamless, robust), no filler ("it's important to note"), no decorative emoji.
- Every code block has a language tag. Internal links are root-relative without the extension (`/agents/pii-validation`).
- Lead with what something is before how it works. Put prerequisites first in procedures.

## Content boundaries

- These docs are **internal** (not public-facing yet), but write to a publishable standard.
- Document the real, current behaviour from code — including known drift, deprecations, and gotchas. Flag common misconceptions with a `<Note>`.
- The Partner API section is the seed for future customer-facing docs; keep it accurate and self-contained.
- Do not paste secrets, real API keys, customer PII, or live tokens into any page. Use realistic placeholder values in examples.
