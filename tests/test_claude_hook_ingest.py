import gzip

import pytest
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient
from pymongo.errors import DuplicateKeyError

from app.claude_hook import crud
from app.claude_hook.models import AgentSession
from app.dependencies import get_api_key_principal, get_client
from app.main import app


@pytest.fixture(autouse=True)
def stub_beanie(monkeypatch):
    # Document.__init__ calls get_pymongo_collection() only as an "init_beanie
    # has run" guard; stub it so constructing an AgentSession needs no Mongo.
    monkeypatch.setattr(
        AgentSession, "get_pymongo_collection", classmethod(lambda cls: None)
    )


# --- parse_git_remote -------------------------------------------------------


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("git@github.com:acme/api.git", ("acme", "api")),
        ("git@github.com:acme/api", ("acme", "api")),
        ("ssh://git@github.com/acme/api.git", ("acme", "api")),
        ("https://github.com/acme/api.git", ("acme", "api")),
        ("https://github.com/acme/api", ("acme", "api")),
        ("https://github.com/acme/api/", ("acme", "api")),
        ("http://github.com/acme/api", ("acme", "api")),
        ("https://x-token@github.com/acme/api.git", ("acme", "api")),
        ("git@github.com:ACME/API.git", ("ACME", "API")),
    ],
)
def test_parse_git_remote_accepts(remote, expected):
    assert crud.parse_git_remote(remote) == expected


@pytest.mark.parametrize(
    "remote",
    [
        "",
        "   ",
        "github.com/acme",
        "https://github.com/acme",
        "https://github.com/acme/api/extra",
        "/local/path/to/repo",
        "not a url",
    ],
)
def test_parse_git_remote_rejects(remote):
    assert crud.parse_git_remote(remote) is None


# --- gunzip_bounded ---------------------------------------------------------


def test_gunzip_bounded_roundtrips():
    payload = b'{"type":"user"}\n{"type":"assistant"}\n'
    assert crud.gunzip_bounded(gzip.compress(payload)) == payload


def test_gunzip_bounded_rejects_truncated():
    blob = gzip.compress(b"hello world" * 100)
    with pytest.raises(crud.BadGzip):
        crud.gunzip_bounded(blob[: len(blob) // 2])


def test_gunzip_bounded_rejects_non_gzip():
    with pytest.raises(crud.BadGzip):
        crud.gunzip_bounded(b"this is plainly not gzip")


def test_gunzip_bounded_rejects_oversized_compressed(monkeypatch):
    monkeypatch.setattr(crud, "MAX_COMPRESSED_BYTES", 8)
    with pytest.raises(crud.TooLarge):
        crud.gunzip_bounded(gzip.compress(b"x" * 1000))


def test_gunzip_bounded_rejects_bomb(monkeypatch):
    monkeypatch.setattr(crud, "MAX_RAW_BYTES", 16)
    # Highly compressible: small gzipped body, large decompressed.
    with pytest.raises(crud.TooLarge):
        crud.gunzip_bounded(gzip.compress(b"a" * 10_000))


# --- upsert_agent_session ---------------------------------------------------


def _kwargs(**over):
    base = dict(
        org_id=PydanticObjectId(),
        repo_id=PydanticObjectId(),
        user_id=PydanticObjectId(),
        session_id="sess-1",
        branch="main",
        transcript_ref="ref-new",
        token_estimate=42,
    )
    base.update(over)
    return base


async def test_upsert_inserts_fresh(monkeypatch):
    inserted = []

    async def fake_find_one(*a, **k):
        return None

    async def fake_insert(self):
        inserted.append(self)
        return self

    monkeypatch.setattr(AgentSession, "find_one", staticmethod(fake_find_one))
    monkeypatch.setattr(AgentSession, "insert", fake_insert)

    doc, old_refs = await crud.upsert_agent_session(**_kwargs())

    assert old_refs == []
    assert inserted == [doc]
    assert doc.status == "stored"
    assert doc.updatedAt is None  # first capture
    assert doc.transcriptRef == "ref-new"
    assert doc.expiresAt is not None


async def test_upsert_replaces_existing(monkeypatch):
    existing = AgentSession.model_construct(
        sessionId="sess-1",
        transcriptRef="ref-old",
        normalizedRef="norm-old",
        normalizedTokenEstimate=99,
        status="normalized",
        updatedAt=None,
    )
    saved = []

    async def fake_find_one(*a, **k):
        return existing

    async def fake_save(self, *a, **k):
        saved.append(self)
        return self

    monkeypatch.setattr(AgentSession, "find_one", staticmethod(fake_find_one))
    monkeypatch.setattr(AgentSession, "save", fake_save)

    doc, old_refs = await crud.upsert_agent_session(**_kwargs())

    assert doc is existing
    assert old_refs == ["ref-old", "norm-old"]  # both blobs superseded
    assert doc.transcriptRef == "ref-new"
    assert doc.normalizedRef is None  # cleared → re-normalize
    assert doc.normalizedTokenEstimate is None
    assert doc.status == "stored"
    assert doc.updatedAt is not None
    assert saved == [existing]


async def test_upsert_falls_through_on_insert_race(monkeypatch):
    existing = AgentSession.model_construct(
        sessionId="sess-1", transcriptRef="ref-old", status="stored"
    )
    calls = {"find": 0}

    async def fake_find_one(*a, **k):
        calls["find"] += 1
        return None if calls["find"] == 1 else existing  # appears after the race

    async def fake_insert(self):
        raise DuplicateKeyError("E11000 duplicate key")

    async def fake_save(self, *a, **k):
        return self

    monkeypatch.setattr(AgentSession, "find_one", staticmethod(fake_find_one))
    monkeypatch.setattr(AgentSession, "insert", fake_insert)
    monkeypatch.setattr(AgentSession, "save", fake_save)

    doc, old_refs = await crud.upsert_agent_session(**_kwargs())

    assert doc is existing
    assert old_refs == ["ref-old"]
    assert calls["find"] == 2


# --- route ------------------------------------------------------------------


class _FakeApiKey:
    orgId = PydanticObjectId()
    userId = PydanticObjectId()


class _FakePrincipal:
    api_key = _FakeApiKey()


@pytest.fixture
def api_client(monkeypatch):
    """AsyncClient with auth + db dependencies overridden and the GridFS /
    upsert layer monkeypatched, so route tests need no Mongo."""
    app.dependency_overrides[get_api_key_principal] = lambda: _FakePrincipal()
    app.dependency_overrides[get_client] = lambda: object()

    async def fake_upload(db, session_id, raw):
        return "ref-new"

    deleted = []

    async def fake_delete(db, ref):
        deleted.append(ref)

    async def fake_enqueue(db, agent_session_id):
        return None

    monkeypatch.setattr(crud, "upload_transcript", fake_upload)
    monkeypatch.setattr(crud, "delete_transcript", fake_delete)
    monkeypatch.setattr(crud, "enqueue_normalization", fake_enqueue)

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield client, deleted
    finally:
        app.dependency_overrides.clear()


def _repo(repo_id=None):
    class _Repo:
        id = repo_id or PydanticObjectId()

    return _Repo()


async def test_ingest_accepts_and_returns_202(api_client, monkeypatch):
    client, deleted = api_client

    async def fake_find_repo(org_id, owner, name):
        return _repo()

    async def fake_upsert(**kwargs):
        doc = AgentSession.model_construct(
            id=PydanticObjectId(), sessionId=kwargs["session_id"], status="stored"
        )
        return doc, []  # first capture, no old blobs

    monkeypatch.setattr(crud, "find_repo_by_remote", fake_find_repo)
    monkeypatch.setattr(crud, "upsert_agent_session", fake_upsert)

    async with client:
        resp = await client.post(
            "/ingest/agent-sessions",
            content=gzip.compress(b'{"type":"user"}\n'),
            headers={
                "Content-Type": "application/x-ndjson",
                "Content-Encoding": "gzip",
                "X-Session-Id": "sess-1",
                "X-Git-Remote": "git@github.com:acme/api.git",
                "X-Git-Branch": "main",
            },
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["sessionId"] == "sess-1"
    assert body["status"] == "stored"
    assert deleted == []  # nothing to GC on first capture


async def test_ingest_gcs_old_blob_after_upsert(api_client, monkeypatch):
    client, deleted = api_client

    async def fake_find_repo(org_id, owner, name):
        return _repo()

    async def fake_upsert(**kwargs):
        doc = AgentSession.model_construct(
            id=PydanticObjectId(), sessionId=kwargs["session_id"], status="stored"
        )
        return doc, ["ref-old", "norm-old"]  # resumed session → blobs to GC

    monkeypatch.setattr(crud, "find_repo_by_remote", fake_find_repo)
    monkeypatch.setattr(crud, "upsert_agent_session", fake_upsert)

    async with client:
        resp = await client.post(
            "/ingest/agent-sessions",
            content=gzip.compress(b'{"type":"user"}\n'),
            headers={
                "Content-Encoding": "gzip",
                "X-Session-Id": "sess-1",
                "X-Git-Remote": "git@github.com:acme/api.git",
            },
        )

    assert resp.status_code == 202
    # both superseded blobs GC'd, only after a successful upsert
    assert deleted == ["ref-old", "norm-old"]


async def test_ingest_404_on_missing_remote(api_client):
    client, _ = api_client
    async with client:
        resp = await client.post(
            "/ingest/agent-sessions",
            content=gzip.compress(b"x"),
            headers={"Content-Encoding": "gzip", "X-Session-Id": "sess-1"},
        )
    assert resp.status_code == 404


async def test_ingest_404_on_unknown_repo(api_client, monkeypatch):
    client, _ = api_client

    async def fake_find_repo(org_id, owner, name):
        return None

    monkeypatch.setattr(crud, "find_repo_by_remote", fake_find_repo)
    async with client:
        resp = await client.post(
            "/ingest/agent-sessions",
            content=gzip.compress(b"x"),
            headers={
                "Content-Encoding": "gzip",
                "X-Session-Id": "sess-1",
                "X-Git-Remote": "git@github.com:acme/api.git",
            },
        )
    assert resp.status_code == 404


async def test_ingest_400_on_bad_gzip(api_client, monkeypatch):
    client, deleted = api_client

    async def fake_find_repo(org_id, owner, name):
        return _repo()

    monkeypatch.setattr(crud, "find_repo_by_remote", fake_find_repo)
    async with client:
        resp = await client.post(
            "/ingest/agent-sessions",
            content=b"this is not gzip",
            headers={
                "X-Session-Id": "sess-1",
                "X-Git-Remote": "git@github.com:acme/api.git",
            },
        )
    assert resp.status_code == 400
    assert deleted == []  # never uploaded, so nothing to clean up
