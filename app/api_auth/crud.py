import hashlib
import secrets
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.api_auth.models import ApiKey
from app.orgs.models import Org

# Distinctive prefix so keys are recognizable in logs/secret scanners and by the
# hook client's redaction. The remainder is 32 bytes of URL-safe entropy.
API_KEY_PREFIX = "mk_"


def generate_api_key() -> str:
    """Mint a new raw API key. High-entropy random token — never stored as-is."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    """Hash a raw key for storage/lookup. SHA-256 is appropriate for
    high-entropy tokens (unlike passwords, no slow KDF is needed)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def list_user_orgs(user_id: PydanticObjectId) -> list[Org]:
    """All orgs the user is a member of (via the embedded members list)."""
    return await Org.find({"members.userId": user_id}).to_list()


async def list_user_api_keys(user_id: PydanticObjectId) -> list[ApiKey]:
    """All API keys owned by the user, newest first. Never exposes the raw key
    (which isn't stored) — callers must project away `keyHash` when serializing."""
    return await ApiKey.find(ApiKey.userId == user_id).sort(-ApiKey.createdAt).to_list()


async def get_user_api_key(
    user_id: PydanticObjectId, key_id: PydanticObjectId
) -> ApiKey | None:
    """A single API key by id, scoped to its owner so users can't read keys
    belonging to another user. Returns None if not found or not owned."""
    return await ApiKey.find_one(ApiKey.id == key_id, ApiKey.userId == user_id)


async def delete_user_api_key(
    user_id: PydanticObjectId, key_id: PydanticObjectId
) -> bool:
    """Revoke (hard-delete) a single API key, scoped to its owner so users can't
    delete keys belonging to another user. Returns True if a key was deleted,
    False if none matched (not found or not owned)."""
    key = await ApiKey.find_one(ApiKey.id == key_id, ApiKey.userId == user_id)
    if key is None:
        return False
    await key.delete()
    return True


async def create_api_key(
    user_id: PydanticObjectId, org_id: PydanticObjectId, label: str
) -> tuple[ApiKey, str]:
    """Persist a new key (storing only its hash) and return the doc plus the
    raw key. The raw key is the caller's only chance to capture the secret."""
    raw_key = generate_api_key()
    doc = ApiKey(
        userId=user_id,
        orgId=org_id,
        keyHash=hash_api_key(raw_key),
        label=label,
        createdAt=datetime.now(timezone.utc),
    )
    await doc.insert()
    return doc, raw_key
