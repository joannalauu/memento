import typer

from app.hackplate.cli import feature, mode, plate, start, utils

app = typer.Typer(help="Hackplate dev CLI")

app.add_typer(utils.app)
app.add_typer(feature.app)
app.add_typer(plate.app)
app.add_typer(mode.app)
app.add_typer(start.app)


if __name__ == "__main__":
    app()
