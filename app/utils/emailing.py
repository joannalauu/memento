from functools import lru_cache

import resend
from pydantic import EmailStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmailSettings(BaseSettings):
    """Loads Resend credentials and defaults from the environment / .env."""

    model_config = SettingsConfigDict(
        env_prefix="RESEND_",
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
    )

    api_key: str
    sender_email: EmailStr = "onboarding@resend.dev"


@lru_cache
def get_email_settings() -> EmailSettings:
    return EmailSettings()


async def send_email(
    to: str | list[str],
    subject: str,
    *,
    html: str | None = None,
    text: str | None = None,
    from_email: str | None = None,
) -> resend.Emails.SendResponse:
    """Send an email through Resend.

    Provide at least one of ``html`` or ``text`` for the body.
    """
    if not html and not text:
        raise ValueError("send_email requires either `html` or `text`.")

    settings = get_email_settings()
    resend.api_key = settings.api_key

    params: resend.Emails.SendParams = {
        "from": from_email or settings.sender_email,
        "to": to,
        "subject": subject,
    }
    if html:
        params["html"] = html
    if text:
        params["text"] = text

    return await resend.Emails.send_async(params)
