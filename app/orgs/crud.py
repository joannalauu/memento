import re
import secrets
from datetime import datetime, timedelta, timezone

from beanie import PydanticObjectId

from app.orgs.models import Org, OrgInvite, OrgMember, Repo, User
from app.orgs.schemas import OrgMemberRead, OrgUpdate, UserPublic

# Org invites remain valid for 3 days from creation.
ORG_INVITE_EXPIRY = timedelta(days=3)


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


async def list_repos_for_org(org_id: PydanticObjectId) -> list[Repo]:
    """List an org's repos, newest first."""
    return await Repo.find(Repo.orgId == org_id).sort(-Repo.createdAt).to_list()


async def list_org_members(org: Org) -> list[OrgMemberRead]:
    """Resolve an org's member list, replacing each userId reference with the
    full user object. Users are fetched in a single batched query; a member
    whose user document has since been deleted is skipped, and the result
    preserves the org's member ordering."""
    user_ids = [m.userId for m in org.members]
    users = await User.find({"_id": {"$in": user_ids}}).to_list()
    users_by_id = {u.id: u for u in users}
    resolved: list[OrgMemberRead] = []
    for member in org.members:
        user = users_by_id.get(member.userId)
        if user is None:
            continue
        resolved.append(
            OrgMemberRead(
                user=UserPublic.model_validate(user),
                role=member.role,
                joinedAt=member.joinedAt,
            )
        )
    return resolved


async def create_org_invite(*, org_id: PydanticObjectId, email: str) -> OrgInvite:
    """Create an org invite valid for ORG_INVITE_EXPIRY, with a unique token."""
    invite = OrgInvite(
        orgId=org_id,
        email=email,
        token=secrets.token_urlsafe(32),
        expiresAt=datetime.now(timezone.utc) + ORG_INVITE_EXPIRY,
    )
    await invite.insert()
    return invite


async def get_org_invite(org_id: PydanticObjectId, token: str) -> OrgInvite | None:
    """Retrieve an org's invite by its token."""
    return await OrgInvite.find_one(OrgInvite.orgId == org_id, OrgInvite.token == token)


async def accept_org_invite(
    *, org: Org, invite: OrgInvite, user_id: PydanticObjectId
) -> Org:
    """Add the user to the org as a member and mark the invite accepted."""
    now = datetime.now(timezone.utc)
    org.members.append(OrgMember(userId=user_id, role="member", joinedAt=now))
    invite.acceptedAt = now
    await org.save()
    await invite.save()
    return org
