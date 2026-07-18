from datetime import datetime, timezone

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.backboard.client import Backboard, get_backboard
from app.dependencies import get_current_user
from app.orgs.crud import (
    accept_org_invite,
    create_org,
    create_org_invite,
    delete_org,
    get_org,
    get_org_invite,
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

router = APIRouter()


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
    # NOTE: sent after the invite is persisted and not wrapped, so a Resend
    # failure 500s the caller even though a valid invite already exists. Make
    # this best-effort (or add a resend/accept-link flow) before relying on it.
    await send_email(
        payload.email,
        f"You've been invited to {org.name}",
        text=f"You have been invited to {org.name}",
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
