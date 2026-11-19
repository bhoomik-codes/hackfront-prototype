# Why

Repository intelligence that connects source code to the decisions and history behind it.

## Current phase

**Phase 1 — Product shell and interactive workspace.** The landing page, sample code map, decision map, entity inspector, and assistant panel are in place. Sample content is explicitly marked as illustrative.

**Phase 2 — Live repository indexing and Neo4j.** The API validates public GitHub URLs, reads bounded repository/source/history data without executing code, and stores a workspace-isolated graph in Neo4j. The workspace can load live code and history graphs. It requires the user's own Neo4j Aura credentials.

**Phase 3 — Evidence assistant with BYOK.** The assistant can answer questions from connected commits, pull requests, issues, discussions, and selected file content. It only accepts citation IDs present in the retrieved graph and declines answers with no verifiable citations. OpenAI-compatible providers are called directly from the browser after per-question consent. The API key stays in memory for the current tab and is not stored in browser storage or sent to this app's API. Confidence is based on the number and variety of validated citations, not the model's self-rating.

**Phase 4 — Persistent browser workspace.** Indexed repositories can be reopened after a refresh from a list scoped to the signed browser workspace. Users can remove a repository graph from Aura from the workspace.

**Phase 5 — Conservative supersession links.** A later pull request or its discussion creates a `SUPERSEDES` relationship only when its text explicitly uses replacement/deprecation/revert language near a reference to an older pull request in the indexed window. The workspace highlights both ends of that link. No link is treated as proof that an older decision remains current.

## Run locally

Requirements: Node.js 20.19+ or 22.12+.

Python 3.11+ and `uv` are required for the API. Copy `server/.env.example` to `server/.env`, configure a Neo4j Aura database, and replace `SESSION_SECRET` with a private random value of at least 32 characters before live repository indexing. Keep the same secret between restarts so the browser can reopen and delete its stored graph. Do not commit `server/.env`.

```sh
npm install
npm run dev
```

Open the local URL printed by Vite and choose **Explore sample** to inspect the workspace.

For a live analysis, paste a public GitHub repository URL and confirm the data handling notice. The app reads the default-branch tree, a bounded recent history window, and a small source subset for import mapping. It stores file paths and relationships, commit/PR/issue metadata and discussion records in your configured Neo4j database. Source file contents are fetched from GitHub on demand for in-app preview and are not persisted in Neo4j. The web service does not run repository code.

To use the assistant, open its settings, enter an OpenAI-compatible chat completions base URL, model name, and API key. The key is cleared when the tab is closed or reloaded. Each live-repository question requires explicit consent before the selected source and connected history are sent directly to the provider. Do not enter credentials into a provider URL.

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
- Model API keys are held in page memory only. The user must opt in before repository evidence is sent to a model provider.
- Graph and assistant coverage is bounded and cannot guarantee a complete history or a correct interpretation of intent. Inspect linked source records before relying on conclusions.
- Supersession detection is deliberately limited to explicit wording and pull request references; it is not a semantic decision classifier. Absence of a detected link is inconclusive.

## Indexing limits

The initial importer is intentionally bounded for public GitHub API limits and safe web-service memory use. It indexes at most 1,800 file paths and parses imports from up to 120 source files (up to 8 MB total). It captures the latest 8 commits, latest 8 pull requests, and detailed files/reviews/comments for the latest 6 pull requests. These limits are visible as coverage metadata; this is not a claim of complete repository history. Re-indexing replaces that workspace's repository snapshot atomically.
