"""
Async wrapper around the official Backboard SDK (`backboard-sdk`).

One module over assistants, threads/messages (streaming + non-streaming),
memories, and documents. Memento runs a single org-level Backboard assistant,
so every memory write MUST be repo-scoped: `add_memory`/`update_memory` always
inject the repo into both the content text and the metadata, and mirror the
authored content into the local `memoryIndex` collection (see models.py) in
the same call.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from backboard import BackboardClient
from backboard.models import (
    Assistant,
    AssistantCloneResponse,
    ChatMessagesResponse,
    Memory,
    MemoriesListResponse,
    MemoryOperationStatus,
    MemoryStats,
    Thread,
    ToolDefinition,
    ToolOutput,
)
from backboard.models import Document as BackboardDocument
from beanie import PydanticObjectId
from fastapi import Request
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.backboard.models import Anchors, MemoryConfidence, MemoryIndex, MemorySource

logger = logging.getLogger(__name__)

Uuid = str | uuid.UUID

# Async memory writes can return an operation id to poll to completion. Terminal
# states are matched case-insensitively (the API is inconsistent about casing).
_MEMORY_OP_POLL_INTERVAL = 0.5  # seconds between op-status polls
_MEMORY_OP_POLL_TRIES = 20  # ~10s ceiling before giving up and proceeding
_MEMORY_OP_DONE = {"COMPLETED", "SUCCESS", "SUCCEEDED", "DONE"}
_MEMORY_OP_FAILED = {"FAILED", "ERROR", "CANCELLED", "CANCELED"}


class BackboardSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BACKBOARD_",
        extra="ignore",
        env_ignore_empty=True,
    )

    api_key: str
    base_url: str = "https://app.backboard.io/api"
    # Applies to every non-streaming call (the SDK has no per-request override).
    # LLM-generation calls — legacy-doc enrichment, the consistency judge, PR
    # distillation, gap detection — routinely take longer than a chat round-trip:
    # a full forced-JSON extraction over a doc + repo tree can run past 30s and
    # then fails as "Request timed out" (silently → zero decisions). Default high;
    # override with BACKBOARD_TIMEOUT for a faster ceiling on interactive paths.
    timeout: int = 180
    # Speech-to-text provider/model for voice answers (BACKBOARD_STT_*). Backboard
    # routes STT through ElevenLabs by default; override for a different model.
    stt_provider: str = "elevenlabs"
    stt_model: str = "scribe_v1"

    def stt_config(self) -> dict[str, str]:
        """The ``voice.stt`` sub-config Backboard passes to the STT provider."""
        return {"provider": self.stt_provider, "model": self.stt_model}


def _extract_transcript(response: ChatMessagesResponse) -> str:
    """Pull the STT transcript out of an add_message response. Backboard nests it
    under ``voice_records`` (top-level or under ``stt``); fall back to the saved
    message content. Empty string when nothing transcribable came back."""
    if not response.messages:
        return ""
    message = response.messages[-1]
    records = message.get("voice_records") or {}
    candidates = (
        records.get("transcript"),
        (records.get("stt") or {}).get("transcript")
        if isinstance(records.get("stt"), dict)
        else None,
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def _inject_repo(repo: str, content: str) -> str:
    """Prefix-tag content with the repo so the org-level assistant's semantic
    search and fact extraction always see the repo scope."""
    return f"[repo: {repo}] {content}"


def _repo_metadata(repo: str, metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Merge caller metadata with the repo key. Repo always wins."""
    return {**(metadata or {}), "repo": repo}


class Backboard:
    """Thin typed facade over ``BackboardClient`` plus Memento's repo-scoped
    memory-write helpers. Use as a long-lived singleton (see app/lifespan.py)
    or as an async context manager in scripts."""

    def __init__(self, settings: BackboardSettings | None = None) -> None:
        # api_key has no default because it's required — but BaseSettings
        # fills it from the environment/.env at runtime, which the type
        # checker can't see from the zero-arg constructor call.
        self.settings = settings or BackboardSettings()  # pyright: ignore[reportCallIssue]
        self._client = BackboardClient(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout,
        )

    @property
    def sdk(self) -> BackboardClient:
        """Escape hatch to the underlying SDK client."""
        return self._client

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "Backboard":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ─── Assistants ───────────────────────────────────────────────────────────

    async def create_assistant(
        self,
        name: str,
        description: str | None = None,
        system_prompt: str | None = None,
        tools: list[ToolDefinition | dict[str, Any]] | None = None,
        custom_fact_extraction_prompt: str | None = None,
        custom_update_memory_prompt: str | None = None,
        **extra: Any,
    ) -> Assistant:
        return await self._client.create_assistant(
            name,
            description=description,
            system_prompt=system_prompt,
            tools=tools,
            custom_fact_extraction_prompt=custom_fact_extraction_prompt,
            custom_update_memory_prompt=custom_update_memory_prompt,
            **extra,
        )

    async def list_assistants(self, skip: int = 0, limit: int = 100) -> list[Assistant]:
        return await self._client.list_assistants(skip=skip, limit=limit)

    async def get_assistant(self, assistant_id: Uuid) -> Assistant:
        return await self._client.get_assistant(assistant_id)

    async def update_assistant(self, assistant_id: Uuid, **fields: Any) -> Assistant:
        return await self._client.update_assistant(assistant_id, **fields)

    async def delete_assistant(self, assistant_id: Uuid) -> dict[str, Any]:
        return await self._client.delete_assistant(assistant_id)

    async def clone_assistant(
        self, assistant_id: Uuid, **options: Any
    ) -> AssistantCloneResponse:
        return await self._client.clone_assistant(assistant_id, **options)

    # ─── Threads ──────────────────────────────────────────────────────────────

    async def create_thread(self, assistant_id: Uuid) -> Thread:
        return await self._client.create_thread(assistant_id)

    async def list_threads(
        self,
        assistant_id: Uuid | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Thread]:
        if assistant_id is not None:
            return await self._client.list_threads_for_assistant(
                assistant_id, skip=skip, limit=limit
            )
        return await self._client.list_threads(skip=skip, limit=limit)

    async def get_thread(self, thread_id: Uuid) -> Thread:
        """Thread with full message history."""
        return await self._client.get_thread(thread_id)

    async def delete_thread(self, thread_id: Uuid) -> dict[str, Any]:
        return await self._client.delete_thread(thread_id)

    # ─── Messages ─────────────────────────────────────────────────────────────

    async def send_message(
        self,
        content: str,
        *,
        thread_id: Uuid | None = None,
        assistant_id: Uuid | None = None,
        system_prompt: str | None = None,
        llm_provider: str | None = None,
        model_name: str | None = None,
        memory: str | None = None,
        memory_pro: str | None = None,
        memory_response_citation: bool | None = None,
        web_search: str | None = None,
        json_output: bool | None = None,
        tools: list[ToolDefinition | dict[str, Any]] | None = None,
        thinking: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **extra: Any,
    ) -> ChatMessagesResponse:
        """Non-streaming send. Omit thread_id to auto-create a thread pinned
        to assistant_id; the response carries both ids for continuation."""
        # The SDK declares a single non-overloaded signature returning
        # ChatMessagesResponse | AsyncIterator[Dict[str, Any]] regardless of
        # `stream`'s value (no @overload keyed on Literal[True]/[False]), so
        # the type checker can't narrow away the iterator branch even though
        # stream=False always returns ChatMessagesResponse at runtime.
        return cast(
            ChatMessagesResponse,
            await self._client.send_message(
                content,
                thread_id=thread_id,
                assistant_id=assistant_id,
                system_prompt=system_prompt,
                llm_provider=llm_provider,
                model_name=model_name,
                stream=False,
                memory=memory,
                memory_pro=memory_pro,
                memory_response_citation=memory_response_citation,
                web_search=web_search,
                json_output=json_output,
                tools=tools,
                thinking=thinking,
                metadata=metadata,
                **extra,
            ),
        )

    async def stream_message(
        self,
        content: str,
        *,
        thread_id: Uuid | None = None,
        assistant_id: Uuid | None = None,
        system_prompt: str | None = None,
        llm_provider: str | None = None,
        model_name: str | None = None,
        memory: str | None = None,
        memory_pro: str | None = None,
        memory_response_citation: bool | None = None,
        web_search: str | None = None,
        json_output: bool | None = None,
        tools: list[ToolDefinition | dict[str, Any]] | None = None,
        thinking: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **extra: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming send. Yields SSE event dicts (``type`` is one of
        ``content_streaming``, ``reasoning_streaming``, ``reasoning_ended``,
        ``tool_submit_required``, ``run_ended``)."""
        # Same untyped-stream-flag gap as send_message above: stream=True
        # always yields events at runtime, but the SDK's return type doesn't
        # narrow away the ChatMessagesResponse branch.
        events = cast(
            AsyncIterator[dict[str, Any]],
            await self._client.send_message(
                content,
                thread_id=thread_id,
                assistant_id=assistant_id,
                system_prompt=system_prompt,
                llm_provider=llm_provider,
                model_name=model_name,
                stream=True,
                memory=memory,
                memory_pro=memory_pro,
                memory_response_citation=memory_response_citation,
                web_search=web_search,
                json_output=json_output,
                tools=tools,
                thinking=thinking,
                metadata=metadata,
                **extra,
            ),
        )
        async for event in events:
            yield event

    async def submit_tool_outputs(
        self,
        thread_id: Uuid,
        tool_outputs: list[ToolOutput | dict[str, str]],
    ) -> ChatMessagesResponse:
        # Same untyped-stream-flag gap as send_message (see above).
        return cast(
            ChatMessagesResponse,
            await self._client.submit_tool_outputs_simple(
                thread_id, tool_outputs, stream=False
            ),
        )

    async def stream_tool_outputs(
        self,
        thread_id: Uuid,
        tool_outputs: list[ToolOutput | dict[str, str]],
    ) -> AsyncIterator[dict[str, Any]]:
        events = cast(
            AsyncIterator[dict[str, Any]],
            await self._client.submit_tool_outputs_simple(
                thread_id, tool_outputs, stream=True
            ),
        )
        async for event in events:
            yield event

    async def cancel_run(self, thread_id: Uuid, run_id: str) -> dict[str, Any]:
        return await self._client.cancel_run(thread_id, run_id)

    # ─── Speech-to-text ────────────────────────────────────────────────────────

    async def transcribe_audio(
        self,
        *,
        thread_id: Uuid,
        audio_path: str | Path,
        stt_config: dict[str, Any] | None = None,
    ) -> str:
        """Transcribe an audio file to text via the assistant's STT provider
        (ElevenLabs by default). Runs with ``send_to_llm="false"`` so the turn
        only transcribes — no model reply — and returns the transcript string.

        STT is thread-scoped in Backboard (it routes through ``add_message``), so
        a ``thread_id`` is required; the transcript is saved as that thread's
        latest message. Returns "" when nothing transcribable came back."""
        response = cast(
            ChatMessagesResponse,
            await self._client.add_message(
                thread_id,
                voice={"stt": stt_config or self.settings.stt_config()},
                audio_file=audio_path,
                send_to_llm="false",
            ),
        )
        return _extract_transcript(response)

    # ─── Memories ─────────────────────────────────────────────────────────────

    async def _await_memory_operation(self, operation_id: str | None) -> None:
        """Poll a memory operation to a terminal state.

        No-op when there's no operation id (synchronous writes). Raises
        ``RuntimeError`` on a FAILED op so the caller treats the write as not
        durable; a poll that never reaches a terminal state within the ceiling
        logs a warning and proceeds rather than blocking the write path."""
        if not operation_id:
            return
        for _ in range(_MEMORY_OP_POLL_TRIES):
            status = await self._client.get_memory_operation_status(operation_id)
            state = (status.status or "").upper()
            if state in _MEMORY_OP_DONE:
                return
            if state in _MEMORY_OP_FAILED:
                raise RuntimeError(
                    f"Backboard memory operation {operation_id} failed: {status.status}"
                )
            await asyncio.sleep(_MEMORY_OP_POLL_INTERVAL)
        logger.warning(
            "memory operation %s did not complete after %d polls; proceeding",
            operation_id,
            _MEMORY_OP_POLL_TRIES,
        )

    async def add_memory(
        self,
        *,
        assistant_id: Uuid,
        org_id: PydanticObjectId,
        repo_id: PydanticObjectId,
        repo: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        source: MemorySource = "manual",
        confidence: MemoryConfidence = "unverified",
        feature: str | None = None,
        pr_number: int | None = None,
        commit_sha: str | None = None,
        author_user_id: PydanticObjectId | None = None,
        files: list[str] | None = None,
        symbols: list[str] | None = None,
        supersedes: list[PydanticObjectId] | None = None,
    ) -> MemoryIndex:
        """Write a repo-scoped memory to Backboard AND mirror it into the local
        ``memoryIndex`` collection in the same call.

        The repo is always injected into the content (``[repo: ...]`` prefix)
        and the metadata (``{"repo": ...}``) — callers cannot opt out, because
        the org-level assistant pools memories from every repo.

        The new memory is stamped ``stalenessStatus="fresh"`` directly — no
        staleness_check/GitHub call needed, since zero commits can have landed
        between minting it and checking it. ``supersedes`` names prior memory
        ids this one replaces: each gets ``supersededBy`` set to the new
        memory's id and its cached status flipped to ``"stale"`` (per
        staleness_check's own definition of stale: an anchored file changed
        AND a newer memory now covers the same anchors — which becomes true
        the instant this memory is created). Missing/already-deleted ids are
        skipped rather than raising, matching update_memory/delete_memory.
        """
        injected = _inject_repo(repo, content)
        result = await self._client.add_memory(
            assistant_id, injected, _repo_metadata(repo, metadata)
        )
        memory_id = result.get("memory_id")
        if not memory_id:
            raise RuntimeError(f"Backboard add_memory returned no memory_id: {result}")
        # Async writes surface an operation id; wait for it to reach a terminal
        # state before mirroring, so callers (e.g. supersession) never reference
        # a memory the backend hasn't durably committed.
        await self._await_memory_operation(
            result.get("memory_operation_id") or result.get("operation_id")
        )
        now = datetime.now(timezone.utc)
        index = MemoryIndex(
            orgId=org_id,
            repoId=repo_id,
            bbMemoryId=str(memory_id),
            contentSnapshot=injected,
            source=source,
            confidence=confidence,
            feature=feature,
            prNumber=pr_number,
            commitSha=commit_sha,
            authorUserId=author_user_id,
            anchors=Anchors(repo=repo, files=files or [], symbols=symbols or []),
            stalenessStatus="fresh",
            stalenessCheckedAt=now,
        )
        await index.insert()
        for old_id in supersedes or []:
            old = await MemoryIndex.get(old_id)
            if old is None:
                continue
            old.supersededBy = index.id
            old.stalenessStatus = "stale"
            old.stalenessCheckedAt = now
            await old.save()
        return index

    async def update_memory(
        self,
        *,
        assistant_id: Uuid,
        memory_id: str,
        repo: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        """Update a memory on Backboard (repo re-injected, same rules as
        ``add_memory``) and refresh the local index's contentSnapshot."""
        injected = _inject_repo(repo, content)
        memory = await self._client.update_memory(
            assistant_id, memory_id, injected, _repo_metadata(repo, metadata)
        )
        index = await MemoryIndex.find_one({"bbMemoryId": str(memory_id)})
        if index is not None:
            index.contentSnapshot = injected
            await index.save()
        return memory

    async def delete_memory(self, assistant_id: Uuid, memory_id: str) -> dict[str, Any]:
        """Delete a memory on Backboard and soft-delete its index doc."""
        result = await self._client.delete_memory(assistant_id, memory_id)
        index = await MemoryIndex.find_one({"bbMemoryId": str(memory_id)})
        if index is not None:
            index.deletedAt = datetime.now(timezone.utc)
            await index.save()
        return result

    async def search_memories(
        self, assistant_id: Uuid, query: str, limit: int = 5
    ) -> dict[str, Any]:
        """Semantic search. Returns ``{"memories": [...], "total_count": n}``."""
        return await self._client.search_memories(assistant_id, query, limit=limit)

    async def list_memories(
        self,
        assistant_id: Uuid,
        page: int | None = None,
        page_size: int | None = None,
    ) -> MemoriesListResponse:
        return await self._client.get_memories(
            assistant_id, page=page, page_size=page_size
        )

    async def get_memory(self, assistant_id: Uuid, memory_id: str) -> Memory:
        return await self._client.get_memory(assistant_id, memory_id)

    async def reset_memories(self, assistant_id: Uuid) -> dict[str, Any]:
        return await self._client.reset_memories(assistant_id)

    async def get_memory_stats(self, assistant_id: Uuid) -> MemoryStats:
        return await self._client.get_memory_stats(assistant_id)

    async def get_memory_operation_status(
        self, operation_id: str
    ) -> MemoryOperationStatus:
        """Status of an async memory op (e.g. triggered by memory="Auto" chat
        turns, surfaced as ``memory_operation_id`` on message responses)."""
        return await self._client.get_memory_operation_status(operation_id)

    # ─── Documents ────────────────────────────────────────────────────────────

    async def upload_document_to_assistant(
        self, assistant_id: Uuid, file_path: str | Path
    ) -> BackboardDocument:
        return await self._client.upload_document_to_assistant(assistant_id, file_path)

    async def upload_document_to_thread(
        self, thread_id: Uuid, file_path: str | Path
    ) -> BackboardDocument:
        return await self._client.upload_document_to_thread(thread_id, file_path)

    async def list_assistant_documents(
        self, assistant_id: Uuid
    ) -> list[BackboardDocument]:
        return await self._client.list_assistant_documents(assistant_id)

    async def list_thread_documents(self, thread_id: Uuid) -> list[BackboardDocument]:
        return await self._client.list_thread_documents(thread_id)

    async def get_document_status(self, document_id: Uuid) -> BackboardDocument:
        """Poll until ``status`` reaches ``indexed`` (or ``error``)."""
        return await self._client.get_document_status(document_id)

    async def delete_document(self, document_id: Uuid) -> dict[str, Any]:
        return await self._client.delete_document(document_id)


def get_backboard(request: Request) -> Backboard:
    """FastAPI dependency returning the app-wide Backboard client
    (initialized in app/lifespan.py)."""
    return request.app.state.backboard
