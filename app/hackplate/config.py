from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from typing import Self

from app.hackplate.plates.db_plates.sqlite.config import SQLitePlate
from app.hackplate.plates.db_plates.postgres.config import PostgresPlate
from app.hackplate.plates.db_plates.postgres.supabase_config import SupabasePlate
from app.hackplate.plates.db_plates.mongo.config import MongoPlate
from app.hackplate.plates.abstract_plates import DatabasePlate, AuthPlate
from app.hackplate.plates.auth_plates.local.config import LocalPlate
from app.hackplate.plates.auth_plates.keycloak.config import KeycloakPlate
from app.hackplate.plates.auth_plates.auth0.config import Auth0Plate
from app.hackplate.toml_settings import BackendTOMLSettings
from app.hackplate.user.models import AbstractUser, AbstractUserDocument
from app.hackplate.user.utils import get_user_model

database_plates = {
    "sqlite": SQLitePlate,
    "postgres": PostgresPlate,
    "supabase": SupabasePlate,
    "mongo": MongoPlate,
}

database_plate_list = list(database_plates.keys())

auth_plates = {"local": LocalPlate, "auth0": Auth0Plate, "keycloak": KeycloakPlate}

auth_plate_list = list(auth_plates.keys())


class BackendEnvSettings(BaseSettings):
    """
    Pulls hackplate's configured authentication and database plates from .env
    """

    model_config = SettingsConfigDict(
        env_prefix="HACKPLATE_",
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
    )

    db: str = "sqlite"
    auth: str = "local"

    @model_validator(mode="after")
    def validate_plates(self) -> Self:
        """
        Validates .env variables to ensure that they align with usable plates
        """
        if self.db not in database_plates:
            raise ValueError(
                f"Database plate {self.db} defined in .env is not a valid plate."
            )
        if self.auth not in auth_plates:
            raise ValueError(
                f"Auth plate {self.auth} defined in .env is not a valid plate."
            )
        if not database_plates[self.db]:
            raise NotImplementedError(
                f"Database plate {self.db} defined in .env is not implemented yet."
            )
        if not auth_plates[self.auth]:
            raise NotImplementedError(
                f"Auth plate {self.auth} defined in .env is not implemented yet."
            )
        return self


class BackendConfig:
    """
    Centralizes hackplate's configured authentication and database plates
    """

    def __init__(self, settings: BackendTOMLSettings):
        config = BackendEnvSettings()
        self.auth_user_model = get_user_model()

        if config.db == "mongo" and not issubclass(
            self.auth_user_model, AbstractUserDocument
        ):
            raise ValueError(
                f"{self.auth_user_model.__name__} must inherit from AbstractUserDocument when using the mongo plate"
            )

        if config.db != "mongo" and not issubclass(self.auth_user_model, AbstractUser):
            raise ValueError(
                f"{self.auth_user_model.__name__} must inherit from AbstractUser when using a SQL plate"
            )

        self.db_name = config.db
        self.db: DatabasePlate = database_plates[config.db](settings.db)

        self.auth_name = config.auth
        self.auth: AuthPlate = auth_plates[config.auth](settings.auth, self.db_name)
