import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from dotenv import set_key
from pydantic import ValidationError
from pydantic_settings import BaseSettings

ROOT_DIR = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()

app = typer.Typer()


def _step(msg: str) -> None:
    typer.echo(f"\n→ {msg}")


def _ensure_uv() -> None:
    if not shutil.which("uv"):
        _step("Installing uv...")
        subprocess.run([sys.executable, "-m", "pip", "install", "uv"], check=True)
    _step("Running uv sync...")
    subprocess.run(["uv", "sync"], check=True, cwd=ROOT_DIR)


def _ensure_env_file() -> Path:
    env_path = Path(ROOT_DIR) / ".env"
    template_path = Path(ROOT_DIR) / ".env.example"

    if env_path.exists():
        return env_path

    if not template_path.exists():
        typer.echo(
            f"error: {template_path.name} not found — cannot create .env. "
            "Restore .env.example or create .env manually, then re-run `hackplate init`.",
            err=True,
        )
        raise typer.Exit(code=1)

    shutil.copy(template_path, env_path)
    _step("Created .env from .env.example")
    return env_path


def _prompt_plate(label: str, choices: list[str], default: str) -> str:
    typer.echo(f"\nAvailable {label} plates: {', '.join(choices)}")
    choice = typer.prompt(label.capitalize(), default=default)
    while choice not in choices:
        typer.echo(f"Invalid choice. Pick one of: {', '.join(choices)}")
        choice = typer.prompt(label.capitalize(), default=default)
    return choice


def _warn_if_docker_missing(auth_plate: str) -> None:
    if auth_plate == "keycloak" and not shutil.which("docker"):
        typer.echo(
            "\nwarning: the keycloak plate needs Docker for local dev "
            "(`hackplate run --docker-compose`), but `docker` isn't on PATH.",
            err=True,
        )


@app.command()
def init():
    """Initialize the repo for development. Prompts for plates and sets up .env. Runs once."""
    from app.hackplate.cli.mode import write_mode_files
    from app.hackplate.config import database_plate_list, auth_plate_list

    sentinel = Path(ROOT_DIR) / ".hackplate_init"
    if sentinel.exists():
        typer.echo("Already initialized. Delete .hackplate_init to re-run.", err=True)
        raise typer.Exit(code=1)

    _ensure_uv()
    env_path = _ensure_env_file()

    auth_plate = _prompt_plate("auth", list(auth_plate_list), "local")
    db_plate = _prompt_plate("database", list(database_plate_list), "sqlite")
    _warn_if_docker_missing(auth_plate)

    set_key(env_path, "HACKPLATE_AUTH", auth_plate, quote_mode="never")
    set_key(env_path, "HACKPLATE_DB", db_plate, quote_mode="never")

    key = secrets.token_urlsafe(32)[:32]
    set_key(env_path, "SECRET_KEY", key, quote_mode="never")

    _step("Installing pre-commit hooks...")
    subprocess.run(["uv", "run", "pre-commit", "install"], check=True, cwd=ROOT_DIR)

    _step("Setting Claude Code mode to 'safe'...")
    write_mode_files("safe")

    sentinel.touch()

    typer.echo(f"\nInitialized: auth={auth_plate}, db={db_plate}")

    _step("Checking .env completeness for the selected plates...")
    if run_checks():
        typer.echo("All required variables are set — run `hackplate run` when ready.")
    else:
        typer.echo(
            "\nFill in the missing values above in .env, then `hackplate check` "
            "to confirm before `hackplate run`."
        )


@app.command()
def regenkey(length: int = typer.Option(32, "-l", "--length", min=8)):
    """Set/regenerate the secret key used for the local authentication plate."""
    key = secrets.token_urlsafe(length)[:length]
    set_key(Path(ROOT_DIR) / ".env", "SECRET_KEY", key, quote_mode="never")
    typer.echo("A new key has been set on SECRET_KEY.")


@app.command()
def clean():
    """Remove cache/metadata directories (.ruff_cache, .pytest_cache, __pycache__, *.egg-info)."""
    root = Path(ROOT_DIR)
    for folder in [".ruff_cache", ".pytest_cache", *root.glob("*.egg-info")]:
        target: Path = root / folder
        if target.exists():
            subprocess.run(["rm", "-r", str(target)], check=True)
    for pycache in root.rglob("__pycache__"):
        if pycache.exists():
            subprocess.run(["rm", "-r", str(pycache)], check=True)


@app.command()
def precommit():
    """Install and run pre-commit hooks on all files."""
    subprocess.run(["pre-commit", "install"], check=True)
    result = subprocess.run(["pre-commit", "run", "--all-files"])
    if result.returncode != 0:
        subprocess.run(["pre-commit", "run", "--all-files"])


def assert_settings(Settings: type[BaseSettings]) -> bool:
    try:
        Settings()
        return True
    except ValidationError as e:
        for err in e.errors():
            field = err["loc"][0]
            typer.echo(f"{field} is missing/empty")
        return False


def run_checks() -> bool:
    """Validate .env against the currently selected plates. Returns True if everything
    required is set. Shared by `hackplate check` and `hackplate init`.
    """
    from app.hackplate.config import BackendEnvSettings
    from app.hackplate.cors import CORSSettings
    from app.hackplate.plates.db_plates.sqlite.config import SQLiteSettings
    from app.hackplate.plates.db_plates.postgres.config import PostgresSettings
    from app.hackplate.plates.db_plates.postgres.supabase_config import SupabaseSettings
    from app.hackplate.plates.db_plates.mongo.config import MongoSettings
    from app.hackplate.plates.auth_plates.local.env_settings import LocalAuthSettings
    from app.hackplate.plates.auth_plates.keycloak.env_settings import KeycloakSettings
    from app.hackplate.plates.auth_plates.auth0.env_settings import Auth0Settings

    settings_map = {
        "sqlite": SQLiteSettings,
        "postgres": PostgresSettings,
        "supabase": SupabaseSettings,
        "mongo": MongoSettings,
        "local": LocalAuthSettings,
        "keycloak": KeycloakSettings,
        "auth0": Auth0Settings,
    }

    if not assert_settings(BackendEnvSettings):
        return False

    backend_settings = BackendEnvSettings()

    all_valid = assert_settings(CORSSettings)
    all_valid &= assert_settings(settings_map[backend_settings.db])
    all_valid &= assert_settings(settings_map[backend_settings.auth])
    return all_valid


@app.command()
def check(
    error: bool = typer.Option(
        False,
        "-e",
        "--error",
        help="Exit with code 1 if any .env variables are missing",
    ),
):
    """Validate that .env variables are set properly"""
    all_valid = run_checks()
    if not all_valid and error:
        raise typer.Exit(code=1)
