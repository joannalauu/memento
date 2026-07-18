import tomllib
from pathlib import Path

with open(Path(__file__).parent.parent / "pyproject.toml", "rb") as f:
    config = tomllib.load(f)

user_model_path = (
    config.get("tool", {})
    .get("hackplate", {})
    .get("auth_user_model", "app.hackplate.user.models.User")
)

if user_model_path == "app.hackplate.user.models.User":
    from app.hackplate.user.models import User  # noqa: F401
