"""
GitHub App client: auth chain + transport for the tool suite (see tools.py) and
the org installation flow (see routes.py / crud.py).

Auth chain: app JWT (RS256, short-lived) → POST /app/installations/{id}/access_tokens
→ installation token (~1h), cached in-memory per installation id. Each org stores
its own `githubInstallationId` (app/orgs/models.py), so all calls are org-scoped.
"""

import asyncio
import hashlib
import hmac
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import jwt
from fastapi import Request
from pydantic_settings import BaseSettings, SettingsConfigDict

GITHUB_API_VERSION = "2022-11-28"
# Refresh a cached installation token when it has less than this long to live.
TOKEN_REFRESH_MARGIN = timedelta(seconds=120)


class GitHubSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="GITHUB_",
        extra="ignore",
        env_ignore_empty=True,
    )

    app_id: str
    private_key: str | None = None
    private_key_path: str | None = None
    api_base_url: str = "https://api.github.com"
    timeout: int = 30

    # ─── Installation flow (routes.py / crud.py) ──────────────────────────────
    # Optional so the app boots for tool-suite-only use; required to drive the
    # org install/webhook flow.
    app_slug: str | None = None
    webhook_secret: str | None = None
    # Where to send the browser after a successful install. When unset the setup
    # callback returns JSON instead of redirecting.
    post_install_redirect_url: str | None = None

    def resolve_private_key(self) -> str:
        """Inline PEM wins over the file path. Resolved lazily (first JWT mint)
        so the app can boot without GitHub credentials configured."""
        if self.private_key:
            # dotenv single-line PEMs use literal \n escapes.
            return self.private_key.replace("\\n", "\n")
        if self.private_key_path:
            return Path(self.private_key_path).read_text()
        raise RuntimeError(
            "GitHub App private key not configured: "
            "set GITHUB_PRIVATE_KEY or GITHUB_PRIVATE_KEY_PATH"
        )


class GitHubError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubNotFoundError(GitHubError):
    pass


class GitHubAuthError(GitHubError):
    pass


class GitHubRateLimitError(GitHubError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        reset_at: datetime | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.reset_at = reset_at


@dataclass
class _CachedToken:
    token: str
    expires_at: datetime


class GitHubApp:
    """Long-lived GitHub App transport: shared connection pool plus an
    installation-token cache. Use as an app-wide singleton (see app/lifespan.py)
    or as an async context manager in scripts."""

    def __init__(self, settings: GitHubSettings | None = None) -> None:
        # app_id has no default because it's required — but BaseSettings
        # fills it from the environment/.env at runtime, which the type
        # checker can't see from the zero-arg constructor call.
        self.settings = settings or GitHubSettings()  # pyright: ignore[reportCallIssue]
        self._http = httpx.AsyncClient(
            base_url=self.settings.api_base_url,
            timeout=self.settings.timeout,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
        )
        self._tokens: dict[int, _CachedToken] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "GitHubApp":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ─── URLs ─────────────────────────────────────────────────────────────────

    def install_url(self, state: str) -> str:
        """The GitHub-hosted installation page, carrying our attribution state."""
        if not self.settings.app_slug:
            raise RuntimeError(
                "GitHub App slug not configured: set GITHUB_APP_SLUG to drive "
                "the installation flow"
            )
        return (
            f"https://github.com/apps/{self.settings.app_slug}"
            f"/installations/new?state={state}"
        )

    # ─── Auth ─────────────────────────────────────────────────────────────────

    def _app_jwt(self) -> str:
        # iat backdated 60s for clock skew; exp stays inside GitHub's 10-min cap.
        now = datetime.now(timezone.utc)
        payload = {
            "iat": int((now - timedelta(seconds=60)).timestamp()),
            "exp": int((now + timedelta(seconds=540)).timestamp()),
            "iss": self.settings.app_id,
        }
        return jwt.encode(
            payload, self.settings.resolve_private_key(), algorithm="RS256"
        )

    async def installation_token(
        self, installation_id: int, *, force_refresh: bool = False
    ) -> str:
        async with self._locks[installation_id]:
            cached = self._tokens.get(installation_id)
            now = datetime.now(timezone.utc)
            if (
                not force_refresh
                and cached is not None
                and cached.expires_at - now > TOKEN_REFRESH_MARGIN
            ):
                return cached.token

            resp = await self._http.post(
                f"/app/installations/{installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {self._app_jwt()}"},
            )
            if resp.status_code == 404:
                raise GitHubNotFoundError(
                    f"GitHub App installation {installation_id} not found "
                    "— app uninstalled from the org?",
                    status_code=404,
                )
            if resp.status_code >= 400:
                raise GitHubAuthError(
                    f"Failed to mint installation token ({resp.status_code}): "
                    f"{resp.text[:200]}",
                    status_code=resp.status_code,
                )
            data = resp.json()
            expires_at = datetime.fromisoformat(
                data["expires_at"].replace("Z", "+00:00")
            )
            self._tokens[installation_id] = _CachedToken(
                token=data["token"], expires_at=expires_at
            )
            return data["token"]

    # ─── Transport ────────────────────────────────────────────────────────────

    async def rest(
        self,
        method: str,
        path: str,
        *,
        installation_id: int,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        token = await self.installation_token(installation_id)
        resp = await self._http.request(
            method,
            path,
            params=params,
            json=json,
            headers={"Authorization": f"Bearer {token}", **(headers or {})},
        )
        if resp.status_code == 401:
            # Token may have been revoked/expired server-side: re-mint once.
            token = await self.installation_token(installation_id, force_refresh=True)
            resp = await self._http.request(
                method,
                path,
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {token}", **(headers or {})},
            )
            if resp.status_code == 401:
                raise GitHubAuthError(
                    f"GitHub rejected the installation token for {path}",
                    status_code=401,
                )
        if resp.status_code == 404:
            raise GitHubNotFoundError(f"Not found: {path}", status_code=404)
        if (
            resp.status_code in (403, 429)
            and resp.headers.get("X-RateLimit-Remaining") == "0"
        ):
            reset_at: datetime | None = None
            reset_header = resp.headers.get("X-RateLimit-Reset")
            if reset_header and reset_header.isdigit():
                reset_at = datetime.fromtimestamp(int(reset_header), tz=timezone.utc)
            raise GitHubRateLimitError(
                "GitHub rate limit exceeded"
                + (f", resets at {reset_at.isoformat()}" if reset_at else ""),
                status_code=resp.status_code,
                reset_at=reset_at,
            )
        if resp.status_code >= 400:
            raise GitHubError(
                f"GitHub API error {resp.status_code} on {path}: {resp.text[:300]}",
                status_code=resp.status_code,
            )
        return resp

    async def graphql(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        installation_id: int,
    ) -> dict[str, Any]:
        """POST /graphql (installation tokens are valid for GraphQL too)."""
        resp = await self.rest(
            "POST",
            "/graphql",
            installation_id=installation_id,
            json={"query": query, "variables": variables},
        )
        body = resp.json()
        if body.get("errors"):
            messages = "; ".join(e.get("message", str(e)) for e in body["errors"])
            raise GitHubError(f"GitHub GraphQL error: {messages}")
        return body["data"]

    # ─── Repos ────────────────────────────────────────────────────────────────

    async def list_installation_repos(self, installation_id: int) -> list[dict]:
        """List every repo the installation can access, following pagination.
        Routes through ``rest`` so it shares token caching and error handling."""
        repos: list[dict] = []
        page = 1
        while True:
            resp = await self.rest(
                "GET",
                "/installation/repositories",
                installation_id=installation_id,
                params={"per_page": 100, "page": page},
            )
            batch = resp.json().get("repositories", [])
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return repos

    # ─── Webhooks ─────────────────────────────────────────────────────────────

    def verify_webhook(self, payload_body: bytes, signature_header: str | None) -> bool:
        """Constant-time check of the ``X-Hub-Signature-256`` header against the
        raw request body using the configured webhook secret. Fails closed when
        the signature is missing or no secret is configured."""
        if not signature_header or not self.settings.webhook_secret:
            return False
        secret = self.settings.webhook_secret.encode()
        expected = (
            "sha256=" + hmac.new(secret, payload_body, hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(expected, signature_header)


def get_github(request: Request) -> GitHubApp:
    """FastAPI dependency returning the app-wide GitHubApp client
    (initialized in app/lifespan.py)."""
    return request.app.state.github
