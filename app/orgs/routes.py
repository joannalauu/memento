from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.backboard.client import Backboard, get_backboard
from app.dependencies import get_current_user
from app.orgs.crud import create_org, delete_org, get_org, update_org
from app.orgs.models import Org, User
from app.orgs.schemas import OrgCreate, OrgRead, OrgUpdate

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
