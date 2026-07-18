# @memento/hook

A [Claude Code](https://code.claude.com) **SessionEnd** hook that captures your
agent transcript and ships it to Memento — redacted, gzipped, and **fail-silent**.
It never blocks or slows a session: any error (no network, no git, bad input)
exits `0` immediately.

## Install

```bash
npx @memento/hook install --api-key <your-key>
```

This does two things:

1. Adds a `SessionEnd` hook to your project's `.claude/settings.json` (safe to
   commit — it contains no secrets):

   ```json
   {
     "hooks": {
       "SessionEnd": [
         { "hooks": [{ "type": "command", "command": "npx -y @memento/hook run", "timeout": 15 }] }
       ]
     }
   }
   ```

2. Saves your API key + ingest URL to `~/.claude/memento-hook/config.json`
   (`chmod 600`, **never committed**). Each engineer installs with their own key.

Re-running `install` is idempotent — it refreshes the entry in place instead of
adding a duplicate.

### Options

| Flag | Description |
|---|---|
| `--api-key <key>` | Your Memento API key (or set `MEMENTO_API_KEY`). |
| `--url <baseUrl>` | Ingest base URL. Defaults to the production endpoint. |

### Environment overrides

| Var | Effect |
|---|---|
| `MEMENTO_API_KEY` | Overrides the stored API key. |
| `MEMENTO_INGEST_URL` | Overrides the stored ingest base URL. |

## Uninstall

```bash
npx @memento/hook uninstall          # remove the hook from .claude/settings.json
npx @memento/hook uninstall --purge  # also delete the saved API key
```

Only Memento's own hook entry is removed — other hooks and settings are left untouched.

## What gets sent

On session end the hook reads `session_id` + `transcript_path` from stdin,
resolves the git `origin` remote and current branch, redacts secrets from the
raw transcript, gzips it, and POSTs:

```
POST {baseUrl}/ingest/agent-sessions
Authorization: Bearer <api-key>
Content-Type: application/x-ndjson
Content-Encoding: gzip
X-Session-Id: <session_id>
X-Git-Branch: <branch>
X-Git-Remote: <origin url>
X-Token-Estimate: <int>
X-Hook-Version: <version>

<gzipped, redacted JSONL transcript>
```

The server resolves your user/org/repo from the API key and remote. The full
transcript is sent on every session end, and the endpoint upserts on
`session_id` — so a resumed session that re-fires simply replaces its stored
transcript with the longer one.

## Secret redaction

Before upload, transcripts are scrubbed of PEM private keys, provider API keys
(Anthropic, OpenAI, GitHub, Slack, Google, AWS), JWTs, `Authorization` bearer
headers, credentials embedded in URLs, and generic `.env`-style secret
assignments. This is **best-effort defence in depth**, not a guarantee — treat
ingested transcripts as sensitive regardless.

## Development

```bash
npm install
npm run build   # compile src -> dist
npm test        # compile + run node:test suites
```

Zero runtime dependencies — only Node built-ins (`node:zlib`, `node:child_process`,
`node:fs`, global `fetch`). Requires Node 18+.
