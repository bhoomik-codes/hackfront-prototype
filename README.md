# Why

Repository intelligence that connects source code to the decisions and history behind it.

## Current phase

**Phase 1 — Product shell and interactive workspace.** The landing page, sample code map, decision map, entity inspector, and assistant panel are in place. Sample content is explicitly marked as illustrative. Live GitHub indexing, Neo4j persistence, and model-backed answers are planned for the following phases.

## Run locally

Requirements: Node.js 20.19+ or 22.12+.

```sh
npm install
npm run dev
```

Open the local URL printed by Vite and choose **Explore sample** to inspect the workspace.

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
