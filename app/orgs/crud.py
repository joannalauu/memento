import re
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.orgs.models import Org, OrgMember
from app.orgs.schemas import OrgUpdate


def slugify(name: str) -> str:
    """Derive a URL-safe slug from a display name: lowercase, non-alphanumeric
    runs collapsed to single hyphens, leading/trailing hyphens stripped."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


async def create_org(
    *, name: str, bb_assistant_id: str, creator_id: PydanticObjectId
) -> Org:
    """Create an org with a name-derived slug and the creator seeded as its
    first admin member."""
    org = Org(
        name=name,
        slug=slugify(name),
        bbAssistantId=bb_assistant_id,
        members=[
            OrgMember(
                userId=creator_id,
                role="admin",
                joinedAt=datetime.now(timezone.utc),
            )
        ],
    )
    await org.insert()
    return org


async def get_org(org_id: PydanticObjectId) -> Org | None:
    """Retrieve an org by its ObjectId."""
    return await Org.get(org_id)


async def update_org(org: Org, payload: OrgUpdate) -> Org:
    """Apply a partial update to an org. Only fields explicitly set on the
    payload are touched; renaming re-derives the slug."""
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        org.name = data["name"]
        org.slug = slugify(data["name"])
    if "githubInstallationId" in data:
        org.githubInstallationId = data["githubInstallationId"]
    await org.save()
    return org


async def delete_org(org: Org) -> None:
    """Delete an org document."""
    await org.delete()
