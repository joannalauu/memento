import gzip
import io
import logging
import re
import zlib
from datetime import datetime, timedelta, timezone

from beanie import PydanticObjectId
from bson import ObjectId
from gridfs.asynchronous import AsyncGridFSBucket
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.claude_hook.models import AgentSession
from app.orgs.models import Repo

logger = logging.getLogger(__name__)

# Sanity caps. The hook redacts + gzips a chat transcript; anything past these
# is not a real capture, so reject rather than buffer/expand it.
MAX_COMPRESSED_BYTES = 20 * 1024 * 1024  # 20 MB gzipped body
MAX_RAW_BYTES = 100 * 1024 * 1024  # 100 MB decompressed (gzip-bomb guard)

TRANSCRIPT_BUCKET = "transcripts"  # GridFS bucket for raw JSONL blobs
EXPIRES_AFTER = timedelta(days=14)  # TTL window; unset once matched/distilled


class BadGzip(ValueError):
    """Body was not valid gzip. Maps to HTTP 400."""


class TooLarge(ValueError):
    """Body exceeded a size cap (compressed or decompressed). Maps to HTTP 413."""


# git@host:owner/name(.git) and ssh://git@host/owner/name(.git)
_SCP_RE = re.compile(
    r"^(?:ssh://)?[^@/\s]+@[\w.-]+[:/](?P<owner>[^/:\s]+)/(?P<name>[^/\s]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
# http(s)://(userinfo@)host/owner/name(.git)
_HTTP_RE = re.compile(
    r"^https?://(?:[^@/\s]+@)?[\w.-]+/(?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def parse_git_remote(remote: str) -> tuple[str, str] | None:
    """Parse a git remote URL into (owner, name), or None if unrecognized.

    Handles scp-style (`git@github.com:acme/api.git`), `ssh://` and
    `https://` forms, an optional `.git` suffix, a trailing slash, and https
    userinfo. Deeper paths, bare hosts, and local paths return None."""
    remote = remote.strip()
    for pattern in (_SCP_RE, _HTTP_RE):
        m = pattern.match(remote)
        if m:
            return m.group("owner"), m.group("name")
    return None


async def find_repo_by_remote(
    org_id: PydanticObjectId, owner: str, name: str
) -> Repo | None:
    """Resolve a repo within an org from its remote's (owner, name).

    Tries an exact match first (served by the org_owner_name index), then a
    case-insensitive fallback since GitHub owner/name are case-insensitive and
    the stored casing may differ from the remote's."""
    repo = await Repo.find_one(
        Repo.orgId == org_id, Repo.owner == owner, Repo.name == name
    )
    if repo is None:
        repo = await Repo.find_one(
            {
                "orgId": org_id,
                "owner": re.compile(f"^{re.escape(owner)}$", re.IGNORECASE),
                "name": re.compile(f"^{re.escape(name)}$", re.IGNORECASE),
            }
        )
    return repo


def gunzip_bounded(body: bytes) -> bytes:
    """Decompress a gzipped body with size guards.

    Raises TooLarge if the compressed body or its decompressed size exceeds the
    caps, and BadGzip if the body is not valid gzip. The read is bounded so a
    gzip bomb can never exhaust memory."""
    if len(body) > MAX_COMPRESSED_BYTES:
        raise TooLarge
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as f:
            raw = f.read(MAX_RAW_BYTES + 1)  # one over the cap to detect overflow
    except (OSError, EOFError, zlib.error) as e:  # BadGzipFile subclasses OSError
        raise BadGzip from e
    if len(raw) > MAX_RAW_BYTES:
        raise TooLarge
    return raw


def _transcript_bucket(db: AsyncDatabase) -> AsyncGridFSBucket:
    return AsyncGridFSBucket(db, bucket_name=TRANSCRIPT_BUCKET)


async def upload_transcript(db: AsyncDatabase, session_id: str, raw: bytes) -> str:
    """Store a raw JSONL transcript in GridFS, returning its id as a string."""
    file_id = await _transcript_bucket(db).upload_from_stream(
        f"{session_id}.jsonl",
        raw,
        metadata={"sessionId": session_id, "contentType": "application/x-ndjson"},
    )
    return str(file_id)


async def delete_transcript(db: AsyncDatabase, ref: str) -> None:
    """Best-effort GridFS blob GC. Never raises — a missing file or bad id is
    logged and swallowed so it can't fail an otherwise-successful ingest."""
    try:
        await _transcript_bucket(db).delete(ObjectId(ref))
    except Exception:
        logger.warning("failed to GC transcript blob %s", ref, exc_info=True)


async def upsert_agent_session(
    *,
    org_id: PydanticObjectId,
    repo_id: PydanticObjectId,
    user_id: PydanticObjectId,
    session_id: str,
    branch: str,
    transcript_ref: str,
    token_estimate: int | None,
) -> tuple[AgentSession, str | None]:
    """Upsert on sessionId, returning (doc, old_transcript_ref).

    A resumed session re-fires the hook with a longer transcript: on a match we
    overwrite the capture, reset status/normalizedRef/expiresAt, and return the
    previous transcriptRef so the caller can GC its blob. On first capture the
    old ref is None. A DuplicateKeyError (E11000 race on the unique sessionId
    index) falls through to the update path — the later SessionEnd, holding the
    fuller transcript, wins."""
    now = datetime.now(timezone.utc)
    for _ in range(2):
        existing = await AgentSession.find_one({"sessionId": session_id})
        if existing is not None:
            old_ref = existing.transcriptRef
            existing.orgId = org_id
            existing.repoId = repo_id
            existing.userId = user_id
            existing.branch = branch
            existing.transcriptRef = transcript_ref
            existing.normalizedRef = None  # fuller capture → re-normalize
            existing.tokenEstimate = token_estimate
            existing.status = "stored"
            existing.expiresAt = now + EXPIRES_AFTER
            existing.updatedAt = now
            await existing.save()
            return existing, old_ref

        doc = AgentSession(
            orgId=org_id,
            repoId=repo_id,
            userId=user_id,
            sessionId=session_id,
            branch=branch,
            transcriptRef=transcript_ref,
            tokenEstimate=token_estimate,
            status="stored",
            expiresAt=now + EXPIRES_AFTER,
            createdAt=now,  # updatedAt stays None on first capture
        )
        try:
            await doc.insert()
            return doc, None
        except DuplicateKeyError:
            continue  # lost the insert race; retry as an update

    # Two consecutive races is effectively unreachable; surface rather than loop.
    raise RuntimeError("agent-session upsert contention")


async def enqueue_normalization(agent_session_id: str) -> None:
    """Stub: hand a stored session off to the normalizer.

    TODO(normalizer): replace with a real queue/worker dispatch. For now this
    just records intent so the ingest path and its contract are complete."""
    logger.info("normalization queued for agentSession %s (stub)", agent_session_id)
