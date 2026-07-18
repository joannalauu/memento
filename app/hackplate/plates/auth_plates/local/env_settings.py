from pydantic_settings import BaseSettings, SettingsConfigDict


class LocalAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True
    )

    secret_key: str
