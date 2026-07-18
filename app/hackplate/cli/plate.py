from pathlib import Path
from typing import Literal

import typer
from dotenv import get_key, load_dotenv, set_key

from app.hackplate.cli.utils import ROOT_DIR

app = typer.Typer()


@app.command()
def getplates():
    """Show the current auth and database plates in use."""
    load_dotenv(Path(ROOT_DIR) / ".env")
    auth = get_key(Path(ROOT_DIR) / ".env", "HACKPLATE_AUTH") or "(not set)"
    db = get_key(Path(ROOT_DIR) / ".env", "HACKPLATE_DB") or "(not set)"
    typer.echo(f"auth: {auth}")
    typer.echo(f"db:   {db}")


@app.command()
def setplate(plate_type: Literal["auth", "db"], plate_name: str):
    """Set/update the authentication and database plates being used by Hackplate."""
    from app.hackplate.config import database_plate_list, auth_plate_list

    plates = {"auth": auth_plate_list, "db": database_plate_list}
    if plate_name not in plates[plate_type]:
        raise typer.BadParameter(
            f"{plate_name} is not a valid plate. {list(plates[plate_type])}"
        )
    set_key(
        Path(ROOT_DIR) / ".env",
        {"db": "HACKPLATE_DB", "auth": "HACKPLATE_AUTH"}[plate_type],
        plate_name,
        quote_mode="never",
    )
