from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.api_auth import crud
from app.api_auth.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.dependencies import get_current_user

router = APIRouter()


@router.get("", response_model=list[ApiKeyRead])
async def list_api_keys(
    user=Depends(get_current_user),
) -> list[ApiKeyRead]:
    """List the authenticated user's API keys. The secret is never returned —
    only metadata (label, org, timestamps)."""
    keys = await crud.list_user_api_keys(user.id)
    return [ApiKeyRead.model_validate(key, from_attributes=True) for key in keys]


@router.get("/{key_id}", response_model=ApiKeyRead)
async def get_api_key(
    key_id: PydanticObjectId,
    user=Depends(get_current_user),
) -> ApiKeyRead:
    """Fetch a single API key owned by the authenticated user. The secret is
    never returned. Returns 404 if the key doesn't exist or belongs to another
    user."""
    key = await crud.get_user_api_key(user.id, key_id)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="api key not found",
        )
    return ApiKeyRead.model_validate(key, from_attributes=True)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: PydanticObjectId,
    user=Depends(get_current_user),
) -> None:
    """Revoke an API key owned by the authenticated user. Deletion is immediate
    and permanent — the key stops authenticating on the next request. Returns 404
    if the key doesn't exist or belongs to another user."""
    deleted = await crud.delete_user_api_key(user.id, key_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="api key not found",
        )


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate,
    user=Depends(get_current_user),
) -> ApiKeyCreated:
    """Create an API key for the authenticated engineer.

    The raw key is returned exactly once (`key`) and never retrievable again —
    only its hash is stored. If the caller belongs to a single org it is used
    automatically; otherwise `orgId` must be supplied and membership is verified.
    """
    orgs = await crud.list_user_orgs(user.id)
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user is not a member of any org",
        )

    if body.orgId is not None:
        if not any(org.id == body.orgId for org in orgs):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not a member of the specified org",
            )
        org_id = body.orgId
    elif len(orgs) == 1:
        org_id = orgs[0].id
        assert org_id is not None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="multiple orgs; specify orgId",
        )

    doc, raw_key = await crud.create_api_key(user.id, org_id, body.label)
    assert doc.id is not None
    return ApiKeyCreated(
        id=doc.id,
        label=doc.label,
        orgId=doc.orgId,
        key=raw_key,
        createdAt=doc.createdAt,
    )
