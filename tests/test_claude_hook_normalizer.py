import json

import pytest
from beanie import PydanticObjectId

from app.claude_hook import crud, normalizer
from app.claude_hook.models import AgentSession
from app.claude_hook.normalizer import (
    BLOCK_CHAR_CAP,
    STUB_CHAR_CAP,
    normalize_jsonl,
    render_jsonl,
    tool_stub,
)


@pytest.fixture(autouse=True)
def stub_beanie(monkeypatch):
    # Document.__init__ calls get_pymongo_collection() only as an "init_beanie
    # has run" guard; stub it so constructing an AgentSession needs no Mongo.
    monkeypatch.setattr(
        AgentSession, "get_pymongo_collection", classmethod(lambda cls: None)
    )


# --- fixture builders -------------------------------------------------------


def _jsonl(*objs) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


def _user(content, **extra) -> dict:
    return {"type": "user", "message": {"role": "user", "content": content}, **extra}


def _assistant(content, **extra) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": content},
        **extra,
    }


def _tool_use(name, tool_input=None, tid="tu-1") -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "input": tool_input or {}}


def _tool_result(tid, content) -> dict:
    return {"type": "tool_result", "tool_use_id": tid, "content": content}


def _texts(result, kind=None):
    return [e.text for e in result.entries if kind is None or e.kind == kind]


# --- keep/drop rules --------------------------------------------------------


def test_keeps_string_content_user_message():
    result = normalize_jsonl(_jsonl(_user("fix the login bug")))
    assert [(e.role, e.kind, e.text) for e in result.entries] == [
        ("user", "text", "fix the login bug")
    ]


def test_keeps_user_text_blocks_drops_sibling_tool_results():
    result = normalize_jsonl(
        _jsonl(
            _assistant([_tool_use("Read", {"file_path": "a.py"}, "tu-1")]),
            _user(
                [
                    {"type": "text", "text": "looks wrong"},
                    _tool_result("tu-1", "x" * 500),
                ]
            ),
        )
    )
    assert _texts(result, "text") == ["looks wrong"]
    assert _texts(result, "tool_result") == []


def test_user_line_with_only_tool_result_yields_nothing():
    result = normalize_jsonl(
        _jsonl(
            _assistant([_tool_use("Bash", {"command": "ls"}, "tu-1")]),
            _user([_tool_result("tu-1", "big stdout")]),
        )
    )
    assert [e.kind for e in result.entries] == ["tool"]


def test_skips_meta_and_sidechain_lines():
    result = normalize_jsonl(
        _jsonl(
            _user("harness caveat", isMeta=True),
            _user("subagent prompt", isSidechain=True),
            _assistant([{"type": "text", "text": "subagent reply"}], isSidechain=True),
            _user("real prompt"),
        )
    )
    assert _texts(result) == ["real prompt"]


def test_keeps_assistant_text_drops_thinking():
    result = normalize_jsonl(
        _jsonl(
            _assistant(
                [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {"type": "text", "text": "here is my plan"},
                ]
            )
        )
    )
    assert _texts(result) == ["here is my plan"]


def test_keeps_summary_lines():
    result = normalize_jsonl(
        _jsonl({"type": "summary", "summary": "Session about auth", "leafUuid": "u1"})
    )
    assert [(e.role, e.kind, e.text) for e in result.entries] == [
        ("system", "summary", "Session about auth")
    ]


def test_skips_unknown_line_types():
    result = normalize_jsonl(
        _jsonl(
            {"type": "system", "content": "hook output"},
            {"type": "file-history-snapshot", "snapshot": {}},
            {"type": "progress", "data": "x"},
        )
    )
    assert result.entries == []
    assert result.source_lines == 3


def test_drops_harness_command_wrappers():
    result = normalize_jsonl(
        _jsonl(
            _user(
                "<command-name>/clear</command-name><command-message>clear</command-message>"
            ),
            _user("<local-command-stdout>ok</local-command-stdout>"),
            _user("real question"),
        )
    )
    assert _texts(result) == ["real question"]


# --- tool_result discrimination by producer --------------------------------


def test_task_result_kept_in_full_bulk_results_dropped():
    report = "Subagent findings: " + "detail " * 50
    result = normalize_jsonl(
        _jsonl(
            _assistant(
                [
                    _tool_use("Task", {"description": "explore auth"}, "tu-task"),
                    _tool_use("Read", {"file_path": "a.py"}, "tu-read"),
                    _tool_use("Bash", {"command": "pytest"}, "tu-bash"),
                    _tool_use("Grep", {"pattern": "foo"}, "tu-grep"),
                ]
            ),
            _user([_tool_result("tu-task", report)]),
            _user([_tool_result("tu-read", "file contents")]),
            _user([_tool_result("tu-bash", "test output")]),
            _user([_tool_result("tu-grep", "matches")]),
        )
    )
    assert _texts(result, "tool_result") == [report.strip()]


def test_tool_result_with_unresolvable_id_dropped():
    result = normalize_jsonl(_jsonl(_user([_tool_result("tu-unknown", "orphan")])))
    assert result.entries == []


def test_task_result_extracts_nested_text_blocks():
    result = normalize_jsonl(
        _jsonl(
            _assistant([_tool_use("Task", {"description": "d"}, "tu-1")]),
            _user(
                [
                    _tool_result(
                        "tu-1",
                        [
                            {"type": "text", "text": "part one"},
                            {"type": "text", "text": "part two"},
                        ],
                    )
                ]
            ),
        )
    )
    assert _texts(result, "tool_result") == ["part one\npart two"]


def test_task_result_exempt_from_block_cap():
    report = "R" * (BLOCK_CHAR_CAP * 3)
    result = normalize_jsonl(
        _jsonl(
            _assistant([_tool_use("Task", {"description": "d"}, "tu-1")]),
            _user([_tool_result("tu-1", report)]),
        )
    )
    assert _texts(result, "tool_result") == [report]


# --- tool stubs -------------------------------------------------------------


@pytest.mark.parametrize(
    "name,tool_input,expected",
    [
        ("Read", {"file_path": "src/auth/session.ts"}, "Read: src/auth/session.ts"),
        ("Edit", {"file_path": "app/x.py"}, "Edit: app/x.py"),
        ("Write", {"file_path": "a.txt"}, "Write: a.txt"),
        (
            "Bash",
            {"description": "run test suite", "command": "pytest"},
            "Bash: run test suite",
        ),
        ("Bash", {"command": "pytest -x\n--verbose"}, "Bash: pytest -x"),
        ("Grep", {"pattern": "normalizedRef"}, "Grep: normalizedRef"),
        ("Glob", {"pattern": "**/*.py"}, "Glob: **/*.py"),
        ("Task", {"description": "explore auth module"}, "Task: explore auth module"),
        ("WebFetch", {"url": "https://example.com"}, "WebFetch: https://example.com"),
        ("WebSearch", {"query": "fastapi gridfs"}, "WebSearch: fastapi gridfs"),
        ("FancyTool", {"whatever": "x"}, "FancyTool"),
        ("Read", {"file_path": 42}, "Read"),
        ("Read", {}, "Read"),
        ("Read", None, "Read"),
    ],
)
def test_tool_stub(name, tool_input, expected):
    assert tool_stub(name, tool_input) == expected


def test_tool_stub_hard_capped():
    stub = tool_stub("Read", {"file_path": "x" * 500})
    assert len(stub) == STUB_CHAR_CAP


def test_tool_use_becomes_stub_entry():
    result = normalize_jsonl(
        _jsonl(_assistant([_tool_use("Read", {"file_path": "a.py"})]))
    )
    assert [(e.role, e.kind, e.text) for e in result.entries] == [
        ("assistant", "tool", "Read: a.py")
    ]


# --- malformed input --------------------------------------------------------


def test_malformed_lines_never_raise():
    raw = b"\n".join(
        [
            b"not json at all",
            b"42",
            b"[1, 2, 3]",
            b'"just a string"',
            json.dumps({"type": "user"}).encode(),  # no message
            json.dumps({"type": "user", "message": "wrong shape"}).encode(),
            json.dumps({"type": "user", "message": {"content": 42}}).encode(),
            json.dumps(
                {"type": "assistant", "message": {"content": [17, None]}}
            ).encode(),
            json.dumps(
                _user(
                    [{"type": "tool_result", "tool_use_id": {"bad": 1}, "content": "x"}]
                )
            ).encode(),
            json.dumps({"type": "summary", "summary": 42}).encode(),
            json.dumps(_user("still works")).encode(),
        ]
    )
    result = normalize_jsonl(raw)
    assert _texts(result) == ["still works"]
    assert result.source_lines == 11


def test_empty_transcript():
    result = normalize_jsonl(b"")
    assert result.entries == []
    assert result.token_estimate == 0
    assert result.truncated is False
    assert result.source_lines == 0
    assert render_jsonl(result.entries) == b""


# --- truncation & budget ----------------------------------------------------


def test_per_block_cap_middle_elides():
    text = "H" * 3000 + "M" * 4000 + "T" * 3000
    result = normalize_jsonl(_jsonl(_user(text)))
    (kept,) = _texts(result)
    assert kept.startswith("H" * 100)
    assert kept.endswith("T" * 100)
    assert "…[truncated ~" in kept
    assert len(kept) <= BLOCK_CHAR_CAP + 50  # cap plus the marker
    assert result.truncated is True


def test_budget_elision_keeps_first_prompt_and_tail():
    lines = [_user("FIRST PROMPT")]
    for i in range(30):
        lines.append(
            _assistant([{"type": "text", "text": f"essay {i} " + "words " * 60}])
        )
    lines.append(_user("FINAL QUESTION"))
    raw = _jsonl(*lines)

    budget = 300  # tokens → 1200 chars, far under the ~10k chars of input
    result = normalize_jsonl(raw, budget_tokens=budget)

    elisions = [e for e in result.entries if e.kind == "elision"]
    assert len(elisions) == 1
    assert "[elided " in elisions[0].text
    assert result.entries[0].text == "FIRST PROMPT"
    assert result.entries[-1].text == "FINAL QUESTION"
    assert result.truncated is True
    # within budget, allowing the marker + unconditional final-entry tolerance
    assert len(render_jsonl(result.entries)) <= budget * 4 + 300


def test_budget_elision_is_deterministic():
    lines = [_user("start")]
    for i in range(50):
        lines.append(_assistant([{"type": "text", "text": f"block {i} " + "x" * 200}]))
    raw = _jsonl(*lines)
    a = render_jsonl(normalize_jsonl(raw, budget_tokens=500).entries)
    b = render_jsonl(normalize_jsonl(raw, budget_tokens=500).entries)
    assert a == b


def test_under_budget_transcript_untouched():
    result = normalize_jsonl(
        _jsonl(_user("hi"), _assistant([{"type": "text", "text": "hello"}]))
    )
    assert result.truncated is False
    assert [e.kind for e in result.entries] == ["text", "text"]


# --- render_jsonl -----------------------------------------------------------


def test_render_jsonl_round_trips():
    result = normalize_jsonl(
        _jsonl(_user("prompt"), _assistant([_tool_use("Read", {"file_path": "a.py"})]))
    )
    blob = render_jsonl(result.entries)
    lines = blob.decode().splitlines()
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        assert set(obj) == {"role", "kind", "text"}


# --- normalize_session driver ----------------------------------------------


def _doc(**over):
    base = dict(
        id=PydanticObjectId(),
        sessionId="sess-1",
        transcriptRef="ref-1",
        normalizedRef=None,
        status="stored",
    )
    base.update(over)
    return AgentSession.model_construct(**base)


@pytest.fixture
def driver_seams(monkeypatch):
    """Fake every I/O seam of normalize_session; return the call recorders."""
    calls = {"download": [], "upload": [], "commit": [], "deleted": []}
    state = {"raw": _jsonl(_user("hello")), "claimed": True}

    async def fake_download(db, ref):
        calls["download"].append(ref)
        return state["raw"]

    async def fake_upload(db, session_id, blob, *, source_ref):
        calls["upload"].append((session_id, blob, source_ref))
        return "norm-new"

    async def fake_commit(doc_id, expected_ref, new_ref, est):
        calls["commit"].append((doc_id, expected_ref, new_ref, est))
        return state["claimed"]

    async def fake_delete(db, ref):
        calls["deleted"].append(ref)

    monkeypatch.setattr(normalizer, "_download_transcript", fake_download)
    monkeypatch.setattr(crud, "upload_normalized", fake_upload)
    monkeypatch.setattr(normalizer, "_commit_normalized", fake_commit)
    monkeypatch.setattr(crud, "delete_transcript", fake_delete)
    return calls, state


def _patch_get(monkeypatch, doc):
    async def fake_get(oid):
        return doc

    monkeypatch.setattr(AgentSession, "get", staticmethod(fake_get))


async def test_normalize_session_happy_path(driver_seams, monkeypatch):
    calls, _ = driver_seams
    doc = _doc()
    _patch_get(monkeypatch, doc)

    await normalizer.normalize_session(object(), str(doc.id))

    assert calls["download"] == ["ref-1"]
    session_id, blob, source_ref = calls["upload"][0]
    assert (session_id, source_ref) == ("sess-1", "ref-1")
    assert json.loads(blob.splitlines()[0]) == {
        "role": "user",
        "kind": "text",
        "text": "hello",
    }
    expected_est = len(blob) // normalizer.CHARS_PER_TOKEN
    assert calls["commit"] == [(doc.id, "ref-1", "norm-new", expected_est)]
    assert calls["deleted"] == []


@pytest.mark.parametrize(
    "doc",
    [
        None,
        _doc(status="normalized", normalizedRef="norm-1"),
        _doc(status="distilled"),
        _doc(normalizedRef="norm-1"),  # stored but already has a normalized blob
    ],
)
async def test_normalize_session_skips(driver_seams, monkeypatch, doc):
    calls, _ = driver_seams
    _patch_get(monkeypatch, doc)

    await normalizer.normalize_session(object(), str(PydanticObjectId()))

    assert calls["download"] == []
    assert calls["upload"] == []


async def test_normalize_session_lost_race_gcs_new_blob(driver_seams, monkeypatch):
    calls, state = driver_seams
    state["claimed"] = False  # concurrent re-ingest replaced transcriptRef
    doc = _doc()
    _patch_get(monkeypatch, doc)

    await normalizer.normalize_session(object(), str(doc.id))

    assert calls["commit"]  # commit was attempted
    assert calls["deleted"] == ["norm-new"]


async def test_normalize_session_missing_blob_no_upload(driver_seams, monkeypatch):
    calls, state = driver_seams
    state["raw"] = None  # _download_transcript logs and returns None on NoFile
    doc = _doc()
    _patch_get(monkeypatch, doc)

    await normalizer.normalize_session(object(), str(doc.id))

    assert calls["upload"] == []
    assert calls["deleted"] == []


async def test_normalize_session_commit_error_gcs_new_blob(driver_seams, monkeypatch):
    calls, _ = driver_seams
    doc = _doc()
    _patch_get(monkeypatch, doc)

    async def boom_commit(doc_id, expected_ref, new_ref, est):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(normalizer, "_commit_normalized", boom_commit)

    with pytest.raises(RuntimeError):
        await normalizer.normalize_session(object(), str(doc.id))

    assert calls["deleted"] == ["norm-new"]


async def test_enqueue_normalization_swallows_errors(monkeypatch, caplog):
    async def boom(db, agent_session_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(normalizer, "normalize_session", boom)

    await crud.enqueue_normalization(object(), "abc")  # must not raise

    assert any("normalization failed" in r.message for r in caplog.records)
