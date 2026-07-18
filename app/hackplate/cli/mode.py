import shutil
from pathlib import Path
from typing import Literal

import typer

from app.hackplate.cli.utils import ROOT_DIR

app = typer.Typer()


def write_mode_files(mode: Literal["safe", "fast"]) -> None:
    """Write modes/CLAUDE.mode.md and copy the matching settings.json into .claude/.
    Shared by `hackplate setmode` and `hackplate init` — .claude/ isn't guaranteed
    to exist on a fresh clone since only .claude/settings.json is gitignored, not
    the directory itself.
    """
    mode_path = Path(ROOT_DIR) / "modes" / "CLAUDE.mode.md"
    mode_path.write_text(f"@modes/CLAUDE.{mode}.md\n")

    claude_dir = Path(ROOT_DIR) / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy(
        Path(ROOT_DIR) / "modes" / f"settings.{mode}.json",
        claude_dir / "settings.json",
    )


@app.command()
def getmode():
    """Show the current Claude Code operating mode."""
    mode_path = Path(ROOT_DIR) / "modes" / "CLAUDE.mode.md"
    if not mode_path.exists():
        typer.echo("mode: (not set)")
        return
    content = mode_path.read_text().strip()
    mode = content.removeprefix("@modes/CLAUDE.").removesuffix(".md")
    typer.echo(f"mode: {mode}")


@app.command()
def setmode(mode: Literal["safe", "fast"]):
    """Switch the Claude Code operating mode. Writes to the gitignored modes/CLAUDE.mode.md."""
    write_mode_files(mode)
    typer.echo(
        f"Claude mode set to '{mode}'. Restart your session for {mode} mode to take effect"
    )
