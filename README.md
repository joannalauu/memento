# Hackplate

A FastAPI metaframework for 24–48 hour hackathons. Clone it, run one command, and have a working API with auth, a database, and a dev CLI, configured by swapping plates, not rewriting code.

## How it works

Hackplate separates two concerns:

- **Framework internals** live in `app/hackplate/` and are not meant to be edited.
- **Your code** lives in `app/` alongside the framework — routes, schemas, models, feature slices.

Backend integrations are called **plates**. Active plates are selected via environment variables; switching plates is a one-liner that requires no changes to your route handlers.

```
HACKPLATE_DB=sqlite    # sqlite | postgres | supabase | mongo
HACKPLATE_AUTH=local   # local | auth0 | keycloak
```

## Quickstart

```bash
git clone <repo> my-project && cd my-project
pip install uv && uv sync
hackplate init
hackplate run
```

`hackplate init` installs dependencies, creates `.env` from the template, prompts for your initial plate selections, generates a secret key, and installs pre-commit hooks. Fill in any remaining `.env` values (database URLs, OAuth credentials) before running.

## Plates

### Database

| Plate | Driver | Notes |
|---|---|---|
| `sqlite` | aiosqlite | Zero config, default |
| `postgres` | asyncpg | Provide `POSTGRES_URL` or individual fields |
| `supabase` | asyncpg | Same as postgres, SSL required by default |
| `mongo` | Beanie ODM | Motor async driver |

SQLite, Postgres, and Supabase use SQLModel/SQLAlchemy for schema management. MongoDB uses Beanie document models. Switching between SQL and Mongo requires updating the user model base class (see [User Model](#user-model)).

### Auth

| Plate | Description |
|---|---|
| `local` | JWT-based, no external dependencies |
| `auth0` | Auth0 OAuth, requires Auth0 tenant credentials |
| `keycloak` | Keycloak SSO, requires Docker or an external instance |

Auth plates automatically register login/logout/token routes and provide a `get_current_user` dependency available through `app/dependencies.py`.

## Project structure

```
app/
├── main.py              ← register routers here
├── lifespan.py          ← pre/post startup hooks (user-editable)
├── dependencies.py      ← get_db and get_current_user wrappers (user-editable)
└── hackplate/           ← framework internals — do not modify
    ├── cli.py
    ├── config.py
    ├── hackplate_types.py
    ├── websocket.py
    ├── lifespan.py
    ├── toml_settings.py
    └── plates/
        ├── auth_plates/
        │   ├── local/
        │   ├── auth0/
        │   └── keycloak/
        └── db_plates/
            ├── sqlite/
            ├── postgres/
            └── mongo/
```

Add your own code as vertical slices under `app/`. Register routers in `app/main.py` inside `register_routes()`.

## Adding a feature

```bash
hackplate startfeature <name>
```

Creates `app/<name>/` with `routes.py`, `schemas.py`, `crud.py`, `models.py`, and `__init__.py`, and registers the model in `migrations/register_models.py`.

```bash
hackplate dropfeature <name>   # removes the directory and its model registration
```

## User model

The active user model is set in `pyproject.toml`:

```toml
[tool.hackplate]
auth_user_model = "app.hackplate.user.models.User"
```

- SQL plates: model must inherit from `AbstractUser` (SQLModel)
- Mongo plate: model must inherit from `AbstractUserDocument` (Beanie Document)

The default `User` lives at `app/hackplate/user/models.py`. To extend it, create your own model class in `app/`, update `auth_user_model`, and register it in `migrations/register_models.py`.

## Configuration

Two sources, two purposes:

- **`.env`** — deployment-varying values: plate selection, database URLs, OAuth credentials, secret keys.
- **`pyproject.toml` `[tool.hackplate]`** — structural project decisions: active user model, Alembic toggle.

Never put deployment-varying values in `pyproject.toml` or structural decisions in `.env`.

## CLI reference

| Command | Description |
|---|---|
| `hackplate run` | Start uvicorn (`-m dev`\|`prod`, default `dev`; `prod` runs `HACKPLATE_WORKERS` workers, no reload) |
| `hackplate run --docker-compose` | Start full stack via Docker Compose (`-m dev`\|`prod` selects the compose profile) |
| `hackplate init` | First-time repo setup (runs once) |
| `hackplate getplates` | Show active auth and db plates |
| `hackplate setplate auth <plate>` | Switch auth plate |
| `hackplate setplate db <plate>` | Switch db plate |
| `hackplate setmode safe\|fast` | Switch Claude Code operating mode |
| `hackplate getmode` | Show the current Claude Code operating mode |
| `hackplate startfeature <name>` | Scaffold a vertical slice under `app/` |
| `hackplate dropfeature <name>` | Remove a feature directory |
| `hackplate regenkey` | Regenerate `SECRET_KEY` in `.env` |
| `hackplate precommit` | Install and run pre-commit on all files |
| `hackplate clean` | Remove cache and build artifacts |
| `hackplate kcsync` | Sync Keycloak realm config to `settings.json` |
| `hackplate down` | Stop Docker containers |

Run `hackplate --help` for the full reference.

## Migrations

Schema is managed automatically by default (`alembic = false` in `pyproject.toml` — SQLModel creates tables on startup). To switch to Alembic:

```toml
[tool.hackplate.db]
alembic = true
```

```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

## WebSockets

`app/hackplate/websocket.py` exports `WSConnectionManager` for broadcasting to all connected clients, and `get_db_from_ws` for injecting a database session into WebSocket handlers.

## Stack

- **FastAPI** + **Uvicorn** — async web framework and server
- **SQLModel** / **SQLAlchemy** (async) — ORM and schema management for SQL plates
- **Beanie** — async ODM for MongoDB
- **fastapi-users** — user management primitives
- **Pydantic v2** + **pydantic-settings** — validation and config
- **Alembic** — optional SQL migrations
- **Typer** — CLI framework
- **uv** — dependency and virtual environment management
- **pytest** + **pytest-asyncio** — testing

## For AI agents

Read `CLAUDE.md` (reasoning-friendly, supports `@import`) and `AGENTS.md` (imperative, cross-tool standard) before making changes. The `.claude/settings.json` pre-configures allowed commands and post-edit hooks.

`CLAUDE.md` imports the gitignored `modes/CLAUDE.mode.md`, which re-exports either `modes/CLAUDE.safe.md` or `modes/CLAUDE.fast.md`. Switch modes with `hackplate setmode safe|fast`; `hackplate init` creates `modes/CLAUDE.mode.md` defaulting to `safe`.

The single extension pattern: add routes in `app/main.py` via `register_routes()`, inject dependencies from `app/dependencies.py`, and scaffold new slices with `hackplate startfeature`. Don't modify `app/hackplate/` unless extending a plate interface.
