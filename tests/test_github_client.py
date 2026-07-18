"""GitHubApp auth chain + transport tests. All network mocked via
httpx.MockTransport; JWTs are minted/verified with a real throwaway RSA key."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.github.client import (
    GitHubApp,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubSettings,
    _CachedToken,
)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
TEST_PUBLIC_KEY = _KEY.public_key()

APP_ID = "12345"
INSTALLATION_ID = 42


def make_app(handler) -> GitHubApp:
    gh = GitHubApp(GitHubSettings(app_id=APP_ID, private_key=TEST_PEM))
    gh._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
        headers={"Accept": "application/vnd.github+json"},
    )
    return gh


def token_response(token: str = "ghs_tok", ttl_minutes: int = 60) -> httpx.Response:
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    return httpx.Response(201, json={"token": token, "expires_at": expires.isoformat()})


async def test_installation_token_minted_and_cached():
    mint_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/app/installations/{INSTALLATION_ID}/access_tokens"
        mint_calls.append(request.headers["Authorization"])
        return token_response()

    gh = make_app(handler)
    tok1 = await gh.installation_token(INSTALLATION_ID)
    tok2 = await gh.installation_token(INSTALLATION_ID)
    assert tok1 == tok2 == "ghs_tok"
    assert len(mint_calls) == 1

    # The mint request authenticated with a valid short-lived app JWT.
    bearer = mint_calls[0].removeprefix("Bearer ")
    claims = jwt.decode(bearer, TEST_PUBLIC_KEY, algorithms=["RS256"])
    assert claims["iss"] == APP_ID
    assert claims["exp"] - claims["iat"] <= 600


async def test_installation_token_refreshes_when_near_expiry():
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return token_response(token=f"ghs_tok_{count}")

    gh = make_app(handler)
    gh._tokens[INSTALLATION_ID] = _CachedToken(
        token="stale",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    tok = await gh.installation_token(INSTALLATION_ID)
    assert tok == "ghs_tok_1"
    assert count == 1


async def test_rest_401_refreshes_once_then_succeeds():
    api_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_attempts
        if "access_tokens" in request.url.path:
            return token_response()
        api_attempts += 1
        if api_attempts == 1:
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(200, json={"ok": True})

    gh = make_app(handler)
    resp = await gh.rest(
        "GET", "/repos/acme/api/contents/x", installation_id=INSTALLATION_ID
    )
    assert resp.json() == {"ok": True}
    assert api_attempts == 2


async def test_rest_persistent_401_raises_auth_error():
    api_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_attempts
        if "access_tokens" in request.url.path:
            return token_response()
        api_attempts += 1
        return httpx.Response(401, json={"message": "Bad credentials"})

    gh = make_app(handler)
    with pytest.raises(GitHubAuthError):
        await gh.rest(
            "GET", "/repos/acme/api/contents/x", installation_id=INSTALLATION_ID
        )
    assert api_attempts == 2


async def test_rest_404_raises_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        if "access_tokens" in request.url.path:
            return token_response()
        return httpx.Response(404, json={"message": "Not Found"})

    gh = make_app(handler)
    with pytest.raises(GitHubNotFoundError):
        await gh.rest(
            "GET", "/repos/acme/api/contents/nope", installation_id=INSTALLATION_ID
        )


async def test_rest_rate_limit_403():
    reset_epoch = int(datetime.now(timezone.utc).timestamp()) + 1800

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_tokens" in request.url.path:
            return token_response()
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_epoch),
            },
        )

    gh = make_app(handler)
    with pytest.raises(GitHubRateLimitError) as exc_info:
        await gh.rest("GET", "/search/code", installation_id=INSTALLATION_ID)
    assert exc_info.value.reset_at == datetime.fromtimestamp(
        reset_epoch, tz=timezone.utc
    )


async def test_graphql_surfaces_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        if "access_tokens" in request.url.path:
            return token_response()
        assert request.url.path == "/graphql"
        body = json.loads(request.content)
        assert "query" in body and "variables" in body
        return httpx.Response(200, json={"data": None, "errors": [{"message": "boom"}]})

    gh = make_app(handler)
    with pytest.raises(Exception, match="boom"):
        await gh.graphql("query { x }", {}, installation_id=INSTALLATION_ID)


def test_settings_inline_pem_beats_path(tmp_path):
    pem_file = tmp_path / "key.pem"
    pem_file.write_text("file-pem")
    settings = GitHubSettings(
        app_id=APP_ID,
        private_key="line1\\nline2",
        private_key_path=str(pem_file),
    )
    assert settings.resolve_private_key() == "line1\nline2"

    settings_path_only = GitHubSettings(app_id=APP_ID, private_key_path=str(pem_file))
    assert settings_path_only.resolve_private_key() == "file-pem"

    with pytest.raises(RuntimeError, match="private key not configured"):
        GitHubSettings(app_id=APP_ID).resolve_private_key()
