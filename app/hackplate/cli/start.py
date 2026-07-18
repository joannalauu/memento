import json
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import typer
from dotenv import get_key, load_dotenv, set_key

from app.hackplate.cli.utils import ROOT_DIR, check

SENSITIVE_KEYS = {"secret", "registrationAccessToken"}

KEYCLOAK_COMPOSE_FILE = (
    "app/hackplate/plates/auth_plates/keycloak/docker-compose.keycloak.yml"
)

app = typer.Typer()


def compose_files(use_keycloak: bool) -> list[str]:
    files = ["-f", "docker-compose.yml"]
    if use_keycloak:
        files += ["-f", KEYCLOAK_COMPOSE_FILE]
    return files


def allow_keycloak_http(host: str, username: str, password: str, service: str):
    kcadm = [
        "docker",
        "compose",
        *compose_files(use_keycloak=True),
        "exec",
        service,
        "/opt/keycloak/bin/kcadm.sh",
    ]
    subprocess.run(
        [
            *kcadm,
            "config",
            "credentials",
            "--server",
            host,
            "--realm",
            "master",
            "--user",
            username,
            "--password",
            password,
        ],
        check=True,
    )
    subprocess.run(
        [*kcadm, "update", "realms/master", "-s", "sslRequired=none"],
        check=True,
    )


def wait_for_keycloak(host: str | None = None, retries: int = 20, delay: float = 1.0):
    from app.hackplate.plates.auth_plates.keycloak.config import KeycloakSettings

    kc_host = host or KeycloakSettings().external_url
    typer.echo("Waiting for Keycloak to start up...")
    for _ in range(retries):
        try:
            httpx.get(f"{kc_host}/realms/master", timeout=2)
            return
        except Exception:
            time.sleep(delay)
    typer.echo("Keycloak did not become ready in time.", err=True)
    raise typer.Exit(code=1)


@app.command()
def run(
    mode: Literal["dev", "prod"] = typer.Option(
        "dev", "-m", "--mode", help="Run mode: dev (hot reload) or prod."
    ),
    docker: bool = typer.Option(False, "-dc", "--docker-compose"),
    args: list[str] = typer.Argument(default=None),
):
    """Start the uvicorn server, with the option to use docker. -m/--mode selects dev or prod (default: dev)."""
    check(error=True)

    extra = args or []

    if not docker:
        uvicorn_cmd = ["uv", "run", "uvicorn", "app.main:app"]
        if mode == "dev":
            uvicorn_cmd += ["--reload"]
        else:
            workers = get_key(Path(ROOT_DIR) / ".env", "HACKPLATE_WORKERS") or "4"
            uvicorn_cmd += ["--host", "0.0.0.0", "--port", "8000", "--workers", workers]
        subprocess.run([*uvicorn_cmd, *extra], check=True)
        return

    load_dotenv(verbose=True)
    auth_plate = get_key(Path(ROOT_DIR) / ".env", "HACKPLATE_AUTH")
    is_local = get_key(Path(ROOT_DIR) / ".env", "KEYCLOAK_USE_LOCAL")
    use_keycloak = bool(auth_plate == "keycloak" and is_local)

    command_prefix = [
        "docker",
        "compose",
        *compose_files(use_keycloak),
        "--profile",
        mode,
    ]

    subprocess.run([*command_prefix, "up", "-d", *extra], check=True)

    if use_keycloak:
        wait_for_keycloak()
        subprocess.run(["hackplate", "kcsync", "--mode", mode], check=True)
        api_service = "api" if mode == "dev" else "api-prod"
        subprocess.run([*command_prefix, "up", "-d", api_service], check=True)

    subprocess.run([*command_prefix, "logs", "-f"], check=True)


@app.command()
def down(args: list[str] = typer.Argument(default=None)):
    """Stop active docker containers."""
    extra = args or []
    subprocess.run(
        [
            "docker",
            "compose",
            *compose_files(use_keycloak=True),
            "--profile",
            "*",
            "down",
            *extra,
        ],
        check=True,
    )


@app.command()
def kcsync(
    mode: Literal["dev", "prod"] = typer.Option(
        "dev",
        "-m",
        "--mode",
        help="Which running mode's Keycloak container to sync from.",
    ),
    host: str | None = typer.Option(None, "-h", "--host"),
    realm: str | None = typer.Option(None, "-r", "--realm"),
    username: str | None = typer.Option(None, "-u", "--username"),
    password: str | None = typer.Option(None, "-p", "--password"),
):
    """Sync Keycloak realm config to app/hackplate/plates/auth_plates/keycloak/settings.json."""
    from keycloak import KeycloakAdmin
    from keycloak.exceptions import KeycloakError

    from app.hackplate.plates.auth_plates.keycloak.config import KeycloakSettings

    settings = KeycloakSettings()

    kc_host = host or settings.external_url
    kc_realm = realm or settings.realm
    kc_username = username or settings.admin_username
    kc_password = password or settings.admin_password
    kc_use_local = settings.use_local

    keycloak_service = "keycloak" if mode == "dev" else "keycloak-prod"

    keycloak_admin = KeycloakAdmin(
        server_url=kc_host,
        username=kc_username,
        password=kc_password,
        realm_name=kc_realm,
        user_realm_name="master",
    )

    if kc_use_local:
        allow_keycloak_http(kc_host, kc_username, kc_password, keycloak_service)

    try:
        exported: dict[str, Any] = keycloak_admin.export_realm(
            export_clients=True, export_groups_and_role=True
        )

        clients: list[dict[str, Any]] = exported.get("clients", [])
        hackplate_client = next(
            (c for c in clients if c["clientId"] == settings.client_id), None
        )
        if not hackplate_client:
            typer.echo(
                f"Could not find client '{settings.client_id}' in realm.", err=True
            )
            raise typer.Exit(code=1)

        client_secret = keycloak_admin.get_client_secrets(hackplate_client["id"]).get(
            "value"
        )
    except KeycloakError as e:
        typer.echo(f"Could not sync Keycloak at {kc_host}: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        if kc_use_local and mode == "dev":
            # Keep admin portal open during development. We assume that master realm will be protected by HTTPS when deployed to production.
            keycloak_admin.connection.realm_name = "master"
            keycloak_admin.update_realm("master", {"sslRequired": "EXTERNAL"})

    if client_secret:
        set_key(
            Path(ROOT_DIR) / ".env",
            "KEYCLOAK_CLIENT_SECRET",
            client_secret,
            quote_mode="never",
        )
        typer.echo("Client secret written to .env")

    exported["clients"] = [
        {k: v for k, v in c.items() if k not in SENSITIVE_KEYS} for c in clients
    ]

    merged = exported

    out_path = (
        Path(ROOT_DIR) / "app/hackplate/plates/auth_plates/keycloak/settings.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, indent=2) + "\n")

    typer.echo("Keycloak synced!")
