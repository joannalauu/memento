import logging
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlencode

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.backboard.client import Backboard, get_backboard
from app.config import get_app_settings
from app.dependencies import get_current_user
from app.github.client import GitHubApp, get_github
from app.github.crud import create_install_state
from app.orgs.crud import (
    accept_org_invite,
    create_org,
    create_org_invite,
    delete_org,
    get_org,
    get_org_invite,
    get_org_invite_by_token,
    list_org_members,
    list_orgs_for_user,
    list_repos_for_org,
    update_org,
)
from app.orgs.models import Org, User
from app.orgs.schemas import (
    OrgCreate,
    OrgInviteCreate,
    OrgInviteRead,
    OrgMemberRead,
    OrgRead,
    OrgUpdate,
    RepoRead,
)
from app.utils.emailing import send_email

logger = logging.getLogger(__name__)

router = APIRouter()


def _invite_email_html(org_name: str, accept_url: str) -> str:
    """Render the org-invite email body: a short line plus an accept button."""
    safe_org = escape(org_name)
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
            max-width:480px;margin:0 auto;padding:24px;color:#0f172a;">
  <h2 style="margin:0 0 12px;font-size:20px;">You've been invited to {safe_org}</h2>
  <p style="margin:0 0 24px;font-size:15px;line-height:1.5;color:#334155;">
    Click the button below to join <strong>{safe_org}</strong>. You'll be asked to
    sign in or create an account first, then your invite is accepted automatically.
  </p>
  <a href="{escape(accept_url)}"
     style="display:inline-block;background:#0f172a;color:#ffffff;text-decoration:none;
            font-size:15px;font-weight:600;padding:12px 24px;border-radius:8px;">
    Join {safe_org}
  </a>
  <p style="margin:24px 0 0;font-size:12px;line-height:1.5;color:#94a3b8;">
    Or paste this link into your browser:<br />
    <a href="{escape(accept_url)}" style="color:#64748b;">{escape(accept_url)}</a><br />
    This invite expires in 3 days. If you weren't expecting it, you can ignore this email.
  </p>
</div>"""


def _invite_status_page(title: str, message: str, *, app_url: str) -> HTMLResponse:
    """A minimal standalone page for terminal invite states (expired, wrong
    account, already used) — the browser lands here directly, so it can't lean on
    the SPA to render feedback."""
    return HTMLResponse(
        f"""\
<!doctype html>
<html lang="en">
<head><meta charset="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{escape(title)}</title></head>
<body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
             background:#f8fafc;color:#0f172a;display:grid;place-items:center;
             min-height:100vh;margin:0;">
  <main style="max-width:420px;text-align:center;padding:32px;">
    <h1 style="font-size:20px;margin:0 0 12px;">{escape(title)}</h1>
    <p style="font-size:15px;line-height:1.5;color:#334155;margin:0 0 24px;">{escape(message)}</p>
    <a href="{escape(app_url)}"
       style="display:inline-block;background:#0f172a;color:#fff;text-decoration:none;
              font-size:15px;font-weight:600;padding:10px 20px;border-radius:8px;">
      Go to app
    </a>
  </main>
</body>
</html>"""
    )


@router.post("", response_model=OrgRead, status_code=status.HTTP_201_CREATED)
async def create_org_endpoint(
    payload: OrgCreate,
    user: User = Depends(get_current_user),
    backboard: Backboard = Depends(get_backboard),
) -> OrgRead:
    """Create an org for the authenticated user, provisioning a dedicated
    Backboard assistant and seeding the creator as admin."""
    assistant = await backboard.create_assistant(name=payload.name)
    return await create_org(
        name=payload.name,
        bb_assistant_id=str(assistant.assistant_id),
        creator_id=user.id,
    )


@router.get("/me", response_model=list[OrgRead])
async def list_my_orgs_endpoint(
    user: User = Depends(get_current_user),
) -> list[OrgRead]:
    """List every org the authenticated user is a member of, newest first."""
    return await list_orgs_for_user(user.id)


@router.get("/{org_id}", response_model=OrgRead)
async def get_org_endpoint(
    org_id: PydanticObjectId,
    user: User = Depends(get_current_user),
) -> OrgRead:
    """Retrieve a single org by id. Only a member of the org may view it."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )
    return org


@router.get("/{org_id}/members", response_model=list[OrgMemberRead])
async def list_org_members_endpoint(
    org_id: PydanticObjectId,
    user: User = Depends(get_current_user),
) -> list[OrgMemberRead]:
    """List an org's members with each userId reference resolved to the full
    user object. Only a member of the org may view them."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )
    return await list_org_members(org)


@router.get("/{org_id}/github/connect")
async def connect_github_endpoint(
    org_id: PydanticObjectId,
    user: User = Depends(get_current_user),
    github: GitHubApp = Depends(get_github),
) -> dict[str, str]:
    """Begin GitHub App installation for an org. Mints a single-use attribution
    state and returns the GitHub install URL to redirect the browser to; the
    install is bound to this org when GitHub calls back to /github/setup. Only
    an admin member may connect GitHub."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_admin = any(m.userId == user.id and m.role == "admin" for m in org.members)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an org admin may connect GitHub",
        )
    state = await create_install_state(org_id=org_id, user_id=user.id)
    return {"installUrl": github.install_url(state.token)}


@router.get("/{org_id}/repos", response_model=list[RepoRead])
async def list_org_repos_endpoint(
    org_id: PydanticObjectId,
    user: User = Depends(get_current_user),
) -> list[RepoRead]:
    """List an org's repos. Only a member of the org may view them."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )
    return await list_repos_for_org(org_id)


@router.post(
    "/{org_id}/invites",
    response_model=OrgInviteRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_org_invite_endpoint(
    org_id: PydanticObjectId,
    payload: OrgInviteCreate,
    user: User = Depends(get_current_user),
) -> OrgInviteRead:
    """Invite someone to an org by email. Only an admin member may invite."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_admin = any(m.userId == user.id and m.role == "admin" for m in org.members)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an org admin may invite members",
        )
    invite = await create_org_invite(org_id=org_id, email=payload.email)
    # The token is globally unique, so the accept-link needs only the token. The
    # button points at the SPA join-org page, which checks the session, drives
    # login-then-return if needed, and calls the token-only accept endpoint.
    accept_url = f"{get_app_settings().frontend_base}/join-org?token={invite.token}"
    # Best-effort: the invite is already persisted and independently redeemable
    # via the link, so a Resend outage must not fail the request. Log and return
    # the invite — the caller can surface/copy the link and retry the send.
    try:
        await send_email(
            payload.email,
            f"You've been invited to {org.name}",
            html=_invite_email_html(org.name, accept_url),
            text=(
                f"You've been invited to join {org.name}. "
                f"Accept your invite: {accept_url}"
            ),
        )
    except Exception:  # noqa: BLE001 - deliberately swallowed, logged below
        logger.exception(
            "invite email send failed org=%s email=%s", org_id, payload.email
        )
    return invite


@router.post("/{org_id}/invites/{token}/accept", response_model=OrgRead)
async def accept_org_invite_endpoint(
    org_id: PydanticObjectId,
    token: str,
    user: User = Depends(get_current_user),
) -> OrgRead:
    """Accept an org invite, adding the authenticated user to the org as a
    member. Future flows for unauthenticated / account-less invitees will
    layer on top of this."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    invite = await get_org_invite(org_id, token)
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found"
        )
    if invite.acceptedAt is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Invite already accepted"
        )
    # TTL reaps expired invites eventually, but the sweep lags — reject
    # explicitly so a not-yet-swept expired invite can't be accepted.
    if invite.expiresAt < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Invite has expired"
        )
    # The invite is addressed to a specific email; require the logged-in user
    # to match it so a forwarded token can't be redeemed by someone else.
    if invite.email.lower() != user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite was issued to a different email",
        )
    if any(m.userId == user.id for m in org.members):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Already a member of this org",
        )
    return await accept_org_invite(org=org, invite=invite, user_id=user.id)


@router.post("/invites/{token}/accept", response_model=OrgRead)
async def accept_org_invite_by_token_endpoint(
    token: str,
    user: User = Depends(get_current_user),
) -> OrgRead:
    """Accept an org invite by its token alone.

    The SPA join-org page has only the token from the invite link (not the org
    id), so this resolves the org from the token and applies the same guards as
    the org-scoped accept endpoint above."""
    invite = await get_org_invite_by_token(token)
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found"
        )
    org: Org = await get_org(invite.orgId)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    if invite.acceptedAt is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Invite already accepted"
        )
    # TTL reaps expired invites eventually, but the sweep lags — reject
    # explicitly so a not-yet-swept expired invite can't be accepted.
    if invite.expiresAt < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Invite has expired"
        )
    # The invite is addressed to a specific email; require the logged-in user
    # to match it so a forwarded token can't be redeemed by someone else.
    if invite.email.lower() != user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite was issued to a different email",
        )
    if any(m.userId == user.id for m in org.members):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Already a member of this org",
        )
    return await accept_org_invite(org=org, invite=invite, user_id=user.id)


@router.get("/invites/{token}/continue", include_in_schema=False)
async def join_org_login_bridge(token: str):
    """Same-origin login-return bridge for the SPA join-org flow.

    The Auth0 plate only honors a same-origin (relative) ``return_to``, which
    resolves to *this* API origin after the callback — it can't land directly on
    the cross-origin SPA. The join-org page therefore points ``return_to`` here;
    once authenticated the browser reaches this route and is bounced to the SPA
    join page (now carrying session cookies), which calls the accept endpoint."""
    app_url = get_app_settings().frontend_base
    return RedirectResponse(
        url=f"{app_url}/join-org?token={token}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/invites/{token}", include_in_schema=False)
async def accept_org_invite_landing(token: str, request: Request):
    """Browser-facing invite landing — the target of the email's accept button.

    Unlike the JSON accept endpoint, the browser arrives here with no bearer
    token, so this endpoint *optionally* authenticates: an unauthenticated
    invitee is sent through the login flow with a ``return_to`` back here, so
    after signing in (or registering) they land here again — now authenticated —
    and the invite is accepted and they're bounced into the SPA. Terminal states
    (expired, already used, wrong account) render a standalone status page since
    there's no SPA route to show them."""
    app_settings = get_app_settings()
    app_url = app_settings.frontend_base

    invite = await get_org_invite_by_token(token)
    if invite is None:
        return _invite_status_page(
            "Invite not found",
            "This invite link is invalid or has already been used.",
            app_url=app_url,
        )
    org = await get_org(invite.orgId)
    if org is None:
        return _invite_status_page(
            "Organization unavailable",
            "The organization for this invite no longer exists.",
            app_url=app_url,
        )

    # Optionally authenticate: fall through to login if there's no valid session.
    try:
        user: User = await request.app.state.config.auth.get_current_user(request)
    except HTTPException:
        user = None

    if user is None:
        # Bounce through login, asking to be returned to this same landing so the
        # accept completes on the round trip. Same-origin path only (the auth
        # plate rejects anything else) to keep this from being an open redirect.
        login_url = f"/auth/login?{urlencode({'return_to': f'/orgs/invites/{token}'})}"
        return RedirectResponse(url=login_url, status_code=status.HTTP_303_SEE_OTHER)

    # Already in the org (or they already accepted this invite themselves) — just
    # send them into the app rather than erroring.
    if any(m.userId == user.id for m in org.members):
        return RedirectResponse(url=app_url, status_code=status.HTTP_303_SEE_OTHER)
    if invite.acceptedAt is not None:
        return _invite_status_page(
            "Invite already used",
            f"This invite to {org.name} has already been accepted.",
            app_url=app_url,
        )
    # TTL reaps expired invites eventually, but the sweep lags — reject a
    # not-yet-swept expired invite explicitly.
    if invite.expiresAt < datetime.now(timezone.utc):
        return _invite_status_page(
            "Invite expired",
            f"This invite to {org.name} has expired. Ask an admin to send a new one.",
            app_url=app_url,
        )
    # The invite is addressed to a specific email; a forwarded link can't be
    # redeemed by a different account.
    if invite.email.lower() != user.email.lower():
        return _invite_status_page(
            "Wrong account",
            (
                f"This invite was sent to {invite.email}, but you're signed in as "
                f"{user.email}. Sign out, sign back in as {invite.email}, and open "
                "the invite link again."
            ),
            app_url=app_url,
        )

    await accept_org_invite(org=org, invite=invite, user_id=user.id)
    return RedirectResponse(url=app_url, status_code=status.HTTP_303_SEE_OTHER)


@router.patch("/{org_id}", response_model=OrgRead)
async def update_org_endpoint(
    org_id: PydanticObjectId,
    payload: OrgUpdate,
    user: User = Depends(get_current_user),
) -> OrgRead:
    """Partially update an org. Only an admin member of the org may update it."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_admin = any(m.userId == user.id and m.role == "admin" for m in org.members)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an org admin may update the org",
        )
    return await update_org(org, payload)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_endpoint(
    org_id: PydanticObjectId,
    user: User = Depends(get_current_user),
    backboard: Backboard = Depends(get_backboard),
) -> None:
    """Delete an org and tear down its Backboard assistant. Only an admin
    member of the org may delete it."""
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_admin = any(m.userId == user.id and m.role == "admin" for m in org.members)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an org admin may delete the org",
        )
    await backboard.delete_assistant(org.bbAssistantId)
    await delete_org(org)
