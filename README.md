# Why

Repository intelligence that connects source code to the decisions and history behind it.

## Current phase

**Phase 1 — Product shell and interactive workspace.** The landing page, sample code map, decision map, entity inspector, and assistant panel are in place. Sample content is explicitly marked as illustrative.

**Phase 2 — Live repository indexing and Neo4j.** The API validates public GitHub URLs, reads bounded repository/source/history data without executing code, and stores a workspace-isolated graph in Neo4j. The workspace can load live code and history graphs. It requires the user's own Neo4j Aura credentials.

## Run locally

Requirements: Node.js 20.19+ or 22.12+.

Python 3.11+ and `uv` are required for the API. Copy `server/.env.example` to `server/.env` and configure a Neo4j Aura database before live repository indexing. Do not commit `server/.env`.

```sh
npm install
npm run dev
```

Open the local URL printed by Vite and choose **Explore sample** to inspect the workspace.

For a live analysis, paste a public GitHub repository URL. The app reads the default-branch tree, a bounded recent history window, and source files for local import mapping. It stores file paths and relationships, commit/PR/issue discussion records, and index metadata in your configured Neo4j database. Source files are fetched on demand for the in-app preview. The web service does not run repository code.

## Checks

```sh
npm run build
npm run lint
npm audit
```

## Product principles

- Evidence must be inspectable and distinguish direct sources from inferred relationships.
- The web edition starts with public GitHub repositories; local folder import belongs to the later desktop edition.
- Repository code is never executed during indexing.
- Cloud graph storage and AI providers must be disclosed and opt-in.
- Each browser receives an anonymous, signed, HTTP-only workspace session; repository graph reads and deletion are scoped to that session.

## Indexing limits

The initial importer is intentionally bounded for public GitHub API limits and safe web-service memory use. It indexes at most 1,800 file paths and parses imports from up to 120 source files (up to 8 MB total). It captures the latest 8 commits, latest 8 pull requests, and detailed files/reviews/comments for the latest 6 pull requests. These limits are visible as coverage metadata; this is not a claim of complete repository history. Re-indexing replaces that workspace's repository snapshot atomically.
