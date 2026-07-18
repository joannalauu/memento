# CLAUDE.md

Guidelines for Claude Code when working in this repository.

@modes/CLAUDE.mode.md

## Project Overview

FastAPI hackathon template for rapid prototyping. The core framework lives in `app/hackplate/` and is not meant to be edited — user code lives in `app/` alongside it. The framework is configured through two mechanisms: `.env` (selects which plates to activate) and `pyproject.toml` `[tool.hackplate]` tables (fine-grained options like alembic toggle, custom user model).

## Setup

First-time setup from a fresh clone:

```bash
hackplate init        # installs uv, syncs deps, creates .env, prompts for plates, installs pre-commit
```

If `hackplate` is not yet available:

```bash
pip install uv && uv sync
hackplate init
```

Fill in any remaining values in `.env` (DB URLs, auth credentials) before running.

## Dev Commands

| Command | Description |
|---|---|
| `hackplate run` | Start uvicorn (`-m dev`\|`prod`, default `dev`; `dev` hot-reloads, `prod` runs `HACKPLATE_WORKERS` workers) |
| `hackplate run --docker-compose` | Start full stack via docker compose (`-m dev`\|`prod` selects the compose profile) |
| `hackplate init` | First-time repo setup (runs once) |
| `hackplate precommit` | Install and run pre-commit on all files |
| `hackplate clean` | Remove `.ruff_cache`, `.pytest_cache`, `__pycache__`, `*.egg-info` |
| `hackplate regenkey` | Regenerate `SECRET_KEY` in `.env` |
| `hackplate getplates` | Show active auth and db plates |
| `hackplate setplate auth <plate>` | Switch auth plate |
| `hackplate setplate db <plate>` | Switch db plate |
| `hackplate setmode safe\|fast` | Switch Claude Code operating mode |
| `hackplate getmode` | Show the current Claude Code operating mode |
| `hackplate startfeature <name>` | Scaffold a new feature directory under `app/` |
| `hackplate dropfeature <name>` | Remove a feature directory |
| `hackplate kcsync` | Sync Keycloak realm config to `settings.json` |
| `hackplate down` | Stop docker containers |
| `uv run alembic revision --autogenerate -m "<msg>"` | Generate a migration |
| `uv run alembic upgrade head` | Apply migrations |

Run `hackplate --help` for the full command reference.

## Architecture

```
app/
├── main.py              ← register routers here
├── lifespan.py          ← pre/post startup hooks (user-editable)
├── dependencies.py      ← get_db and get_current_user wrappers (user-editable)
│                          for WebSocket handlers use get_db_from_ws from hackplate/websocket.py
└── hackplate/           ← framework internals — do not modify unless necessary
    ├── cli.py
    ├── config.py        ← loads plates from .env, validates choices
    ├── hackplate_types.py ← Hackplate (FastAPI subclass), HackplateRequest, HackplateWebSocket
    ├── websocket.py     ← WSConnectionManager (broadcast to all clients), get_db_from_ws
    ├── lifespan.py      ← orchestrates startup/shutdown with plates
    ├── toml_settings.py ← reads [tool.hackplate] from pyproject.toml
    └── plates/
        ├── auth_plates/
        │   ├── local/   ← JWT-based, no external deps
        │   ├── auth0/   ← Auth0 OAuth
        │   └── keycloak/ ← Keycloak SSO (requires docker or external instance)
        └── db_plates/
            ├── sqlite/  ← aiosqlite, zero config
            ├── postgres/ ← asyncpg, includes Supabase variant
            └── mongo/   ← Beanie ODM
```

`app/main.py` is the entry point. Add routers inside `register_routes()`. Do not touch `configure()` or the `Hackplate(...)` constructor arguments — those wire up the framework.

## Plates

Active plates are set in `.env`:

```
HACKPLATE_AUTH=local   # local | auth0 | keycloak
HACKPLATE_DB=sqlite    # sqlite | postgres | supabase | mongo
```

Switch plates with `hackplate setplate auth <name>` or `hackplate setplate db <name>`.

**Auth plates** register login/logout/token routes automatically and provide a `get_current_user` dependency. Access it via `app/dependencies.py`.

**DB plates** provide a `get_db` session dependency. SQLite, Postgres, and Supabase use SQLModel/SQLAlchemy (sync schema); Mongo uses Beanie (document schema). Switching between SQL and Mongo requires changing the user model base class (see User Model below). `supabase` is the same as `postgres` but defaults `ssl_required=True` and appends `?ssl=require` to the connection URL.

## User Model

Defined in `pyproject.toml`:

```toml
[tool.hackplate]
auth_user_model = "app.hackplate.user.models.User"
```

- SQL plates: model must inherit from `AbstractUser` (SQLModel)
- Mongo plate: model must inherit from `AbstractUserDocument` (Beanie Document)

The default `User` model lives at `app/hackplate/user/models.py`. To customize, create your own model class in `app/`, update `auth_user_model` in `pyproject.toml`, and register it in `migrations/register_models.py`.

## Adding Features

```bash
hackplate startfeature <name>
```

Creates `app/<name>/` with `routes.py`, `schemas.py`, `crud.py`, `models.py`, `__init__.py` and registers the model in `migrations/register_models.py`.

Then in `app/main.py`:

```python
from app.<name>.routes import router as <name>_router

def register_routes(app: Hackplate) -> None:
    app.include_router(<name>_router, prefix="/<name>", tags=["<name>"])
```

## Migrations

Alembic is disabled by default (`[tool.hackplate.db] alembic = false`). When disabled, SQLModel creates tables on startup. Set `alembic = true` to manage schema with migrations instead.

Migrations only apply to SQL plates (SQLite, Postgres). For Mongo, Beanie manages schema automatically.

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

Models must be imported in `migrations/register_models.py` to appear in autogenerated migrations.

## Configuration Reference

Key `.env` variables by plate:

| Plate | Required vars |
|---|---|
| `postgres` | `POSTGRES_URL` or `POSTGRES_HOST/PORT/NAME/USERNAME/PASSWORD` |
| `supabase` | same as `postgres` — `POSTGRES_URL` or `POSTGRES_HOST/PORT/NAME/USERNAME/PASSWORD` (`POSTGRES_SSL_REQUIRED` defaults to `true`) |
| `mongo` | `MONGO_URL` or `MONGO_HOST/PORT/NAME/USERNAME/PASSWORD` |
| `auth0` | `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_AUDIENCE` |
| `keycloak` | `KEYCLOAK_HOST`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_ADMIN_USERNAME/PASSWORD` |
| `local` | `SECRET_KEY` (auto-set by `hackplate init` or `hackplate regenkey`) |

`HACKPLATE_WORKERS` sets the number of uvicorn worker processes used by `hackplate run -m prod` (default `4`). In `--docker-compose` mode it's read from the container's env (via `env_file: .env`) directly by the Dockerfile's `CMD`, which passes it to uvicorn as `--workers`.
