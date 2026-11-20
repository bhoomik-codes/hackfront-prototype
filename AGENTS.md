# Instructions for AI agents

## Preserve the product

- Read `README.md` and inspect the relevant existing flow before changing it. Keep working behavior intact while implementing the requested change.
- The web app must keep its public-repository-only import, bounded source and history coverage, interactive code and decision maps, file preview/editor, evidence citations, BYOK consent, authentication, saved-index controls, account settings, and temporary-index cleanup.
- Do not execute repository code. Do not store repository source contents in Neo4j. Do not expose credentials, tokens, or passwords to frontend code, logs, commits, or tool output.
- Authentication and destructive account operations must remain user-scoped, origin-checked, and protected by the existing session model. Email account deletion requires the current password; account deletion must revoke existing sessions and remove the account's saved indexes.

## GitHub indexing limits are a product requirement

- `server/app/github.py` contains separate paths for GitHub REST metadata/history and raw file contents. A GitHub API quota error is not fixed by changing file chunk size.
- Keep selected-file previews streamed in `SOURCE_CHUNK_BYTES` chunks. Import-time source parsing must stay bounded by `max_source_files`, `max_source_bytes`, and `max_source_total_bytes`, and continue batching requests to `raw.githubusercontent.com`.
- Keep REST API calls within `API_REQUEST_BUDGET`, preserve the shared response cache and single-flight request coalescing, and use GitHub rate-limit/reset headers to pause imports safely. Do not reintroduce per-commit or per-pull-request fan-out without measuring the complete worst-case call count and updating this budget.
- GitHub API calls are shared across hosted users. `GITHUB_API_TOKEN` is an optional server-only setting; never move it to a `VITE_*` variable or pass it to the browser.
- Keep coverage metadata and the UI error understandable when indexing is partial or paused by a quota limit. Do not claim that the imported history is complete.

## Project layout and verification

- Frontend: `src/App.tsx`, `src/App.css`, `src/services/`, and `src/types.ts`.
- API and indexing: `server/app/main.py`, `server/app/github.py`, `server/app/graph.py`, `server/app/accounts.py`, and `server/app/schemas.py`.
- Before finishing code changes, run `npm run build`, `npm run lint`, `uv --directory server run ruff check app`, `uv --directory server run python -m compileall -q app`, and `uv --directory server run bandit -q -r app`. Run `npm audit` when dependency manifests or lockfiles change.
- The user has explicitly requested a commit and push after each completed update. After checks pass, commit only the intended files and push the current branch. Never force-push or commit secret values, local `.env` files, generated credentials, or repository data.
