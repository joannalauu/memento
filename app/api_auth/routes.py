from fastapi import APIRouter, Depends, HTTPException, status

from app.api_auth import crud
from app.api_auth.schemas import ApiKeyCreate, ApiKeyCreated
from app.dependencies import get_current_user

router = APIRouter()


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
