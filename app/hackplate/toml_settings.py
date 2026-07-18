from typing import Any

from pydantic_settings import (
    BaseSettings,
    PyprojectTomlConfigSettingsSource,
    SettingsConfigDict,
)


class BaseTOMLSettings(BaseSettings):
    model_config = SettingsConfigDict(
        pyproject_toml_table_header=("tool", "hackplate"),
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        **kwargs: Any,
    ) -> tuple[PyprojectTomlConfigSettingsSource]:
        return (PyprojectTomlConfigSettingsSource(settings_cls),)


class ProjectDetails(BaseTOMLSettings):
    model_config = SettingsConfigDict(
        pyproject_toml_table_header=("project",),
        extra="ignore",
    )

    name: str = "fastapi-hackplate"
    version: str = "0.1.0"
    description: str = ""


class GeneralSettings(BaseTOMLSettings):
    auth_user_model: str = "app.hackplate.user.models.User"


class DatabaseSettings(BaseTOMLSettings):
    model_config = SettingsConfigDict(
        pyproject_toml_table_header=("tool", "hackplate", "db"),
        extra="ignore",
    )

    alembic: bool = False


class AuthSettings(BaseTOMLSettings):
    model_config = SettingsConfigDict(
        pyproject_toml_table_header=("tool", "hackplate", "auth"),
        extra="ignore",
    )


class BackendTOMLSettings:
    def __init__(self):
        self.project = GeneralSettings()
        self.db = DatabaseSettings()
        self.auth = AuthSettings()
