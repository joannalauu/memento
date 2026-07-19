"""Org invite email flow — the accept landing, best-effort send, and the
Auth0 return-to threading that lets an unauthenticated invitee log in and have
their invite accepted on the round trip.

These call the route handlers directly with stubbed CRUD / auth (the pattern in
test_github_webhook.py), so no Mongo or Auth0 is needed.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from starlette import status

from app.orgs import routes as orgs_routes

NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def _member(user_id, role="member"):
    return SimpleNamespace(userId=user_id, role=role, joinedAt=NOW)


def _org(members=None, name="Acme"):
    return SimpleNamespace(id=PydanticObjectId(), name=name, members=members or [])


def _invite(org, email="invitee@example.com", *, accepted=False, expired=False):
    return SimpleNamespace(
        orgId=org.id,
        email=email,
        token="tok_abc123",
        acceptedAt=NOW if accepted else None,
        expiresAt=(NOW - timedelta(days=1)) if expired else (NOW + timedelta(days=1)),
    )


def _request(user):
    """A fake Request whose auth plate returns `user`, or raises 401 when None."""

    async def get_current_user(_request):
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return user

    auth = SimpleNamespace(get_current_user=get_current_user)
    config = SimpleNamespace(auth=auth)
    app = SimpleNamespace(state=SimpleNamespace(config=config))
    return SimpleNamespace(app=app)


@pytest.fixture(autouse=True)
def freeze_now(monkeypatch):
    """Pin datetime.now() inside the routes module so expiry checks are stable."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr(orgs_routes, "datetime", _Frozen)


# ── landing endpoint ─────────────────────────────────────────────────────────


async def test_landing_invite_not_found(monkeypatch):
    async def _none(_token):
        return None

    monkeypatch.setattr(orgs_routes, "get_org_invite_by_token", _none)
    resp = await orgs_routes.accept_org_invite_landing("missing", _request(None))
    assert resp.status_code == 200
    assert b"Invite not found" in resp.body


async def test_landing_unauthenticated_redirects_to_login_with_return_to(monkeypatch):
    org = _org()
    invite = _invite(org)

    async def _get_invite(_token):
        return invite

    async def _get_org(_id):
        return org

    monkeypatch.setattr(orgs_routes, "get_org_invite_by_token", _get_invite)
    monkeypatch.setattr(orgs_routes, "get_org", _get_org)

    resp = await orgs_routes.accept_org_invite_landing(invite.token, _request(None))
    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == status.HTTP_303_SEE_OTHER
    location = resp.headers["location"]
    assert location.startswith("/auth/login?")
    # return_to points back to this same landing so accept completes post-login.
    assert "return_to=%2Forgs%2Finvites%2Ftok_abc123" in location


async def test_landing_authenticated_match_accepts_and_redirects(monkeypatch):
    org = _org()
    user = SimpleNamespace(id=PydanticObjectId(), email="Invitee@Example.com")
    invite = _invite(org, email="invitee@example.com")
    accepted = {}

    async def _get_invite(_token):
        return invite

    async def _get_org(_id):
        return org

    async def _accept(*, org, invite, user_id):
        accepted["user_id"] = user_id
        return org

    monkeypatch.setattr(orgs_routes, "get_org_invite_by_token", _get_invite)
    monkeypatch.setattr(orgs_routes, "get_org", _get_org)
    monkeypatch.setattr(orgs_routes, "accept_org_invite", _accept)

    resp = await orgs_routes.accept_org_invite_landing(invite.token, _request(user))
    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == status.HTTP_303_SEE_OTHER
    # Redirects into the SPA, and the accept ran with the logged-in user.
    assert resp.headers["location"] == orgs_routes.get_app_settings().frontend_base
    assert accepted["user_id"] == user.id


async def test_landing_wrong_account_shows_status_page(monkeypatch):
    org = _org()
    user = SimpleNamespace(id=PydanticObjectId(), email="someone.else@example.com")
    invite = _invite(org, email="invitee@example.com")

    async def _get_invite(_token):
        return invite

    async def _get_org(_id):
        return org

    async def _accept(**_kw):  # must not be called
        raise AssertionError("accept should not run for a mismatched account")

    monkeypatch.setattr(orgs_routes, "get_org_invite_by_token", _get_invite)
    monkeypatch.setattr(orgs_routes, "get_org", _get_org)
    monkeypatch.setattr(orgs_routes, "accept_org_invite", _accept)

    resp = await orgs_routes.accept_org_invite_landing(invite.token, _request(user))
    assert resp.status_code == 200
    assert b"Wrong account" in resp.body
    assert b"invitee@example.com" in resp.body


async def test_landing_already_member_redirects_to_app(monkeypatch):
    user = SimpleNamespace(id=PydanticObjectId(), email="invitee@example.com")
    org = _org(members=[_member(user.id, role="admin")])
    invite = _invite(org, email="invitee@example.com")

    async def _get_invite(_token):
        return invite

    async def _get_org(_id):
        return org

    async def _accept(**_kw):
        raise AssertionError("accept should not run when already a member")

    monkeypatch.setattr(orgs_routes, "get_org_invite_by_token", _get_invite)
    monkeypatch.setattr(orgs_routes, "get_org", _get_org)
    monkeypatch.setattr(orgs_routes, "accept_org_invite", _accept)

    resp = await orgs_routes.accept_org_invite_landing(invite.token, _request(user))
    assert isinstance(resp, RedirectResponse)
    assert resp.headers["location"] == orgs_routes.get_app_settings().frontend_base


async def test_landing_expired_shows_status_page(monkeypatch):
    org = _org()
    user = SimpleNamespace(id=PydanticObjectId(), email="invitee@example.com")
    invite = _invite(org, email="invitee@example.com", expired=True)

    async def _get_invite(_token):
        return invite

    async def _get_org(_id):
        return org

    monkeypatch.setattr(orgs_routes, "get_org_invite_by_token", _get_invite)
    monkeypatch.setattr(orgs_routes, "get_org", _get_org)

    resp = await orgs_routes.accept_org_invite_landing(invite.token, _request(user))
    assert resp.status_code == 200
    assert b"Invite expired" in resp.body


# ── create invite endpoint (best-effort email) ───────────────────────────────


async def test_create_invite_email_failure_is_best_effort(monkeypatch):
    admin = SimpleNamespace(id=PydanticObjectId(), email="admin@example.com")
    org = _org(members=[_member(admin.id, role="admin")])
    invite = _invite(org, email="invitee@example.com")
    sent = {}

    async def _get_org(_id):
        return org

    async def _create_invite(*, org_id, email):
        return invite

    async def _boom(*args, **kwargs):
        sent["called"] = True
        raise RuntimeError("Resend down")

    monkeypatch.setattr(orgs_routes, "get_org", _get_org)
    monkeypatch.setattr(orgs_routes, "create_org_invite", _create_invite)
    monkeypatch.setattr(orgs_routes, "send_email", _boom)

    payload = SimpleNamespace(email="invitee@example.com")
    # A Resend outage must not fail the request — the invite is returned anyway.
    result = await orgs_routes.create_org_invite_endpoint(org.id, payload, user=admin)
    assert result is invite
    assert sent["called"] is True


def test_invite_email_html_contains_accept_link():
    html = orgs_routes._invite_email_html(
        "Acme", "http://localhost:8000/orgs/invites/tok"
    )
    assert "http://localhost:8000/orgs/invites/tok" in html
    assert "Acme" in html


# ── token-only accept endpoint (SPA join-org page) ───────────────────────────


def _wire_token_accept(monkeypatch, *, invite, org):
    async def _by_token(_token):
        return invite

    async def _get_org(_id):
        return org

    monkeypatch.setattr(orgs_routes, "get_org_invite_by_token", _by_token)
    monkeypatch.setattr(orgs_routes, "get_org", _get_org)


async def test_accept_by_token_happy_path(monkeypatch):
    user = SimpleNamespace(id=PydanticObjectId(), email="invitee@example.com")
    org = _org()
    invite = _invite(org, email="invitee@example.com")
    accepted = {}

    async def _accept(*, org, invite, user_id):
        accepted["user_id"] = user_id
        return org

    _wire_token_accept(monkeypatch, invite=invite, org=org)
    monkeypatch.setattr(orgs_routes, "accept_org_invite", _accept)

    result = await orgs_routes.accept_org_invite_by_token_endpoint(
        invite.token, user=user
    )
    assert result is org
    assert accepted["user_id"] == user.id


async def test_accept_by_token_unknown_token_404(monkeypatch):
    async def _by_token(_token):
        return None

    monkeypatch.setattr(orgs_routes, "get_org_invite_by_token", _by_token)
    user = SimpleNamespace(id=PydanticObjectId(), email="invitee@example.com")

    with pytest.raises(HTTPException) as exc:
        await orgs_routes.accept_org_invite_by_token_endpoint("missing", user=user)
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND


async def test_accept_by_token_wrong_email_403(monkeypatch):
    user = SimpleNamespace(id=PydanticObjectId(), email="someone.else@example.com")
    org = _org()
    invite = _invite(org, email="invitee@example.com")
    _wire_token_accept(monkeypatch, invite=invite, org=org)

    with pytest.raises(HTTPException) as exc:
        await orgs_routes.accept_org_invite_by_token_endpoint(invite.token, user=user)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


async def test_accept_by_token_expired_410(monkeypatch):
    user = SimpleNamespace(id=PydanticObjectId(), email="invitee@example.com")
    org = _org()
    invite = _invite(org, email="invitee@example.com", expired=True)
    _wire_token_accept(monkeypatch, invite=invite, org=org)

    with pytest.raises(HTTPException) as exc:
        await orgs_routes.accept_org_invite_by_token_endpoint(invite.token, user=user)
    assert exc.value.status_code == status.HTTP_410_GONE


async def test_accept_by_token_already_accepted_409(monkeypatch):
    user = SimpleNamespace(id=PydanticObjectId(), email="invitee@example.com")
    org = _org()
    invite = _invite(org, email="invitee@example.com", accepted=True)
    _wire_token_accept(monkeypatch, invite=invite, org=org)

    with pytest.raises(HTTPException) as exc:
        await orgs_routes.accept_org_invite_by_token_endpoint(invite.token, user=user)
    assert exc.value.status_code == status.HTTP_409_CONFLICT


async def test_accept_by_token_already_member_409(monkeypatch):
    user = SimpleNamespace(id=PydanticObjectId(), email="invitee@example.com")
    org = _org(members=[_member(None)])
    org.members[0].userId = user.id
    invite = _invite(org, email="invitee@example.com")
    _wire_token_accept(monkeypatch, invite=invite, org=org)

    with pytest.raises(HTTPException) as exc:
        await orgs_routes.accept_org_invite_by_token_endpoint(invite.token, user=user)
    assert exc.value.status_code == status.HTTP_409_CONFLICT


async def test_join_login_bridge_redirects_to_spa():
    resp = await orgs_routes.join_org_login_bridge("tok_abc123")
    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == status.HTTP_303_SEE_OTHER
    location = resp.headers["location"]
    assert location.endswith("/join-org?token=tok_abc123")


# ── Auth0 return-to threading ─────────────────────────────────────────────────


def _auth0_router(monkeypatch):
    """Build the Auth0 router with the network clients stubbed out."""
    from app.hackplate.plates.auth_plates.auth0 import routes as a0

    monkeypatch.setattr(a0, "GetToken", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(a0, "Users", lambda *a, **k: SimpleNamespace())
    settings = SimpleNamespace(
        domain="tenant.us.auth0.com",
        client_id="cid",
        client_secret="secret",
        callback_url="http://localhost:8000/auth/callback",
        audience="https://api",
        redirect_uri="http://localhost:8000/docs",
        secure_cookies=False,
    )

    def _manager_dep():
        return None

    router = a0.auth0_router_factory(settings, _manager_dep)
    routes = {r.name: r.endpoint for r in router.routes}
    return routes, settings


async def test_login_threads_same_origin_return_to_into_state(monkeypatch):
    routes, _ = _auth0_router(monkeypatch)
    resp = await routes["login"](return_to="/orgs/invites/tok_abc123")
    location = resp.headers["location"]
    # Same-origin path is carried through Auth0's `state` echo.
    assert "state=%2Forgs%2Finvites%2Ftok_abc123" in location


async def test_login_rejects_off_origin_return_to(monkeypatch):
    routes, _ = _auth0_router(monkeypatch)
    for bad in ("https://evil.com", "//evil.com", None):
        resp = await routes["login"](return_to=bad)
        assert "state=" not in resp.headers["location"]


async def test_callback_redirects_to_same_origin_state(monkeypatch):
    routes, settings = _auth0_router(monkeypatch)

    # Stub the token exchange + userinfo so the callback needs no network.
    import app.hackplate.plates.auth_plates.auth0.routes as a0

    async def _to_thread(fn, *args):
        return fn(*args)

    monkeypatch.setattr(a0.asyncio, "to_thread", _to_thread)

    user_db = SimpleNamespace(
        get_by_sub=lambda sub: SimpleNamespace(id="u1"),
    )
    manager = SimpleNamespace(user_db=user_db)

    # Re-issue the router with token clients that return canned values.
    monkeypatch.setattr(
        a0,
        "GetToken",
        lambda *a, **k: SimpleNamespace(
            authorization_code=lambda code, cb: {
                "access_token": "at",
                "id_token": "it",
            }
        ),
    )
    monkeypatch.setattr(
        a0,
        "Users",
        lambda *a, **k: SimpleNamespace(
            userinfo=lambda at: {"email": "u@example.com", "sub": "sub-1"}
        ),
    )
    router = a0.auth0_router_factory(settings, lambda: manager)
    callback = {r.name: r.endpoint for r in router.routes}["callback"]

    # get_by_sub must be awaitable in the real manager; wrap it.
    async def _get_by_sub(sub):
        return SimpleNamespace(id="u1")

    manager.user_db.get_by_sub = _get_by_sub

    resp = await callback(
        code="x", state="/orgs/invites/tok_abc123", user_manager=manager
    )
    assert resp.headers["location"] == "/orgs/invites/tok_abc123"

    # Off-origin state falls back to the configured default redirect.
    resp2 = await callback(code="x", state="https://evil.com", user_manager=manager)
    assert resp2.headers["location"] == settings.redirect_uri
