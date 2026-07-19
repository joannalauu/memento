import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.file_upload import gap_detection
from app.file_upload.gap_detection import (
    MAX_GAP_QUESTIONS_PER_DOC,
    _parse_verdict,
    _read_current_code,
    detect_and_open_gaps,
)


def _memory(content="[repo:acme/api] we use asyncpg", files=("app/main.py",)):
    return SimpleNamespace(
        contentSnapshot=content,
        anchors=SimpleNamespace(files=list(files)),
    )


def _bb_returning(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(content=text))
    )


def _conflict(files=("app/main.py",), conflicts=True) -> str:
    return json.dumps({"conflicts": conflicts, "files": list(files), "reasoning": "r"})


def _stub_repo(monkeypatch, *, get_file, head="headsha"):
    monkeypatch.setattr(
        gap_detection,
        "build_github_toolset",
        lambda org, repo, gh: ([], {"get_file": get_file}),
    )
    monkeypatch.setattr(
        gap_detection,
        "build_repo_history",
        lambda org, repo, gh: SimpleNamespace(head_sha=AsyncMock(return_value=head)),
    )


ORG = SimpleNamespace(id="o", bbAssistantId="assist")
REPO = SimpleNamespace(id="r", owner="acme", name="api")


# ─── pure helpers ─────────────────────────────────────────────────────────────


def test_parse_verdict_reads_object_and_strips_fence():
    v = _parse_verdict('```json\n{"conflicts": true, "files": ["a.py"]}\n```')
    assert v.conflicts is True and v.files == ["a.py"]


@pytest.mark.parametrize("bad", [None, "", "not json", "[]", '"x"'])
def test_parse_verdict_defaults_to_no_conflict(bad):
    v = _parse_verdict(bad)
    assert v.conflicts is False and v.files == []


@pytest.mark.anyio
async def test_read_current_code_drops_errors_and_empties():
    async def get_file(args):
        return {"a.py": "code a", "b.py": "Error: not found", "c.py": ""}[args["path"]]

    code = await _read_current_code(
        ["a.py", "b.py", "c.py"], get_file=get_file, cache={}
    )
    assert code == {"a.py": "code a"}


# ─── orchestration ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_opens_gap_chat_on_conflict(monkeypatch):
    async def get_file(_args):
        return "current code"

    _stub_repo(monkeypatch, get_file=get_file)
    open_mock = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(gap_detection, "open_gap_chat", open_mock)
    bb = _bb_returning(_conflict())

    opened = await detect_and_open_gaps(
        [_memory()], org=ORG, repo=REPO, bb=bb, github=object()
    )

    assert opened == 1
    # closed-world judge call
    _, kwargs = bb.send_message.call_args
    assert kwargs["memory"] == "off" and kwargs["json_output"] is True
    # synthetic verdict carries a "gap" status and the current HEAD as baseline
    args, kw = open_mock.call_args
    verdict = args[1]
    assert verdict.status == "gap" and verdict.changedFiles == ["app/main.py"]
    assert kw["trigger_commit_sha"] == "headsha"


@pytest.mark.anyio
async def test_no_chat_when_code_agrees(monkeypatch):
    async def get_file(_args):
        return "current code"

    _stub_repo(monkeypatch, get_file=get_file)
    open_mock = AsyncMock()
    monkeypatch.setattr(gap_detection, "open_gap_chat", open_mock)
    bb = _bb_returning(_conflict(conflicts=False))

    opened = await detect_and_open_gaps(
        [_memory()], org=ORG, repo=REPO, bb=bb, github=object()
    )
    assert opened == 0
    open_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_skips_memories_without_file_anchors(monkeypatch):
    async def get_file(_args):
        return "current code"

    _stub_repo(monkeypatch, get_file=get_file)
    monkeypatch.setattr(gap_detection, "open_gap_chat", AsyncMock())
    bb = _bb_returning(_conflict())

    # A memory with no file anchors is not checkable — no judge call at all.
    opened = await detect_and_open_gaps(
        [_memory(files=())], org=ORG, repo=REPO, bb=bb, github=object()
    )
    assert opened == 0
    bb.send_message.assert_not_awaited()


@pytest.mark.anyio
async def test_respects_per_doc_cap(monkeypatch):
    async def get_file(args):
        return f"code for {args['path']}"

    _stub_repo(monkeypatch, get_file=get_file)
    open_mock = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(gap_detection, "open_gap_chat", open_mock)
    bb = _bb_returning(_conflict())

    # Three conflicting memories, cap is 2: only two open, and the third is never
    # even judged (loop breaks at the cap).
    memories = [_memory(files=(f"app/m{i}.py",)) for i in range(3)]
    for i, m in enumerate(memories):
        m.anchors.files = [f"app/m{i}.py"]

    opened = await detect_and_open_gaps(
        memories, org=ORG, repo=REPO, bb=bb, github=object()
    )
    assert opened == MAX_GAP_QUESTIONS_PER_DOC == 2
    assert open_mock.await_count == 2
    assert bb.send_message.await_count == 2


@pytest.mark.anyio
async def test_returns_zero_when_repo_unreachable(monkeypatch):
    def _raise(org, repo, gh):
        raise RuntimeError("no installation")

    monkeypatch.setattr(gap_detection, "build_github_toolset", _raise)
    monkeypatch.setattr(gap_detection, "open_gap_chat", AsyncMock())
    bb = _bb_returning(_conflict())

    opened = await detect_and_open_gaps(
        [_memory()], org=ORG, repo=REPO, bb=bb, github=object()
    )
    assert opened == 0
