from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """App-wide URLs used to build browser-facing links.

    These back flows where the browser navigates directly to the API (no bearer
    token) and then needs to be sent on to the SPA — currently the org-invite
    email: the button points at ``api_base_url`` and, once accepted, the invitee
    is redirected to ``frontend_url``.
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
    )

    # Public base URL of this API — where browser-navigable links (the invite
    # accept landing) point.
    api_base_url: str = "http://localhost:8000"
    # Base URL of the SPA — where the invite flow sends the browser once the
    # invite has been accepted.
    frontend_url: str = "http://localhost:5173"

    @property
    def api_base(self) -> str:
        """API base URL without a trailing slash."""
        return self.api_base_url.rstrip("/")

    @property
    def frontend_base(self) -> str:
        """SPA base URL without a trailing slash."""
        return self.frontend_url.rstrip("/")


@lru_cache
def get_app_settings() -> AppSettings:
    return AppSettings()
