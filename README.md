# Why

Repository intelligence that connects source code to the decisions and history behind it.

## Current phase

**Phase 1 — Product shell and interactive workspace.** The landing page, sample code map, decision map, entity inspector, and assistant panel are in place. Sample content is explicitly marked as illustrative.

**Phase 2 — Live repository indexing and Neo4j.** The API validates public GitHub URLs, reads bounded repository/source/history data without executing code, and stores a workspace-isolated graph in Neo4j. The workspace can load live code and history graphs. It requires the user's own Neo4j Aura credentials.

**Phase 3 — Evidence assistant with BYOK.** The assistant can answer questions from connected commits, pull requests, issues, discussions, and selected file content. It only accepts citation IDs present in the retrieved graph and declines answers with no verifiable citations. OpenAI-compatible providers are called directly from the browser after per-question consent. The API key stays in memory for the current tab and is not stored in browser storage or sent to this app's API. Confidence is based on the number and variety of validated citations, not the model's self-rating.

**Phase 4 — Temporary browser workspace.** Anonymous repository graphs are available only while the browser workspace is active. The app requests cleanup on exit, deletes the current index when leaving it, and expires inactive graphs after two hours. A periodic Aura cleanup removes expired and legacy anonymous graph data.

**Phase 4.3 — Sign-in and opt-in saved indexes.** Users can authenticate with GitHub, Google, or an email/password account. Email signup requires a one-time email verification code; users can optionally enable email-code verification at each password sign-in from **Account settings**. Passwords are Argon2id hashes, verification codes are HMAC digests, and account records live in Neo4j without storing plaintext passwords. Google and email use separate account IDs so Google sign-in cannot bypass an email account's optional verification step; existing GitHub account IDs remain compatible. Codes expire after 10 minutes, allow five verification attempts, and are rate-limited. SMTP credentials never reach the browser. Saved graph roots are associated with the authenticated account; indexes remain temporary unless a user signs in and presses **Save index**. OAuth access tokens are discarded after profile lookup and are not stored.

**Phase 5 — Conservative supersession links.** A later pull request or its discussion creates a `SUPERSEDES` relationship only when its text explicitly uses replacement/deprecation/revert language near a reference to an older pull request in the indexed window. The workspace highlights both ends of that link. No link is treated as proof that an older decision remains current.

## Run locally

Requirements: Node.js 20.19+ or 22.12+.

Python 3.11+ and `uv` are required for the API. Copy `server/.env.example` to `server/.env`, configure a Neo4j Aura database, and replace `SESSION_SECRET` with a private random value of at least 32 characters before live repository indexing. Keep the same secret between restarts so the browser can reopen and delete its stored graph. Do not commit `server/.env`.

```sh
npm install
npm run dev
```

Open the local URL printed by Vite and choose **Explore sample** to inspect the workspace.

To enable persistent saves locally, create a GitHub OAuth App with callback URL `http://127.0.0.1:8765/api/auth/github/callback`, then set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` in `server/.env`. For Google, create a web OAuth client with callback URL `http://127.0.0.1:8765/api/auth/google/callback` and set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. To enable email signup and code delivery, set `SMTP_USERNAME` and `SMTP_PASSWORD` to a Gmail account and Google App Password; `SMTP_FROM` may be left blank to use the username. Do not put credentials in `VITE_*` variables or frontend code. Set `FRONTEND_URL` to your local Vite origin. Each sign-in method is optional; saved indexes require an authenticated account and an explicit save action.

For a live analysis, paste a public GitHub repository URL and confirm the data handling notice. The app reads the default-branch tree, a bounded recent history window, and a small source subset for import mapping. It stores file paths and relationships, commit/PR/issue metadata and discussion records in your configured Neo4j database. Source file contents are fetched from GitHub on demand for in-app preview and are not persisted in Neo4j. The web service does not run repository code.

To use the assistant, open its settings, enter an OpenAI-compatible chat completions base URL, model name, and API key. The key is cleared when the tab is closed or reloaded. Each live-repository question requires explicit consent before the selected source and connected history are sent directly to the provider. Do not enter credentials into a provider URL.

## Checks

```sh
npm run build
npm run lint
npm audit
```

## Free hosted demo

The repository includes a `Dockerfile` and Render Blueprint for a single-origin deployment. The service builds the React app and serves it together with FastAPI on one Render URL, avoiding cross-site sign-in cookies. Use a Neo4j AuraDB Free instance for graph storage. Render's free API sleeps after 15 minutes without traffic; the first request after sleep can take about a minute. AuraDB Free is limited to 1 GB storage, 200,000 nodes, and 400,000 relationships, so this is appropriate for a hackathon demo rather than production. Never put Neo4j or GitHub OAuth secrets in GitHub Pages, Vite variables, or committed files.

The production provider callbacks must be `https://<your-render-host>/api/auth/github/callback` and `https://<your-render-host>/api/auth/google/callback`. Set `FRONTEND_URL` to that host with a trailing slash and `ALLOWED_ORIGINS` to its exact origin. `SESSION_SECRET` is generated by the Render Blueprint. For Gmail SMTP, use `smtp.gmail.com` on port `587`, enable 2-step verification on the sending account, create a Google App Password, then fill `SMTP_USERNAME`, `SMTP_PASSWORD`, and optionally `SMTP_FROM` in Render's Environment page. Never use your normal Google account password or commit any of these values.

## Product principles

- Evidence must be inspectable and distinguish direct sources from inferred relationships.
- The web edition starts with public GitHub repositories; local folder import belongs to the later desktop edition.
- Repository code is never executed during indexing.
- Cloud graph storage and AI providers must be disclosed and opt-in.
- Each browser receives a signed, HTTP-only workspace session. Temporary graphs are isolated to that workspace; saved graphs are accessible only to the authenticated account that owns them.
- Repository indexes stay temporary unless a signed-in user explicitly saves them. Saved data is scoped to an immutable provider identity or keyed email identity and can be deleted from the workspace.
- Model API keys are held in page memory only. The user must opt in before repository evidence is sent to a model provider.
- Graph and assistant coverage is bounded and cannot guarantee a complete history or a correct interpretation of intent. Inspect linked source records before relying on conclusions.
- Supersession detection is deliberately limited to explicit wording and pull request references; it is not a semantic decision classifier. Absence of a detected link is inconclusive.
- Persistent retention is tied to a verified account and requires the separate explicit save action.

## Indexing limits

The initial importer is intentionally bounded for public GitHub API limits and safe web-service memory use. It indexes at most 1,800 file paths and parses imports from up to 120 source files (up to 8 MB total). It captures the latest 8 commits, latest 8 pull requests, and detailed files/reviews/comments for the latest 6 pull requests. These limits are visible as coverage metadata; this is not a claim of complete repository history. Re-indexing replaces that workspace's repository snapshot atomically.
