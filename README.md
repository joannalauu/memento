# Memento

Memento is an engineering-memory system for teams that ship with coding agents. It captures Claude Code session transcripts, distills them together with merged PR diffs into structured memories anchored to code, and stores them in an org-wide knowledge graph. That memory is then served back two ways:

- **A web app** where engineers explore the graph and ask questions over it ("why does X work this way?"), with answers streamed alongside a live animation of the memory graph traversal that produced them.
- **An MCP server** so coding agents can query the same memory mid-session — `find_related_context`, `check_consistency`, `find_entry_points`, `walk_graph` — plus GitHub-backed tools (`search_code`, `get_file`, `list_repos`, …).

The capture loop: the [`@memento-ai/hook`](packages/claude-hook/README.md) npm package installs a Claude Code `SessionEnd` hook that redacts, gzips, and ships each transcript to the API. When a PR merges, a GitHub App webhook kicks off a distillation job that matches the PR's diff to the agent sessions that produced it and writes the resulting memories into the graph.

## Repo structure

Three deliverables in one repo:

```
app/                    ← FastAPI backend (Python 3.13, uv)
├── main.py             ← router registration
├── claude_hook/        ← transcript ingest endpoint (POST /ingest/agent-sessions)
├── distillation/       ← PR diff + sessions → memories pipeline (async job queue)
├── context_engine/     ← code anchors, retrieval, staleness, consistency checks
├── graph/              ← graph read APIs, live WS updates, SSE Q&A (/graph/ask)
├── mcp/                ← MCP server (JSON-RPC 2.0, Streamable HTTP) at /mcp
├── github/             ← GitHub App client, webhook, install flow, code tools
├── backboard/          ← LLM client (Backboard SDK) used by distillation & Q&A
├── orgs/               ← users, orgs, invites (custom Beanie User model)
├── api_auth/           ← API keys used by the hook and MCP clients
└── hackplate/          ← framework internals — do not modify

frontend/               ← React SPA (Vite, TypeScript, Tailwind, shadcn)
└── src/features/       ← graph explorer + ask, admin, api-keys, auth, documents

packages/claude-hook/   ← @memento-ai/hook — the Claude Code SessionEnd hook (npm)
```

The backend is built on **Hackplate**, a FastAPI template where framework internals live in `app/hackplate/` and integrations ("plates") are selected in `.env`. Memento runs on the **mongo** DB plate (the user model and all domain models are Beanie documents) with **local** JWT auth. See [CLAUDE.md](CLAUDE.md) for the full Hackplate reference (CLI commands, plate system, conventions).

## Dev environment

Prerequisites: Python 3.13+, Node 18+, MongoDB (local or a connection string), and a [Backboard](https://app.backboard.io) API key.

**Backend**

```bash
pip install uv && uv sync
hackplate init                 # creates .env, generates SECRET_KEY, installs pre-commit
# fill in .env: MONGO_* (or MONGO_URL), BACKBOARD_API_KEY, GITHUB_* (see below)
hackplate run                  # uvicorn on :8000, hot reload
```

**Frontend**

```bash
cd frontend
npm install
npm run dev                    # Vite on :5173, talks to the API at :8000 (CORS pre-configured)
```

**Hook package**

```bash
cd packages/claude-hook
npm install
npm run build && npm test
```

To exercise the full capture loop locally, point the hook at your dev server:

```bash
npx @memento-ai/hook install --api-key <key> --url http://localhost:8000
```

(API keys are minted in the web app under **API Keys**.)

`.env.example` documents every variable.

## Production requirements

Memento is a hosted product; several pieces only work when the API is publicly reachable:

- **Host the API** at a public URL. The MCP server (`<host>/mcp`) and the ingest endpoint (`<host>/ingest/agent-sessions`) are called from engineers' machines and agents, and the GitHub webhook must be able to reach `<host>/github/webhook`. Run with `hackplate run -m prod` (uvicorn, `HACKPLATE_WORKERS` workers) or the provided `Dockerfile` / `docker-compose.yml`.
- **Publish `@memento-ai/hook` to the npm registry.** The install flow is `npx @memento-ai/hook install`, and the committed hook entry runs `npx -y @memento-ai/hook run` on every session end — both resolve the package from npm. Its default ingest URL must point at the production API.
- **Host the frontend.** `cd frontend && npm run build` produces a static SPA in `dist/`; serve it from any static host.
- **Memento GitHub App.** Create a GitHub App (Settings → Developer settings → GitHub Apps) and set in `.env`: `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY` (or `GITHUB_PRIVATE_KEY_PATH`), and — for the install/webhook flow — `GITHUB_APP_SLUG` and `GITHUB_WEBHOOK_SECRET`. Point the App's **Setup URL** at `<host>/github/setup` and its **webhook URL** at `<host>/github/webhook`. Users install the Memento GitHub App into their own accounts through the GitHub integration flow in the app.
- **MongoDB** (e.g. Atlas) — set `HACKPLATE_DB=mongo` and `MONGO_URL`.
- **Env** - `.env.example` documents all required env variables.
