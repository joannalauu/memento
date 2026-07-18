import shutil
from pathlib import Path

import typer

from app.hackplate.cli.utils import ROOT_DIR

app = typer.Typer()


@app.command()
def startfeature(feature_name: str):
    """Autogenerate feature files and directory."""
    feature_dir = Path(ROOT_DIR) / "app" / feature_name
    try:
        feature_dir.mkdir(exist_ok=False)
    except Exception:
        typer.BadParameter(f"feature directory /{feature_name} already exists.")
    for filename in ["routes.py", "schemas.py", "crud.py", "models.py", "__init__.py"]:
        (feature_dir / filename).touch()
    models_file = feature_dir / "models.py"
    models_file.write_text(
        "from app.hackplate.plates.db_plates.mongo.registry import register_document  # noqa: F401\n"
    )
    registry = Path(ROOT_DIR) / "migrations" / "register_models.py"
    current = registry.read_text()
    import_line = f"import app.{feature_name}.models  # noqa: F401\n"
    registry.write_text(current + import_line)
    typer.echo(f"Started feature '{feature_name}'.")


@app.command()
def dropfeature(feature_name: str):
    """Remove a feature directory and its registration from register_models.py."""
    feature_dir = Path(ROOT_DIR) / "app" / feature_name
    if not feature_dir.exists():
        typer.echo(f"Feature directory /app/{feature_name} does not exist.", err=True)
        raise typer.Exit(code=1)
    typer.confirm(
        f"Drop feature '{feature_name}'? This will delete /app/{feature_name} and its model import.",
        abort=True,
    )

    registry = Path(ROOT_DIR) / "migrations" / "register_models.py"
    import_line = f"import app.{feature_name}.models  # noqa: F401\n"
    registry.write_text(registry.read_text().replace(import_line, ""))
    shutil.rmtree(feature_dir)
    typer.echo(f"Dropped feature '{feature_name}'.")
