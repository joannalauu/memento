from pydantic_settings import BaseSettings, SettingsConfigDict


class Auth0Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AUTH0_",
        extra="ignore",
        env_ignore_empty=True,
    )

    domain: str
    client_id: str
    client_secret: str
    audience: str
    redirect_uri: str = "http://localhost:8000/docs"
    callback_url: str = "http://localhost:8000/auth/callback"
    m2m_client_id: str
    m2m_client_secret: str
    secure_cookies: bool = False
